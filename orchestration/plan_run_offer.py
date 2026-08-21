"""Offer to run the story a planning session just committed.

`l5-plan` commits the artifact its session wrote and pushes it, and then the
developer reads the story id off the screen and types the run command. The
script that just committed the artifact is the thing best placed to offer to
run it, so this module holds the mechanism: deciding whether there is a
terminal to ask at all, deciding run or skip from the developer's reply,
launching the run, and rendering the command a skipped run would have been
started with.

Every function returns what happened rather than printing it, the way
`plan_commit` does, so `l5-plan`'s `report()` remains the one place the
developer-facing wording lives. Nothing here prompts, and nothing here decides
what the offer is worth: the script asks, this module answers.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TextIO


def can_prompt(stream: TextIO) -> bool:
    """Whether there is a terminal on `stream` for the offer to be made to.

    Both halves of the offer ask this — the script, to decide whether to write
    the prompt at all, and `should_run` below, to decide whether to read — so it
    is answered here once rather than spelled in each. Two spellings of one
    question are two answers that can disagree.

    A stream that cannot say whether it is a terminal is treated as not one, the
    same one-directional bias the rest of the harness takes: the cost of
    answering no is a command printed instead of a prompt, and the cost of
    answering yes wrongly is a read that never returns.
    """
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except ValueError:
        # A closed stream raises rather than answering. It is not a terminal.
        return False


def should_run(stream: TextIO) -> bool:
    """Whether the developer's reply on `stream` asks for the run to start.

    An empty line — Enter alone — runs it; any other reply skips. The reply is
    read once and never re-asked, so a developer who types something the offer
    does not recognise is not held at a prompt.

    A stream that is not a terminal skips *without reading*, and so does a read
    that reaches end of input, which is what keeps a scripted or cron
    invocation from blocking on a prompt nothing can answer.
    """
    if not can_prompt(stream):
        return False
    reply = stream.readline()
    if reply == "":
        return False
    return reply.strip() == ""


def run_script(harness_root: Path) -> Path:
    """The `l5-run` sibling of the `l5-plan` this offer was made from.

    The launch and the rendered command take the executable from here rather
    than each spelling it, so what is printed on the skip path is what the run
    path would have started.
    """
    return harness_root / "scripts" / "l5-run"


def _arguments(harness_root: Path, story_id: str, base: str | None) -> list[str]:
    argv = [str(run_script(harness_root)), story_id]
    if base is not None:
        argv += ["--base", base]
    return argv


def launch_run(harness_root: Path, story_id: str, base: str | None = None) -> int:
    """Run the story as a child process, returning its exit status.

    No stdout, stderr or capture argument is passed at all, so the run inherits
    the terminal `l5-plan` was given and nothing buffers its output. The working
    directory is left alone: `l5-run` finds the target repository by walking up
    from it, and that is the directory `l5-plan` was started in.
    """
    return subprocess.run(_arguments(harness_root, story_id, base)).returncode


def run_command(harness_root: Path, story_id: str, base: str | None = None) -> str:
    """The command a skipped run would be started with, as it must be typed.

    The executable is the same one `launch_run` uses, made relative to the
    current directory when it lies beneath it and left absolute otherwise, so
    what is printed can be pasted back. A relative path that carries no
    separator is written with a leading `./`, because a bare name is looked up
    on PATH rather than in the current directory.
    """
    argv = _arguments(harness_root, story_id, base)
    argv[0] = _typed_path(Path(argv[0]))
    return " ".join(argv)


def _typed_path(script: Path) -> str:
    try:
        relative = script.relative_to(Path.cwd())
    except ValueError:
        return str(script)
    text = str(relative)
    return text if os.sep in text else f".{os.sep}{text}"
