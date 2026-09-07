"""The one route by which a test says the machine, not the code, is what it
could not get past -- and the ceiling that keeps such a report expensive.

A test in this suite may bound how slow an *operation* is allowed to be. A
command asked to sleep far past a configured timeout must be killed at that
timeout; a loaded machine only makes that more true, so the assertion is about
the harness. A test may not bound how slow the *machine* is allowed to be. An
assertion that a real operation finished inside a wall-clock window is a claim
about the hardware at that moment, and the hardware owes it nothing. Under
`-n auto` across a suite whose workers each spawn processes of their own, that
second kind of claim is the one that ends runs, and widening its constant is
the same defect with a better number.

Several of those claims are not the question their test exists to ask. They are
preconditions: a child has to have been spawned before anything can be asked
about whether a kill reached its group, a session has to have written its
artifact before anything can be asked about when its approval was read. A
precondition the machine did not let the run reach is not a defect in the code
under test, and reporting it as a failure says something false -- it says the
harness is broken when what happened is that the run could not be conducted at
all.

`inconclusive` is how a test says that instead, and it is meant to be the only
way. A test that can skip is a test that can go quietly missing, so the skip is
made expensive to hide:

  * every such skip carries `INCONCLUSIVE_PREFIX`, so a reader can tell one
    from a declared `skip` or `skipif` marker without guessing at wording;
  * `pytest_sessionfinish` below decides the count once, on the controller,
    over every worker's reports, and a run that reported more than
    `MAX_INCONCLUSIVE` of them exits non-zero naming each one -- a suite that
    proved nothing because the machine was too loaded is a failure rather than
    a pass;
  * `tests/test_no_test_bounds_the_machine.py` holds the standing rule that no
    module under `tests/` skips for a machine-load reason by any other route,
    and the enumeration of which modules read a clock at all.

A call here means the test never got as far as its own assertions. It is not
the skipping-to-pass the testing standard forbids, which is a test that reached
its question and declined to answer it.
"""
import sys
from typing import NoReturn

import pytest

#: What every inconclusive result carries, and nothing else in this suite does.
#: The count below is decided on this rather than on wording, so a reason
#: rephrased by a later author still spends the ceiling.
INCONCLUSIVE_PREFIX = "machine-load precondition not met: "

#: How many inconclusive results a run may report and still be a run that
#: proved something. The ceiling is chosen against the sites a *merely busy*
#: machine can make report: those asking a real process to have got somewhere
#: before a bound expires -- the transport's spawned child under
#: SPAWN_BOUND_SECONDS, and the landing halves of the two configured bounds,
#: SYNC_TIMEOUT and FILED_QUERY_TIMEOUT. Callers of `inconclusive` are found by
#: reading the code rather than counted here, since a number written down
#: beside them goes stale the moment a site is added or converted. Several of
#: those races lost at once is still a run whose other several thousand
#: assertions were decided; beyond that is a machine too loaded for the run to
#: have established anything, and a green pass would be the wrong report of it.
MAX_INCONCLUSIVE = 2

#: What a run that exceeded the ceiling exits with. `1` rather than a code of
#: this module's own invention: the coordinator that runs this suite as a
#: subprocess reads the exit status as a pass-or-fail verdict, and a run that
#: proved nothing is a failed run.
CEILING_EXIT_STATUS = 1


def inconclusive(reason: str) -> NoReturn:
    """Report that a precondition the run could not meet was not met.

    `reason` says which precondition, in the words of the test that needed it,
    so a reader of a run that reported one is told what the machine did not
    let happen rather than only that something was skipped.
    """
    raise inconclusive_error(reason)


def inconclusive_error(reason: str) -> BaseException:
    """The same report as an exception object rather than a raise.

    A caller that has to choose between reporting inconclusive and failing --
    `drain` in `tests/test_plan_commit.py` is the one, because one of its
    callers drives it with a deliberately tiny deadline and there the expiry
    *is* the assertion -- builds the exception and raises it itself.
    """
    return pytest.skip.Exception(INCONCLUSIVE_PREFIX + reason)


def inconclusive_reason(report) -> str | None:
    """The reason carried by `report`, or `None` if it is not one of ours.

    Reads the report rather than the item, because under `-n auto` the
    controller sees workers' reports and nothing else of theirs. A declared
    `skip` or `skipif` marker produces a skipped report too and is not one of
    ours: it carries no prefix, so it does not spend the ceiling.
    """
    if not report.skipped:
        return None
    # A skip's `longrepr` is the `(path, lineno, message)` triple, which
    # survives xdist's serialisation as a tuple. Anything else is stringified
    # rather than assumed about, so a shape this does not know about is looked
    # at rather than dropped.
    longrepr = report.longrepr
    text = (longrepr[2] if isinstance(longrepr, tuple) and len(longrepr) == 3
            else str(longrepr))
    if INCONCLUSIVE_PREFIX not in text:
        return None
    return text.split(INCONCLUSIVE_PREFIX, 1)[1].strip()


# --------------------------------------------------------------------------
# The ceiling
#
# Installed by `tests/conftest.py`, which imports both hooks below by name so
# that pytest finds them on the conftest module. A suite that this repository's
# validation builds under a temporary directory installs them the same way, so
# what the demonstrations run is this code rather than a copy of it.
# --------------------------------------------------------------------------

#: Accumulated by node id, so a report seen twice -- a setup skip and its
#: call-phase report -- is one inconclusive result rather than two.
_REPORTED: dict[str, str] = {}


def pytest_runtest_logreport(report) -> None:
    reason = inconclusive_reason(report)
    if reason is not None:
        _REPORTED.setdefault(report.nodeid, reason)


def pytest_sessionfinish(session, exitstatus) -> None:
    """Decide the count once, on the controller, over every worker's reports.

    Guarded to the controller because an xdist worker sees only its own shard:
    a ceiling read per worker is a different, looser ceiling than the one this
    suite declares, and on a run sharded across a dozen workers it would be
    almost impossible to exceed.
    """
    if hasattr(session.config, "workerinput"):
        return
    reported = dict(_REPORTED)
    _REPORTED.clear()
    if len(reported) <= MAX_INCONCLUSIVE:
        return

    lines = [f"{len(reported)} inconclusive results, more than the "
             f"{MAX_INCONCLUSIVE} this suite allows: the machine was too "
             f"loaded for this run to have proven anything."]
    lines += [f"  {nodeid}: {reason}" for nodeid, reason in sorted(reported.items())]
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:                       # pragma: no cover - -p no:terminal
        print("\n".join(lines), file=sys.stderr)
    else:
        for line in lines:
            reporter.write_line(line)
    if not exitstatus:
        session.exitstatus = CEILING_EXIT_STATUS
