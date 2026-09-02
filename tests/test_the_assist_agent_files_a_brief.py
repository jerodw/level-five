"""Independent validation for the story that lets an assist session file the
brief it wrote, into the outbox, exactly as the Inspector's are filed.

Written from the story's acceptance criteria rather than from the
implementation. Five subjects, each asserted at the altitude it lives at:

  * **the filing module.** `orchestration/brief_filing.py` is a function over
    a brief, a configuration and a target, so it is driven directly: against a
    harness root this module wrote, a target repository this module built and
    filed-query commands this module wrote, so that no assertion here depends
    on which workflows this repository happens to deploy or on what any
    tracker holds. Every way of not filing is driven and each is shown to be
    reported distinctly and to enqueue nothing.
  * **the one identity.** A brief filed through the new path and a finding the
    Inspector filed are driven through both producers into one queue, and the
    queue is shown to hold one entry under one key. That is the whole reason
    `orchestration/story_brief.py` exists, so it is demonstrated rather than
    asserted about the source.
  * **the extraction.** The names `orchestration/inspection.py` exposed before
    this story are run over inputs chosen for what the bare-path rule and the
    identity have to decide, and compared against the answers written here.
    Behaviour-preserving is a comparison, not a claim — and the answers are
    values this module states rather than a prior version of the functions
    recovered out of the commit graph, because what those functions answer is
    not a property of this repository's history and would move under a rebase,
    a squash or a rename that says nothing about whether the move was right.
  * **the entry point and the seam.** `main` is driven for its words and its
    exit status; `scripts/l5-assist` is driven with `execvp` intercepted, so
    what it would have handed `claude` is read rather than reasoned about; and
    the shipped plugin is handed to `claude plugin validate --strict`.
  * **the prose.** `plugin/skills/file-a-brief/SKILL.md` and
    `prompts/assist.md` are read and searched, never eyeballed, and the shipped
    skill is put through the real harness-source scan.

Every absence asserted here carries a demonstration that it can fail:

  * "nothing was enqueued" sits beside the same call with the refusal removed,
    where the same queue read reports an entry;
  * "the filing path hashes nothing and reaches the queue only through
    `enqueue`" sits beside the same two searches over the module that does
    hash and over a planted source that names a second queue operation;
  * "the exposed names still resolve to the same values" sits beside the same
    comparison against a mutant of `story_brief` whose identity differs, which
    the comparison reports — an equality between two spellings of one object
    would pass however either was written;
  * "the shipped skill carries no stack token and no target-layout path" sits
    beside the same scan over a throwaway copy of that skill with one of each
    planted in it, which reports both;
  * "the shipped plugin validates strictly" sits beside the same command over
    a copy whose manifest is broken, which fails;
  * "nothing beneath the stories directory was written" sits beside the same
    snapshot comparison with a file planted there, which reports it;
  * "the skill names what this story put in it" sits beside the same searches
    over a rendering of the prompt with those words taken out, which report
    every one of them absent, so a search that has stopped seeing anything is
    told apart from a prompt that says it.

Nothing here invokes a model: the one inspection driven below goes through the
fake runner `tests/test_inspection.py` already provides.
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import load_mutant, load_script

import brief_filing
import filed_query
import harness_config
import harness_source
import inspection
import outbox
import schema_validator
import story_brief

import test_inspection as producer
import test_no_target_stack_in_harness_source as scan_module

REPO_ROOT = Path(brief_filing.__file__).resolve().parents[1]

#: The names the module under test says of itself, read off it so this file
#: spells no outcome, no key and no reason of its own.
FILED = brief_filing.FILED
ALREADY_FILED = brief_filing.ALREADY_FILED
ALREADY_FILED_LOCALLY = brief_filing.ALREADY_FILED_LOCALLY
ALREADY_QUEUED = brief_filing.ALREADY_QUEUED
MALFORMED = brief_filing.MALFORMED
UNKNOWN_WORKFLOW = brief_filing.UNKNOWN_WORKFLOW
LOST_BY_THE_QUEUE = brief_filing.LOST_BY_THE_QUEUE

#: The three states an entry can be in, read off the queue's own module.
PENDING = outbox.PENDING
LANDED = outbox.LANDED
FAILED = outbox.FAILED

#: The shape a brief is held to, loaded as it ships: the shape *is* the
#: subject of the schema assertions, so it is the deployed one rather than a
#: fixture, exactly as `tests/test_inspection.py` treats it.
BRIEF_SCHEMA = schema_validator.load_schema(brief_filing.BRIEF_SCHEMA)
REQUIRED_FIELDS = tuple(BRIEF_SCHEMA["required"])

#: The workflow definitions, the blocked prefixes and the prompt every
#: assertion below is decided against come from the mirrored harness root
#: `tests/test_inspection.py` builds, for the reason stated there: they are
#: *inputs* to what the filing decides, and reading the deployed ones would
#: turn shipping a third workflow into something this suite reddens.
MIRROR_WORKFLOW = producer.MIRROR_WORKFLOW
OTHER_MIRROR_WORKFLOW = producer.OTHER_MIRROR_WORKFLOW
UNDEFINED_WORKFLOW = producer.UNDEFINED_WORKFLOW
SOURCE_FILE = producer.SOURCE_FILE
OTHER_SOURCE_FILE = producer.OTHER_SOURCE_FILE

#: The entry point an assist session invokes, by the name a skill has to use
#: to invoke it, derived rather than spelled.
ENTRY_POINT = Path(brief_filing.__file__).name

# --------------------------------------------------------------------------
# The shipped seam: the launcher, the plugin directory and the one skill
#
# Everything below is derived from the launcher rather than named here. The
# launcher says which directory it loads; the plugin directory says which
# skills it carries; and the skill this story is about is the one that names
# the entry point, so a second skill shipped later is not mistaken for it.
# --------------------------------------------------------------------------

ASSIST_LAUNCHER = "l5-assist"
LAUNCHER = load_script(ASSIST_LAUNCHER)
PLUGIN_DIR = Path(LAUNCHER.PLUGIN_DIR)

FILING_SKILLS = sorted(
    path for path in PLUGIN_DIR.rglob("SKILL.md")
    if ENTRY_POINT in path.read_text(encoding="utf-8")
)


def skill_path() -> Path:
    """The one shipped skill that invokes the entry point.

    Resolved rather than named, and refused when it is not exactly one: two
    skills naming the entry point would make every prose assertion below
    silently about whichever sorted first.
    """
    assert len(FILING_SKILLS) == 1, [p.as_posix() for p in FILING_SKILLS]
    return FILING_SKILLS[0]


SKILL_PATH = skill_path()
SKILL_REL = SKILL_PATH.relative_to(REPO_ROOT).as_posix()
SKILL_TEXT = SKILL_PATH.read_text(encoding="utf-8")


def frontmatter(text: str) -> dict:
    """What a skill's own frontmatter declares, as the fields it declares."""
    lines = text.splitlines()
    assert lines and lines[0].strip() == "---", lines[:1]
    declared = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return declared
        key, separator, value = line.partition(":")
        if separator and not key.startswith(" "):
            declared[key.strip()] = value.strip()
    raise AssertionError("the skill's frontmatter is not closed")


FRONTMATTER = frontmatter(SKILL_TEXT)
SKILL_NAME = FRONTMATTER["name"]


# --------------------------------------------------------------------------
# The inputs one filing is decided against
# --------------------------------------------------------------------------


def a_brief(**overrides) -> dict:
    """One conforming brief, with the caller's departures applied.

    The producer module's own fixture brief, so a brief filed here and a
    finding the Inspector files are the same document — which is what makes
    the shared-key demonstration below a demonstration rather than a
    coincidence of two fixtures that happen to agree.
    """
    return producer.brief(**overrides)


def a_configuration(command: str | None = None) -> dict:
    """The target configuration one filing reads: the filed query, and nothing.

    Nothing else in the filing path reads configuration, so a configuration
    carrying more would say that it did.
    """
    return {} if command is None else {filed_query.COMMAND_KEY: command}


def entries(target: Path) -> list[dict]:
    """Every entry the target's queue holds, read as the sweep reads them."""
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in outbox.entry_files(outbox.queue_dir(target))]


def key_of(brief: dict) -> str:
    """What a brief is filed under, derived the way the harness derives it."""
    return outbox.identity_key(story_brief.identity(brief))


@pytest.fixture(scope="module")
def harness(tmp_path_factory) -> Path:
    """A harness root defining workflows this repository does not ship."""
    return producer.harness_mirror(tmp_path_factory.mktemp("mirror"))


@pytest.fixture
def target(tmp_path) -> Path:
    """A target repository this module built, with an empty queue."""
    return producer.target_repository(tmp_path)


def filing(brief: dict, target: Path, harness: Path,
           command: str | None = None):
    """One filing, against a target and a harness root the test owns."""
    return brief_filing.file_brief(brief, a_configuration(command), target,
                                   harness)


# ==========================================================================
# A brief that does not satisfy the shape is not filed, and the field is named
# ==========================================================================


@pytest.mark.parametrize("missing", REQUIRED_FIELDS)
def test_a_brief_missing_a_required_field_is_refused_naming_that_field(
        missing, target, harness):
    """Named by the field that failed rather than by the shape, so what the
    developer is told is what to change. Driven for every required field the
    shipped schema declares, so a field added to it is covered without this
    module being edited."""
    incomplete = {name: value for name, value in a_brief().items()
                  if name != missing}

    outcome = filing(incomplete, target, harness)

    assert outcome.outcome == MALFORMED
    assert missing in outcome.detail, outcome.detail
    assert not outcome.filed
    assert outcome.key == ""
    assert entries(target) == []


def test_a_brief_whose_field_is_out_of_its_enum_is_refused_naming_the_field(
        target, harness):
    """The other half of the shape: a value the schema does not accept."""
    outcome = filing(a_brief(severity="not a severity"), target, harness)

    assert outcome.outcome == MALFORMED
    assert "severity" in outcome.detail, outcome.detail
    assert entries(target) == []


def test_the_same_brief_is_filed_once_the_shape_is_satisfied(target, harness):
    """The control for the refusals above.

    The queue read that reports nothing there reports an entry here, so
    "nothing was enqueued" is a fact about the refusal rather than about a
    queue nothing could have reached.
    """
    outcome = filing(a_brief(), target, harness)

    assert outcome.outcome == FILED
    assert [entry["key"] for entry in entries(target)] == [outcome.key]


# ==========================================================================
# A workflow no definition names is not filed, and the refusal lists what is
# ==========================================================================


def test_a_brief_naming_an_undefined_workflow_is_refused_and_files_nothing(
        target, harness):
    outcome = filing(a_brief(workflow=UNDEFINED_WORKFLOW), target, harness)

    assert outcome.outcome == UNKNOWN_WORKFLOW
    assert UNDEFINED_WORKFLOW in outcome.detail, outcome.detail
    assert entries(target) == []


def test_the_refusal_names_the_workflows_the_harness_holds_definitions_for(
        target, harness):
    """Read from the definitions rather than from a restated list.

    The names are the mirrored root's own — workflows this repository does not
    ship — so a refusal quoting them can only have read the definitions it was
    given.
    """
    outcome = filing(a_brief(workflow=UNDEFINED_WORKFLOW), target, harness)

    defined = harness_config.workflow_names(harness)
    assert defined, harness
    for name in defined:
        assert name in outcome.detail, (name, outcome.detail)


def test_a_third_definition_becomes_filable_with_no_edit_to_the_filing_path(
        tmp_path, target):
    """A workflow the harness holds is a workflow a brief may name.

    A harness root carrying a definition neither this repository nor the
    shared mirror knows about: the brief naming it is filed, and the refusal
    for a name that root does *not* carry lists it. Both come from the
    definitions alone.
    """
    third = "zzz-a-third-definition-nothing-else-carries"
    root = producer.harness_mirror(
        tmp_path, workflows={third: "the definition this test shipped"},
        name="third-workflow-harness")

    assert filing(a_brief(workflow=third), target, root).outcome == FILED

    refused = filing(a_brief(workflow=UNDEFINED_WORKFLOW, slug="another-one"),
                     target, root)
    assert refused.outcome == UNKNOWN_WORKFLOW
    assert third in refused.detail, refused.detail


@pytest.mark.parametrize("workflow", [MIRROR_WORKFLOW, OTHER_MIRROR_WORKFLOW])
def test_every_workflow_the_root_defines_is_accepted(workflow, tmp_path,
                                                     harness):
    """The control for the refusal: the check refuses a name, not everything."""
    fresh = producer.target_repository(tmp_path, name=f"target-{workflow}")
    assert filing(a_brief(workflow=workflow), fresh, harness).outcome == FILED


# ==========================================================================
# The key is the outbox's derivation over story_brief's identity
# ==========================================================================


def test_the_key_a_brief_is_filed_under_is_the_outboxs_own_derivation(target,
                                                                     harness):
    outcome = filing(a_brief(), target, harness)

    assert outcome.key == outbox.identity_key(story_brief.identity(a_brief()))
    assert [entry["key"] for entry in entries(target)] == [outcome.key]
    assert entries(target)[0]["identity"] == story_brief.identity(a_brief())


def test_the_filing_path_hashes_nothing_and_reaches_the_queue_one_way():
    """Two halves of one claim: the outbox computes the key.

    Neither module derives a digest of its own, and the only queue operations
    the filing names are the key derivation, the single enqueue, where the
    queue lives, the single-key read the local check makes, and the two states
    that read is decided by. The controls are below.
    """
    filing_source = Path(brief_filing.__file__).read_text(encoding="utf-8")
    identity_source = Path(story_brief.__file__).read_text(encoding="utf-8")

    assert producer.outbox_attributes(filing_source) == {
        "identity_key", "enqueue", "queue_dir",
        "entry_path", "read_entry", "LANDED", "PENDING",
    }
    assert producer.outbox_attributes(identity_source) == set()
    for source in (filing_source, identity_source):
        assert "hashlib" not in source
        assert "sha256" not in source


def test_the_same_searches_report_a_module_that_hashes_and_a_second_call():
    """The controls for the two absences above.

    The digest search is pointed at the module that legitimately derives one,
    and the attribute scan at a source naming a queue operation the filing does
    not make, so neither absence above is a search that has stopped seeing
    anything.
    """
    hashing = Path(outbox.__file__).read_text(encoding="utf-8")
    assert "hashlib" in hashing
    assert "sha256" in hashing

    planted = ("import outbox\n\n\n"
               "def drain(queue):\n"
               "    return outbox.sync(queue)\n")
    assert producer.outbox_attributes(planted) == {"sync"}


# ==========================================================================
# One identity, two producers
# ==========================================================================


def test_a_brief_and_a_finding_alike_in_the_four_members_derive_one_key():
    """Derived through the one module, from both names that reach it.

    `inspection.identity` and `story_brief.identity` are asked about the same
    document and must answer alike, and the key over each must be the same
    string — which is what makes a brief a developer filed by hand and a
    finding the Inspector reported land on one entry.
    """
    document = a_brief()

    assert inspection.identity(document) == story_brief.identity(document)
    assert outbox.identity_key(inspection.identity(document)) \
        == outbox.identity_key(story_brief.identity(document))
    assert inspection.KIND == story_brief.KIND


def test_the_two_producers_filing_one_piece_of_work_leave_one_entry(tmp_path,
                                                                    harness):
    """The demonstration the shared module exists for, driven end to end.

    A brief is filed through the new path, and then the Inspector runs against
    the same target and reports the same piece of work through the fake runner
    the producer module provides. The queue holds one entry, under the key the
    filing answered with, and the Inspector reports it as already queued rather
    than filing a second one.
    """
    target = producer.target_repository(tmp_path)
    command = producer.answering_query(tmp_path)
    config = producer.configuration(**{filed_query.COMMAND_KEY: command})

    filed = brief_filing.file_brief(a_brief(), config, target, harness)
    assert filed.outcome == FILED

    found = producer.inspecting(tmp_path, target=target, harness=harness,
                                config=config, act=producer.writes(a_brief()))

    assert found.filed_slugs == []
    assert [drop.detail for drop in found.dropped(inspection.ALREADY_QUEUED)]
    assert [entry["key"] for entry in entries(target)] == [filed.key]


def test_the_inspector_files_that_same_work_when_no_brief_preceded_it(
        tmp_path, harness):
    """The control for the entry count above.

    Without the filing, the same inspection against the same target files the
    same piece of work — so the single entry above is the two producers meeting
    on one key rather than an inspection that filed nothing.
    """
    command = producer.answering_query(tmp_path)
    config = producer.configuration(**{filed_query.COMMAND_KEY: command})

    found = producer.inspecting(tmp_path, harness=harness, config=config,
                                act=producer.writes(a_brief()))

    assert found.filed_slugs == [a_brief()["slug"]]
    assert [entry["key"] for entry in found.entries] == [key_of(a_brief())]


# ==========================================================================
# A path carrying a line number is made bare before anything reads it
# ==========================================================================


LINE_SUFFIXED = (f"{SOURCE_FILE}:42", f"{OTHER_SOURCE_FILE}:7:3")


def test_a_line_numbered_path_is_bare_in_the_identity_and_in_the_payload(
        target, harness):
    """Both readings, because both matter: the key is what dedupe compares and
    the payload is what a marker is written from. The body is untouched, which
    is where the line the path lost still is."""
    body = f"{SOURCE_FILE}:42 states it and {OTHER_SOURCE_FILE}:7 disagrees"
    lined = a_brief(paths=list(LINE_SUFFIXED), body=body)

    outcome = filing(lined, target, harness)

    bare = a_brief(paths=[SOURCE_FILE, OTHER_SOURCE_FILE])
    assert outcome.key == key_of(bare)

    payload = entries(target)[0]["payload"]
    assert payload["paths"] == sorted([SOURCE_FILE, OTHER_SOURCE_FILE])
    for suffixed in LINE_SUFFIXED:
        assert suffixed not in payload["paths"]
    assert payload["body"] == body
    assert ":42" in payload["body"]


def test_a_brief_filed_with_lines_and_one_filed_without_them_are_one_entry(
        target, harness):
    """The same claim as an idempotency, which is what it is for."""
    first = filing(a_brief(paths=list(LINE_SUFFIXED)), target, harness)
    assert first.outcome == FILED

    again = filing(a_brief(paths=[SOURCE_FILE, OTHER_SOURCE_FILE]), target,
                   harness)

    assert again.outcome == ALREADY_QUEUED
    assert [entry["key"] for entry in entries(target)] == [first.key]


def test_a_brief_about_a_different_file_is_a_different_entry(target, harness):
    """The control: the bare-path rule collapses line numbers and not files."""
    first = filing(a_brief(paths=[SOURCE_FILE]), target, harness)
    second = filing(a_brief(paths=[OTHER_SOURCE_FILE]), target, harness)

    assert second.outcome == FILED
    assert sorted(entry["key"] for entry in entries(target)) \
        == sorted([first.key, second.key])


# ==========================================================================
# The three ways a brief is already filed, told apart
# ==========================================================================


def landed(target: Path, key: str) -> dict:
    """The entry at `key`, moved to landed as a sync would leave it."""
    queue = outbox.queue_dir(target)
    entry = json.loads(outbox.entry_path(queue, key).read_text(encoding="utf-8"))
    entry["state"] = LANDED
    outbox.write_entry(queue, entry)
    return entry


def test_a_brief_a_tracker_reported_is_not_enqueued(tmp_path, target, harness):
    """The filed query answered with this brief's own key."""
    reported = producer.answering_query(
        tmp_path, {"key": key_of(a_brief()), "title": "the tracker's item"},
        name="reports-this-one.sh")

    outcome = filing(a_brief(), target, harness, command=reported)

    assert outcome.outcome == ALREADY_FILED
    assert outcome.dedupe_ran
    assert entries(target) == []


def test_a_brief_the_query_reported_under_another_key_is_still_filed(
        tmp_path, target, harness):
    """The control for the suppression above: the comparison is on the key."""
    other = producer.answering_query(
        tmp_path, {"key": key_of(a_brief(slug="some-other-work")),
                   "title": "a different item"},
        name="reports-another.sh")

    outcome = filing(a_brief(), target, harness, command=other)

    assert outcome.outcome == FILED
    assert [entry["key"] for entry in entries(target)] == [outcome.key]


def test_a_brief_the_local_queue_holds_pending_is_reported_as_already_queued(
        tmp_path, target, harness):
    answers = producer.answering_query(tmp_path)
    first = filing(a_brief(), target, harness, command=answers)

    outcome = filing(a_brief(), target, harness, command=answers)

    assert outcome.outcome == ALREADY_QUEUED
    assert entries(target)[0]["state"] == PENDING
    assert [entry["key"] for entry in entries(target)] == [first.key]


def test_a_brief_the_local_queue_holds_landed_is_reported_as_filed_here(
        tmp_path, target, harness):
    answers = producer.answering_query(tmp_path)
    first = filing(a_brief(), target, harness, command=answers)
    before = landed(target, first.key)

    outcome = filing(a_brief(), target, harness, command=answers)

    assert outcome.outcome == ALREADY_FILED_LOCALLY
    assert entries(target) == [before]


def test_a_brief_the_local_queue_holds_failed_is_filed_again(tmp_path, target,
                                                             harness):
    """A failed entry is terminal and suppresses nothing.

    Also the control that the local check reads the *state* rather than the
    presence of a file: the same key, the same file, and a different answer.
    """
    answers = producer.answering_query(tmp_path)
    first = filing(a_brief(), target, harness, command=answers)
    queue = outbox.queue_dir(target)
    entry = json.loads(
        outbox.entry_path(queue, first.key).read_text(encoding="utf-8"))
    entry["state"] = FAILED
    outbox.write_entry(queue, entry)

    outcome = filing(a_brief(), target, harness, command=answers)

    assert outcome.outcome == FILED
    assert outcome.key == first.key
    assert [entry["state"] for entry in entries(target)] == [PENDING]


def test_the_three_already_filed_outcomes_are_distinguishable(tmp_path,
                                                              harness):
    """What the developer reads tells the three apart.

    Each of the three is driven to its own target, each report is captured, and
    the three texts are required to be pairwise different and each to carry its
    own outcome. Three suppressions reported in one wording would be a
    developer who cannot tell a tracker's answer from this machine's.
    """
    said = {}
    for outcome_name, build in (
        (ALREADY_FILED, "tracker"),
        (ALREADY_FILED_LOCALLY, "landed"),
        (ALREADY_QUEUED, "pending"),
    ):
        target = producer.target_repository(tmp_path, name=f"target-{build}")
        if build == "tracker":
            command = producer.answering_query(
                tmp_path, {"key": key_of(a_brief()), "title": "the item"},
                name=f"query-{build}.sh")
        else:
            command = producer.answering_query(tmp_path, name=f"query-{build}.sh")
            first = filing(a_brief(), target, harness, command=command)
            if build == "landed":
                landed(target, first.key)
        outcome = filing(a_brief(), target, harness, command=command)
        assert outcome.outcome == outcome_name, outcome
        said[outcome_name] = report_text(outcome)

    assert len(set(said.values())) == len(said), said
    for outcome_name, text in said.items():
        assert outcome_name in text, (outcome_name, text)


def report_text(outcome) -> str:
    """What `report` says about an outcome, captured rather than recomposed."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        brief_filing.report(outcome)
    return buffer.getvalue()


# ==========================================================================
# A query that could not answer costs dedupe and costs nothing else
# ==========================================================================


def test_a_query_that_could_not_answer_files_the_brief_anyway(tmp_path, target,
                                                              harness):
    outcome = filing(a_brief(), target, harness,
                     command=producer.failing_query(tmp_path))

    assert outcome.outcome == FILED
    assert outcome.dedupe_asked
    assert not outcome.dedupe_ran
    assert outcome.dedupe_reason
    assert [entry["key"] for entry in entries(target)] == [outcome.key]


def test_the_developer_is_told_that_dedupe_did_not_run(tmp_path, target,
                                                       harness):
    outcome = filing(a_brief(), target, harness,
                     command=producer.failing_query(tmp_path))

    said = report_text(outcome).lower()
    assert "dedupe did not run" in said, said
    assert "filed anyway" in said, said


def test_a_query_that_answered_is_not_reported_as_one_that_did_not(tmp_path,
                                                                   target,
                                                                   harness):
    """The control for the sentence above: it is said where it is true."""
    outcome = filing(a_brief(), target, harness,
                     command=producer.answering_query(tmp_path))

    assert outcome.dedupe_ran
    said = report_text(outcome).lower()
    assert "dedupe did not run" not in said, said
    assert "filed anyway" not in said, said


def test_a_brief_refused_above_the_query_is_not_reported_as_a_failed_dedupe(
        target, harness):
    """A check that never ran did not fail, and is not named as the reason."""
    for refused in (a_brief(workflow=UNDEFINED_WORKFLOW),
                    {name: value for name, value in a_brief().items()
                     if name != "slug"}):
        outcome = filing(refused, target, harness)
        assert not outcome.dedupe_asked, outcome
        said = report_text(outcome).lower()
        assert "dedupe" not in said, said


# ==========================================================================
# A drop is a drop and never a key
# ==========================================================================


def unwritable_queue(target: Path) -> Path:
    """A file standing where the queue directory belongs.

    The same forcing `tests/test_inspection.py` uses for the other producer, so
    the queue's contract is exercised rather than mocked: `enqueue` cannot
    write and answers with the empty string.
    """
    queue = outbox.queue_dir(target)
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text("a file standing where the queue directory belongs\n",
                     encoding="utf-8")
    return queue


def test_an_enqueue_that_answered_with_the_empty_string_is_reported_as_a_drop(
        target, harness):
    unwritable_queue(target)

    outcome = filing(a_brief(), target, harness)

    assert outcome.outcome == LOST_BY_THE_QUEUE
    assert not outcome.filed
    assert outcome.key == ""

    said = report_text(outcome).lower()
    assert "dropped" in said, said
    assert "filed under key" not in said, said


def test_the_same_brief_is_filed_when_the_queue_can_be_written(target,
                                                               harness):
    """The control for the drop: the loss is the queue's, not the brief's."""
    outcome = filing(a_brief(), target, harness)

    assert outcome.outcome == FILED
    assert outcome.key
    assert "filed under key" in report_text(outcome)


# ==========================================================================
# The entry point: its words, and the status that says the same thing
# ==========================================================================


@pytest.fixture
def document(tmp_path):
    """A writer of brief documents for the entry point to read."""
    def write(brief: dict | str, name: str = "brief.json") -> Path:
        path = tmp_path / name
        path.write_text(brief if isinstance(brief, str) else json.dumps(brief),
                        encoding="utf-8")
        return path
    return write


def a_shipped_workflow() -> str:
    """A workflow name `main` will accept, derived from what the harness holds.

    `main` resolves the harness beside itself, so an entry-point assertion has
    to name a workflow that root defines. Derived rather than spelled, so
    renaming a shipped definition does not redden an assertion that has nothing
    to say about it.
    """
    defined = harness_config.workflow_names(brief_filing.HARNESS_ROOT)
    assert defined, brief_filing.HARNESS_ROOT
    return defined[0]


def run_entry_point(arguments, target_root: Path, monkeypatch, capsys):
    """`main` driven from inside a target, returning its status and its words."""
    monkeypatch.chdir(target_root)
    status = brief_filing.main([str(one) for one in arguments])
    captured = capsys.readouterr()
    return status, captured.out + captured.err


def test_the_entry_point_exits_zero_when_a_brief_was_enqueued(
        target_root, document, monkeypatch, capsys):
    path = document(a_brief(workflow=a_shipped_workflow()))

    status, said = run_entry_point([path], target_root, monkeypatch, capsys)

    assert status == 0, said
    assert [entry["key"] for entry in entries(target_root)] \
        == [key_of(a_brief(workflow=a_shipped_workflow()))]
    assert "filed under key" in said, said


def test_the_entry_point_exits_non_zero_when_nothing_was_filed(
        target_root, document, monkeypatch, capsys):
    """Every way of not filing the entry point can be driven into, and each
    answers with a status an assist session can read without reading prose."""
    workflow = a_shipped_workflow()
    filed = document(a_brief(workflow=workflow))
    run_entry_point([filed], target_root, monkeypatch, capsys)

    refusals = {
        "already queued": [filed],
        "an undefined workflow": [document(
            a_brief(workflow=UNDEFINED_WORKFLOW, slug="undefined-workflow"),
            name="undefined.json")],
        "a missing field": [document(
            {name: value for name, value in
             a_brief(workflow=workflow).items() if name != "slug"},
            name="incomplete.json")],
        "a document that is not JSON": [document("not a json document\n",
                                                 name="prose.txt")],
        "a document that is not an object": [document("[1, 2, 3]",
                                                      name="array.json")],
        "no document at all": [target_root / "nothing-is-here.json"],
        "no argument": [],
    }
    for description, arguments in refusals.items():
        status, said = run_entry_point(arguments, target_root, monkeypatch,
                                       capsys)
        assert status != 0, (description, said)


def test_the_entry_point_raises_nothing_and_names_what_failed(
        target_root, document, monkeypatch, capsys):
    """A failure to file is a status and a sentence, never a traceback."""
    path = document(a_brief(workflow=UNDEFINED_WORKFLOW))

    status, said = run_entry_point([path], target_root, monkeypatch, capsys)

    assert status != 0
    assert UNKNOWN_WORKFLOW in said, said
    assert UNDEFINED_WORKFLOW in said, said
    for name in harness_config.workflow_names(brief_filing.HARNESS_ROOT):
        assert name in said, (name, said)


def test_the_entry_point_says_nothing_has_reached_a_tracker(
        target_root, document, monkeypatch, capsys):
    """What is bought is durability; the network is somebody else's job."""
    path = document(a_brief(workflow=a_shipped_workflow()))

    _, said = run_entry_point([path], target_root, monkeypatch, capsys)

    assert "tracker" in said.lower(), said


# ==========================================================================
# Nothing this path adds writes to the repository or disturbs what is filed
# ==========================================================================


def tree_snapshot(root: Path, *, skip: Path) -> dict:
    """Every file beneath `root` and its bytes, ignoring git's own directory.

    `skip` is the queue, which is the one thing a filing is entitled to write.
    """
    found = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if ".git" in path.relative_to(root).parts:
            continue
        if skip in path.parents:
            continue
        found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def test_a_filing_writes_nothing_in_the_repository_but_the_queue(target_root,
                                                                 harness):
    """Driven against a target carrying a stories directory, an artifact in it
    and a configuration — the whole shape a filing could disturb — and nothing
    outside the queue is touched. The control is below."""
    queue = outbox.queue_dir(target_root)
    before = tree_snapshot(target_root, skip=queue)

    outcome = filing(a_brief(), target_root, harness)
    assert outcome.outcome == FILED

    assert tree_snapshot(target_root, skip=queue) == before
    assert [entry["key"] for entry in entries(target_root)] == [outcome.key]


def test_the_same_snapshot_reports_a_file_planted_under_the_stories_directory(
        target_root, harness):
    """The control for the absence above.

    A snapshot comparison that reports nothing means nothing until it has been
    shown to speak, so a file is planted where this story must never write and
    the same comparison is required to report it.
    """
    queue = outbox.queue_dir(target_root)
    before = tree_snapshot(target_root, skip=queue)

    stories = target_root / ".harness" / "stories"
    assert stories.is_dir(), stories
    (stories / "story-planted.yaml").write_text("planted\n", encoding="utf-8")

    assert tree_snapshot(target_root, skip=queue) != before


def test_a_suppressed_filing_leaves_the_entry_it_was_suppressed_by_untouched(
        tmp_path, target, harness):
    """Nothing here changes the status of anything already filed."""
    answers = producer.answering_query(tmp_path)
    first = filing(a_brief(), target, harness, command=answers)
    entry_file = outbox.entry_path(outbox.queue_dir(target), first.key)
    landed(target, first.key)
    before = entry_file.read_bytes()

    assert filing(a_brief(), target, harness,
                  command=answers).outcome == ALREADY_FILED_LOCALLY

    assert entry_file.read_bytes() == before


#: What a module would have to name to plan, to stamp a mandate, or to reach a
#: transport of its own. Each is looked for as an imported module name rather
#: than as a substring, so a sentence about planning in a docstring is not read
#: as a call to it.
FORBIDDEN_IMPORTS = ("plan_mandate", "plan_validation", "command_transport",
                     "subprocess", "outbox_sweep")


def imported_modules(source: str) -> set[str]:
    """Every module name a source imports, however it imports it."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


@pytest.mark.parametrize("module", [brief_filing, story_brief],
                         ids=[brief_filing.__name__, story_brief.__name__])
def test_the_new_modules_neither_plan_nor_reach_a_transport_of_their_own(
        module):
    source = Path(module.__file__).read_text(encoding="utf-8")
    imported = imported_modules(source)

    assert imported.isdisjoint(FORBIDDEN_IMPORTS), sorted(
        imported.intersection(FORBIDDEN_IMPORTS))


def test_the_same_scan_reports_a_source_that_imports_one_of_them():
    """The control for the absence above.

    Pointed at a source naming one of the forbidden modules under each import
    form, so the scan is known to see what it is looking for rather than to
    have stopped seeing anything.
    """
    for form in ("import command_transport\n",
                 "import command_transport as transport\n",
                 "from plan_mandate import stamp\n"):
        found = imported_modules(form)
        assert not found.isdisjoint(FORBIDDEN_IMPORTS), form


# ==========================================================================
# The extraction: the names inspection exposed still resolve to what they did
# ==========================================================================

#: The names `orchestration/inspection.py` exposed before this story that the
#: extraction had to leave reachable, and the kind beside them.
MOVED_FUNCTIONS = ("bare_path", "bare_paths", "identity", "payload")

#: The kind both producers file under, as `inspection.py` carried it before the
#: extraction. A literal rather than a value resolved out of the commit graph:
#: an entry already filed by an earlier Inspector run carries this string, so a
#: change to it orphans everything filed to date, and an assertion whose
#: expected value is recovered from wherever the constant currently lives would
#: agree with the change instead of reporting it. It is the one figure here a
#: reader cannot derive from what is beside it.
KIND_BEFORE = "story-brief"

#: Briefs the exposed functions are run over, each with what they answered
#: before the extraction. Chosen for what the bare-path rule and the identity
#: have to decide: a line, a line and a column, a duplicate that survives only
#: one of them, an order that must not matter, whitespace, an empty entry, a
#: brief with no paths at all, and a path a naive suffix rule would eat whole.
#:
#: The expected paths are written here rather than recomputed by a second
#: implementation of the rule, because a second implementation would agree with
#: whatever the first one did.
BOTH_FILES = tuple(sorted((SOURCE_FILE, OTHER_SOURCE_FILE)))
COMPARED_OVER = (
    ({"category": "correctness", "slug": "one-path", "paths": [SOURCE_FILE]},
     (SOURCE_FILE,)),
    ({"category": "correctness", "slug": "a-line",
      "paths": [f"{SOURCE_FILE}:42"]},
     (SOURCE_FILE,)),
    ({"category": "correctness", "slug": "a-line-and-a-column",
      "paths": [f"{SOURCE_FILE}:42:7"]},
     (SOURCE_FILE,)),
    ({"category": "correctness", "slug": "the-same-file-twice",
      "paths": [f"{SOURCE_FILE}:42", SOURCE_FILE]},
     (SOURCE_FILE,)),
    ({"category": "correctness", "slug": "an-order-that-must-not-matter",
      "paths": [OTHER_SOURCE_FILE, SOURCE_FILE]},
     BOTH_FILES),
    ({"category": "correctness", "slug": "the-other-order",
      "paths": [SOURCE_FILE, OTHER_SOURCE_FILE]},
     BOTH_FILES),
    ({"category": "correctness", "slug": "whitespace-and-an-empty-entry",
      "paths": [f"  {SOURCE_FILE}  ", "", "   "]},
     (SOURCE_FILE,)),
    ({"category": "correctness", "slug": "no-paths-at-all"}, ()),
    ({"category": "correctness", "slug": "a-path-that-is-only-digits",
      "paths": ["1234"]},
     ("1234",)),
)


class AScope:
    """What `payload` reads a scope through: its path, and nothing else."""

    def __init__(self, path: str = "src/"):
        self.path = path


def identity_before(brief: dict, paths: tuple[str, ...]) -> dict:
    """What `inspection.identity` answered before the extraction, as a value.

    The four members it carried, spelled out here over the bare paths this
    module states for each input, so the comparison below is against something
    written rather than against a second run of the code under test.
    """
    return {
        "kind": KIND_BEFORE,
        "category": brief["category"],
        "paths": list(paths),
        "slug": brief["slug"],
    }


def payload_before(brief: dict, paths: tuple[str, ...], scope: str) -> dict:
    """What `inspection.payload` answered before the extraction, as a value."""
    return {**brief, "kind": KIND_BEFORE, "scope": scope, "paths": list(paths)}


def test_every_name_the_extraction_moved_is_still_reachable_on_inspection():
    for name in (*MOVED_FUNCTIONS, "KIND"):
        assert hasattr(inspection, name), name


def test_the_kind_is_the_value_it_was():
    assert inspection.KIND == KIND_BEFORE
    assert story_brief.KIND == KIND_BEFORE


@pytest.mark.parametrize("written,expected", COMPARED_OVER,
                         ids=[one["slug"] for one, _ in COMPARED_OVER])
def test_the_moved_functions_answer_what_they_answered_before(written,
                                                              expected):
    """Run over the same inputs and compared against the answers stated above,
    which is what behaviour-preserving means. The control is below."""
    scope = AScope()

    assert inspection.bare_paths(written) == expected
    assert inspection.identity(written) == identity_before(written, expected)
    assert inspection.payload(written, scope) \
        == payload_before(written, expected, scope.path)


#: One path in, one path out, as `bare_path` answered before the extraction.
#: Beside the briefs above rather than folded into them, because a path a brief
#: drops — an empty entry, whitespace — has an answer of its own that no
#: comparison over a brief's deduplicated paths can state.
BARE_PATHS_BEFORE = (
    (SOURCE_FILE, SOURCE_FILE),
    (f"{SOURCE_FILE}:42", SOURCE_FILE),
    (f"{SOURCE_FILE}:42:7", SOURCE_FILE),
    (f"  {SOURCE_FILE}:42  ", SOURCE_FILE),
    ("1234", "1234"),
    (":42", ":42"),
    ("", ""),
    ("   ", ""),
)


@pytest.mark.parametrize("path,expected", BARE_PATHS_BEFORE)
def test_bare_path_answers_what_it_answered_before(path, expected):
    assert inspection.bare_path(path) == expected


def test_the_key_over_each_of_those_is_the_key_it_was():
    """The reading that matters downstream: the same entry, not merely the same
    mapping."""
    for written, expected in COMPARED_OVER:
        assert outbox.identity_key(inspection.identity(written)) \
            == outbox.identity_key(identity_before(written, expected))


def test_the_same_comparison_reports_an_identity_that_changed(tmp_path):
    """The control for the equalities above.

    An identity the module answers with and an identity this module wrote could
    agree because the comparison has stopped discriminating. It is therefore
    pointed at a mutant of the shared module whose identity carries one member
    differently, and it must report the difference.
    """
    mutant = load_mutant(
        Path(story_brief.__file__),
        [('"slug": brief["slug"],', '"slug": brief["slug"] + "-changed",')],
        name="story_brief_with_a_changed_identity", tmp_path=tmp_path)

    written, expected = COMPARED_OVER[0]
    assert mutant.identity(written) != identity_before(written, expected)
    assert outbox.identity_key(mutant.identity(written)) \
        != outbox.identity_key(identity_before(written, expected))


def test_the_shared_module_is_what_inspection_exposes():
    """One derivation beneath both names, rather than two that agree today."""
    assert inspection.bare_path is story_brief.bare_path
    assert inspection.bare_paths is story_brief.bare_paths
    assert inspection.identity is story_brief.identity


# ==========================================================================
# The seam: the launcher hands the shipped plugin to the session
# ==========================================================================


def launching(monkeypatch, argv: list[str]):
    """`l5-assist` run with the exec intercepted, returning module and record.

    The launcher's last act replaces the process, so what it decided is read by
    standing in for that call rather than by reading the source. The
    environment variable it exports is put under `monkeypatch`'s control first,
    so the launcher's own assignment to it is reverted when the test ends
    rather than leaking into the ones that follow.
    """
    recorded: dict = {}
    module = load_script(ASSIST_LAUNCHER, name="l5_assist_under_test")
    monkeypatch.setenv(module.HARNESS_ROOT_VARIABLE,
                       "a value no launcher wrote")
    monkeypatch.setattr(module.os, "execvp",
                        lambda file, args: recorded.update(
                            file=file, args=list(args)))
    monkeypatch.setattr(module.sys, "argv", argv)
    module.main()
    assert recorded, "the launcher exec'd nothing"
    return module, recorded


def launched(monkeypatch, argv: list[str]) -> list[str]:
    """What `l5-assist` would have handed `claude`."""
    return launching(monkeypatch, argv)[1]["args"]


def test_the_launcher_loads_the_shipped_plugin_directory_for_the_session(
        monkeypatch):
    """Loaded for the session rather than installed into the target, which is
    what makes the skill available in any target with nothing put into it."""
    args = launched(monkeypatch, [ASSIST_LAUNCHER])

    assert "--plugin-dir" in args, args
    assert args[args.index("--plugin-dir") + 1] == str(PLUGIN_DIR)


def test_the_directory_the_launcher_loads_is_the_one_carrying_the_skill():
    """The seam is only worth what is behind it."""
    assert PLUGIN_DIR.is_dir(), PLUGIN_DIR
    assert REPO_ROOT in PLUGIN_DIR.parents, PLUGIN_DIR
    assert SKILL_PATH.is_file(), SKILL_PATH
    assert PLUGIN_DIR in SKILL_PATH.parents, SKILL_PATH


def test_the_launcher_still_appends_the_assist_prompt_it_appended(monkeypatch):
    """The control that loading the plugin did not displace what was there.

    A launcher that handed `claude` the plugin and stopped appending the prompt
    would pass the assertion above and start an assist session that is not one.
    """
    args = launched(monkeypatch, [ASSIST_LAUNCHER])

    assert "--append-system-prompt" in args, args
    appended = args[args.index("--append-system-prompt") + 1]
    assert appended == (REPO_ROOT / "prompts" / "assist.md").read_text(
        encoding="utf-8")


def test_the_launcher_tells_the_session_where_the_harness_it_started_from_is(
        monkeypatch):
    """The skill invokes an entry point in the harness, and a session standing
    in a target has no other way to find it."""
    module, _ = launching(monkeypatch, [ASSIST_LAUNCHER])

    exported = os.environ[module.HARNESS_ROOT_VARIABLE]
    assert exported == str(module.HARNESS_ROOT)
    assert (Path(exported) / "orchestration" / ENTRY_POINT).is_file()


def test_a_question_on_the_command_line_still_reaches_the_session(monkeypatch):
    """The launcher's own contract, unchanged by what this story added."""
    args = launched(monkeypatch, [ASSIST_LAUNCHER, "why", "did", "it", "fail"])

    assert args[-1] == "why did it fail"


# ==========================================================================
# The shipped plugin validates, strictly
# ==========================================================================

VALIDATE = ("claude", "plugin", "validate", "--strict")


def validated(directory: Path) -> subprocess.CompletedProcess:
    return subprocess.run([*VALIDATE, str(directory)], capture_output=True,
                          text=True)


@pytest.fixture
def the_validator():
    """The plugin validator, or a skip saying it is not installed.

    A skip rather than a silent pass: an assertion about a command that is not
    there has established nothing, and saying so is the honest report.
    """
    if shutil.which(VALIDATE[0]) is None:
        pytest.skip(f"{VALIDATE[0]} is not on PATH, so the shipped plugin "
                    f"cannot be validated here")
    return VALIDATE


def test_the_shipped_plugin_directory_validates_strictly(the_validator):
    result = validated(PLUGIN_DIR)

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_same_validation_fails_on_a_manifest_this_test_broke(
        the_validator, tmp_path):
    """The control for the pass above.

    A copy of the shipped directory with its manifest emptied of the one field
    a manifest must carry, put through the same command, which must refuse it —
    otherwise a green validation says nothing about the shipped one.
    """
    copy = tmp_path / "broken-plugin"
    shutil.copytree(PLUGIN_DIR, copy)
    manifest = copy / ".claude-plugin" / "plugin.json"
    assert manifest.is_file(), manifest
    original = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(
        json.dumps({name: value for name, value in original.items()
                    if name != "name"}), encoding="utf-8")

    assert validated(copy).returncode != 0


def test_the_manifest_carries_a_name_and_says_what_the_directory_is():
    manifest = json.loads(
        (PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text(
            encoding="utf-8"))

    assert manifest.get("name"), manifest
    assert manifest.get("description"), manifest


# ==========================================================================
# The skill's own prose
# ==========================================================================


def collapsed(text: str) -> str:
    """One line, lowercased, so a phrase is found across a line break."""
    return " ".join(text.split()).lower()


SKILL_SAID = collapsed(SKILL_TEXT)

#: What the skill must state, keyed by what the story requires it to state.
#: Each value is the phrases that must all appear; the key is why.
SKILL_STATES = {
    "the slug names the work rather than the fix or the file":
        ("name neither the fix nor the file",),
    "the slug's shape": ("lowercase", "hyphen-separated"),
    "the paths are bare repository-relative paths":
        ("bare repository-relative paths",),
    "line-level evidence goes in the body, cited as file:line":
        ("file:line", "body"),
    "the developer is shown the brief": ("show it to the developer",),
    "it is filed on their word": ("do not file it until they say so",),
    "the status decides, not the prose": ("exit status", "non-zero"),
    "a drop is a drop": ("never a key",),
}


@pytest.mark.parametrize("requirement", sorted(SKILL_STATES),
                         ids=sorted(SKILL_STATES))
def test_the_skill_states_what_the_filing_procedure_requires(requirement):
    for phrase in SKILL_STATES[requirement]:
        assert phrase in SKILL_SAID, (requirement, phrase)


def test_the_same_searches_report_a_rendering_with_each_statement_removed():
    """The control for the searches above.

    Every phrase is stripped out of a rendering of the skill and the same
    searches are run over it; each must report its absence. A search that has
    stopped seeing anything reports nothing whatever the skill says.
    """
    for requirement, phrases in SKILL_STATES.items():
        stripped = SKILL_SAID
        for phrase in phrases:
            stripped = stripped.replace(phrase, "")
        for phrase in phrases:
            assert phrase not in stripped, (requirement, phrase)


def test_the_skill_names_the_entry_point_and_reports_its_three_suppressions():
    """What a developer reads has to distinguish the three, so the skill that
    writes those words carries all three."""
    assert ENTRY_POINT in SKILL_TEXT, ENTRY_POINT
    for outcome in (ALREADY_FILED, ALREADY_FILED_LOCALLY, ALREADY_QUEUED):
        assert outcome in SKILL_SAID, outcome


def test_the_skill_says_nothing_it_files_plans_or_authorizes_anything():
    """The widening this story makes is bounded, and the skill says so."""
    assert "nothing executes a brief" in SKILL_SAID, SKILL_SAID
    assert "mandate" in SKILL_SAID, SKILL_SAID


def test_the_skill_declares_a_name_and_a_description():
    """A skill with no description is a skill the session never reaches for,
    and one whose description does not say when to use it is one the session
    reaches for at the wrong moment."""
    assert SKILL_NAME, FRONTMATTER
    described = FRONTMATTER.get("description", "").lower()
    assert described, FRONTMATTER
    assert "brief" in described, described
    assert "file" in described, described


# ==========================================================================
# The shipped skill prose is held to the two source rules
# ==========================================================================


def test_the_plugin_directory_is_scanned_and_is_target_facing():
    """Both lists, because the skill is prose an agent is given while it is
    standing in a target: the stack rule and the layout rule both apply."""
    directory = PLUGIN_DIR.relative_to(REPO_ROOT).as_posix()

    assert directory in harness_source.HARNESS_SOURCE_DIRS
    assert directory in harness_source.TARGET_FACING_DIRS


def test_the_shipped_skill_carries_no_stack_token_and_no_layout_path():
    """Asserted over the whole plugin directory rather than the one skill, so a
    second skill shipped later is covered without this module being edited."""
    directory = PLUGIN_DIR.relative_to(REPO_ROOT).as_posix()

    assert [finding for finding in harness_source.scan()
            if finding.path.startswith(f"{directory}/")] == []


def test_both_rules_report_a_violation_planted_in_a_copy_of_that_skill(
        tmp_path):
    """The control for the absence above.

    A throwaway copy of the harness's source directories, with a stack token
    and a target-layout path appended to the shipped skill, put through the
    same scan: both rules must report it at that path. Silence over the shipped
    skill then means the skill is clean rather than that the scan cannot reach
    it.
    """
    root = scan_module.build_throwaway(tmp_path / "throwaway")
    assert (root / SKILL_REL).is_file(), SKILL_REL

    scan_module.append(root, SKILL_REL,
                       "\nthe target is built with gradle and its tests live "
                       "in tests/\n")

    reported = [finding for finding in harness_source.scan(root)
                if finding.path == SKILL_REL]
    assert {finding.rule for finding in reported} == {
        harness_source.STACK_RULE, harness_source.LAYOUT_RULE}, reported


# ==========================================================================
# The assist prompt keeps its brief shape and names where filing happens
# ==========================================================================

ASSIST_PROMPT_REL = "prompts/assist.md"
ASSIST_NOW = (REPO_ROOT / ASSIST_PROMPT_REL).read_text(encoding="utf-8")

#: What the brief shape story-094 and story-098 put in the prompt rests on, and
#: what this story adds beside it. Asserted of the prompt as it reads today
#: rather than against the prompt as it stood at some commit: what the words say
#: is a property of the shipped prompt, and a comparison against a recovered
#: revision would move under a rebase or a squash that says nothing about
#: whether the shape survived.
ASSIST_STATES = {
    "the shape a brief is held to": ("story-brief.schema.json",),
    "the evidentiary standard": ("file:line",),
    "the paths a brief carries": ("bare repository-relative paths",),
    "where filing happens": (SKILL_NAME, "filing"),
}


@pytest.mark.parametrize("requirement", sorted(ASSIST_STATES),
                         ids=sorted(ASSIST_STATES))
def test_the_assist_prompt_states_the_shape_and_names_where_filing_happens(
        requirement):
    """The brief shape stays where it is and the skill joins it."""
    said = collapsed(ASSIST_NOW)

    for phrase in ASSIST_STATES[requirement]:
        assert phrase in said, (requirement, phrase)


def test_the_same_searches_report_a_prompt_with_each_statement_removed():
    """The control for the searches above.

    Every phrase is taken out of a rendering of the prompt and the same
    searches are run over it; each must report its absence. A search that has
    stopped seeing anything reports nothing whatever the prompt says.
    """
    said = collapsed(ASSIST_NOW)

    for requirement, phrases in ASSIST_STATES.items():
        stripped = said
        for phrase in phrases:
            stripped = stripped.replace(phrase, "")
        for phrase in phrases:
            assert phrase not in stripped, (requirement, phrase)
