"""The suite reads the machine it runs on, and the plugin that lets it is declared.

Independent validation for story-079. Two claims, and they are one claim split
across two files:

  * `.harness/config.yaml` configures a parallel suite run whose worker count is
    `auto` — no number, so the same command is correct on a two-core CI runner
    and on a ten-core laptop; and
  * the plugin that makes that flag a recognised argument is declared in a
    tracked `requirements-dev.txt`, and the CI workflow installs *that file*
    rather than a hand-written list that can silently disagree with the
    configured command.

Both subjects are live artifacts this repository ships, which is why this
module reads them rather than a fixture: "the command this deployment
configures pins no core count" and "the workflow this deployment runs installs
the declaration it tracks" are claims about what is shipped and are answerable
from nothing else. The configuration is reached through
`conftest.repository_config`, the one shared loader, rather than through a
second spelling of the path; the CI workflow has no loader and is read as text.

Every absence asserted here sits beside a demonstration that it can fail:

  * "the configured command pins no worker count" sits beside the same reading
    applied to commands that pin one in each of the four spellings pytest-xdist
    accepts, which it reports — and beside a serial command, for which the
    reading answers `None` rather than `auto`, so the positive assertion above
    cannot be satisfied by a parse that has stopped finding anything;
  * "the declared file is tracked" sits beside the same query for a name this
    repository does not track, which answers no;
  * "CI hand-lists no test dependency" sits beside a mutated copy of the same
    workflow text in which the install step is hand-listed again, which the
    same scan reports.

Nothing here invokes a model, runs a suite, or writes into this repository: the
one mutation is made to a string in memory.
"""
import re
import shlex
import subprocess
from pathlib import Path

import pytest

import conftest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The CI workflow definition this repository ships. Read as text because what
#: is asserted about it — which install command it runs — is not something a
#: parsed view would state more honestly.
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"

#: The dependency declaration, named here for the assertion that it exists and
#: names the plugin. Which file *CI* installs is not named here: it is read off
#: the workflow below and compared against this one, so the two can disagree.
DECLARATION = "requirements-dev.txt"

#: This repository's own configured suite command, through the shared loader.
CONFIGURED_COMMAND = conftest.repository_config()["test_command"]

#: The two spellings pytest-xdist gives the worker-count option. Written here
#: rather than read off anything: the option's name is a fact about the plugin,
#: not about this repository, and a reading that only understood the spelling
#: this repository happens to use would report "no fixed count" for a later
#: edit that pinned one in the other spelling.
WORKER_OPTIONS = ("-n", "--numprocesses")


# --------------------------------------------------------------------------
# Reading a command
# --------------------------------------------------------------------------


def worker_count(command: str) -> str | None:
    """What a command asks for as a worker count, or None if it asks for none.

    All four spellings: `-n auto`, `-nauto`, `--numprocesses auto` and
    `--numprocesses=auto`. Answering None for a command that names no worker
    count at all is what makes the positive assertion below mean something —
    a reading that silently answered `auto` when it found nothing would pass
    for a serial command too.
    """
    tokens = shlex.split(command)
    for index, token in enumerate(tokens):
        for option in WORKER_OPTIONS:
            if token == option:
                return tokens[index + 1] if index + 1 < len(tokens) else ""
            if token.startswith(f"{option}="):
                return token[len(option) + 1:]
        if token.startswith("-n") and not token.startswith("--") and len(token) > 2:
            return token[2:]
    return None


def numeric_tokens(command: str) -> list[str]:
    """Every token of a command that is a bare number.

    A second, blunter reading than `worker_count`, and deliberately not a
    refinement of it: it knows nothing about which option a number follows, so
    a core count written into the command in some spelling this module never
    anticipated is still reported.
    """
    return [token for token in shlex.split(command) if token.isdigit()]


# --------------------------------------------------------------------------
# Reading the declaration and the workflow
# --------------------------------------------------------------------------


def declared_requirements(text: str) -> list[str]:
    """The distribution names a requirements file declares, without versions."""
    names = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped:
            names.append(re.split(r"[<>=!~;\[ ]", stripped, maxsplit=1)[0])
    return names


def _install_arguments(text: str) -> list[str]:
    """The argument string of every `pip install` in a workflow definition.

    Comment lines are dropped first, so prose about an install step is not read
    as one.
    """
    body = "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))
    return [match.group(1).strip()
            for match in re.finditer(r"pip install\s+(.+)", body)]


def requirement_files(text: str) -> list[str]:
    """Every file a workflow's install steps install *from*."""
    files = []
    for arguments in _install_arguments(text):
        tokens = shlex.split(arguments)
        for index, token in enumerate(tokens):
            if token == "-r" and index + 1 < len(tokens):
                files.append(tokens[index + 1])
            elif token.startswith("--requirement="):
                files.append(token.split("=", 1)[1])
    return files


def hand_listed_packages(text: str) -> list[str]:
    """Every package a workflow's install steps name directly.

    An argument that is not an option and is not the value of `-r` is a package
    somebody typed, which is the thing this story removed: a hand-written list
    can be missing the plugin the configured command needs while a developer's
    environment has it.
    """
    listed = []
    for arguments in _install_arguments(text):
        tokens = shlex.split(arguments)
        skip = False
        for token in tokens:
            if skip:
                skip = False
                continue
            if token == "-r":
                skip = True
            elif not token.startswith("-"):
                listed.append(token)
    return listed


# --------------------------------------------------------------------------
# Reading what git tracks
# --------------------------------------------------------------------------


def tracked(relative: str) -> bool:
    """Whether this repository tracks a path, asked of git rather than of disk.

    A file that exists and is untracked reaches neither a clone nor CI, which
    is the difference the story's claim turns on.
    """
    listed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--", relative],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    return relative in listed


# --------------------------------------------------------------------------
# The configured command
# --------------------------------------------------------------------------


def test_the_configured_command_runs_the_suite_in_parallel():
    assert worker_count(CONFIGURED_COMMAND) == "auto"


def test_the_configured_command_names_no_fixed_worker_count():
    """`auto` reads the core count of whatever machine the command lands on.

    A number here would be a statement about one machine, written down in the
    one place every gate reads: the revert check, the coordinator's run in the
    tree and the clean-clone check all execute this command.
    """
    count = worker_count(CONFIGURED_COMMAND)
    assert not any(character.isdigit() for character in count), count
    assert numeric_tokens(CONFIGURED_COMMAND) == [], CONFIGURED_COMMAND


@pytest.mark.parametrize("pinned", [
    ".venv/bin/python -m pytest tests/ -q -n 8",
    ".venv/bin/python -m pytest tests/ -q -n8",
    ".venv/bin/python -m pytest tests/ -q --numprocesses 8",
    ".venv/bin/python -m pytest tests/ -q --numprocesses=8",
])
def test_the_same_reading_reports_a_pinned_worker_count(pinned):
    """The control for the assertion above, in each spelling the plugin takes.

    The commands are constructed here rather than read from anywhere: a later
    edit pinning a count in any of these four ways is what the assertion above
    has to catch, and this is the demonstration that it does.
    """
    count = worker_count(pinned)
    assert count == "8"
    assert any(character.isdigit() for character in count)


def test_the_same_reading_answers_nothing_for_a_serial_command():
    """So `== "auto"` above is not satisfied by a reading that found nothing."""
    assert worker_count(".venv/bin/python -m pytest tests/ -q") is None
    assert numeric_tokens(".venv/bin/python -m pytest tests/ -q -n 8") == ["8"]


# --------------------------------------------------------------------------
# The declaration
# --------------------------------------------------------------------------


def test_the_dependency_file_is_tracked_and_names_what_the_command_needs():
    assert tracked(DECLARATION)
    names = declared_requirements(
        (REPO_ROOT / DECLARATION).read_text(encoding="utf-8"))
    assert "pytest" in names, names
    assert "pytest-xdist" in names, names


def test_the_same_query_answers_no_for_a_path_this_repository_does_not_track():
    """The control for `tracked` above: a reading that answered yes to
    everything would say nothing about the declaration."""
    absent = f"{DECLARATION}.not-a-tracked-path"
    assert not (REPO_ROOT / absent).exists()
    assert not tracked(absent)


def test_the_plugin_the_configured_command_needs_is_the_one_declared():
    """The two halves of the story meet here: the flag and the distribution
    that makes it a recognised argument."""
    assert worker_count(CONFIGURED_COMMAND) is not None
    assert "pytest-xdist" in declared_requirements(
        (REPO_ROOT / DECLARATION).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# What CI installs
# --------------------------------------------------------------------------


def test_ci_installs_from_the_file_this_repository_declares():
    """Read off the workflow, not off a list kept beside it.

    The file CI installs from is recovered from the workflow definition and
    then asserted to be the tracked declaration — so the two can disagree, and
    a workflow pointed at some other file fails here rather than passing
    against a copy of its own answer.
    """
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    installed = requirement_files(text)
    assert installed == [DECLARATION], installed
    for name in installed:
        assert tracked(name), name
        assert (REPO_ROOT / name).is_file(), name


def test_ci_hand_lists_no_test_dependency():
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    assert _install_arguments(text), "no pip install step was found at all"
    assert hand_listed_packages(text) == []


def test_the_same_scan_reports_a_hand_listed_install():
    """The control, driven against a mutated copy of the shipped text.

    The mutation is the change this story made, undone: the install step is
    pointed back at a hand-listed `pytest`. The copy lives in memory and is
    never written into this repository, so nothing here can leave the workflow
    definition altered.
    """
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace(f"pip install -r {DECLARATION}", "pip install pytest")
    assert mutated != text

    assert hand_listed_packages(mutated) == ["pytest"]
    assert requirement_files(mutated) == []
