"""story-133 validation: a run whose tracker commands moved under it says so.

A run reads its target's configuration once, before anything else happens, and
threads that one dictionary through to the end — including to the post-story
steps that reach for the tracker commands after the completion commit. So a
story whose own work is to move one of those commands spends the rest of its
run calling the name it has just replaced. Loading once is deliberate and this
story keeps it; what it adds is that each of those steps now compares the value
it holds against the value the tree's configuration carries, and names the
disagreement where a reader of that run will meet it.

The subjects are kept apart deliberately:

  * **the two sites inside a run.** A whole run of a story whose writing stage
    repoints the tree's configuration at commands sitting beside the ones the
    run is holding, driven so that the move happens where a story's own work
    happens: in the tree, recorded in the stage's changed-files, committed with
    everything else. The post-story inspection and the completion path each
    have to name both values in that run's own events.log.

  * **the sweep.** Driven at `outbox_sweep.sweep` directly, because the sweep
    that meets a moved command is the one *after* the completion commit, and
    that one deliberately reports into no run directory. What the criterion
    asks of it — a note on the Summary it already returns, reaching the line
    the coordinator already prints — is asked of the seam that returns the
    Summary and of the renderer that prints it.

  * **the helper.** `harness_config.moved_command` against a target this module
    owns: a file that agrees, a file that moved, a key neither carries, and the
    three ways a file can fail to be read at all.

  * **what none of it changes.** The commands the run actually launched are
    read out of a journal each of them writes, so "the run acted on what it
    loaded" is observed at the commands rather than inferred; and the moved run
    and the unmoved one are compared on their exit code, their durable
    inspection record and their queue.

Every absence asserted here carries a demonstration that it can fail:

  * "a run whose configuration did not move writes no drift line" sits beside
    the moved run in the same fixture, where the same search finds one at each
    site;
  * "the command the tree now names was never launched" sits beside the same
    journal read for the command the run held, which the same reader finds;
  * "the sweep whose sync command did not move carries no such note" sits
    beside the moved sweep, whose Summary carries it;
  * "the helper answers nothing-moved for a configuration it cannot read" sits
    beside the same call against a readable file that has moved, which the same
    helper reports.

Nothing here reaches a model: the fake inspector is
`tests/test_an_inspection_records_what_it_cost.py`'s, whose autouse guard fails
any test in this module that reaches the real runner, and the stages are driven
by a fake of this module's own. Nothing here reaches a tracker or a network
either: every command a run or a sweep launches is a shell script this module
wrote inside a target it built.
"""
from __future__ import annotations

import json
from pathlib import Path

import agent_runner
import brief_key
import conftest
import command_transport
import filed_query
import harness_config
import item_update
import outbox
import outbox_sweep
import story_coordinator
import story_inspection
from agent_runner import AgentResult

from test_an_inspection_records_what_it_cost import (  # noqa: F401 - shared
    CHANGED_SOURCE,                                   # idioms and fixtures
    PASSED,
    ROOMY_CAP,
    STORY_ID,
    VERIFYING,
    WRITING,
    Inspector,
    build_target,
    finding,
    harness,
    messages,
    no_model,
    records,
    run_dir_of,
)

#: The target's configuration file, repository-relative. The harness spells it
#: at one place — `harness_config.load_config` — and exports no constant for
#: it, so it is written here once and composed everywhere else from this.
CONFIG_REL = str(Path(".harness") / "config.yaml")

#: Where this module's fixture targets keep the commands they ship, and the two
#: names each tracker command is known by. Both files exist in the tree
#: throughout: what *moves* is which of them the tree's configuration names,
#: which is precisely what a run holding the other name cannot see. Neither
#: name is a substring of the other, so a line naming both is recognised by
#: finding both in it.
TRACKER_DIR = "tracker"
QUERY_HELD = f"{TRACKER_DIR}/ask-what-is-filed.sh"
QUERY_NOW = f"{TRACKER_DIR}/ask-what-is-filed-from-one-script.sh"
ITEM_HELD = f"{TRACKER_DIR}/say-where-the-work-got-to.sh"
ITEM_NOW = f"{TRACKER_DIR}/say-where-the-work-got-to-from-one-script.sh"
SYNC_HELD = f"{TRACKER_DIR}/file-what-is-queued.sh"
SYNC_NOW = f"{TRACKER_DIR}/file-what-is-queued-from-one-script.sh"

#: What a filed-query command says when it answers, and what it says when it
#: cannot. The refusing one's words are this module's own, so a reason quoted
#: back on a line is traceable to the command that produced it.
ANSWERS = "printf '%s\\n' '{\"items\":[]}'\n"
THE_TRACKER_REFUSED = "the tracker refused to answer"
REFUSES = f"echo '{THE_TRACKER_REFUSED}' >&2\nexit 3\n"

#: The item this story was planned from, in a form nothing could resolve: the
#: key is opaque to the harness, and a story carrying one is what makes the
#: completion path reach for the item-update command at all.
ITEM_KEY = "an-item-nothing-resolves"

#: The story every run in this module executes: the fixture story, carrying the
#: brief key above. Composed by inserting the line above the mandate block,
#: because an artifact ends with its mandate and a line appended after that
#: lands inside the block.
STORY_FROM_A_BRIEF = conftest.STORY.replace(
    conftest.MANDATE_OPENING,
    f"\n{brief_key.FIELD}: {ITEM_KEY}\n" + conftest.MANDATE_OPENING,
)

#: What the writing stage adds to the configuration file whether or not it
#: moves anything. It is a comment, which the configuration reader strips
#: before it reads a key, so the unmoved run changes exactly the file the moved
#: run changes and records exactly the paths the moved run records — leaving
#: the value the tree names as the one thing the two runs differ in.
A_COMMENT = "# what this story's own work left behind"

#: What a sweep's queue is offered. The identity is the entry's own; the
#: payload is what a filing command would be handed.
IDENTITY = {"kind": "sample", "subject": STORY_ID}
PAYLOAD = {"title": "something to file", "body": "what it says"}


# ==========================================================================
# The target: commands that record what they were asked for, and a story whose
# own work moves which of them the tree names
# ==========================================================================


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def command(root: Path, relative: str, journal: Path, body: str) -> None:
    """One command the target ships, which records that it was launched.

    Every command writes its own path into `journal` before doing its job, so
    which of two commands a run reached is read off what ran rather than
    inferred from what was configured. The journal is deliberately **outside**
    the target: a file written inside it would be work no stage produced.
    """
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "{relative}" >> "{journal}"\n{body}',
        encoding="utf-8")
    path.chmod(0o755)


def launched(journal: Path) -> list[str]:
    """Every command that was launched, in the order it ran."""
    if not journal.is_file():
        return []
    return [line for line in journal.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def moving_target(tmp_path: Path, name: str, journal: Path, *,
                  answers: bool = True, **config_keys) -> Path:
    """A target whose runs ask a tracker, and whose story came from an item.

    The four commands are written before the target is built, so they are
    committed with everything else and are there when a run reaches them. The
    configuration names the *held* ones; the ones beside them are what a story
    that moved a command would have left the tree naming.
    """
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    command(root, QUERY_HELD, journal, ANSWERS if answers else REFUSES)
    command(root, QUERY_NOW, journal, ANSWERS)
    command(root, ITEM_HELD, journal, "exit 0\n")
    command(root, ITEM_NOW, journal, "exit 0\n")
    target = build_target(root, **{
        story_inspection.MAX_FILES_KEY: ROOMY_CAP,
        filed_query.COMMAND_KEY: QUERY_HELD,
        item_update.COMMAND_KEY: ITEM_HELD,
        **config_keys,
    })
    (target / ".harness" / "stories" / f"{STORY_ID}.yaml").write_text(
        STORY_FROM_A_BRIEF, encoding="utf-8")
    conftest.commit_setup(target)
    return target


#: What a story that moves this repository's tracker commands does to the
#: tree's configuration: each key repointed from the command the run is holding
#: to the one the tree will name from here on.
THE_STORY_S_OWN_WORK = {
    filed_query.COMMAND_KEY: (QUERY_HELD, QUERY_NOW),
    item_update.COMMAND_KEY: (ITEM_HELD, ITEM_NOW),
}


def repoint(root: Path, moves: dict) -> None:
    """Rewrite the tree's configuration the way this story's own work does.

    Each named key is moved from the value the run is holding to the value the
    tree carries from here on. The comment is added whether or not anything
    moved, so a run that moves nothing still changes the same file and records
    the same paths as one that does.
    """
    path = root / CONFIG_REL
    text = path.read_text(encoding="utf-8")
    for key, (held, now) in moves.items():
        line = f"{key}: {held}"
        assert line in text, (line, text)
        text = text.replace(line, f"{key}: {now}", 1)
    path.write_text(f"{text}{A_COMMENT}\n", encoding="utf-8")


class Stages:
    """The fake agent runner a run's stages are driven by.

    The writing stage does what this story's own implementer did: it edits a
    source file, repoints the tree's configuration, and records both in its
    changed-files. Nothing it does depends on which commands moved, so the
    moved run and the unmoved one differ in the configured value alone.
    """

    def __init__(self, target_root: Path, moves: dict):
        self.run_dir = conftest.run_dir_for(Path(target_root), STORY_ID)
        self.moves = dict(moves)
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 **declared):
        self.calls.append(stage)
        if stage == WRITING:
            (Path(cwd) / CHANGED_SOURCE).write_text(
                "def a():\n    return 11\n", encoding="utf-8")
            repoint(Path(cwd), self.moves)
            _write(self.run_dir / conftest.CHANGED_FILES,
                   {"modified": [CHANGED_SOURCE, CONFIG_REL],
                    "created": [], "deleted": []})
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Moved a tracker command and repointed the configuration.\n",
                encoding="utf-8")
        elif stage == VERIFYING:
            _write(self.run_dir / conftest.VERIFICATION_RESULT, PASSED)
        return AgentResult(ok=True, result_text=f"{stage} done")


def a_run(target: Path, harness: Path, monkeypatch, *, moves: dict,
          act=None) -> tuple[int, Inspector, Stages]:
    """One completing run of the fixture, with the fake inspector installed."""
    config = harness_config.load_config(target)
    inspector = Inspector(target, config, findings=[finding()], act=act)
    monkeypatch.setattr(agent_runner, "run_agent", inspector)
    stages = Stages(target, moves)
    code = story_coordinator.run_story(
        STORY_ID, harness, target, stages, sleep=lambda _seconds: None)
    return code, inspector, stages


def naming_both(target: Path, held: str, now: str) -> list[str]:
    """Every line of this run's events.log naming both values of one command."""
    return [line for line in messages(target)
            if held in line and now in line]


def one_record(target: Path) -> dict:
    written = records(target)
    assert len(written) == 1, written
    return written[0]


def queued(target: Path) -> int:
    """How many entries this target's queue holds."""
    return len(outbox.entry_files(outbox.queue_dir(target)))


# ==========================================================================
# The two sites inside a run
# ==========================================================================


def test_the_two_sites_in_a_run_each_name_the_command_that_moved(
        tmp_path, harness, monkeypatch):
    """The lines this story exists to add, in the run's own events.log.

    Both sites are reached by one run because both sit after the completion
    commit of the same run, and a story that moves a tracker command moves it
    once for all of them. Each line is required to name the key, the value the
    run held and the value the tree now carries, so a reader meeting an empty
    dedupe or an unsent status can tell a stale name from a broken command.
    """
    journal = tmp_path / "launched.txt"
    target = moving_target(tmp_path, "whose-commands-moved", journal)

    code, inspector, _stages = a_run(target, harness, monkeypatch,
                                     moves=THE_STORY_S_OWN_WORK)

    assert code == 0
    assert inspector.invocations, "the inspection was never attempted"

    query_lines = naming_both(target, QUERY_HELD, QUERY_NOW)
    assert len(query_lines) == 1, messages(target)
    assert filed_query.COMMAND_KEY in query_lines[0], query_lines[0]
    assert "may already be filed" in query_lines[0], query_lines[0]

    item_lines = naming_both(target, ITEM_HELD, ITEM_NOW)
    assert len(item_lines) == 1, messages(target)
    assert item_update.COMMAND_KEY in item_lines[0], item_lines[0]

    # The line about the item sits beside the report of whether the status was
    # sent, which is on stderr rather than in the log; what the log has to show
    # is that the status was in fact sent by the command the run held.
    assert ITEM_HELD in launched(journal), launched(journal)


def test_the_line_is_written_when_the_dedupe_query_could_not_answer_either(
        tmp_path, harness, monkeypatch):
    """A dedupe that never launched is reported on the same terms as one that
    ran against a stale-but-existing command.

    The two are the same doubt reached by different roads — what was filed may
    already be filed — so the drift line is required beside the failed-dedupe
    note rather than instead of it. The record saying dedupe did not run is
    what makes this run the other case from the one above, whose record says it
    did.
    """
    journal = tmp_path / "launched.txt"
    target = moving_target(tmp_path, "whose-query-also-refused", journal,
                           answers=False)

    code, _inspector, _stages = a_run(target, harness, monkeypatch,
                                      moves=THE_STORY_S_OWN_WORK)

    assert code == 0
    assert one_record(target)["dedupe_ran"] is False

    query_lines = naming_both(target, QUERY_HELD, QUERY_NOW)
    assert len(query_lines) == 1, messages(target)
    assert "may already be filed" in query_lines[0], query_lines[0]

    # The note the failed dedupe already had is still its own line, and is not
    # the line above: this story adds a line rather than rewriting one.
    refused = [line for line in messages(target)
               if THE_TRACKER_REFUSED in line]
    assert len(refused) == 1, messages(target)
    assert refused[0] not in query_lines


def test_a_run_whose_configuration_did_not_move_says_nothing_new(
        tmp_path, harness, monkeypatch):
    """The control for both absences above, and for what the noticing costs.

    The two runs differ in one thing: whether the writing stage repointed the
    keys or only added the comment beside them. So the unmoved run is required
    to write no drift line at either site, and everything a run decides — its
    exit code, what its inspection filed, what its queue holds, and which
    commands it launched — is required to be what the moved run's was.

    Without the moved run beside it, "no drift line" would be satisfied by a
    reader looking in the wrong file; without the unmoved run, "a line at each
    site" would be satisfied by a harness that writes one on every run.
    """
    moved_journal = tmp_path / "moved-launched.txt"
    still_journal = tmp_path / "unmoved-launched.txt"
    moved = moving_target(tmp_path, "moved-under-the-run", moved_journal)
    still = moving_target(tmp_path, "still-what-it-was", still_journal)

    moved_code, _inspector, _stages = a_run(moved, harness, monkeypatch,
                                            moves=THE_STORY_S_OWN_WORK)
    still_code, _inspector, _stages = a_run(still, harness, monkeypatch,
                                            moves={})

    assert naming_both(moved, QUERY_HELD, QUERY_NOW), messages(moved)
    assert naming_both(moved, ITEM_HELD, ITEM_NOW), messages(moved)
    assert naming_both(still, QUERY_HELD, QUERY_NOW) == []
    assert naming_both(still, ITEM_HELD, ITEM_NOW) == []
    assert [line for line in messages(still)
            if QUERY_NOW in line or ITEM_NOW in line] == []

    # Whatever the drift lines said, they said it and nothing else: the two
    # runs agree on every decision, and their logs differ by those lines alone.
    assert moved_code == still_code == 0
    drift = (naming_both(moved, QUERY_HELD, QUERY_NOW)
             + naming_both(moved, ITEM_HELD, ITEM_NOW))
    assert len(messages(moved)) - len(drift) == len(messages(still))

    moved_record, still_record = one_record(moved), one_record(still)
    for field in ("findings", "filed", "dropped", "dedupe_ran", "mode",
                  "invocations"):
        assert moved_record[field] == still_record[field], field
    assert queued(moved) == queued(still)


def test_a_run_launches_the_commands_it_loaded_and_never_the_ones_it_read_back(
        tmp_path, harness, monkeypatch):
    """The dictionary a run acts on is still the one it loaded.

    Read at the commands rather than off the source: the run holds one pair and
    the tree names another, and both pairs exist and both record every launch.
    A run that had started routing on the re-read values would have asked the
    tracker through the commands the tree names, and the journal would say so.

    The commands the run holds having run is the control for the ones it did
    not: without it, "the moved-to commands were never launched" would be
    satisfied by a run that launched nothing at all.
    """
    journal = tmp_path / "launched.txt"
    target = moving_target(tmp_path, "acting-on-what-it-loaded", journal)

    code, _inspector, _stages = a_run(target, harness, monkeypatch,
                                      moves=THE_STORY_S_OWN_WORK)

    assert code == 0
    ran = launched(journal)
    assert QUERY_HELD in ran, ran
    assert ITEM_HELD in ran, ran
    assert QUERY_NOW not in ran, ran
    assert ITEM_NOW not in ran, ran


def test_a_run_whose_drift_check_cannot_read_the_configuration_records_what_it_would_have(
        tmp_path, harness, monkeypatch):
    """The configuration is made unreadable underneath the checks themselves.

    The inspection's own invocation writes bytes that are not text into the
    configuration file, which is the last thing to happen before the first
    drift check and well before the last. So both checks meet a file they
    cannot read, and the run is required to end exactly as the run beside it
    ends — the same exit code, the same record, the same queue — with no drift
    line and nothing raised.

    The file stays unreadable for the whole of the run and is put back exactly
    as it stood when it was blinded, once the run has ended and nothing under
    test will read it again. That is for the reader below rather than for the
    run: a target's run directory is composed from its configuration, so the
    shared reader that finds a run's events.log cannot find one under a target
    whose configuration is not text.
    """
    put_back: list[tuple[Path, str]] = []

    def unreadable(root: Path) -> None:
        path = Path(root) / CONFIG_REL
        put_back.append((path, path.read_text(encoding="utf-8")))
        path.write_bytes(b"\xff\xfe not text at all\n")

    blinded_journal = tmp_path / "blinded-launched.txt"
    ordinary_journal = tmp_path / "ordinary-launched.txt"
    blinded = moving_target(tmp_path, "with-a-blinded-check", blinded_journal)
    ordinary = moving_target(tmp_path, "with-a-check-that-reads",
                             ordinary_journal)

    blinded_code, inspector, _stages = a_run(
        blinded, harness, monkeypatch, moves=THE_STORY_S_OWN_WORK,
        act=unreadable)
    assert put_back, "the configuration was never made unreadable"
    for path, text in put_back:
        path.write_text(text, encoding="utf-8")
    ordinary_code, _inspector, _stages = a_run(ordinary, harness, monkeypatch,
                                               moves={})

    assert blinded_code == ordinary_code == 0
    assert inspector.invocations, "the inspection was never attempted"
    assert naming_both(blinded, QUERY_HELD, QUERY_NOW) == []
    assert naming_both(blinded, ITEM_HELD, ITEM_NOW) == []
    assert len(messages(blinded)) == len(messages(ordinary))

    blinded_record, ordinary_record = one_record(blinded), one_record(ordinary)
    for field in ("findings", "filed", "dropped", "dedupe_ran", "mode",
                  "invocations"):
        assert blinded_record[field] == ordinary_record[field], field
    assert queued(blinded) == queued(ordinary)
    assert ITEM_HELD in launched(blinded_journal), launched(blinded_journal)


# ==========================================================================
# The sweep
# ==========================================================================


def sweeping_target(tmp_path: Path, name: str, journal: Path) -> Path:
    """A target whose queue is filed by a command that defers every entry.

    The transient exit code is the transport's own, read off the module rather
    than written here: it is what leaves a seeded entry pending, which is the
    state the sweep's note is about.
    """
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    command(root, SYNC_HELD, journal,
            f"exit {command_transport.TRANSIENT_EXIT_CODE}\n")
    command(root, SYNC_NOW, journal,
            f"exit {command_transport.TRANSIENT_EXIT_CODE}\n")
    return build_target(root, **{outbox_sweep.COMMAND_KEY: SYNC_HELD})


def a_sweep(target: Path, harness: Path, held: dict,
            run_dir: Path | None = None) -> outbox.Summary:
    """One sweep of `target`'s queue, made under the configuration `held`.

    The run directory is the one a run of this target would write into, so the
    line the sweep reports lands where the coordinator's pre-flight sweep
    reports it. A caller that has already resolved it says so, because that
    directory is composed from the target's configuration and one case below
    sweeps a target whose configuration cannot be read at all.
    """
    run_dir = run_dir_of(target) if run_dir is None else run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    return outbox_sweep.sweep(target, held, harness, run_dir=run_dir)


def held_and_moved(target: Path, moves: dict) -> dict:
    """The configuration a sweep holds, with the tree moved on from it.

    The dictionary is loaded first, so it is the one a run would be holding,
    and only then is the file repointed — which is the order a run meets, with
    the story's own work in between.
    """
    held = harness_config.load_config(target)
    repoint(target, moves)
    return held


THE_SYNC_COMMAND_MOVED = {outbox_sweep.COMMAND_KEY: (SYNC_HELD, SYNC_NOW)}


def notes_naming(summary: outbox.Summary, held: str, now: str) -> list[str]:
    return [note for note in summary.notes if held in note and now in note]


def test_a_sweep_whose_sync_command_moved_carries_a_note_naming_both_values(
        tmp_path, harness):
    """The note on the Summary the sweep already returns, and the line the
    coordinator already prints for it.

    A pending count that is not going down is what a reader meets, and the note
    is what tells them why: the entry was offered to a command this tree no
    longer names. So the note is required to reach `render`, which is the one
    rendering every site that mentions a queue in passing prints, and to reach
    the run directory's events.log through it.
    """
    journal = tmp_path / "filed.txt"
    target = sweeping_target(tmp_path, "whose-sync-moved", journal)
    key = outbox.enqueue(outbox.queue_dir(target), PAYLOAD, IDENTITY)
    held = held_and_moved(target, THE_SYNC_COMMAND_MOVED)

    summary = a_sweep(target, harness, held)

    named = notes_naming(summary, SYNC_HELD, SYNC_NOW)
    assert len(named) == 1, summary.notes
    assert outbox_sweep.COMMAND_KEY in named[0], named[0]
    assert named[0] in outbox_sweep.render(summary)

    printed = [line for line in messages(target)
               if SYNC_HELD in line and SYNC_NOW in line]
    assert len(printed) == 1, messages(target)

    # The luck this story preserves: an entry offered to a command the tree has
    # moved on from is deferred rather than failed, so it is still there for a
    # later run to file under the new name.
    entry = json.loads(outbox.entry_path(outbox.queue_dir(target), key)
                       .read_text(encoding="utf-8"))
    assert entry["state"] == outbox.PENDING


def test_the_same_sweep_whose_sync_command_did_not_move_carries_no_such_note(
        tmp_path, harness):
    """The control for the absence, and for what the note costs the sweep.

    The two sweeps differ in one thing — whether the tree's configuration was
    repointed after the dictionary was loaded — so the summary is required to
    differ in its notes and in nothing else, which is asserted through the
    module's own `replace_notes` rather than field by field. The command the
    sweep held having filed is the control for the one it did not launch.
    """
    moved_journal = tmp_path / "moved-filed.txt"
    still_journal = tmp_path / "unmoved-filed.txt"
    moved = sweeping_target(tmp_path, "sweep-that-moved", moved_journal)
    still = sweeping_target(tmp_path, "sweep-that-did-not", still_journal)
    outbox.enqueue(outbox.queue_dir(moved), PAYLOAD, IDENTITY)
    outbox.enqueue(outbox.queue_dir(still), PAYLOAD, IDENTITY)

    moved_summary = a_sweep(moved, harness,
                            held_and_moved(moved, THE_SYNC_COMMAND_MOVED))
    still_summary = a_sweep(still, harness, held_and_moved(still, {}))

    assert notes_naming(moved_summary, SYNC_HELD, SYNC_NOW)
    assert notes_naming(still_summary, SYNC_HELD, SYNC_NOW) == []
    assert [note for note in still_summary.notes if SYNC_NOW in note] == []
    assert [line for line in messages(still) if SYNC_NOW in line] == []

    assert (outbox_sweep.replace_notes(moved_summary, ())
            == outbox_sweep.replace_notes(still_summary, ()))
    for filed in (launched(moved_journal), launched(still_journal)):
        assert SYNC_HELD in filed, filed
        assert SYNC_NOW not in filed, filed


def test_a_sweep_whose_configuration_cannot_be_read_sweeps_exactly_as_it_did(
        tmp_path, harness):
    """A drift check with nothing to compare against leaves the sweep alone.

    The file is made unreadable rather than merely moved, so the check meets
    the case it may not fail on. The sweep is required to come back as the
    unmoved sweep beside it came back — a Summary, the same counts, no note
    about a moved command — which is the whole of "it behaves as it did".
    """
    blinded_journal = tmp_path / "blinded-filed.txt"
    still_journal = tmp_path / "readable-filed.txt"
    blinded = sweeping_target(tmp_path, "sweep-that-cannot-read",
                              blinded_journal)
    still = sweeping_target(tmp_path, "sweep-that-can", still_journal)
    outbox.enqueue(outbox.queue_dir(blinded), PAYLOAD, IDENTITY)
    outbox.enqueue(outbox.queue_dir(still), PAYLOAD, IDENTITY)

    held = harness_config.load_config(blinded)
    # Resolved while the file is still text, because where a run of this target
    # would report is composed from its configuration — the sweep under test is
    # the one that meets the unreadable file, not the reader that placed it.
    where_it_reports = run_dir_of(blinded)
    (blinded / CONFIG_REL).write_bytes(b"\xff\xfe not text at all\n")

    blinded_summary = a_sweep(blinded, harness, held, where_it_reports)
    still_summary = a_sweep(still, harness, held_and_moved(still, {}))

    assert isinstance(blinded_summary, outbox.Summary)
    assert blinded_summary == still_summary
    assert SYNC_HELD in launched(blinded_journal), launched(blinded_journal)


# ==========================================================================
# The helper
# ==========================================================================


A_KEY = filed_query.COMMAND_KEY


def a_configured_target(tmp_path: Path, name: str, **config_keys) -> Path:
    """A directory carrying a configuration file and nothing else.

    Enough for the helper, which reads one file and compares one value: what a
    target is in every other respect is not what this half is about.
    """
    root = tmp_path / name
    (root / ".harness").mkdir(parents=True, exist_ok=True)
    (root / CONFIG_REL).write_text(
        "".join(f"{key}: {value}\n" for key, value in config_keys.items()),
        encoding="utf-8")
    return root


def test_the_helper_names_the_key_and_both_values_when_the_tree_moved_on(
        tmp_path):
    """What every caller renders, said once by the helper.

    The description is required to carry the key and both values, because a
    caller that had to spell them itself is a third place they could be spelled
    differently.
    """
    target = a_configured_target(tmp_path, "moved-on", **{A_KEY: QUERY_NOW})

    moved = harness_config.moved_command({A_KEY: QUERY_HELD}, target, A_KEY)

    assert moved is not None
    assert moved.key == A_KEY
    said = moved.describe()
    assert A_KEY in said
    assert QUERY_HELD in said
    assert QUERY_NOW in said


def test_the_helper_answers_nothing_moved_where_the_two_values_agree(tmp_path):
    """Agreement, and a key neither side carries, are both nothing moved.

    A key absent from both is the ordinary case for a target that configures no
    such command, and reporting a move between two absences would put a line in
    the events.log of every run of every target that files nothing. The moved
    answer above is what shows this one is not vacuous.
    """
    target = a_configured_target(tmp_path, "unmoved", **{A_KEY: QUERY_HELD})

    assert harness_config.moved_command({A_KEY: QUERY_HELD}, target,
                                        A_KEY) is None
    assert harness_config.moved_command({}, a_configured_target(
        tmp_path, "configures-nothing"), A_KEY) is None


def test_the_helper_answers_nothing_moved_for_a_file_it_cannot_read(tmp_path):
    """The ways a file can fail to be read, and the control beside them.

    This is a report about a run whose work is already committed, so it may not
    become the thing that fails: a configuration that is absent, that is not
    text, or that is not a file at all answers nothing moved rather than
    raising or reporting a move the file does not support. The control is the same call against a readable file that
    *has* moved, which the same helper reports — without it, three Nones would
    be satisfied by a helper that had stopped comparing anything.
    """
    held = {A_KEY: QUERY_HELD}

    absent = tmp_path / "no-target-here"
    assert harness_config.moved_command(held, absent, A_KEY) is None

    unreadable = a_configured_target(tmp_path, "unreadable",
                                     **{A_KEY: QUERY_NOW})
    (unreadable / CONFIG_REL).write_bytes(b"\xff\xfe not text at all\n")
    assert harness_config.moved_command(held, unreadable, A_KEY) is None

    directory = a_configured_target(tmp_path, "a-directory-in-its-place")
    (directory / CONFIG_REL).unlink()
    (directory / CONFIG_REL).mkdir()
    assert harness_config.moved_command(held, directory, A_KEY) is None

    readable = a_configured_target(tmp_path, "readable", **{A_KEY: QUERY_NOW})
    assert harness_config.moved_command(held, readable, A_KEY) is not None
