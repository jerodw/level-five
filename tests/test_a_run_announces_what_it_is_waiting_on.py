"""What a run says before it makes somebody wait.

Every long step inside a run announces itself before it starts and says what
the wait is worth beside it, so a reader watching a console does not read a
silence as a fault. Two steps did not. The post-story inspection began after
the run had said "story completed" — the one moment a developer has been given
permission to stop watching — and then invoked an agent in silence for about as
long as a stage. The pre-flight outbox sweep opened a run the same way, above
the first stage line, running the configured sync command once per queued entry
under a per-entry timeout.

This module is the standing home of that convention, and it holds five things:

  * the composer every announcement renders through, and its refusal of a line
    that says a step started without saying what the wait is worth;
  * the inspection's announcement, shown to stand *before* the invocation by
    what the run's own events log held at the moment the invocation was made,
    rather than by reading the source;
  * the sweep's announcement, shown the same way — by what that log held when
    the drain first reached the transport;
  * the silences that must stay silent, each beside the run of the same fixture
    that does announce, so an absence is a decision the harness took rather
    than a reader looking somewhere nothing was ever written: the inspection
    switched off, the queue empty, and the completion sweep, which is handed no
    run directory and therefore says nothing whatever the queue holds;
  * the suite-rerun announcements, which story-140 rewrote to render through
    that composer and which must therefore produce, character for character,
    the lines they produced before it.

Nothing here reaches a model and nothing here reaches a tracker. The runs are
driven against the two fixtures that already exist for these subjects —
`tests/test_an_inspection_records_what_it_cost.py`'s completing run with a fake
inspector installed, and `tests/test_opportunistic_sweep.py`'s run over a queue
whose configured sync command is a shell script — rather than a third target
built beside them. What this module adds to the second of them is an
observation: a sync command that records what the events log held when it was
called, which is the only way to see a line written *before* a drain rather
than merely before the report the drain writes when it is done.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

import conftest
import outbox
import outbox_sweep
import story_coordinator
import story_inspection

import test_an_inspection_records_what_it_cost as inspecting
import test_opportunistic_sweep as sweeping

from test_an_inspection_records_what_it_cost import (  # noqa: F401 - fixtures
    harness,                                          # used by name
    no_model,
)
from test_opportunistic_sweep import harness as sweep_harness  # noqa: F401
from test_execution_history import (  # noqa: F401 - the standing scan, reused
    ALLOWED_KINDS,                    # rather than copied
    COORDINATOR_SOURCE,
    PLANTED_EMITTER,
    kind_spellings,
)


# ==========================================================================
# The convention: one composer, and a line that cannot omit its cost clause
# ==========================================================================


#: The function every announcement renders its line through, which is what
#: makes an announcement one. An event whose kind merely reads like a beginning
#: is not one of these: `stage-started` says a stage was entered and is the
#: stream's ordinary content, while an announcement is a notice that a wait is
#: about to happen and says what that wait is worth.
COMPOSER = "_announcement"


def composed(node: ast.AST) -> bool:
    """Whether an expression is a call to the shared composer."""
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == COMPOSER)


def message_of(call: ast.Call) -> ast.AST | None:
    """The message an `append_event` call hands the log."""
    if len(call.args) > 1:
        return call.args[1]
    return next((keyword.value for keyword in call.keywords
                 if keyword.arg == "message"), None)


def announcing_calls(source: str) -> dict[str, str]:
    """Each kind in `source` whose line the composer renders, mapped to the
    function that appends it.

    `source` is a parameter rather than the coordinator's own text because the
    controls below drive this same scan over a copy carrying the defect each
    exists to report. A kind assembled at runtime is invisible to a scan like
    this one, and `tests/test_execution_history.py` already refuses one
    outright, so nothing here repeats that refusal.
    """
    found: dict[str, str] = {}

    def visit(node, enclosing: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if (isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == "append_event"
                    and composed(message_of(child))):
                kind = next((keyword.value for keyword in child.keywords
                             if keyword.arg == "kind"), None)
                assert isinstance(kind, ast.Constant), ast.unparse(child)
                found[kind.value] = enclosing
            inner = (child.name
                     if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                     else enclosing)
            visit(child, inner)

    visit(ast.parse(source), None)
    return found


def loose_compositions(source: str) -> list[str]:
    """Every composed line in `source` that is not handed straight to the log.

    The composer's output has one destination. A line built here and carried
    somewhere else is a second output path for the convention, which is what
    `append_event` being the coordinator's one write path exists against.
    """
    tree = ast.parse(source)
    written = {id(message_of(node)) for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Name)
               and node.func.id == "append_event"}
    return [ast.unparse(node) for node in ast.walk(tree)
            if composed(node) and id(node) not in written]


#: Every kind the coordinator announces a long step with, derived from the
#: emitting code rather than listed. Read by the modules that pin a clean run's
#: whole event stream and drop its announcements by kind, so a step that learns
#: to announce itself does not have to be added to each of them by hand.
ANNOUNCEMENT_KINDS = frozenset(announcing_calls(COORDINATOR_SOURCE))

#: The two kinds this story added. Spelled here because an event kind is the
#: coordinator's own vocabulary rather than a name a workflow declares — the
#: same reason every module that reads one spells it — and tied back to the
#: function that emits each below, so a literal here that named nothing the
#: coordinator emits would be reported rather than quietly satisfied.
INSPECTION_KIND = "inspection-started"
SWEEP_KIND = "outbox-sweep-started"


def test_a_composed_line_says_what_started_and_what_the_wait_is_worth():
    """The two halves and the one separator, which is the whole convention."""
    assert story_coordinator._announcement(
        "doing the long thing", "this takes a while") == \
        "doing the long thing; this takes a while"


@pytest.mark.parametrize("cost", ["", " ", "\n\t "])
def test_an_announcement_with_no_cost_clause_is_refused(cost: str):
    """Refused rather than rendered bare, on the fail-loudly standard.

    A line that says a step started and not what the wait is worth is the line
    the convention exists against, and rendering it silently would make the
    omission indistinguishable from a step whose wait genuinely costs nothing.
    """
    with pytest.raises(ValueError):
        story_coordinator._announcement("doing the long thing", cost)


def test_a_clause_that_is_not_empty_still_renders():
    """The control for the refusals above: the composer is refusing the missing
    clause rather than refusing everything handed to it."""
    assert story_coordinator._announcement("doing it", " slowly ") == \
        "doing it;  slowly "


def test_each_step_that_announces_renders_its_line_through_the_composer():
    """One home for the convention, held as a property of the emitting code.

    A step that composed its own line would carry whatever its author
    remembered of the convention, which is how the two steps this story is
    about came to say nothing at all while their neighbours announced. The
    suite rerun is here beside the two new ones because this story rewrote it
    to render through the composer rather than to spell the line itself.
    """
    assert announcing_calls(COORDINATOR_SOURCE) == {
        SUITE_RERUN_KIND: "_suite_rerun_started",
        INSPECTION_KIND: "inspection_started",
        SWEEP_KIND: "outbox_sweep_started",
    }


def test_a_step_that_composes_its_own_line_is_not_taken_for_an_announcement():
    """The control for the reading above: the scan finds announcements by the
    composer and not by anything about the kind.

    A planted emitter naming a kind that reads exactly like an announcement,
    and spelling its own message, is not one — which is also why
    `stage-started` and the rest of the stream are not swept up by
    `ANNOUNCEMENT_KINDS` and dropped from the modules that read a whole stream.
    """
    planted = "a-later-step-started"
    announcing = announcing_calls(
        COORDINATOR_SOURCE + PLANTED_EMITTER.format(kind=planted))
    assert planted not in announcing
    assert planted not in ANNOUNCEMENT_KINDS
    assert "stage-started" not in ANNOUNCEMENT_KINDS


def test_no_composed_line_goes_anywhere_but_the_logs_one_write_path():
    """The composer's output has one destination: `append_event`.

    The control is a planted composition carried somewhere else, which the same
    reader reports — so the empty list is the coordinator having no second
    output path rather than a reader that has stopped seeing one.
    """
    assert loose_compositions(COORDINATOR_SOURCE) == []

    planted = ('\n\ndef _planted_loose(run_dir):\n'
               '    print(_announcement("planted", "this takes a moment"))\n')
    assert loose_compositions(COORDINATOR_SOURCE + planted) == [
        '_announcement(\'planted\', \'this takes a moment\')']


def test_each_new_kind_is_spelled_by_the_one_announcer_that_owns_it():
    """The literals above, tied to the functions that emit them.

    Each kind stays a literal in its own function's body: a shared writer
    taking the kind as a parameter would forward a variable into
    `append_event`, which the standing scan refuses, and would collapse the
    one-kind-one-emitter rule beside it. The composer is the shared part and
    the event write is not.
    """
    spellings = kind_spellings(COORDINATOR_SOURCE)
    assert spellings.get(INSPECTION_KIND) == {"inspection_started"}
    assert spellings.get(SWEEP_KIND) == {"outbox_sweep_started"}
    assert INSPECTION_KIND in ANNOUNCEMENT_KINDS
    assert SWEEP_KIND in ANNOUNCEMENT_KINDS


# ==========================================================================
# Where the two kinds are declared, and where they are not
# ==========================================================================


def test_both_kinds_are_declared_in_the_runs_own_event_enum():
    """The enum is closed, so an undeclared kind fails the run's own history
    against its declaration."""
    assert INSPECTION_KIND in ALLOWED_KINDS
    assert SWEEP_KIND in ALLOWED_KINDS


def test_neither_kind_reaches_a_cross_run_log():
    """An announcement is a run's own business and outlives nothing.

    A cross-run log is a durable summary of what runs *decided*, and a notice
    that a step is about to take a while decides nothing. The control is the
    same projection against the same declaration with the kind added to its
    enum: it produces a record, so the None above is the declaration deciding
    rather than the projection being unable to produce anything at all.
    """
    declarations = story_coordinator.history_log_declarations()
    assert declarations, "no cross-run log is declared at all"
    event = story_coordinator.HISTORY_EVENT_PROPERTY

    for kind in (INSPECTION_KIND, SWEEP_KIND):
        entry = {"sequence": 1, "timestamp": "2026-09-12 08:05:12",
                 event: kind, "message": "a step said it had started"}
        for log, declaration in declarations.items():
            assert story_coordinator.history_record(
                entry, [], "story-001", declaration) is None, (kind, log)

            routed = json.loads(json.dumps(declaration))
            routed["properties"][event]["enum"] = [kind]
            assert story_coordinator.history_record(
                entry, [], "story-001", routed) is not None, (kind, log)


# ==========================================================================
# The suite reruns render what they rendered before
# ==========================================================================


#: The phrase, the stage and the artifact this module hands the suite-rerun
#: announcer. Its own, because what is asserted below is how the pieces are
#: rendered into a line rather than which checks announce or what they say.
A_PHRASE = "for a reason this module made up"
A_STAGE = "a-stage-this-module-named"
AN_ARTIFACT = "an-artifact-this-module-named.json"

#: The kind those announcements carry, derived from the function that emits
#: them rather than spelled, so this half of the module says nothing about the
#: vocabulary that the scan above does not already establish.
SUITE_RERUN_KIND = next(
    kind for kind, functions in kind_spellings(COORDINATOR_SOURCE).items()
    if functions == {"_suite_rerun_started"})


def as_it_rendered_before(phrase: str, cost: str) -> str:
    """The line the suite-rerun announcement produced before story-140 split
    the composer out of it.

    Written here rather than derived from the coordinator: the claim is that
    today's line is character for character the old one, and a formula read out
    of the code being asserted about would agree with itself whatever it said.
    """
    return f"re-running the suite {phrase}; {cost}"


def default_cost(source: str) -> str:
    """The cost clause a call that overrides none takes, read off the
    announcer's own signature."""
    function = next(
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
        and node.name == "_suite_rerun_started")
    where = [argument.arg for argument in function.args.kwonlyargs].index("cost")
    return function.args.kw_defaults[where].value


def overridden_costs(source: str) -> set[str]:
    """Every cost clause a call site in `source` states for itself.

    Collected from the calls rather than listed, so the check below covers the
    override a check whose wait is a different size uses — the one that says
    seconds rather than a suite run — without this module knowing which check
    that is or how many there are.
    """
    stated = set()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_suite_rerun_started"):
            continue
        given = next((keyword.value for keyword in node.keywords
                      if keyword.arg == "cost"), None)
        if given is None:
            continue
        assert isinstance(given, ast.Constant), ast.unparse(node)
        stated.add(given.value)
    return stated


def messages_in(run_dir: Path) -> list[str]:
    """The events log's lines with their timestamps removed."""
    path = run_dir / "events.log"
    if not path.is_file():
        return []
    return [line.split("] ", 1)[-1]
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def history_in(run_dir: Path) -> list[dict]:
    return json.loads(
        (run_dir / "execution-history.json").read_text(encoding="utf-8"))


def test_the_default_clause_renders_the_line_it_rendered_before(tmp_path):
    """The announcement with nothing overridden: its text, its kind, the stage
    it names and the artifact it carries, all as they were."""
    run_dir = tmp_path / "default-clause"
    run_dir.mkdir()

    story_coordinator._suite_rerun_started(
        run_dir, A_STAGE, AN_ARTIFACT, A_PHRASE)

    assert messages_in(run_dir) == [
        as_it_rendered_before(A_PHRASE, default_cost(COORDINATOR_SOURCE))]
    entry = history_in(run_dir)[-1]
    assert entry["event"] == SUITE_RERUN_KIND
    assert entry["stage"] == A_STAGE
    assert entry["artifacts"] == [AN_ARTIFACT]


def test_every_clause_a_call_site_overrides_renders_what_it_rendered_before(
        tmp_path):
    """The other half: a check whose wait is a different size says so, and the
    line it says it in is unchanged too."""
    stated = overridden_costs(COORDINATOR_SOURCE)
    assert stated, "no call site states a clause of its own, so none is covered"
    assert default_cost(COORDINATOR_SOURCE) not in stated

    for index, cost in enumerate(sorted(stated)):
        run_dir = tmp_path / f"stated-clause-{index}"
        run_dir.mkdir()
        story_coordinator._suite_rerun_started(
            run_dir, A_STAGE, AN_ARTIFACT, A_PHRASE, cost=cost)
        assert messages_in(run_dir) == [as_it_rendered_before(A_PHRASE, cost)]


# ==========================================================================
# The post-story inspection says it has started
# ==========================================================================


#: Smaller than the fixture's whole expansion, so the bound really trims and
#: the size the announcement names is the scope the invocation was handed
#: rather than the expansion it was taken from.
A_BOUND_THE_SCOPE_EXCEEDS = 1


def announcements_in(lines, cost: str) -> list[str]:
    """Every line carrying one step's cost clause.

    An announcement is found by the clause it ends with rather than by its
    wording, which is also the assertion that it carries one: a line that had
    stopped saying what the wait is worth would not be found here at all.
    """
    return [line for line in lines if line.endswith("; " + cost)]


def numbers_in(line: str, story_id: str) -> list[int]:
    """The figures a line states, with the story's own identifier taken out of
    it first so the digits in a story id are not read as one of them."""
    return [int(found) for found in re.findall(r"\d+", line.replace(story_id, ""))]


def kinds_of(target: Path, story_id: str) -> list[str]:
    run_dir = conftest.run_dir_for(target, story_id)
    return [entry["event"] for entry in history_in(run_dir)]


@pytest.fixture
def announced(tmp_path, harness, monkeypatch):
    """One completing run whose inspection is switched on and bounded below its
    own scope, with what the events log held at the moment the invocation was
    made.

    The snapshot is taken by the fake inspector itself, which the fixture hands
    an action to run when it is called: the log as the invocation saw it is the
    only observation that distinguishes a line written before the wait from one
    written after it.
    """
    target = inspecting.build_target(
        tmp_path / "announcing",
        **{story_inspection.MAX_FILES_KEY: A_BOUND_THE_SCOPE_EXCEEDS})
    seen: dict[str, list[str]] = {}

    def watch(_root) -> None:
        seen["during"] = inspecting.messages(target)

    code, inspector, _runner = inspecting.narrow_run(
        target, harness, monkeypatch, findings=[inspecting.finding()],
        act=watch)
    assert code == 0
    assert inspector.invocations, \
        "no invocation was made, so there is no ordering here to observe"
    return target, inspector, seen["during"]


def test_the_inspection_says_it_has_started_before_it_invokes(announced):
    """The announcement was already written when the invocation was made, and
    the summary was not: the notice is not the record, and the gap between
    them is the wait."""
    _target, _inspector, during = announced

    assert announcements_in(during, story_coordinator.INSPECTION_COST), during
    assert [line for line in during if "finding(s)" in line] == [], during


def test_the_announcement_stands_before_the_record_in_the_runs_own_history(
        announced):
    """The same ordering as a fact about the stream the run left behind, under
    the kind this story declared, exactly once."""
    target, _inspector, _during = announced
    kinds = kinds_of(target, inspecting.STORY_ID)

    assert kinds.count(INSPECTION_KIND) == 1, kinds
    assert kinds.index(INSPECTION_KIND) < kinds.index(
        story_inspection.INSPECTION_EVENT), kinds


def test_the_announcement_names_the_story_and_the_scope_the_bound_left(
        announced):
    """What is being inspected and how much of it — computed and bounded before
    the line is written, which is what makes the gap after it the invocation
    rather than the whole mechanism.

    The figure it states is the scope the invocation was handed rather than the
    expansion that scope was cut from, and the fixture's bound is smaller than
    that expansion, so the two are distinguishable.
    """
    _target, inspector, during = announced
    handed = inspecting.rendered_paths(inspector.invocations[0]["prompt"])
    assert len(handed) == A_BOUND_THE_SCOPE_EXCEEDS, handed

    line = announcements_in(during, story_coordinator.INSPECTION_COST)[0]
    assert inspecting.STORY_ID in line, line
    assert numbers_in(line, inspecting.STORY_ID) == [len(handed)], line


def test_a_run_with_the_inspection_switched_off_announces_nothing(
        tmp_path, harness, monkeypatch, no_model):
    """Unset is off, and off says nothing at all.

    The control is the run of the same fixture differing in that key alone: it
    announces, so the silence above is the key being unset rather than a reader
    looking for a line nothing ever writes.
    """
    off = inspecting.build_target(tmp_path / "switched-off")
    assert inspecting.run(off, harness, inspecting.Runner(off)) == 0
    assert no_model.calls == 0
    assert announcements_in(inspecting.messages(off),
                            story_coordinator.INSPECTION_COST) == []
    assert INSPECTION_KIND not in kinds_of(off, inspecting.STORY_ID)

    on = inspecting.build_target(
        tmp_path / "switched-on",
        **{story_inspection.MAX_FILES_KEY: inspecting.ROOMY_CAP})
    code, inspector, _runner = inspecting.narrow_run(
        on, harness, monkeypatch, findings=[inspecting.finding()])
    assert code == 0 and inspector.invocations
    assert announcements_in(inspecting.messages(on),
                            story_coordinator.INSPECTION_COST) != []
    assert INSPECTION_KIND in kinds_of(on, inspecting.STORY_ID)


# ==========================================================================
# The pre-flight sweep says it has started, and the completion sweep does not
# ==========================================================================


#: How the sweep's own report opens, taken from the renderer that writes it
#: rather than spelled here. The report is what has to stand *after* the
#: announcement, and a prefix written down would go on matching a report that
#: had been reworded into something else.
REPORT_LABEL = outbox_sweep.render(outbox.Summary()).split(" ", 1)[0]

#: What the observing sync command below prefixes each log line it copies with,
#: so the journal can be read back as filings and snapshots rather than as one
#: undifferentiated stream.
SNAPSHOT_LABEL = "log: "


def observing_target(tmp_path, name: str, journal: Path) -> Path:
    """A sweeping target whose sync command records what the run's events log
    held at the moment it was called.

    `tests/test_opportunistic_sweep.py`'s target with its command replaced by
    one that does what that command did and then copies the log. Reading the
    log from inside the drain is the only way to see a line written before the
    drain rather than merely before the report the drain writes afterwards. The
    log's path is resolved here, where the target is known, rather than in the
    script.

    The replacement is committed, because a run refuses a dirty tree and the
    observation may not be what refuses it.
    """
    target = sweeping.build_target(tmp_path / name, journal)
    events = conftest.run_dir_for(target, sweeping.STORY_ID) / "events.log"
    command = target / sweeping.SYNC_COMMAND_REL
    command.write_text(
        "#!/bin/sh\n"
        # The line the module this fixture comes from reads: what was filed,
        # and what the target's HEAD was when it was filed.
        f'printf "filed %s at %s\\n" "$L5_SYNC_KEY" "$(git log -1 --format=%s)"'
        f' >> "{journal}"\n'
        f'if [ -f "{events}" ]; then\n'
        f'  sed "s/^/{SNAPSHOT_LABEL}/" "{events}" >> "{journal}"\n'
        f'fi\n'
        f"exit {sweeping.conftest_transient_exit_code()}\n",
        encoding="utf-8")
    command.chmod(0o755)
    sweeping._git(target, "add", "-A")
    sweeping._git(target, "commit", "-q", "-m",
                  "the command this module observes through")
    return target


def snapshot_at_first_filing(journal: Path) -> list[str]:
    """What the run's events log held when the sync command was first called.

    The command records the filing and then copies the log under a label of its
    own, so the labelled lines between the first filing and the next are the
    log as the drain saw it.
    """
    lines = sweeping.journal_lines(journal)
    filed = [index for index, line in enumerate(lines)
             if line.startswith("filed ")]
    assert filed, "the sweep never reached the sync command"
    ends = filed[1] if len(filed) > 1 else len(lines)
    return [line[len(SNAPSHOT_LABEL):].split("] ", 1)[-1]
            for line in lines[filed[0] + 1:ends]
            if line.startswith(SNAPSHOT_LABEL)]


def test_a_run_whose_queue_holds_entries_says_so_before_it_drains(
        tmp_path, sweep_harness):
    """The line is on the log before the first entry reaches the transport, and
    it says how much there is to drain.

    The report the sweep writes when it is done is not on that log yet, which
    is what separates "before the drain" from "before the report".
    """
    journal = tmp_path / "queued-journal.txt"
    target = observing_target(tmp_path, "queued", journal)
    queued = [sweeping.seeded_pending(outbox.queue_dir(target),
                                      sweeping.IDENTITY),
              sweeping.seeded_pending(outbox.queue_dir(target),
                                      sweeping.OTHER_IDENTITY)]

    assert sweeping.run(target, sweep_harness,
                        sweeping.Runner(target, journal)) == 0

    during = snapshot_at_first_filing(journal)
    said = announcements_in(during, story_coordinator.OUTBOX_SWEEP_COST)
    assert len(said) == 1, during
    assert numbers_in(said[0], sweeping.STORY_ID) == [len(queued)], said
    assert [line for line in during
            if line.startswith(REPORT_LABEL)] == [], during


def test_a_run_whose_queue_holds_nothing_says_nothing_about_the_sweep(
        tmp_path, sweep_harness):
    """A sweep with nothing to drain is not a wait, so it is not announced.

    Two runs of one fixture differing in the queue alone, and they really did
    differ: one reached the configured sync command and the other had nothing
    to reach it with.
    """
    empty_journal = tmp_path / "empty-journal.txt"
    empty = sweeping.build_target(tmp_path / "empty-queue", empty_journal)
    assert sweeping.run(empty, sweep_harness,
                        sweeping.Runner(empty, empty_journal)) == 0

    seeded_journal = tmp_path / "seeded-journal.txt"
    seeded = sweeping.build_target(tmp_path / "seeded-queue", seeded_journal)
    sweeping.seeded_pending(outbox.queue_dir(seeded))
    assert sweeping.run(seeded, sweep_harness,
                        sweeping.Runner(seeded, seeded_journal)) == 0

    assert SWEEP_KIND not in kinds_of(empty, sweeping.STORY_ID)
    assert SWEEP_KIND in kinds_of(seeded, sweeping.STORY_ID)
    assert sweeping.filings(empty_journal) == []
    assert sweeping.filings(seeded_journal) != []


def test_the_sweep_that_is_given_no_run_directory_announces_nothing(
        tmp_path, sweep_harness):
    """The completion sweep's silence, over a queue that still holds what it
    held at the start.

    That silence is an existing decision with reasons of its own and this story
    does not reopen it: the sweep after the completion commit reports into no
    run directory at all. The control is that both sweeps really ran over a
    non-empty queue — every filing records the subject of the commit HEAD stood
    on when it was made, and the completion commit falls between the two, so
    two distinct subjects is two sweeps — and only one of them announced.
    """
    journal = tmp_path / "both-sweeps-journal.txt"
    target = observing_target(tmp_path, "both-sweeps", journal)
    sweeping.seeded_pending(outbox.queue_dir(target))

    assert sweeping.run(target, sweep_harness,
                        sweeping.Runner(target, journal)) == 0

    assert len(set(sweeping.filings(journal))) == 2, \
        sweeping.journal_lines(journal)
    assert len(outbox.entry_files(outbox.queue_dir(target))) == 1
    assert kinds_of(target, sweeping.STORY_ID).count(SWEEP_KIND) == 1
