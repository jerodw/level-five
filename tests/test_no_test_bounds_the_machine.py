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
    backgrounds a child — at the transport, and at the two seams whose kill
    cases took the transport's pid-based form and whose helper the fetch
    module borrows — a child whose source never announces itself, a session
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
import test_a_planned_story_is_published_onto_its_item as item_module
import test_brief_fetch as fetch_module
import test_command_transport as transport_module
import test_config_keys_are_obeyed as config_module
import test_filed_query as query_module
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
    # Elapsed after a kill, for the timing cases: the command was asked to run
    # far longer than the bound, so a return inside the ceiling can only be the
    # kill, and a loaded machine makes that more true rather than less. The
    # process-group kill case decides by the child's recorded pid polled out
    # of the process table -- an absence load makes more certain, not less --
    # and then reads the marker once with nothing waited for, since a child
    # that finished its sleep also leaves the table and leaves the marker
    # behind, so both absences together are what say "killed". What it cannot
    # decide is a run whose leader was killed before it had backgrounded the
    # child at all: no pid was recorded, and that is the precondition reported
    # through the helper under the spawn bound rather than passed on nothing.
    # The control waits for a marker to appear, which is what a poll is for.
    "test_a_planned_story_is_published_onto_its_item.py": CARRIES_PRECONDITIONS,
    # A reset time handed to a fake capacity stop, never compared against a
    # real elapsed interval.
    "test_a_run_commits_the_history_it_writes.py": BOUNDS_ONLY_SLOWNESS,
    # Elapsed measured after a kill, for the timing cases: the command was
    # asked to run far longer than the bound, so a return inside it can only be
    # the kill, and a loaded machine makes that more true rather than less. The
    # fetch's process-group kill case takes the pid-based form its dedupe-query
    # sibling in test_filed_query.py takes, through the fixture and helpers it
    # borrows from there: the child's pid polled out of the process table, the
    # marker read once because a child that finished also leaves the table and
    # leaves the marker behind, and a leader killed before it had backgrounded
    # the child reported through the borrowed `spawned_child_pid` -- which is
    # how this module reaches the helper without naming it.
    "test_brief_fetch.py": CARRIES_PRECONDITIONS,
    # Reset times composed for a fake capacity stop, and the bound a pause is
    # held to, compared against nothing the machine does.
    "test_capacity_pause.py": BOUNDS_ONLY_SLOWNESS,
    "test_command_transport.py": CARRIES_PRECONDITIONS,
    "test_config_keys_are_obeyed.py": CARRIES_PRECONDITIONS,
    # A timestamp arithmetic for history files written days in the past.
    "test_cross_run_history_retention.py": BOUNDS_ONLY_SLOWNESS,
    # Elapsed after a kill, and a generous poll for a marker to appear: the
    # stalled fetch's git stub was asked to sit for far longer than the
    # ceiling, so a refresh that came back inside it can only have been killed.
    # The escaped-descendant pair asks nothing of the machine either, and asks
    # it in the other direction: the case rests on the outcome carrying the
    # subject's own exit status -- the field a fetch killed at the bound does
    # not carry -- and on that descendant being seen still running, with no
    # elapsed interval compared against anything; and its control waits on that
    # same subject's output under the short bound, which the descendant holds
    # far past. Load makes both more true rather than less.
    "test_branch_base.py": BOUNDS_ONLY_SLOWNESS,
    # Elapsed after a kill, for the timing cases: the command was asked to run
    # far longer than the bound, so a return inside the ceiling can only be the
    # kill, and a loaded machine makes that more true rather than less. The
    # process-group kill case decides by the child's recorded pid polled out
    # of the process table -- an absence load makes more certain, not less --
    # and then reads the marker once with nothing waited for, since a child
    # that finished its sleep also leaves the table and leaves the marker
    # behind, so both absences together are what say "killed". What it cannot
    # decide is a run whose leader was killed before it had backgrounded the
    # child at all: no pid was recorded, and that is the precondition reported
    # through the helper under the spawn bound rather than passed on nothing.
    # The control waits for a marker to appear, which is what a poll is for.
    "test_filed_query.py": CARRIES_PRECONDITIONS,
    "test_no_test_bounds_the_machine.py": CARRIES_PRECONDITIONS,
    "test_plan_commit.py": CARRIES_PRECONDITIONS,
    "test_pty_drain.py": CARRIES_PRECONDITIONS,
    # Two coordinator runs in two processes, whose configured command records
    # its own interval and holds until released: a run has to have started
    # that command, and both have to have finished, before their intervals
    # can be read. The bound the story adds is observed through an injected
    # clock and never against a real one.
    "test_suite_turns.py": CARRIES_PRECONDITIONS,
    "test_the_approval_is_observed.py": CARRIES_PRECONDITIONS,
}

#: The shared helpers that report through `machine_load` on a caller's behalf —
#: one waiting on a path a caller knows, one waiting on a path only the
#: planning worktree's name completes, and one reading the pid a spawned child
#: recorded, which test_brief_fetch.py borrows from test_filed_query.py. A
#: module reaching the route this way names the helper rather than the module,
#: and these are the only other spellings the rule below admits.
SHARED_ROUTES = ("wait_for_the_session_to_write",
                 "wait_for_the_planning_session_to_write",
                 "spawned_child_pid")


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


@pytest.mark.parametrize("module", [query_module, fetch_module, item_module],
                         ids=lambda module: module.__name__)
def test_a_command_that_backgrounds_no_child_is_inconclusive_for_each_seam(
        module, tmp_path):
    """The same precondition, missed deliberately, at each seam whose kill
    case took the transport's form.

    Each module's `spawned_child_pid` is driven against a pid file nothing
    wrote, which is what a leader killed before it reached its first line
    leaves behind, and the report is required to name the spawn bound that
    module declares -- so a reader of a run that reported it is told which
    precondition the machine did not let happen. The fetch module borrows the
    query module's helper today, and it is driven as itself rather than
    through the module it borrows from: whatever it reaches the route through
    is what gets driven, so the borrowing is asserted here rather than
    described, and a helper of the fetch module's own would be held to the
    same report the moment it appeared.
    """
    with pytest.raises(pytest.skip.Exception) as raised:
        module.spawned_child_pid(tmp_path / "never-written.pid")
    reason = reported(raised)
    assert "backgrounded" in reason
    assert f"{module.SPAWN_BOUND_SECONDS:g}s" in reason

    # The control the report needs: the same helper, against a pid file that
    # was written, hands back the pid. So the report above is the precondition
    # being missed rather than a helper that reports whatever it is given.
    recorded = tmp_path / "recorded.pid"
    recorded.write_text("4242\n", encoding="utf-8")
    assert module.spawned_child_pid(recorded) == 4242


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
# 3a. A configured-bound proof that is not about the bound runs under the
#     harness default
#
# The config module varies every key in every fixture, so a proof that spawns
# a fixture command inherits the tight timeout for that command unless it
# says otherwise. A proof that is about the bound wants that; a proof about
# which command ran, or how many entries were attempted, does not, and under
# the tight bound it is also asserting that this machine spawns a shell inside
# a few seconds — which is what went red on story-177's second attempt. The
# module's remedy is the `not_about_the_*_bound()` helper for each command,
# which hands the fixture the harness's own default. What is held here is
# that every proof of that shape takes one side or the other.
# --------------------------------------------------------------------------

#: Each way a test in the config module runs a fixture command, and for that
#: command the bound constant a proof about the bound names and the helper a
#: proof that is not about it passes. The runners and helpers are read off the
#: module as its own functions rather than spelled here; the constants are
#: names, checked below to exist on the module so the table cannot go on
#: naming a constant that was renamed away.
FIXTURE_COMMAND_RUNNERS = {
    config_module.swept.__name__:
        ("SYNC_TIMEOUT", config_module.not_about_the_sync_bound.__name__),
    f"{config_module.outbox_sweep.__name__}.{config_module.outbox_sweep.sweep.__name__}":
        ("SYNC_TIMEOUT", config_module.not_about_the_sync_bound.__name__),
    config_module.asked_what_is_filed.__name__:
        ("FILED_QUERY_TIMEOUT", config_module.not_about_the_bound.__name__),
    config_module.published_from.__name__:
        ("ITEM_UPDATE_TIMEOUT", config_module.not_about_the_item_bound.__name__),
}

#: The one keyword by which `swept` is asked to build the transport and run
#: nothing, so a call carrying it spawns no command and is not held to this.
RUNS_NOTHING = ("run", False)


def _dotted(func: ast.expr) -> str | None:
    """`name` or `module.name` for a call's callee, and None for any other."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    return None


def _runs_nothing(call: ast.Call) -> bool:
    keyword_name, keyword_value = RUNS_NOTHING
    return any(keyword.arg == keyword_name
               and isinstance(keyword.value, ast.Constant)
               and keyword.value.value is keyword_value
               for keyword in call.keywords)


def fixture_commands_run_by(function: ast.FunctionDef) -> set[str]:
    """Which runners in the table `function` calls, ignoring a call that is
    asked to run nothing."""
    return {name for node in ast.walk(function)
            if isinstance(node, ast.Call)
            for name in [_dotted(node.func)]
            if name in FIXTURE_COMMAND_RUNNERS and not _runs_nothing(node)}


def _names_referenced(function: ast.FunctionDef) -> set[str]:
    return {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}


def _helpers_called(function: ast.FunctionDef) -> set[str]:
    return {name for node in ast.walk(function)
            if isinstance(node, ast.Call)
            for name in [_dotted(node.func)] if name}


def configured_bound_problems(source: str) -> list[str]:
    """Every test in `source` that runs a fixture command under a configured
    bound without being about that bound or configuring the default.

    A test is about the bound when its source names the bound constant for the
    command it runs; it configures the default when it calls the helper for
    that command. Either satisfies the rule. Every test that runs a fixture
    command is found by what it calls, so a fourth proof written in the
    inherited-bound shape is reported without being listed anywhere.
    """
    problems = []
    for function in ast.parse(source).body:
        if not isinstance(function, ast.FunctionDef) \
                or not function.name.startswith("test_"):
            continue
        referenced = _names_referenced(function)
        called = _helpers_called(function)
        for runner in sorted(fixture_commands_run_by(function)):
            bound, helper = FIXTURE_COMMAND_RUNNERS[runner]
            if bound not in referenced and helper not in called:
                problems.append(
                    f"{function.name} runs a fixture command through "
                    f"{runner} under the configured bound, and neither names "
                    f"{bound} nor passes {helper}()")
    return problems


def config_module_source() -> str:
    return Path(config_module.__file__).read_text(encoding="utf-8")


def test_every_bound_constant_the_table_names_is_one_the_module_declares():
    """The table names its constants as strings, and a string cannot be
    imported — so each is checked to be a number the module still declares,
    and the tight value rather than the default the helper hands out."""
    for bound, helper in FIXTURE_COMMAND_RUNNERS.values():
        assert isinstance(getattr(config_module, bound), (int, float)), bound
        handed = getattr(config_module, helper)()
        assert getattr(config_module, bound) not in handed.values(), bound


def test_every_configured_bound_proof_is_about_the_bound_or_runs_under_the_default():
    """The standing assertion, against the config module as it is."""
    assert configured_bound_problems(config_module_source()) == []


def test_the_rule_sees_a_test_behind_every_runner_in_the_table():
    """The companion the absence above needs: the scan is finding the tests
    that run fixture commands rather than finding nothing.

    Every runner in the table is reached by at least one test in the live
    module, and each of the two sides — a proof naming its bound, and a proof
    passing the helper — is in use. Either side having no member would mean
    the distinction had stopped being drawn.
    """
    tests = [function for function in ast.parse(config_module_source()).body
             if isinstance(function, ast.FunctionDef)
             and function.name.startswith("test_")]
    reached = set().union(*(fixture_commands_run_by(test) for test in tests))
    assert reached == set(FIXTURE_COMMAND_RUNNERS)

    about_the_bound = [test.name for test in tests
                       if fixture_commands_run_by(test)
                       and any(bound in _names_referenced(test)
                               for bound, _ in FIXTURE_COMMAND_RUNNERS.values())]
    under_the_default = [test.name for test in tests
                         if fixture_commands_run_by(test)
                         and any(helper in _helpers_called(test)
                                 for _, helper in FIXTURE_COMMAND_RUNNERS.values())]
    assert about_the_bound
    assert under_the_default
    assert not set(about_the_bound) & set(under_the_default)


def _proof_source(body: str, *, name: str = "test_it") -> str:
    return f"def {name}(tmp_path):\n{body}"


def test_the_rule_reports_a_proof_that_neither_names_its_bound_nor_configures_the_default():
    """The negative control, constructed for each runner in the table.

    A proof that spawns a fixture command and asserts nothing about the
    bound, in the shape the three proofs had before this rule existed, is
    reported naming the test, the runner, the constant and the helper.
    """
    for runner, (bound, helper) in FIXTURE_COMMAND_RUNNERS.items():
        planted = _proof_source(
            f"    result = {runner}(tmp_path)\n"
            f"    assert result\n", name="test_inherits_the_bound")
        problems = configured_bound_problems(planted)
        assert len(problems) == 1, problems
        for named in ("test_inherits_the_bound", runner, bound, f"{helper}()"):
            assert named in problems[0], (named, problems[0])


def test_the_rule_accepts_a_proof_on_either_side_and_a_call_that_runs_nothing():
    """The positive controls beside the negative one.

    The same reader is silent on a proof naming its bound, on a proof passing
    the helper — including the `fixture_config(**helper())` spelling the
    sweep-max-entries proof uses — and on a `swept` asked to build the
    transport and run nothing, so the empty report above is the rule being
    satisfied rather than a reader that reports nothing.
    """
    for runner, (bound, helper) in FIXTURE_COMMAND_RUNNERS.items():
        names_its_bound = _proof_source(
            f"    result = {runner}(tmp_path)\n"
            f"    assert str({bound}) in result.reason\n")
        assert configured_bound_problems(names_its_bound) == []

        passes_the_helper = _proof_source(
            f"    result = {runner}(tmp_path, **{helper}())\n"
            f"    assert result\n")
        assert configured_bound_problems(passes_the_helper) == []

        through_the_config = _proof_source(
            f"    values = fixture_config(**{helper}())\n"
            f"    result = {runner}(tmp_path, values)\n"
            f"    assert result\n")
        assert configured_bound_problems(through_the_config) == []

    keyword_name, keyword_value = RUNS_NOTHING
    builds_only = _proof_source(
        f"    built = {config_module.swept.__name__}"
        f"(tmp_path, {keyword_name}={keyword_value!r})\n"
        f"    assert built.transport\n")
    assert configured_bound_problems(builds_only) == []
    # And a helper that is not a test is not what the rule is over: the
    # runners themselves live in the module and run nothing on their own.
    assert configured_bound_problems(
        f"def helper(tmp_path):\n"
        f"    return {config_module.swept.__name__}(tmp_path)\n") == []


#: Each helper, the orchestration module whose key it hands out, and the
#: module whose default it hands out at that key. The sync default lives on
#: the transport rather than on the sweep module, which is where the sweep
#: itself falls back to when the key is absent.
NOT_ABOUT_THE_BOUND_HELPERS = [
    (config_module.not_about_the_sync_bound, config_module.outbox_sweep,
     config_module.command_transport),
    (config_module.not_about_the_bound, config_module.filed_query,
     config_module.filed_query),
    (config_module.not_about_the_item_bound, config_module.item_update,
     config_module.item_update),
]


@pytest.mark.parametrize("helper, keyed_by, defaulted_by",
                         NOT_ABOUT_THE_BOUND_HELPERS,
                         ids=lambda value: value.__name__)
def test_each_not_about_the_bound_helper_hands_the_fixture_exactly_the_harness_default(
        helper, keyed_by, defaulted_by):
    """What each helper returns is one key, the one the orchestration module
    reads, at the value the harness falls back to — read off the orchestration
    modules rather than restated, so a helper cannot drift into a bound of the
    test module's own."""
    handed = helper()
    assert list(handed) == [keyed_by.TIMEOUT_KEY]
    assert handed[keyed_by.TIMEOUT_KEY] == defaulted_by.DEFAULT_TIMEOUT_SECONDS
    # The control the equality needs: the default is not the tight value the
    # fixture varies the key to — compared as numbers, since the fixture
    # writes its values as text — so the helper is a departure from the
    # fixture rather than a restatement of it.
    assert float(handed[keyed_by.TIMEOUT_KEY]) != \
        float(config_module.VARYING[keyed_by.TIMEOUT_KEY])


def test_the_helper_table_covers_every_runner_in_the_rule():
    """The two tables agree: every helper the rule accepts is one whose
    return is checked above, and no helper is checked that the rule does not
    accept."""
    accepted = {helper for _, helper in FIXTURE_COMMAND_RUNNERS.values()}
    checked = {helper.__name__ for helper, _, _ in NOT_ABOUT_THE_BOUND_HELPERS}
    assert accepted == checked


# --------------------------------------------------------------------------
# 3b. No proof node may report inconclusive
#
# The config module's account of why a bound is made irrelevant rather than
# widened rests on this: the pristine-copy control asserts that every node in
# KEY_PROOFS *passed*, so a proof node that reported inconclusive would turn
# that control red on a loaded machine. The rule in 3a does not hold it — a
# proof node rewritten to route through `entry_that_got_to_land` names its
# bound and so satisfies 3a — and `reaches_the_helper` does not see it either,
# since the `machine_load.inconclusive` call sits inside those two routes
# rather than in their callers. So it is held here, over every proof node, by
# name of the routes the config module itself declares.
# --------------------------------------------------------------------------

#: The config module's own inconclusive routes, read off it as names so a
#: renamed route cannot leave this looking for the old spelling.
CONFIG_MODULE_ROUTES = (config_module.entry_that_got_to_land.__name__,
                        config_module.answer_that_got_to_be_heard.__name__)


def reaches_an_inconclusive_route(source: str) -> bool:
    """Whether `source` can report inconclusive, through the shared helper or
    either of the config module's own routes to it."""
    return (reaches_the_helper(source)
            or any(f"{route}(" in source for route in CONFIG_MODULE_ROUTES))


def proof_node_sources() -> dict[str, str]:
    return {proof.node: inspect.getsource(getattr(config_module, proof.node))
            for proof in config_module.KEY_PROOFS.values()}


def test_no_proof_node_reaches_an_inconclusive_route():
    """The standing assertion: every node the pristine control requires to
    have passed has no route by which it could report anything else."""
    for node, source in sorted(proof_node_sources().items()):
        assert not reaches_an_inconclusive_route(source), node


def test_the_proof_node_reader_finds_every_node():
    """The positive control the absence above needs: the sources read are
    one per proof in the table, and the table is not empty."""
    sources = proof_node_sources()
    assert len(sources) == len(config_module.KEY_PROOFS)
    assert sources
    assert all(source.lstrip().startswith(("def ", "@"))
               for source in sources.values())


def test_the_proof_node_reader_reports_a_node_routed_through_either_route():
    """The negative control: a proof node rewritten in the shape the previous
    ARCHITECTURE.md paragraph prescribed — the sync-command proof routed
    through `entry_that_got_to_land` with its bound — is reported, as is one
    routed through `answer_that_got_to_be_heard`, and one reaching the shared
    helper directly. The source that does none is not, so the report is
    about the route rather than about any proof node."""
    for route, bound in zip(CONFIG_MODULE_ROUTES,
                            ("SYNC_TIMEOUT", "FILED_QUERY_TIMEOUT")):
        routed = _proof_source(
            f"    result = {route}({config_module.swept.__name__}"
            f"(tmp_path), {bound})\n"
            f"    assert result\n")
        assert reaches_an_inconclusive_route(routed), route
    assert reaches_an_inconclusive_route(_proof_source(
        f"    {machine_load.__name__}.inconclusive('loaded')\n"))
    assert not reaches_an_inconclusive_route(_proof_source(
        f"    result = {config_module.swept.__name__}(tmp_path)\n"
        f"    assert result\n"))


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
