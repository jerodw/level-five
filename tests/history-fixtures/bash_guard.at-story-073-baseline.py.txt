#!/usr/bin/env python3
"""Deny a Bash call that mutates the working tree, the index or the repository.

This is a PreToolUse hook, registered against Bash by hooks/settings.json and
passed to every stage invocation by orchestration/agent_runner.py. It reads the
hook payload from stdin and writes a deny decision to stdout when it finds a
mutator; otherwise it writes nothing at all.

Two properties are load-bearing and neither is an accident of the code:

**It denies only.** There is no allow path anywhere in this file — no input
produces a decision other than "deny". The allowlist in the target's
.harness/config.yaml is the thing that permits; this is the net behind it, so a
command the allowlist would refuse is never admitted by the guard reporting no
problem with it.

**Its bias is fail-open.** Anything this cannot establish yields *no* decision
rather than a deny, and the call falls through to the allowlist. Unreadable
stdin, a malformed payload, an unbalanced quote, an unterminated substitution
and a heredoc all take that path. A fail-closed parser mistake would stop runs
that should have proceeded, which is the more expensive error, and it is the
same one-directional bias the coordinator's own checks already take.

What it does not cover, stated here rather than implied: it reads the command as
written, so a mutator spelled to avoid recognition (quoted, assembled from
variables, base64-decoded, run through an interpreter) is not seen. That is the
allowlist's job, not this one's.
"""
from __future__ import annotations

import json
import re
import sys

HOOK_EVENT = "PreToolUse"

# Commands that write to the filesystem. chmod is deliberately absent: it is
# granted on purpose, and it changes a mode rather than content.
MUTATORS = frozenset(
    {
        "rm",
        "rmdir",
        "mv",
        "cp",
        "dd",
        "tee",
        "truncate",
        "ln",
        "mkdir",
        "touch",
        "chown",
        "chgrp",
        "shred",
        "unlink",
        "install",
        "patch",
        "rsync",
        "mkfifo",
    }
)

# git subcommands that move the working tree, the index or the repository.
# status, diff, log, show, branch and ls-files are granted read-only
# inspection and are deliberately not here.
GIT_MUTATORS = frozenset(
    {
        "add",
        "am",
        "apply",
        "bisect",
        "checkout",
        "cherry-pick",
        "clean",
        "clone",
        "commit",
        "config",
        "fetch",
        "filter-branch",
        "gc",
        "init",
        "merge",
        "mv",
        "notes",
        "prune",
        "pull",
        "push",
        "rebase",
        "reflog",
        "remote",
        "reset",
        "restore",
        "revert",
        "rm",
        "stash",
        "submodule",
        "switch",
        "tag",
        "update-index",
        "update-ref",
        "worktree",
    }
)

# find actions that run a command or write something.
FIND_ACTIONS = frozenset(
    {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprintf", "-fls"}
)

# Commands whose arguments name another command to run. The command being
# wrapped is checked in its own right, so `xargs rm` is not a way past this.
WRAPPERS = frozenset(
    {"xargs", "env", "sudo", "doas", "nohup", "nice", "time", "timeout", "command"}
)

SEPARATORS = ";\n&|"
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
REDIRECT = re.compile(r"^(?:\d*|&)>>?")


class Unparseable(Exception):
    """The command could not be decomposed, so the guard says nothing."""


def _unquote(word: str) -> str:
    if len(word) >= 2 and word[0] == word[-1] and word[0] in "'\"":
        return word[1:-1]
    return word


def _substitution(command: str, start: int, closer: str) -> tuple[str, int]:
    """The interior of a command substitution, and the index past its closer."""
    depth = 1
    quote: str | None = None
    index = start
    while index < len(command):
        char = command[index]
        if quote == "'":
            if char == "'":
                quote = None
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if quote == '"':
            if char == '"':
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            continue
        if closer == ")" and char == "(":
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return command[start:index], index + 1
        index += 1
    raise Unparseable("unterminated command substitution")


def decompose(command: str) -> list[str]:
    """Every command string inside `command`.

    Its top-level components — split on pipes, semicolons, logical operators
    and newlines — plus the interior of every command substitution, written as
    $(...) or with backticks, decomposed the same way. A substitution inside
    single quotes is left alone, because the shell does not run it either.
    """
    components: list[str] = []
    substitutions: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    length = len(command)

    while index < length:
        char = command[index]

        if quote == "'":
            if char == "'":
                quote = None
            current.append(char)
            index += 1
            continue

        if char == "\\":
            if command[index + 1 : index + 2] == "\n":
                index += 2  # line continuation: the next line is this command
                continue
            current.append(char)
            current.append(command[index + 1 : index + 2])
            index += 2
            continue

        if char == "$" and command[index + 1 : index + 2] == "(":
            interior, index = _substitution(command, index + 2, ")")
            substitutions.append(interior)
            current.append(" ")
            continue

        if char == "`":
            interior, index = _substitution(command, index + 1, "`")
            substitutions.append(interior)
            current.append(" ")
            continue

        if quote == '"':
            if char == '"':
                quote = None
            current.append(char)
            index += 1
            continue

        if char in "'\"":
            quote = char
            current.append(char)
            index += 1
            continue

        # A heredoc's body is text rather than commands, and reading it as
        # commands would deny for words that are only ever data. The guard has
        # nothing to say about it.
        if char == "<" and command[index + 1 : index + 2] == "<":
            raise Unparseable("heredoc")

        # Redirection operators are consumed whole so that the & of 2>&1 and
        # of &> is not mistaken for a separator.
        if char == ">" or (char == "&" and command[index + 1 : index + 2] == ">"):
            # A redirect starts a word of its own unless what precedes it is a
            # bare file descriptor: `f>out` is an argument and a redirect,
            # while `2>&1` is one operator.
            trailing = "".join(current).rsplit(" ", 1)[-1].rsplit("\t", 1)[-1]
            if trailing and not trailing.isdigit():
                current.append(" ")
            if char == "&":
                current.append("&")
                index += 1
            current.append(">")
            index += 1
            if command[index : index + 1] == ">":
                current.append(">")
                index += 1
            if command[index : index + 1] == "&":
                current.append("&")
                index += 1
            continue

        if char in SEPARATORS:
            components.append("".join(current))
            current = []
            while index < length and command[index] in SEPARATORS:
                index += 1
            continue

        if char in "()":  # a subshell begins a new command
            components.append("".join(current))
            current = []
            index += 1
            continue

        current.append(char)
        index += 1

    if quote is not None:
        raise Unparseable("unbalanced quote")

    components.append("".join(current))
    for interior in substitutions:
        components.extend(decompose(interior))
    return [component for component in components if component.strip()]


def tokens(component: str) -> list[str]:
    """The component's words, with their quoting left on.

    Quotes are kept so that a quoted operator stays an argument: `grep ">" f`
    searches for a character, and reporting it as a redirect would be a denial
    the story exists to stop paying for.
    """
    words: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in component:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            continue
        if char.isspace():
            if current:
                words.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        words.append("".join(current))
    return words


def _command_words(words: list[str]) -> list[str]:
    """The words from the command name onward, with env assignments dropped."""
    for position, word in enumerate(words):
        if ASSIGNMENT.match(word):
            continue
        return words[position:]
    return []


def _name(word: str) -> str:
    return _unquote(word).rsplit("/", 1)[-1]


def _writes_a_file(words: list[str]) -> str | None:
    """The redirect in these words that names a file, if any.

    A descriptor duplication (2>&1) and a redirect to /dev/null write no file
    and are left alone.
    """
    for position, word in enumerate(words):
        match = REDIRECT.match(word)
        if not match:
            continue
        rest = word[match.end() :]
        if rest.startswith("&"):
            continue
        target = rest or (words[position + 1] if position + 1 < len(words) else "")
        target = _unquote(target)
        if not target or target == "/dev/null":
            continue
        return word if rest else f"{word} {target}"
    return None


def _mutator(words: list[str], depth: int = 0) -> str | None:
    """What in these words mutates, named, or None."""
    words = _command_words(words)
    if not words:
        return None
    name = _name(words[0])
    arguments = words[1:]

    if name in MUTATORS:
        return name
    if name == "git":
        for argument in arguments:
            if argument.startswith("-"):
                continue
            subcommand = _unquote(argument)
            return f"git {subcommand}" if subcommand in GIT_MUTATORS else None
        return None
    if name == "find":
        for argument in arguments:
            if _unquote(argument) in FIND_ACTIONS:
                return f"find {_unquote(argument)}"
        return None
    if name == "sed":
        for argument in arguments:
            stripped = _unquote(argument)
            if stripped == "--in-place" or stripped.startswith("--in-place="):
                return "sed --in-place"
            if stripped.startswith("-i"):
                return "sed -i"
        return None
    if name == "perl":
        for argument in arguments:
            stripped = _unquote(argument)
            if stripped.startswith("-") and not stripped.startswith("--"):
                if "i" in stripped[1:].split("e", 1)[0]:
                    return "perl -i"
        return None
    if name in WRAPPERS and depth < 3:
        # Skip the wrapper's own flags and any numeric argument (a timeout's
        # duration), then judge whatever command it was given.
        remainder = list(arguments)
        while remainder:
            candidate = _unquote(remainder[0])
            if candidate.startswith("-") or candidate.replace(".", "").isdigit():
                remainder = remainder[1:]
                continue
            break
        return _mutator(remainder, depth + 1)
    return None


def offence(command: str) -> str | None:
    """Why this command is denied, or None when the guard has nothing to say.

    Raises Unparseable when the command cannot be decomposed, which the caller
    turns into no decision rather than into a denial.
    """
    for component in decompose(command):
        words = tokens(component)
        found = _mutator(words)
        if found:
            return f"{found} in `{component.strip()}`"
        redirect = _writes_a_file(words)
        if redirect:
            return f"redirect `{redirect}` in `{component.strip()}`"
    return None


def deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": HOOK_EVENT,
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def main() -> int:
    # Every failure below is silent by design: no decision returns the call to
    # the allowlist, which is the gate. See the module docstring.
    try:
        payload = json.loads(sys.stdin.read())
    except (OSError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return 0

    try:
        reason = offence(command)
    except Unparseable:
        return 0
    except Exception:  # a parser defect must not stop a run
        return 0
    if reason is None:
        return 0

    json.dump(
        deny(
            f"This harness denies Bash commands that write to the working tree, "
            f"the index or the repository: {reason}. Use Edit or Write to change "
            f"files."
        ),
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
