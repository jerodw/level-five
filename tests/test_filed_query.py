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

  * **the board, through both copies of the sync script.** The mechanics are
    the template's and the values are this target's, so the same assertions
    are made of both: the installed `.harness/sync/github.sh` on its own
    values with nothing in its environment, and `templates/sync/github.sh`
    handed exactly the two values the installed copy sets. An entry reaches
    the board in the configured column; every failure below the issue's
    creation exits 75 with the issue still filed; an entry whose board call
    failed reaches the board on the next sweep with no second issue created;
    an item the board already reports a Status for is left where it is; and
    an item the listing did not return at all is answered transiently rather
    than overwritten.

  * **the split between the two copies.** The template carries no project and
    no column, the installed copy carries both, and every line the two do not
    share is one of the editable constant assignments — asserted as the shape
    of the difference rather than as byte identity, which the installed copy
    is meant to break.

  * **what the byte comparison used to guarantee.** That the file this
    repository runs is the file its suite exercises is asserted behaviourally
    in its place: the installed sync script files a brief, and the command
    this repository has configured as its `filed_query_command` — read out of
    `.harness/config.yaml` rather than named here — answers for it and
    fetches it back whole.

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
    filing with paths, whose body carries one marker per path;
  * "no item was added" and "no `item-edit` was made" each sit beside the same
    drive with nothing broken, where the item is added and the Status written,
    which the stub's record of every project call it was made shows;
  * "no second issue was created" sits beside a filing under a different key,
    which does create one;
  * "the template names no project and no column" sits beside the same
    extraction over the installed copy, which names both;
  * "the two copies differ only in constant values" sits beside a rendering of
    the template differing in a line of mechanics, which the same predicate
    reports;
  * "neither sync script invokes git" sits beside a rendering of one with a
    commit added, which the same scan reports.

Every command driven as a `filed_query_command` here is a file this module
wrote, and `fixture_command_problems` is what makes that a checked property
rather than a habit. Nothing here reaches a network: the two reference
scripts are run against a stub `gh` this module wrote, first on `PATH`.
"""
from __future__ import annotations

import ast
import difflib
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

import brief_fetch
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

#: The two copies of the sync script: the one the harness ships to every other
#: target, and the one this repository actually files its own briefs through.
TEMPLATE_SYNC = TEMPLATES / SYNC_DIR / "github.sh"
INSTALLED_SYNC = REPO_ROOT / ".harness" / SYNC_DIR / "github.sh"

#: How an editable constant is written in a sync script: one name, one
#: environment variable, one default, on one line. Both copies state their
#: values this way, which is what lets the difference between them be read as
#: values rather than as text.
CONSTANT_ASSIGNMENT = re.compile(
    r'^(?P<name>[A-Z][A-Z0-9_]*)="\$\{(?P<variable>L5_[A-Z0-9_]+)'
    r':-(?P<default>[^}]*)\}"', re.MULTILINE)


def sync_constants(text: str) -> dict[str, tuple[str, str]]:
    """Each editable constant a sync script declares: name → (variable, default).

    Read off the script rather than listed here, so a constant that was renamed
    or dropped is a resolution that fails rather than an override that silently
    stops overriding anything.
    """
    return {found.group("name"): (found.group("variable"),
                                  found.group("default"))
            for found in CONSTANT_ASSIGNMENT.finditer(text)}


TEMPLATE_CONSTANTS = sync_constants(TEMPLATE_SYNC.read_text(encoding="utf-8"))

#: The prefix every one of those variables shares, derived from the template
#: rather than spelled here — it is what `stub_tracker` strips out of the
#: environment so a copy driven "with nothing set" really has nothing set.
SYNC_VARIABLE_PREFIX = os.path.commonprefix(
    [variable for variable, _ in TEMPLATE_CONSTANTS.values()])

#: The board this deployment files against, and the column a newly filed entry
#: lands in. Written here rather than read out of `.harness/sync/github.sh`,
#: because the claim these make is that the installed copy files against
#: *these*: a test that read the values out of its own subject would pass
#: whatever they had been changed to, which is the assertion not being made.
THIS_TARGETS_PROJECT = "1"
THIS_TARGETS_PROJECT_OWNER = "@me"
THIS_TARGETS_STATUS_FIELD = "Status"
THIS_TARGETS_STATUS_OPTION = "Backlog"

#: A column nothing files into: what a human moved a landed item to, and what a
#: later sweep must leave it at.
A_COLUMN_A_HUMAN_MOVED_IT_TO = "In progress"

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

    The query half is still byte-identical to its template, and that half of
    the assertion is unchanged: nothing about this deployment's queries is
    particular to it. The sync half no longer is, deliberately — the installed
    copy carries the project this repository files against, which is exactly
    what a template must not carry — so what is asserted of it here is that it
    is present and runnable. What the byte comparison used to guarantee, that
    the file this repository runs is the file its suite exercises, is asserted
    behaviourally instead: the installed copy is driven end to end through the
    same stub tracker the template is.
    """
    for template in sorted((TEMPLATES / QUERY_DIR).glob("*.sh")):
        installed = REPO_ROOT / ".harness" / QUERY_DIR / template.name
        assert installed.read_bytes() == template.read_bytes()
        assert os.access(installed, os.X_OK)

    for template in sorted((TEMPLATES / SYNC_DIR).glob("*.sh")):
        installed = REPO_ROOT / ".harness" / SYNC_DIR / template.name
        assert installed.is_file()
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

#: How a test tells the stub to break on purpose. Two variables rather than
#: one, because the two failures they cause are different claims: a project
#: call that fails is a board the sync cannot write to, and a listing that
#: reports nothing is a board the sync cannot *read* — which the script must
#: not mistake for an item whose Status is empty.
FAIL_VARIABLE = "L5_STUB_FAILS_AT"
OMIT_VARIABLE = "L5_STUB_ITEM_LIST_REPORTS_NOTHING"

STUB_GH = '''#!INTERPRETER
"""A stub `gh`, standing in for a tracker and its project board. It reaches no
network.

It implements exactly the invocations the reference scripts make and exits
non-zero on anything else, which is what keeps it a fake tracker rather than a
second implementation:

  issue create        appends to the ledger and prints a URL.
  issue list --search matches the search text against each issue's body.
  issue view          prints one issue's body by the key the create printed --
                      the invocation the query script makes to answer a
                      brief-fetch question.
  project item-add    adds the url to a project, or reports the item already
                      there rather than adding a second one, which is the
                      behaviour the sync script's retry depends on.
  project item-list   the project's items. An item whose Status is unset
                      carries no `status` key at all, which is how gh reports
                      one, so a script that read a missing key as an empty
                      string and a script that could not tell them apart are
                      distinguishable here.
  project view        the project's node id.
  project field-list  the project's fields and their options, by name.
  project item-edit   sets one single-select field on one item, by ids.

The ledger holds the issues, the projects and every project invocation that was
made, so a test can assert on a call that was *not* made as well as on one that
was. FAIL_VARIABLE names project subcommands that must exit non-zero, and
OMIT_VARIABLE makes `item-list` report a project with no items in it.
"""
import json
import os
import sys


def flag(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


argv = sys.argv[1:]
ledger = os.environ["LEDGER_VARIABLE"]
state = json.load(open(ledger))
issues = state["issues"]
projects = state["projects"]


def save():
    json.dump(state, open(ledger, "w"))


def refuse(message):
    sys.stderr.write(message + "\\n")
    sys.exit(1)


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
    save()
    print(issue["url"])
elif argv[:2] == ["issue", "view"]:
    # One issue by the key `issue create` printed, which for this stub is the
    # url and for a real tracker is whatever that tracker's own key is. The
    # number is accepted too, because the reference sync falls back to it.
    wanted = argv[2]
    found = [issue for issue in issues
             if wanted in (issue["url"], str(issue["number"]))]
    if not found:
        refuse("no issue is filed under %s" % wanted)
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
elif argv[:1] == ["project"]:
    subcommand = argv[1]
    # Recorded before the refusal below, so a call a test told the stub to fail
    # is still a call the test can see was made.
    state["calls"].append({"command": subcommand, "argv": argv})
    save()
    if subcommand in (os.environ.get("FAIL_VARIABLE", "") or "").split(","):
        refuse("the stub was told to fail at project %s" % subcommand)

    if subcommand == "item-edit":
        # By ids, which is what the real one takes. Every id must resolve, so a
        # script that passed a field id where a project id belongs is reported
        # rather than quietly writing.
        project_id = flag(argv, "--project-id")
        owned = [one for one in projects.values() if one["id"] == project_id]
        if not owned:
            refuse("no project is known by the id %s" % project_id)
        project = owned[0]
        fields = [one for one in project["fields"]
                  if one["id"] == flag(argv, "--field-id")]
        options = [one for one in (fields[0].get("options", []) if fields else [])
                   if one["id"] == flag(argv, "--single-select-option-id")]
        items = [one for one in project["items"] if one["id"] == flag(argv, "--id")]
        if not (fields and options and items):
            refuse("project %s has no such item, field or option: %s"
                   % (project_id, " ".join(argv)))
        items[0]["status"] = options[0]["name"]
        save()
        print(json.dumps(items[0]))
    else:
        owner = flag(argv, "--owner")
        project = projects.get("%s/%s" % (owner, argv[2]))
        if project is None:
            refuse("no project %s is owned by %s" % (argv[2], owner))
        if subcommand == "view":
            print(json.dumps({"id": project["id"], "title": project["title"]}))
        elif subcommand == "field-list":
            print(json.dumps({"fields": project["fields"]}))
        elif subcommand == "item-add":
            url = flag(argv, "--url")
            found = [one for one in project["items"] if one["url"] == url]
            if found:
                item = found[0]
            else:
                item = {"id": "PVTI_%d" % (len(project["items"]) + 1), "url": url}
                project["items"].append(item)
                save()
            print(json.dumps({"id": item["id"], "type": "Issue",
                              "url": item["url"]}))
        elif subcommand == "item-list":
            limit = int(flag(argv, "--limit", "30"))
            reported = ([] if os.environ.get("OMIT_VARIABLE")
                        else project["items"][:limit])
            listed = []
            for one in reported:
                shown = {"id": one["id"],
                         "content": {"type": "Issue", "url": one["url"]}}
                if one.get("status"):
                    shown["status"] = one["status"]
                listed.append(shown)
            print(json.dumps({"items": listed}))
        else:
            refuse("the stub was asked for something it does not do: %s"
                   % " ".join(argv))
else:
    refuse("the stub was asked for something it does not do: %s"
           % " ".join(argv))
'''


def seeded_board() -> dict:
    """The board the stub starts with: this target's project, and a Status
    field with the options a project of this kind has.

    A Title field sits beside the Status field so that resolving the Status
    field's id by name is a resolution rather than a choice of the only field
    there is, and two options sit beside `Backlog` so that resolving the option
    by name is the same.
    """
    return {
        f"{THIS_TARGETS_PROJECT_OWNER}/{THIS_TARGETS_PROJECT}": {
            "id": "PVT_the-stubs-project",
            "title": "the board this module wrote",
            "fields": [
                {"id": "PVTF_title", "name": "Title", "type": "TITLE"},
                {
                    "id": "PVTSSF_status",
                    "name": THIS_TARGETS_STATUS_FIELD,
                    "type": "SINGLE_SELECT",
                    "options": [
                        {"id": "opt-backlog", "name": THIS_TARGETS_STATUS_OPTION},
                        {"id": "opt-moved", "name": A_COLUMN_A_HUMAN_MOVED_IT_TO},
                        {"id": "opt-done", "name": "Done"},
                    ],
                },
            ],
            "items": [],
        }
    }


def stub_tracker(tmp_path: Path) -> tuple[dict, Path]:
    """A `gh` this module wrote, first on PATH, and the ledger it writes to.

    Every variable the sync scripts read their board values out of is stripped
    from the environment rather than inherited, so a copy driven with nothing
    set is driven with nothing set — which is the whole claim of the test that
    files through the installed copy on this target's own values.
    """
    directory = tmp_path / "stub-bin"
    ledger = tmp_path / "tracker-ledger.json"
    fixture_file(directory, "gh",
                 STUB_GH.replace("INTERPRETER", sys.executable)
                        .replace("LEDGER_VARIABLE", LEDGER_VARIABLE)
                        .replace("FAIL_VARIABLE", FAIL_VARIABLE)
                        .replace("OMIT_VARIABLE", OMIT_VARIABLE))
    ledger.write_text(json.dumps(
        {"issues": [], "projects": seeded_board(), "calls": []}),
        encoding="utf-8")
    environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith(SYNC_VARIABLE_PREFIX)
    }
    environment.update({
        "PATH": f"{directory}{os.pathsep}{os.environ.get('PATH', '')}",
        LEDGER_VARIABLE: str(ledger),
    })
    return environment, ledger


def run_the_sync(script: Path, tmp_path: Path, environment: dict, *,
                 key: str, payload: dict,
                 extra: dict | None = None) -> subprocess.CompletedProcess:
    """One invocation of a sync script on one entry, whatever it exits."""
    entry = {"key": key, "identity": {"kind": "finding"}, "state": "pending",
             "payload": payload}
    return subprocess.run(
        [INTERPRETER, str(script)],
        input=json.dumps(entry), capture_output=True, text=True, timeout=60,
        cwd=tmp_path,
        env={**environment, **(extra or {}),
             command_transport.KEY_ENVIRONMENT_VARIABLE: key})


def file_through_the_reference_sync(tmp_path: Path, environment: dict, *,
                                    key: str, payload: dict,
                                    script: Path | None = None,
                                    extra: dict | None = None) -> str:
    """One entry filed by a sync script, and the reference it named."""
    result = run_the_sync(script or TEMPLATE_SYNC, tmp_path, environment,
                          key=key, payload=payload, extra=extra)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip().splitlines()[-1]


def ledger_state(ledger: Path) -> dict:
    return json.loads(ledger.read_text(encoding="utf-8"))


def bodies(ledger: Path) -> list[str]:
    return [issue["body"] for issue in ledger_state(ledger)["issues"]]


def board_items(ledger: Path) -> list[dict]:
    """The items on this target's project, as the stub holds them."""
    projects = ledger_state(ledger)["projects"]
    return projects[
        f"{THIS_TARGETS_PROJECT_OWNER}/{THIS_TARGETS_PROJECT}"]["items"]


def project_calls(ledger: Path, command: str | None = None) -> list[dict]:
    """Every project invocation the stub was made, optionally by subcommand."""
    return [call for call in ledger_state(ledger)["calls"]
            if command is None or call["command"] == command]


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
# The board, driven through both copies of the sync script
#
# The mechanics are the template's and the values are this target's, so the
# same assertions are made of both copies: the installed one on its own values
# with nothing in its environment, and the template on the two values the
# installed copy sets, handed to it through the variables the template itself
# declares for them. A mechanic that works in one and not the other is a
# failing test here rather than an unnoticed divergence.
# --------------------------------------------------------------------------

BOTH_SYNC_COPIES = [
    pytest.param(INSTALLED_SYNC, id="installed"),
    pytest.param(TEMPLATE_SYNC, id="template"),
]

#: Every project subcommand the shipped script invokes, read off the script
#: rather than listed here: the claim below is about *every* call made after
#: the issue exists, and a call added to the script without being added to this
#: list would be a claim quietly narrowed.
PROJECT_SUBCOMMANDS = sorted(set(re.findall(
    r"gh project ([a-z-]+)", TEMPLATE_SYNC.read_text(encoding="utf-8"))))

#: What the transport reads as "the entry stays pending and a later sweep
#: retries it". Named rather than written as a bare 75 beside each assertion.
TRANSIENT_EXIT = 75

#: The payload the board tests file. Nothing about the board depends on its
#: shape, so it is the smallest thing the sync script can file.
AN_ENTRY = {"title": "the parser drops the last token",
            "body": "what it says", "paths": list(ASKED)}


def a_filed_brief() -> dict:
    """A payload that is a brief, for the fetch that reads one back whole.

    The workflow is derived from the definitions the harness holds rather than
    named here, because what this asserts is that the pair carries a payload
    back unchanged — a name written here would make it assert which workflows
    this repository ships instead.
    """
    return {
        **AN_ENTRY,
        "slug": "the-parser-drops-the-last-token",
        "category": "correctness",
        "severity": 2,
        "confidence": "high",
        "effort": "S",
        "workflow": harness_config.workflow_names(REPO_ROOT)[0],
    }


def board_environment_for(script: Path) -> dict:
    """What a copy needs in its environment to file against the stub's board.

    The installed copy needs nothing, which is the point of it. The template
    carries no project and no column by design, so it is handed exactly the two
    values the installed copy sets in its own text.
    """
    if script == INSTALLED_SYNC:
        return {}
    return {
        TEMPLATE_CONSTANTS["PROJECT"][0]: THIS_TARGETS_PROJECT,
        TEMPLATE_CONSTANTS["STATUS_OPTION"][0]: THIS_TARGETS_STATUS_OPTION,
    }


def sync_to_the_board(script: Path, tmp_path: Path, environment: dict, *,
                      key: str, payload: dict | None = None,
                      breaking: dict | None = None):
    """One invocation of `script` against the stub's board.

    `breaking` is whatever the stub is to be broken with for this invocation
    alone, so a test can drive the same key twice with the board failing the
    first time and answering the second.
    """
    return run_the_sync(
        script, tmp_path, environment, key=key, payload=payload or AN_ENTRY,
        extra={**board_environment_for(script), **(breaking or {})})


def test_the_template_declares_the_two_constants_the_board_tests_override():
    """What `board_environment_for` rests on, asserted rather than assumed.

    A shipped artifact and the subject: the template's whole design is that its
    board values are set from outside it, so the two constants below must exist
    and must be spelled with the prefix the stripping in `stub_tracker` uses. A
    rename would otherwise leave the template driven with no project at all,
    and every board assertion about it passing on a board it never touched.
    """
    assert set(TEMPLATE_CONSTANTS) >= {"PROJECT", "STATUS_OPTION"}, \
        sorted(TEMPLATE_CONSTANTS)
    assert SYNC_VARIABLE_PREFIX.startswith("L5_")
    for name in ("PROJECT", "STATUS_OPTION"):
        assert TEMPLATE_CONSTANTS[name][0].startswith(SYNC_VARIABLE_PREFIX), name
    assert PROJECT_SUBCOMMANDS, "the script invokes no project subcommand"


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_an_entry_filed_with_a_project_configured_lands_on_the_board(
        script, tmp_path):
    """The item exists and its Status is the configured option."""
    environment, ledger = stub_tracker(tmp_path)
    result = sync_to_the_board(script, tmp_path, environment, key="k-board")

    assert result.returncode == 0, result.stderr
    url = result.stdout.strip().splitlines()[-1]
    assert url.startswith("https://tracker.invalid/")

    items = board_items(ledger)
    assert len(items) == 1, items
    assert items[0]["url"] == url
    assert items[0]["status"] == THIS_TARGETS_STATUS_OPTION


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
@pytest.mark.parametrize("subcommand", PROJECT_SUBCOMMANDS)
def test_every_failure_after_the_issue_exists_is_transient(
        subcommand, script, tmp_path):
    """The issue is the record and the board is a view of it.

    Each project call the script makes is failed in turn, and each must exit 75
    rather than 0 or 1: a zero would report an entry as landed with the board
    call lost, and a non-zero that is not 75 would fail the entry terminally
    and lose it. The issue is filed either way, which is what makes the retry
    the next sweep performs find it rather than create a second one.
    """
    environment, ledger = stub_tracker(tmp_path)
    result = sync_to_the_board(script, tmp_path, environment, key="k-fails",
                               breaking={FAIL_VARIABLE: subcommand})

    assert result.returncode == TRANSIENT_EXIT, (result.returncode, result.stderr)
    assert len(ledger_state(ledger)["issues"]) == 1
    assert project_calls(ledger, subcommand), \
        f"the script never invoked project {subcommand}"


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_board_failure_leaves_the_entry_pending_with_no_item(
        script, tmp_path):
    """The first half of the retry, stated on its own.

    The control is the test above it: the same script, the same stub and the
    same entry with nothing broken files an item — so "no item" here is the
    board call having failed rather than a board nothing ever reaches.
    """
    environment, ledger = stub_tracker(tmp_path)
    result = sync_to_the_board(script, tmp_path, environment, key="k-retry",
                               breaking={FAIL_VARIABLE: "item-add"})

    assert result.returncode == TRANSIENT_EXIT, result.stderr
    assert len(ledger_state(ledger)["issues"]) == 1
    assert board_items(ledger) == []


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_the_next_sweep_reaches_the_board_for_an_issue_already_created(
        script, tmp_path):
    """The repair, driven rather than read.

    One key, two invocations, the board failing on the first and answering on
    the second. The second invocation's search finds the issue the first one
    created, and the whole claim is that it goes on to do the board work
    anyway: a script whose found-existing path answers and returns leaves the
    board empty here forever.

    The absence — no second issue — is controlled beside itself: a third
    invocation under a *different* key does create one, so the count staying at
    one is idempotency rather than a stub that stopped filing.
    """
    environment, ledger = stub_tracker(tmp_path)

    failed = sync_to_the_board(script, tmp_path, environment, key="k-twice",
                               breaking={FAIL_VARIABLE: "item-add"})
    assert failed.returncode == TRANSIENT_EXIT, failed.stderr
    assert board_items(ledger) == []
    created = ledger_state(ledger)["issues"][0]["url"]

    retried = sync_to_the_board(script, tmp_path, environment, key="k-twice")
    assert retried.returncode == 0, retried.stderr
    assert retried.stdout.strip().splitlines()[-1] == created

    assert len(ledger_state(ledger)["issues"]) == 1, "a second issue was created"
    items = board_items(ledger)
    assert len(items) == 1, items
    assert items[0]["url"] == created
    assert items[0]["status"] == THIS_TARGETS_STATUS_OPTION

    other = sync_to_the_board(script, tmp_path, environment, key="k-a-different-one")
    assert other.returncode == 0, other.stderr
    assert len(ledger_state(ledger)["issues"]) == 2
    assert len(board_items(ledger)) == 2


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_an_item_whose_status_the_board_reports_is_left_where_it_is(
        script, tmp_path):
    """Nothing moves an item out of the column a human put it in.

    The write and its absence are driven in one test so each controls the
    other: the first invocation finds an empty Status and writes, which is
    observed both in the item and in the `item-edit` the stub recorded; a human
    then moves the item; and the second invocation over the same entry makes no
    `item-edit` at all and leaves the value alone.
    """
    environment, ledger = stub_tracker(tmp_path)
    first = sync_to_the_board(script, tmp_path, environment, key="k-settled")
    assert first.returncode == 0, first.stderr
    assert board_items(ledger)[0]["status"] == THIS_TARGETS_STATUS_OPTION
    assert len(project_calls(ledger, "item-edit")) == 1

    state = ledger_state(ledger)
    project = state["projects"][
        f"{THIS_TARGETS_PROJECT_OWNER}/{THIS_TARGETS_PROJECT}"]
    project["items"][0]["status"] = A_COLUMN_A_HUMAN_MOVED_IT_TO
    state["calls"] = []
    ledger.write_text(json.dumps(state), encoding="utf-8")

    again = sync_to_the_board(script, tmp_path, environment, key="k-settled")
    assert again.returncode == 0, again.stderr
    assert board_items(ledger)[0]["status"] == A_COLUMN_A_HUMAN_MOVED_IT_TO
    assert project_calls(ledger, "item-edit") == []


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_an_item_the_listing_did_not_report_is_a_failure_to_know(
        script, tmp_path):
    """A listing that did not return the item is not an empty Status.

    The stub reports a project with no items in it while the item is in fact
    there, which is what a listing bounded too short or a tracker answering
    partially looks like. Read as an empty Status it would be overwritten; read
    as a failure to know it is answered transiently and left alone.

    The control is the same drive with the listing answering, below the
    assertion: there the `item-edit` is made and the Status is written, so the
    absence here is the guard and not a write that never happens.
    """
    environment, ledger = stub_tracker(tmp_path)
    blind = sync_to_the_board(script, tmp_path, environment, key="k-unlisted",
                              breaking={OMIT_VARIABLE: "1"})

    assert blind.returncode == TRANSIENT_EXIT, (blind.returncode, blind.stderr)
    items = board_items(ledger)
    assert len(items) == 1, items
    assert "status" not in items[0], items[0]
    assert project_calls(ledger, "item-edit") == []

    seeing = sync_to_the_board(script, tmp_path, environment, key="k-unlisted")
    assert seeing.returncode == 0, seeing.stderr
    assert board_items(ledger)[0]["status"] == THIS_TARGETS_STATUS_OPTION
    assert len(project_calls(ledger, "item-edit")) == 1


@needs_jq
def test_the_installed_copy_files_to_this_targets_board_with_nothing_set(
        tmp_path):
    """This deployment's own wiring, rather than the test's environment.

    Nothing named with the sync scripts' variable prefix is in the environment
    this runs under — asserted, not assumed — so the project and the column the
    item lands in can only have come out of `.harness/sync/github.sh` itself.
    """
    environment, ledger = stub_tracker(tmp_path)
    assert [name for name in environment
            if name.startswith(SYNC_VARIABLE_PREFIX)] == []

    result = run_the_sync(INSTALLED_SYNC, tmp_path, environment,
                          key="k-this-target", payload=AN_ENTRY)
    assert result.returncode == 0, result.stderr

    projects = ledger_state(ledger)["projects"]
    landed = projects[
        f"{THIS_TARGETS_PROJECT_OWNER}/{THIS_TARGETS_PROJECT}"]["items"]
    assert len(landed) == 1, landed
    assert landed[0]["status"] == THIS_TARGETS_STATUS_OPTION


@needs_jq
def test_the_template_with_no_project_configured_files_exactly_as_before(
        tmp_path):
    """A target that configures no project must file as it always has.

    The template is run with nothing set, which is what every other target gets
    until it edits its installed copy: the issue is filed, the reference is
    named, and the board is not touched at all. The control is the same
    template under the two values above, which does add an item.
    """
    environment, ledger = stub_tracker(tmp_path)
    result = run_the_sync(TEMPLATE_SYNC, tmp_path, environment,
                          key="k-no-project", payload=AN_ENTRY)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1].startswith(
        "https://tracker.invalid/")
    assert len(ledger_state(ledger)["issues"]) == 1
    assert project_calls(ledger) == []
    assert board_items(ledger) == []

    configured = sync_to_the_board(TEMPLATE_SYNC, tmp_path, environment,
                                   key="k-with-a-project")
    assert configured.returncode == 0, configured.stderr
    assert len(board_items(ledger)) == 1


# --------------------------------------------------------------------------
# The template carries no value particular to this deployment
# --------------------------------------------------------------------------


def test_the_template_names_no_project_and_no_status_option():
    """A shipped artifact and the subject: what the template carries.

    A template carrying a project number would file another repository's briefs
    onto this board. The owner is allowed the generic default it ships with;
    the project and the column must both default to empty, and the column this
    target files into must not appear anywhere in the file.

    The control is the same extraction and the same search over the installed
    copy, which does name both — so the emptiness here is the template's rather
    than a parse that stopped matching anything.
    """
    template = TEMPLATE_SYNC.read_text(encoding="utf-8")
    installed = sync_constants(INSTALLED_SYNC.read_text(encoding="utf-8"))

    assert TEMPLATE_CONSTANTS["PROJECT"][1] == ""
    assert TEMPLATE_CONSTANTS["STATUS_OPTION"][1] == ""
    assert TEMPLATE_CONSTANTS["PROJECT_OWNER"][1] == THIS_TARGETS_PROJECT_OWNER
    assert THIS_TARGETS_STATUS_OPTION not in template

    assert installed["PROJECT"][1] == THIS_TARGETS_PROJECT
    assert installed["STATUS_OPTION"][1] == THIS_TARGETS_STATUS_OPTION
    assert THIS_TARGETS_STATUS_OPTION in \
        INSTALLED_SYNC.read_text(encoding="utf-8")


def lines_that_differ(left: str, right: str) -> list[str]:
    """Every line one text has and the other does not, without its diff mark."""
    return [line[1:] for line in difflib.unified_diff(
        left.splitlines(), right.splitlines(), lineterm="", n=0)
        if line[:1] in "+-" and not line.startswith(("---", "+++"))]


def differences_that_are_not_constant_values(left: str, right: str) -> list[str]:
    return [line for line in lines_that_differ(left, right)
            if not CONSTANT_ASSIGNMENT.match(line)]


def test_the_installed_copy_differs_from_its_template_only_in_constant_values():
    """Both are shipped artifacts and both are the subject.

    The split the story rests on is that the mechanics live in one file and the
    values in the other, and this is what holds it: every line the two do not
    share is one of the editable constant assignments at the top. Textual
    identity is deliberately not asserted — the installed copy is *expected* to
    differ in its values — so what is asserted is the shape of the difference.
    """
    template = TEMPLATE_SYNC.read_text(encoding="utf-8")
    installed = INSTALLED_SYNC.read_text(encoding="utf-8")

    assert installed != template, \
        "the installed copy sets no value of its own, so it files nowhere"
    assert differences_that_are_not_constant_values(template, installed) == []


def test_that_comparison_reports_a_difference_that_is_not_a_constant(tmp_path):
    """The control: the same predicate over a copy of the template whose
    difference is a line of mechanics rather than a value.

    Rendered here rather than written to the tree, so the control is about the
    comparison and not about this repository.
    """
    template = TEMPLATE_SYNC.read_text(encoding="utf-8")
    tampered = template.replace("fail_transient()", "fail_transient_renamed()")
    assert tampered != template

    reported = differences_that_are_not_constant_values(template, tampered)
    assert reported, "the comparison sees no difference it should report"
    assert any("fail_transient_renamed" in line for line in reported), reported


# --------------------------------------------------------------------------
# A sync command must not commit
# --------------------------------------------------------------------------


#: A `git` invoked as a command: at the start of a line or after a shell
#: operator, rather than the word appearing inside a longer one or in the
#: header paragraph that tells a script author not to add one.
GIT_INVOCATION = re.compile(r'(?:^|[;&|(]|\$\()\s*git\s', re.MULTILINE)


def git_invocations(text: str) -> list[str]:
    return [line for line in text.splitlines()
            if GIT_INVOCATION.search(line) and not line.lstrip().startswith("#")]


@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_no_sync_script_invokes_git(script):
    """The header says a sync command must not commit and nothing enforces it.

    A shipped artifact and the subject. The control is below: the same scan
    over a rendering of the same script with a commit added reports it, so
    silence here is the file rather than a scan that matches nothing.
    """
    assert git_invocations(script.read_text(encoding="utf-8")) == []


def test_that_scan_reports_a_commit_added_to_a_sync_script():
    """The control, on a rendering rather than on the tree."""
    committing = TEMPLATE_SYNC.read_text(encoding="utf-8").replace(
        'echo "$url"', 'git commit -m "filed"\necho "$url"')
    reported = git_invocations(committing)
    assert reported, "the scan sees no git invocation in a script that has one"
    assert any("git commit" in line for line in reported), reported


# --------------------------------------------------------------------------
# What the byte-identity assertion used to guarantee, asserted behaviourally
# --------------------------------------------------------------------------


@needs_jq
def test_this_repositorys_own_pair_files_and_answers_through_its_configured_query(
        tmp_path):
    """The file this repository runs is the file its suite exercises.

    This is what stands in place of the byte comparison the story falsified:
    the installed sync script files a brief and the command this repository has
    configured as its `filed_query_command` — read out of `.harness/config.yaml`
    rather than named here — answers for it. Both halves are the installed
    copies, so a deployment whose installed pair had drifted from what its
    suite drives would fail here.

    Nothing reaches a network: the stub `gh` this module wrote is first on PATH
    for both halves, and the board it writes to is a file under `tmp_path`.
    """
    config = harness_config.load_config(REPO_ROOT)
    assert COMMAND_KEY in config, \
        f"this repository configures no {COMMAND_KEY}, so dedupe never runs"

    brief = a_filed_brief()
    environment, ledger = stub_tracker(tmp_path)
    url = file_through_the_reference_sync(
        tmp_path, environment, key="k-this-repositorys-own", payload=brief,
        script=INSTALLED_SYNC)
    assert board_items(ledger)[0]["status"] == THIS_TARGETS_STATUS_OPTION

    previous = dict(os.environ)
    os.environ.update({name: environment[name]
                       for name in ("PATH", LEDGER_VARIABLE)})
    try:
        answer = filed_query.query(ASKED, config, target_root=REPO_ROOT)
        unrelated = filed_query.query(
            ("src/nothing-is-filed-against-this.py",), config,
            target_root=REPO_ROOT)
        fetched = brief_fetch.fetch(url, config, target_root=REPO_ROOT,
                                    harness_root=REPO_ROOT)
    finally:
        os.environ.clear()
        os.environ.update(previous)

    # A second inspection over the same paths recognises the first one's brief
    # rather than refiling it, which is the whole point of turning the query
    # side on.
    assert answer.answered is True, answer.reason
    assert [item.key for item in answer.items] == [url]
    assert answer.items[0].title == brief["title"]
    assert set(answer.items[0].paths) == set(ASKED)

    # Nothing is filed against a path no marker was written for, and the pair
    # says so as an answer rather than as a silence. Its control is the
    # assertion above, where the same pair over the filed paths reports one.
    assert unrelated.answered is True, unrelated.reason
    assert unrelated.items == ()

    # The payload comes back whole rather than as a title and a body, which is
    # what the payload marker exists for: every field the brief was filed with
    # is the field it is fetched with.
    assert fetched.brief is not None, fetched.reason
    assert fetched.brief == brief


def test_this_repository_configures_its_query_command_at_its_installed_script():
    """A shipped artifact and the subject: the one line of configuration.

    The command the deployment actually files and asks through is the installed
    script beside the installed sync one, and it is runnable. The key needed no
    schema change — it was already declared — which is asserted here rather
    than argued.
    """
    config = harness_config.load_config(REPO_ROOT)
    configured = shlex.split(config[COMMAND_KEY])
    assert len(configured) == 1, configured

    command = (REPO_ROOT / configured[0]).resolve()
    installed = (REPO_ROOT / ".harness" / QUERY_DIR).resolve()
    assert command.parent == installed, command
    assert command.name in REFERENCE_QUERY_SCRIPTS, command.name
    assert command.is_file()
    assert os.access(command, os.X_OK)

    # Asked of the harness's own declaration reader rather than by opening the
    # schema here, so this module resolves no live artifact of its own and the
    # question is answered by the same route the pre-flight check asks it by.
    assert COMMAND_KEY in harness_config.declared_config_keys()


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
