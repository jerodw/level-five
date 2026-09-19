"""A confined stage's necessary edit outside its confinement is routed, not
kept.

The revert check answers one question — does the suite fail with a stage's
governed edits reverted? — and until story-175 a yes was the end of it: the
edits stood, whoever had made them. Whether the stage was *entitled* to make
them is a different question, and the answer is the restriction's rather than
the check's. A stage confined by `may_only_change` whose outside edit the
suite needs has done another stage's work, so the coordinator undoes it by
the same restore the refusal uses, keeps the stage's version under the same
discarded directory, records the check as permitted *and* routed, and says so
under an event kind of its own. A stage restricted by `may_not_create` whose
necessary modification lands under its prefix is maintaining something it may
own, and that permission stands exactly as before.

Driven, not read. The confined workflow is the one
`tests/test_confined_stage_edits.py` builds, and the create-restricted control
is derived from it by swapping the one declaration this module is about, so
the two runs differ in the sense of the restriction and in nothing else. The
target beneath both is that module's: a real suite, so the check's verdict is
whatever the suite does in a clone with the edit restored.

Every absence asserted here carries a demonstration that it can fail:

  * "a create-restricted stage's record carries no `reverted` block and no
    `routed` key" sits beside the identical edit under the confinement, where
    the identical run writes both;
  * "a refused record carries no `routed` key" sits beside the routed record
    over the same path by the same stage, which does;
  * "no routing call site compares a restriction's sense" sits beside a
    snippet that does, which the same search reports, and beside the table
    with one row flipped, which the property and the set of edits both follow;
  * "the verifier's two rewritten passages no longer say what they said" sits
    beside the passages as they stood before this story, constructed here,
    which the same checks report.

Nothing here invokes a model: every run goes through the fake runner
`tests/test_confined_stage_edits.py` provides.
"""
from __future__ import annotations

import copy
import inspect
import json
import re
import subprocess
from pathlib import Path

import pytest

import conftest
import story_coordinator
import test_confined_stage_edits as confined
from test_confined_stage_edits import harness_root, target  # noqa: F401

STORY_ID = confined.STORY_ID
SOURCE_DIR = confined.SOURCE_DIR
CONFINED_TO = confined.CONFINED_TO

#: The path every governed case below lands on, outside the confinement and
#: under the create restriction the control swaps in.
APP = f"{SOURCE_DIR}app.py"

#: The kinds the check's three dispositions append, as the execution-history
#: schema enumerates them.
ROUTED_EVENT = "revert-check-routed"
PERMITTED_EVENT = "revert-check-permitted"
REVERTED_EVENT = "revert-check-reverted"

CONFINED_WORKFLOW = confined.WORKFLOW


def _create_restricted_variant(workflow: dict) -> dict:
    """The same workflow with the confinement swapped for a create restriction
    over the directory the governed path lives in.

    One declaration differs, so a run under this definition governs the same
    path by the same stage through the other sense — which is the only thing
    the control is allowed to change.
    """
    variant = copy.deepcopy(workflow)
    variant["name"] = "create-restricted-stage-workflow"
    stage = next(stage for stage in variant["stages"]
                 if story_coordinator.CONFINEMENT in stage)
    del stage[story_coordinator.CONFINEMENT]
    stage[story_coordinator.CREATE_RESTRICTION] = [SOURCE_DIR]
    return variant


CREATE_WORKFLOW = _create_restricted_variant(CONFINED_WORKFLOW)

#: The governed stage's declarations under each definition, found by the
#: restriction rather than by a name written here.
CONFINED_STAGE = next(stage for stage in CONFINED_WORKFLOW["stages"]
                      if story_coordinator.CONFINEMENT in stage)
CREATE_STAGE = next(stage for stage in CREATE_WORKFLOW["stages"]
                    if story_coordinator.CREATE_RESTRICTION in stage)
GOVERNED = CONFINED_STAGE["name"]
DECLARATION = CONFINED_STAGE["revert_check"]
ARTIFACT = DECLARATION["result"]
DISCARDED = DECLARATION["discarded"]

VERIFIER_PROMPT = conftest.HARNESS_ROOT / "prompts" / "story-verifier.md"


# --------------------------------------------------------------------------
# Fixtures: the control workflow, materialized and selected
# --------------------------------------------------------------------------


@pytest.fixture
def create_harness(tmp_path: Path) -> Path:
    return conftest.materialize_workflow(CREATE_WORKFLOW,
                                         tmp_path / "create-harness")


def select_workflow(target_root: Path, workflow: dict) -> None:
    """Point the target's config at `workflow`, committed, because the run
    works in a worktree cut from the committed base."""
    config = target_root / ".harness" / "config.yaml"
    text = config.read_text(encoding="utf-8")
    replaced = re.sub(r"^workflow: .*$", f"workflow: {workflow['name']}",
                      text, count=1, flags=re.MULTILINE)
    assert replaced != text
    config.write_text(replaced, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=target_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "select the control workflow"],
                   cwd=target_root, check=True)


def history_of(run_dir: Path) -> list[dict]:
    return json.loads((run_dir / "execution-history.json").read_text(
        encoding="utf-8"))


def events_of_kind(run_dir: Path, kind: str) -> list[dict]:
    """History entries of one kind; the schema keys the kind as `event` and
    the wording as `message`."""
    return [entry for entry in history_of(run_dir) if entry.get("event") == kind]


def events_log(run_dir: Path) -> str:
    return (run_dir / "events.log").read_text(encoding="utf-8")


def tree_content(target_root: Path, path: str) -> str:
    return (confined.tree_of(target_root) / path).read_text(encoding="utf-8")


def confinement_wording() -> str:
    (wording,) = [restriction.wording for restriction
                  in story_coordinator.restrictions_on(CONFINED_STAGE)]
    return wording


# --------------------------------------------------------------------------
# The routed disposition, end to end
# --------------------------------------------------------------------------


def test_a_necessary_edit_outside_the_confinement_is_undone_and_the_run_advances(
    target, harness_root,
):
    """The suite fails with the edit reverted, so the check finds it
    necessary — and it is undone anyway, kept, and the run goes on."""
    code, runner = confined.run(target, harness_root,
                                confined.forced_edit_outside)

    assert code == 0
    assert runner.calls == confined.STAGE_NAMES
    # The tree holds what the stage baseline captured; the stage's own version
    # is under the directory the declaration names as discarded.
    assert tree_content(target, APP) == confined.APP_AT_HEAD
    assert (confined.run_dir_of(target) / DISCARDED / APP).read_text(
        encoding="utf-8") == confined.APP_WITH_HELPER
    # And the stage's in-lane work is untouched by the undoing.
    assert tree_content(target, f"{CONFINED_TO}test_shout.py") == confined.TEST_SHOUT


def test_the_routed_record_says_the_edit_was_needed_and_where_it_went(
    target, harness_root,
):
    assert confined.run(target, harness_root,
                        confined.forced_edit_outside)[0] == 0

    record = confined.record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is True
    assert record["exit_code"] != 0          # the suite needed the edit
    assert record["routed"] == confinement_wording()
    assert record["reverted"]["restored"] == [APP]
    assert record["reverted"]["removed"] == []
    assert record["reverted"]["discarded"] == DISCARDED


def test_the_routed_event_names_the_stage_the_paths_and_where_they_were_kept(
    target, harness_root,
):
    """A reader of the log alone sees that a confined stage wrote outside its
    confinement and what became of the edit."""
    assert confined.run(target, harness_root,
                        confined.forced_edit_outside)[0] == 0
    run_dir = confined.run_dir_of(target)

    (event,) = events_of_kind(run_dir, ROUTED_EVENT)
    assert event["stage"] == GOVERNED
    assert APP in event["message"]
    assert DISCARDED in event["message"]
    assert ARTIFACT in event.get("artifacts", [])
    # The same words reach events.log, which is the rendering a reader of the
    # log alone has.
    assert event["message"] in events_log(run_dir)
    # Neither of the other two dispositions was announced for this stage.
    assert [e for e in events_of_kind(run_dir, PERMITTED_EVENT)
            if e.get("stage") == GOVERNED] == []
    assert [e for e in events_of_kind(run_dir, REVERTED_EVENT)
            if e.get("stage") == GOVERNED] == []


def test_the_stages_own_record_is_left_exactly_as_it_wrote_it(target,
                                                              harness_root):
    """The correction sits beside the attestation rather than in it: the
    changed-files record is byte-identical to what the stage wrote, and the
    revert-check record beside it is what carries the undoing."""
    code, runner = confined.run(target, harness_root,
                                confined.forced_edit_outside)
    assert code == 0
    run_dir = confined.run_dir_of(target)

    # The fake runner keeps the record it handed the stage's artifact, and
    # writes it exactly as `confined.write_json` renders it.
    as_written = json.dumps(runner.records[GOVERNED], indent=2) + "\n"
    assert APP in runner.records[GOVERNED]["modified"]
    record_name = CONFINED_STAGE["changed_files"]
    assert (run_dir / record_name).read_bytes() == as_written.encode("utf-8")
    # The control: the correction *is* recorded, so the bytes above being
    # unchanged is the attestation left alone rather than nothing having
    # happened.
    assert "reverted" in confined.record_of(target)


# --------------------------------------------------------------------------
# The two controls: the other sense, and the refusal
# --------------------------------------------------------------------------


def test_the_same_necessary_edit_under_a_create_restriction_still_stands(
    target, create_harness,
):
    """The disposition is the confinement's, not the check's. The identical
    stage making the identical edit, governed through the other sense over
    the directory the path lives in, is permitted and left in the tree with
    no `reverted` block and no `routed` key — and no routed event."""
    select_workflow(target, CREATE_WORKFLOW)
    code, runner = confined.run(target, create_harness,
                                confined.forced_edit_outside)

    assert code == 0
    assert runner.calls == confined.STAGE_NAMES
    record = confined.record_of(target)
    assert record["ran"] is True
    assert record["paths"] == [APP]
    assert record["permitted"] is True
    assert record["exit_code"] != 0
    assert "reverted" not in record
    assert "routed" not in record
    assert tree_content(target, APP) == confined.APP_WITH_HELPER
    assert not (confined.run_dir_of(target) / DISCARDED / APP).exists()
    run_dir = confined.run_dir_of(target)
    assert events_of_kind(run_dir, ROUTED_EVENT) == []
    (event,) = [e for e in events_of_kind(run_dir, PERMITTED_EVENT)
                if e.get("stage") == GOVERNED]
    assert APP in event["message"]


def test_the_same_edit_with_nothing_needing_it_is_still_refused_without_routed(
    target, harness_root,
):
    """The two undoings leave the same tree; the record tells them apart.
    `permitted` false, no `routed` key, and the refusal's own event rather
    than the routed one."""
    code, _ = confined.run(target, harness_root,
                           confined.unforced_edit_outside)

    assert code == 0
    record = confined.record_of(target)
    assert record["permitted"] is False
    assert record["exit_code"] == 0
    assert "routed" not in record
    assert record["reverted"]["restored"] == [APP]
    assert tree_content(target, APP) == confined.APP_AT_HEAD
    run_dir = confined.run_dir_of(target)
    assert events_of_kind(run_dir, ROUTED_EVENT) == []
    assert [e for e in events_of_kind(run_dir, REVERTED_EVENT)
            if e.get("stage") == GOVERNED]


def test_an_edit_inside_the_confinement_is_not_put_to_the_check(target,
                                                                harness_root):
    """Unchanged from before: nothing outside the confinement, so nothing is
    decided and no record is written. Its control is every routed run above,
    which is the same machinery with one path moved outside."""
    assert confined.run(target, harness_root, confined.only_inside)[0] == 0
    assert not (confined.run_dir_of(target) / ARTIFACT).exists()
    assert events_of_kind(confined.run_dir_of(target), ROUTED_EVENT) == []


def test_a_granted_outside_path_is_exempt_from_the_routed_disposition(
    target, harness_root,
):
    """A grant keeps the path out of governed_edits entirely, so the routed
    disposition never sees it: the necessary edit stands, no record is
    written and no event of any disposition is appended. Its control is the
    routed run above, which is this run without the grant."""
    confined.append_to_story(target, confined.grant(APP))
    code, runner = confined.run(target, harness_root,
                                confined.forced_edit_outside)

    assert code == 0
    assert runner.calls == confined.STAGE_NAMES
    run_dir = confined.run_dir_of(target)
    assert not (run_dir / ARTIFACT).exists()
    assert tree_content(target, APP) == confined.APP_WITH_HELPER
    for kind in (ROUTED_EVENT, PERMITTED_EVENT, REVERTED_EVENT):
        assert events_of_kind(run_dir, kind) == []


# --------------------------------------------------------------------------
# Where the decision lives: a per-sense table, not a comparison
# --------------------------------------------------------------------------


def test_every_sense_has_a_routing_row_and_the_confinement_is_the_one_that_routes():
    """Adding a sense means adding a row: the table is keyed exactly as the
    wording table is, so a sense in one and not the other is a KeyError in
    the property rather than a silent default."""
    senses = story_coordinator._SENSES
    table = story_coordinator._ROUTES_NECESSARY_EDITS
    assert set(table) == set(senses)

    for sense in senses:
        (restriction,) = story_coordinator.restrictions_on(
            {"name": GOVERNED, sense: [SOURCE_DIR]})
        assert restriction.sense == sense
        assert restriction.routes_necessary_edits is table[sense]
    (confinement,) = story_coordinator.restrictions_on(CONFINED_STAGE)
    (creating,) = story_coordinator.restrictions_on(CREATE_STAGE)
    assert confinement.routes_necessary_edits is True
    assert creating.routes_necessary_edits is False


def test_the_set_of_edits_carries_the_matched_restrictions_answer(tmp_path):
    """governed_edits asks each matched restriction and carries the routing
    wordings on the set, at whole-set granularity."""
    record_name = CONFINED_STAGE["changed_files"]
    (tmp_path / record_name).write_text(json.dumps(
        {"modified": [APP], "created": [], "deleted": []}), encoding="utf-8")

    confinement = story_coordinator.restrictions_on(CONFINED_STAGE)
    creating = story_coordinator.restrictions_on(CREATE_STAGE)

    routed = story_coordinator.governed_edits(tmp_path, record_name,
                                              confinement, [])
    assert routed.paths == (APP,)
    assert routed.routed == (confinement_wording(),)
    assert routed.routes is True

    standing = story_coordinator.governed_edits(tmp_path, record_name,
                                                creating, [])
    assert standing.paths == (APP,)
    assert standing.routed == ()
    assert standing.routes is False


def test_flipping_the_table_row_flips_the_answer(tmp_path, monkeypatch):
    """The control for the two above: the decision is read off the table, so
    a table with the confinement's row flipped makes the property and the set
    of edits answer the other way — which they could not if either compared
    the sense itself."""
    flipped = dict(story_coordinator._ROUTES_NECESSARY_EDITS)
    flipped[story_coordinator.CONFINEMENT] = False
    flipped[story_coordinator.CREATE_RESTRICTION] = True
    monkeypatch.setattr(story_coordinator, "_ROUTES_NECESSARY_EDITS", flipped)

    record_name = CONFINED_STAGE["changed_files"]
    (tmp_path / record_name).write_text(json.dumps(
        {"modified": [APP], "created": [], "deleted": []}), encoding="utf-8")

    (confinement,) = story_coordinator.restrictions_on(CONFINED_STAGE)
    (creating,) = story_coordinator.restrictions_on(CREATE_STAGE)
    assert confinement.routes_necessary_edits is False
    assert creating.routes_necessary_edits is True
    assert story_coordinator.governed_edits(
        tmp_path, record_name, [confinement], []).routes is False
    assert story_coordinator.governed_edits(
        tmp_path, record_name, [creating], []).routes is True


def _compares_a_sense(source: str) -> bool:
    """Whether a body of source decides anything by a restriction's sense."""
    return bool(re.search(
        r"\.sense\b|\bCONFINEMENT\b|\bCREATE_RESTRICTION\b", source))


def test_no_routing_call_site_compares_a_restrictions_sense():
    """The call site and the function that builds the set both ask the
    restriction rather than its sense. Read from the live functions, which are
    the subject: the assertion is about what this harness ships."""
    for function in (story_coordinator.run_story,
                     story_coordinator.governed_edits,
                     story_coordinator._revert_check_routed):
        assert not _compares_a_sense(inspect.getsource(function)), function


def test_the_sense_search_reports_a_call_site_that_compares_one():
    """The control for the absence above: the same search over a snippet that
    branches on the sense, in each of the spellings it could take."""
    for snippet in ("if restriction.sense == CONFINEMENT:\n    route()\n",
                    "if edits.sense is CREATE_RESTRICTION:\n    stand()\n",
                    "routed = sense in (CONFINEMENT,)\n"):
        assert _compares_a_sense(snippet), snippet
    # And it is quiet over the shape the call site actually uses.
    assert not _compares_a_sense("elif edits.routes:\n    revert()\n")


# --------------------------------------------------------------------------
# The verifier's prompt: the other half of the loop
# --------------------------------------------------------------------------


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _tester_slot_guidance(prompt: str) -> str:
    """The prose introducing the tester's record, between the implementer's
    placeholder and the tester's."""
    start = prompt.index("{{changed_files}}") + len("{{changed_files}}")
    return prompt[start:prompt.index("{{tester_changed_files}}")]


def _revert_check_passage(prompt: str) -> str:
    """The passage introducing the revert-check record, from its label to its
    placeholder."""
    start = prompt.index("Revert check result")
    return prompt[start:prompt.index("{{revert_check_result}}")]


#: What the two passages said before this story, constructed here so the
#: absences below have something to report on. These are not read from any
#: history; they are the sentences the story exists to replace.
OLD_TESTER_GUIDANCE = (
    "Tester changed files (tester's record — test files created or modified "
    "by the tester stage; treat them as expected additions of a later stage, "
    "not implementation scope violations):"
)
OLD_REVERT_PASSAGE = (
    "A `permitted` of true means the suite failed with those edits reverted, "
    "so the change forced them and they stand."
)


def _describes_the_record_as_test_files(guidance: str) -> bool:
    """Whether the guidance says what the old slot said: that the entries are
    test files, or that they are expected additions rather than violations.
    Matched on the affirmative phrasing, because the rewritten slot may
    legitimately *deny* both."""
    normalized = _normalized(guidance)
    return ("test files created or modified" in normalized
            or "expected additions of a later stage, not" in normalized)


def _says_permitted_edits_stand(passage: str) -> bool:
    """Whether the passage closes the question: the old sentence's claim that
    a permitted edit was forced and stands. Matched on the affirmative
    phrasing, because the rewritten passage may legitimately *deny* it."""
    normalized = _normalized(passage)
    return "forced them" in normalized or "and they stand" in normalized


def test_the_tester_slot_describes_every_file_touched_and_points_outside_at_the_check():
    guidance = _normalized(_tester_slot_guidance(
        VERIFIER_PROMPT.read_text(encoding="utf-8")))

    assert "every repository file the tester" in guidance
    assert "outside the tester's confinement" in guidance
    assert "revert check record" in guidance
    assert not _describes_the_record_as_test_files(guidance)
    # The control: the passage as it stood is what the check reports.
    assert _describes_the_record_as_test_files(OLD_TESTER_GUIDANCE)


def test_the_revert_check_passage_presents_permitted_as_a_fact_to_judge():
    passage = _normalized(_revert_check_passage(
        VERIFIER_PROMPT.read_text(encoding="utf-8")))

    # Permitted true is the tree needing the edits, not a closed question.
    assert "`permitted` of true means the tree needed the edits" in passage
    assert "does not say they stand" in passage
    # The routed case is spelled out: needed, undone anyway, and the tree
    # being judged lacks that work.
    assert "`routed`" in passage
    assert "belong to another stage" in passage
    assert "lacks that work" in passage
    # The verdict and its category are the verifier's, off the injected table.
    assert "routing table" in passage
    assert "yours to decide" in passage
    assert not _says_permitted_edits_stand(passage)
    # The control: the sentence this story replaces is what the check reports.
    assert _says_permitted_edits_stand(OLD_REVERT_PASSAGE)


def test_the_routed_record_the_verifier_is_told_about_is_the_one_the_coordinator_writes(
    target, harness_root,
):
    """The prompt names `permitted`, `reverted` and `routed` as the keys that
    together mean the routed case; the record the run writes carries exactly
    those keys together, so the prose and the artifact describe one thing."""
    assert confined.run(target, harness_root,
                        confined.forced_edit_outside)[0] == 0
    record = confined.record_of(target)
    passage = _revert_check_passage(VERIFIER_PROMPT.read_text(encoding="utf-8"))
    for key in ("permitted", "reverted", "routed"):
        assert key in record
        assert f"`{key}`" in passage
