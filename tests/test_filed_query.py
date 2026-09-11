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

  * **the reference commands.** `templates/scripts/github.sh`,
    `scripts/l5-init` and this repository's own `.harness/` are live harness
    artifacts and are the subjects of the assertions that name them: what
    this repository ships is read as it ships. One file answers all three
    jobs, dispatching on its first argument, so the sync branch and the query
    branch are two branches of one subject rather than two files that have to
    be held to agreeing. It is then run against a stub tracker this module
    wrote, so "the pair agrees" is a fact about what the branches do rather
    than about what the header says.

  * **the board, through both copies of the merged script.** The mechanics are
    the template's and the values are this target's, so the same assertions
    are made of both: the installed `.harness/scripts/github.sh` on its own
    values with nothing in its environment, and `templates/scripts/github.sh`
    handed exactly the two values the installed copy sets. An entry reaches
    the board in the configured column; every failure below the issue's
    creation exits 75 with the issue still filed; an entry whose board call
    failed reaches the board on the next sweep with no second issue created;
    an item the board already reports a Status for is left where it is; and
    an item the listing did not return at all is answered transiently rather
    than overwritten.

  * **the classification a filed brief carries.** A brief's category reaches
    the issue as a second label beside the one every entry gets, and its
    category, severity, confidence, effort and workflow reach the board as
    five single-select fields. The values driven are resolved from
    `schemas/story-brief.schema.json`'s enums and from the harness's own
    workflow listing rather than written here, so a category added to the
    schema is a case in the sweep without this module being edited. The two
    rules that differ are driven apart: a label the repository does not have
    is created and then applied, while a field the board does not have — or an
    option it does not offer — costs that field alone and the entry still
    lands.

  * **the split between the two copies.** The template carries no project, no
    column and no field name, the installed copy carries all of them, and
    every line the two do not share is one of the editable constant
    assignments — asserted as the shape of the difference rather than as byte
    identity, which the installed copy is meant to break.

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
  * "the template names no project, no column and no field" sits beside the
    same extraction over the installed copy, which names all of them;
  * "a payload carrying no category adds no label and creates none" sits beside
    the same filing of a brief, which does both;
  * "the field the board already reports is not edited" sits beside a field
    cleared on the same item, which is written by the same invocation;
  * "the label the script creates is what lets it be applied" sits beside a
    rendering of the same script with the create taken out, which the stub
    refuses the add for;
  * "the two copies differ only in constant values" sits beside a rendering of
    the template differing in a line of mechanics, which the same predicate
    reports;
  * "neither sync script invokes git" sits beside a rendering of one with a
    commit added, which the same scan reports;
  * "no field-name lookup states a rule of its own" sits beside two renderings
    of the template — one whose id lookup matches the board verbatim and one
    whose read spells the normalization inline — each of which the scan that
    exists to catch it reports.

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
from typing import NamedTuple

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

#: Where a target's tracker commands are installed, and the reference commands
#: themselves, derived from what the harness ships rather than written here:
#: the template directory is the declaration of what l5-init installs.
SCRIPTS_DIR = "scripts"
REFERENCE_SCRIPTS = sorted(
    path.name for path in (TEMPLATES / SCRIPTS_DIR).glob("*.sh"))

#: The three jobs one reference command answers to, named by the argument that
#: selects each. They are the harness's three configured commands, and being
#: three arguments to one file rather than three files is what this module's
#: subject now is.
SYNC_JOB = "sync"
QUERY_JOB = "query"
ITEM_JOB = "item"

#: The two copies of that command: the one the harness ships to every other
#: target, and the one this repository actually files its own briefs through.
TEMPLATE_SCRIPT = TEMPLATES / SCRIPTS_DIR / "github.sh"
INSTALLED_SCRIPT = REPO_ROOT / ".harness" / SCRIPTS_DIR / "github.sh"

#: How an editable constant is written in a tracker script: one name, one
#: environment variable, one default, on one line. Both copies state their
#: values this way, which is what lets the difference between them be read as
#: values rather than as text.
CONSTANT_ASSIGNMENT = re.compile(
    r'^(?P<name>[A-Z][A-Z0-9_]*)="\$\{(?P<variable>L5_[A-Z0-9_]+)'
    r':-(?P<default>[^}]*)\}"', re.MULTILINE)


def sync_constants(text: str) -> dict[str, tuple[str, str]]:
    """Each editable constant a tracker script declares: name → (variable, default).

    Read off the script rather than listed here, so a constant that was renamed
    or dropped is a resolution that fails rather than an override that silently
    stops overriding anything.
    """
    return {found.group("name"): (found.group("variable"),
                                  found.group("default"))
            for found in CONSTANT_ASSIGNMENT.finditer(text)}


TEMPLATE_CONSTANTS = sync_constants(TEMPLATE_SCRIPT.read_text(encoding="utf-8"))

#: The prefix every one of those variables shares, derived from the template
#: rather than spelled here — it is what `stub_tracker` strips out of the
#: environment so a copy driven "with nothing set" really has nothing set.
#: Since the three jobs became one file it is the prefix they all share rather
#: than any one job's, which is what makes stripping it strip every job's.
SYNC_VARIABLE_PREFIX = os.path.commonprefix(
    [variable for variable, _ in TEMPLATE_CONSTANTS.values()])

#: What the shared constants are named, which is the whole of the claim that
#: the three jobs no longer each carry their own copy of them.
PROJECT_CONSTANT = "L5_TRACKER_PROJECT"
PROJECT_OWNER_CONSTANT = "L5_TRACKER_PROJECT_OWNER"
STATUS_FIELD_CONSTANT = "L5_TRACKER_STATUS_FIELD"
SHARED_CONSTANTS = (PROJECT_CONSTANT, PROJECT_OWNER_CONSTANT,
                    STATUS_FIELD_CONSTANT)

#: The names that would say a job kept a copy of one of those. Written out
#: rather than derived, because what is asserted is that none of them is there.
RETIRED_PER_JOB_CONSTANTS = (
    "L5_SYNC_PROJECT", "L5_SYNC_PROJECT_OWNER", "L5_SYNC_STATUS_FIELD",
    "L5_ITEM_PROJECT", "L5_ITEM_PROJECT_OWNER", "L5_ITEM_STATUS_FIELD",
)

#: What the installed copy declares, read once here and derived from below, so
#: that every board value this module and the modules importing it use comes
#: from one reading of the file that decides them.
INSTALLED_CONSTANTS = sync_constants(
    INSTALLED_SCRIPT.read_text(encoding="utf-8"))


def this_targets(constant: str,
                 constants: dict[str, tuple[str, str]] | None = None) -> str:
    """What a tracker script declares for one of its board constants.

    Read out of the script rather than written down, because a value written
    here is this suite's second copy of a configured value: a target that
    renamed the column its filings land in, or moved to another board, would
    have to edit this module to keep the suite green, and a configuration
    change would arrive as a test failure.

    What the writing-down was for is kept by two claims that name no value.
    The first is here: a declared value must be non-empty, so a target that has
    configured no board fails while this module is collected rather than
    passing every board assertion vacuously. The second is the drive — the
    installed copy is run with nothing set in its environment, so the board it
    reaches is the one it declares rather than one this module handed it.

    `constants` is a reading other than the installed one, which is how the
    same derivation is applied to a copy declaring invented values.
    """
    _, declared = (INSTALLED_CONSTANTS if constants is None
                   else constants)[constant]
    assert declared, f"{constant} declares no value, so no board is configured"
    return declared


#: The board this deployment files against, and the column a newly filed entry
#: lands in, each read out of `.harness/scripts/github.sh`.
STATUS_OPTION_CONSTANT = "STATUS_OPTION"
THIS_TARGETS_PROJECT = this_targets(PROJECT_CONSTANT)
THIS_TARGETS_PROJECT_OWNER = this_targets(PROJECT_OWNER_CONSTANT)
THIS_TARGETS_STATUS_FIELD = this_targets(STATUS_FIELD_CONSTANT)
THIS_TARGETS_STATUS_OPTION = this_targets(STATUS_OPTION_CONSTANT)

#: A column nothing files into: what a human moved a landed item to, and what a
#: later sweep must leave it at.
A_COLUMN_A_HUMAN_MOVED_IT_TO = "In progress"


class Axis(NamedTuple):
    """One part of a brief's classification, on its way to the board.

    `payload_field` is what the brief calls it, `constant` is what the
    tracker script names its board field with, `field_name` is what this deployment
    calls that field on its board, and `values` is every value the field may
    be written with.
    """

    payload_field: str
    constant: str
    field_name: str
    values: tuple[str, ...]


BRIEF_SHAPE = schema_validator.load_schema(brief_fetch.BRIEF_SCHEMA, REPO_ROOT)


def declared_values(name: str) -> tuple[str, ...]:
    """Every value the brief schema allows for one of its classifying fields.

    Resolved out of the shipped schema rather than listed here, because the
    acceptable values *are* the schema's enums: a category added there is a
    case in the sweep below without this module being edited, and a field that
    stopped declaring an enum is a resolution that raises rather than a sweep
    that quietly drives nothing. Rendered as the strings a board option is
    named with, which is what turns the integer severity into an option name.
    """
    return tuple(str(value) for value in BRIEF_SHAPE["properties"][name]["enum"])


def axis(payload_field: str, constant: str, values: tuple[str, ...]) -> Axis:
    """One axis, its board field name read out of the installed copy.

    The field names are read for the reason the Status values above are: they
    are what this target calls five columns, and a target that renamed one of
    them would otherwise have to edit this module. The values are not written
    here either, for a different reason: they are the schema's and the
    harness's, and restating them would be a second list beside them.
    """
    return Axis(payload_field, constant, this_targets(constant), values)


#: The five fields a brief's classification is written into, under the names
#: `.harness/scripts/github.sh` declares for them.
CLASSIFICATION = (
    axis("category", "CATEGORY_FIELD", declared_values("category")),
    axis("severity", "SEVERITY_FIELD", declared_values("severity")),
    axis("confidence", "CONFIDENCE_FIELD", declared_values("confidence")),
    axis("effort", "EFFORT_FIELD", declared_values("effort")),
    #: The workflow a brief is planned under is not an enum: the acceptable
    #: names are the definitions the harness holds, so they are read from the
    #: same listing the coordinator refuses an unknown name against.
    axis("workflow", "WORKFLOW_FIELD", harness_config.workflow_names(REPO_ROOT)),
)

#: Every constant this target's board values are read out of, so the module
#: holding the suite to reading them asks about the names this reading uses
#: rather than about a second list beside it.
BOARD_CONSTANTS = (PROJECT_CONSTANT, PROJECT_OWNER_CONSTANT,
                   STATUS_FIELD_CONSTANT, STATUS_OPTION_CONSTANT) + tuple(
    one.constant for one in CLASSIFICATION)

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
    header = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
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
# The reference commands
# --------------------------------------------------------------------------

#: How the marker the reference script uses is stated in it. One assignment on
#: one line, which is what makes it readable without the script being parsed as
#: a shell program.
MARKER_ASSIGNMENT = re.compile(r'^PATH_MARKER_PREFIX="(?P<marker>.*)"$',
                               re.MULTILINE)

#: The string an item's paths have been recorded under since story-093, written
#: here rather than read out of the subject: the claim is that the merge left
#: it alone, so items already filed stay findable, and a test that read the
#: value out of its own subject would pass whatever it had been changed to.
PATH_MARKER = "l5-path: "


def branch_source(text: str, name: str) -> str:
    """The body of one job's branch, from its `name() {` line to the closing
    brace in the first column.

    Read by shape rather than by parsing shell, which is enough for the one
    question asked of it: whether the statement of a shared rule is inside a
    branch, and therefore a copy, or above them all and therefore shared.
    """
    opening = f"\n{name}() {{\n"
    start = text.index(opening) + len(opening)
    return text[start:text.index("\n}\n", start)]


def declared_marker(text: str) -> str | None:
    found = MARKER_ASSIGNMENT.search(text)
    return None if found is None else found.group("marker")


def test_the_pair_writes_and_searches_for_the_same_marker():
    """Declared once in the one shipped script, so there is nothing to drift.

    A live harness artifact and the subject of the assertion: what makes the
    sync branch's work findable by the query branch is that the marker is one
    string, and until the two were one file nothing in the harness could
    enforce it. What is asserted is therefore that the file states it exactly
    once and that both branches refer to that statement rather than to a rule
    of their own.
    """
    shipped = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    marker = declared_marker(shipped)
    assert marker, "the script declares no path marker"
    assert len(MARKER_ASSIGNMENT.findall(shipped)) == 1, \
        "the path marker is stated more than once"
    assert marker == PATH_MARKER

    # The string is what the two branches were held to agreeing about, and it
    # is unchanged by the merge, so an item already filed stays findable.
    writes = branch_source(shipped, "do_sync")
    searches = branch_source(shipped, "do_query")
    assert "PATH_MARKER_PREFIX" in writes
    assert "PATH_MARKER_PREFIX" in searches
    for branch in (writes, searches):
        assert MARKER_ASSIGNMENT.search(branch) is None


def test_the_marker_comparison_reports_a_pair_that_drifted(tmp_path):
    """The control: the same extraction over a rendering of one script with
    its marker changed, which must come back different."""
    shipped = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    drifted = MARKER_ASSIGNMENT.sub('PATH_MARKER_PREFIX="l5-other-marker: "',
                                    shipped, count=1)
    assert drifted != shipped
    assert declared_marker(drifted) != declared_marker(shipped)
    assert declared_marker(drifted) == "l5-other-marker: "


def test_the_merged_script_states_every_contract_it_satisfies():
    """Its header is the documentation a target writing its own script reads,
    and it now carries all three contracts rather than one.

    The three headers said things the harness relies on and cannot enforce, so
    none of them could be lost in the merge: the file states each contract and
    says which branch it belongs to.
    """
    header = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    for stated in ("stdin", "stdout", "stderr", "exit 0"):
        assert stated in header, stated

    # One contract heading per job, each naming the argument it belongs to.
    for job in (SYNC_JOB, QUERY_JOB, ITEM_JOB):
        assert f"github.sh {job}" in header, job

    # The sync contract's two unenforceable promises.
    assert "IDEMPOTENT GIVEN THE KEY" in header
    assert "MUST NOT COMMIT" in header
    # The query contract's one-document rule and its policy on which items to
    # report, where a script author meets them.
    assert "NOTHING ELSE" in header
    assert "REJECTED" in header
    assert "COMPLETED" in header
    # The item contract's idempotency and its obligation to the other markers.
    assert "IDEMPOTENT GIVEN THE STORY ID" in header
    assert "MUST NOT DISTURB THE MARKERS" in header


def test_the_reference_script_names_the_pairing_as_an_unenforced_contract():
    header = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    assert "cannot enforce" in header
    assert "no dedupe" in header


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


def test_a_freshly_initialized_target_has_every_job_of_the_family(initialized):
    """A target that got one job without the others would have filing with no
    dedupe behind it, or a projection nothing could be planned from — the
    half-a-pair state the family exists to avoid, and one a single file makes
    unreachable.

    What is asserted is what `.harness/scripts/` holds against what
    `templates/scripts/` holds: the directory is the declaration, so a second
    reference command shipped later is installed with no edit here.
    """
    assert REFERENCE_SCRIPTS, "the harness ships no reference tracker command"
    installed = initialized / ".harness" / SCRIPTS_DIR
    assert installed.is_dir(), SCRIPTS_DIR
    assert sorted(path.name for path in installed.iterdir()) == REFERENCE_SCRIPTS
    for name in REFERENCE_SCRIPTS:
        copy = installed / name
        assert copy.read_bytes() == \
            (TEMPLATES / SCRIPTS_DIR / name).read_bytes()
        assert copy.stat().st_mode & stat.S_IXUSR
        assert os.access(copy, os.X_OK)


def test_a_freshly_initialized_target_gets_none_of_the_three_old_directories(
        initialized):
    """The three directories the merged file replaces are not created.

    A target that got them would carry empty directories nothing installs into
    and nothing reads, and a reader of a fresh target would have two places to
    look for one command.
    """
    for gone in ("sync", "query", "item"):
        assert not (initialized / ".harness" / gone).exists(), gone


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


def test_this_repository_carries_the_installed_tracker_script():
    """A shipped artifact, so this repository's own `.harness/` is the subject.

    The reference implementation is exercised by the repository that ships it,
    which is what stops the template being a file nobody ever runs — and the
    pair is what makes holding one half of it wrong.

    The installed copy is deliberately not byte-identical to its template — it
    carries the project this repository files against, which is exactly what a
    template must not carry — so what is asserted here is that it is present
    and runnable, and the comparison that it differs only in the constant
    assignments at the top is made below. What the byte comparison used to
    guarantee, that the file this repository runs is the file its suite
    exercises, is asserted behaviourally instead: the installed copy is driven
    end to end through the same stub tracker the template is.
    """
    for template in sorted((TEMPLATES / SCRIPTS_DIR).glob("*.sh")):
        installed = REPO_ROOT / ".harness" / SCRIPTS_DIR / template.name
        assert installed.is_file()
        assert os.access(installed, os.X_OK)


def test_this_repository_holds_none_of_the_three_old_directories():
    """The six files the merge removed, and the directories that held them, are
    gone from what this repository ships and from what it runs."""
    for gone in ("sync", "query", "item"):
        assert not (TEMPLATES / gone).exists(), gone
        assert not (REPO_ROOT / ".harness" / gone).exists(), gone


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


def reference_script(job: str) -> str:
    """The shipped script answering `job`, as a command line that will launch.

    A configured command is split into words before it is run, so the job's
    argument rides on the command line exactly as a target's configuration
    spells it.
    """
    return f"{INTERPRETER} {shlex.quote(str(TEMPLATE_SCRIPT))} {job}"

needs_jq = pytest.mark.skipif(
    JQ is None,
    reason="the reference scripts state jq as their dependency and it is "
           "absent here; the marker comparison holds the pairing claim without it")

#: What the stub tracker records, and where. An environment variable rather
#: than a path compiled into the stub, so one stub serves both scripts.
LEDGER_VARIABLE = "L5_STUB_LEDGER"

#: How a test tells the stub to break on purpose. Two variables rather than
#: one, because the two failures they cause are different claims: a call that
#: fails is a tracker the sync cannot write to, and a read that answers with no
#: such node is a board the sync cannot *read* — which the script must not
#: mistake for an item whose Status is empty.
#:
#: What the first names is a call rather than a project subcommand: the label
#: work happens above the board work and every one of its calls is made after
#: the issue exists, so it answers the same way and is failed the same way.
FAIL_VARIABLE = "L5_STUB_FAILS_AT"

#: What a test names in FAIL_VARIABLE to fail the two calls the label work
#: makes. The project subcommands are named there by their own names, which is
#: why these two carry the issue-side spelling rather than colliding with
#: `item-edit`.
LABEL_CREATE_CALL = "label-create"
LABEL_ADD_CALL = "issue-edit"

#: How the ledger names the read the sync makes of one item's own field values.
#: The project subcommands are recorded under their own names; this one is not
#: a project subcommand, so — like the two label calls above — it carries a
#: name of the stub's rather than one taken from an argument.
GRAPHQL_CALL = "api-graphql"

#: What a test names in FAIL_VARIABLE to fail a search. The first fails every
#: search the invocation makes; the second fails only a search carrying more
#: than one term, which is the batched form — so a stub told that one answers a
#: per-path search and refuses a batched one, which is exactly the tracker the
#: query branch's fallback exists for.
ISSUE_LIST_CALL = "issue-list"
BATCHED_SEARCH_CALL = "batched-search"

#: What makes that read answer with no such node while the item is in fact on
#: the board. That is what a tracker whose read has not caught up with an add
#: looks like, and it is the case the script must answer transiently rather
#: than read as a set of empty fields.
OMIT_VARIABLE = "L5_STUB_ITEM_READ_REPORTS_NOTHING"

STUB_GH = '''#!INTERPRETER
"""A stub `gh`, standing in for a tracker and its project board. It reaches no
network.

It implements exactly the invocations the reference scripts make and exits
non-zero on anything else, which is what keeps it a fake tracker rather than a
second implementation:

  issue create        appends to the ledger and prints a URL, carrying the
                      label it was created with.
  issue list --search matches the search text against each issue's body. The
                      search is read as quoted terms joined by OR, because the
                      query script batches several path markers into one
                      search, and an issue matches when its body contains any
                      of them; a search carrying no quotes is one term. --limit
                      is honoured, so a page filled to the limit -- which is
                      how the query script learns a batch may have been
                      truncated -- is something this stub can produce. Every
                      search is recorded in the ledger in the order it was
                      made, so how many searches a scope cost is readable
                      rather than inferred.
  issue view          prints one issue's body by the key the create printed --
                      the invocation the query script makes to answer a
                      brief-fetch question -- or its url where url is the one
                      field asked for, which is what the item-update script's
                      status branch needs to name the issue to a board.
  issue edit          given --body-file, replaces the issue's whole body with
                      that file, which is the one edit the item-update script
                      makes; given --add-label,
                      adds a label to an issue, beside the ones it carries. A
                      label the repository does not hold is refused, as gh
                      refuses it, which is what makes "created before it was
                      applied" something this stub can report rather than
                      something a test has to take on trust.
  label create        gives the repository a label. A create over a label that
                      already exists is refused unless --force is passed, again
                      as gh does it, so a filing that is idempotent here is
                      idempotent for the reason it claims to be.
  project item-add    adds the url to a project, or reports the item already
                      there rather than adding a second one, which is the
                      behaviour the sync script's retry depends on.
  api graphql         one item's own field values, looked up by the node id the
                      invocation carries. A field the item has no value for
                      contributes no node at all, which is how the real answer
                      reports one, and an empty node stands beside them for a
                      value of a type the query's fragment does not match — so
                      a script that read a missing field as an empty string and
                      a script that could not tell them apart are
                      distinguishable here. An item the read cannot resolve is
                      answered with a null node rather than with an empty list
                      of values, which is the distinction the sync script's
                      transient exit rests on.
  project view        the project's node id.
  project field-list  the project's fields and their options, by name.
  project item-edit   sets one single-select field on one item, by ids. The
                      value lands under the key gh names that field's column
                      after -- its name with the spaces removed and the case
                      lowered -- so a board carrying five fields is read back
                      field by field rather than as one column.

The ledger holds the issues, the repository's labels, the projects and every
board invocation that was made — the project subcommands under their own names
and the item read under GRAPHQL_CALL — so a test can assert on a call that was
*not* made as well as on one that was. A subcommand this stub does not
implement is recorded before it is refused, so an invocation the sync script
must no longer make is visible in the ledger rather than only in its exit
status. FAIL_VARIABLE names calls that must exit non-zero, and OMIT_VARIABLE
makes the item read answer with no such node.
"""
import json
import os
import re
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


def told_to_fail(call):
    return call in (os.environ.get("FAIL_VARIABLE", "") or "").split(",")


def board_key(name):
    """The key gh reports one field's value under, from the field's name."""
    return name.replace(" ", "").lower()


def search_terms(argv):
    """The terms one --search carries, as a search of quoted terms joined by OR.

    The query script batches several path markers into one search, so a search
    is one or more quoted runs with OR between them and it matches an issue
    whose body contains any of them. A search carrying no quotes at all is one
    term, which is what the fetch and the sync branch's own searches look like.
    """
    text = flag(argv, "--search", "") or ""
    quoted = re.findall(r'"([^"]*)"', text)
    if quoted:
        return [term for term in quoted if term]
    return [text] if text else []


def record_issue_call(command):
    """One call made against the issue itself, kept whatever it answers.

    Held apart from the project calls rather than beside them, so an assertion
    that no project call was made stays an assertion about the board.
    """
    state["issue_calls"].append({"command": command, "argv": argv})
    save()


if argv[:2] == ["issue", "create"]:
    number = len(issues) + 1
    issue = {
        "number": number,
        "title": flag(argv, "--title", ""),
        "body": flag(argv, "--body", ""),
        "labels": [flag(argv, "--label")] if "--label" in argv else [],
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
    # Which field was asked for. The body is what the query script and the
    # item-update script's document branch ask for, and stays the answer to a
    # view naming no field at all; the url is what the item-update script's
    # status branch asks for, because a board takes an issue by url rather
    # than by body.
    asked_for = (flag(argv, "--json", "") or "").split(",")
    print(found[0]["url"] if asked_for == ["url"] else found[0]["body"])
elif argv[:2] == ["issue", "edit"]:
    # The one edit the sync script makes: a label added beside the ones the
    # issue carries rather than replacing them.
    record_issue_call("LABEL_ADD_CALL")
    if told_to_fail("LABEL_ADD_CALL"):
        refuse("the stub was told to fail at issue edit")
    wanted = argv[2]
    found = [issue for issue in issues
             if wanted in (issue["url"], str(issue["number"]))]
    if not found:
        refuse("no issue is filed under %s" % wanted)
    replacement = flag(argv, "--body-file")
    if replacement is not None:
        # The one edit the item-update script makes: the whole body replaced
        # with the file it wrote. Held here beside the label edit rather than
        # in a stub of its own, because the third member of the family writes
        # to the same item the other two read, and a second stub could not
        # report that it left their markers alone.
        found[0]["body"] = open(replacement).read()
        save()
        print(found[0]["url"])
        sys.exit(0)
    added = flag(argv, "--add-label")
    if added is None:
        refuse("the stub was asked for something it does not do: %s"
               % " ".join(argv))
    if added not in state["labels"]:
        refuse("the repository has no label %s" % added)
    if added not in found[0]["labels"]:
        found[0]["labels"].append(added)
    save()
elif argv[:2] == ["label", "create"]:
    record_issue_call("LABEL_CREATE_CALL")
    if told_to_fail("LABEL_CREATE_CALL"):
        refuse("the stub was told to fail at label create")
    name = argv[2]
    if name in state["labels"] and "--force" not in argv:
        refuse("the label %s already exists" % name)
    if name not in state["labels"]:
        state["labels"].append(name)
    save()
elif argv[:2] == ["issue", "list"]:
    state["searches"].append(flag(argv, "--search", "") or "")
    save()
    if told_to_fail("ISSUE_LIST_CALL"):
        refuse("the stub was told to fail at issue list")
    if told_to_fail("BATCHED_SEARCH_CALL") and len(search_terms(argv)) > 1:
        refuse("the stub was told to fail at a batched search")
    fields = (flag(argv, "--json", "") or "").split(",")
    terms = search_terms(argv)
    matched = [issue for issue in issues
               if any(term in issue["body"] for term in terms)]
    limit = flag(argv, "--limit")
    if limit is not None:
        # gh returns at most --limit items, and a page filled to the limit is
        # how the query script learns its batch may have been truncated. A stub
        # that answered past the limit could not report that at all.
        matched = matched[:int(limit)]
    if "--jq" in argv:
        # The one program the sync script asks for: the first url, or nothing.
        print(matched[0]["url"] if matched else "")
    else:
        print(json.dumps([{name: issue.get(name) for name in fields}
                          for issue in matched]))
elif argv[:2] == ["api", "graphql"]:
    # One item's own field values, by the node id the invocation carries.
    # Recorded in the same ledger the project subcommands are, so "the read was
    # made once" and "the listing was not made at all" are both readable there.
    state["calls"].append({"command": "GRAPHQL_CALL", "argv": argv})
    save()
    if told_to_fail("GRAPHQL_CALL"):
        refuse("the stub was told to fail at api graphql")
    variables = dict(pair.split("=", 1)
                     for index, pair in enumerate(argv)
                     if index and argv[index - 1] == "-f" and "=" in pair)
    wanted = variables.get("item")
    holding = [project for project in projects.values()
               for one in project["items"] if one["id"] == wanted]
    if os.environ.get("OMIT_VARIABLE") or not holding:
        # No such node: a well formed answer that describes no item. It is
        # deliberately not an item carrying an empty list of values, because
        # those two are what the sync script must tell apart.
        print(json.dumps({"data": {"node": None}}))
    else:
        project = holding[0]
        item = [one for one in project["items"] if one["id"] == wanted][0]
        # An empty node stands for a field value of a type the query's inline
        # fragment does not match, which is what the real answer carries for
        # every value that is not a single select.
        nodes = [{}]
        for field in project["fields"]:
            held = item.get(board_key(field["name"]))
            if held:
                nodes.append({"name": held, "field": {"name": field["name"]}})
        print(json.dumps({"data": {"node": {"fieldValues": {"nodes": nodes}}}}))
elif argv[:1] == ["project"]:
    subcommand = argv[1]
    # Recorded before the refusal below, so a call a test told the stub to fail
    # is still a call the test can see was made.
    state["calls"].append({"command": subcommand, "argv": argv})
    save()
    if told_to_fail(subcommand):
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
        items[0][board_key(fields[0]["name"])] = options[0]["name"]
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
        else:
            refuse("the stub was asked for something it does not do: %s"
                   % " ".join(argv))
else:
    refuse("the stub was asked for something it does not do: %s"
           % " ".join(argv))
'''


def classification_fields() -> list[dict]:
    """The five fields a brief's classification is written into, as the board
    holds them: each single-select, each offering every value the schema or the
    harness allows.

    Built from `CLASSIFICATION` rather than written out, so a category added to
    the brief schema is an option on this board without this module being
    edited — which is the same property the sync script has and the reason it
    enumerates nothing itself.
    """
    return [
        {
            "id": f"PVTSSF_{axis.payload_field}",
            "name": axis.field_name,
            "type": "SINGLE_SELECT",
            "options": [{"id": f"opt-{axis.payload_field}-{value}",
                         "name": value}
                        for value in axis.values],
        }
        for axis in CLASSIFICATION
    ]


def seeded_board() -> dict:
    """The board the stub starts with: this target's project, a Status field
    with the options a project of this kind has, and the five classification
    fields beside it.

    A Title field sits beside the Status field so that resolving the Status
    field's id by name is a resolution rather than a choice of the only field
    there is, and two options sit beside the one this target declares so that
    resolving the option by name is the same.
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
            ] + classification_fields(),
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
                        .replace("OMIT_VARIABLE", OMIT_VARIABLE)
                        .replace("GRAPHQL_CALL", GRAPHQL_CALL)
                        .replace("LABEL_CREATE_CALL", LABEL_CREATE_CALL)
                        .replace("LABEL_ADD_CALL", LABEL_ADD_CALL)
                        .replace("BATCHED_SEARCH_CALL", BATCHED_SEARCH_CALL)
                        .replace("ISSUE_LIST_CALL", ISSUE_LIST_CALL))
    ledger.write_text(json.dumps(
        {"issues": [], "labels": [], "projects": seeded_board(),
         "calls": [], "issue_calls": [],
         # One entry per `issue list --search`, in the order the searches were
         # made, so how many searches a scope cost is readable rather than
         # inferred from how long the script took.
         "searches": []}),
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
        [INTERPRETER, str(script), SYNC_JOB],
        input=json.dumps(entry), capture_output=True, text=True, timeout=60,
        cwd=tmp_path,
        env={**environment, **(extra or {}),
             command_transport.KEY_ENVIRONMENT_VARIABLE: key})


def file_through_the_reference_sync(tmp_path: Path, environment: dict, *,
                                    key: str, payload: dict,
                                    script: Path | None = None,
                                    extra: dict | None = None) -> str:
    """One entry filed by a sync script, and the reference it named."""
    result = run_the_sync(script or TEMPLATE_SCRIPT, tmp_path, environment,
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
    """Every board invocation the stub was made, optionally by name.

    The project subcommands are recorded under their own names and the read of
    one item's field values under `GRAPHQL_CALL`, so an assertion that a
    particular call was *not* made is made of the same ledger either way.
    """
    return [call for call in ledger_state(ledger)["calls"]
            if command is None or call["command"] == command]


def issue_calls(ledger: Path, command: str | None = None) -> list[dict]:
    """Every call made against the issue itself, optionally by name."""
    return [call for call in ledger_state(ledger)["issue_calls"]
            if command is None or call["command"] == command]


def issue_labels(ledger: Path, index: int = 0) -> list[str]:
    """The labels one filed issue carries."""
    return ledger_state(ledger)["issues"][index]["labels"]


def repository_labels(ledger: Path) -> list[str]:
    """The labels the stub's repository holds, whoever made them."""
    return ledger_state(ledger)["labels"]


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
        TEMPLATE_SCRIPT.read_text(encoding="utf-8"))
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
        TEMPLATE_SCRIPT.read_text(encoding="utf-8"))
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
        answer = asked(reference_script(QUERY_JOB), tmp_path)
        unrelated = asked(reference_script(QUERY_JOB), tmp_path,
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
        answer = asked(reference_script(QUERY_JOB), tmp_path)
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
    pytest.param(INSTALLED_SCRIPT, id="installed"),
    pytest.param(TEMPLATE_SCRIPT, id="template"),
]

#: Every project subcommand the shipped script invokes, read off the script
#: rather than listed here: the claim below is about *every* call made after
#: the issue exists, and a call added to the script without being added to this
#: list would be a claim quietly narrowed.
PROJECT_SUBCOMMANDS = sorted(set(re.findall(
    r"gh project ([a-z-]+)", TEMPLATE_SCRIPT.read_text(encoding="utf-8"))))

#: How the script asks for one item's own field values, read off the script for
#: the same reason. The read is not a project subcommand, so it is not among the
#: names above and would otherwise drop out of the sweep of every call made
#: after the issue exists.
GRAPHQL_INVOCATIONS = re.findall(
    r"gh api graphql", TEMPLATE_SCRIPT.read_text(encoding="utf-8"))

#: Every board call the script makes, under the names the ledger records them
#: by. The project subcommands answer to their own names and the item read
#: answers to the stub's.
BOARD_CALLS = tuple(PROJECT_SUBCOMMANDS) + (GRAPHQL_CALL,)

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
    carries no project, no column and no field name by design, so it is handed
    exactly the values the installed copy sets in its own text.
    """
    if script == INSTALLED_SCRIPT:
        return {}
    return {
        TEMPLATE_CONSTANTS[PROJECT_CONSTANT][0]: THIS_TARGETS_PROJECT,
        TEMPLATE_CONSTANTS[STATUS_OPTION_CONSTANT][0]: THIS_TARGETS_STATUS_OPTION,
        **{TEMPLATE_CONSTANTS[axis.constant][0]: axis.field_name
           for axis in CLASSIFICATION},
    }


#: Every constant `board_environment_for` overrides, so the assertion that they
#: are declared is made of the names the overriding uses rather than of a
#: second list beside it.
OVERRIDDEN_CONSTANTS = (PROJECT_CONSTANT, STATUS_OPTION_CONSTANT) + tuple(
    axis.constant for axis in CLASSIFICATION)


def sync_to_the_board(script: Path, tmp_path: Path, environment: dict, *,
                      key: str, payload: dict | None = None,
                      breaking: dict | None = None):
    """One invocation of `script` against the stub's board.

    `breaking` is whatever this invocation alone is driven with beside the
    board values — most often what the stub is to be broken with, so a test can
    drive the same key twice with the board failing the first time and
    answering the second, and sometimes a board value overridden for one
    invocation.
    """
    return run_the_sync(
        script, tmp_path, environment, key=key, payload=payload or AN_ENTRY,
        extra={**board_environment_for(script), **(breaking or {})})


def test_the_template_declares_the_constants_the_board_tests_override():
    """What `board_environment_for` rests on, asserted rather than assumed.

    A shipped artifact and the subject: the template's whole design is that its
    board values are set from outside it, so the constants it is handed must
    exist and must be spelled with the prefix the stripping in `stub_tracker`
    uses. A rename would otherwise leave the template driven with no project at
    all, and every board assertion about it passing on a board it never
    touched.
    """
    assert set(TEMPLATE_CONSTANTS) >= set(OVERRIDDEN_CONSTANTS), \
        sorted(TEMPLATE_CONSTANTS)
    assert SYNC_VARIABLE_PREFIX.startswith("L5_")
    for name in OVERRIDDEN_CONSTANTS:
        assert TEMPLATE_CONSTANTS[name][0].startswith(SYNC_VARIABLE_PREFIX), name
    assert PROJECT_SUBCOMMANDS, "the script invokes no project subcommand"
    assert GRAPHQL_INVOCATIONS, \
        "the script asks for no item's field values, so the sweep over the " \
        "calls it makes after the issue exists would not cover that read"


def test_every_axis_the_classification_is_written_over_carries_values():
    """The sweep below drives what these tuples hold, so an axis that resolved
    to nothing would be a sweep that asserts nothing and passes.

    The brief schema's enums and the harness's workflow listing are live
    artifacts and are the subject here: what is asserted is that each axis
    resolved to more than one value, so that a filing writing one of them is a
    choice rather than the only thing there was, and that each axis names a
    field the schema requires a brief to carry.
    """
    for axis in CLASSIFICATION:
        assert len(axis.values) >= 2, axis
        assert all(isinstance(value, str) and value for value in axis.values), \
            axis
    assert {axis.payload_field for axis in CLASSIFICATION} <= \
        set(BRIEF_SHAPE["required"])


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
@pytest.mark.parametrize("subcommand", BOARD_CALLS)
def test_every_failure_after_the_issue_exists_is_transient(
        subcommand, script, tmp_path):
    """The issue is the record and the board is a view of it.

    Each board call the script makes is failed in turn — the project
    subcommands and the read of the item's own field values alike — and each
    must exit 75 rather than 0 or 1: a zero would report an entry as landed
    with the board call lost, and a non-zero that is not 75 would fail the
    entry terminally and lose it. The issue is filed either way, which is what
    makes the retry the next sweep performs find it rather than create a second
    one.
    """
    environment, ledger = stub_tracker(tmp_path)
    result = sync_to_the_board(script, tmp_path, environment, key="k-fails",
                               breaking={FAIL_VARIABLE: subcommand})

    assert result.returncode == TRANSIENT_EXIT, (result.returncode, result.stderr)
    assert len(ledger_state(ledger)["issues"]) == 1
    assert project_calls(ledger, subcommand), \
        f"the script never made the {subcommand} call"


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


#: The two ways the item's own field values can fail to be obtained, as the
#: stub is told to produce them: a read that answers with no such node while
#: the item is in fact there, and a read that fails outright. Both are a
#: failure to know rather than a set of empty fields, and the script must
#: answer both the same way.
WAYS_OF_NOT_KNOWING = [
    pytest.param({OMIT_VARIABLE: "1"}, id="no-such-node"),
    pytest.param({FAIL_VARIABLE: GRAPHQL_CALL}, id="read-failed"),
]


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
@pytest.mark.parametrize("breaking", WAYS_OF_NOT_KNOWING)
def test_an_item_whose_field_values_were_not_obtained_is_a_failure_to_know(
        breaking, script, tmp_path):
    """A read that did not describe the item is not an empty Status.

    The item is on the board and the read either fails or answers with no such
    node — which is what a tracker whose read has not caught up with the add
    looks like. Read as an empty Status the item would be written into; read as
    a failure to know it is answered transiently and left exactly as it was.

    The control is the same drive with the read answering, below the
    assertions: there the `item-edit` is made and the Status is written, so the
    absence here is the guard and not a write that never happens.
    """
    environment, ledger = stub_tracker(tmp_path)
    brief = a_filed_brief()
    blind = sync_to_the_board(script, tmp_path, environment, key="k-unread",
                              payload=brief, breaking=breaking)

    assert blind.returncode == TRANSIENT_EXIT, (blind.returncode, blind.stderr)
    items = board_items(ledger)
    assert len(items) == 1, items
    assert "status" not in items[0], items[0]
    for axis in CLASSIFICATION:
        assert board_field_value(ledger, axis.field_name) == "", axis.field_name
    assert project_calls(ledger, "item-edit") == []

    seeing = sync_to_the_board(script, tmp_path, environment, key="k-unread",
                               payload=brief)
    assert seeing.returncode == 0, seeing.stderr
    assert board_items(ledger)[0]["status"] == THIS_TARGETS_STATUS_OPTION
    for axis in CLASSIFICATION:
        assert board_field_value(ledger, axis.field_name) == \
            str(brief[axis.payload_field]), axis.field_name
    assert len(project_calls(ledger, "item-edit")) == 1 + len(CLASSIFICATION)


def digits_beside(message: str, *names: str) -> str:
    """Every digit in `message` that is not part of one of `names`.

    The transient message is required to name the item and the project, both of
    which are spelled with digits on this board, so "and no count of items" is
    asserted of what is left once those two are taken out.
    """
    for name in sorted(names, key=len, reverse=True):
        message = message.replace(name, " ")
    return "".join(character for character in message if character.isdigit())


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
@pytest.mark.parametrize("breaking", WAYS_OF_NOT_KNOWING)
def test_the_transient_answer_names_the_item_and_the_project_and_no_count(
        breaking, script, tmp_path):
    """A developer reading that line is not told about a bound.

    The message the script used to give named the size of a listing, which was
    the wrong cause on every board smaller than it — so what is asserted is
    that the item and the project are named and that no other number is.

    The absence of a number is controlled beside itself: the same reduction
    over the message this one replaced, constructed here rather than read out
    of the tree, does report a number.
    """
    environment, ledger = stub_tracker(tmp_path)
    blind = sync_to_the_board(script, tmp_path, environment, key="k-unread",
                              breaking=breaking)
    assert blind.returncode == TRANSIENT_EXIT, blind.stderr

    item_id = board_items(ledger)[0]["id"]
    said = blind.stderr.strip()
    assert item_id in said, said
    assert THIS_TARGETS_PROJECT in said, said
    assert digits_beside(said, item_id, THIS_TARGETS_PROJECT) == "", said

    superseded = (f"item {item_id} was not in the first 5000 items of project "
                  f"{THIS_TARGETS_PROJECT}, so its fields are unknown")
    assert digits_beside(superseded, item_id, THIS_TARGETS_PROJECT) != "", \
        "the reduction reports no number in a message that names a bound"


@needs_jq
def test_the_installed_copy_files_to_this_targets_board_with_nothing_set(
        tmp_path):
    """This deployment's own wiring, rather than the test's environment.

    None of the variables the script reads its board values out of is in the
    environment this runs under — asserted against the names the script itself
    declares, not assumed — so the project and the column the item lands in can
    only have come out of `.harness/scripts/github.sh` itself.
    """
    environment, ledger = stub_tracker(tmp_path)
    declared = {variable for variable, _ in TEMPLATE_CONSTANTS.values()}
    assert declared, "the script declares no editable constant"
    assert [name for name in environment if name in declared] == []

    result = run_the_sync(INSTALLED_SCRIPT, tmp_path, environment,
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
    result = run_the_sync(TEMPLATE_SCRIPT, tmp_path, environment,
                          key="k-no-project", payload=AN_ENTRY)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1].startswith(
        "https://tracker.invalid/")
    assert len(ledger_state(ledger)["issues"]) == 1
    assert project_calls(ledger) == []
    assert board_items(ledger) == []

    configured = sync_to_the_board(TEMPLATE_SCRIPT, tmp_path, environment,
                                   key="k-with-a-project")
    assert configured.returncode == 0, configured.stderr
    assert len(board_items(ledger)) == 1


# --------------------------------------------------------------------------
# The classification a filed brief carries: one label and five board fields
#
# The same two copies are driven, for the same reason. What is written is the
# payload's own classification, and the values driven are resolved from the
# brief schema and from the harness's workflow listing rather than written
# here, so a category added to the schema is a case in the sweep below without
# this module being edited.
# --------------------------------------------------------------------------


def a_brief_carrying(**overrides) -> dict:
    """The brief above with one part of its classification replaced."""
    return {**a_filed_brief(), **overrides}


def label_constants(script: Path) -> tuple[str, str]:
    """The label every entry gets and the prefix a category's label carries.

    Read off whichever copy is being driven rather than written here, so the
    installed copy is held to its own values and the template to its own — and
    a copy that overrode either is driven against what it overrode it to.
    """
    constants = sync_constants(script.read_text(encoding="utf-8"))
    return constants["LABEL"][1], constants["CATEGORY_LABEL_PREFIX"][1]


def stub_project(state: dict) -> dict:
    return state["projects"][
        f"{THIS_TARGETS_PROJECT_OWNER}/{THIS_TARGETS_PROJECT}"]


def board_field_value(ledger: Path, field_name: str) -> str:
    """What the board holds for one field of the item it carries.

    Keyed the way gh keys an item's columns — the field's name with its spaces
    removed and its case lowered — which is the same derivation the stub makes
    and the sync script reads back through.
    """
    item = board_items(ledger)[0]
    return item.get(field_name.replace(" ", "").lower(), "")


def rewrite_the_board(ledger: Path, change) -> None:
    """Edit the stub's project in place, as a board somebody else changed."""
    state = ledger_state(ledger)
    change(stub_project(state))
    ledger.write_text(json.dumps(state), encoding="utf-8")


def board_without_the_field(ledger: Path, field_name: str) -> None:
    """A board that never had one of the fields this target names."""
    def drop(project):
        project["fields"] = [field for field in project["fields"]
                             if field["name"] != field_name]
    rewrite_the_board(ledger, drop)


def board_without_the_option(ledger: Path, field_name: str,
                             option_name: str) -> None:
    """A board whose field does not offer the value the payload carries."""
    def drop(project):
        for field in project["fields"]:
            if field["name"] == field_name:
                field["options"] = [option for option in field["options"]
                                    if option["name"] != option_name]
    rewrite_the_board(ledger, drop)


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_filed_brief_carries_its_category_as_a_second_label(script, tmp_path):
    """The label a developer scanning the tracker reads the category off.

    Beside the label every entry has always carried rather than in place of it,
    and made of the configured prefix and the payload's own category — neither
    of which this test writes: both are read off the copy being driven.
    """
    environment, ledger = stub_tracker(tmp_path)
    brief = a_filed_brief()
    every_entry, prefix = label_constants(script)

    result = sync_to_the_board(script, tmp_path, environment, key="k-labelled",
                               payload=brief)
    assert result.returncode == 0, result.stderr
    assert issue_labels(ledger) == [every_entry,
                                    f"{prefix}{brief['category']}"]


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_category_whose_label_the_repository_lacks_is_created_then_applied(
        script, tmp_path):
    """The label is made by the script rather than by hand.

    The stub's repository starts with no labels at all and refuses to add one
    it does not hold, exactly as gh refuses it — so a filing that lands with
    the label on the issue can only have created it first. The order is read
    off the calls as well, so "created then applied" is a sequence rather than
    an inference from the outcome.

    The control is below, in `test_the_same_filing_fails_when_the_create_is
    _taken_out`: the same script with the create removed is refused by the same
    stub, so the create here is what the filing rests on.
    """
    environment, ledger = stub_tracker(tmp_path)
    assert repository_labels(ledger) == []
    brief = a_filed_brief()
    _, prefix = label_constants(script)
    expected = f"{prefix}{brief['category']}"

    result = sync_to_the_board(script, tmp_path, environment, key="k-new-label",
                               payload=brief)
    assert result.returncode == 0, result.stderr
    assert repository_labels(ledger) == [expected]
    assert expected in issue_labels(ledger)
    assert [call["command"] for call in issue_calls(ledger)] == \
        [LABEL_CREATE_CALL, LABEL_ADD_CALL]


@needs_jq
def test_the_same_filing_fails_when_the_create_is_taken_out(tmp_path):
    """The control for the create above, on a rendering rather than the tree.

    The shipped template with its label create removed is a script that applies
    a label nothing made, and the stub refuses it — which is what makes the
    create in the shipped script the thing that carries the filing rather than
    a call that happens to be there.
    """
    environment, ledger = stub_tracker(tmp_path)
    shipped = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    stripped = without_the_label_create(shipped)
    assert stripped != shipped
    assert "gh label create" not in stripped

    rendered = fixture_file(tmp_path / "no-create", "github.sh", stripped)
    result = sync_to_the_board(rendered, tmp_path, environment,
                               key="k-no-create", payload=a_filed_brief())

    assert result.returncode == TRANSIENT_EXIT, (result.returncode,
                                                 result.stderr)
    assert repository_labels(ledger) == []
    assert len(ledger_state(ledger)["issues"]) == 1


def without_the_label_create(text: str) -> str:
    """The same script with the whole call that creates the label removed.

    Removed as a command rather than as a line, because the invocation is
    continued across several of them, so what is left is a script that reaches
    the add with nothing having been created.
    """
    kept, dropping = [], False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("gh label create"):
            dropping = True
        if dropping:
            if not line.rstrip("\n").endswith("\\"):
                dropping = False
            continue
        kept.append(line)
    return "".join(kept)


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_an_entry_carrying_no_category_adds_no_label_and_creates_none(
        script, tmp_path):
    """A non-brief entry files exactly as it did before this story.

    The control is in the same drive: the same script and the same stub over a
    payload that *is* a brief does create a label and does add one, so the
    absence here is the guard rather than label work that never happens.
    """
    environment, ledger = stub_tracker(tmp_path)
    every_entry, _ = label_constants(script)

    plain = sync_to_the_board(script, tmp_path, environment, key="k-no-category")
    assert plain.returncode == 0, plain.stderr
    assert issue_labels(ledger) == [every_entry]
    assert repository_labels(ledger) == []
    assert issue_calls(ledger) == []

    brief = sync_to_the_board(script, tmp_path, environment, key="k-a-brief",
                              payload=a_filed_brief())
    assert brief.returncode == 0, brief.stderr
    assert len(issue_labels(ledger, 1)) == 2, issue_labels(ledger, 1)
    assert repository_labels(ledger) != []


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_second_brief_of_the_same_category_files_over_the_label_it_made(
        script, tmp_path):
    """The create is idempotent, which is what makes it safe on every filing.

    The stub refuses a create over a label that already exists unless the
    create says it means to update it, as gh does — so a second brief of one
    category landing here is the create being genuinely idempotent rather than
    the case never arising.
    """
    environment, ledger = stub_tracker(tmp_path)
    brief = a_filed_brief()

    first = sync_to_the_board(script, tmp_path, environment, key="k-one",
                              payload=brief)
    assert first.returncode == 0, first.stderr
    second = sync_to_the_board(script, tmp_path, environment, key="k-two",
                               payload=brief)
    assert second.returncode == 0, second.stderr

    _, prefix = label_constants(script)
    assert repository_labels(ledger) == [f"{prefix}{brief['category']}"]
    assert issue_labels(ledger, 0) == issue_labels(ledger, 1)


@needs_jq
@pytest.mark.parametrize("call", [LABEL_CREATE_CALL, LABEL_ADD_CALL])
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_label_call_that_fails_after_the_issue_exists_is_transient(
        call, script, tmp_path):
    """The label work sits below the issue's creation, so it answers as the
    board work does: 75, the issue filed, the entry pending for a later sweep.

    A zero would report an entry as landed with its label lost, and a non-zero
    that is not 75 would fail the entry terminally and lose the entry.
    """
    environment, ledger = stub_tracker(tmp_path)
    result = sync_to_the_board(script, tmp_path, environment, key="k-label-fails",
                               payload=a_filed_brief(),
                               breaking={FAIL_VARIABLE: call})

    assert result.returncode == TRANSIENT_EXIT, (result.returncode,
                                                 result.stderr)
    assert len(ledger_state(ledger)["issues"]) == 1
    assert issue_calls(ledger, call), f"the script never made the {call} call"


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_filed_brief_carries_its_classification_on_the_board(
        script, tmp_path):
    """The five fields, written from the five values the payload carries.

    The severity is asserted where it is written rather than separately: the
    payload carries it as an integer — asserted here, so the case is the one it
    claims to be — and the board carries it as the option named after it, so a
    brief of severity 2 lands on the option named 2.
    """
    environment, ledger = stub_tracker(tmp_path)
    brief = a_filed_brief()
    assert isinstance(brief["severity"], int), brief["severity"]

    result = sync_to_the_board(script, tmp_path, environment, key="k-classified",
                               payload=brief)
    assert result.returncode == 0, result.stderr

    for axis in CLASSIFICATION:
        assert board_field_value(ledger, axis.field_name) == \
            str(brief[axis.payload_field]), axis.field_name
    assert board_items(ledger)[0]["status"] == THIS_TARGETS_STATUS_OPTION

    # One edit per field written, the Status among them, and nothing repeated.
    assert len(project_calls(ledger, "item-edit")) == 1 + len(CLASSIFICATION)


@needs_jq
@pytest.mark.parametrize("axis,value", [
    pytest.param(axis, value, id=f"{axis.payload_field}-{value}")
    for axis in CLASSIFICATION for value in axis.values])
def test_every_value_the_schema_and_the_harness_allow_reaches_the_board(
        axis, value, tmp_path):
    """The sweep, over values this module does not name.

    Each case is one axis of the classification driven at one of its values,
    with the rest of the brief left as it is — so what the board carries can
    only have come from the payload. The cases come from
    `schemas/story-brief.schema.json`'s enums and from the harness's own
    workflow listing, which is what makes a category added to the schema a case
    here without this module being edited.

    Driven through the installed copy, which is the one this deployment files
    with; that the template behaves identically is what the parametrization
    over both copies above holds.
    """
    environment, ledger = stub_tracker(tmp_path)
    brief = a_brief_carrying(**{axis.payload_field: value})

    result = sync_to_the_board(INSTALLED_SCRIPT, tmp_path, environment,
                               key=f"k-{axis.payload_field}-{value}",
                               payload=brief)
    assert result.returncode == 0, result.stderr
    assert board_field_value(ledger, axis.field_name) == value

    if axis.payload_field == "category":
        _, prefix = label_constants(INSTALLED_SCRIPT)
        assert f"{prefix}{value}" in issue_labels(ledger)


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_field_the_project_does_not_have_costs_that_field_alone(
        script, tmp_path):
    """A board without a column is not a filing that failed.

    gh cannot create a project field, so a field the board does not have is
    said on stderr and skipped: the issue is created, the item is on the board,
    every other field is written and the entry lands. The control is the
    assertion above, where the same drive against a board that *has* the field
    writes it.
    """
    environment, ledger = stub_tracker(tmp_path)
    missing = CLASSIFICATION[1]
    board_without_the_field(ledger, missing.field_name)
    brief = a_filed_brief()

    result = sync_to_the_board(script, tmp_path, environment, key="k-no-field",
                               payload=brief)

    assert result.returncode == 0, result.stderr
    assert missing.field_name in result.stderr
    assert board_field_value(ledger, missing.field_name) == ""
    assert len(board_items(ledger)) == 1
    assert board_items(ledger)[0]["status"] == THIS_TARGETS_STATUS_OPTION
    for axis in CLASSIFICATION:
        if axis is not missing:
            assert board_field_value(ledger, axis.field_name) == \
                str(brief[axis.payload_field]), axis.field_name


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_field_whose_options_lack_the_value_costs_that_field_alone(
        script, tmp_path):
    """A column that does not offer the value is the same answer as no column.

    The field is there and the option is not, which is what a board configured
    before a category was added to the schema looks like. It is said on stderr
    and skipped; the entry lands with everything else written.
    """
    environment, ledger = stub_tracker(tmp_path)
    brief = a_filed_brief()
    narrowed = CLASSIFICATION[2]
    board_without_the_option(ledger, narrowed.field_name,
                             str(brief[narrowed.payload_field]))

    result = sync_to_the_board(script, tmp_path, environment, key="k-no-option",
                               payload=brief)

    assert result.returncode == 0, result.stderr
    assert narrowed.field_name in result.stderr
    assert str(brief[narrowed.payload_field]) in result.stderr
    assert board_field_value(ledger, narrowed.field_name) == ""
    for axis in CLASSIFICATION:
        if axis is not narrowed:
            assert board_field_value(ledger, axis.field_name) == \
                str(brief[axis.payload_field]), axis.field_name


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_the_project_the_fields_and_the_item_are_read_once_per_filing(
        script, tmp_path):
    """Six writes rather than six reads each.

    Each of the three reads is asserted to have been made exactly once: an
    upper bound alone would pass a script that made none of them, and this
    filing needs all three, so the equality carries both halves. The third is
    the read of this item's own field values, which every one of the six writes
    consults and which none of them may repeat.

    The item-edit count beside them is what makes "six writes" the premise
    rather than an assumption.
    """
    environment, ledger = stub_tracker(tmp_path)
    result = sync_to_the_board(script, tmp_path, environment, key="k-read-once",
                               payload=a_filed_brief())
    assert result.returncode == 0, result.stderr

    assert len(project_calls(ledger, "item-edit")) == 1 + len(CLASSIFICATION)
    for subcommand in ("view", "field-list", GRAPHQL_CALL):
        assert len(project_calls(ledger, subcommand)) == 1, \
            (subcommand, project_calls(ledger, subcommand))

    read = project_calls(ledger, GRAPHQL_CALL)[0]
    assert board_items(ledger)[0]["id"] in " ".join(read["argv"]), read


#: A field name with a space in it. The board this module seeds has none, and
#: the two spellings of such a name — as the board declares it and as the
#: listing used to key it — are what the lookup has to reconcile, so a board
#: carrying one is built here rather than assumed absent.
A_FIELD_NAME_CARRYING_A_SPACE = "Some Category"


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_field_whose_name_carries_a_space_is_written_once_and_then_left(
        script, tmp_path):
    """A board naming a field with a space resolves as one naming it without.

    The read keys a value by the field's name as the board declares it, and the
    listing it replaces keyed the same value by that name with the spaces taken
    out — so a lookup tolerant of only one of those spellings writes such a
    field on every sweep for ever, moving a value a person set.

    Both halves are driven: the first filing finds the field empty and writes
    it, and the second over the same entry reads back what it wrote and makes
    no edit at all. The write is what controls the absence.
    """
    environment, ledger = stub_tracker(tmp_path)
    axis = CLASSIFICATION[0]
    brief = a_filed_brief()

    def rename(project):
        for field in project["fields"]:
            if field["name"] == axis.field_name:
                field["name"] = A_FIELD_NAME_CARRYING_A_SPACE
    rewrite_the_board(ledger, rename)
    spaced = {TEMPLATE_CONSTANTS[axis.constant][0]:
              A_FIELD_NAME_CARRYING_A_SPACE}

    first = sync_to_the_board(script, tmp_path, environment, key="k-spaced",
                              payload=brief, breaking=spaced)
    assert first.returncode == 0, first.stderr
    written = board_field_value(ledger, A_FIELD_NAME_CARRYING_A_SPACE)
    assert written == str(brief[axis.payload_field]), written

    state = ledger_state(ledger)
    state["calls"] = []
    ledger.write_text(json.dumps(state), encoding="utf-8")

    again = sync_to_the_board(script, tmp_path, environment, key="k-spaced",
                              payload=brief, breaking=spaced)
    assert again.returncode == 0, again.stderr
    assert board_field_value(ledger, A_FIELD_NAME_CARRYING_A_SPACE) == written
    assert project_calls(ledger, "item-edit") == [], \
        "a field the board already reports was written over"


def in_a_case_the_board_does_not_use(name: str) -> str:
    """The same name, spelled in a case the board does not spell it in.

    Derived from whatever the board calls the field rather than written out, so
    a field renamed on the seeded board is still driven at a name differing
    from it in case alone. Both halves of that are asserted here: a name that
    came back identical would make the filings below the ordinary filings every
    test above already makes, and a name that differed in anything but case
    would be asking a wider question than the one these tests ask.
    """
    swapped = name.swapcase()
    assert swapped != name, name
    assert swapped.lower() == name.lower(), (swapped, name)
    return swapped


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_status_field_configured_in_another_case_is_resolved_and_written(
        script, tmp_path):
    """A target that configures `status` against a board titled `Status` files.

    The read of the item's current value has always ignored case, so the field
    read as empty; the lookup that resolves the id to write with matched the
    board verbatim, so the write resolved nothing and the filing exited
    transiently — leaving the entry pending and retried for ever without ever
    landing. One rule shared by both lookups is what makes the name the read
    tolerates a name the write resolves.

    The board keeps the field names it is seeded with, and only the configured
    name is spelled differently, so what resolves the field can only be the
    comparison and not a board rewritten to suit it.
    """
    environment, ledger = stub_tracker(tmp_path)
    named = {TEMPLATE_CONSTANTS[STATUS_FIELD_CONSTANT][0]:
             in_a_case_the_board_does_not_use(THIS_TARGETS_STATUS_FIELD)}

    result = sync_to_the_board(script, tmp_path, environment,
                               key="k-status-in-another-case",
                               payload=a_filed_brief(), breaking=named)

    assert result.returncode == 0, result.stderr
    assert len(board_items(ledger)) == 1
    assert board_items(ledger)[0]["status"] == THIS_TARGETS_STATUS_OPTION


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_classification_field_configured_in_another_case_reaches_the_board(
        script, tmp_path):
    """The same disagreement on a classification field, where it was quieter.

    A classification field whose id resolves to nothing is said on stderr and
    skipped, so the filing exits 0 and the value is simply written nowhere —
    the failure a developer reads as a board that is merely missing a column's
    value. Driven at the same axis the rest of the brief is driven at, so the
    value on the board can only have come from the payload.
    """
    environment, ledger = stub_tracker(tmp_path)
    axis = CLASSIFICATION[0]
    brief = a_filed_brief()
    named = {TEMPLATE_CONSTANTS[axis.constant][0]:
             in_a_case_the_board_does_not_use(axis.field_name)}

    result = sync_to_the_board(script, tmp_path, environment,
                               key="k-classification-in-another-case",
                               payload=brief, breaking=named)

    assert result.returncode == 0, result.stderr
    assert board_field_value(ledger, axis.field_name) == \
        str(brief[axis.payload_field]), result.stderr
    assert board_items(ledger)[0]["status"] == THIS_TARGETS_STATUS_OPTION


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_status_option_configured_in_another_case_still_resolves_to_nothing(
        script, tmp_path):
    """What the field-name rule widened, the option-name rule did not.

    An option is the value a person reads off the board, and the board's own
    spelling of it is the one that goes there — so an option value that does
    not match the board verbatim still resolves to nothing, and for the Status
    field that still costs the filing a transient exit with the entry left
    pending. The field name here is configured exactly as the board spells it,
    so the only thing differing is the option.

    That the item's Status is left unwritten is controlled by the ordinary
    filing above, where the same drive with the option spelled as the board
    spells it writes it.
    """
    environment, ledger = stub_tracker(tmp_path)
    configured = in_a_case_the_board_does_not_use(THIS_TARGETS_STATUS_OPTION)

    result = sync_to_the_board(script, tmp_path, environment,
                               key="k-option-in-another-case",
                               breaking={TEMPLATE_CONSTANTS[STATUS_OPTION_CONSTANT][0]:
                                         configured})

    assert result.returncode == TRANSIENT_EXIT, result.stderr
    assert configured in result.stderr
    assert board_items(ledger)[0].get("status", "") == ""


#: The three lookups that match a configured field name against the board's,
#: by the names the sync scripts give them: the read of this item's current
#: value, the resolution of a field's id, and the resolution of an option's id.
#: Written here rather than derived from the script, because the claim is about
#: these three in particular — a list read off the script would grow with a
#: fourth lookup and go on passing whatever that fourth one did.
FIELD_NAME_LOOKUPS = ("board_value", "field_id_for", "option_id_for")

#: How a jq program spells the comparison this story gave one home: a name
#: matched with its spaces removed and its case ignored. Anywhere but inside
#: the shared definition, one of these is a lookup carrying a rule of its own.
NORMALIZING_IDIOM = re.compile(r'gsub\(" "; ""\)|ascii_downcase')

#: The shared definition itself: a jq function of two names, up to the `;` that
#: closes it. Matched by its shape rather than by its name, so what is asserted
#: is that the script states the rule once and defers to it — not that it
#: chose a particular name for it.
SHARED_FIELD_NAME_RULE = re.compile(
    r"""def (?P<name>[a-z_]+)\(\$[a-z]+; *\$[a-z]+\):(?P<rule>.*?);(?=['"]|[ \t]*$)""",
    re.DOTALL | re.MULTILINE)


def sync_function_body(text: str, name: str) -> str:
    """One shell function's body, as the sync scripts lay them out."""
    found = re.search(
        r"^[ \t]*%s\(\) \{\n(?P<body>.*?)^[ \t]*\}$" % re.escape(name),
        text, re.MULTILINE | re.DOTALL)
    assert found, f"the script declares no {name}"
    return found.group("body")


def rules_of_sameness_outside_the_shared_one(text: str) -> list[str]:
    """Every line stating what makes two field names the same, other than the
    one statement of it the script is supposed to hold.

    The shared definition is cut out of the text and what is scanned is what is
    left, so a lookup that spelled the normalization inline is a line reported
    here whether or not it also calls the shared one.
    """
    stated = SHARED_FIELD_NAME_RULE.search(text)
    assert stated, "the script states no shared field-name comparison at all"
    assert NORMALIZING_IDIOM.search(stated.group("rule")), stated.group("rule")
    elsewhere = text[:stated.start()] + text[stated.end():]
    return [line for line in elsewhere.splitlines()
            if NORMALIZING_IDIOM.search(line)]


def lookups_not_deferring_to_the_shared_rule(text: str) -> list[str]:
    """Every field-name lookup whose body does not call the shared rule."""
    stated = SHARED_FIELD_NAME_RULE.search(text)
    assert stated, "the script states no shared field-name comparison at all"
    return [name for name in FIELD_NAME_LOOKUPS
            if stated.group("name") not in sync_function_body(text, name)]


@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_every_field_name_lookup_defers_to_one_statement_of_sameness(script):
    """Both are shipped artifacts and both are the subject here.

    What the two filings above assert is that the three lookups agree today.
    What this asserts is why they cannot come to disagree tomorrow: the script
    says once what makes two field names the same, no line outside that
    statement says it again, and each of the three lookups defers to it rather
    than carrying a rule of its own. Three sites that happened to agree would
    pass the filings and fail this.
    """
    text = script.read_text(encoding="utf-8")

    assert rules_of_sameness_outside_the_shared_one(text) == []
    assert lookups_not_deferring_to_the_shared_rule(text) == []


def test_those_scans_report_a_lookup_that_went_its_own_way(tmp_path):
    """The control for both absences above, over two renderings of the template
    that this repository does not ship.

    The first has the id lookup matching the configured name against the board
    verbatim, which is the drift this story removed; the second has the read of
    the item's value spelling the normalization inline, which is where the rule
    used to live. Each is reported by the scan that exists to catch it, so
    silence over the shipped copies is the copies and not a scan that stopped
    seeing anything.
    """
    text = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    stated = SHARED_FIELD_NAME_RULE.search(text)
    assert stated, "the template states no shared field-name comparison at all"
    calling = f'{stated.group("name")}(.name; $name)'

    drifted = text.replace(f"select({calling}) | .id",
                           "select(.name == $name) | .id")
    assert drifted != text, "the id lookup was not found to drift"
    assert lookups_not_deferring_to_the_shared_rule(drifted) == ["field_id_for"]

    inline = text.replace(
        f'select({stated.group("name")}(.key; $name))',
        'select((.key | gsub(" "; "") | ascii_downcase)'
        ' == ($name | gsub(" "; "") | ascii_downcase))')
    assert inline != text, "the read was not found to spell a rule inline"
    reported = rules_of_sameness_outside_the_shared_one(inline)
    assert reported, "the scan sees no rule stated outside the shared one"
    assert all("ascii_downcase" in line for line in reported), reported


#: The subcommand that listed a whole project to find one item in it. Written
#: here rather than derived, because what is asserted is that no script and no
#: filing names it any more — a name derived from the scripts would be the
#: empty string and the assertion would hold of everything.
THE_RETIRED_LISTING = "item-list"


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_filing_that_writes_every_field_lists_no_project(script, tmp_path):
    """The board is never listed, however many fields are written.

    A filing that sets the Status and all five classification fields is driven,
    and the ledger must hold no listing at all. The absence is controlled twice
    over: the read that replaces the listing *is* in the same ledger, asserted
    above; and the stub is then invoked with the listing directly, which the
    ledger does record — so silence here is the script and not a ledger that
    cannot see a listing.
    """
    environment, ledger = stub_tracker(tmp_path)
    result = sync_to_the_board(script, tmp_path, environment, key="k-no-list",
                               payload=a_filed_brief())
    assert result.returncode == 0, result.stderr

    assert project_calls(ledger, THE_RETIRED_LISTING) == []
    assert [call for call in project_calls(ledger)
            if THE_RETIRED_LISTING in call["argv"]] == []
    assert project_calls(ledger, GRAPHQL_CALL), \
        "the ledger holds no board read at all, so it saw nothing either way"

    subprocess.run(
        [sys.executable, str(tmp_path / "stub-bin" / "gh"), "project",
         THE_RETIRED_LISTING, THIS_TARGETS_PROJECT,
         "--owner", THIS_TARGETS_PROJECT_OWNER, "--format", "json"],
        capture_output=True, text=True, timeout=60, env=environment)
    assert project_calls(ledger, THE_RETIRED_LISTING), \
        "the ledger does not record a listing that was in fact made"


#: A board with more items on it than the listing the scripts used to make was
#: ever bounded at. Not derived from either copy: the bound this story retired
#: is gone from both of them, so this is the size of the board the test builds
#: rather than a number read off anything.
A_BOARD_LARGER_THAN_THE_RETIRED_BOUND = 5001


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_board_larger_than_the_retired_bound_files_the_item_added_last(
        script, tmp_path):
    """The board a bounded listing could never have reported this item on.

    The item a filing adds sits at the end of the board's order, so on a board
    past the retired bound it was exactly the item the listing could not
    report, and the entry stayed pending for ever. Asked for by its own node id
    there is no size to outgrow, so the filing lands.
    """
    environment, ledger = stub_tracker(tmp_path)

    def crowd(project):
        project["items"] = [
            {"id": "PVTI_%d" % (number + 1),
             "url": "https://tracker.invalid/issues/already-%d" % (number + 1)}
            for number in range(A_BOARD_LARGER_THAN_THE_RETIRED_BOUND)]
    rewrite_the_board(ledger, crowd)

    result = sync_to_the_board(script, tmp_path, environment, key="k-crowded",
                               payload=a_filed_brief())
    assert result.returncode == 0, result.stderr

    items = board_items(ledger)
    assert len(items) == A_BOARD_LARGER_THAN_THE_RETIRED_BOUND + 1
    filed = items[-1]
    assert filed["url"] == result.stdout.strip().splitlines()[-1]
    assert filed["status"] == THIS_TARGETS_STATUS_OPTION
    for axis in CLASSIFICATION:
        assert filed.get(axis.field_name.replace(" ", "").lower()) == \
            str(a_filed_brief()[axis.payload_field]), axis.field_name


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_field_the_board_already_reports_is_left_alone(script, tmp_path):
    """Nothing overwrites a value a person edited.

    One filing writes all five; a person then changes one of them and another
    is cleared; the same entry is filed again. The changed one keeps what the
    person put there and the cleared one is written — so the absence of an edit
    is controlled by the edit that is made in the same invocation.
    """
    environment, ledger = stub_tracker(tmp_path)
    edited, cleared = CLASSIFICATION[0], CLASSIFICATION[3]
    brief = a_filed_brief()

    first = sync_to_the_board(script, tmp_path, environment, key="k-settled-field",
                              payload=brief)
    assert first.returncode == 0, first.stderr

    a_person_chose = [value for value in edited.values
                      if value != brief[edited.payload_field]][0]

    def change(project):
        item = project["items"][0]
        item[edited.field_name.replace(" ", "").lower()] = a_person_chose
        item.pop(cleared.field_name.replace(" ", "").lower())
    rewrite_the_board(ledger, change)
    state = ledger_state(ledger)
    state["calls"] = []
    ledger.write_text(json.dumps(state), encoding="utf-8")

    again = sync_to_the_board(script, tmp_path, environment,
                              key="k-settled-field", payload=brief)
    assert again.returncode == 0, again.stderr

    assert board_field_value(ledger, edited.field_name) == a_person_chose
    assert board_field_value(ledger, cleared.field_name) == \
        str(brief[cleared.payload_field])
    assert len(project_calls(ledger, "item-edit")) == 1


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_retry_reaches_the_label_work_and_the_field_work(script, tmp_path):
    """A filing whose board work failed is finished by the next sweep.

    The first invocation is failed at the item-add, below the issue's creation
    and below the label add, so the entry is left with an issue, a label and no
    board item. The second invocation finds that issue and must go on to do
    both — the label work is idempotent over what is already there, and the
    field work is what the first one never reached — without creating a second
    issue.
    """
    environment, ledger = stub_tracker(tmp_path)
    brief = a_filed_brief()

    failed = sync_to_the_board(script, tmp_path, environment, key="k-finished",
                               payload=brief,
                               breaking={FAIL_VARIABLE: "item-add"})
    assert failed.returncode == TRANSIENT_EXIT, failed.stderr
    assert board_items(ledger) == []
    created = ledger_state(ledger)["issues"][0]["url"]

    retried = sync_to_the_board(script, tmp_path, environment, key="k-finished",
                                payload=brief)
    assert retried.returncode == 0, retried.stderr
    assert retried.stdout.strip().splitlines()[-1] == created
    assert len(ledger_state(ledger)["issues"]) == 1, "a second issue was created"

    _, prefix = label_constants(script)
    assert f"{prefix}{brief['category']}" in issue_labels(ledger)
    for axis in CLASSIFICATION:
        assert board_field_value(ledger, axis.field_name) == \
            str(brief[axis.payload_field]), axis.field_name


@needs_jq
def test_the_template_with_nothing_configured_writes_no_field(tmp_path):
    """A target that has configured no field files as it does today.

    The template is run with a project and a column and no field name at all,
    which is what a target that edited neither gets: the brief is filed, the
    item lands in its column, and the only edit made is the Status one. The
    control is the drive above through the same template *with* the five names
    set, where all five are written.
    """
    environment, ledger = stub_tracker(tmp_path)
    result = run_the_sync(
        TEMPLATE_SCRIPT, tmp_path, environment, key="k-no-fields",
        payload=a_filed_brief(),
        extra={TEMPLATE_CONSTANTS[PROJECT_CONSTANT][0]: THIS_TARGETS_PROJECT,
               TEMPLATE_CONSTANTS[STATUS_OPTION_CONSTANT][0]:
                   THIS_TARGETS_STATUS_OPTION})

    assert result.returncode == 0, result.stderr
    assert board_items(ledger)[0]["status"] == THIS_TARGETS_STATUS_OPTION
    assert len(project_calls(ledger, "item-edit")) == 1
    for axis in CLASSIFICATION:
        assert board_field_value(ledger, axis.field_name) == "", axis.field_name

    configured = sync_to_the_board(TEMPLATE_SCRIPT, tmp_path, environment,
                                   key="k-with-fields", payload=a_filed_brief())
    assert configured.returncode == 0, configured.stderr
    assert len(project_calls(ledger, "item-edit")) == \
        1 + 1 + len(CLASSIFICATION)


# --------------------------------------------------------------------------
# The template carries no value particular to this deployment
# --------------------------------------------------------------------------


def test_the_template_names_no_project_no_status_option_and_no_field():
    """A shipped artifact and the subject: what the template carries.

    A template carrying a project number would file another repository's briefs
    onto this board, and one carrying a field name would name a column another
    board has no reason to have. The owner is allowed the generic default it
    ships with, and that is asserted as the claim this sentence makes — that
    the default is non-empty and generic — rather than as an equality with what
    this target's owner happens to be, which would redden a test about the
    template the day this target changed owner. The project, the column and the
    five field names must all default to empty, and the column this target
    files into must not appear anywhere in the file.

    The prefix a category's label carries is deliberately not in that list: it
    is a mechanic every target that files briefs wants rather than a property
    of a board, so the template is required to carry a non-empty one.

    The control is the same extraction and the same search over the installed
    copy, which does name all of them — so the emptiness here is the
    template's rather than a parse that stopped matching anything. The
    installed side is asserted as non-emptiness rather than as an equality with
    a value read out of that same copy, which would be that value compared
    against itself.
    """
    template = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    installed = sync_constants(INSTALLED_SCRIPT.read_text(encoding="utf-8"))

    assert TEMPLATE_CONSTANTS[PROJECT_CONSTANT][1] == ""
    assert TEMPLATE_CONSTANTS[STATUS_OPTION_CONSTANT][1] == ""
    assert TEMPLATE_CONSTANTS[PROJECT_OWNER_CONSTANT][1] != ""
    assert THIS_TARGETS_STATUS_OPTION not in template
    assert TEMPLATE_CONSTANTS["CATEGORY_LABEL_PREFIX"][1] != ""

    assert installed[PROJECT_CONSTANT][1] != ""
    assert installed[STATUS_OPTION_CONSTANT][1] != ""
    for constant in BOARD_CONSTANTS:
        assert installed[constant][1] != "", constant

    for one in CLASSIFICATION:
        assert TEMPLATE_CONSTANTS[one.constant][1] == "", one.constant
        assert installed[one.constant][1] != "", one.constant


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
    template = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    installed = INSTALLED_SCRIPT.read_text(encoding="utf-8")

    assert installed != template, \
        "the installed copy sets no value of its own, so it files nowhere"
    assert differences_that_are_not_constant_values(template, installed) == []


def test_that_comparison_reports_a_difference_that_is_not_a_constant(tmp_path):
    """The control: the same predicate over a copy of the template whose
    difference is a line of mechanics rather than a value.

    Rendered here rather than written to the tree, so the control is about the
    comparison and not about this repository.
    """
    template = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    tampered = template.replace("fail_transient()", "fail_transient_renamed()")
    assert tampered != template

    reported = differences_that_are_not_constant_values(template, tampered)
    assert reported, "the comparison sees no difference it should report"
    assert any("fail_transient_renamed" in line for line in reported), reported


# --------------------------------------------------------------------------
# Neither copy lists a project, and neither carries a bound on doing so
# --------------------------------------------------------------------------


#: The constant that bounded the listing, and the invocation it bounded. Both
#: are written here rather than derived: the whole claim is that neither
#: appears, and a name derived from the files being scanned would be the empty
#: string, which appears in every file there is.
THE_RETIRED_BOUND = "ITEM_LIST_LIMIT"
THE_RETIRED_INVOCATION = f"gh project {THE_RETIRED_LISTING}"


def listing_mentions(text: str) -> list[str]:
    """Every line naming the retired bound or the listing it bounded."""
    return [line for line in text.splitlines()
            if THE_RETIRED_BOUND in line or THE_RETIRED_INVOCATION in line]


@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_no_sync_script_lists_a_project_or_bounds_a_listing(script):
    """A shipped artifact and the subject.

    Neither copy may name the bound or make the invocation, and the two are
    scanned for together because deleting one without the other leaves a
    constant nothing reads or a listing nothing bounds. The control is below:
    the same scan over a rendering of the same script with the listing put back
    reports both lines, so silence here is the file rather than a scan that
    matches nothing.
    """
    assert listing_mentions(script.read_text(encoding="utf-8")) == []


def test_that_scan_reports_a_listing_put_back_into_a_sync_script():
    """The control, on a rendering rather than on the tree."""
    restored = TEMPLATE_SCRIPT.read_text(encoding="utf-8").replace(
        'echo "$url"',
        f'{THE_RETIRED_BOUND}=5000\n'
        f'{THE_RETIRED_INVOCATION} "$PROJECT" --limit "${THE_RETIRED_BOUND}"\n'
        'echo "$url"')
    reported = listing_mentions(restored)

    assert any(THE_RETIRED_BOUND in line for line in reported), reported
    assert any(THE_RETIRED_INVOCATION in line for line in reported), reported


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
    committing = TEMPLATE_SCRIPT.read_text(encoding="utf-8").replace(
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
        script=INSTALLED_SCRIPT)
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
    # Two words now: the installed script, and the job it is being asked for.
    # The harness splits a configured command into words before running it, so
    # the first word is the path and the rest are the command's own arguments.
    assert configured[1:] == [QUERY_JOB], configured

    command = (REPO_ROOT / configured[0]).resolve()
    installed = (REPO_ROOT / ".harness" / SCRIPTS_DIR).resolve()
    assert command.parent == installed, command
    assert command.name in REFERENCE_SCRIPTS, command.name
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


# --------------------------------------------------------------------------
# One file, three jobs: the dispatcher, and the failure vocabularies
# --------------------------------------------------------------------------


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
@pytest.mark.parametrize(
    "arguments, id_",
    [pytest.param([], "no-argument", id="no-argument"),
     pytest.param(["sinc"], "sinc", id="mistyped"),
     pytest.param(["SYNC"], "SYNC", id="wrong-case")])
def test_the_dispatcher_refuses_an_argument_it_does_not_answer_to(
        script, arguments, id_, tmp_path):
    """It refuses rather than guesses, so a mistyped configuration is reported.

    Answering a mistyped first argument as some other job would file an entry
    the target asked to have queried, or publish onto an item it asked to have
    filed, and a target would have no way to see that it had happened. So both
    cases exit non-zero saying what they were given, and neither performs any
    part of any job: nothing is filed, nothing is read and nothing is printed
    on stdout.
    """
    environment, ledger = stub_tracker(tmp_path)
    before = ledger.read_text(encoding="utf-8")

    result = subprocess.run(
        [INTERPRETER, str(script), *arguments],
        input=json.dumps({"key": "k-refused", "identity": {}, "state": "pending",
                          "payload": dict(AN_ENTRY)}),
        capture_output=True, text=True, timeout=60, cwd=tmp_path,
        env={**environment, **board_environment_for(script),
             command_transport.KEY_ENVIRONMENT_VARIABLE: "k-refused"})

    assert result.returncode != 0
    assert result.stdout == ""
    # It says what it was given, where there was something to say, and what it
    # does answer to either way — so the repair is in the message.
    if arguments:
        assert arguments[0] in result.stderr
    for job in (SYNC_JOB, QUERY_JOB, ITEM_JOB):
        assert job in result.stderr, job

    # No part of any job ran: nothing reached the tracker at all.
    assert ledger.read_text(encoding="utf-8") == before
    assert ledger_state(ledger)["issues"] == []


def test_only_the_sync_branch_carries_a_transient_exit():
    """A shipped artifact and the subject: the failure vocabularies stay apart.

    Exit 75 means "the entry stays pending and a later sweep tries again", and
    nothing retries behind the query or the item branch — a 75 there would name
    a mechanism that does not exist, and the merge is exactly where the three
    vocabularies could have been blended by accident. So the transient helper
    is declared once, the sync branch is the only branch that reaches it, and
    the transient code appears nowhere in the other two.
    """
    shipped = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    assert f"exit {TRANSIENT_EXIT}" in shipped

    sync = branch_source(shipped, "do_sync")
    assert "fail_transient" in sync

    for name in ("do_query", "do_item"):
        branch = branch_source(shipped, name)
        assert "fail_transient" not in branch, name
        assert str(TRANSIENT_EXIT) not in branch, name


def test_the_shared_declarations_are_stated_once_and_no_job_keeps_a_copy():
    """A shipped artifact and the subject: what the merge was for.

    The project, its owner and the Status field's name are assigned once under
    the shared names, and none of the six per-job names the three scripts used
    to carry survives — which is what makes one project constant serve all
    three branches, and a status move stop reporting a failure on a deployment
    whose filings land.
    """
    shipped = TEMPLATE_SCRIPT.read_text(encoding="utf-8")

    for name in SHARED_CONSTANTS:
        assert name in TEMPLATE_CONSTANTS, name
        assert TEMPLATE_CONSTANTS[name][0] == name, name
        assert len([line for line in shipped.splitlines()
                    if line.startswith(f"{name}=")]) == 1, name

    for retired in RETIRED_PER_JOB_CONSTANTS:
        assert retired not in shipped, retired


def test_the_field_matching_rule_is_stated_once_and_every_lookup_defers_to_it():
    """The rule story-129 wrote for the sync script, now serving both writers.

    It is stated once above every branch, and the three lookups that match a
    field name — the read of an item's current value, the resolution of a
    field's id and the field-name half of the resolution of an option's id —
    each refer to that statement rather than carrying a rule of their own. That
    is what makes a field name resolve the same way whichever command asks.
    """
    shipped = TEMPLATE_SCRIPT.read_text(encoding="utf-8")

    assert shipped.count("SAME_FIELD_NAME='def same_field_name") == 1
    for lookup in ("board_value()", "field_id_for()", "option_id_for()"):
        assert lookup in shipped, lookup
    assert shipped.count('"$SAME_FIELD_NAME"') == 3

    # Declared above every branch rather than inside one, so both writers reach
    # the same statement.
    for name in ("do_sync", "do_query", "do_item"):
        assert "SAME_FIELD_NAME='def" not in branch_source(shipped, name), name
