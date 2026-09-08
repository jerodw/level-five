"""Independent validation for story-109: no test in this suite times the
machine.

A test here may bound how slow an *operation* is allowed to be and may not
bound how slow the *machine* is allowed to be. `tests/machine_load.py` states
that distinction and is the one route by which a test reports that the machine
is what it could not get past. This module is what makes the rule hold rather
than merely be written down:

  * **the enumeration and its classification, in both directions.** Every
    module under `tests/` that sleeps or reads a clock is found by a source
    scan, and every one of them is classified as bounding only slowness or as
    carrying a precondition routed through the helper. A module that starts
    reading a clock without being classified reddens this, and a classification
    naming a module that no longer reads one reddens it too. Nothing is left
    pending: the sweep is total over what the scan finds.

  * **the single route.** No module under `tests/` calls `pytest.skip` for a
    machine-load reason. The reader that says so is shown reporting a
    constructed module that does, and shown finding the suite's other skips, so
    a clean report is the absence of a load skip rather than a reader that has
    stopped seeing skips at all.

  * **each converted site, missed deliberately.** A command that never
    backgrounds a child, a child whose source never announces itself, a session
    that exits without writing, a drain against a child that never exits, and
    the two configured-bound landing halves driven with a command that sleeps
    past the bound. Each is constructed here and each is shown reporting
    inconclusive. None of them waits for load to produce one.

  * **the ceiling, firing and not firing.** Suites this module writes under
    `tmp_path` and runs as nested pytest processes, installing the shipped
    hooks out of `tests/machine_load.py` rather than a copy of them. A run at
    or below `MAX_INCONCLUSIVE` exits zero; a run past it exits non-zero and
    names each inconclusive test and its reason; a run carrying declared `skip`
    markers alongside inconclusive results spends the ceiling on the latter
    only; and the firing run repeated under xdist reports once, from the
    controller, over every worker's reports.

What this module does not do is wait for a loaded machine. Every inconclusive
result it observes is one it constructed.
"""
from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

import machine_load
import outbox
import test_command_transport as transport_module
import test_config_keys_are_obeyed as config_module
import test_plan_commit as plan_module
import test_pty_drain as pty_module
from machine_load import INCONCLUSIVE_PREFIX, MAX_INCONCLUSIVE
from test_plan_commit import drain

TESTS_DIR = Path(__file__).resolve().parent

#: The file that owns the route, and so the one module the single-route rule
#: below does not hold against itself.
HELPER_FILE = f"{machine_load.__name__}.py"


# --------------------------------------------------------------------------
# 1. The enumeration: which modules read a clock at all
# --------------------------------------------------------------------------

#: What "sleeps or reads a clock" is decided by. Matched against module source
#: as text rather than against its parsed calls, deliberately: several modules
#: here compose child programs as string literals, and a child told to sleep is
#: as much a dependence on timing as a parent that sleeps. Over-inclusion costs
#: a classification entry; under-inclusion costs the whole point of the scan.
CLOCK_CALLS = ("time.sleep(", "time.monotonic(", "time.time(",
               "time.perf_counter(")


def modules_reading_a_clock(directory: Path) -> list[str]:
    """Every module under `directory` that sleeps or reads a clock.

    A search taking the directory as a parameter, so the same search the live
    suite is held to is the one run over a directory with a violation planted
    in it.
    """
    return sorted(path.name for path in directory.glob("*.py")
                  if any(token in path.read_text(encoding="utf-8")
                         for token in CLOCK_CALLS))


#: The two kinds a clock-reading module may be. A module is one or the other
#: and never neither: "unclassified" is not a state this table can express, so
#: a module cannot be parked in it.
BOUNDS_ONLY_SLOWNESS = "bounds only how slow an operation may be"
CARRIES_PRECONDITIONS = "carries a precondition reported through the helper"

#: Every module the scan finds, and which kind it carries. Declared here rather
#: than inferred, so that a module which quietly acquires a wall-clock claim is
#: a module whose classification a human had to write down.
CLASSIFICATION = {
    # A reset time handed to a fake capacity stop, never compared against a
    # real elapsed interval.
    "test_a_run_commits_the_history_it_writes.py": BOUNDS_ONLY_SLOWNESS,
    # Elapsed measured after a kill: the command was asked to run far longer
    # than the bound, so a return inside it can only be the kill. A loaded
    # machine makes that more true rather than less.
    "test_brief_fetch.py": BOUNDS_ONLY_SLOWNESS,
    # Reset times composed for a fake capacity stop, and the bound a pause is
    # held to, compared against nothing the machine does.
    "test_capacity_pause.py": BOUNDS_ONLY_SLOWNESS,
    "test_command_transport.py": CARRIES_PRECONDITIONS,
    "test_config_keys_are_obeyed.py": CARRIES_PRECONDITIONS,
    # A timestamp arithmetic for history files written days in the past.
    "test_cross_run_history_retention.py": BOUNDS_ONLY_SLOWNESS,
    # Elapsed after a kill, and a generous poll for a marker to appear.
    "test_filed_query.py": BOUNDS_ONLY_SLOWNESS,
    "test_no_test_bounds_the_machine.py": CARRIES_PRECONDITIONS,
    "test_plan_commit.py": CARRIES_PRECONDITIONS,
    "test_pty_drain.py": CARRIES_PRECONDITIONS,
    "test_the_approval_is_observed.py": CARRIES_PRECONDITIONS,
}

#: The shared helpers that report through `machine_load` on a caller's behalf —
#: one waiting on a path a caller knows, and one waiting on a path only the
#: planning worktree's name completes. A module reaching the route this way
#: names the helper rather than the module, and these are the only other
#: spellings the rule below admits.
SHARED_ROUTES = ("wait_for_the_session_to_write",
                 "wait_for_the_planning_session_to_write")


def enumeration_problems(found, classified) -> list[str]:
    """What stops `classified` from covering `found` exactly.

    A function rather than a pair of inline assertions, so each direction can
    be shown reporting against a constructed pair rather than only observed to
    be silent against the real one.
    """
    problems = []
    for name in sorted(set(found) - set(classified)):
        problems.append(f"{name} sleeps or reads a clock and is not classified")
    for name in sorted(set(classified) - set(found)):
        problems.append(f"{name} is classified and no longer reads a clock")
    return problems


def test_every_clock_reading_module_is_classified_and_every_classified_one_reads_one():
    assert enumeration_problems(modules_reading_a_clock(TESTS_DIR),
                                CLASSIFICATION) == []
    # The companion the scan needs: a scan finding nothing would satisfy the
    # first direction for a reason that has nothing to do with clocks.
    assert modules_reading_a_clock(TESTS_DIR)


def test_the_comparison_reports_a_module_that_started_reading_a_clock(tmp_path):
    """The control for the first direction, constructed rather than argued."""
    planted = tmp_path / "tests"
    planted.mkdir()
    (planted / "test_newly_timed.py").write_text(
        "import time\n\n\ndef test_it():\n    time.sleep(0.01)\n",
        encoding="utf-8")
    (planted / "test_untimed.py").write_text(
        "def test_it():\n    assert True\n", encoding="utf-8")

    found = modules_reading_a_clock(planted)
    assert found == ["test_newly_timed.py"]
    assert enumeration_problems(found, {}) == [
        "test_newly_timed.py sleeps or reads a clock and is not classified"]


def test_the_comparison_reports_a_classification_for_a_module_that_stopped():
    """The control for the other direction."""
    assert enumeration_problems([], {"test_retired.py": BOUNDS_ONLY_SLOWNESS}) == [
        "test_retired.py is classified and no longer reads a clock"]


def test_no_module_is_left_pending_between_the_two_kinds():
    """The sweep is total over what the enumeration finds.

    Every classified module is one kind or the other, and both kinds are in
    use — a table that had collapsed to a single kind would still satisfy the
    containment and would mean the distinction had stopped being drawn.
    """
    kinds = set(CLASSIFICATION.values())
    assert kinds == {BOUNDS_ONLY_SLOWNESS, CARRIES_PRECONDITIONS}


def reaches_the_helper(source: str) -> bool:
    """Whether `source` reports an unmet precondition through the one route."""
    return (machine_load.__name__ in source
            or any(route in source for route in SHARED_ROUTES))


@pytest.mark.parametrize("module", sorted(
    name for name, kind in CLASSIFICATION.items()
    if kind == CARRIES_PRECONDITIONS))
def test_each_module_carrying_a_precondition_reaches_the_helper(module: str):
    assert reaches_the_helper((TESTS_DIR / module).read_text(encoding="utf-8"))


def test_the_route_check_reports_a_module_that_reaches_nothing():
    """The negative control for the assertion above.

    A source that sleeps and reports nowhere is what a module reclassified as
    carrying a precondition, without having been converted, would look like.
    """
    assert not reaches_the_helper("import time\n\n\ndef test_it():\n"
                                  "    time.sleep(0.01)\n")


# --------------------------------------------------------------------------
# 2. The single route: no module skips for load by any other means
# --------------------------------------------------------------------------

#: What makes a skip's stated reason a machine-load reason. A vocabulary rather
#: than a judgement, so the rule can be run over source rather than read by a
#: person, and so a later author's rewording of an existing skip is measured
#: against the same words this story was.
LOAD_VOCABULARY = ("load", "slow", "machine", "busy", "timing", "timed out",
                   "timeout", "deadline", "flake", "flaky", "elapsed",
                   "wall clock", "wall-clock", "too long")


def _is_skip_call(func: ast.expr) -> bool:
    """`pytest.skip(...)`, and a bare `skip(...)` for the imported spelling.

    The bare form also matches a local helper that happens to be called
    `skip`, which this suite has. That is deliberate: a rule that only saw the
    qualified spelling would be evaded by `from pytest import skip`,
    and a local helper's reason is held to the same vocabulary as anyone
    else's rather than excused.
    """
    if isinstance(func, ast.Attribute):
        return func.attr == "skip" and isinstance(func.value, ast.Name) \
            and func.value.id == "pytest"
    return isinstance(func, ast.Name) and func.id == "skip"


def _stated(call: ast.Call) -> str:
    """The literal text of a skip call's reason, f-strings included."""
    pieces = []
    for argument in [*call.args, *(keyword.value for keyword in call.keywords)]:
        for node in ast.walk(argument):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                pieces.append(node.value)
    return " ".join(pieces)


def skip_calls(source: str) -> list[str]:
    """The stated reason of every `pytest.skip` call in `source`."""
    return [_stated(node) for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and _is_skip_call(node.func)]


def load_skips(source: str) -> list[str]:
    """Those of them that skip for a reason about the machine."""
    return [stated for stated in skip_calls(source)
            if any(word in stated.lower() for word in LOAD_VOCABULARY)]


MODULES_UNDER_TESTS = sorted(path.name for path in TESTS_DIR.glob("*.py")
                             if path.name != HELPER_FILE)


@pytest.mark.parametrize("module", MODULES_UNDER_TESTS)
def test_no_module_skips_for_a_machine_load_reason_of_its_own(module: str):
    source = (TESTS_DIR / module).read_text(encoding="utf-8")
    assert load_skips(source) == [], module


def test_the_rule_reports_a_module_that_skips_for_load_directly():
    """The negative control for the absence above, constructed here.

    A module that reaches for `pytest.skip` itself, with the reason this story
    exists to route, is what the rule is looking for — and the same reader
    reports it, so a clean report over the real suite is the absence of such a
    skip rather than a reader that has stopped seeing them.
    """
    planted = ('import pytest\n\n\ndef test_it():\n'
               '    pytest.skip("the machine was too loaded to finish in time")\n')
    assert load_skips(planted) == [
        "the machine was too loaded to finish in time"]


def test_the_rule_sees_the_skips_the_suite_does_carry():
    """The other half the absence needs: the reader is not blind to skips.

    Several modules skip for reasons that are nothing to do with the machine —
    a filesystem that ignores permissions, a tool absent from PATH. The reader
    finds those, and the load filter is what leaves the list above empty.
    """
    stated = [reason for module in MODULES_UNDER_TESTS
              for reason in skip_calls(
                  (TESTS_DIR / module).read_text(encoding="utf-8"))]
    assert stated, "no module under tests/ calls pytest.skip at all"
    assert any(reason.strip() for reason in stated)


def test_the_helper_is_where_the_route_is_and_is_exempt_for_that_reason():
    """The exemption above is one file, and that file is the route.

    Stated as an assertion rather than as a comment: a rule that exempted a
    module which had stopped being the route would exempt nothing useful.
    """
    assert HELPER_FILE not in MODULES_UNDER_TESTS
    assert (TESTS_DIR / HELPER_FILE).is_file()
    assert machine_load.INCONCLUSIVE_PREFIX in str(
        machine_load.inconclusive_error("a reason"))


# --------------------------------------------------------------------------
# 3. Each converted site, shown reporting inconclusive
#
# Every case below constructs the miss rather than waiting for one: a command
# that backgrounds nothing, a child whose source cannot announce, a session
# that never writes, a child that never exits, and commands that sleep past the
# bound their landing half needs them to finish inside.
# --------------------------------------------------------------------------


def reported(raised) -> str:
    """The reason carried by a raised inconclusive report."""
    stated = str(raised.value)
    assert INCONCLUSIVE_PREFIX in stated, stated
    return stated.split(INCONCLUSIVE_PREFIX, 1)[1]


def test_a_command_that_backgrounds_no_child_is_inconclusive(tmp_path):
    """The transport's process-group precondition, missed deliberately.

    A real command, really killed at a real bound — it just never backgrounds
    the child the group assertion is about, so the pid file it would have
    written is not there. That is what a machine too loaded to reach the
    command's first line produces, without needing a loaded machine.
    """
    command = transport_module.fixture_command(
        tmp_path / "commands", "backgrounds-nothing.sh",
        f"sleep {transport_module.LONGER_THAN_ANY_BOUND}\n")
    queue = outbox.queue_dir(tmp_path / "a-target")

    entry = transport_module.filed_through(
        queue, transport_module.transport_for(
            command, timeout=transport_module.KILL_BOUND_SECONDS))
    assert entry["state"] == outbox.PENDING

    with pytest.raises(pytest.skip.Exception) as raised:
        transport_module.spawned_child_pid(tmp_path / "child.pid")
    assert "backgrounded" in reported(raised)

    # The control the report needs: the same helper, against a pid file that
    # was written, hands back the pid. So the report above is the precondition
    # being missed rather than a helper that reports whatever it is given.
    recorded = tmp_path / "recorded.pid"
    recorded.write_text("4242\n", encoding="utf-8")
    assert transport_module.spawned_child_pid(recorded) == 4242


def test_a_child_that_never_announces_itself_is_inconclusive():
    """The pty module's announce precondition, missed deliberately.

    A child whose source cannot announce, rather than a child that was too slow
    to: it exits at once, the read reaches end of file with no announcement,
    and the helper reports the same thing it would report for a child the
    machine never scheduled.
    """
    with pytest.raises(pytest.skip.Exception) as raised:
        pty_module.start_child("import sys\n\nsys.exit(3)\n")
    assert "never announced itself" in reported(raised)


def test_a_session_that_never_writes_is_inconclusive(tmp_path):
    """The wait for the session's artifact, missed deliberately.

    A real child process, really running, which writes nothing — so what the
    wait meets is the case a machine too slow to let the stub get as far as
    writing would produce.
    """
    artifact = tmp_path / "story-900.yaml"
    session = subprocess.Popen(
        [sys.executable, "-c", "import time\ntime.sleep(30)\n"])
    try:
        with pytest.raises(pytest.skip.Exception) as raised:
            plan_module.wait_for_the_session_to_write(artifact, deadline=1.0)
    finally:
        session.kill()
        session.wait(timeout=30)

    assert artifact.name in reported(raised)
    assert not artifact.exists()

    # The control: the same wait, against a path that is there, returns rather
    # than reporting — so the report above is the session's silence and not a
    # wait that reports whatever it is asked about.
    artifact.write_text("id: story-900\n", encoding="utf-8")
    assert plan_module.wait_for_the_session_to_write(
        artifact, deadline=1.0) == artifact


def test_a_drain_whose_deadline_expires_is_inconclusive_by_default():
    """`drain`'s expiry, for the callers that did not come to assert it.

    The sibling of this case lives in `tests/test_pty_drain.py`: the same child
    and the same expiry, driven with `expiry_is_the_claim=True`, is an
    `AssertionError` there. The two callers are told apart by the caller, and
    this is the half that says the child never got there.
    """
    process, master, _ = pty_module.start_child(
        pty_module.CHILD_THAT_NEVER_EXITS)
    pgid = os.getpgid(process.pid)
    # The negative control the absence below needs: the same probe, against
    # this same group while the child is running, reports it alive.
    assert pty_module.process_group_is_alive(pgid)

    with pytest.raises(pytest.skip.Exception) as raised:
        drain(process, master, deadline=1.0)

    stated = reported(raised)
    assert "1s" in stated, stated
    # An inconclusive report is not a licence to leave the child running: the
    # kill and the reap happen exactly as they do when the expiry is a failure.
    assert not pty_module.process_group_is_alive(pgid)
    assert process.poll() is not None, "the hung child was never reaped"


def test_a_sync_command_that_never_finishes_makes_the_landing_half_inconclusive(
        tmp_path):
    """The configured sync bound's landing half, missed deliberately.

    The command sleeps past the bound, so the entry comes back pending naming
    it — which is exactly the shape a machine too slow to spawn a shell inside
    the bound produces for a command that sleeps for nothing.
    """
    sweep = config_module.swept(tmp_path,
                                sleeps=config_module.SLEEPS_PAST_THE_BOUND)

    with pytest.raises(pytest.skip.Exception) as raised:
        config_module.entry_that_got_to_land(sweep, config_module.SYNC_TIMEOUT)
    assert str(config_module.SYNC_TIMEOUT) in reported(raised)


def test_a_query_command_that_never_answers_makes_the_heard_half_inconclusive(
        tmp_path):
    """The configured query bound's answering half, missed the same way."""
    answer = config_module.asked_what_is_filed(
        tmp_path, sleeps=config_module.QUERY_SLEEPS_PAST_THE_BOUND)

    with pytest.raises(pytest.skip.Exception) as raised:
        config_module.answer_that_got_to_be_heard(
            answer, config_module.FILED_QUERY_TIMEOUT)
    assert str(config_module.FILED_QUERY_TIMEOUT) in reported(raised)


def test_the_halves_beside_each_missed_precondition_cannot_skip(tmp_path):
    """A missed precondition takes no load-independent assertion with it.

    The two halves of each configured bound that do not depend on the machine
    are driven here in the same conditions that make their sibling report
    inconclusive above: the bound is still read off what the harness built, and
    a command sleeping past it is still killed. Neither can reach the helper,
    so neither can be skipped by the other's precondition.
    """
    built = config_module.swept(tmp_path / "built", run=False)
    assert built.transport.timeout == config_module.SYNC_TIMEOUT

    settings, problem = config_module.filed_query.resolve_settings(
        dict(config_module.VARYING))
    assert problem == ""
    assert settings.timeout == config_module.FILED_QUERY_TIMEOUT

    for name in ("test_sync_timeout_seconds_is_the_bound_a_sync_command_is_held_to",
                 "test_a_sync_command_sleeping_past_the_bound_is_killed_and_left_pending",
                 "test_filed_query_timeout_seconds_is_the_bound_a_query_is_held_to",
                 "test_a_query_command_sleeping_past_the_bound_is_killed_and_unanswered"):
        source = inspect.getsource(getattr(config_module, name))
        assert not reaches_the_helper(source), name


# --------------------------------------------------------------------------
# 4. The ceiling, over suites this module builds and runs
#
# The nested suites install the shipped hooks by importing them out of
# `tests/machine_load.py`, which is how `tests/conftest.py` installs them, so
# what these runs demonstrate is the shipped ceiling rather than a restatement
# of it.
# --------------------------------------------------------------------------

NESTED_CONFTEST = f"""\
import sys

sys.path.insert(0, {str(TESTS_DIR)!r})

from machine_load import (pytest_runtest_logreport,  # noqa: F401
                          pytest_sessionfinish)
"""


def reason_for(ordinal: int) -> str:
    return (f"precondition number {ordinal} that this constructed suite "
            f"could not meet")


def build_suite(directory: Path, *, inconclusive: int = 0,
                declared_skips: int = 0, passing: int = 1) -> Path:
    """A runnable suite carrying exactly the results asked for."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "conftest.py").write_text(NESTED_CONFTEST, encoding="utf-8")

    lines = ["import pytest", "", "import machine_load", "", ""]
    for ordinal in range(inconclusive):
        lines += [f"def test_a_precondition_that_was_missed_{ordinal}():",
                  f"    machine_load.inconclusive({reason_for(ordinal)!r})",
                  "", ""]
    for ordinal in range(declared_skips):
        lines += ['@pytest.mark.skip(reason="declared, and nothing to do with '
                  'the machine")',
                  f"def test_suppressed_by_a_declared_marker_{ordinal}():",
                  "    assert True", "", ""]
    for ordinal in range(passing):
        lines += [f"def test_that_passes_{ordinal}():", "    assert True",
                  "", ""]
    (directory / "test_generated.py").write_text("\n".join(lines),
                                                 encoding="utf-8")
    return directory


def run_suite(directory: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(directory), *extra],
        cwd=directory.parent, capture_output=True, text=True)


def output_of(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


def test_a_run_at_the_ceiling_exits_zero(tmp_path):
    """The not-firing half. A run that reported inconclusive results up to the
    ceiling still proved everything else it ran, and passes."""
    suite = build_suite(tmp_path / "at-the-ceiling",
                        inconclusive=MAX_INCONCLUSIVE, passing=2)
    result = run_suite(suite)

    printed = output_of(result)
    assert result.returncode == 0, printed
    # And the results were reported rather than never produced, so the zero is
    # a run that reported up to the ceiling rather than a run that reported
    # nothing at all.
    assert f"{MAX_INCONCLUSIVE} skipped" in printed, printed


def test_a_run_past_the_ceiling_exits_non_zero_naming_each_result(tmp_path):
    """The firing half, and what it has to say when it fires.

    Non-zero is not enough on its own: a reader of a red run needs to know
    which tests were inconclusive and why, or the ceiling is a bare number
    telling them to run it again.
    """
    past = MAX_INCONCLUSIVE + 1
    suite = build_suite(tmp_path / "past-the-ceiling", inconclusive=past,
                        passing=2)
    result = run_suite(suite)
    printed = output_of(result)

    assert result.returncode != 0, printed
    # Red although nothing failed: the verdict came from the ceiling rather
    # than from a test that went wrong, which is the whole of what it adds.
    assert "failed" not in printed, printed
    assert "passed" in printed, printed
    for ordinal in range(past):
        assert f"test_a_precondition_that_was_missed_{ordinal}" in printed
        assert reason_for(ordinal) in printed


def test_the_two_runs_differ_only_in_how_many_were_inconclusive(tmp_path):
    """The control the pair above needs.

    Both suites are built by the same generator and run by the same command,
    so the verdict that differs between them is the count and nothing else.
    """
    at = build_suite(tmp_path / "at", inconclusive=MAX_INCONCLUSIVE, passing=2)
    past = build_suite(tmp_path / "past", inconclusive=MAX_INCONCLUSIVE + 1,
                       passing=2)

    assert (at / "conftest.py").read_text(encoding="utf-8") == \
        (past / "conftest.py").read_text(encoding="utf-8")
    assert run_suite(at).returncode == 0
    assert run_suite(past).returncode != 0


def test_a_declared_skip_does_not_spend_the_ceiling(tmp_path):
    """A run carrying both kinds: declared markers and inconclusive results.

    The declared skips outnumber the ceiling on their own. They are skips a
    human wrote down for a reason that is not the machine, and counting them
    would make the ceiling a bound on skipping rather than on what this story
    is about. Their stated reason mentions the machine even so, because what
    the count is decided on is the prefix the helper writes rather than the
    wording of a reason anybody can choose.
    """
    suite = build_suite(tmp_path / "both-kinds",
                        declared_skips=MAX_INCONCLUSIVE + 2,
                        inconclusive=MAX_INCONCLUSIVE, passing=1)
    result = run_suite(suite)
    printed = output_of(result)

    assert result.returncode == 0, printed
    assert "test_suppressed_by_a_declared_marker_0" not in printed
    # And the same suite with one more of the kind that *does* count is red,
    # so the pass above is the markers being ignored rather than the ceiling
    # having stopped looking.
    louder = build_suite(tmp_path / "both-kinds-louder",
                         declared_skips=MAX_INCONCLUSIVE + 2,
                         inconclusive=MAX_INCONCLUSIVE + 1, passing=1)
    assert run_suite(louder).returncode != 0


def test_the_ceiling_is_decided_once_on_the_controller(tmp_path):
    """Under xdist, over every worker's reports, and reported once.

    The suite this repository runs is sharded across workers. A count read per
    worker would be a looser ceiling than the one declared — each worker seeing
    only its own shard — and would report once per worker if it fired at all.
    """
    past = MAX_INCONCLUSIVE + 1
    suite = build_suite(tmp_path / "sharded", inconclusive=past, passing=4)
    result = run_suite(suite, "-n", "2")
    printed = output_of(result)

    assert result.returncode != 0, printed
    assert printed.count("inconclusive results, more than the") == 1, printed
    for ordinal in range(past):
        assert reason_for(ordinal) in printed
