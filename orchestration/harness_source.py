"""Declare where the harness's own source lives, and scan it for ties to a target's stack.

The harness is meant to run against any repository. Its only tie to a
target's language, toolchain or directory layout belongs in `.harness/`,
which is that target's own configuration. A tie anywhere else -- a Python
snippet executed as a probe, a configuration key whose name is a language,
a workflow restriction naming a directory, a sentence of prose naming a
pytest layout -- is the harness assuming what it is running against.

This module is a **declaration the suite asserts against**. Nothing in
orchestration calls it: the coordinator does not run it, no run-time
behaviour reads it, and no run is refused because of what it reports. It
exists so that `tests/test_no_target_stack_in_harness_source.py` can hold
two allowlists against what the scan actually finds, and so that the list
of known ties burns down measurably rather than by assertion.

Prose was tried twice for rules of this shape in this repository -- the
`git diff HEAD` baseline rule and the git-history-loader rule were both
written down, injected into prompts, and shipped again five and three more
times respectively. Both stopped when a scan landed. A scan is therefore
the deliverable here, and this docstring is the pointer to the mechanism
rather than the mechanism.

Two rules
---------

`STACK_RULE` looks for a token naming a language or toolchain
(`STACK_TOKENS`) anywhere under `HARNESS_SOURCE_DIRS`.

`LAYOUT_RULE` looks for a path shaped like a target's test layout
(`TARGET_LAYOUT_PATHS`) under `TARGET_FACING_DIRS` only.

Classifying what the scan reports
---------------------------------

The scan reports mentions; it does not judge them, and the judgement is the
suite's two lists. A mention is a **temporary tie** when it names or
assumes a target's stack or layout -- something a target-agnostic harness
should not be saying. It is a **permanent mention** when it describes the
harness's *own* implementation language, or explicitly says a target need
*not* be Python. A docstring saying every scalar this parser produces is a
Python `str` is a fact about this code; a sentence saying a target's test
command need not be a Python interpreter is the opposite of a tie. The two
lists stay separate because only the temporary one is a burn-down: merged,
a list that stops shrinking cannot be told from work that finished.

What this does not catch
------------------------

`STACK_TOKENS` is **a guess about languages nobody has tried, and it will
be incomplete**. It is written from the toolchains this repository's
authors happen to know, so a tie naming a language absent from the list --
a Ruby `Gemfile`, an Elixir `mix.exs`, a toolchain invented after this was
written -- is reported by nothing here. The list is worth having anyway
because it catches the *shape* of the mistake, which recurs, rather than
every instance of it. Widen it when a new tie teaches a new token.

The layout half **cannot read `orchestration/` at all**, and that is a
deliberate limit rather than an oversight. This harness's own suite is
called `tests/`, so in Python source a `tests/` literal cannot be told from
an honest reference to this repository's own test directory. The layout
half therefore reads only the target-facing files -- prompts and workflow
definitions -- where a path can only mean a target's. A `tests/` literal in
`orchestration/` is invisible to this scan and always will be.

`.harness/` and `tests/` are outside the scan entirely. A target's ties
belong in `.harness/`, and this repository's own suite is legitimately full
of Python.

This module is **exempt from its own scan by name**: `EXEMPT_FILES` holds
`orchestration/harness_source.py` and nothing else, because the token list
declared here would otherwise report itself on every line. The exemption is
by name and covers no other file -- a second file carrying these same
tokens is reported like any other.

The scan is not tamper-proof and does not claim to be. Deleting it is not
caught here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# The scan runs against this repository by default, resolved relative to
# this module the way schema_validator resolves schemas/ -- the harness
# source ships with the harness code, not with a target's .harness/.
HARNESS_ROOT = Path(__file__).resolve().parents[1]

# The directories holding the harness's own source. Everything the scan
# reads lives under one of these; .harness/ and tests/ are deliberately
# absent, per the module docstring.
HARNESS_SOURCE_DIRS: tuple[str, ...] = (
    "orchestration",
    "prompts",
    "workflows",
    "schemas",
    "rules",
    "hooks",
    "scripts",
    "templates",
)

# The subset of the above that a target reads or that describes a target's
# repository: prompts an agent is given, and the workflow definition whose
# declarations name paths in the target. A path literal here can only mean
# a target's, which is what makes the layout rule decidable in them.
TARGET_FACING_DIRS: tuple[str, ...] = ("prompts", "workflows")

# Tokens naming a language or toolchain. A guess, and incomplete by
# construction -- see "What this does not catch" above.
STACK_TOKENS: tuple[str, ...] = (
    "python",
    "python3",
    "pytest",
    "venv",
    "conftest",
    "pip",
    "npm",
    "jest",
    "go.mod",
    "cargo",
    "gradle",
    "tox",
)

# Directory shapes a target's test layout would use. Each ends in a slash,
# which is what makes it a path rather than a word.
TARGET_LAYOUT_PATHS: tuple[str, ...] = (
    "tests/",
    "test/",
    "spec/",
    "specs/",
    "__tests__/",
)

# Exempt by name, and covering nothing else: this module declares the token
# list, so it would otherwise report itself on every line of it.
EXEMPT_FILES: tuple[str, ...] = ("orchestration/harness_source.py",)

# Compiled artifacts are not source and carry no judgement.
SKIP_DIR_NAMES: frozenset[str] = frozenset({"__pycache__"})

STACK_RULE = "stack-token"
LAYOUT_RULE = "target-layout"


@dataclass(frozen=True)
class Finding:
    """One matched line. `line` is the text so a caller can key on it.

    Keying on the text rather than on `line_number` is what lets an
    allowlist survive an unrelated edit above a match; the line number is
    carried anyway so a failure message can name it.
    """

    path: str
    line_number: int
    line: str
    token: str
    rule: str


def _stack_pattern(tokens: tuple[str, ...]) -> re.Pattern[str]:
    """One alternation, longest token first, bounded by a non-alphanumeric.

    Bounding on "not a letter or a digit" rather than on a word boundary is
    the choice that makes an identifier-embedded token visible:
    `clean_clone_python` and `platform.python_version()` are seen, because
    `_` and `.` are not letters or digits, while `pipeline` and `pipe` are
    not, because `e` is. `\\b` would do the opposite on both counts.

    Longest first so that `python3` claims its own match rather than being
    read as `python` followed by a digit -- which the boundary would then
    reject, hiding it entirely.
    """
    ordered = sorted(tokens, key=len, reverse=True)
    alternation = "|".join(re.escape(token) for token in ordered)
    return re.compile(rf"(?<![A-Za-z0-9])({alternation})(?![A-Za-z0-9])", re.IGNORECASE)


def _layout_pattern(paths: tuple[str, ...]) -> re.Pattern[str]:
    """The same left boundary, and no right boundary.

    Each path already ends in a slash, so it delimits itself on the right;
    requiring a non-alphanumeric after it would hide `tests/conftest.py`,
    which is exactly the shape being looked for.
    """
    ordered = sorted(paths, key=len, reverse=True)
    alternation = "|".join(re.escape(path) for path in ordered)
    return re.compile(rf"(?<![A-Za-z0-9])({alternation})", re.IGNORECASE)


STACK_PATTERN = _stack_pattern(STACK_TOKENS)
LAYOUT_PATTERN = _layout_pattern(TARGET_LAYOUT_PATHS)


def _scanned_files(root: Path, directories: tuple[str, ...]) -> list[Path]:
    """Every readable file under the named directories of `root`, sorted."""
    found: list[Path] = []
    for name in directories:
        directory = root / name
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if SKIP_DIR_NAMES.intersection(path.parts):
                continue
            found.append(path)
    return sorted(found)


def _lines(path: Path) -> list[str] | None:
    """The file's lines, or None when it is not text this scan can read."""
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return None


def scan(root: Path | None = None) -> list[Finding]:
    """Every tie the two rules find under `root`, sorted deterministically.

    `root` is a repository root: this repository by default, and a
    throwaway one built by a test otherwise, so the same code path decides
    both. A directory the root does not have is skipped rather than raising
    -- a throwaway root need only carry what the test is about.

    A line matching both rules yields one finding per rule, each carrying
    the token that rule matched; within a rule, a line yields one finding
    carrying its first match.
    """
    base = (root or HARNESS_ROOT).resolve()
    exempt = {base / name for name in EXEMPT_FILES}

    findings: list[Finding] = []
    for path in _scanned_files(base, HARNESS_SOURCE_DIRS):
        if path in exempt:
            continue
        lines = _lines(path)
        if lines is None:
            continue
        relative = path.relative_to(base).as_posix()
        target_facing = path.relative_to(base).parts[0] in TARGET_FACING_DIRS
        for number, line in enumerate(lines, start=1):
            stack = STACK_PATTERN.search(line)
            if stack is not None:
                findings.append(
                    Finding(relative, number, line, stack.group(1), STACK_RULE)
                )
            if not target_facing:
                continue
            layout = LAYOUT_PATTERN.search(line)
            if layout is not None:
                findings.append(
                    Finding(relative, number, line, layout.group(1), LAYOUT_RULE)
                )
    return sorted(findings, key=lambda f: (f.path, f.line_number, f.rule, f.token))
