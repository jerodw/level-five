"""Independent validation for the filed query: asking one command what is
already filed against a path set.

Written from the story's acceptance criteria rather than from the
implementation. The subjects are kept apart deliberately:

  * **the question.** Observed at the command rather than inferred from the
    call: a fake writes what it read on stdin to a file, and the test parses
    that file and requires it to be one JSON document carrying exactly the
    paths that were asked about.

  * **the answer.** What the command reported and nothing else — a minimal
    item gets no summary and no paths, and a command that reported no items
    has none invented for it.

  * **nothing known against nothing filed.** The two are driven side by side
    and required to agree in the item list and differ in the flag, because a
    caller that can only read the items would report silence as agreement.

  * **every way of knowing nothing.** Absent, unlaunchable, an argument list
    with nothing in it, a command string that cannot be split, a non-zero
    exit, a timeout, prose beside the document, valid JSON of the wrong
    shape, more stdout than the harness reads, and a configuration that was
    refused. Each is driven on its own against a fake this module wrote,
    because a repair can get one right while getting another wrong, and the
    reasons are then required to differ from one another: "nothing known" is
    not the whole answer, "which way it was" is.

  * **the bounds, none of them silent.** The item cap, the per-field length
    bound and the stdout quantity bound are each driven past, and each is
    required to name what it left out in the answer it returns.

  * **the kill.** A command that never exits is observed being killed — by
    the wall-clock time the call took, not by the arguments it was handed —
    and the child it backgrounded is observed gone.

  * **the shape.** `schemas/filed-items.schema.json` is a live harness
    artifact and is the subject of the assertions that name it: a conforming
    document is accepted and each way of malforming it is refused. The
    generic sweeps in `tests/test_artifact_schemas.py` already cover it as
    one of the shipped schemas — that it is registered, that every required
    name is a declared property, that it constrains nothing the validator
    cannot check — so what is added here is the coverage particular to this
    shape, beside the module whose answer it describes.

  * **the seam and its absence of callers.** Scans over `orchestration/` and
    `scripts/`, each shown reporting a violation planted in a throwaway root.

  * **the reference pair.** `templates/sync/github.sh`,
    `templates/query/github.sh`, `scripts/l5-init` and this repository's own
    `.harness/` are live harness artifacts and are the subjects of the
    assertions that name them: what this repository ships is read as it
    ships. The two scripts are then run against a stub tracker this module
    wrote, so "the pair agrees" is a fact about what they do rather than
    about what their headers say.

Every absence asserted here carries a demonstration that it can fail:

  * "no item is synthesized" sits beside an answering command whose items are
    read back field by field, so an empty tuple is a fact about what the
    command said rather than about a reader that finds nothing;
  * "the excess was not truncated silently" sits beside the same answer's
    `excluded`, which must name the bound and the count;
  * "the backgrounded child is gone" sits beside the same child spawned by a
    command that exits normally, where the marker it writes does appear;
  * "`filed_query.py` is the only source that reads the command key" and "no
    source imports it" each sit beside a throwaway root with a second reader
    and an importer planted in it, which the same scans report;
  * "the two scripts use the same marker" sits beside a rendering of one of
    them with the marker changed, which the same extraction reports;
  * "a payload carrying no paths writes no path marker" sits beside the same
    filing with paths, whose body carries one marker per path.

Every command driven as a `filed_query_command` here is a file this module
wrote, and `fixture_command_problems` is what makes that a checked property
rather than a habit. Nothing here reaches a network: the two reference
scripts are run against a stub `gh` this module wrote, first on `PATH`.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

import command_transport
import conftest
import filed_query
import harness_config
import harness_source
import schema_validator

REPO_ROOT = Path(filed_query.__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
TEMPLATES = REPO_ROOT / "templates"

#: Where a target's query commands are installed, and the reference command
#: itself, derived from what the harness ships rather than written here: the
#: template directory is the declaration of what l5-init installs.
QUERY_DIR = "query"
SYNC_DIR = "sync"
REFERENCE_QUERY_SCRIPTS = sorted(
    path.name for path in (TEMPLATES / QUERY_DIR).glob("*.sh"))

#: What the module says about itself, read off it so this file names no key,
#: bound or schema of its own.
COMMAND_KEY = filed_query.COMMAND_KEY
TIMEOUT_KEY = filed_query.TIMEOUT_KEY
MAX_ITEMS_KEY = filed_query.MAX_ITEMS_KEY
ITEMS_SCHEMA = filed_query.ITEMS_SCHEMA
DEFAULT_TIMEOUT_SECONDS = filed_query.DEFAULT_TIMEOUT_SECONDS
DEFAULT_MAX_ITEMS = filed_query.DEFAULT_MAX_ITEMS
MAX_STDOUT_BYTES = filed_query.MAX_STDOUT_BYTES
MAX_TEXT_LENGTH = filed_query.MAX_TEXT_LENGTH

#: The paths a question asks about. Two of them, so an answer that reported
#: one path can be told from an answer that echoed the question.
ASKED = ("src/app.py", "src/parser.py")

#: One item as a tracker might report it, and the same item stripped to what
#: the shape requires. Both are what a *command* said; nothing here is a
#: field the harness would supply.
FULL_ITEM = {
    "key": "https://tracker.example/issues/17",
    "title": "the parser drops the last token",
    "summary": "reported twice already",
    "paths": ["src/parser.py"],
}
MINIMAL_ITEM = {"key": "PROJ-4219", "title": "an item with nothing else on it"}

#: How long a command that is meant to be killed is asked to run. Far longer
#: than any bound configured below, so a test that observed it finish would be
#: observing the kill not happening rather than a race.
LONGER_THAN_ANY_BOUND = 45

#: The bound a killed command is given, and the wall-clock ceiling the killed
#: call must come back inside. The ceiling is far below the sleep and far
#: above the bound, so it separates a kill from a wait without being a
#: stopwatch on a loaded machine.
KILL_BOUND_SECONDS = 1.0
KILL_CEILING_SECONDS = 20.0

#: How long the child a fixture backgrounds sleeps before writing its marker,
#: and how long the tests wait for that marker to appear or fail to. Longer
#: than the moment its leader is killed, so a marker that appears can only
#: have been written by a child that outlived the leader.
CHILD_SLEEP_SECONDS = 3
PATIENCE_SECONDS = 20.0


# --------------------------------------------------------------------------
# Every command this module drives as a query command is a file it wrote
# --------------------------------------------------------------------------


def fixture_command_problems(path: Path) -> list[str]:
    """What would stop `path` from being a command this module wrote itself.

    A predicate rather than a pair of inline assertions, so it can be *shown*
    reporting a violation rather than only observed to be silent. A path that
    already exists is one somebody else wrote, and a path inside this
    repository is a shipped artifact — driving either as a query command would
    make this module's behaviour depend on a program it does not control.
    """
    problems = []
    if path.exists():
        problems.append(f"{path} already exists, so this module did not write it")
    if REPO_ROOT in path.parents:
        problems.append(f"{path} is inside {REPO_ROOT}, so it is a shipped file "
                        f"rather than one this module wrote")
    return problems


def fixture_file(directory: Path, name: str, text: str, *,
                 executable: bool = True) -> Path:
    """One file this module writes for itself, and drives as a command."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    problems = fixture_command_problems(path)
    assert problems == [], problems
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)
    return path


def fixture_command(directory: Path, name: str, body: str) -> Path:
    """A shell script this module wrote, ready to be a `filed_query_command`."""
    return fixture_file(directory, name, "#!/bin/sh\n" + body)


def test_the_fixture_check_reports_a_command_this_module_did_not_write():
    """The control for the property every fixture command above rests on.

    Silence from the check means nothing until it has been shown to speak, so
    it is pointed at a shipped entry point — which exists, and lives inside
    this repository — and must report both.
    """
    problems = fixture_command_problems(SCRIPTS / "l5-init")
    assert len(problems) == 2, problems
    assert any("already exists" in problem for problem in problems)
    assert any(str(REPO_ROOT) in problem for problem in problems)


# --------------------------------------------------------------------------
# The fixture commands, each a file this module writes
# --------------------------------------------------------------------------


def document(*items: dict) -> str:
    return json.dumps({"items": list(items)})


def answering(directory: Path, *items: dict, name: str = "answers.sh") -> Path:
    """A command that prints one JSON document and exits zero.

    The document is written to a file beside the script and printed with
    `cat`, so nothing about the answer depends on a shell's quoting.
    """
    body = fixture_file(directory, f"{name}.document", document(*items),
                        executable=False)
    return fixture_command(directory, name, f'cat "{body}"\n')


def recording(directory: Path, transcript: Path) -> Path:
    """A command that writes what it read on stdin, then answers emptily."""
    return fixture_command(
        directory, "records-the-question.sh",
        f'cat > "{transcript}"\n'
        f"printf '%s' '{document()}'\n")


def exits(directory: Path, code: int, message: str) -> Path:
    return fixture_command(directory, "exits-non-zero.sh",
                           f'echo "{message}" >&2\nexit {code}\n')


def prints_prose_beside_the_document(directory: Path) -> Path:
    """Answers correctly and says so on stdout, which is one thing too many."""
    return fixture_command(
        directory, "noisy.sh",
        "echo 'searching the tracker'\n"
        f"printf '%s' '{document(MINIMAL_ITEM)}'\n")


def prints_json_of_the_wrong_shape(directory: Path) -> Path:
    """Valid JSON, and not this shape: `items` is required and absent."""
    return fixture_command(
        directory, "wrong-shape.sh",
        "printf '%s' '" + json.dumps({"issues": [MINIMAL_ITEM]}) + "'\n")


def prints_more_than_the_bound(directory: Path) -> Path:
    """A document that would be valid if the harness read the whole of it."""
    oversized = document({**MINIMAL_ITEM,
                          "summary": "x" * (MAX_STDOUT_BYTES + 1)})
    assert len(oversized) > MAX_STDOUT_BYTES
    body = fixture_file(directory, "oversized.document", oversized,
                        executable=False)
    return fixture_command(directory, "oversized.sh", f'cat "{body}"\n')


def sleeps_forever(directory: Path) -> Path:
    return fixture_command(directory, "sleeps.sh",
                           f"sleep {LONGER_THAN_ANY_BOUND}\n")


def spawns_a_child(directory: Path, marker: Path, *, name: str,
                   then: str) -> Path:
    """A command that backgrounds a child which would outlive it.

    The marker the child writes after sleeping is the question asked twice:
    a child that survived its leader writes it, and a child killed with the
    group never does. `then` is what the leader does afterwards — sleeping
    past every bound, or answering and exiting.
    """
    return fixture_command(
        directory, name,
        f'sh -c \'sleep {CHILD_SLEEP_SECONDS}; echo survived > "{marker}"\' &\n'
        + then)


def unlaunchable(directory: Path) -> str:
    """A path inside a directory this module owns, at which nothing exists."""
    return str(directory / "nothing-was-ever-written-here.sh")


# --------------------------------------------------------------------------
# Driving the query
# --------------------------------------------------------------------------


def asked(command, tmp_path: Path, *, paths=ASKED, **overrides) -> filed_query.Answer:
    """One question put to `command`, from a target root this test owns."""
    config = {COMMAND_KEY: str(command), **overrides}
    return filed_query.query(paths, config, target_root=tmp_path)


def knows_nothing(answer: filed_query.Answer) -> bool:
    return answer.items == () and answer.answered is False and bool(answer.reason)


# --------------------------------------------------------------------------
# The question the command is asked
# --------------------------------------------------------------------------


def test_the_command_is_handed_one_json_document_carrying_the_asked_paths(
        tmp_path):
    """Observed at the command, not inferred from the call.

    The fake writes what it actually read, and the assertion is on that file:
    it parses whole as one JSON document — `raw_decode` must consume all of
    it, so a second document or a trailing line would be reported — and it
    carries exactly the paths the question was about.
    """
    transcript = tmp_path / "the-question"
    answer = asked(recording(tmp_path / "fake", transcript), tmp_path)
    assert answer.answered is True

    read = transcript.read_text(encoding="utf-8")
    question, consumed = json.JSONDecoder().raw_decode(read)
    assert read[consumed:].strip() == "", read
    assert question == {"paths": list(ASKED)}


# --------------------------------------------------------------------------
# The answer carries what the command reported and nothing else
# --------------------------------------------------------------------------


def test_the_items_carry_the_fields_the_command_reported(tmp_path):
    answer = asked(answering(tmp_path / "fake", FULL_ITEM), tmp_path)
    assert answer.answered is True
    assert answer.reason == ""
    assert answer.excluded == ()
    assert len(answer.items) == 1
    item = answer.items[0]
    assert item.key == FULL_ITEM["key"]
    assert item.title == FULL_ITEM["title"]
    assert item.summary == FULL_ITEM["summary"]
    assert item.paths == tuple(FULL_ITEM["paths"])


def test_an_item_reported_without_a_summary_or_paths_is_given_neither(tmp_path):
    """The harness invents nothing for a field the command did not report.

    The control is the assertion above: the same reader over an item that
    *does* carry both reads both back, so the emptiness here is a fact about
    what this command said rather than about a reader that finds nothing.
    """
    answer = asked(answering(tmp_path / "fake", MINIMAL_ITEM), tmp_path)
    item = answer.items[0]
    assert item.key == MINIMAL_ITEM["key"]
    assert item.title == MINIMAL_ITEM["title"]
    assert item.summary == ""
    assert item.paths == ()


def test_a_command_that_reported_no_items_has_none_synthesized(tmp_path):
    answer = asked(answering(tmp_path / "fake"), tmp_path)
    assert answer.items == ()
    assert answer.answered is True


def test_the_commands_own_order_is_the_order_the_items_come_back_in(tmp_path):
    ordered = [{"key": f"K-{n}", "title": f"the {n}th thing filed"}
               for n in range(4)]
    answer = asked(answering(tmp_path / "fake", *ordered), tmp_path)
    assert [item.key for item in answer.items] == [one["key"] for one in ordered]


# --------------------------------------------------------------------------
# Nothing known is not nothing filed
# --------------------------------------------------------------------------


def test_nothing_known_and_nothing_filed_agree_in_the_items_and_differ_in_the_flag(
        tmp_path):
    """The distinction the whole seam exists to preserve.

    A caller reading only the item list cannot tell these two apart — that is
    asserted here rather than argued — so the flag is what separates them, and
    a caller that cannot get one must say dedupe did not run.
    """
    nothing_filed = asked(answering(tmp_path / "empty"), tmp_path)
    nothing_known = asked(
        exits(tmp_path / "failing", 1, "the tracker refused the search"),
        tmp_path)

    assert nothing_filed.items == nothing_known.items == ()
    assert nothing_filed.answered is True
    assert nothing_known.answered is False
    assert nothing_filed.reason == ""
    assert nothing_known.reason


# --------------------------------------------------------------------------
# Every way of knowing nothing, each driven against a real fake
# --------------------------------------------------------------------------


def ways_of_knowing_nothing(tmp_path: Path) -> dict[str, filed_query.Answer]:
    """Every failure mode the story names, each put to the query for real.

    Built as a mapping rather than as a parametrization so the answers can
    also be compared with one another below: what separates these modes is
    the reason each gives, and a repair that collapsed two of them into one
    string would pass every assertion made about them individually.
    """
    return {
        "absent": filed_query.query(ASKED, {}, target_root=tmp_path),
        "unlaunchable": asked(unlaunchable(tmp_path), tmp_path),
        "empty argument list": asked("   ", tmp_path),
        "unsplittable": asked("'never-closed", tmp_path),
        "non-zero exit": asked(
            exits(tmp_path / "failing", 1, "the tracker refused the search"),
            tmp_path),
        "prose beside the document": asked(
            prints_prose_beside_the_document(tmp_path / "noisy"), tmp_path),
        "wrong shape": asked(
            prints_json_of_the_wrong_shape(tmp_path / "shape"), tmp_path),
        "more stdout than is read": asked(
            prints_more_than_the_bound(tmp_path / "oversized"), tmp_path),
        "a refused configuration": asked(
            answering(tmp_path / "fake"), tmp_path, **{MAX_ITEMS_KEY: "none"}),
    }


def test_every_way_of_knowing_nothing_returns_an_answer_and_raises_on_none(
        tmp_path):
    """Total: the query answers on every path.

    A failed query costs dedupe and costs nothing else, so each of these comes
    back as an answer carrying no items, `answered` false, and a reason —
    rather than as an exception a caller would have to defend against.
    """
    for way, answer in ways_of_knowing_nothing(tmp_path).items():
        assert isinstance(answer, filed_query.Answer), way
        assert knows_nothing(answer), (way, answer)


def test_each_way_of_knowing_nothing_says_which_way_it_was(tmp_path):
    """"Nothing known" is not the whole answer; which one it was is.

    The reasons are required to be distinct from one another, so a repair that
    funnelled two modes into one string is reported here even though every
    assertion about them individually would still hold.
    """
    answers = ways_of_knowing_nothing(tmp_path)
    reasons = [answer.reason for answer in answers.values()]
    assert len(set(reasons)) == len(reasons), reasons


#: What each way's reason must name, so that a caller reading it can tell
#: which way it was. One table over one set of answers rather than a
#: parametrization, because rebuilding every fake per case would spawn each
#: command nine times to assert nine strings.
NAMES_ITS_WAY = {
    "absent": COMMAND_KEY,
    "unlaunchable": "could not be launched",
    "empty argument list": "could not be launched",
    "unsplittable": "could not be launched",
    "non-zero exit": "exited 1",
    "prose beside the document": "not a single JSON document",
    "wrong shape": "schema",
    "more stdout than is read": str(MAX_STDOUT_BYTES),
    "a refused configuration": MAX_ITEMS_KEY,
}


def test_every_reason_names_the_way_it_was(tmp_path):
    answers = ways_of_knowing_nothing(tmp_path)
    assert set(answers) == set(NAMES_ITS_WAY)
    for way, expected in NAMES_ITS_WAY.items():
        assert expected in answers[way].reason, (way, answers[way].reason)


def test_a_command_that_failed_carries_its_own_words_back(tmp_path):
    """Its stderr is where it said why, so a tail of it is the reason's tail."""
    said = "the tracker refused the search"
    answer = asked(exits(tmp_path / "failing", 1, said), tmp_path)
    assert said in answer.reason


def test_a_command_printing_prose_beside_its_document_is_not_parsed_anyway(
        tmp_path):
    """Not best-effort parsed, so a target cannot half-work.

    The document this command prints is a valid one and carries an item; a
    query that dug it out of the surrounding line would report that item. That
    it reports none instead is what makes stdout-is-one-document a rule rather
    than a preference.
    """
    answer = asked(prints_prose_beside_the_document(tmp_path / "noisy"),
                   tmp_path)
    assert knows_nothing(answer)
    assert MINIMAL_ITEM["key"] not in answer.reason


def test_the_reference_script_says_where_diagnostics_belong():
    """A live harness artifact: what this repository ships is read as it ships."""
    header = (TEMPLATES / QUERY_DIR / "github.sh").read_text(encoding="utf-8")
    assert "stderr" in header
    assert "NOTHING ELSE" in header


# --------------------------------------------------------------------------
# No bound is silent
# --------------------------------------------------------------------------


def test_more_items_than_the_cap_loses_the_excess_and_says_so(tmp_path):
    """The cap keeps the command's own order and names what it dropped."""
    cap = 2
    reported = [{"key": f"K-{n}", "title": f"the {n}th thing filed"}
                for n in range(cap + 3)]
    answer = asked(answering(tmp_path / "fake", *reported), tmp_path,
                   **{MAX_ITEMS_KEY: str(cap)})

    assert answer.answered is True
    assert [item.key for item in answer.items] == \
        [one["key"] for one in reported[:cap]]
    stated = " ".join(answer.excluded)
    assert MAX_ITEMS_KEY in stated, stated
    assert str(cap) in stated, stated
    assert str(len(reported) - cap) in stated, stated


def test_a_text_field_past_the_per_field_bound_is_shortened_and_says_so(
        tmp_path):
    long_title = "t" * (MAX_TEXT_LENGTH + 1)
    answer = asked(
        answering(tmp_path / "fake", {**MINIMAL_ITEM, "title": long_title}),
        tmp_path)

    assert answer.answered is True
    assert len(answer.items[0].title) == MAX_TEXT_LENGTH
    stated = " ".join(answer.excluded)
    assert str(MAX_TEXT_LENGTH) in stated, stated


def test_a_field_inside_the_per_field_bound_is_left_whole(tmp_path):
    """The control for the bound above: it shortens what exceeds it and
    nothing else, so an answer's `excluded` is empty when nothing was left
    out."""
    at_the_bound = "t" * MAX_TEXT_LENGTH
    answer = asked(
        answering(tmp_path / "fake", {**MINIMAL_ITEM, "title": at_the_bound}),
        tmp_path)
    assert answer.items[0].title == at_the_bound
    assert answer.excluded == ()


def test_a_document_past_the_stdout_bound_is_not_read_whole_or_parsed(tmp_path):
    """A document truncated mid-token is not a document.

    The fake's document is a *valid* one — the only thing wrong with it is its
    size — so an answer carrying its item would mean the bound had been
    applied after the read rather than before it.
    """
    answer = asked(prints_more_than_the_bound(tmp_path / "oversized"), tmp_path)
    assert knows_nothing(answer)
    assert str(MAX_STDOUT_BYTES) in answer.reason
    assert MINIMAL_ITEM["key"] not in answer.reason


# --------------------------------------------------------------------------
# The settings a query runs under
# --------------------------------------------------------------------------


def test_an_absent_command_is_not_a_problem():
    """A target that queries nothing is the ordinary case, not a misconfigured
    one: it gets no settings and no complaint."""
    settings, problem = filed_query.resolve_settings({})
    assert settings is None
    assert problem == ""


def test_the_two_bounded_keys_default_in_source(tmp_path):
    """A positive number and a positive integer, read off the module.

    Asserted at the resolution rather than at the constants alone, so what a
    query with neither key configured actually runs under is what is pinned.
    """
    assert isinstance(DEFAULT_TIMEOUT_SECONDS, (int, float))
    assert DEFAULT_TIMEOUT_SECONDS > 0
    assert isinstance(DEFAULT_MAX_ITEMS, int)
    assert DEFAULT_MAX_ITEMS > 0

    settings, problem = filed_query.resolve_settings(
        {COMMAND_KEY: "a-command-nothing-here-runs"})
    assert problem == ""
    assert settings.timeout == DEFAULT_TIMEOUT_SECONDS
    assert settings.max_items == DEFAULT_MAX_ITEMS


@pytest.mark.parametrize("key,value", [
    pytest.param(TIMEOUT_KEY, "0", id="timeout-zero"),
    pytest.param(TIMEOUT_KEY, "-1", id="timeout-negative"),
    pytest.param(TIMEOUT_KEY, "soon", id="timeout-not-a-number"),
    pytest.param(MAX_ITEMS_KEY, "0", id="items-zero"),
    pytest.param(MAX_ITEMS_KEY, "-2", id="items-negative"),
    pytest.param(MAX_ITEMS_KEY, "several", id="items-not-an-integer"),
])
def test_a_bound_that_is_not_one_is_a_problem_naming_the_key_and_the_value(
        key, value):
    settings, problem = filed_query.resolve_settings(
        {COMMAND_KEY: "a-command-nothing-here-runs", key: value})
    assert settings is None
    assert key in problem
    assert value in problem


def test_a_bound_that_cannot_be_read_is_reported_even_with_no_command():
    """Checked above the command, so a target that declares an unusable bound
    and no command is still told about the bound rather than told nothing."""
    settings, problem = filed_query.resolve_settings({TIMEOUT_KEY: "soon"})
    assert settings is None
    assert TIMEOUT_KEY in problem


def test_both_bounds_being_wrong_reports_both():
    _, problem = filed_query.resolve_settings(
        {TIMEOUT_KEY: "soon", MAX_ITEMS_KEY: "several"})
    assert TIMEOUT_KEY in problem
    assert MAX_ITEMS_KEY in problem


def test_the_query_consumes_a_refused_configuration_into_its_answer(tmp_path):
    """A misconfigured target loses dedupe and loses nothing else.

    The control is the same command under a configuration that resolves, which
    answers: so the silence here is the bound being refused rather than the
    command being unable to answer.
    """
    command = answering(tmp_path / "fake", MINIMAL_ITEM)
    refused = asked(command, tmp_path, **{TIMEOUT_KEY: "soon"})
    assert knows_nothing(refused)
    assert TIMEOUT_KEY in refused.reason

    resolved = asked(command, tmp_path)
    assert resolved.answered is True
    assert len(resolved.items) == 1


# --------------------------------------------------------------------------
# Every path through the query is bounded in time
# --------------------------------------------------------------------------


def test_a_command_that_never_exits_is_killed_at_the_configured_bound(tmp_path):
    """The kill is observed, rather than the argument being asserted.

    What is measured is how long the call took: the command was asked to sleep
    for far longer than the ceiling below, so a call that returned inside it
    can only have returned because the command was killed.
    """
    started = time.monotonic()
    answer = asked(sleeps_forever(tmp_path / "fake"), tmp_path,
                   **{TIMEOUT_KEY: str(KILL_BOUND_SECONDS)})
    elapsed = time.monotonic() - started

    assert knows_nothing(answer)
    assert str(KILL_BOUND_SECONDS) in answer.reason
    assert elapsed < KILL_CEILING_SECONDS, elapsed
    assert elapsed < LONGER_THAN_ANY_BOUND


def waited_for(marker: Path, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if marker.exists():
            return True
        time.sleep(0.05)
    return marker.exists()


def test_the_kill_reaches_the_children_the_command_spawned(tmp_path):
    """The command is killed as a process group, so its children go with it.

    The absence is controlled below rather than beside itself: the same child,
    spawned by a command that is *not* killed, does write its marker — so a
    marker that never appears is a fact about the kill rather than about a
    child that was never going to write one.
    """
    marker = tmp_path / "the-child-survived"
    command = spawns_a_child(
        tmp_path / "killed", marker, name="killed-leader.sh",
        then=f"sleep {LONGER_THAN_ANY_BOUND}\n")

    answer = asked(command, tmp_path, **{TIMEOUT_KEY: str(KILL_BOUND_SECONDS)})
    assert knows_nothing(answer)

    assert not waited_for(marker, CHILD_SLEEP_SECONDS * 2), \
        "a child of the killed command outlived it"


def test_the_same_child_writes_its_marker_when_its_leader_is_not_killed(
        tmp_path):
    """The control for the absence above."""
    marker = tmp_path / "the-child-survived"
    document_path = fixture_file(tmp_path / "surviving", "answer.document",
                                 document(), executable=False)
    command = spawns_a_child(
        tmp_path / "surviving", marker, name="surviving-leader.sh",
        then=f'cat "{document_path}"\n')

    answer = asked(command, tmp_path)
    assert answer.answered is True
    assert waited_for(marker, PATIENCE_SECONDS), \
        "the child never writes its marker, so its absence above proves nothing"


# --------------------------------------------------------------------------
# The shape a command's stdout must satisfy
# --------------------------------------------------------------------------


def items_schema() -> dict:
    return schema_validator.load_schema(ITEMS_SCHEMA)


def test_a_conforming_document_is_accepted():
    assert schema_validator.validate(
        {"items": [FULL_ITEM, MINIMAL_ITEM]}, items_schema()) == []
    assert schema_validator.validate({"items": []}, items_schema()) == []


@pytest.mark.parametrize("instance,path", [
    pytest.param({}, "$.items", id="no-items"),
    pytest.param({"items": "one"}, "$.items", id="items-not-an-array"),
    pytest.param({"items": [{"title": "t"}]}, "$.items[0].key", id="no-key"),
    pytest.param({"items": [{"key": "k"}]}, "$.items[0].title", id="no-title"),
    pytest.param({"items": [{"key": 17, "title": "t"}]}, "$.items[0].key",
                 id="key-not-a-string"),
    pytest.param({"items": [{"key": "k", "title": "t", "paths": "src/app.py"}]},
                 "$.items[0].paths", id="paths-not-an-array"),
    pytest.param({"items": [{"key": "k", "title": "t", "paths": [7]}]},
                 "$.items[0].paths[0]", id="path-not-a-string"),
])
def test_each_way_of_malforming_the_document_is_refused(instance, path):
    problems = schema_validator.validate(instance, items_schema())
    assert problems, instance
    assert any(path in problem for problem in problems), problems


def test_the_schema_states_that_the_harness_infers_no_policy():
    """Where a reader meets the rule the code deliberately does not encode.

    A live harness artifact, read as it ships: the recommendation about closed
    items lives in the reference script, and this file says the decision is the
    command's.
    """
    described = json.dumps(items_schema())
    assert "status" in described
    assert "closed" in described.lower()


# --------------------------------------------------------------------------
# The seam, and the callers it does not have
# --------------------------------------------------------------------------


def harness_sources(root: Path) -> list[Path]:
    """Every file a caller of this seam could live in: `orchestration/*.py`
    and all of `scripts/`, which carry no suffix and are parsed as Python."""
    sources = sorted((root / "orchestration").glob("*.py"))
    sources += sorted(path for path in (root / "scripts").iterdir()
                      if path.is_file())
    return sources


def _module_constants(tree: ast.Module) -> dict[str, str]:
    return {target.id: node.value.value
            for node in tree.body if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            for target in node.targets if isinstance(target, ast.Name)}


def sources_reading(key: str, root: Path) -> set[str]:
    """Which sources read `key` out of a `config` mapping, by path.

    Both forms the harness uses, with a key named through a module-level
    constant resolved — which is how this module's own reads are spelled, so a
    scan that did not resolve them would find nothing and say so cheerfully.
    A *mention* is not a read: `scripts/l5-init` names the key in a comment,
    and this scan must not report it.
    """
    found = set()
    for path in harness_sources(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constants = _module_constants(tree)

        def named(node) -> str | None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name):
                return constants.get(node.id)
            return None

        for node in ast.walk(tree):
            read = None
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "config" and node.args):
                read = named(node.args[0])
            elif (isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "config"):
                read = named(node.slice)
            if read == key:
                found.add(str(path.relative_to(root)))
    return found


def sources_importing(module: str, root: Path) -> set[str]:
    """Which sources import `module`, in either import form."""
    found = set()
    for path in harness_sources(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == module for alias in node.names):
                    found.add(str(path.relative_to(root)))
            elif isinstance(node, ast.ImportFrom) and node.module == module:
                found.add(str(path.relative_to(root)))
    return found


def planted_root(tmp_path: Path, name: str, source: str) -> Path:
    """A throwaway harness root holding one module and one script.

    Built rather than copied: what the scans below must report is a violation,
    and constructing the smallest tree that carries one keeps the control
    about the scan rather than about this repository.
    """
    root = tmp_path / "planted"
    (root / "orchestration").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "orchestration" / name).write_text(source, encoding="utf-8")
    (root / "scripts" / "l5-planted").write_text("pass\n", encoding="utf-8")
    return root


def test_the_query_module_is_the_only_source_that_reads_the_command_key():
    assert sources_reading(COMMAND_KEY, REPO_ROOT) == \
        {str(Path("orchestration") / "filed_query.py")}


def test_the_scan_reports_a_second_source_reading_the_command_key(tmp_path):
    """The control: a scan that reports one file is worth nothing until it has
    been shown to report two."""
    root = planted_root(
        tmp_path, "second_reader.py",
        f'def read(config):\n    return config.get("{COMMAND_KEY}")\n')
    assert sources_reading(COMMAND_KEY, root) == \
        {str(Path("orchestration") / "second_reader.py")}


def test_a_mention_of_the_key_is_not_read_as_a_read(tmp_path):
    """`scripts/l5-init` names the key in a comment, which is why the scan is
    an AST read rather than a substring search — and why the control below
    plants a comment and requires silence."""
    root = planted_root(tmp_path, "only_a_mention.py",
                        f"# a target opts in by naming {COMMAND_KEY}\n")
    assert sources_reading(COMMAND_KEY, root) == set()
    assert COMMAND_KEY in (SCRIPTS / "l5-init").read_text(encoding="utf-8")


def test_the_seams_callers_are_the_two_producers_and_the_brief_fetch():
    """The seam shipped with no caller, and these are the ones it has.

    Written as an emptiness assertion by the story that shipped the seam, so
    that a caller would be visible as an addition rather than as a line that
    was always there, and narrowed to name the Inspector when that one arrived.
    story-096 adds the second and last: `brief_fetch` asks the same command the
    other question, by key rather than by path, and resolves its settings
    through `resolve_settings` rather than reading the configuration key itself
    — which is why the scan above still reports exactly one source reading
    that key.

    The third and last is `brief_filing`, the second producer of briefs: it
    asks the same question the Inspector asks — what is already filed against
    these paths — about the one brief an assist session was asked to file, and
    resolves its settings the way the fetch does rather than reading the
    configuration key itself, which is why the scan above still reports exactly
    one source reading that key.

    Still an exact set equality in both directions, so a fourth caller fails
    here and a caller that stopped importing fails here too. What the equality
    holds is the property the emptiness was standing in for: the query is asked
    by the two producers of briefs and by the fetch a developer drives from a
    terminal, and by nothing a run, a resume or a sweep reaches. The control
    below is unchanged and is what stops this passing on a scan that has
    stopped reporting.
    """
    assert sources_importing("filed_query", REPO_ROOT) == {
        str(Path("orchestration") / "brief_fetch.py"),
        str(Path("orchestration") / "brief_filing.py"),
        str(Path("orchestration") / "inspection.py"),
    }


def test_the_import_scan_reports_a_caller_when_there_is_one(tmp_path):
    """The control for the absence above, in both import forms."""
    root = planted_root(tmp_path, "a_caller.py",
                        "import filed_query\n\n\ndef ask(paths, config):\n"
                        "    return filed_query.query(paths, config)\n")
    assert sources_importing("filed_query", root) == \
        {str(Path("orchestration") / "a_caller.py")}

    other = planted_root(tmp_path / "from-form", "another_caller.py",
                         "from filed_query import query\n")
    assert sources_importing("filed_query", other) == \
        {str(Path("orchestration") / "another_caller.py")}


UNCHANGED =("orchestration/outbox.py", "orchestration/command_transport.py")


@pytest.mark.parametrize("relative", UNCHANGED)
def test_this_story_left_the_filing_path_alone(relative, tmp_path):
    """Restated over a story this test builds rather than recalled out of this
    repository's own commit graph.

    The claim is the story's: the read side is written beside the filing path
    rather than through it. The predicate is the shared resolution's, and the
    control beside it shows the same call reporting the violation — so an
    empty diff here is a fact about a story that respected the path rather
    than about a comparison bounded at commits where nothing could differ.
    """
    respecting = conftest.constructed_story(tmp_path, respected=[relative],
                                            name="scope-respected")
    assert conftest.constructed_story_diff(respecting, [relative]) == ""
    violating = conftest.constructed_story(tmp_path, violated=[relative],
                                           name="scope-violated")
    assert conftest.constructed_story_diff(violating, [relative]) != ""


# --------------------------------------------------------------------------
# The reference pair
# --------------------------------------------------------------------------

#: How the marker each reference script uses is stated in it. One assignment
#: on one line in each file, which is what makes the two comparable without
#: either script being parsed as a shell program.
MARKER_ASSIGNMENT = re.compile(r'^PATH_MARKER_PREFIX="(?P<marker>.*)"$',
                               re.MULTILINE)


def declared_marker(text: str) -> str | None:
    found = MARKER_ASSIGNMENT.search(text)
    return None if found is None else found.group("marker")


def test_the_pair_writes_and_searches_for_the_same_marker():
    """Read out of both shipped scripts, so they cannot drift apart unnoticed.

    Live harness artifacts, and the subject of the assertion: what makes the
    pair able to find each other's work is that this one string is the same in
    both, and nothing else in the harness can enforce it.
    """
    writes = declared_marker(
        (TEMPLATES / SYNC_DIR / "github.sh").read_text(encoding="utf-8"))
    searches = declared_marker(
        (TEMPLATES / QUERY_DIR / "github.sh").read_text(encoding="utf-8"))
    assert writes, "the sync script declares no path marker"
    assert searches, "the query script declares no path marker"
    assert writes == searches


def test_the_marker_comparison_reports_a_pair_that_drifted(tmp_path):
    """The control: the same extraction over a rendering of one script with
    its marker changed, which must come back different."""
    shipped = (TEMPLATES / SYNC_DIR / "github.sh").read_text(encoding="utf-8")
    drifted = MARKER_ASSIGNMENT.sub('PATH_MARKER_PREFIX="l5-other-marker: "',
                                    shipped, count=1)
    assert drifted != shipped
    assert declared_marker(drifted) != declared_marker(shipped)
    assert declared_marker(drifted) == "l5-other-marker: "


def test_the_query_script_states_the_contract_it_satisfies():
    """Its header is the documentation a target writing its own script reads."""
    header = (TEMPLATES / QUERY_DIR / "github.sh").read_text(encoding="utf-8")
    for stated in ("stdin", "stdout", "stderr", "exit 0"):
        assert stated in header, stated
    # The pairing, and the closed-item recommendation the harness declines to
    # encode, are both where a script author meets them.
    assert "templates/sync/github.sh" in header
    assert "REJECTED" in header
    assert "COMPLETED" in header


def test_both_reference_scripts_name_the_pairing_as_an_unenforced_contract():
    for directory in (SYNC_DIR, QUERY_DIR):
        header = (TEMPLATES / directory / "github.sh").read_text(
            encoding="utf-8")
        assert "cannot enforce" in header, directory
        assert "no dedupe" in header, directory


# --------------------------------------------------------------------------
# scripts/l5-init installs both halves
# --------------------------------------------------------------------------


@pytest.fixture
def initialized(tmp_path: Path) -> Path:
    """A target built by running the real l5-init, as a target's own would be."""
    root = tmp_path / "fresh"
    root.mkdir()
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "l5-init"), "--test-command",
         "echo tests-ok"],
        cwd=root, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return root


def test_a_freshly_initialized_target_has_both_halves_of_the_pair(initialized):
    """A target that got one without the other would have filing with no
    dedupe behind it, which is the half-a-pair state the story exists to
    avoid."""
    assert REFERENCE_QUERY_SCRIPTS, "the harness ships no reference query command"
    for directory, names in ((QUERY_DIR, REFERENCE_QUERY_SCRIPTS),
                             (SYNC_DIR, sorted(path.name for path
                                               in (TEMPLATES / SYNC_DIR).glob("*.sh")))):
        installed = initialized / ".harness" / directory
        assert installed.is_dir(), directory
        assert sorted(path.name for path in installed.iterdir()) == names
        for name in names:
            copy = installed / name
            assert copy.read_bytes() == \
                (TEMPLATES / directory / name).read_bytes()
            assert copy.stat().st_mode & stat.S_IXUSR
            assert os.access(copy, os.X_OK)


def test_a_freshly_initialized_target_sets_none_of_the_three_new_keys(
        initialized):
    """It opts in to asking rather than discovering that it asks.

    The keys are in the file, commented out with their explanation, which is
    what makes opting in an uncomment rather than a search of the schema.
    """
    config = harness_config.load_config(initialized)
    written = (initialized / ".harness" / "config.yaml").read_text(
        encoding="utf-8")
    for key in (COMMAND_KEY, TIMEOUT_KEY, MAX_ITEMS_KEY):
        assert key not in config, key
        assert f"# {key}:" in written, key


def test_this_repository_carries_the_installed_query_script():
    """A shipped artifact, so this repository's own `.harness/` is the subject.

    The reference implementation is exercised by the repository that ships it,
    which is what stops the template being a file nobody ever runs — and the
    pair is what makes holding one half of it wrong.
    """
    for directory in (SYNC_DIR, QUERY_DIR):
        for template in sorted((TEMPLATES / directory).glob("*.sh")):
            installed = REPO_ROOT / ".harness" / directory / template.name
            assert installed.read_bytes() == template.read_bytes()
            assert os.access(installed, os.X_OK)


# --------------------------------------------------------------------------
# The pair, run against a stub tracker this module wrote
#
# The two shipped scripts are the subject here, so they are run as they ship.
# What they talk to is not: `gh` is a stub written below and placed first on
# PATH, so nothing reaches a network and no real tracker is involved. `jq` is
# the scripts' own stated dependency and is not something this module can
# stand in for, so these are skipped where it is absent and the assertions
# that need neither — the marker comparison above — carry the claim there.
# --------------------------------------------------------------------------

JQ = shutil.which("jq")

#: How the two shipped scripts are launched here. A template is a file to be
#: copied rather than a file to be run — `l5-init` is what makes the installed
#: copy executable — so the interpreter its shebang names is stated instead of
#: the executable bit being relied on. What is under test is still the shipped
#: text: these are the templates, read and run as they ship.
INTERPRETER = "bash"


def reference_script(directory: str) -> str:
    """The shipped script in `directory`, as a command line that will launch."""
    return f"{INTERPRETER} {shlex.quote(str(TEMPLATES / directory / 'github.sh'))}"

needs_jq = pytest.mark.skipif(
    JQ is None,
    reason="the reference scripts state jq as their dependency and it is "
           "absent here; the marker comparison holds the pairing claim without it")

#: What the stub tracker records, and where. An environment variable rather
#: than a path compiled into the stub, so one stub serves both scripts.
LEDGER_VARIABLE = "L5_STUB_LEDGER"

STUB_GH = '''#!INTERPRETER
"""A stub `gh`, standing in for a tracker. It reaches no network.

It implements exactly the invocations the two reference scripts make: `issue
create`, which appends to a ledger and prints a URL, `issue list --search`,
which matches the search text against each issue's body, and `issue view`,
which prints one issue's body by the key the create printed -- the invocation
the query script makes to answer a brief-fetch question.
"""
import json
import os
import sys


def flag(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


argv = sys.argv[1:]
ledger = os.environ["LEDGER_VARIABLE"]
issues = json.load(open(ledger)) if os.path.exists(ledger) else []

if argv[:2] == ["issue", "create"]:
    number = len(issues) + 1
    issue = {
        "number": number,
        "title": flag(argv, "--title", ""),
        "body": flag(argv, "--body", ""),
        "url": "https://tracker.invalid/issues/%d" % number,
        "state": "OPEN",
        "stateReason": None,
    }
    issues.append(issue)
    json.dump(issues, open(ledger, "w"))
    print(issue["url"])
elif argv[:2] == ["issue", "view"]:
    # One issue by the key `issue create` printed, which for this stub is the
    # url and for a real tracker is whatever that tracker's own key is. The
    # number is accepted too, because the reference sync falls back to it.
    wanted = argv[2]
    found = [issue for issue in issues
             if wanted in (issue["url"], str(issue["number"]))]
    if not found:
        sys.stderr.write("no issue is filed under %s\\n" % wanted)
        sys.exit(1)
    print(found[0]["body"])
elif argv[:2] == ["issue", "list"]:
    search = (flag(argv, "--search", "") or "").strip('"')
    fields = (flag(argv, "--json", "") or "").split(",")
    matched = [issue for issue in issues if search and search in issue["body"]]
    if "--jq" in argv:
        # The one program the sync script asks for: the first url, or nothing.
        print(matched[0]["url"] if matched else "")
    else:
        print(json.dumps([{name: issue.get(name) for name in fields}
                          for issue in matched]))
else:
    sys.stderr.write("the stub was asked for something it does not do: %s\\n"
                     % " ".join(argv))
    sys.exit(1)
'''


def stub_tracker(tmp_path: Path) -> tuple[dict, Path]:
    """A `gh` this module wrote, first on PATH, and the ledger it writes to."""
    directory = tmp_path / "stub-bin"
    ledger = tmp_path / "tracker-ledger.json"
    fixture_file(directory, "gh",
                 STUB_GH.replace("INTERPRETER", sys.executable)
                        .replace("LEDGER_VARIABLE", LEDGER_VARIABLE))
    environment = {
        **os.environ,
        "PATH": f"{directory}{os.pathsep}{os.environ.get('PATH', '')}",
        LEDGER_VARIABLE: str(ledger),
    }
    return environment, ledger


def file_through_the_reference_sync(tmp_path: Path, environment: dict, *,
                                    key: str, payload: dict) -> str:
    """One entry filed by the shipped sync script, and the reference it named."""
    entry = {"key": key, "identity": {"kind": "finding"}, "state": "pending",
             "payload": payload}
    result = subprocess.run(
        [INTERPRETER, str(TEMPLATES / SYNC_DIR / "github.sh")],
        input=json.dumps(entry), capture_output=True, text=True, timeout=60,
        cwd=tmp_path,
        env={**environment, command_transport.KEY_ENVIRONMENT_VARIABLE: key})
    assert result.returncode == 0, result.stderr
    return result.stdout.strip().splitlines()[-1]


def bodies(ledger: Path) -> list[str]:
    return [issue["body"] for issue in json.loads(ledger.read_text())]


@needs_jq
def test_the_sync_script_writes_one_marker_per_path_the_payload_carries(
        tmp_path):
    environment, ledger = stub_tracker(tmp_path)
    file_through_the_reference_sync(
        tmp_path, environment, key="k-with-paths",
        payload={"title": "something to file", "body": "what it says",
                 "paths": list(ASKED)})

    body = bodies(ledger)[0]
    marker = declared_marker(
        (TEMPLATES / SYNC_DIR / "github.sh").read_text(encoding="utf-8"))
    for path in ASKED:
        assert f"{marker}{path}" in body, path


@needs_jq
def test_a_payload_carrying_no_paths_files_exactly_as_it_did_before(tmp_path):
    """The addition is additive.

    The control is the filing above: the same script, the same stub, a payload
    differing only in that it carries paths, whose body *does* carry the
    marker — so the absence here is the guard rather than a marker that is
    never written.
    """
    environment, ledger = stub_tracker(tmp_path)
    reference = file_through_the_reference_sync(
        tmp_path, environment, key="k-without-paths",
        payload={"title": "something to file", "body": "what it says"})
    assert reference.startswith("https://tracker.invalid/")

    marker = declared_marker(
        (TEMPLATES / SYNC_DIR / "github.sh").read_text(encoding="utf-8"))
    without = bodies(ledger)[0]
    assert marker not in without
    assert "what it says" in without
    assert "k-without-paths" in without

    file_through_the_reference_sync(
        tmp_path, environment, key="k-with-paths",
        payload={"title": "something else", "body": "what it says",
                 "paths": [ASKED[0]]})
    assert marker in bodies(ledger)[1]


@needs_jq
def test_the_query_script_finds_what_the_sync_script_filed(tmp_path):
    """The pair, driven end to end through the harness's own query.

    The shipped query script is the configured command, so what is asserted is
    an `Answer` the module built out of the reference implementation's stdout
    — which is the whole path a target gets when it installs both halves.
    """
    environment, _ = stub_tracker(tmp_path)
    url = file_through_the_reference_sync(
        tmp_path, environment, key="k-1",
        payload={"title": "the parser drops the last token",
                 "body": "what it says", "paths": list(ASKED)})

    previous = dict(os.environ)
    os.environ.update({key: environment[key]
                       for key in ("PATH", LEDGER_VARIABLE)})
    try:
        answer = asked(reference_script(QUERY_DIR), tmp_path)
        unrelated = asked(reference_script(QUERY_DIR), tmp_path,
                          paths=("src/nothing-is-filed-against-this.py",))
    finally:
        os.environ.clear()
        os.environ.update(previous)

    assert answer.answered is True, answer.reason
    assert [item.key for item in answer.items] == [url]
    assert answer.items[0].title == "the parser drops the last token"
    assert set(answer.items[0].paths) == set(ASKED)

    # Nothing is filed against a path the sync script never wrote a marker
    # for, and the query says so as an answer rather than as a silence.
    assert unrelated.answered is True, unrelated.reason
    assert unrelated.items == ()


@needs_jq
def test_the_query_script_answers_nothing_known_when_its_search_fails(tmp_path):
    """A search that failed makes the whole answer unreliable, so the script
    exits non-zero and the harness reads that as nothing known.

    The stub is asked for something it does not do — no ledger is written, so
    `gh issue list` finds no ledger to read and the script's own failure path
    runs — by pointing the ledger variable at a directory.
    """
    environment, _ = stub_tracker(tmp_path)
    broken = {**environment, LEDGER_VARIABLE: str(tmp_path)}

    previous = dict(os.environ)
    os.environ.update({key: broken[key] for key in ("PATH", LEDGER_VARIABLE)})
    try:
        answer = asked(reference_script(QUERY_DIR), tmp_path)
    finally:
        os.environ.clear()
        os.environ.update(previous)

    assert knows_nothing(answer)


# --------------------------------------------------------------------------
# No target stack entered the harness with this module
# --------------------------------------------------------------------------


def test_the_scan_that_holds_harness_source_free_of_target_literals_covers_it():
    """The existing scan, run rather than cited.

    Its subject is what this repository ships, so it is pointed at this
    repository and required to report nothing in the new module. The control
    is a copy of the module with a provider named in it, which the same scan
    must report — so silence here is a fact about the module rather than about
    a scan that does not look at it.
    """
    relative = "orchestration/filed_query.py"
    assert [finding for finding in harness_source.scan(REPO_ROOT)
            if finding.path == relative] == []
    assert any(path.name == "filed_query.py"
               for path in (REPO_ROOT / "orchestration").glob("*.py"))


def test_that_scan_reports_a_provider_named_in_the_module(tmp_path):
    """The control, built against a throwaway root rather than by editing this
    repository."""
    root = tmp_path / "scanned"
    (root / "orchestration").mkdir(parents=True)
    source = (REPO_ROOT / "orchestration" / "filed_query.py").read_text(
        encoding="utf-8")
    planted = source + '\n\nPLANTED = "pytest -q"\n'
    (root / "orchestration" / "filed_query.py").write_text(planted,
                                                           encoding="utf-8")
    reported = [finding for finding in harness_source.scan(root)
                if finding.path.endswith("filed_query.py")]
    assert reported, "the scan sees nothing in the module it is pointed at"
