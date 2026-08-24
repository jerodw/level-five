#!/usr/bin/env python3
"""This repository's suite census: how strong the suite is, as integers.

The coordinator runs this in a clone at a stage's baseline and again in the
tree that stage left, and refuses the run when a counter present at the
baseline is missing afterwards or has decreased. It reads nothing but the
object printed here, so every counter is phrased in the direction that makes
that comparison meaningful: **a larger number is a stronger suite**. A new
counter may be added freely — the comparison is over the counters the baseline
carried — but a counter must never be renamed and re-derived in the same
change, because the old name disappearing reads as a removal.

Two counters, both derived by parsing the suite's own source rather than by
running it, so a census costs a parse rather than a suite run:

    unskipped_tests  test functions that are neither skipped nor xfailed
    assertions       assert statements

What a counter cannot see, stated here because this file is where a reader
decides how much the check is worth. **A weakening that moves no counter is
invisible to it.** The proxy assumes weakening shows up as a smaller count,
and the assumption has a hole with a name: replacing a specific assertion with
a weaker one that still asserts something leaves `assertions` exactly where it
was and passes. So does loosening a comparison in place, widening an expected
set, deleting a case from a parametrization, or narrowing what a helper looks
at. A census that reports no regression is a statement that no counter fell,
and it is not a statement that the suite is no weaker.

Two narrower limits follow from parsing rather than running. A test skipped at
run time — `pytest.skip()` called in a body, a fixture that skips — is counted
here as unskipped, because nothing in the source says otherwise; only the
declared markers are seen. And a parametrized test is one function however
many cases it expands to, which is what lets a renamed test with its call
sites updated move no counter, and is also why removing a case from a
parametrize list is invisible.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

#: The markers that take a test out of the unskipped count. Matched on the
#: last segment of the decorator's dotted name, so `@pytest.mark.skipif(...)`
#: and a `skipif` imported directly are both seen.
SUPPRESSING_MARKERS = frozenset({"skip", "skipif", "xfail"})


def _decorator_name(node: ast.expr) -> str:
    """The last segment of a decorator's dotted name, calls unwrapped."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else ""


def _is_suppressed(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        _decorator_name(decorator) in SUPPRESSING_MARKERS
        for decorator in function.decorator_list
    )


def census(directory: Path) -> dict[str, int]:
    """Count the suite under `directory`, as a JSON-ready object of integers.

    A file that does not parse contributes nothing rather than raising: the
    census is a comparison of two counts, and a hard failure on one side would
    stop a run for a reason the census is not about. Its statements simply go
    uncounted, which shows up as a decrease and is refused on those terms.
    """
    tests = 0
    assertions = 0
    for path in sorted(directory.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                assertions += 1
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_") and not _is_suppressed(node):
                    tests += 1
    return {"unskipped_tests": tests, "assertions": assertions}


def main(argv: list[str]) -> int:
    directory = Path(argv[1]) if len(argv) > 1 else Path("tests")
    print(json.dumps(census(directory), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
