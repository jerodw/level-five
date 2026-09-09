"""story-127 validation: a story carries its brief's key, and three moments
move the item's status.

A brief filed in a tracker is what a human looks at to know what is happening,
and before this story its status never moved. This story records the brief's
key on the story artifact and has three moments say where the work has got to:
`planned`, on the invocation a planning session already makes; `in_progress`,
at the start of a fresh run; and `ready_to_merge`, at the completion. What a
token *means* stays the invoked command's business — the harness knows a key
and which moment was reached, and reads no status back.

Written from the acceptance criteria rather than from the implementation, at
three altitudes:

  * **the field** — `orchestration/brief_key.py` and the story schema — driven
    directly. What a recording writes, what a second recording leaves, and what
    a parsed story answers are observations of artifacts this module wrote.

  * **the seam** — `orchestration/item_update.py` — put a question carrying a
    status and no document, against commands this module wrote. What the
    command receives and what every way of failing comes back as are read at
    the command rather than off the source.

  * **the three call sites** — the real `scripts/l5-plan` driven end to end
    against a throwaway repository, and real coordinator runs driven through a
    fake agent runner against a target this module built under a workflow it
    built. What was invoked, with what, on which entries, and where each site
    reported are observations of the run.

Every absence asserted here carries a demonstration that it can fail:

  * "a story with no key invokes nothing" sits beside the same target whose
    story carries one, which invokes at both run-time moments;
  * "a target naming no command announces nothing" sits beside the same target
    naming one, which announces at both;
  * "a resumed run announces no fresh start" sits beside the fresh run of the
    same story, which does — once for each of the three interruptions;
  * "the question carries no document field" sits beside the plan-time question
    on the same seam, which carries one;
  * "the completion appends to no file" sits beside a mutant of that same call
    site which appends through the coordinator's own append, and whose run does
    leave the token in events.log and the tree dirty;
  * "one source spells the field name" and "one source spells a status token"
    are scans shown reporting a planted second speller;
  * "a second recording changes no byte" sits beside the first recording of the
    same key, which changes several.

Nothing here invokes a model, reaches a network or touches a tracker. Every
command driven as an item-update command is a file this module wrote, and every
run goes through a fake agent runner.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

import conftest
from agent_runner import AgentResult

import brief_key
import harness_config
import item_update
import schema_validator
import story_coordinator

from test_filed_query import (  # noqa: F401 - shared idioms and fixtures
    FAIL_VARIABLE,
    INTERPRETER,
    REPO_ROOT,
    THIS_TARGETS_PROJECT,
    THIS_TARGETS_PROJECT_OWNER,
    THIS_TARGETS_STATUS_FIELD,
    board_items,
    bodies,
    fixture_command,
    harness_sources,
    ledger_state,
    needs_jq,
    planted_root,
    project_calls,
    stub_tracker,
)
from test_plan_commit import (  # noqa: F401 - shared idioms
    kept_worktree_in,
    writes,
)
from test_plan_from_a_brief import KEY  # noqa: F401 - the key --brief is given
from test_a_planned_story_is_published_onto_its_item import (  # noqa: F401
    COMMAND_KEY,
    TEMPLATE_ITEM,
    an_item_already_filed,
    sync_markers,
    ITEM_KEY,
    KILL_BOUND_SECONDS,
    KEY_VARIABLE,
    OPAQUE_KEYS,
    TIMEOUT_KEY,
    a_publishing_target,
    committed_artifact,
    exits,
    one_question,
    plan_session,
    prints_nothing,
    questions_asked,
    recording,
    sleeps_forever,
    unlaunchable,
)
from test_workflow_proposal import (  # noqa: F401 - fixtures used by name
    ADDING,
    APPROVES,
    CONFIRMS,
    DECLINES,
    PLANNED_ID,
    plan_on_a_terminal,
    planned,
    planning_harness,
    relative_artifact,
    story_text,
)

#: What the modules say about themselves, so this file spells neither the
#: field's name nor any of the three tokens of its own.
FIELD = brief_key.FIELD
TOKENS = item_update.STATUSES

#: Where each module lives, as the scans below report a source.
BRIEF_KEY_MODULE = str(Path("orchestration") / "brief_key.py")
ITEM_UPDATE_MODULE = str(Path("orchestration") / "item_update.py")


# ==========================================================================
# 1. The field on the artifact
# ==========================================================================


#: A story artifact carrying no key, minimal but whole: it parses, it satisfies
#: the schema, and it is what a session planned from typed request text leaves.
#: Composed here rather than read from anywhere, because what a recording does
#: to an artifact is not a function of which story the artifact describes.
ARTIFACT = """\
story:
  id: story-417
  title: A thing that was planned
  description: |
    A stand-in story, so that recording a key has an artifact to record onto.

tasks:
  - do the sample work

acceptance_criteria:
  - the sample behavior exists

scope:
  modify:
    - src/
  do_not_modify:
    - rules/

verification_requirements:
  - confirm the sample behavior

constraints:
  - preserve existing behavior

mandate:
  source:
    kind: human
  conferred_at: 2026-09-09 08:00:00
  conferred_by: A Developer <developer@example.com>
  recorded_by: l5-plan
"""


def an_artifact(tmp_path: Path, name: str = "story-417.yaml",
                text: str = ARTIFACT) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def read_key_of(text: str) -> str:
    """The key a story artifact carries, through the run's own one reading.

    `read_story` is what a run does to an artifact and `key_of` is what the
    call sites ask of that reading, so the two together are the route the
    harness itself takes rather than a second reader written here.
    """
    reading = story_coordinator.read_story(text)
    assert reading.problems == [], reading.problems
    return brief_key.key_of(reading.parsed)


def test_the_story_schema_declares_the_field_as_an_optional_string():
    """Optional and top-level, because an artifact carrying none is ordinary
    and because it is written by appending rather than by insertion."""
    schema = schema_validator.load_schema("story")

    declared = schema["properties"][FIELD]
    assert declared["type"] == "string"
    assert FIELD not in schema.get("required", [])


@pytest.mark.parametrize("key", OPAQUE_KEYS)
def test_a_recorded_key_is_read_back_exactly_as_it_was_given(key, tmp_path):
    """Nothing resolves it, normalizes it or joins it against a root.

    Every shape records and every shape comes back as the string it was given,
    so a recording that had joined a key against a root or checked that it
    named something would have had to answer differently for at least one of
    these: one names nothing, one climbs out of a root, and one carries a
    space.
    """
    path = an_artifact(tmp_path)
    recorded = brief_key.record(path, key)

    assert recorded.recorded is True, recorded.detail
    assert read_key_of(path.read_text(encoding="utf-8")) == key


def test_recording_leaves_every_byte_the_session_wrote(tmp_path):
    """Appended, so the artifact a developer reads afterwards is the one the
    session wrote with one line after it."""
    path = an_artifact(tmp_path)
    before = path.read_text(encoding="utf-8")

    brief_key.record(path, KEY)

    after = path.read_text(encoding="utf-8")
    assert after.startswith(before)
    assert after.strip().splitlines()[-1] == f"{FIELD}: {KEY}"


def test_a_second_recording_changes_the_artifact_on_no_byte(tmp_path):
    """A re-planned artifact keeps the key it was committed with, because a
    recording that moved a committed story from one item to another would be a
    change nobody could see.

    Its control is the first recording beside it, which does change the bytes —
    so "unchanged" is the refusal rather than a writer that never writes.
    """
    path = an_artifact(tmp_path)
    original = path.read_bytes()

    first = brief_key.record(path, KEY)
    once = path.read_bytes()
    assert first.recorded is True
    assert once != original

    second = brief_key.record(path, "https://tracker.invalid/issues/999")

    assert second.recorded is False
    assert second.detail.strip()
    assert path.read_bytes() == once
    assert read_key_of(path.read_text(encoding="utf-8")) == KEY


def test_an_empty_key_records_nothing(tmp_path):
    """There is no item behind it, and a field naming nothing would still be
    enough to make a run invoke a command about it."""
    path = an_artifact(tmp_path)
    original = path.read_bytes()

    recorded = brief_key.record(path, "")

    assert recorded.recorded is False
    assert path.read_bytes() == original


def test_an_artifact_carrying_no_key_still_parses_and_answers_emptily(
        tmp_path):
    """The absence, with the same artifact carrying a key as its control.

    Both are put through the run's own reading: the one with no field parses,
    satisfies the schema and answers the empty string, and the one with the
    field parses, satisfies the schema and answers the key — so the empty
    answer is the field being absent rather than the reading failing.
    """
    assert FIELD not in ARTIFACT
    assert read_key_of(ARTIFACT) == ""

    path = an_artifact(tmp_path)
    brief_key.record(path, KEY)
    assert read_key_of(path.read_text(encoding="utf-8")) == KEY


def sources_spelling(text: str, root: Path) -> set[str]:
    """Which harness sources carry `text` as a string of their own, by path.

    A string constant rather than a mention: a docstring that says the word
    inside a sentence is not a second spelling of it, and a module that names
    the constant it imported is not one either. What this reports is a source
    that wrote the word down for itself, which is the thing two spellings of
    one fact are made of.
    """
    found = set()
    for path in harness_sources(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value == text):
                found.add(str(path.relative_to(root)))
    return found


def test_one_harness_source_spells_the_field_name():
    """The writer and the readers cannot drift about what the field is called,
    because only one of them says what it is called."""
    assert sources_spelling(FIELD, REPO_ROOT) == {BRIEF_KEY_MODULE}


def test_the_spelling_scan_reports_a_second_speller(tmp_path):
    """The control for the absence above: a scan that reports one source is
    worth nothing until it has been shown to report two."""
    root = planted_root(tmp_path, "second_speller.py",
                        f'FIELD = "{FIELD}"\n')

    assert sources_spelling(FIELD, root) == \
        {str(Path("orchestration") / "second_speller.py")}


# ==========================================================================
# 2. The three tokens, and what the harness knows about them
# ==========================================================================


def test_the_three_moments_are_three_distinct_tokens():
    assert TOKENS == (item_update.PLANNED, item_update.IN_PROGRESS,
                      item_update.READY_TO_MERGE)
    assert len(set(TOKENS)) == len(TOKENS)


@pytest.mark.parametrize("token", TOKENS)
def test_one_harness_source_spells_a_status_token(token):
    """The words on the wire are written where the contract is written.

    A source that spelled one for itself would be a source that could branch on
    it, which is what the harness must not do: it knows a key and which moment
    was reached, and what a token means in a tracker is decided in the invoked
    command and nowhere else.
    """
    assert sources_spelling(token, REPO_ROOT) == {ITEM_UPDATE_MODULE}


@pytest.mark.parametrize("token", TOKENS)
def test_the_scan_reports_a_source_that_branches_on_a_token(token, tmp_path):
    """The control for each absence above, in the shape the rule forbids."""
    root = planted_root(
        tmp_path, "branches_on_it.py",
        f'def decide(status):\n    if status == "{token}":\n'
        '        return "the tracker column"\n    return ""\n')

    assert sources_spelling(token, root) == \
        {str(Path("orchestration") / "branches_on_it.py")}


# ==========================================================================
# 3. The seam, asked a question that carries a status and no document
# ==========================================================================


STORY = "story-417"


def ask(command, tmp_path: Path, *, document: str | None = None,
        status: str | None = None, key: str = ITEM_KEY,
        **overrides) -> item_update.Published:
    """One question put to `command` from a target root this test owns."""
    config = {COMMAND_KEY: str(command), **overrides}
    return item_update.publish(key, STORY, document, config, tmp_path,
                               status=status)


def test_a_question_may_carry_a_status_and_no_document(tmp_path):
    """What the two run-time moments send: there is nothing new to publish
    then, and the projection the planning session published has not changed.

    The document field is absent rather than empty, so a command distinguishes
    "publish nothing" from "publish this emptiness"; its control is the
    question beside it, carrying both, where the field is present.
    """
    questions = tmp_path / "status-only"
    answer = ask(recording(tmp_path / "fake", questions), tmp_path,
                 status=item_update.IN_PROGRESS)

    assert answer.published is True, answer.reason
    question = one_question(questions)
    assert question["document"] == {
        "key": ITEM_KEY, "story_id": STORY,
        "status": item_update.IN_PROGRESS,
    }
    assert question["environment_key"] == ITEM_KEY

    both = tmp_path / "both"
    assert ask(recording(tmp_path / "fake-both", both), tmp_path,
               document="a projection",
               status=item_update.PLANNED).published is True
    carried = one_question(both)["document"]
    assert set(carried) == {"key", "story_id", "document", "status"}


def test_a_question_carrying_neither_invokes_nothing_and_says_so(tmp_path):
    """There is nothing to say about that item, so nothing is said to it.

    Its control is the same command asked a question that does carry a status,
    which is invoked — so an empty questions directory here is the refusal
    rather than a command that never records.
    """
    questions = tmp_path / "neither"
    command = recording(tmp_path / "fake", questions)

    answer = ask(command, tmp_path)

    assert answer.published is False
    assert answer.reason.strip()
    assert questions_asked(questions) == []

    assert ask(command, tmp_path,
               status=item_update.READY_TO_MERGE).published is True
    assert len(questions_asked(questions)) == 1


def status_failures(tmp_path: Path) -> dict[str, item_update.Published]:
    """Every way a status can fail to be sent, each driven rather than named.

    That this function returns at all is the whole of the "raises into no
    caller" claim for a question carrying a status: any of these raising would
    fail the tests below as an error rather than as an assertion.
    """
    return {
        "no command configured": item_update.publish(
            ITEM_KEY, STORY, None, {}, tmp_path,
            status=item_update.IN_PROGRESS),
        "a command that could not be launched": ask(
            unlaunchable(tmp_path / "empty"), tmp_path,
            status=item_update.IN_PROGRESS),
        "a command that exited non-zero": ask(
            exits(tmp_path / "refused", 4, "the board said no"), tmp_path,
            status=item_update.IN_PROGRESS),
        "a command that ran past its bound": ask(
            sleeps_forever(tmp_path / "slow"), tmp_path,
            status=item_update.IN_PROGRESS,
            **{TIMEOUT_KEY: str(KILL_BOUND_SECONDS)}),
        "a bound that is not a positive number": ask(
            prints_nothing(tmp_path / "quick"), tmp_path,
            status=item_update.IN_PROGRESS, **{TIMEOUT_KEY: "whenever"}),
        "a command that wrote nothing at all": ask(
            prints_nothing(tmp_path / "silent"), tmp_path,
            status=item_update.IN_PROGRESS),
    }


def test_a_status_that_could_not_be_sent_raises_into_no_caller(tmp_path):
    """Every way comes back as a result carrying its reason, and the reasons
    say which way it was — a caller told only "it was not sent" cannot tell a
    board that is down from a command that is not configured."""
    ways = status_failures(tmp_path)
    for way, answer in ways.items():
        assert isinstance(answer, item_update.Published), way

    silent = ways.pop("a command that wrote nothing at all")
    assert silent.published is True, silent.reason

    for way, answer in ways.items():
        assert answer.published is False, way
        assert answer.reason.strip(), way
    reasons = [answer.reason for answer in ways.values()]
    assert len(set(reasons)) == len(reasons), sorted(reasons)


# ==========================================================================
# 4. The plan-time moment, driven end to end
# ==========================================================================


#: An artifact the plan-time validation refuses, so that where the key is
#: recorded relative to that validation is observable. It is the artifact the
#: stub session writes with one required section taken out of it — a defect the
#: validation owns, which this module makes no claim about beyond its being
#: refused and which the test below establishes by reading the refused text
#: back rather than by trusting the removal.
TASKS_SECTION = "tasks:\n  - do the sample work\n"
REFUSED_ARTIFACT = planned(ADDING["name"]).replace(TASKS_SECTION, "")


@pytest.fixture
def publishing(tmp_path):
    return a_publishing_target(tmp_path, "follows")


def test_the_committed_artifact_carries_the_key_the_brief_was_fetched_under(
        publishing, planning_harness):
    """Byte for byte as it was given, with no resolution and no joining.

    Read out of the commit rather than off the working tree, because the
    artifact carrying the key has to be the one a run will read.
    """
    status, output = plan_session(publishing, planning_harness, "--brief", KEY)
    assert status == 0, output

    committed = committed_artifact(publishing.planning, PLANNED_ID)
    assert read_key_of(committed) == KEY
    assert f"{FIELD}: {KEY}" in committed


def test_a_session_given_request_text_commits_an_artifact_with_no_field(
        tmp_path, planning_harness):
    """The absence, with the brief-driven session on a target built the same
    way as its control: that one commits the field and this one commits no
    field at all, and both artifacts parse and satisfy the schema."""
    from_text = a_publishing_target(tmp_path, "from-text")
    status, output = plan_session(
        from_text, planning_harness, "--workflow", ADDING["name"],
        "add a thing", reply=APPROVES + DECLINES)
    assert status == 0, output

    committed = committed_artifact(from_text.planning, PLANNED_ID)
    assert f"{FIELD}:" not in committed
    assert read_key_of(committed) == ""

    from_a_brief = a_publishing_target(tmp_path, "from-a-brief")
    status, output = plan_session(from_a_brief, planning_harness,
                                  "--brief", KEY)
    assert status == 0, output
    assert read_key_of(
        committed_artifact(from_a_brief.planning, PLANNED_ID)) == KEY


def test_a_rejected_session_records_no_key_and_publishes_nothing(
        publishing, planning_harness):
    """The recording sits below the approval, so an artifact a developer
    rejected is left exactly as the session wrote it."""
    status, output = plan_session(publishing, planning_harness, "--brief", KEY,
                                  reply=CONFIRMS + DECLINES)
    assert status != 0, output

    left = kept_worktree_in(output) / relative_artifact()
    assert FIELD not in left.read_text(encoding="utf-8")
    assert questions_asked(publishing.questions) == []


def test_an_artifact_the_validation_refused_is_left_carrying_the_key(
        publishing, planning_harness):
    """The recording sits above the validation, so what is validated and
    committed is what carries the key.

    Nothing is committed and nothing is published — the refusal is what ends
    the session — and the artifact the developer is left to repair carries the
    key this session was given.
    """
    status, output = plan_session(
        publishing, planning_harness, "--brief", KEY,
        artifacts=((relative_artifact(), REFUSED_ARTIFACT),))
    assert status != 0, output

    left = kept_worktree_in(output) / relative_artifact()
    text = left.read_text(encoding="utf-8")
    assert text.startswith(REFUSED_ARTIFACT)
    assert text.strip().splitlines()[-1] == f"{FIELD}: {KEY}"
    assert questions_asked(publishing.questions) == []
    # The refusal was the validation's rather than this fixture's good luck:
    # the artifact as it was left, stamp and key and all, still has something
    # wrong with it, and the removed section is what it is.
    assert TASKS_SECTION in planned(ADDING["name"])
    assert story_coordinator.read_story(text).problems


def test_the_planning_session_moves_the_item_on_the_publish_it_already_makes(
        publishing, planning_harness):
    """One item edit happens where one happened before: the single question
    carries the projection and the planned token together."""
    status, output = plan_session(publishing, planning_harness, "--brief", KEY)
    assert status == 0, output

    question = one_question(publishing.questions)
    document = question["document"]
    assert document["status"] == item_update.PLANNED
    assert document["key"] == KEY
    assert question["environment_key"] == KEY
    assert set(document) == {"key", "story_id", "document", "status"}


# ==========================================================================
# 5. The two run-time moments
#
# A target this module built, running a workflow this module built, driven by a
# fake agent runner. The shipped workflow is an input to these questions rather
# than their subject: what is being asked is which entries announce and where
# each site reports, and a run under a definition built here answers it without
# turning a deployment decision into something this suite enforces.
# ==========================================================================


WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        outputs=(conftest.TEST_RESULTS, conftest.TESTER_CHANGED_FILES),
        changed_files=conftest.TESTER_CHANGED_FILES,
        schemas={conftest.TEST_RESULTS: "test-results",
                 conftest.TESTER_CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        retry_routing={"implementation-defect": {
            "stage": conftest.StageRef(0),
            "when": "the behaviour the story asked for is missing"}}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="brief-follows-workflow",
)
STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, VERIFYING = STAGE_NAMES

STORY_ID = "story-001"
DEFAULT_BRANCH = "main"

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}
FAIL = {
    "status": "failed",
    "blocking_issues": [{
        "severity": "high",
        "issue": "the sample behavior is missing",
        "location": "src/app.py",
        "required_behavior": "the sample behavior exists",
    }],
    "unverified": [],
    "retry_recommended": False,
    "retry_target": "implementation-defect",
}

TARGET_STORY = f"""\
story:
  id: {STORY_ID}
  title: A story whose brief is followed
  description: |
    A stand-in story used to exercise the two run-time moments
    deterministically.

tasks:
  - do the sample work

acceptance_criteria:
  - the sample behavior exists

scope:
  modify:
    - src/
  do_not_modify:
    - rules/

verification_requirements:
  - confirm the sample behavior

constraints:
  - preserve existing behavior

mandate:
  source:
    kind: human
  conferred_at: 2026-09-09 08:00:00
  conferred_by: A Developer <developer@example.com>
  recorded_by: l5-plan
"""

TARGET_CONFIG = f"""\
workflow: {WORKFLOW['name']}
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: echo tests-ok
tests_dir: tests/
"""


@pytest.fixture
def harness_root(tmp_path) -> Path:
    """A harness root carrying the definition built above, as a repository.

    A real directory a real coordinator loads a real file out of, and a
    repository because an escalation records the harness's revision.
    """
    root = conftest.materialize_workflow(WORKFLOW, tmp_path / "brief-harness")
    conftest.init_repository(root, "the harness this runs out of")
    return root


class Target:
    """A target repository, and where the questions its command records go."""

    def __init__(self, root: Path, questions: Path, command: str | None):
        self.root = root
        self.questions = questions
        self.command = command

    def taken(self) -> list[dict]:
        """Every question asked since the last time this was called.

        Read and then cleared, because the command's path is fixed in the
        target's configuration and several entries of one run's life are driven
        against one target: what each entry announced is what it announced,
        rather than what the target has accumulated.
        """
        asked_for = questions_asked(self.questions)
        for path in self.questions.iterdir():
            path.unlink()
        return asked_for

    def statuses(self) -> list[str]:
        return sorted(question["document"].get("status", "")
                      for question in self.taken())


def a_target(tmp_path: Path, name: str, *, key: str | None = KEY,
             command: str | Path | None = None,
             names_a_command: bool = True, **settings: str) -> Target:
    """A target carrying a story and, unless told otherwise, a command.

    The questions directory is created whether or not anything is invoked, so
    "nothing was invoked" is an empty directory rather than a missing one.
    """
    root = tmp_path / f"target-{name}"
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs", "src", "tests"):
        (root / sub).mkdir(parents=True)

    questions = tmp_path / f"questions-{name}"
    questions.mkdir()
    invoked = None
    if names_a_command:
        invoked = str(command) if command is not None else str(
            recording(tmp_path / f"item-{name}", questions))

    declared = TARGET_CONFIG
    if invoked is not None:
        declared += f"{COMMAND_KEY}: {invoked}\n"
    for setting, value in settings.items():
        declared += f"{setting}: {value}\n"
    (root / ".harness" / "config.yaml").write_text(declared, encoding="utf-8")

    story_path = root / ".harness" / "stories" / f"{STORY_ID}.yaml"
    story_path.write_text(TARGET_STORY, encoding="utf-8")
    if key is not None:
        # Written through the module that owns the field rather than spelled
        # here, so this fixture and the writer cannot disagree about it.
        recorded = brief_key.record(story_path, key)
        assert recorded.recorded, recorded.detail

    (root / ".harness" / "standards" / "coding.md").write_text(
        "# Coding\n- simple\n", encoding="utf-8")
    (root / ".harness" / "standards" / "testing.md").write_text(
        "# Testing\n- test it\n", encoding="utf-8")
    (root / ".harness" / "docs" / "ARCHITECTURE.md").write_text(
        "# Architecture\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (root / "tests" / "test_existing.py").write_text(
        "def test_nothing():\n    assert True\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q", "-b", DEFAULT_BRANCH], cwd=root,
                   check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root,
                   check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root,
                   check=True)
    return Target(root, questions, invoked)


class Runner:
    """A fake agent runner: each stage writes the artifacts it declares, and
    the writing stage also makes an edit in the tree it was handed, so a run
    that reaches its end is a run that produced work."""

    def __init__(self, target: Target, verdicts: list | None = None):
        self.run_dir = conftest.run_dir_for(target.root, STORY_ID)
        self.verdicts = verdicts or [PASS]
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None, run_dir=None):
        tree = Path(cwd) if cwd else None
        self.calls.append(stage)
        if stage == WRITING:
            edited = self.calls.count(stage)
            if tree is not None:
                (tree / "src" / "app.py").write_text(
                    f"print('hello')\nprint('attempt {edited}')\n",
                    encoding="utf-8")
            write_json(self.run_dir / conftest.CHANGED_FILES,
                       {"modified": ["src/app.py"], "created": [],
                        "deleted": []})
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Implemented.\n", encoding="utf-8")
        elif stage == VALIDATING:
            write_json(self.run_dir / conftest.TEST_RESULTS,
                       {"tests_written": 1})
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES,
                       {"modified": [], "created": [], "deleted": []})
        elif stage == VERIFYING:
            seen = self.calls.count(stage) - 1
            write_json(self.run_dir / conftest.VERIFICATION_RESULT,
                       self.verdicts[min(seen, len(self.verdicts) - 1)])
        return AgentResult(ok=True, result_text=f"{stage} done")


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run(target: Target, harness: Path, *, verdicts: list | None = None,
        coordinator=story_coordinator) -> int:
    return coordinator.run_story(STORY_ID, harness, target.root,
                                 Runner(target, verdicts))


def working_tree(target: Target) -> Path:
    return conftest.run_root_for(target.root, STORY_ID)


def run_dir(target: Target) -> Path:
    return conftest.run_dir_for(target.root, STORY_ID)


def events(target: Target) -> str:
    path = run_dir(target) / "events.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def porcelain(tree: Path) -> str:
    return subprocess.run(["git", "-C", str(tree), "status", "--porcelain"],
                          capture_output=True, text=True, check=True).stdout


def lines_naming(text: str, token: str) -> list[str]:
    return [line for line in text.splitlines() if token in line]


# --------------------------------------------------------------------------
# What each entry announces
# --------------------------------------------------------------------------


def test_a_fresh_run_moves_the_item_to_in_progress(tmp_path, harness_root):
    """The key, the story id and the status, and no document field at all:
    there is nothing new to publish at the start of a run, and the projection
    the planning session published is already on the item."""
    target = a_target(tmp_path, "fresh")

    assert run(target, harness_root) == 0

    asked_for = target.taken()
    started = [question for question in asked_for
               if question["document"].get("status") == item_update.IN_PROGRESS]
    assert len(started) == 1, asked_for
    document = started[0]["document"]
    assert document == {"key": KEY, "story_id": STORY_ID,
                        "status": item_update.IN_PROGRESS}
    assert started[0]["environment_key"] == KEY


def test_a_run_that_completes_moves_the_item_to_ready_to_merge(
        tmp_path, harness_root):
    target = a_target(tmp_path, "completes")

    assert run(target, harness_root) == 0

    asked_for = target.taken()
    finished = [question for question in asked_for
                if question["document"].get("status")
                == item_update.READY_TO_MERGE]
    assert len(finished) == 1, asked_for
    document = finished[0]["document"]
    assert document == {"key": KEY, "story_id": STORY_ID,
                        "status": item_update.READY_TO_MERGE}
    assert finished[0]["environment_key"] == KEY


def test_a_fresh_completing_run_announces_those_two_moments_and_no_others(
        tmp_path, harness_root):
    target = a_target(tmp_path, "two-moments")

    assert run(target, harness_root) == 0

    assert target.statuses() == sorted(
        [item_update.IN_PROGRESS, item_update.READY_TO_MERGE])


def test_a_story_carrying_no_key_invokes_nothing_at_either_moment(
        tmp_path, harness_root):
    """A story planned from typed request text names no item, so there is
    nothing for either moment to be about.

    Its control is the same target with the key recorded, whose run announces
    at both moments — so an empty questions directory here is the key's absence
    rather than a command that never records.
    """
    keyless = a_target(tmp_path, "keyless", key=None)

    assert run(keyless, harness_root) == 0
    assert keyless.taken() == []

    keyed = a_target(tmp_path, "keyed")
    assert run(keyed, harness_root) == 0
    assert keyed.statuses() == sorted(
        [item_update.IN_PROGRESS, item_update.READY_TO_MERGE])


def test_a_target_naming_no_command_announces_nothing_and_refuses_nothing(
        tmp_path, harness_root, capsys):
    """Behaving exactly as it did before this story: nothing invoked, no line
    in the run's events and none on stderr.

    Its control is the target beside it that does name a command, whose run
    writes a line in each place.
    """
    silent = a_target(tmp_path, "unnamed", names_a_command=False)

    assert run(silent, harness_root) == 0
    said = capsys.readouterr()
    assert silent.taken() == []
    for token in (item_update.IN_PROGRESS, item_update.READY_TO_MERGE):
        assert lines_naming(events(silent), token) == []
        assert lines_naming(said.err, token) == []

    named = a_target(tmp_path, "named")
    assert run(named, harness_root) == 0
    spoken = capsys.readouterr()
    assert lines_naming(events(named), item_update.IN_PROGRESS)
    assert lines_naming(spoken.err, item_update.READY_TO_MERGE)


# --------------------------------------------------------------------------
# A resumed run announces no fresh start
# --------------------------------------------------------------------------


def a_state_a_run_left(target: Target, status: str, stage: str) -> None:
    """The run directory an interrupted run left, with its state written.

    Hand-written rather than driven, because what a crash and a capacity pause
    leave is a status in state.json and the coordinator infers a resume from
    that and from nothing else.

    What each of them leaves behind the state differs, though, and the
    clean-tree pre-flight reads that difference: a pause commits the run
    directory it left, so a paused tree is clean and anything uncommitted in it
    is the developer's own, while nothing commits when a process dies, so a
    crashed tree holds the run's own unfinished work. Written uncommitted in
    both, the paused resume would be refused for a dirty tree it never had, and
    the announcement this module is about would never be reached.
    """
    directory = conftest.run_directory_a_run_left(target.root, STORY_ID)
    config = harness_config.load_config(target.root)
    story_coordinator.save_state(
        directory,
        story_coordinator.RunState(
            story_id=STORY_ID,
            branch=story_coordinator.story_branch(config, STORY_ID),
            status=status, current_stage=stage),
    )
    if status != "running":
        conftest.commit_setup(conftest.run_root_for(target.root, STORY_ID),
                              "what the pause committed of the run it left")


@pytest.mark.parametrize("status", ("running", "paused"))
def test_a_resumed_run_announces_no_fresh_start(status, tmp_path,
                                                harness_root):
    """A crashed run and a capacity-paused one are continued rather than
    started, and nothing here knows whether the tracker moved the item on
    since.

    The control is the completion in the same run: the resume does reach the
    command, and what it says there is the finish rather than a second start.
    """
    target = a_target(tmp_path, f"resumed-{status}")
    a_state_a_run_left(target, status, VALIDATING)

    assert run(target, harness_root) == 0

    assert target.statuses() == [item_update.READY_TO_MERGE]


def test_an_escalated_resume_announces_no_fresh_start(tmp_path, harness_root):
    """The third interruption, driven rather than written: the run escalates,
    the developer changes something, and the resume finishes it.

    The fresh entry's own announcement is read first, so the resume's silence
    is a fact about the second entry rather than about a target that never
    announced at all.
    """
    target = a_target(tmp_path, "escalated")

    assert run(target, harness_root, verdicts=[FAIL]) == 2
    assert target.statuses() == [item_update.IN_PROGRESS]

    tree = working_tree(target)
    (tree / "src" / "app.py").write_text("print('by hand')\n",
                                         encoding="utf-8")
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tree), "commit", "-q", "--allow-empty",
                    "-m", "what the developer decided to do"], check=True)

    assert run(target, harness_root, verdicts=[PASS]) == 0

    assert target.statuses() == [item_update.READY_TO_MERGE]


# --------------------------------------------------------------------------
# Where each site reports
# --------------------------------------------------------------------------


def test_the_run_start_reports_into_the_run_and_the_completion_onto_stderr(
        tmp_path, harness_root, capsys):
    """Each site reports where it may write, and the difference between them is
    the point: the run's start writes into the run it is reporting on, and the
    completion may write nowhere at all.

    Each half is the other's control — the start's token is in the events and
    not on stderr, the completion's is on stderr and not in the events — so
    neither absence is a search that would have found nothing anywhere.
    """
    target = a_target(tmp_path, "sinks")

    assert run(target, harness_root) == 0
    said = capsys.readouterr()

    assert lines_naming(events(target), item_update.IN_PROGRESS)
    assert lines_naming(said.err, item_update.IN_PROGRESS) == []
    assert lines_naming(said.err, item_update.READY_TO_MERGE)
    assert lines_naming(events(target), item_update.READY_TO_MERGE) == []


def test_the_completion_appends_to_no_file_and_leaves_the_tree_as_it_found_it(
        tmp_path, harness_root):
    """A line written after the completion commit leaves the tree dirty for the
    next run's clean-tree pre-flight to refuse on.

    The tree a run announcing at the completion ends on is compared with the
    tree a run announcing nothing ends on, so what is asserted is that the
    announcement cost nothing rather than that a run leaves a clean tree; and
    every file the run wrote is searched for the token, so "appended nowhere"
    covers the execution history and the events alike.
    """
    announcing = a_target(tmp_path, "announcing")
    silent = a_target(tmp_path, "silent-tree", names_a_command=False)

    assert run(announcing, harness_root) == 0
    assert run(silent, harness_root) == 0

    assert porcelain(working_tree(announcing)) == porcelain(
        working_tree(silent))

    for path in sorted(run_dir(announcing).rglob("*")):
        if path.is_file():
            assert item_update.READY_TO_MERGE not in path.read_text(
                encoding="utf-8", errors="replace"), path


def completion_that_appends(tmp_path):
    """The completion site reporting through the coordinator's own append.

    The control for the absence above, and the shape the rule forbids: the same
    site, saying the same thing, into the run directory instead of onto stderr.
    """
    return conftest.load_mutant(
        Path(story_coordinator.__file__),
        [(
            "    if moved is not None:\n        print(\n",
            "    if moved is not None:\n"
            "        append_event(\n"
            "            run_dir,\n"
            "            f\"item status {item_update.READY_TO_MERGE} noted\",\n"
            "            kind=\"note\",\n"
            "        )\n"
            "        print(\n",
        )],
        name="coordinator_appending_at_the_completion", tmp_path=tmp_path)


def test_a_completion_that_appended_would_be_seen(tmp_path, harness_root):
    """The control: the token reaches the events, and the tree the run ends on
    is no longer the tree a silent run ends on."""
    target = a_target(tmp_path, "appending")
    silent = a_target(tmp_path, "silent-control", names_a_command=False)

    assert run(target, harness_root,
               coordinator=completion_that_appends(tmp_path)) == 0
    assert run(silent, harness_root) == 0

    assert lines_naming(events(target), item_update.READY_TO_MERGE)
    assert porcelain(working_tree(target)) != porcelain(working_tree(silent))


# --------------------------------------------------------------------------
# A status that could not be sent stops nothing
# --------------------------------------------------------------------------


#: Every way the command can fail a run-time moment, each one otherwise
#: ordinary. "No command configured" is absent deliberately: a target naming
#: none invokes nothing and announces nothing, which is the test above rather
#: than a failure.
def failing_targets(tmp_path: Path) -> dict[str, Target]:
    return {
        "a command that could not be launched": a_target(
            tmp_path, "unlaunchable",
            command=unlaunchable(tmp_path / "nowhere")),
        "a command that exited non-zero": a_target(
            tmp_path, "refusing",
            command=exits(tmp_path / "refuses", 3, "the board refused it")),
        "a command that ran past its bound": a_target(
            tmp_path, "slow", command=sleeps_forever(tmp_path / "sleeping"),
            **{TIMEOUT_KEY: str(KILL_BOUND_SECONDS)}),
        "a bound that is not a positive number": a_target(
            tmp_path, "unbounded", **{TIMEOUT_KEY: "whenever"}),
    }


def test_a_status_that_could_not_be_sent_fails_no_run(tmp_path, harness_root,
                                                      capsys):
    """A run that finished is finished whether or not its tracker heard.

    Every way is driven through a whole run: the run completes, the start is
    reported in the run's events and the completion on stderr, and each way's
    reason says which way it was — a developer told only that a status was not
    sent cannot tell a board that is down from a bound nobody wrote.
    """
    started, finished = [], []
    for way, target in failing_targets(tmp_path).items():
        assert run(target, harness_root) == 0, way
        said = capsys.readouterr()

        start_lines = lines_naming(events(target), item_update.IN_PROGRESS)
        finish_lines = lines_naming(said.err, item_update.READY_TO_MERGE)
        assert len(start_lines) == 1, (way, events(target))
        assert len(finish_lines) == 1, (way, said.err)
        started.append(start_lines[0])
        finished.append(finish_lines[0])

    assert len(set(started)) == len(started), started
    assert len(set(finished)) == len(finished), finished


# ==========================================================================
# 6. The reference implementation, against the stub tracker
#
# The shipped script is the subject here, so it is run as it ships. What it
# talks to is not: `gh` is the stub `tests/test_filed_query.py` wrote, first on
# PATH, over a board that stub seeded, so nothing reaches a network. `jq` is
# the script's own stated dependency and is not something a test can stand in
# for, so these are skipped where it is absent.
# ==========================================================================


#: How the script declares a value it takes from the environment, and how it
#: says which option a token names. Read off the script rather than written
#: here for the reason its markers are: the script is what decides which board
#: value is a configuration and what each token means, and a test that wrote
#: those names down would stop testing that.
ENVIRONMENT_ASSIGNMENT = re.compile(
    r'^(?P<constant>[A-Z_]+)="\$\{(?P<variable>[A-Z_0-9]+):-(?P<default>[^}]*)\}"',
    re.MULTILINE)
TOKEN_CASE = re.compile(
    r'^[ \t]*(?P<token>[a-z_]+)\)[ \t]*option="\$(?P<constant>[A-Z_]+)"',
    re.MULTILINE)


def script_configuration() -> tuple[dict[str, str], dict[str, str]]:
    """What the script reads from the environment, and which variable each
    token's option comes out of.

    Returns the constants by name, each mapped to the variable it is read
    from, and the three tokens mapped to those variables — so a board that
    spells its columns differently is a configuration here exactly as the
    script's header says it is.
    """
    text = TEMPLATE_ITEM.read_text(encoding="utf-8")
    constants = {match.group("constant"): match.group("variable")
                 for match in ENVIRONMENT_ASSIGNMENT.finditer(text)}
    tokens = {match.group("token"): constants[match.group("constant")]
              for match in TOKEN_CASE.finditer(text)}
    return constants, tokens


def test_the_script_knows_an_option_for_each_of_the_three_tokens():
    """And for no others: what the script knows about a token is which column
    it names, so the vocabulary the harness sends and the vocabulary the
    reference honours are the same vocabulary."""
    _, tokens = script_configuration()

    assert set(tokens) == set(TOKENS)


def test_the_configuration_readers_report_what_the_script_declares():
    """The control for the two derivations above: read out of text declaring
    none, they must come back empty rather than silently right."""
    constants, tokens = script_configuration()
    assert constants and tokens

    assert ENVIRONMENT_ASSIGNMENT.search('OTHER="a literal"\n') is None
    assert TOKEN_CASE.search('  planned) something_else="$PLANNED_OPTION"\n') \
        is None


def board_status_options(ledger: Path) -> list[str]:
    """The options the stub's board offers on its Status field, in order."""
    project = ledger_state(ledger)["projects"][
        f"{THIS_TARGETS_PROJECT_OWNER}/{THIS_TARGETS_PROJECT}"]
    field = next(one for one in project["fields"]
                 if one["name"] == THIS_TARGETS_STATUS_FIELD)
    return [option["name"] for option in field["options"]]


def board_environment(ledger: Path) -> tuple[dict, dict]:
    """What to tell the script about the stub's board, and which option each
    token then names on it.

    The three tokens are pointed at three of the board's own options, in the
    order the board offers them, so this module names no column of its own and
    a script that had confused two tokens would be moving an item to the wrong
    one of three rather than to the only one there is.
    """
    constants, tokens = script_configuration()
    options = dict(zip(TOKENS, board_status_options(ledger)))
    environment = {
        constants["PROJECT"]: THIS_TARGETS_PROJECT,
        constants["PROJECT_OWNER"]: THIS_TARGETS_PROJECT_OWNER,
        constants["STATUS_FIELD"]: THIS_TARGETS_STATUS_FIELD,
    }
    for token, variable in tokens.items():
        environment[variable] = options[token]
    return environment, options


def ask_the_reference_script(tmp_path: Path, environment: dict, *, key: str,
                             document: str | None = None,
                             status: str | None = None,
                             story_id: str = STORY,
                             extra: dict | None = None
                             ) -> subprocess.CompletedProcess:
    """One invocation of the shipped item command, whatever it exits.

    The question carries each half only where this call was given one, exactly
    as the seam composes it, so what the script is put through here is the
    question the harness actually sends.
    """
    question: dict = {"key": key, "story_id": story_id}
    if document is not None:
        question["document"] = document
    if status is not None:
        question["status"] = status
    return subprocess.run(
        [INTERPRETER, str(TEMPLATE_ITEM)],
        input=json.dumps(question), capture_output=True, text=True, timeout=60,
        cwd=tmp_path,
        env={**environment, **(extra or {}), KEY_VARIABLE: key})


@needs_jq
@pytest.mark.parametrize("token", TOKENS)
def test_the_script_moves_the_status_to_the_option_the_token_names(token,
                                                                   tmp_path):
    """Written over whatever the field already said, because the movement is
    the point: an item already in a column is exactly the item a later moment
    has to move out of it."""
    environment, ledger = stub_tracker(tmp_path)
    item = an_item_already_filed(tmp_path, environment)
    board, options = board_environment(ledger)

    result = ask_the_reference_script(tmp_path, {**environment, **board},
                                      key=item, status=token)

    assert result.returncode == 0, result.stderr
    moved = board_items(ledger)
    assert len(moved) == 1, moved
    assert moved[0][THIS_TARGETS_STATUS_FIELD.lower()] == options[token]


@needs_jq
def test_a_status_arriving_alone_leaves_the_body_exactly_as_it_found_it(
        tmp_path):
    """Filing, dedupe and brief fetch keep finding what they wrote.

    The markers are asserted onto the body *before* the invocation, so their
    presence afterwards is the script leaving the body alone rather than a
    search that would have found them anywhere; and the whole body is compared
    rather than the markers alone, so a line added anywhere in it is reported.
    """
    environment, ledger = stub_tracker(tmp_path)
    item = an_item_already_filed(tmp_path, environment)
    board, _ = board_environment(ledger)

    before = bodies(ledger)[0]
    present = [marker for marker in sync_markers() if marker in before]
    assert present, sync_markers()

    result = ask_the_reference_script(tmp_path, {**environment, **board},
                                      key=item, status=item_update.IN_PROGRESS)

    assert result.returncode == 0, result.stderr
    assert bodies(ledger)[0] == before


@needs_jq
def test_a_question_carrying_both_publishes_and_moves(tmp_path):
    """What the planning moment sends: one item edit, doing both.

    Its controls are the two beside it — the status alone, which changes no
    body, and the document alone, which reaches no board.
    """
    environment, ledger = stub_tracker(tmp_path)
    item = an_item_already_filed(tmp_path, environment)
    board, options = board_environment(ledger)
    projected = item_update.projection(STORY, ARTIFACT)

    result = ask_the_reference_script(tmp_path, {**environment, **board},
                                      key=item, document=projected,
                                      status=item_update.PLANNED)

    assert result.returncode == 0, result.stderr
    assert projected in bodies(ledger)[0]
    assert board_items(ledger)[0][THIS_TARGETS_STATUS_FIELD.lower()] == \
        options[item_update.PLANNED]


@needs_jq
def test_a_document_arriving_alone_reaches_no_board(tmp_path):
    """The control for the movement above: a publish with nothing to say about
    status makes no call against the board at all."""
    environment, ledger = stub_tracker(tmp_path)
    item = an_item_already_filed(tmp_path, environment)
    board, _ = board_environment(ledger)
    before = project_calls(ledger)

    result = ask_the_reference_script(
        tmp_path, {**environment, **board}, key=item,
        document=item_update.projection(STORY, ARTIFACT))

    assert result.returncode == 0, result.stderr
    assert project_calls(ledger) == before


@needs_jq
def test_a_board_that_could_not_be_reached_is_said_and_exits_non_zero(
        tmp_path):
    """An item that was not moved must not be reported as one that was.

    Three ways of not reaching it — a board this copy names none of, a token it
    knows no option for, and a board call the tracker refuses — each said on
    stderr with a non-zero exit. Their control is the ordinary move above,
    which exits zero and says nothing.
    """
    environment, ledger = stub_tracker(tmp_path)
    item = an_item_already_filed(tmp_path, environment)
    board, _ = board_environment(ledger)

    ways = {
        "no board is configured": ask_the_reference_script(
            tmp_path, environment, key=item, status=item_update.IN_PROGRESS),
        "a token it knows no option for": ask_the_reference_script(
            tmp_path, {**environment, **board}, key=item,
            status="something-nobody-declared"),
        "a board call the tracker refuses": ask_the_reference_script(
            tmp_path, {**environment, **board}, key=item,
            status=item_update.IN_PROGRESS,
            extra={FAIL_VARIABLE: "item-edit"}),
    }

    for way, result in ways.items():
        assert result.returncode != 0, way
        assert result.stderr.strip(), way
    assert board_items(ledger) == [] or all(
        THIS_TARGETS_STATUS_FIELD.lower() not in one
        for one in board_items(ledger))
