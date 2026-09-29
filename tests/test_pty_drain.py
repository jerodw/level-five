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
    module source that defines one, which the same reader reports;
  * "a child that registers its exit-time callback only after announcing
    itself writes nothing at shutdown" sits beside the same child with the
    same registration ordered *ahead* of the announcement, which does write --
    so the empty read is the placement of the registration and not a pty this
    module has stopped reading.

The children driven here are of this module's making: short scripts run under
`sys.executable` on a pty this module opens. One precondition below does depend
on the machine: every case needs its child to have announced itself before the
parent signals it, and `start_child` waits `ANNOUNCE_DEADLINE` for that
announcement. A child that has not spoken by then is killed and reaped exactly
as before, and what is reported is `machine_load.inconclusive` rather than a
failure -- the test never reached the teardown it exists to be about, so a
failure would say the teardown is broken when what happened is that the run
could not be conducted. Nothing else here depends on machine load, on the rest
of the suite, or on any timing a test cannot control: `HANDLER_DELAY` and
`WINDOWED_TEARDOWN_SILENCE` order two events by construction rather than by
winning a race, `before_announcing` orders a child's own setup ahead of the
signal the same way and the control beside it puts that setup behind a sleep no
signalled child returns from, and the deadline case drives `drain` against a
child that never exits at all.
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

import machine_load
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


def _appended(fragment: str) -> str:
    """A fragment ready to append at column zero, or nothing where it is empty.

    Appended rather than interpolated into the indented templates below: a
    multi-line fragment substituted there would carry the template's
    indentation on its first line and none on the rest, which
    `textwrap.dedent` cannot repair and the child would refuse to run.
    """
    dedented = textwrap.dedent(fragment).strip("\n")
    return dedented + "\n" if dedented else ""


def child_source(body: str, *, before_announcing: str = "") -> str:
    """A child that announces itself, then runs `body`.

    The announcement is flushed so the parent can wait for the child to be
    running before it signals; anything the body writes afterwards has no
    newline and so stays in the interpreter's own buffer, which is the
    condition the substitution needs -- output that has left the buffer for
    the kernel is already written and there is nothing left to fail at exit.

    `before_announcing` is where a case puts whatever its child must already
    have in place to be signalled at all -- an exit-time callback registered,
    say. The announcement is the child's claim to be *ready for the signal*
    rather than merely to have started, because the parent signals the instant
    it reads "ready": setup left in `body` is setup a child descheduled
    between the flush and its first line has not reached when the signal
    arrives, and the case then observes a child signalled too early rather
    than the teardown it exists to be about. That is not hypothetical -- a
    registration in `body` cost a clean-clone run of this suite one failure,
    the child taking the signal with nothing registered, exiting with the
    signal's own status and writing nothing at all. The window is a couple of
    bytecodes wide and an `import` sits inside it, which is enough under a
    loaded machine; closing it is cheaper than tolerating it, and a bound
    would only have moved the load at which it reopens. A case with nothing to
    set up passes nothing and gets exactly the child it got before.
    """
    setting_up = textwrap.dedent(f"""\
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
        """)
    announcing = textwrap.dedent("""\
        sys.stdout.write("ready\\n")
        sys.stdout.flush()
        """)
    return (setting_up + _appended(before_announcing)
            + announcing + _appended(body))


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

#: What a child writes at interpreter shutdown, after its status is settled.
#: Named once because two cases below read for it and a third asserts where in
#: a child's source the write is arranged.
EXIT_TIME_OUTPUT = "written as it exits"

#: The registration that arranges that write. Shared by the pair of children
#: below so the two of them differ in where this sits and in nothing else.
REGISTERS_AT_EXIT = (
    'import atexit\n'
    f'atexit.register(lambda: sys.stdout.write("{EXIT_TIME_OUTPUT}"))')

#: The two markers the pair is told apart by. Substrings of `child_source`'s
#: own output rather than of any body, so a reader can say which side of the
#: announcement a child's registration fell on.
ANNOUNCEMENT = 'sys.stdout.write("ready'
REGISTRATION = "atexit.register("

#: Registers its exit-time callback *before* announcing itself, so a signal
#: that reaches this child at all reaches it with the callback in place. This
#: is the child the shipped teardown is read through.
CHILD_REGISTERING_BEFORE_ANNOUNCING = child_source(
    'time.sleep(30)', before_announcing=REGISTERS_AT_EXIT)

#: The same child with the registration after the announcement instead, which
#: is where it sat when a clean-clone run of this suite read back the signal's
#: status and an empty pty. The sleep the registration follows is the same
#: thirty seconds every child here gives the parent to signal within, so this
#: child reaches its registration for no signal the parent sends at all --
#: which is what makes the control below ordered by construction rather than
#: by winning a race.
CHILD_REGISTERING_AFTER_ANNOUNCING = child_source(
    'time.sleep(30)\n' + REGISTERS_AT_EXIT)


def without_the_registration(source: str) -> str:
    """`source` with the exit-time registration's own lines taken out.

    What is left is everything the pair of children above shares, so two
    sources that agree here differ in where the registration sits and in
    nothing else.
    """
    dropped = REGISTERS_AT_EXIT.splitlines()
    return "\n".join(line for line in source.splitlines()
                     if line not in dropped)


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
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break        # EIO: the child closed its side without announcing
            if not chunk:            # the same thing, where the platform reports
                break                # end of file rather than raising
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
        # The child is still killed and reaped; what changes is what is
        # reported. The announcement is a precondition of every case in this
        # module rather than any case's subject, so a child that never got as
        # far as speaking leaves the teardown untested rather than refuted.
        machine_load.inconclusive(
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

    The callback is registered `before_announcing` rather than in the body, so
    a signal that reaches this child at all reaches it with the callback in
    place. Registered in the body it is a race the parent can win, and winning
    it produces a child that exits with the signal's own status and writes
    nothing -- which is an empty pty read as a teardown that closed the master
    early, the opposite of what this case would then be reporting. The two
    cases below are that arrangement driven deliberately and the placement
    asserted where no load can hide it.
    """
    process, master, _ = start_child(CHILD_REGISTERING_BEFORE_ANNOUNCING)
    os.killpg(os.getpgid(process.pid), signal.SIGINT)

    status, output = drain(process, master)

    assert status == SIGNAL_STATUS
    assert EXIT_TIME_OUTPUT in output


def test_a_child_registering_after_announcing_writes_nothing_as_it_exits():
    """The control for the absence the case above would otherwise not notice.

    The same child and the same teardown; the only difference is that the
    registration sits after the announcement, which is where it sat when a
    clean-clone run of this suite read back the signal's status and an empty
    pty. The parent signals the instant it reads "ready", so this child takes
    the signal inside the sleep its registration follows and never reaches the
    registration at all: it exits with the signal's own status having arranged
    no write at shutdown.

    Nothing here races. The sleep is the same thirty seconds every child in
    this module gives the parent to signal within, so the registration is out
    of reach for any signal the parent sends rather than for one that happens
    to be quick.

    What the pair demonstrates is why the case above orders its registration
    ahead of its announcement: an empty read *here* is indistinguishable from
    the empty read a master closed before the reap produces, so the case above
    run on this child would report a broken teardown on the evidence of a
    child of its own making.
    """
    process, master, _ = start_child(CHILD_REGISTERING_AFTER_ANNOUNCING)
    os.killpg(os.getpgid(process.pid), signal.SIGINT)

    status, output = drain(process, master)

    assert status == SIGNAL_STATUS
    assert EXIT_TIME_OUTPUT not in output, (
        "the child was expected to take the signal before reaching its "
        "registration and to write nothing at shutdown, and wrote anyway -- "
        "so this control no longer demonstrates the arrangement the case "
        f"above exists to avoid: {output!r}")


def test_the_pair_differs_only_in_which_side_of_the_announcement_it_registers():
    """The half of the pair above that no machine load can hide.

    Both behavioural cases pass on an idle machine whichever side of the
    announcement the registration sits on -- which is exactly what made the
    original arrangement a flake the suite went green over rather than a
    defect it caught, and why reverting the repair left the suite passing.
    This case asserts the placement itself, so moving the registration back
    into the body reddens the suite on the next run rather than on the next
    loaded one.
    """
    before, after = (CHILD_REGISTERING_BEFORE_ANNOUNCING,
                     CHILD_REGISTERING_AFTER_ANNOUNCING)

    # `index` raises rather than comparing on a source that has quietly lost
    # its registration or its announcement, so neither placement below can be
    # asserted of a child that no longer carries the thing being placed.
    assert before.index(REGISTRATION) < before.index(ANNOUNCEMENT)
    assert after.index(REGISTRATION) > after.index(ANNOUNCEMENT)

    # And the placement is the whole of the difference: everything else the two
    # children do, they do identically, so the behavioural pair above varies
    # one thing.
    assert without_the_registration(before) != before, (
        "taking the registration out changed nothing, so the comparison "
        "below would hold for two children that never carried one")
    assert without_the_registration(before) == without_the_registration(after)


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

    # `expiry_is_the_claim` because here it is: the child is one that never
    # exits, driven against a deadline chosen to expire, so the expiry is what
    # this test asserts rather than a precondition it failed to reach. Every
    # other caller of `drain` means the opposite by an expiry and gets the
    # inconclusive report instead.
    with pytest.raises(AssertionError) as raised:
        drain(process, master, deadline=1.0, expiry_is_the_claim=True)

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


#: The readings that answer "what did the plan commit hold". `committed_paths`
#: takes a repository and a revision; `planned_paths` is the same question put
#: to a `Planning`, which since story-117 is what resolves where the commit is —
#: on the remote when the push landed, on the local branch when it did not.
#: Either spelling is the assertion this module is checking for.
COMMITTED_PATH_READINGS = ("committed_paths", "planned_paths")


def _reads_the_committed_paths(call: ast.Call) -> bool:
    name = call.func
    if isinstance(name, ast.Attribute):
        return name.attr in COMMITTED_PATH_READINGS
    return isinstance(name, ast.Name) and name.id in COMMITTED_PATH_READINGS


def committed_path_lists(source: str) -> list[list[str]]:
    """The literal path lists the source asserts a committed-paths reading equals."""
    asserted = []
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if not isinstance(node, ast.Assert):
            continue
        for compare in ast.walk(node.test):
            if not (isinstance(compare, ast.Compare)
                    and isinstance(compare.left, ast.Call)
                    and _reads_the_committed_paths(compare.left)):
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
