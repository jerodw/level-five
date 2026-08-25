"""Independent validation for story-078: the shared pty teardown does not
race the status the test asserts.

`drain` in `tests/test_plan_commit.py` is the teardown every terminal-driven
module in this suite runs through. It used to give up after a window of
silence, close the pty master, and only then reap the child -- so the master
could be closed while the child still held it and still had unflushed output,
the child's exit-time flush then failed, and the status the parent read back
was the interpreter's flush-failure status rather than the one the child meant
to exit with. That is what turned `assert status == 130` into an observed
`120` in two full-suite runs on macOS.

The subject here is that helper, and the mechanism is *shown* rather than
asserted:

  * **the control-and-subject pair.** One child, one signal, two teardowns.
    Driven through the shipped `drain` the parent reads back the signal's
    status; driven through a teardown that closes the master while the child
    is alive -- the pre-story ordering, written out below -- it reads back a
    different one. The teardown is the only difference between the two runs,
    so the teardown is what substitutes the status.
  * **the deadline.** A child that never exits is reported as a failure naming
    the deadline that expired, and its process group is killed rather than
    left running.
  * **silence is not end of output.** A child that says nothing for longer
    than a window of silence and then exits is drained to its true status and
    its whole output, where the same child through a windowed teardown is cut
    off mid-run.

Every absence asserted here carries a demonstration that the same check
reports the violation it exists to catch:

  * "the killed child's process group does not survive" sits beside the same
    probe run against a child that is still alive, which reports it alive;
  * "the interrupt test admits the signal's status and no other" sits beside
    copies of that test's own source widened to admit a second status and
    stripped of the assertion entirely, both of which the same reader
    reports;
  * "no module that imports `drain` carries its own copy of it" sits beside a
    module source that defines one, which the same reader reports.

The children driven here are of this module's making: short scripts run under
`sys.executable` on a pty this module opens. Nothing below depends on machine
load, on the rest of the suite, or on any timing a test cannot control.
"""
import ast
import inspect
import os
import pty
import selectors
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from test_plan_commit import (
    DRAIN_DEADLINE,
    drain,
    test_an_interrupt_still_commits_what_was_written_and_exits_130
    as interrupt_test,
)

TESTS_DIR = Path(__file__).resolve().parent

#: The status the children below exit with when they take the signal. Chosen
#: to be the one story-023's contract asserts, so the substitution these tests
#: are about is the same substitution the interrupt test saw.
SIGNAL_STATUS = 130

#: The status CPython exits with when it cannot flush its standard streams at
#: shutdown, whatever status the program asked for. Recorded here as the
#: mechanism the reproduction observed; the assertions below require only that
#: the substituted status *differs* from the signal's, so a platform that
#: substitutes some other status still shows the same defect.
FLUSH_FAILURE_STATUS = 120

#: How long a teardown that closes the master early leaves the child holding
#: buffered output. The child's signal handler waits this long before exiting,
#: so the close is ordered before the flush by construction rather than by
#: winning a race, which is what makes the control deterministic.
HANDLER_DELAY = 0.4

#: How long a child gets to announce itself before the helper gives up. A
#: child that dies before announcing leaves nothing to wait for, so this is a
#: hang guard rather than a timing tolerance.
ANNOUNCE_DEADLINE = 30.0

#: The window of silence the pre-story teardown used as both its
#: end-of-output detector and its only bound. The silence test below uses a
#: scaled-down stand-in: the property -- that a gap in a child's output is not
#: the end of it -- does not depend on the length of the gap, and sleeping past
#: the original window would add half a minute to every run of this suite.
WINDOWED_TEARDOWN_SILENCE = 0.5


def child_source(body: str) -> str:
    """A child that announces itself, then runs `body`.

    The announcement is flushed so the parent can wait for the child to be
    running before it signals; anything the body writes afterwards has no
    newline and so stays in the interpreter's own buffer, which is the
    condition the substitution needs -- output that has left the buffer for
    the kernel is already written and there is nothing left to fail at exit.
    """
    announcing = textwrap.dedent(f"""\
        import signal, sys, time

        def handler(signum, frame):
            time.sleep({HANDLER_DELAY})
            sys.exit({SIGNAL_STATUS})

        signal.signal(signal.SIGINT, handler)
        # Closing the master hangs the child's terminal up as well as
        # orphaning its buffered output. Ignoring the hangup leaves the flush
        # as the only way a closed master can reach this child, so what the
        # control below demonstrates is the flush and nothing else.
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        sys.stdout.write("ready\\n")
        sys.stdout.flush()
        """)
    # The body is appended already at column zero rather than interpolated into
    # the indented template above: a multi-line body substituted there would
    # carry the template's indentation on its first line and none on the rest,
    # which `textwrap.dedent` cannot repair and the child would refuse to run.
    return announcing + textwrap.dedent(body).strip("\n") + "\n"


#: Buffers a short line and waits for the signal.
CHILD_HOLDING_UNFLUSHED_OUTPUT = child_source(
    'sys.stdout.write("pending output with no newline")\n'
    'time.sleep(30)')

#: Never exits and never says anything more, so only a deadline ends it.
CHILD_THAT_NEVER_EXITS = child_source(
    'signal.signal(signal.SIGINT, signal.SIG_IGN)\n'
    'time.sleep(600)')

#: Says something, says nothing for longer than a window of silence, then says
#: the rest and exits of its own accord.
CHILD_SILENT_THEN_EXITING = child_source(
    'sys.stdout.write("before the silence\\n")\n'
    'sys.stdout.flush()\n'
    f'time.sleep({WINDOWED_TEARDOWN_SILENCE * 4})\n'
    'sys.stdout.write("after the silence\\n")\n'
    'sys.stdout.flush()\n'
    'sys.exit(7)')


def start_child(source: str):
    """Start a child on a pty of this module's making, running once it is.

    Returns the child, its pty master, and whatever the child had already said
    by the time it announced itself. That third value is returned rather than
    discarded because a single read of the pty can carry the announcement and
    the child's next line together, and a test that asserts on what a teardown
    read would otherwise be missing a line this helper swallowed.
    """
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, "-c", source],
        stdin=slave, stdout=slave, stderr=slave,
        start_new_session=True,
    )
    os.close(slave)
    announcement = b""
    # Bounded for the reason this whole module exists: an unbounded read of a
    # pty spins at full CPU when the child dies before it announces, and there
    # is nothing left to wait for. A child that never says "ready" is killed
    # and reported here rather than hanging the suite that runs this module.
    selector = selectors.DefaultSelector()
    selector.register(master, selectors.EVENT_READ)
    expires = time.monotonic() + ANNOUNCE_DEADLINE
    try:
        while b"ready" not in announcement:
            remaining = expires - time.monotonic()
            if remaining <= 0 or not selector.select(timeout=remaining):
                break
            chunk = os.read(master, 4096)
            if not chunk:            # the child closed its side without announcing
                break
            announcement += chunk
    finally:
        selector.close()
    if b"ready" not in announcement:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait(timeout=30)
        os.close(master)
        raise AssertionError(
            f"the child never announced itself within "
            f"{ANNOUNCE_DEADLINE:g}s; it exited {process.returncode}")
    # Split on the announcement alone, not on the newline after it: a pty
    # translates the child's "\n" into "\r\n" on its way out, so a separator
    # carrying the newline matches nothing and would silently return "".
    _, _, spoken = announcement.decode(errors="replace").partition("ready")
    return process, master, spoken


def teardown_that_closes_the_master_early(process, master: int) -> int:
    """The pre-story ordering: close the master, then reap the child.

    Written out here as the control the shipped teardown is measured against.
    It is not a copy of `drain` for any module to use -- it returns only a
    status, it reads nothing, and this module is its only caller.
    """
    os.close(master)
    return process.wait(timeout=30)


def teardown_that_gives_up_after_silence(process, master: int,
                                         window: float) -> tuple[int, str]:
    """The pre-story loop: treat a gap in the child's output as its end."""
    output, selector = b"", selectors.DefaultSelector()
    selector.register(master, selectors.EVENT_READ)
    try:
        while selector.select(timeout=window):
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
    finally:
        selector.close()
    status = process.wait(timeout=30) if process.poll() is not None else None
    return status, output.decode(errors="replace")


def process_group_is_alive(pgid: int) -> bool:
    """Whether any process in `pgid` is still there to be signalled."""
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


# --------------------------------------------------------------------------
# The mechanism: one child, one signal, two teardowns.
# --------------------------------------------------------------------------


def test_the_shipped_teardown_reads_back_the_signals_own_status():
    process, master, _ = start_child(CHILD_HOLDING_UNFLUSHED_OUTPUT)
    os.killpg(os.getpgid(process.pid), signal.SIGINT)

    status, output = drain(process, master)

    assert status == SIGNAL_STATUS
    # `drain` still returns the child's status *and* its decoded output, so
    # every existing call site's unpacking is unchanged. The buffered line the
    # child was holding reaches the parent because the master outlives it.
    assert "pending output with no newline" in output


def test_a_teardown_that_closes_the_master_early_substitutes_the_status():
    """The control for the assertion above: same child, same signal.

    The only difference is the ordering of the teardown, so a status that
    differs here is a status the teardown produced rather than the child.
    """
    process, master, _ = start_child(CHILD_HOLDING_UNFLUSHED_OUTPUT)
    os.killpg(os.getpgid(process.pid), signal.SIGINT)

    status = teardown_that_closes_the_master_early(process, master)

    assert status != SIGNAL_STATUS, (
        "closing the master while the child held unflushed output was "
        "expected to overwrite the signal's status, and did not -- the "
        "control this story's diagnosis rests on no longer demonstrates "
        "anything")
    assert status == FLUSH_FAILURE_STATUS, (
        f"the substituted status was {status}, not the flush-failure status "
        f"{FLUSH_FAILURE_STATUS} the reproduction recorded")


def test_the_shipped_teardown_closes_the_master_only_after_reaping():
    """The master outlives the child on the path that returns a status.

    Read from the pty rather than from the source: the child's last write is
    made at interpreter shutdown, after the status is settled, and it arrives.
    A master closed before the reap could not have carried it.
    """
    process, master, _ = start_child(child_source(
        'import atexit\n'
        'atexit.register(lambda: sys.stdout.write("written as it exits"))\n'
        'time.sleep(30)'))
    os.killpg(os.getpgid(process.pid), signal.SIGINT)

    status, output = drain(process, master)

    assert status == SIGNAL_STATUS
    assert "written as it exits" in output


# --------------------------------------------------------------------------
# The bound on a child that never exits.
# --------------------------------------------------------------------------


def test_a_child_that_never_exits_fails_naming_the_deadline():
    process, master, _ = start_child(CHILD_THAT_NEVER_EXITS)
    pgid = os.getpgid(process.pid)
    # The negative control for the absence asserted below: the same probe,
    # against this same group while the child is still running, reports it
    # alive -- so a report of "gone" afterwards is the kill and not a probe
    # that has stopped seeing anything.
    assert process_group_is_alive(pgid)

    with pytest.raises(AssertionError) as raised:
        drain(process, master, deadline=1.0)

    assert "1s" in str(raised.value), (
        f"the failure did not name the deadline that expired: {raised.value}")
    assert not process_group_is_alive(pgid)
    assert process.poll() is not None, "the hung child was never reaped"


def test_every_existing_call_site_still_calls_the_teardown_unchanged():
    """The bound arrived without changing how `drain` is called.

    Its two original arguments are still what a caller passes, and the
    deadline every existing call site gets is the documented default -- so
    none of the modules that import it had to be touched.
    """
    parameters = list(inspect.signature(drain).parameters.values())

    assert [parameter.name for parameter in parameters[:2]] == ["process",
                                                                "master"]
    assert all(parameter.default is inspect.Parameter.empty
               for parameter in parameters[:2])
    assert parameters[2].default == DRAIN_DEADLINE


# --------------------------------------------------------------------------
# Silence is not end of output.
# --------------------------------------------------------------------------


def test_a_child_silent_then_exiting_is_drained_to_its_true_status():
    process, master, spoken = start_child(CHILD_SILENT_THEN_EXITING)

    status, output = drain(process, master)

    assert status == 7
    assert "before the silence" in spoken + output
    assert "after the silence" in output


def test_a_teardown_that_gives_up_after_silence_misses_the_rest():
    """The control for the assertion above, on the same child.

    A window of silence shorter than the child's gap ends the read before the
    child has finished speaking and before it has exited at all, which is how
    a window came to stand in for end of output.
    """
    process, master, spoken = start_child(CHILD_SILENT_THEN_EXITING)
    try:
        status, output = teardown_that_gives_up_after_silence(
            process, master, WINDOWED_TEARDOWN_SILENCE)

        assert "before the silence" in spoken + output
        assert "after the silence" not in output
        assert status is None, (
            "the windowed teardown was expected to give up while the child "
            f"was still running, and instead read back {status}")
    finally:
        os.close(master)
        process.wait(timeout=30)


# --------------------------------------------------------------------------
# What the interrupt test asserts, and what its siblings do not carry.
# --------------------------------------------------------------------------


def statuses_a_source_admits(source: str) -> list[int]:
    """Every status literal the source's assertions about `status` admit.

    `assert status == 130` yields one; a widened `assert status in (130, 120)`
    or `assert status == 130 or status == 120` yields both; an assertion that
    has been removed yields none. So a reader can tell the three apart.
    """
    admitted = []
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if not isinstance(node, ast.Assert):
            continue
        for compare in ast.walk(node.test):
            if not isinstance(compare, ast.Compare):
                continue
            if not (isinstance(compare.left, ast.Name)
                    and compare.left.id == "status"):
                continue
            for comparator in compare.comparators:
                elements = (comparator.elts
                            if isinstance(comparator, (ast.Tuple, ast.List,
                                                       ast.Set))
                            else [comparator])
                admitted += [element.value for element in elements
                             if isinstance(element, ast.Constant)]
    return admitted


def committed_path_lists(source: str) -> list[list[str]]:
    """The literal path lists the source asserts `committed_paths` equals."""
    asserted = []
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if not isinstance(node, ast.Assert):
            continue
        for compare in ast.walk(node.test):
            if not (isinstance(compare, ast.Compare)
                    and isinstance(compare.left, ast.Call)
                    and isinstance(compare.left.func, ast.Name)
                    and compare.left.func.id == "committed_paths"):
                continue
            if not all(isinstance(operator, ast.Eq)
                       for operator in compare.ops):
                continue
            asserted += [[element.value for element in comparator.elts]
                         for comparator in compare.comparators
                         if isinstance(comparator, ast.List)]
    return asserted


INTERRUPT_TEST_SOURCE = inspect.getsource(interrupt_test)


def test_the_interrupt_test_still_asserts_the_signals_status_and_no_other():
    assert statuses_a_source_admits(INTERRUPT_TEST_SOURCE) == [SIGNAL_STATUS]


@pytest.mark.parametrize("mutation, admitted", [
    (f"status == {SIGNAL_STATUS}",
     f"status in ({SIGNAL_STATUS}, {FLUSH_FAILURE_STATUS})"),
    (f"status == {SIGNAL_STATUS}",
     f"status == {SIGNAL_STATUS} or status == {FLUSH_FAILURE_STATUS}"),
])
def test_a_widened_status_assertion_is_reported(mutation: str, admitted: str):
    """The negative control for the assertion above.

    The same reader, over the same test's source with its status assertion
    widened to admit the status this story removes, reports more than the
    signal's -- so a source that reports only the signal's is one that has not
    been widened, rather than one the reader has stopped looking at.
    """
    widened = INTERRUPT_TEST_SOURCE.replace(mutation, admitted)
    assert widened != INTERRUPT_TEST_SOURCE, (
        "the mutation matched nothing in the interrupt test's source, so this "
        "control demonstrates nothing")

    assert statuses_a_source_admits(widened) == [SIGNAL_STATUS,
                                                 FLUSH_FAILURE_STATUS]


def test_a_removed_status_assertion_is_reported():
    """The other negative control: relaxation by deletion."""
    stripped = "\n".join(line for line in INTERRUPT_TEST_SOURCE.splitlines()
                         if "status ==" not in line)
    assert stripped != INTERRUPT_TEST_SOURCE

    assert statuses_a_source_admits(stripped) == []


def test_the_interrupt_test_still_asserts_exactly_the_one_artifact():
    assert committed_path_lists(INTERRUPT_TEST_SOURCE) == [
        [".harness/stories/story-900.yaml"]]


def test_a_widened_artifact_assertion_is_reported():
    """The negative control: a second path admitted beside the artifact."""
    widened = INTERRUPT_TEST_SOURCE.replace(
        '[".harness/stories/story-900.yaml"]',
        '[".harness/stories/story-900.yaml", ".harness/stories/other.yaml"]')
    assert widened != INTERRUPT_TEST_SOURCE

    assert committed_path_lists(widened) != [
        [".harness/stories/story-900.yaml"]]


def modules_importing(source_dir: Path, name: str) -> dict[str, str]:
    """Every module under `source_dir` importing `name`, by its source."""
    importers = {}
    for path in sorted(source_dir.glob("test_*.py")):
        source = path.read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and any(
                    alias.name == name for alias in node.names):
                importers[path.name] = source
    return importers


def defines_its_own(source: str, name: str) -> bool:
    """Whether the source defines `name` itself rather than importing it."""
    return any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name == name
               for node in ast.walk(ast.parse(source)))


DRAIN_NAME = drain.__name__
DRAIN_IMPORTERS = modules_importing(TESTS_DIR, DRAIN_NAME)


def test_the_terminal_driven_modules_import_the_shared_teardown():
    """The teardown is shared, so the fix reaches every module through it.

    The set is read off the imports rather than surveyed module by module,
    which is what makes it an answer about the whole suite.
    """
    assert DRAIN_IMPORTERS, (
        f"no module under {TESTS_DIR.name}/ imports {DRAIN_NAME}, so the "
        "checks below have nothing to look at")


@pytest.mark.parametrize("module", sorted(DRAIN_IMPORTERS))
def test_no_importer_carries_its_own_copy_of_the_teardown(module: str):
    assert not defines_its_own(DRAIN_IMPORTERS[module], DRAIN_NAME)


def test_a_module_that_copied_the_teardown_is_reported():
    """The negative control for the absence asserted above.

    The same reader, over an importer's source with a definition of the
    teardown appended, reports it -- so a clean report is the absence of a
    copy rather than a reader that has stopped seeing definitions.
    """
    module, source = sorted(DRAIN_IMPORTERS.items())[0]
    copied = source + f"\n\ndef {DRAIN_NAME}(process, master):\n    ...\n"

    assert not defines_its_own(source, DRAIN_NAME), module
    assert defines_its_own(copied, DRAIN_NAME)
