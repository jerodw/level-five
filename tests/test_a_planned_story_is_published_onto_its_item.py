"""story-126 validation: a planned story is published onto the item it was
planned from.

When a brief is planned, the artifact is written, committed and pushed, and the
tracker item the developer has been watching still shows the brief and nothing
about the plan approved from it. This story gives the harness a way to publish
the planned story onto that item and has `l5-plan` use it. What goes onto the
item is a **projection and not a move**: the artifact in the repository stays
the source of truth and stays what a run reads, and an edit made on the item
reaches nothing.

Written from the acceptance criteria rather than from the implementation, at
three altitudes:

  * **the seam** — `orchestration/item_update.py` — driven directly against
    commands this module wrote. What the command receives, what it is told in
    its environment, what is read back from it and what every way of failing
    comes back as are observations at the command rather than of the source.

  * **the reference implementation** — the item branch of
    `templates/scripts/github.sh` — run as it
    ships against the stub `gh` `tests/test_filed_query.py` wrote, over an item
    that the reference *sync* script filed. So "a second publish replaces the
    first" and "the markers the other two scripts depend on survive" are facts
    about the shipped script rather than about a paraphrase of it.

  * **the planning session** — the real `scripts/l5-plan`, driven end to end
    against a throwaway repository with a stub `claude` on PATH, a fake
    filed-query command and a fake item-update command this module wrote. What
    was published, when, keyed by what, and what a failure to publish cost the
    session are observations of the script.

Every absence asserted here carries a demonstration that it can fail:

  * "a session planned from request text publishes nothing" sits beside the
    same target planned from a brief, which publishes exactly one projection;
  * "no status is sent" sits beside a publish given one, whose document carries
    it;
  * "a rejected plan and a refused push publish nothing" sit beside the
    approved, pushed session that publishes;
  * "what was published is not the text the session wrote" sits beside the
    equality that shows what *was* published — the artifact as committed;
  * "the markers the sync and query scripts wrote survive a publish" is
    asserted against a body observed to carry them beforehand, and the query
    script is then run over the published item and required to still find it;
  * "no run path reaches this module" is a scan shown reporting a planted
    importer and a planted reader.

Nothing here invokes a model, reaches a network or touches a tracker. Every
command driven as an item-update command is a file this module wrote, and the
one `gh` any shipped script sees is the stub on PATH.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import shlex
import time
from pathlib import Path

import pytest

import command_transport
import harness_config
import item_update
import plan_mandate
import story_coordinator

from test_filed_query import (  # noqa: F401 - shared idioms and fixtures
    INTERPRETER,
    LEDGER_VARIABLE,
    QUERY_JOB,
    REPO_ROOT,
    SYNC_JOB,
    TEMPLATES,
    asked,
    A_COLUMN_A_HUMAN_MOVED_IT_TO,
    PROJECT_CONSTANT,
    STATUS_FIELD_CONSTANT,
    TEMPLATE_CONSTANTS,
    THIS_TARGETS_STATUS_FIELD,
    board_environment_for,
    board_items,
    bodies,
    branch_source,
    differences_that_are_not_constant_values,
    in_a_case_the_board_does_not_use,
    sync_constants,
    file_through_the_reference_sync,
    fixture_command,
    fixture_file,
    initialized,
    needs_jq,
    planted_root,
    reference_script,
    sources_importing,
    sources_reading,
    stub_tracker,
)
from test_plan_commit import (  # noqa: F401 - shared idioms
    Planning,
    remote_refs,
    writes,
)
from test_plan_from_a_brief import (  # noqa: F401 - shared idioms
    KEY,
    a_brief,
    answering_command,
    asking,
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

#: What the module says about itself, so this file names no configuration key,
#: no environment variable and no bound of its own.
COMMAND_KEY = item_update.COMMAND_KEY
TIMEOUT_KEY = item_update.TIMEOUT_KEY
KEY_VARIABLE = item_update.KEY_ENVIRONMENT_VARIABLE
DEFAULT_TIMEOUT_SECONDS = item_update.DEFAULT_TIMEOUT_SECONDS
ERROR_TAIL_LENGTH = item_update.ERROR_TAIL_LENGTH

#: Where this repository installs its own item-update command, and therefore
#: the sub-directory and the template it was installed from. Read off this
#: repository's configuration rather than written here, so the assertions below
#: are about the copy this repository actually runs.
#: A configured command is split into words before it is run, so the first
#: word is the path and the rest are the command's own arguments — which since
#: story-130 is the job this one file is being asked for.
CONFIGURED_ITEM = shlex.split(harness_config.load_config(REPO_ROOT)[COMMAND_KEY])
INSTALLED_ITEM = REPO_ROOT / CONFIGURED_ITEM[0]
ITEM_JOB_ARGUMENTS = CONFIGURED_ITEM[1:]
SCRIPTS_DIR = INSTALLED_ITEM.parent.name
TEMPLATE_ITEM = TEMPLATES / SCRIPTS_DIR / INSTALLED_ITEM.name

#: The story a publish is about, and the artifact it publishes. The artifact is
#: prose this module composed rather than a story read from anywhere: what the
#: seam does with a document is not a function of the document being a story.
STORY = "story-417"
ARTIFACT = "story:\n  id: story-417\n  title: A thing that was planned\n"

#: The item a publish is put onto, in a form nothing could resolve: it is not a
#: path in any repository, not a number, and not a URL that resolves. The key
#: is opaque, and a seam that had started deciding something from its form
#: would have to decide something about this one.
ITEM_KEY = "https://tracker.invalid/items/17"

#: How long a command that is meant to be killed is asked to run, and the bound
#: it is given. The sleep is far longer than the bound, so a call that came back
#: can only have come back because the command was killed. The ceiling is far
#: below the sleep and far above the bound, so it separates a kill from a wait
#: without being a stopwatch on a loaded machine.
LONGER_THAN_ANY_BOUND = 45
KILL_BOUND_SECONDS = 1.0
KILL_CEILING_SECONDS = 20.0

#: How long the child a fixture backgrounds sleeps before writing its marker,
#: and how long the tests wait for that marker to appear or fail to.
CHILD_SLEEP_SECONDS = 3
PATIENCE_SECONDS = 20.0


# --------------------------------------------------------------------------
# The fixture commands, each a file this module wrote
# --------------------------------------------------------------------------


def recording(directory: Path, questions: Path, *,
              name: str = "records-the-publish.sh", then: str = "",
              refs_from: Path | None = None) -> Path:
    """A command that records every publish it is asked to make.

    Each invocation writes the document it read on stdin to a file of its own,
    the value of the key variable beside it, and what the remote holds at the
    moment it ran beside that — so "one invocation per artifact", "the two
    spellings of the key agree" and "the push had landed already" are read off
    what the command saw rather than inferred from the call.

    The remote is named by path rather than by remote name, because the name a
    target gives its origin is that target's business and this command is run
    from wherever the seam launches it. A publish driven with no remote to
    watch records an empty refs file, so every question is read the same way.
    """
    questions.mkdir(parents=True, exist_ok=True)
    listing = (f'git ls-remote "{refs_from}" > "$out.refs" 2>/dev/null || :\n'
               if refs_from is not None else ': > "$out.refs"\n')
    return fixture_command(
        directory, name,
        f'out="$(mktemp "{questions}/question.XXXXXX")"\n'
        'cat > "$out"\n'
        f'printf %s "${KEY_VARIABLE}" > "$out.key"\n'
        + listing + then)


def prints_a_page(directory: Path) -> Path:
    """Publishes, and says a great deal about it on stdout, which is one thing
    too many: nothing reads it."""
    return fixture_command(directory, "noisy.sh",
                           "echo 'https://tracker.invalid/items/17#note-4'\n"
                           "echo 'published, and here is the whole body'\n")


def prints_nothing(directory: Path) -> Path:
    return fixture_command(directory, "silent.sh", "exit 0\n")


def exits(directory: Path, code: int, message: str, *,
          name: str | None = None) -> Path:
    return fixture_command(directory, name or f"exits-{code}.sh",
                           f'echo "{message}" >&2\nexit {code}\n')


def says_far_more_than_the_tail(directory: Path) -> Path:
    """A command whose last words are longer than the bound on carrying them."""
    said = fixture_file(directory, "much-to-say.txt",
                        "x" * (ERROR_TAIL_LENGTH * 3), executable=False)
    return fixture_command(directory, "verbose-failure.sh",
                           f'cat "{said}" >&2\nexit 1\n')


def sleeps_forever(directory: Path) -> Path:
    return fixture_command(directory, "sleeps.sh",
                           f"sleep {LONGER_THAN_ANY_BOUND}\n")


def spawns_a_child(directory: Path, marker: Path, *, name: str,
                   then: str) -> Path:
    """A command that backgrounds a child which would outlive it.

    The marker the child writes after sleeping is the question asked twice: a
    child that survived its leader writes it, and a child killed with the group
    never does.
    """
    return fixture_command(
        directory, name,
        f'sh -c \'sleep {CHILD_SLEEP_SECONDS}; echo survived > "{marker}"\' &\n'
        + then)


def unlaunchable(directory: Path) -> str:
    """A path inside a directory this module owns, at which nothing exists."""
    return str(directory / "nothing-was-ever-written-here.sh")


# --------------------------------------------------------------------------
# Driving the seam
# --------------------------------------------------------------------------


def publish(command, tmp_path: Path, *, key: str = ITEM_KEY,
            story_id: str = STORY, document: str | None = None,
            status: str | None = None, **overrides) -> item_update.Published:
    """One publish put to `command`, from a target root this test owns."""
    config = {COMMAND_KEY: str(command), **overrides}
    return item_update.publish(
        key, story_id,
        ARTIFACT if document is None else document,
        config, tmp_path, status)


def questions_asked(questions: Path) -> list[dict]:
    """Every publish a recording command was asked to make, story id first.

    Each document is required to parse whole — `raw_decode` must consume all of
    it — so a second document or a trailing line would be reported rather than
    ignored. The environment's copy of the key and the remote's refs at the
    moment of the call travel beside it.
    """
    found = []
    for path in sorted(questions.iterdir()):
        if path.name.endswith((".key", ".refs")):
            continue
        read = path.read_text(encoding="utf-8")
        document, consumed = json.JSONDecoder().raw_decode(read)
        assert read[consumed:].strip() == "", read
        found.append({
            "document": document,
            "environment_key": Path(f"{path}.key").read_text(encoding="utf-8"),
            "refs": Path(f"{path}.refs").read_text(encoding="utf-8"),
        })
    return sorted(found, key=lambda one: one["document"].get("story_id", ""))


def one_question(questions: Path) -> dict:
    asked_for = questions_asked(questions)
    assert len(asked_for) == 1, asked_for
    return asked_for[0]


# ==========================================================================
# 1. The document the command is handed
# ==========================================================================


def test_the_command_is_handed_the_key_the_story_and_the_projection(tmp_path):
    """Observed at the command rather than inferred from the call.

    The document carries exactly three fields — the item's key, the story id
    and the document to publish — so a fourth would be reported here.
    """
    questions = tmp_path / "questions"
    answer = publish(recording(tmp_path / "fake", questions), tmp_path,
                     document=item_update.projection(STORY, ARTIFACT))

    assert answer.published is True, answer.reason
    document = one_question(questions)["document"]
    assert document == {
        "key": ITEM_KEY,
        "story_id": STORY,
        "document": item_update.projection(STORY, ARTIFACT),
    }


def test_a_status_is_carried_only_where_a_caller_supplied_one(tmp_path):
    """The status half of the contract is declared and unsent.

    A caller that supplies one has it carried, which is the control for the
    absence above and below: nothing in this harness supplies one, so the
    field's absence there is the harness not sending it rather than the seam
    being unable to.
    """
    questions = tmp_path / "questions"
    answer = publish(recording(tmp_path / "fake", questions), tmp_path,
                     status="Planned")

    assert answer.published is True, answer.reason
    document = one_question(questions)["document"]
    assert document["status"] == "Planned"
    assert set(document) == {"key", "story_id", "document", "status"}


#: Keys of shapes a resolution would have to disagree about: a URL, a
#: repository-relative path, a digest, a number, and one carrying a space.
#: Every one of them is a key, and which of them a target's keys are is the
#: invoked command's business rather than the harness's.
OPAQUE_KEYS = (
    "https://tracker.invalid/items/17",
    "docs/decisions/0004-the-parser.md",
    "sha256:2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae",
    "4219",
    "PROJ 4219",
    "../outside/the/target",
)


@pytest.mark.parametrize("key", OPAQUE_KEYS)
def test_the_key_is_sent_exactly_as_it_was_given(key, tmp_path):
    """Nothing resolves it, normalizes it, joins it against the target root or
    decides anything from its form.

    Every shape publishes, and every shape arrives at the command as the string
    it was given: a seam that had joined a key against the root, or checked
    that it named something, would have had to answer differently for at least
    one of these — one of them names nothing, one climbs out of the root, and
    one carries a space.
    """
    questions = tmp_path / "questions"
    answer = publish(recording(tmp_path / "fake", questions), tmp_path,
                     key=key)

    assert answer.published is True, answer.reason
    assert one_question(questions)["document"]["key"] == key


@pytest.mark.parametrize("key", OPAQUE_KEYS)
def test_the_environment_carries_the_key_the_document_carries(key, tmp_path):
    """Both spellings come from one value, so they cannot disagree."""
    questions = tmp_path / "questions"
    publish(recording(tmp_path / "fake", questions), tmp_path, key=key)

    question = one_question(questions)
    assert question["environment_key"] == question["document"]["key"] == key


# ==========================================================================
# 2. What is published
# ==========================================================================


def test_the_projection_is_headed_by_the_story_and_by_what_it_cannot_do():
    """The heading is what a reader meets before the copy.

    It names the story id the copy was taken from, says that the artifact in
    the repository is what runs, and says that an edit made on the item reaches
    nothing — because a reader who believes otherwise will edit the copy and
    expect a run to obey it.
    """
    projected = item_update.projection(STORY, ARTIFACT)

    assert ARTIFACT in projected
    heading = projected[:projected.index(ARTIFACT)]
    assert STORY in heading
    assert re.search(r"(?i)repositor", heading), heading
    assert re.search(r"(?i)\bruns\b", heading), heading
    assert re.search(r"(?i)reaches nothing", heading), heading


def test_the_projection_carries_the_artifact_it_was_given_and_not_another():
    """Derived from its argument rather than read from anywhere.

    Two artifacts differing only in their text project differently, so a
    composition that had gone and read a file would be reported here.
    """
    other = ARTIFACT.replace("A thing that was planned", "Something else")
    assert item_update.projection(STORY, other) != \
        item_update.projection(STORY, ARTIFACT)
    assert other in item_update.projection(STORY, other)
    assert ARTIFACT not in item_update.projection(STORY, other)


# ==========================================================================
# 3. What is read back: an exit code, and nothing else
# ==========================================================================


def test_a_command_that_printed_a_page_publishes_as_one_that_printed_nothing(
        tmp_path):
    """Stdout is not read: no reference is recorded and nothing is parsed."""
    noisy = publish(prints_a_page(tmp_path / "noisy"), tmp_path)
    silent = publish(prints_nothing(tmp_path / "silent"), tmp_path)

    assert noisy == silent
    assert noisy.published is True
    assert noisy.reason == ""


def test_nothing_the_command_printed_on_stdout_is_carried_back(tmp_path):
    """The absence, with the control that shows the same words getting through
    when they are said where the seam does read.

    The marker is printed on stdout by one command and on stderr by another
    that also fails; it reaches the answer only from the second, so its absence
    from the first is stdout going unread rather than a search that would not
    have found it anyway.
    """
    marker = "the-command-said-this"
    printing = fixture_command(tmp_path / "on-stdout", "says.sh",
                               f"echo '{marker}'\n")
    answered = publish(printing, tmp_path)
    assert marker not in json.dumps(dataclasses.asdict(answered))

    said = publish(exits(tmp_path / "on-stderr", 1, marker), tmp_path)
    assert marker in said.reason


#: Exit codes a publish answers the same way for. The transient code is read
#: off the transport that does read one as "try again later", so "no code means
#: retry here" is stated against the harness's own idea of a retryable code
#: rather than against a number written down twice.
EXIT_CODES = (1, 2, 42, command_transport.TRANSIENT_EXIT_CODE)


@pytest.mark.parametrize("code", EXIT_CODES)
def test_no_exit_code_is_read_as_a_retry(code, tmp_path):
    """Every non-zero code is one answer: it did not publish.

    The result carries whether it published and why not, and nothing a caller
    could read as an instruction to try again — there is nothing behind this to
    try again with, because a publish enters no queue.
    """
    answer = publish(exits(tmp_path / f"code-{code}", code, "no"), tmp_path)

    assert answer.published is False
    assert str(code) in answer.reason
    assert {field.name for field in dataclasses.fields(answer)} == \
        {"published", "reason"}


def test_a_failing_commands_own_words_come_back_as_the_reason(tmp_path):
    said = "the item could not be updated"
    answer = publish(exits(tmp_path / "spoke", 3, said), tmp_path)

    assert answer.published is False
    assert said in answer.reason


def test_a_command_that_said_far_more_than_the_bound_is_carried_bounded(
        tmp_path):
    """A tail rather than the whole, so one command cannot fill a terminal.

    Its control is the test above: a short message comes back whole, so what is
    bounded here is the length rather than the reporting.
    """
    answer = publish(says_far_more_than_the_tail(tmp_path / "verbose"),
                     tmp_path)

    assert answer.published is False
    assert len(answer.reason) < ERROR_TAIL_LENGTH * 2
    assert "x" in answer.reason


# ==========================================================================
# 4. The seam raises on nothing
# ==========================================================================


def ways_of_not_publishing(tmp_path: Path) -> dict[str, item_update.Published]:
    """Every way a publish can fail, each driven rather than described.

    That this function returns at all is the whole of the "raises on nothing"
    claim: any of these raising would fail every test below as an error rather
    than as an assertion.
    """
    return {
        "no command configured":
            item_update.publish(ITEM_KEY, STORY, ARTIFACT, {}, tmp_path),
        "a command line that cannot be split":
            publish('publishes --with "an unbalanced quote', tmp_path),
        "a command line that is empty once split":
            publish("   ", tmp_path),
        "a command that could not be launched":
            publish(unlaunchable(tmp_path / "empty"), tmp_path),
        "a command that exited non-zero":
            publish(exits(tmp_path / "refused", 4, "the tracker said no"),
                    tmp_path),
        "a command that ran past its bound":
            publish(sleeps_forever(tmp_path / "slow"), tmp_path,
                    **{TIMEOUT_KEY: str(KILL_BOUND_SECONDS)}),
        "a bound that is not a positive number":
            publish(prints_nothing(tmp_path / "quick"), tmp_path,
                    **{TIMEOUT_KEY: "whenever"}),
    }


def test_every_way_of_not_publishing_is_a_result_carrying_a_reason(tmp_path):
    ways = ways_of_not_publishing(tmp_path)
    for way, answer in ways.items():
        assert isinstance(answer, item_update.Published), way
        assert answer.published is False, way
        assert answer.reason.strip(), way


def test_each_way_of_not_publishing_says_which_way_it_was(tmp_path):
    """A developer told only "it did not publish" cannot tell a command that is
    not configured from one that ran and refused."""
    ways = ways_of_not_publishing(tmp_path)
    reasons = [answer.reason for answer in ways.values()]
    assert len(set(reasons)) == len(reasons), sorted(reasons)

    assert COMMAND_KEY in ways["no command configured"].reason
    assert TIMEOUT_KEY in ways["a bound that is not a positive number"].reason
    assert "whenever" in ways["a bound that is not a positive number"].reason
    assert "4" in ways["a command that exited non-zero"].reason
    assert str(KILL_BOUND_SECONDS) in ways["a command that ran past its bound"].reason


def test_the_seam_prints_nothing_on_any_path(tmp_path, capsys):
    """Every word a developer reads lives in the script; every judgement lives
    here. A module that printed would put one of them in the other's place."""
    ways_of_not_publishing(tmp_path)
    publish(prints_nothing(tmp_path / "published"), tmp_path)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_a_bad_bound_does_not_fall_back_to_the_default(tmp_path):
    """A bound that cannot be read is a bound the target did not declare, and
    obeying the default in its place would obey a number nobody wrote.

    Its control is the unset case beside it, which does take the default and
    does publish.
    """
    for declared in ("whenever", "0", "-1", ""):
        answer = publish(prints_nothing(tmp_path / f"bound-{declared or 'empty'}"),
                         tmp_path, **{TIMEOUT_KEY: declared})
        assert answer.published is False, declared
        assert TIMEOUT_KEY in answer.reason, declared

    assert publish(prints_nothing(tmp_path / "unset"), tmp_path).published


# ==========================================================================
# 5. The invocation is bounded in time, and the kill reaches the group
# ==========================================================================


def test_a_command_that_never_exits_is_killed_at_the_configured_bound(tmp_path):
    """The kill is observed rather than the argument asserted.

    What is measured is how long the call took: the command was asked to sleep
    for far longer than the ceiling, so a call that returned inside it can only
    have returned because the command was killed. A loaded machine makes the
    kill more certain rather than less, so this bounds the operation and not
    the machine.
    """
    started = time.monotonic()
    answer = publish(sleeps_forever(tmp_path / "fake"), tmp_path,
                     **{TIMEOUT_KEY: str(KILL_BOUND_SECONDS)})
    elapsed = time.monotonic() - started

    assert answer.published is False
    assert str(KILL_BOUND_SECONDS) in answer.reason
    assert elapsed < KILL_CEILING_SECONDS, elapsed
    assert elapsed < LONGER_THAN_ANY_BOUND


def test_the_default_bound_is_a_real_duration_rather_than_zero():
    """Zero would be no bound at all, which is the failure a bound exists to
    prevent."""
    assert DEFAULT_TIMEOUT_SECONDS > 0


def test_a_target_that_configures_no_bound_is_held_to_the_default(
        tmp_path, monkeypatch):
    """The default in source is the bound an unconfigured publish runs under.

    Observed rather than read: the constant is moved to a duration a test can
    wait out, no bound is configured, and the command asked to sleep past it is
    killed at *that* number — so a seam that had stopped falling back to the
    constant would run to the sleep and fail here.
    """
    monkeypatch.setattr(item_update, "DEFAULT_TIMEOUT_SECONDS",
                        KILL_BOUND_SECONDS)
    answer = publish(sleeps_forever(tmp_path / "fake"), tmp_path)

    assert answer.published is False
    assert str(KILL_BOUND_SECONDS) in answer.reason


def waited_for(marker: Path, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if marker.exists():
            return True
        time.sleep(0.05)
    return marker.exists()


def test_the_kill_reaches_the_children_the_command_spawned(tmp_path):
    """The command leads a session of its own, so the kill reaches its group.

    The absence is controlled below rather than beside itself: the same child,
    spawned by a command that is not killed, does write its marker — so a
    marker that never appears is a fact about the kill rather than about a
    child that was never going to write one.
    """
    marker = tmp_path / "the-child-survived"
    command = spawns_a_child(tmp_path / "killed", marker,
                             name="killed-leader.sh",
                             then=f"sleep {LONGER_THAN_ANY_BOUND}\n")

    answer = publish(command, tmp_path,
                     **{TIMEOUT_KEY: str(KILL_BOUND_SECONDS)})

    assert answer.published is False
    assert not waited_for(marker, CHILD_SLEEP_SECONDS * 2), \
        "a child of the killed command outlived it"


def test_the_same_child_writes_its_marker_when_its_leader_is_not_killed(
        tmp_path):
    """The control for the absence above."""
    marker = tmp_path / "the-child-survived"
    command = spawns_a_child(tmp_path / "surviving", marker,
                             name="surviving-leader.sh", then="exit 0\n")

    assert publish(command, tmp_path).published is True
    assert waited_for(marker, PATIENCE_SECONDS), \
        "the child never writes its marker, so its absence above proves nothing"


# ==========================================================================
# 6. The seam has one caller, and it is not a run
# ==========================================================================

MODULE = str(Path("orchestration") / "item_update.py")
PLANNING_SCRIPT = str(Path("scripts") / "l5-plan")

#: The filing path this story leaves alone. A publish is a separate invocation
#: and enters no queue, so the module reaches none of these.
THE_QUEUE = ("outbox", "outbox_sweep", "command_transport")


#: Who may reach the seam, each with what earns it. story-126 shipped it with
#: one caller and story-127 gave it a second: the coordinator announces the two
#: run-time moments, so a run does reach it now, and what stays true is that no
#: sweep and no pre-flight does and that a resume announces nothing. Declared
#: rather than asserted loosely, so a third caller has to be added here
#: deliberately.
MAY_REACH_THE_SEAM = {
    PLANNING_SCRIPT: "publishes the projection and moves the item to planned",
    str(Path("orchestration") / "story_coordinator.py"):
        "moves the item at the run's start and at its completion",
}


def test_the_seams_callers_are_the_ones_declared():
    """Only the declared callers reach it, and each of them still does.

    A set equality in both directions, so a caller that is not declared fails
    here and a declared caller that stopped importing fails here too.
    """
    assert sources_importing("item_update", REPO_ROOT) == \
        set(MAY_REACH_THE_SEAM)


def test_the_import_scan_reports_a_caller_when_there_is_one(tmp_path):
    """The control for the absence above, in both import forms."""
    root = planted_root(tmp_path, "a_caller.py",
                        "import item_update\n\n\ndef go(config):\n"
                        "    return item_update.publish('k', 's', 'd', config)\n")
    assert sources_importing("item_update", root) == \
        {str(Path("orchestration") / "a_caller.py")}

    other = planted_root(tmp_path / "from-form", "another_caller.py",
                         "from item_update import publish\n")
    assert sources_importing("item_update", other) == \
        {str(Path("orchestration") / "another_caller.py")}


@pytest.mark.parametrize("key", (COMMAND_KEY, TIMEOUT_KEY))
def test_the_module_is_the_only_source_that_reads_either_key(key):
    assert sources_reading(key, REPO_ROOT) == {MODULE}


@pytest.mark.parametrize("key", (COMMAND_KEY, TIMEOUT_KEY))
def test_the_key_scan_reports_a_second_reader(key, tmp_path):
    """The control: a scan reporting one file is worth nothing until it has
    been shown to report two."""
    root = planted_root(tmp_path / key, "second_reader.py",
                        f'def read(config):\n    return config.get("{key}")\n')
    assert sources_reading(key, root) == \
        {str(Path("orchestration") / "second_reader.py")}


@pytest.mark.parametrize("module", THE_QUEUE)
def test_the_publish_reaches_nothing_in_the_filing_queue(module):
    """It is a separate invocation rather than an entry: the outbox, its
    sweeps and its transport are not on this path at all."""
    assert MODULE not in sources_importing(module, REPO_ROOT)


@pytest.mark.parametrize("module", THE_QUEUE)
def test_the_scan_reports_a_module_that_did_reach_the_queue(module, tmp_path):
    """The control for each absence above."""
    root = planted_root(tmp_path / module, "item_update.py",
                        f"import {module}\n")
    assert sources_importing(module, root) == \
        {str(Path("orchestration") / "item_update.py")}


# ==========================================================================
# 7. The reference implementation, against the stub tracker
#
# The shipped script is the subject here, so it is run as it ships. What it
# talks to is not: `gh` is the stub `tests/test_filed_query.py` wrote, first on
# PATH, so nothing reaches a network. `jq` is the script's own stated
# dependency and is not something this module can stand in for, so these are
# skipped where it is absent.
# ==========================================================================

#: How the script's own story markers are spelled, read off the script rather
#: than written here — the script is what decides where a projection lives, and
#: a test that wrote the marker down would stop testing that.
#: Leading whitespace is allowed because the assignments sit inside the
#: script's document branch since story-127 gave it a status branch beside
#: one; what is read is still the assignment the script makes, indented or not.
BEGIN_ASSIGNMENT = re.compile(r'^[ \t]*begin="(?P<marker>.*)"$', re.MULTILINE)
END_ASSIGNMENT = re.compile(r'^[ \t]*end="(?P<marker>.*)"$', re.MULTILINE)

#: How the sync branch's own markers are spelled. Every marker constant the
#: file declares, whatever it is called, so a marker added to it is one this
#: module requires a publish to leave alone without being edited. Since the
#: three jobs became one file those constants are declared once at the top of
#: it, above every branch, which is what makes reading them one read.
MARKER_CONSTANT = re.compile(
    r'^(?P<name>[A-Z_]*MARKER[A-Z_]*)="(?P<value>[^"]*)"$', re.MULTILINE)


def story_markers(story_id: str) -> tuple[str, str]:
    """The two markers the reference script delimits one story's block with."""
    text = TEMPLATE_ITEM.read_text(encoding="utf-8")
    found = []
    for pattern in (BEGIN_ASSIGNMENT, END_ASSIGNMENT):
        match = pattern.search(text)
        assert match, TEMPLATE_ITEM
        found.append(match.group("marker").replace("${story_id}", story_id))
    assert found[0] != found[1], found
    return found[0], found[1]


def sync_markers() -> list[str]:
    """Every marker the reference sync branch writes into a body."""
    text = TEMPLATE_ITEM.read_text(encoding="utf-8")
    found = [match.group("value")
             for match in MARKER_CONSTANT.finditer(text)]
    assert found, "the script declares no marker constant"
    return found


def test_the_marker_readers_report_what_the_scripts_declare():
    """The control for the two derivations above: markers read out of a script
    that carries none must come back empty rather than silently right."""
    begin, end = story_markers(STORY)
    assert STORY in begin and STORY in end

    assert MARKER_CONSTANT.search("nothing here declares one") is None
    assert BEGIN_ASSIGNMENT.search('other="value"\n') is None


def publish_through_the_reference_script(
        tmp_path: Path, environment: dict, *, key: str, story_id: str,
        document: str,
        script: Path | None = None) -> subprocess.CompletedProcess:
    """One invocation of the shipped item command, whatever it exits."""
    question = {"key": key, "story_id": story_id, "document": document}
    return subprocess.run(
        [INTERPRETER, str(script or TEMPLATE_ITEM), *ITEM_JOB_ARGUMENTS],
        input=json.dumps(question), capture_output=True, text=True, timeout=60,
        cwd=tmp_path,
        env={**environment, KEY_VARIABLE: key})


def an_item_already_filed(tmp_path: Path, environment: dict) -> str:
    """One item filed by the reference *sync* script, with its markers on it.

    The item a projection goes onto in this repository is one the filing path
    made, so that is what these are driven against rather than an empty body.
    """
    return file_through_the_reference_sync(
        tmp_path, environment, key="k-1",
        payload={"title": "the parser drops the last token",
                 "body": "what it says", "paths": ["src/parser.py"]})


@needs_jq
def test_a_publish_puts_the_projection_on_the_item(tmp_path):
    environment, ledger = stub_tracker(tmp_path)
    item = an_item_already_filed(tmp_path, environment)
    projected = item_update.projection(STORY, ARTIFACT)

    result = publish_through_the_reference_script(
        tmp_path, environment, key=item, story_id=STORY, document=projected)

    assert result.returncode == 0, result.stderr
    body = bodies(ledger)[0]
    begin, end = story_markers(STORY)
    assert projected in body
    assert begin in body and end in body


@needs_jq
def test_a_second_publish_for_one_story_replaces_its_projection(tmp_path):
    """Idempotent given the story id: an item that grew a second copy of a
    story each time would be worse than one that had none."""
    environment, ledger = stub_tracker(tmp_path)
    item = an_item_already_filed(tmp_path, environment)
    begin, _ = story_markers(STORY)

    first = item_update.projection(STORY, ARTIFACT)
    revised = item_update.projection(
        STORY, ARTIFACT.replace("A thing that was planned", "Planned again"))
    for document in (first, revised):
        result = publish_through_the_reference_script(
            tmp_path, environment, key=item, story_id=STORY,
            document=document)
        assert result.returncode == 0, result.stderr

    body = bodies(ledger)[0]
    assert body.count(begin) == 1, body
    assert revised in body
    assert "A thing that was planned" not in body


@needs_jq
def test_a_second_storys_projection_lands_beside_the_first(tmp_path):
    """The markers name the story, so one item carries several stories'
    projections side by side rather than one replacing another."""
    environment, ledger = stub_tracker(tmp_path)
    item = an_item_already_filed(tmp_path, environment)
    other = f"{STORY}-b"

    for story_id in (STORY, other):
        result = publish_through_the_reference_script(
            tmp_path, environment, key=item, story_id=story_id,
            document=item_update.projection(story_id, ARTIFACT))
        assert result.returncode == 0, result.stderr

    body = bodies(ledger)[0]
    for story_id in (STORY, other):
        begin, end = story_markers(story_id)
        assert body.count(begin) == 1, body
        assert body.count(end) == 1, body


@needs_jq
def test_publishing_one_document_twice_leaves_the_body_where_it_was(tmp_path):
    """A publish that is repeated changes nothing, so an item that is
    re-published onto does not drift a line at a time."""
    environment, ledger = stub_tracker(tmp_path)
    item = an_item_already_filed(tmp_path, environment)
    projected = item_update.projection(STORY, ARTIFACT)

    bodies_seen = []
    for _ in range(3):
        result = publish_through_the_reference_script(
            tmp_path, environment, key=item, story_id=STORY,
            document=projected)
        assert result.returncode == 0, result.stderr
        bodies_seen.append(bodies(ledger)[0])

    assert bodies_seen[1] == bodies_seen[2]


@needs_jq
def test_the_markers_the_other_two_scripts_depend_on_survive_a_publish(
        tmp_path):
    """Filing, dedupe and brief fetch keep working over a published item.

    The markers are asserted to be on the body *before* the publish as well as
    after it, so their presence afterwards is the publish leaving them alone
    rather than a search that would have found them anywhere.
    """
    environment, ledger = stub_tracker(tmp_path)
    item = an_item_already_filed(tmp_path, environment)

    before = bodies(ledger)[0]
    declared = sync_markers()
    present = [marker for marker in declared if marker in before]
    assert present, declared

    result = publish_through_the_reference_script(
        tmp_path, environment, key=item, story_id=STORY,
        document=item_update.projection(STORY, ARTIFACT))
    assert result.returncode == 0, result.stderr

    after = bodies(ledger)[0]
    for marker in present:
        assert marker in after, marker


@needs_jq
def test_the_query_script_still_finds_an_item_that_was_published_onto(
        tmp_path):
    """The family, driven end to end: filed by one script, published onto by
    the second, and still found by the third.

    The control is the same question asked about a path nothing was filed
    against, which is answered rather than found — so "it was found" is the
    dedupe working rather than the query reporting everything.
    """
    environment, _ = stub_tracker(tmp_path)
    item = an_item_already_filed(tmp_path, environment)

    result = publish_through_the_reference_script(
        tmp_path, environment, key=item, story_id=STORY,
        document=item_update.projection(STORY, ARTIFACT))
    assert result.returncode == 0, result.stderr

    previous = dict(os.environ)
    os.environ.update({name: environment[name]
                       for name in ("PATH", LEDGER_VARIABLE)})
    try:
        answer = asked(reference_script(QUERY_JOB), tmp_path,
                       paths=("src/parser.py",))
        unrelated = asked(reference_script(QUERY_JOB), tmp_path,
                          paths=("src/nothing-is-filed-against-this.py",))
    finally:
        os.environ.clear()
        os.environ.update(previous)

    assert answer.answered is True, answer.reason
    assert [one.key for one in answer.items] == [item]
    assert unrelated.answered is True, unrelated.reason
    assert unrelated.items == ()


@needs_jq
def test_a_publish_onto_an_item_that_cannot_be_read_publishes_nothing(
        tmp_path):
    """The script's own failure path: it exits non-zero and says why, which is
    the whole of what the harness reads back from it."""
    environment, ledger = stub_tracker(tmp_path)
    an_item_already_filed(tmp_path, environment)
    before = bodies(ledger)[0]

    result = publish_through_the_reference_script(
        tmp_path, environment, key="https://tracker.invalid/issues/404",
        story_id=STORY, document=item_update.projection(STORY, ARTIFACT))

    assert result.returncode != 0
    assert result.stderr.strip()
    assert bodies(ledger)[0] == before


# ==========================================================================
# 8. The command is installed, and this repository runs its own copy
# ==========================================================================


def test_a_freshly_initialized_target_carries_the_item_command(initialized):
    """Installed as the same file the sync and query commands are, executable,
    because the harness launches it as a command rather than reading it.

    A target that got the other two without this one would have filing and
    dedupe with no way to publish what was planned from what they filed, and
    one file answering all three is what makes that state unreachable.
    """
    templates = sorted((TEMPLATES / SCRIPTS_DIR).glob("*.sh"))
    assert templates, "the harness ships no reference tracker command"

    installed = initialized / ".harness" / SCRIPTS_DIR
    assert installed.is_dir(), SCRIPTS_DIR
    assert sorted(path.name for path in installed.iterdir()) == \
        [path.name for path in templates]
    for template in templates:
        copy = installed / template.name
        assert copy.read_bytes() == template.read_bytes()
        assert os.access(copy, os.X_OK)


def test_a_freshly_initialized_target_publishes_nothing_until_it_says_to(
        initialized):
    """Installing the command files nothing, asks nothing and publishes
    nothing: a target opts in by naming it, and one that has not named it is a
    target the seam declines to publish from.

    The control is every publish above, which names one and does publish.
    """
    config = harness_config.load_config(initialized)
    assert COMMAND_KEY not in config
    assert item_update.publish(
        ITEM_KEY, STORY, ARTIFACT, config, initialized).published is False


def test_this_repository_runs_the_copy_it_ships():
    """The reference implementation is exercised by the repository that ships
    it, which is what stops the template being a file nobody ever runs.

    Byte identity is deliberately no longer what is asserted. There is no
    separate item script now: the file this repository publishes through is the
    one it files through, and that one carries the project this deployment
    files against — which is exactly what a template must not carry. So what is
    asserted is the shape of the difference, the comparison the sync half
    already made: every line the two do not share is one of the editable
    constant assignments at the top.
    """
    assert INSTALLED_ITEM.is_file()
    assert os.access(INSTALLED_ITEM, os.X_OK)

    template = TEMPLATE_ITEM.read_text(encoding="utf-8")
    installed = INSTALLED_ITEM.read_text(encoding="utf-8")
    assert installed != template, \
        "the installed copy sets no value of its own, so it publishes nowhere"
    assert differences_that_are_not_constant_values(template, installed) == []


def test_that_comparison_reports_a_difference_that_is_not_a_constant():
    """The control: the same predicate over a copy of the template whose
    difference is a line of mechanics rather than a value.

    Rendered here rather than written to the tree, so the control is about the
    comparison and not about this repository. Without it, "every difference is
    a constant" would be satisfied just as happily by a comparison that had
    stopped seeing differences at all.
    """
    template = TEMPLATE_ITEM.read_text(encoding="utf-8")
    tampered = template.replace("READY_TO_MERGE_OPTION", "READY_TO_SHIP_OPTION")
    assert tampered != template

    reported = differences_that_are_not_constant_values(template, tampered)
    assert reported, "the comparison sees no difference it should report"
    assert any("READY_TO_SHIP_OPTION" in line for line in reported), reported


# ==========================================================================
# 9. The planning session, driven end to end
# ==========================================================================


@dataclasses.dataclass
class Session:
    """A throwaway target that publishes, and where its publishes are recorded."""

    planning: Planning
    questions: Path
    command: str | None


#: Passed as `command` to build a target that names no item-update command at
#: all, which is a different thing from naming one that fails. The key is left
#: out of the configuration the target is first committed with, rather than
#: removed from it afterwards: the target stands level with its remote from the
#: moment it is built, which is the state `l5-plan`'s base check requires, and a
#: later commit on top of it would leave the session refused for a reason that
#: has nothing to do with publishing.
NAMES_NONE = object()


def a_publishing_target(tmp_path: Path, name: str, *,
                        command: str | object | None = None,
                        **settings) -> Session:
    """A target configured to fetch a brief and to publish what it planned.

    The questions directory is created whether or not anything is published, so
    "nothing was published" is an empty directory rather than a missing one.
    """
    questions = tmp_path / f"questions-{name}"
    questions.mkdir()
    item_dir = tmp_path / f"item-{name}"
    recorder = item_dir / "records-the-publish.sh"
    if command is NAMES_NONE:
        invoked = None
    elif command is None:
        invoked = str(recorder)
    else:
        invoked = command
    planning = asking(
        tmp_path, name,
        answering_command(tmp_path / f"query-{name}", {"brief": a_brief()}),
        **({} if invoked is None else {COMMAND_KEY: invoked}), **settings)
    if command is None:
        # Written after the target has its remote, and at the path already
        # configured above, so the command can list the remote this session
        # pushes to rather than guessing what it is called.
        assert recording(item_dir, questions,
                         refs_from=planning.remote) == recorder
    return Session(planning, questions, invoked)


def plan_session(session: Session, harness: Path, *args: str,
                 artifacts=None,
                 reply: bytes | None = None) -> tuple[int, str]:
    """One planning session on a terminal against `session`'s target.

    The default replies are the three a brief-driven session is asked for, in
    the order the script asks them: the workflow confirmation the brief's own
    workflow is put through, the approval the mandate is stamped from, and the
    offer to run what was committed. A session driven some other way passes the
    replies its own path asks for.
    """
    written = artifacts or ((relative_artifact(), planned(ADDING["name"])),)
    return plan_on_a_terminal(
        session.planning, harness, *args,
        reply=reply if reply is not None else CONFIRMS + APPROVES + DECLINES,
        L5_STUB_WRITE=writes(*written))


def artifact_relative(story_id: str) -> str:
    return f".harness/stories/{story_id}.yaml"


def committed_artifact(planning: Planning, story_id: str,
                       at: str = PLANNED_ID) -> str:
    """One artifact as it was committed, read at the revision the plan landed.

    Read out of the commit rather than off the working tree, because what a
    projection must carry is what the run will read.
    """
    repository, revision = planning.planned_in(at)
    result = subprocess.run(
        ["git", "-C", str(repository), "show",
         f"{revision}:{artifact_relative(story_id)}"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def publishing(tmp_path) -> Session:
    return a_publishing_target(tmp_path, "publishes")


def test_a_brief_driven_session_publishes_what_it_committed(
        publishing, planning_harness):
    """One publish, carrying the artifact as committed.

    The artifact the session wrote carried no mandate; the artifact that was
    committed carries the one this process stamped. What was published is
    equal to the projection of the committed text, so the read happens after
    the stamp and after the commit rather than reusing what the session first
    wrote.
    """
    status, output = plan_session(publishing, planning_harness, "--brief", KEY)
    assert status == 0, output

    question = one_question(publishing.questions)["document"]
    committed = committed_artifact(publishing.planning, PLANNED_ID)
    assert question["key"] == KEY
    assert question["story_id"] == PLANNED_ID
    assert question["document"] == item_update.projection(PLANNED_ID, committed)

    # What was published is the committed artifact and not the text the
    # session wrote: the projection of the one differs from the projection of
    # the other, and it is the committed one that was sent. The mandate is the
    # difference — the session wrote none and the commit carries the one this
    # process stamped — so a publish that had reused the pre-stamp text would
    # be equal to the second of these instead of the first.
    assert f"{plan_mandate.MANDATE_KEY}:" in question["document"]
    assert question["document"] != \
        item_update.projection(PLANNED_ID, planned(ADDING["name"]))


def test_the_published_key_is_the_one_the_brief_was_fetched_under(
        publishing, planning_harness):
    """The item published onto is the item planned from, in both spellings."""
    status, output = plan_session(publishing, planning_harness, "--brief", KEY)
    assert status == 0, output

    question = one_question(publishing.questions)
    assert question["document"]["key"] == KEY
    assert question["environment_key"] == KEY


def test_a_planning_session_supplies_the_planned_status(publishing,
                                                        planning_harness):
    """The status half rides on the publish this session already makes.

    story-126 asserted here that nothing supplied a status, which story-127
    deliberately supersedes: the claim it stood for — that one item edit
    happens where one happened before — is what the single-question assertion
    below still carries, and what changed is that the one question now says
    which of the three moments this is.
    """
    status, output = plan_session(publishing, planning_harness, "--brief", KEY)
    assert status == 0, output

    document = one_question(publishing.questions)["document"]
    assert document["status"] == item_update.PLANNED
    assert set(document) == {"key", "story_id", "document", "status"}


def test_the_publish_happens_after_the_push_landed(publishing,
                                                   planning_harness):
    """No projection ever names a story that exists only in one clone.

    The command recorded what the remote held at the moment it ran, and the
    story branch is among those refs — so the push had landed before the
    publish was made rather than after it.
    """
    status, output = plan_session(publishing, planning_harness, "--brief", KEY)
    assert status == 0, output

    branch = publishing.planning.planned_branch()
    refs = one_question(publishing.questions)["refs"]
    assert f"refs/heads/{branch}" in refs, refs


def test_the_publish_happens_before_the_run_offer(publishing,
                                                  planning_harness):
    """Done whether or not the developer runs the story now, and said in the
    order it happened: pushed, then published, then offered."""
    status, output = plan_session(publishing, planning_harness, "--brief", KEY)
    assert status == 0, output

    pushed = output.index("l5-plan: pushed ")
    published = output.index(f"published {PLANNED_ID}")
    offered = re.search(r"l5-plan: run .*(now\?|with:)", output)
    assert offered, output
    assert pushed < published < offered.start(), output


def test_a_session_planned_from_request_text_publishes_nothing(
        tmp_path, planning_harness):
    """There is no item to publish onto, so the command is invoked on no path.

    The control is the brief-driven session beside it, on a target built the
    same way, which does publish.
    """
    from_text = a_publishing_target(tmp_path, "from-text")
    status, output = plan_session(
        from_text, planning_harness, "--workflow", ADDING["name"],
        "add a thing", reply=APPROVES + DECLINES)
    assert status == 0, output
    assert questions_asked(from_text.questions) == []

    from_a_brief = a_publishing_target(tmp_path, "from-a-brief")
    status, output = plan_session(from_a_brief, planning_harness,
                                         "--brief", KEY)
    assert status == 0, output
    assert len(questions_asked(from_a_brief.questions)) == 1


SECOND_ID = "story-901"


def test_a_session_that_wrote_two_artifacts_publishes_each_of_them(
        publishing, planning_harness):
    """Keyed by its own story id, because the item's reader wants to see
    everything that was planned from it."""
    status, output = plan_session(
        publishing, planning_harness, "--brief", KEY,
        artifacts=(
            (relative_artifact(), planned(ADDING["name"])),
            (artifact_relative(SECOND_ID),
             story_text(ADDING["name"], story_id=SECOND_ID, mandate=False)),
        ))
    assert status == 0, output

    asked_for = questions_asked(publishing.questions)
    assert [one["document"]["story_id"] for one in asked_for] == \
        [PLANNED_ID, SECOND_ID]
    for one in asked_for:
        story_id = one["document"]["story_id"]
        committed = committed_artifact(publishing.planning, story_id)
        assert one["document"]["document"] == \
            item_update.projection(story_id, committed)


def test_a_plan_that_was_rejected_publishes_nothing(publishing,
                                                    planning_harness):
    """Nothing was committed, so there is nothing a projection could name.

    The control is every session above: the same target, the same brief, and an
    approval instead of a rejection, which publishes.
    """
    status, output = plan_session(publishing, planning_harness,
                                         "--brief", KEY,
                                         reply=CONFIRMS + DECLINES)

    assert status != 0, output
    assert questions_asked(publishing.questions) == []


def test_a_push_that_failed_publishes_nothing(tmp_path, planning_harness):
    """The commit exists in one clone alone, so nothing may name it.

    The remote refuses the story branch and accepts everything else, so the
    reservation still lands and the plan push is what comes back rejected.
    """
    session = a_publishing_target(tmp_path, "unpushed")
    branch = story_coordinator.story_branch(
        harness_config.load_config(session.planning.root), PLANNED_ID)
    hook = session.planning.remote / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        "#!/bin/sh\n"
        "while read old new ref; do\n"
        f'  if [ "$ref" = "refs/heads/{branch}" ]; then\n'
        '    echo "refusing $ref" >&2; exit 1\n'
        "  fi\n"
        "done\n"
        "exit 0\n",
        encoding="utf-8")
    hook.chmod(0o755)

    status, output = plan_session(session, planning_harness, "--brief", KEY)

    assert status != 0, output
    assert questions_asked(session.questions) == []


# --------------------------------------------------------------------------
# A publish that failed refuses nothing
# --------------------------------------------------------------------------


#: Every way a publish can fail from inside a planning session. Named here so
#: the parametrization and the builder below cannot disagree about them, which
#: the test after them holds.
FAILING_WAYS = (
    "no command configured",
    "a command that could not be launched",
    "a command that exited non-zero",
    "a command that ran past its bound",
)


def failing_targets(tmp_path: Path) -> dict[str, Session]:
    """A target for every way a publish can fail, each one otherwise ordinary.

    "Unset" is a target that names no command at all, which is why it is built
    without the key rather than pointed somewhere.
    """
    return {
        "no command configured": a_publishing_target(
            tmp_path, "unset", command=NAMES_NONE),
        "a command that could not be launched": a_publishing_target(
            tmp_path, "unlaunchable",
            command=unlaunchable(tmp_path / "nowhere")),
        "a command that exited non-zero": a_publishing_target(
            tmp_path, "refused",
            command=str(exits(tmp_path / "refusing", 3,
                              "the tracker refused it"))),
        "a command that ran past its bound": a_publishing_target(
            tmp_path, "slow",
            command=str(sleeps_forever(tmp_path / "sleeping")),
            **{TIMEOUT_KEY: str(KILL_BOUND_SECONDS)}),
    }


def test_the_ways_a_publish_can_fail_are_the_ways_a_target_is_built_for(
        tmp_path):
    """Without this, a way removed from the builder would stop being driven and
    nothing would say so."""
    assert sorted(failing_targets(tmp_path)) == sorted(FAILING_WAYS)


@pytest.mark.parametrize("way", FAILING_WAYS)
def test_a_publish_that_failed_changes_no_exit_status_and_unwinds_nothing(
        way, tmp_path, planning_harness):
    """A planning session whose work is committed and pushed is never refused
    for a tracker.

    The commit and the push have already landed by the time the command is
    invoked, and neither is reconsidered on its answer: the exit status is the
    one a successful publish leaves, the artifact is on the branch, and the
    branch is on the remote.
    """
    session = failing_targets(tmp_path)[way]

    status, output = plan_session(session, planning_harness, "--brief", KEY)

    assert status == 0, output
    # Each way really did fail to publish, so a zero status above is a failure
    # refusing nothing rather than a way that quietly published after all. Its
    # control is the ordinary session, built the same way and recording one
    # question.
    assert questions_asked(session.questions) == []
    assert session.planning.planned_paths() == [relative_artifact()]
    branch = session.planning.planned_branch()
    refs = remote_refs(session.planning.remote)
    assert f"refs/heads/{branch}" in refs, refs


def test_a_failed_publish_prints_one_line_naming_the_story_and_the_reason(
        tmp_path, planning_harness):
    """One line, so a failure is legible without being an incident.

    It names the story that was not published, the item it was not published
    onto, and what the command said — a developer told only that something did
    not publish cannot tell a tracker that is down from a command that is not
    configured.
    """
    said = "the tracker refused it"
    session = a_publishing_target(
        tmp_path, "one-line",
        command=str(exits(tmp_path / "refusing", 3, said)))

    status, output = plan_session(session, planning_harness, "--brief", KEY)
    assert status == 0, output

    lines = [line for line in output.splitlines()
             if "did not publish" in line]
    assert len(lines) == 1, output
    assert PLANNED_ID in lines[0]
    assert KEY in lines[0]
    assert said in lines[0]


def test_the_line_a_successful_publish_prints_says_what_it_is(
        publishing, planning_harness):
    """The control for the failure line above, and the whole of what a
    developer is told on the ordinary path: it was published, it is a copy, and
    the artifact in the repository is what runs."""
    status, output = plan_session(publishing, planning_harness, "--brief", KEY)
    assert status == 0, output

    lines = [line for line in output.splitlines()
             if f"published {PLANNED_ID}" in line]
    assert len(lines) == 1, output
    assert KEY in lines[0]
    assert re.search(r"(?i)copy", lines[0]), lines[0]


# ==========================================================================
# 12. What the merge made newly checkable
#
# The item branch resolves a field name through the rule the sync branch
# resolves one through, because there is one rule and one file. Until they were
# merged the item script compared a configured field name against the board's
# verbatim, so a board titled `Status` and a target configuring `status` were
# the same field for one command and different fields for the other.
# ==========================================================================


def move_through_the_reference_script(
        tmp_path: Path, environment: dict, *, key: str, story_id: str,
        status: str, script: Path | None = None,
        extra: dict | None = None) -> subprocess.CompletedProcess:
    """One invocation asking the item branch for a status and no document.

    A status arriving alone rewrites no body, which is what the two run-time
    moments send, so this is the invocation the board claims are about.
    """
    question = {"key": key, "story_id": story_id, "status": status}
    target = script or TEMPLATE_ITEM
    return subprocess.run(
        [INTERPRETER, str(target), *ITEM_JOB_ARGUMENTS],
        input=json.dumps(question), capture_output=True, text=True, timeout=60,
        cwd=tmp_path,
        env={**environment, **board_environment_for(target), **(extra or {}),
             KEY_VARIABLE: key})


@needs_jq
def test_the_item_branch_resolves_a_field_name_the_board_spells_differently(
        tmp_path):
    """A target configuring `status` against a board titled `Status` moves.

    This is the regression the merge removes. story-129 made a field name match
    case-insensitively and changed the sync script; the item script, landed by
    story-127, went on comparing verbatim, so the same board and the same
    configuration disagreed depending on which command was asking — a filing
    landed in its column and every status move the harness asked for reported a
    failure. One rule in one file is what makes the name the sync branch
    resolves a name the item branch resolves.

    The board keeps the field name it is seeded with and only the configured
    name is spelled differently, so what resolves the field can only be the
    comparison rather than a board rewritten to suit it. The control is below.
    """
    environment, ledger = stub_tracker(tmp_path)
    item = an_item_already_filed(tmp_path, environment)
    named = {TEMPLATE_CONSTANTS[STATUS_FIELD_CONSTANT][0]:
             in_a_case_the_board_does_not_use(THIS_TARGETS_STATUS_FIELD)}

    result = move_through_the_reference_script(
        tmp_path, environment, key=item, story_id=STORY,
        status=item_update.IN_PROGRESS, extra=named)

    assert result.returncode == 0, result.stderr
    assert board_items(ledger)[0]["status"] == A_COLUMN_A_HUMAN_MOVED_IT_TO


@needs_jq
def test_that_same_move_fails_where_the_field_name_is_matched_verbatim(
        tmp_path):
    """The control: the item branch as story-127 left it, comparing verbatim.

    Rendered here rather than recovered out of history, so what is shown is the
    comparison rather than a file that has since been deleted: the shared rule
    is replaced by an equality against the board's own name, which is what the
    item script carried, and the same drive must then report a field the
    project does not have and move nothing.
    """
    environment, ledger = stub_tracker(tmp_path)
    item = an_item_already_filed(tmp_path, environment)

    verbatim = tmp_path / "matches-verbatim.sh"
    verbatim.write_text(
        TEMPLATE_ITEM.read_text(encoding="utf-8").replace(
            "select(same_field_name(.name; $name))", "select(.name == $name)"),
        encoding="utf-8")

    named = {TEMPLATE_CONSTANTS[STATUS_FIELD_CONSTANT][0]:
             in_a_case_the_board_does_not_use(THIS_TARGETS_STATUS_FIELD)}
    result = move_through_the_reference_script(
        tmp_path, environment, key=item, story_id=STORY,
        status=item_update.IN_PROGRESS, script=verbatim, extra=named)

    assert result.returncode != 0
    assert "no field named" in result.stderr
    # Nothing was moved: the board reports the column the passing case above
    # reaches for no item at all.
    assert [one for one in board_items(ledger)
            if one.get("status") == A_COLUMN_A_HUMAN_MOVED_IT_TO] == []


def test_this_repositorys_installed_copy_names_a_project_for_the_item_branch():
    """A shipped artifact and the subject: this deployment's own wiring.

    Pointing this deployment at a project was done by editing the sync script's
    installed copy; the item script needed the same value, had no copy of it,
    and so reported a failure for every status move it was asked to make. One
    project constant serving all three branches is what makes that value reach
    the item branch, and it is the behaviour change the merge produced rather
    than the object of it.

    What is asserted is the wiring rather than the number: that the constant is
    declared once and read by both branches. The comparison against a written
    down project number that used to stand here is gone, because the suite now
    reads that value out of this same file — so it was that value compared
    against itself, and it said nothing about whether the item branch can see
    it. That the value is non-empty is asserted where it is read.
    """
    installed = INSTALLED_ITEM.read_text(encoding="utf-8")
    declared = sync_constants(installed)

    # One assignment, so the value the item branch reads and the value the sync
    # branch reads cannot be two values.
    assert len([line for line in installed.splitlines()
                if line.startswith(PROJECT_CONSTANT + "=")]) == 1
    for job in ("do_sync", "do_item"):
        assert PROJECT_CONSTANT in branch_source(installed, job), job
