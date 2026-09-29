"""The scope extension's create-sense bound, its accounting on every verdict
and its parenthetical walk agree with the rest of the coordinator.

Story-183 let a verifier reach one file outside a story's declared scope and
had the coordinator refuse a reach that crosses a bound. Three things it left
behind are validated here. The stage-restriction bound used to treat a stage
forbidden to *create* files somewhere as forbidden to *change* anything there,
so an implementer's reach into an existing test was refused with a message
about creating a file nobody would create; now the bound asks the question the
enforcement asks — a confinement governs a change whether or not the file
exists, a create restriction governs only a creation — and the check is handed
a predicate answering whether a path exists, which the verifier branch
supplies from the tree the run works in. The accounting of routed findings was
written once per attempt, so a refusal made on the second verdict of an
attempt — the one a correction or repair pass returns to — was logged and then
forgotten; now such a refusal amends the artifact already written, moving each
routed slug it names from acted on to the remainder with the refusal as its
reason, so the completion filing files it. And the two readers of a
`do_not_modify` entry each walked back to the opening of its closing
parenthetical on their own; now both call one function.

The runs here execute the workflows the story-183 module runs — the
repair-pass fixture, and the inspection fixture with a repair pass declared —
and one derived from the first by giving the stage one category routes to a
create restriction over a directory this module names. Every stage name,
category and restriction is read off those definitions. The subject that
genuinely is this repository — that every committed story artifact's entries
are judged and split as before — reads the committed artifacts and says so.

Every absence asserted here carries a demonstration that it can fail:

  * "the file exists, so the extension is accepted" sits beside the same
    finding with the file absent, which is refused, and the reverse;
  * "existence is never asked for a stage under no create restriction" sits
    beside a governed path under one, for which it is asked exactly once;
  * "a second verdict refusing nothing leaves the artifact byte-for-byte" sits
    beside a second verdict that does refuse, which rewrites it;
  * "no reader of an entry walks on its own" is a scan over the shipped source
    that reports the walk when the pre-story readers are handed to it;
  * "every committed entry is judged and split as before" compares against a
    reference written from the pre-story readers, and a reference altered in
    one detail disagrees with it over the same corpus;
  * "no workflow, prompt or schema changed" resolves through the shared story
    range and sits beside constructed histories the same call tells apart.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import textwrap
from pathlib import Path

import pytest

import conftest
import plan_validation
import schema_validator
import story_coordinator
import story_inspection
import story_parser
import test_a_repair_reaches_outside_the_storys_scope_within_bounds as extended
from test_a_repair_reaches_outside_the_storys_scope_within_bounds import (  # noqa: F401 - fixtures used by name
    DECLARED,
    EXTENSION_GRANT,
    EXTENSION_REFUSED,
    IN_SCOPE_HALF,
    ROUTED_FINDING,
    RULES,
    SEALED,
    SEALED_FILE,
    announcement_line,
    assert_accepted,
    assert_refused_and_the_run_went_on,
    declined_by_slug,
    extending,
    inspected_check,
    inspecting_harness,
    inspecting_target,
    routed_repair,
)
import test_inspection_findings_reach_the_story as inspected
from test_inspection_findings_reach_the_story import (  # noqa: F401 - fixtures used by name
    filings,
    no_model,
)
from test_repair_pass import (  # noqa: F401 - the fixture the runs configure
    GRANT_EVENT,
    PROMPTS,
    RECORD_CATEGORY,
    REPAIRABLE,
    ROUTES,
    STAGE_NAMES,
    WORKFLOW,
    configure,
    configured_workflow,
    destination_of,
    drive,
    entry_shape,
    events_of_kind,
    paths_named_in,
    read_state,
    record_of,
    repairing,
)

REPO_ROOT = conftest.HARNESS_ROOT
COORDINATOR_SOURCE = Path(story_coordinator.__file__).read_text(encoding="utf-8")
PLAN_VALIDATION_SOURCE = Path(plan_validation.__file__).read_text(encoding="utf-8")
STORY_SCHEMA = schema_validator.load_schema("story")

#: The walk both readers call, named once here and asserted to be the one
#: function they call below.
WALK = "_closing_parenthetical_start"

# --------------------------------------------------------------------------
# A workflow whose routed stage declares a create restriction
# --------------------------------------------------------------------------

#: The stage the record category routes to, which the repair-pass fixture
#: declares no restriction on. The probe below gives it a create restriction
#: over the directories named here, so the create-sense bound has a stage to
#: ask about. Two prefixes, one under the other, so a path both govern shows
#: existence asked once and not once per prefix.
RESTRICTED = destination_of(RECORD_CATEGORY)
GUARDED = "guides/"
DEEP = f"{GUARDED}deep/"
GUARDED_PREFIXES = (GUARDED, DEEP)

#: A file this module commits into the target under the guarded directory, one
#: it does not, and one under both prefixes.
EXISTING = f"{GUARDED}EXISTING.md"
MISSING = f"{GUARDED}MISSING.md"
DEEP_FILE = f"{DEEP}NOTE.md"

EXISTING_EXTENSION = extending(EXISTING, RECORD_CATEGORY, "GUARDED-EXISTING")
MISSING_EXTENSION = extending(MISSING, RECORD_CATEGORY, "GUARDED-MISSING")
DEEP_EXTENSION = extending(DEEP_FILE, RECORD_CATEGORY, "GUARDED-DEEP")

PROBE_NAME = "create-restricted-workflow"


def create_restricted_workflow() -> dict:
    """The repair-pass fixture's definition with the routed stage forbidden to
    create files under the guarded directories, and nothing else changed."""
    workflow = json.loads(json.dumps(WORKFLOW))
    for stage in workflow["stages"]:
        if stage["name"] == RESTRICTED:
            stage["may_not_create"] = list(GUARDED_PREFIXES)
    workflow["name"] = PROBE_NAME
    return workflow


PROBE = create_restricted_workflow()


def probe_stage(name: str) -> dict:
    return next(stage for stage in PROBE["stages"] if stage["name"] == name)


def create_restrictions() -> list[story_coordinator.StageRestriction]:
    return story_coordinator.restrictions_on(probe_stage(RESTRICTED))


class Asked:
    """An existence predicate that records what it was asked, answering from
    the set of paths it is told are present."""

    def __init__(self, present=()):
        self.present = frozenset(present)
        self.asked: list[str] = []

    def __call__(self, path: str) -> bool:
        self.asked.append(path)
        return path in self.present


def check(*findings: dict, exists, stages: list[dict] = PROBE["stages"],
          story: dict = DECLARED, rules: dict = RULES):
    """The bounds asked over the probe's stages with the given predicate; the
    routes and stage names are the fixture's, which the probe leaves alone."""
    return story_coordinator.scope_extension_check(
        list(findings), ROUTES, STAGE_NAMES, stages, story, rules, exists=exists)


# --------------------------------------------------------------------------
# Fixture plumbing for the run under the probe
# --------------------------------------------------------------------------


@pytest.fixture
def guarded_target(target_root: Path) -> Path:
    """The shared target with the existing guarded file committed and the
    missing one absent, configured to run the probe."""
    path = target_root / EXISTING
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# A guide that exists\n", encoding="utf-8")
    conftest.commit_setup(target_root, "the guarded file an extension may reach")
    configure(target_root, workflow=PROBE_NAME)
    assert not (target_root / MISSING).exists()
    return target_root


@pytest.fixture
def probe_harness(tmp_path: Path) -> Path:
    return conftest.materialize_workflow(PROBE, tmp_path / PROBE_NAME,
                                         prompts=PROMPTS, rules=RULES)


def tree_of(run_dir: Path) -> Path:
    """The tree the run worked in, which holds its run directory under the
    configured runs_dir — two levels above it."""
    return run_dir.parents[2]


# ==========================================================================
# The fixture is what it claims to be
# ==========================================================================


def test_the_probe_differs_from_the_fixture_in_the_one_restriction_it_adds():
    assert story_coordinator.restrictions_on(
        next(s for s in WORKFLOW["stages"] if s["name"] == RESTRICTED)) == []
    restrictions = create_restrictions()
    assert [one.prefix for one in restrictions] == list(GUARDED_PREFIXES)
    for one in restrictions:
        assert one.sense == story_coordinator.CREATE_RESTRICTION
        assert one.stage == RESTRICTED
    for restriction in restrictions:
        assert restriction.governs(EXISTING) == (restriction.prefix == GUARDED)
        assert restriction.governs(MISSING) == (restriction.prefix == GUARDED)
        assert restriction.governs(DEEP_FILE)
    # Everything but the one key is the fixture's own.
    without = json.loads(json.dumps(PROBE))
    for stage in without["stages"]:
        stage.pop("may_not_create", None)
    without["name"] = WORKFLOW["name"]
    assert without == WORKFLOW
    # The guarded paths are under no other bound the check asks.
    for path in (EXISTING, MISSING, DEEP_FILE):
        assert not story_coordinator.is_blocked(path, RULES["blocked_paths"])
        assert story_coordinator.do_not_modify_bound(DECLARED, path) is None
        assert not extended.in_declared_scope(path)


# ==========================================================================
# The table beside _ROUTES_NECESSARY_EDITS, read through the property
# ==========================================================================


def test_the_table_is_keyed_as_the_senses_are_and_the_property_reads_it():
    senses = set(story_coordinator._SENSES)
    assert set(story_coordinator._GOVERNS_MODIFICATIONS) == senses
    assert set(story_coordinator._ROUTES_NECESSARY_EDITS) == senses
    assert story_coordinator._GOVERNS_MODIFICATIONS[
        story_coordinator.CREATE_RESTRICTION] is False
    assert story_coordinator._GOVERNS_MODIFICATIONS[
        story_coordinator.CONFINEMENT] is True
    for restriction in (*create_restrictions(),
                        extended.confinement_of(extended.WRITING)):
        assert restriction.governs_modifications == \
            story_coordinator._GOVERNS_MODIFICATIONS[restriction.sense]
    assert not any(one.governs_modifications for one in create_restrictions())
    assert extended.confinement_of(extended.WRITING).governs_modifications


def function_named(source: str, name: str) -> ast.FunctionDef:
    (found,) = [node for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.FunctionDef) and node.name == name]
    return found


def sense_reads_in(function: ast.FunctionDef) -> list[ast.Attribute]:
    """Every read of a `.sense` attribute inside `function`, which is how a
    call site would compare the sense rather than read the table."""
    return [node for node in ast.walk(function)
            if isinstance(node, ast.Attribute) and node.attr == "sense"]


def test_the_check_compares_no_sense_and_the_scan_would_report_one():
    assert sense_reads_in(function_named(COORDINATOR_SOURCE,
                                         "scope_extension_check")) == []
    # Control: the same scan over the check with a comparison planted.
    planted = ast.parse(
        "def scope_extension_check(one):\n"
        "    if one.sense == CONFINEMENT:\n"
        "        return True\n")
    assert len(sense_reads_in(planted.body[0])) == 1
    # And the property itself is the only reader of the table.
    readers = [node for node in ast.walk(ast.parse(COORDINATOR_SOURCE))
               if isinstance(node, ast.Name)
               and node.id == "_GOVERNS_MODIFICATIONS"
               and isinstance(node.ctx, ast.Load)]
    assert len(readers) == 1


# ==========================================================================
# The create-sense bound, decided as a function
# ==========================================================================


def test_a_create_restricted_stage_accepts_an_existing_file_and_refuses_a_missing_one():
    """The same finding, decided twice: existence is the only input that
    differs, so the decision is a fact about existence and not the fixture."""
    accepted = check(MISSING_EXTENSION, exists=Asked({MISSING}))
    assert accepted.accepted == [MISSING_EXTENSION]
    assert accepted.refused == []

    refused = check(MISSING_EXTENSION, exists=Asked(()))
    assert refused.accepted == []
    (decision,) = refused.refused
    assert decision.finding is MISSING_EXTENSION
    assert decision.stage == RESTRICTED
    governing = next(one for one in create_restrictions()
                     if one.governs(MISSING))
    assert governing.wording in decision.bound
    assert MISSING in decision.bound
    assert "does not exist" in decision.bound
    assert "would create it" in decision.bound
    assert story_coordinator._SENSES[story_coordinator.CONFINEMENT] \
        not in decision.bound
    reason = story_coordinator.refusal_reason(decision)
    assert reason.startswith("scope extension refused")
    assert decision.bound in reason


def test_the_confinement_refusal_reads_as_it_did_and_says_nothing_of_creation():
    """The confined stage of the story-183 fixture: refused whether or not the
    file exists, in the wording story-183 chose."""
    notes = extended.NOTES_EXTENSION
    for present in (Asked({notes["path"]}), Asked(())):
        (decision,) = check(notes, exists=present,
                            stages=WORKFLOW["stages"]).refused
        assert decision.bound == (
            f"{extended.confinement_of(extended.WRITING).wording}, "
            f"and {notes['path']} is governed by it")
        assert "would create" not in decision.bound
        assert present.asked == []


@pytest.mark.parametrize("present", [True, False], ids=["exists", "missing"])
def test_the_other_two_bounds_and_the_confinement_decide_as_before(present):
    """Over the story-183 fixture's whole set of findings, existence changes
    nothing: what was accepted is accepted and what was refused is refused
    with the same bound, and the story-183 module's own predicate — every
    path exists — agrees."""
    findings = (*extended.ACCEPTED, *extended.REFUSED)
    decided = check(*findings, exists=lambda path: present,
                    stages=WORKFLOW["stages"])
    reference = extended.check(*findings)
    assert decided.accepted == reference.accepted == list(extended.ACCEPTED)
    assert [(one.finding, one.stage, one.bound) for one in decided.refused] == \
        [(one.finding, one.stage, one.bound) for one in reference.refused]
    assert [one.finding for one in decided.refused] == list(extended.REFUSED)


def test_a_finding_under_both_prefixes_asks_existence_once():
    asked = Asked({DEEP_FILE})
    assert check(DEEP_EXTENSION, exists=asked).accepted == [DEEP_EXTENSION]
    assert asked.asked == [DEEP_FILE]
    asked = Asked(())
    (decision,) = check(DEEP_EXTENSION, exists=asked).refused
    assert asked.asked == [DEEP_FILE]
    # The first governing restriction in declared order is the one named.
    assert create_restrictions()[0].wording in decision.bound


def test_existence_is_asked_only_where_a_create_restriction_governs():
    """The blocked-path bound is asked first, the confined stage's restriction
    governs modifications, and an unrestricted stage has nothing to ask
    about: none of them consults the tree. The control is the guarded path,
    for which it is asked."""
    asked = Asked(())
    sealed_to_restricted = extending(SEALED_FILE, RECORD_CATEGORY, "SEALED-GUARDED")
    decided = check(extended.SEALED_EXTENSION, extended.NOTES_EXTENSION,
                    extended.NOTES_UNRESTRICTED_EXTENSION,
                    extended.HELPERS_EXTENSION, extended.FROZEN_EXTENSION,
                    extended.BARE_EXTENSION, sealed_to_restricted,
                    exists=asked, stages=WORKFLOW["stages"])
    assert asked.asked == []
    assert [one.finding for one in decided.refused] == [
        extended.SEALED_EXTENSION, extended.NOTES_EXTENSION,
        extended.FROZEN_EXTENSION, sealed_to_restricted]
    # Under the probe the record category's stage is restricted, and the
    # blocked path is still asked first: a sealed path under it never reaches
    # the tree either.
    asked = Asked(())
    (decision,) = check(sealed_to_restricted, exists=asked).refused
    assert SEALED in decision.bound
    assert asked.asked == []
    # A path a stage under no restriction reaches is never asked.
    unrestricted = extending(MISSING, extended.BEHAVIOUR_CATEGORY, "OUT-OF-LANE")
    asked = Asked(())
    (decision,) = check(unrestricted, exists=asked).refused
    assert extended.confinement_of(extended.WRITING).wording in decision.bound
    assert asked.asked == []
    # Control: the guarded path under the restricted stage is asked.
    asked = Asked(())
    check(MISSING_EXTENSION, exists=asked)
    assert asked.asked == [MISSING]


def test_a_reasoned_entry_still_refuses_an_existing_guarded_file():
    """The bounds keep their order: a file that exists under the create
    restriction is past that bound, and a reasoned do_not_modify entry naming
    it refuses it as before; the same story with the entry's reason stripped
    accepts it."""
    reasoned = {"scope": {"do_not_modify": [f"{GUARDED} (published elsewhere)"]}}
    (decision,) = check(EXISTING_EXTENSION, exists=Asked({EXISTING}),
                        story=reasoned).refused
    assert "do_not_modify" in decision.bound
    assert f"{GUARDED} (published elsewhere)" in decision.bound
    bare = {"scope": {"do_not_modify": [GUARDED]}}
    assert check(EXISTING_EXTENSION, exists=Asked({EXISTING}),
                 story=bare).refused == []
    # And a missing file under the same reasoned entry names the restriction,
    # asked before the entry.
    (decision,) = check(MISSING_EXTENSION, exists=Asked(()),
                        story=reasoned).refused
    assert "would create it" in decision.bound
    assert "do_not_modify" not in decision.bound


def test_an_undeclared_category_is_still_left_before_existence_is_asked():
    unknown = {**MISSING_EXTENSION,
               "category": "a-category-this-workflow-does-not-declare"}
    assert unknown["category"] not in ROUTES
    asked = Asked(())
    decided = check(unknown, exists=asked)
    assert decided.accepted == [unknown]
    assert asked.asked == []


def test_the_predicate_is_required():
    with pytest.raises(TypeError):
        story_coordinator.scope_extension_check(  # type: ignore[call-arg]
            [MISSING_EXTENSION], ROUTES, STAGE_NAMES, PROBE["stages"],
            DECLARED, RULES)


# ==========================================================================
# The verifier branch decides existence against the run's target root
# ==========================================================================


def test_one_verdict_reaching_an_existing_and_a_missing_guarded_file(
        guarded_target, probe_harness):
    """One run under the probe: the extension naming the file the target holds
    is granted and the pass is taken for it; the one naming the file it does
    not hold is refused naming the restriction and the creation it would be.
    Existence was decided against the tree the run worked in."""
    code, runner, run_dir = drive(guarded_target, probe_harness,
                                  [repairing(MISSING_EXTENSION,
                                             EXISTING_EXTENSION),
                                   extended.PASS])
    assert code == 0, runner.calls
    assert read_state(run_dir)["status"] == "completed"
    assert read_state(run_dir)["repair_pass_count"] == 1
    assert runner.calls == entry_shape(RESTRICTED)
    tree = tree_of(run_dir)
    assert (tree / EXISTING).is_file()
    assert not (tree / MISSING).exists()

    (refused,) = extended.refusals(run_dir)
    message = refused["message"]
    assert MISSING in message
    assert RESTRICTED in message
    governing = next(one for one in create_restrictions() if one.governs(MISSING))
    assert governing.wording in message
    assert "does not exist" in message
    assert "would create it" in message
    assert message in extended.events_log(run_dir)
    assert not paths_named_in(message, (EXISTING,))

    (grant,) = extended.grants_naming(run_dir, EXISTING)
    assert grant["stage"] == RESTRICTED
    assert extended.grants_naming(run_dir, MISSING) == []
    (announced,) = extended.announcements(run_dir)
    assert announced["message"] == announcement_line(RESTRICTED, EXISTING_EXTENSION)
    assert record_of(run_dir)["findings"] == [
        {**EXISTING_EXTENSION, "stage": RESTRICTED}]
    assert "escalated" not in extended.events_of(run_dir)


def test_the_existing_guarded_file_alone_is_accepted(guarded_target, probe_harness):
    run = drive(guarded_target, probe_harness,
                [repairing(EXISTING_EXTENSION), extended.PASS])
    assert_accepted(run, EXISTING_EXTENSION, RESTRICTED)


def test_the_missing_guarded_file_alone_is_refused_and_the_run_goes_on(
        guarded_target, probe_harness):
    run = drive(guarded_target, probe_harness, [repairing(MISSING_EXTENSION)])
    refused = assert_refused_and_the_run_went_on(run, MISSING_EXTENSION, RESTRICTED)
    assert "would create it" in refused["message"]
    _, runner, run_dir = run
    assert runner.calls == STAGE_NAMES
    assert read_state(run_dir)["repair_pass_count"] == 0


def test_the_same_missing_file_under_the_unrestricted_fixture_is_accepted(
        target_root, tmp_path):
    """Control for the refusal: the identical run under the fixture the probe
    was derived from — the routed stage under no restriction — grants the
    missing file, so the refusal is the restriction's doing and not the
    path's."""
    harness = conftest.materialize_workflow(
        WORKFLOW, tmp_path / "unrestricted-harness", prompts=PROMPTS, rules=RULES)
    assert not (target_root / MISSING).exists()
    run = drive(target_root, harness, [repairing(MISSING_EXTENSION), extended.PASS])
    assert_accepted(run, MISSING_EXTENSION, RESTRICTED)


# ==========================================================================
# A refusal on the second verdict of an attempt amends the accounting
# ==========================================================================
#
# These run the inspection fixture with a repair pass declared, as the
# story-183 module does: one finding routed to the story, a first verdict
# that acts on it and re-enters a stage, and a second verdict — the one the
# pass returns to — that reaches a blocked path from the same slug.


SLUG = ROUTED_FINDING["slug"]
REFUSED_ON_RETURN = routed_repair(SEALED_FILE, "SEALED-ON-RETURN")
REFUSED_AGAIN = routed_repair(f"{SEALED}OTHER.md", "SEALED-AGAIN")

#: The first verdicts that act on the slug and re-enter a stage, so the judge
#: is entered twice inside one attempt: through the repair pass and through
#: the correction pass.
FIRST_VERDICTS = {
    "repair-pass": repairing(IN_SCOPE_HALF),
    "correction-pass": inspected.passing_with(inspected.naming_the_slug(ROUTED_FINDING)),
}


def test_the_second_verdicts_finding_is_refused_and_names_the_slug():
    """The premises: each refused half names the routed slug, is refused by
    the blocked-path bound, and the two carry different refusals."""
    for half in (REFUSED_ON_RETURN, REFUSED_AGAIN):
        assert story_inspection.names_a_slug(half["location"], (SLUG,))
        (decision,) = inspected_check(half).refused
        assert SEALED in decision.bound
    assert story_coordinator.refusal_reason(inspected_check(REFUSED_ON_RETURN).refused[0]) \
        != story_coordinator.refusal_reason(inspected_check(REFUSED_AGAIN).refused[0])
    for first in FIRST_VERDICTS.values():
        acted, _ = story_inspection.acted_on_by([ROUTED_FINDING], first)
        assert acted == [ROUTED_FINDING]


@pytest.mark.parametrize("first", list(FIRST_VERDICTS), ids=list(FIRST_VERDICTS))
def test_a_refusal_on_the_second_verdict_moves_the_slug_and_files_it(
        inspecting_target, inspecting_harness, tmp_path, filings, first):
    """The judge is entered twice inside attempt 1. The first verdict acts on
    the routed slug, so the accounting records it as acted on; the second
    refuses a file of the same slug. The artifact is amended — the slug moves
    to the remainder with the refusal as its reason — an accounting event says
    so, and the completion files the finding as a brief."""
    run = inspected.drive(inspecting_target, inspecting_harness, tmp_path,
                          findings=[ROUTED_FINDING],
                          verdicts=[FIRST_VERDICTS[first],
                                    repairing(REFUSED_ON_RETURN)],
                          name=f"amended-{first}")
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.state["retry_count"] == 0
    assert run.runner.calls.count(inspected.JUDGING) == 2

    (refused,) = inspected.events_of_kind(run, EXTENSION_REFUSED)
    assert SEALED_FILE in refused["message"]
    recorded = inspected.accounting(run)
    assert recorded["attempt"] == 1
    assert recorded["routed"] == 1
    assert recorded["routed_slugs"] == [SLUG]
    assert recorded["acted_on"] == 0
    assert recorded["acted_on_slugs"] == []
    assert recorded["remainder"] == 1
    assert recorded["remainder_slugs"] == [SLUG]
    entry = declined_by_slug(recorded)[SLUG]
    assert entry["reason_given"] is True
    assert entry["reason"] == refused["message"]
    assert recorded["declined_with_reason"] == 1
    assert recorded["declined_without_reason"] == 0

    events = inspected.accounting_events(run)
    assert len(events) == 2
    assert "1 acted on by the verdict, 0 left by it" in events[0]["message"]
    amended = events[1]["message"]
    assert "amended" in amended
    assert SLUG in amended
    assert refused["message"] in amended
    assert "0 acted on by the verdict, 1 left by it" in amended
    assert refused["sequence"] < events[1]["sequence"]
    assert inspected.accounting_files(run) == [inspected.accounting_path(run, 1).name]

    assert inspected.filings_of(filings, ROUTED_FINDING) == 1
    (filed,) = inspected.filed_events(run)
    assert filed["filed"] == 1
    assert inspected.routed_markers(run) == [ROUTED_FINDING["body"]]


@pytest.mark.parametrize("first", list(FIRST_VERDICTS), ids=list(FIRST_VERDICTS))
def test_a_second_verdict_refusing_nothing_leaves_the_slug_acted_on(
        inspecting_target, inspecting_harness, tmp_path, filings, first):
    """The control: the identical first verdict followed by a clean one is
    accounted once, the slug stays acted on, and nothing is filed."""
    run = inspected.drive(inspecting_target, inspecting_harness, tmp_path,
                          findings=[ROUTED_FINDING],
                          verdicts=[FIRST_VERDICTS[first], inspected.PASS],
                          name=f"unamended-{first}")
    assert run.code == 0
    assert run.runner.calls.count(inspected.JUDGING) == 2
    assert inspected.events_of_kind(run, EXTENSION_REFUSED) == []
    recorded = inspected.accounting(run)
    assert recorded["acted_on_slugs"] == [SLUG]
    assert recorded["remainder_slugs"] == []
    assert recorded["declined"] == []
    assert len(inspected.accounting_events(run)) == 1
    assert inspected.filings_of(filings, ROUTED_FINDING) == 0
    assert inspected.routed_markers(run) == []


def test_a_slug_already_in_the_remainder_keeps_the_reason_it_was_recorded_with(
        inspecting_target, inspecting_harness, tmp_path, filings):
    """The first verdict fixes the slug across two files and one is refused,
    so the slug is left with that refusal as its reason and the pass is taken
    for the other; the second verdict refuses another file of the same slug.
    The artifact keeps the first reason, no amendment event is written, and
    the finding is filed once."""
    run = inspected.drive(inspecting_target, inspecting_harness, tmp_path,
                          findings=[ROUTED_FINDING],
                          verdicts=[repairing(IN_SCOPE_HALF, REFUSED_ON_RETURN),
                                    repairing(REFUSED_AGAIN)],
                          name="already-left")
    assert run.code == 0
    assert run.state["repair_pass_count"] == 1
    assert run.runner.calls.count(inspected.JUDGING) == 2
    first, second = inspected.events_of_kind(run, EXTENSION_REFUSED)
    assert SEALED_FILE in first["message"]
    assert REFUSED_AGAIN["path"] in second["message"]
    recorded = inspected.accounting(run)
    assert recorded["remainder_slugs"] == [SLUG]
    assert recorded["acted_on_slugs"] == []
    (entry,) = recorded["declined"]
    assert entry["slug"] == SLUG
    assert entry["reason"] == first["message"]
    assert entry["reason"] != second["message"]
    assert len(inspected.accounting_events(run)) == 1
    assert inspected.filings_of(filings, ROUTED_FINDING) == 1
    assert inspected.routed_markers(run) == [ROUTED_FINDING["body"]]


# --------------------------------------------------------------------------
# The amendment as a function: byte-for-byte where nothing moves
# --------------------------------------------------------------------------


def account(run_dir: Path, verdict: dict, refused_slugs=()) -> None:
    story_inspection.account_after_verdict(
        run_dir, run_dir / "target", {}, run_dir / "harness", inspected.STORY_ID,
        artifact=inspected.FINDINGS_ARTIFACT, attempt=1, verdict=verdict,
        refused_slugs=refused_slugs)


def accounting_bytes(run_dir: Path) -> bytes:
    return (run_dir / story_inspection.accounting_artifact_file(
        inspected.FINDINGS_ARTIFACT, 1)).read_bytes()


def accounting_lines(run_dir: Path) -> list[str]:
    return [line for line in (run_dir / "events.log").read_text(
        encoding="utf-8").splitlines() if "accounting for the findings" in line]


def refusing(*halves: dict, verifier_declined: list | None = None):
    """The verdict the coordinator hands the accounting for `halves`, each
    refused, beside the routed slugs it names — through the coordinator's own
    transformation, so the declined entries are the ones a run writes."""
    verdict = repairing(*halves)
    if verifier_declined is not None:
        verdict["declined_findings"] = verifier_declined
    decided = inspected_check(*verdict[REPAIRABLE])
    assert [one.finding for one in decided.refused] == list(verdict[REPAIRABLE])
    named = story_coordinator.refused_routed_slugs(decided, (SLUG,))
    return story_coordinator.verdict_after_refusals(verdict, decided, (SLUG,)), named


@pytest.fixture
def accounted(tmp_path: Path) -> Path:
    """A run directory whose attempt routed the finding and whose first verdict
    acted on it."""
    run_dir = tmp_path / "accounted"
    run_dir.mkdir()
    story_inspection.write_findings(run_dir, inspected.FINDINGS_ARTIFACT, 1,
                                    inspected.STORY_ID, ran=True,
                                    findings=[ROUTED_FINDING])
    account(run_dir, repairing(IN_SCOPE_HALF))
    recorded = json.loads(accounting_bytes(run_dir))
    assert recorded["acted_on_slugs"] == [SLUG]
    assert len(accounting_lines(run_dir)) == 1
    return run_dir


def test_a_later_verdict_refusing_nothing_leaves_the_artifact_byte_for_byte(
        accounted):
    before = accounting_bytes(accounted)
    account(accounted, inspected.PASS)
    account(accounted, repairing(IN_SCOPE_HALF))
    assert accounting_bytes(accounted) == before
    assert len(accounting_lines(accounted)) == 1
    # A refused slug the artifact does not record as routed changes nothing.
    handed, _ = refusing(REFUSED_ON_RETURN)
    account(accounted, handed, refused_slugs=("zzz-never-routed",))
    assert accounting_bytes(accounted) == before
    assert len(accounting_lines(accounted)) == 1
    # Control for the equality: the same call with the refused slug named
    # rewrites the artifact.
    account(accounted, handed, refused_slugs=(SLUG,))
    assert accounting_bytes(accounted) != before


def test_the_amendment_moves_the_slug_with_the_refusal_as_its_reason(accounted):
    """The refusal the coordinator appended is what is recorded — the last
    declined entry naming the slug — even where the verifier's own verdict
    had declined the slug first for a reason of its own."""
    handed, named = refusing(
        REFUSED_ON_RETURN,
        verifier_declined=[{"slug": SLUG, "reason": "the verifier's own reason"}])
    assert named == (SLUG,)
    assert [one["slug"] for one in handed["declined_findings"]] == [SLUG, SLUG]
    refusal = story_coordinator.refusal_reason(
        inspected_check(REFUSED_ON_RETURN).refused[0])
    assert handed["declined_findings"][-1]["reason"] == refusal

    account(accounted, handed, refused_slugs=named)
    recorded = json.loads(accounting_bytes(accounted))
    assert recorded["acted_on"] == 0
    assert recorded["acted_on_slugs"] == []
    assert recorded["remainder"] == 1
    assert recorded["remainder_slugs"] == [SLUG]
    assert recorded["routed_slugs"] == [SLUG]
    (entry,) = recorded["declined"]
    # `abandoned` false: what moved this slug was a refused scope extension
    # and not a spent pass allowance, and story-213's marker is what tells the
    # two apart on an entry the amendment wrote.
    assert entry == {"slug": SLUG, "reason_given": True, "reason": refusal,
                     "dropped": False, "abandoned": False}
    assert recorded["declined_with_reason"] == 1
    assert recorded["declined_without_reason"] == 0
    assert (recorded["filed"], recorded["dropped"]) == (0, 0)
    lines = accounting_lines(accounted)
    assert len(lines) == 2
    assert "amended" in lines[1]
    assert SLUG in lines[1]
    assert refusal in lines[1]
    # What the completion filing reads is now the moved finding.
    assert story_inspection.remainder_findings(
        accounted, inspected.FINDINGS_ARTIFACT, 1) == [ROUTED_FINDING]
    # Amending again with the same slug moves nothing more.
    after = accounting_bytes(accounted)
    account(accounted, handed, refused_slugs=named)
    assert accounting_bytes(accounted) == after
    assert len(accounting_lines(accounted)) == 2


def test_the_amendment_records_the_slug_declined_without_a_reason_where_none_is_there(
        accounted):
    """A verdict naming the refused slug in no declined entry: the slug still
    moves, recorded as declined without a reason, and the event says the
    verdict records none."""
    account(accounted, repairing(), refused_slugs=(SLUG,))
    recorded = json.loads(accounting_bytes(accounted))
    assert recorded["remainder_slugs"] == [SLUG]
    (entry,) = recorded["declined"]
    # `dropped` false: a slug moved here by a refused scope extension is
    # left for the backlog and never dropped, whatever else the verdict said.
    # `abandoned` false for the reason above it: no allowance was spent here.
    assert entry == {"slug": SLUG, "reason_given": False, "dropped": False,
                     "abandoned": False}
    assert recorded["declined_without_reason"] == 1
    assert "records no reason" in accounting_lines(accounted)[1]


@pytest.mark.parametrize("damage", ["absent", "unparseable", "not-an-object"])
def test_an_unreadable_findings_record_amends_nothing_and_says_so(accounted,
                                                                   damage):
    """The amendment rebuilds the acted-on and remainder lists from the
    attempt's findings record, so a record that cannot be read would rewrite
    the accounting with nothing routed and nothing left — erasing the first
    verdict's record and the refusal with it. It stops instead: the artifact
    is byte-for-byte as written, and an accounting event says the refusal
    could not be amended in and why. A record that parses as JSON but is not
    an object is as unreadable as one that does not parse."""
    findings = accounted / story_inspection.findings_artifact_file(
        inspected.FINDINGS_ARTIFACT, 1)
    if damage == "absent":
        findings.unlink()
    elif damage == "unparseable":
        findings.write_text("{not json", encoding="utf-8")
    else:
        findings.write_text(json.dumps([ROUTED_FINDING]), encoding="utf-8")
    before = accounting_bytes(accounted)
    handed, named = refusing(REFUSED_ON_RETURN)
    assert named == (SLUG,)

    account(accounted, handed, refused_slugs=named)
    assert accounting_bytes(accounted) == before
    lines = accounting_lines(accounted)
    assert len(lines) == 2
    assert "not amended" in lines[1]
    assert SLUG in lines[1]
    assert findings.name in lines[1]
    assert "left as the first verdict wrote it" in lines[1]
    assert "moved from acted on" not in lines[1]
    # Control: the same refusal over a readable record moves the slug.
    story_inspection.write_findings(accounted, inspected.FINDINGS_ARTIFACT, 1,
                                    inspected.STORY_ID, ran=True,
                                    findings=[ROUTED_FINDING])
    account(accounted, handed, refused_slugs=named)
    assert accounting_bytes(accounted) != before
    assert json.loads(accounting_bytes(accounted))["remainder_slugs"] == [SLUG]
    assert "moved from acted on" in accounting_lines(accounted)[2]


def test_a_slug_the_first_verdict_left_keeps_its_reason_as_a_function(tmp_path):
    run_dir = tmp_path / "left-first"
    run_dir.mkdir()
    story_inspection.write_findings(run_dir, inspected.FINDINGS_ARTIFACT, 1,
                                    inspected.STORY_ID, ran=True,
                                    findings=[ROUTED_FINDING])
    first, named = refusing(REFUSED_ON_RETURN)
    account(run_dir, first, refused_slugs=named)
    before = accounting_bytes(run_dir)
    recorded = json.loads(before)
    assert recorded["remainder_slugs"] == [SLUG]
    first_reason = declined_by_slug(recorded)[SLUG]["reason"]
    assert SEALED_FILE in first_reason

    second, named = refusing(REFUSED_AGAIN)
    account(run_dir, second, refused_slugs=named)
    assert accounting_bytes(run_dir) == before
    assert declined_by_slug(json.loads(accounting_bytes(run_dir)))[SLUG]["reason"] \
        == first_reason
    assert len(accounting_lines(run_dir)) == 1


# --------------------------------------------------------------------------
# The amendment moves the slug the refusal names and nothing beside it
# --------------------------------------------------------------------------
#
# The fixture above routes one finding, so an amendment that rebuilt the
# split from nothing would look the same as one that moved the refused slug.
# Two routed findings tell those apart: one is refused on the later verdict
# and the other is not, and what the artifact records of the other — acted
# on, or left with the reason the verifier gave — is what must survive.


#: A second finding routed alongside the story-183 module's, naming the same
#: workflow so it is routed the same way, with a slug of its own.
OTHER_SLUG = "zzz-routed-beside-it"
OTHER_ROUTED_FINDING = {
    **inspected.finding(OTHER_SLUG, [inspected.CHANGED_FILE],
                        "MARKER-OTHER the change is wrong a second way"),
    "workflow": ROUTED_FINDING["workflow"],
}
OTHER_IN_SCOPE_HALF = inspected.naming_the_slug(OTHER_ROUTED_FINDING,
                                                inspected.REPAIRABLE_FINDING)


def routing_both(run_dir: Path) -> None:
    run_dir.mkdir()
    story_inspection.write_findings(
        run_dir, inspected.FINDINGS_ARTIFACT, 1, inspected.STORY_ID, ran=True,
        findings=[ROUTED_FINDING, OTHER_ROUTED_FINDING])


def test_the_two_routed_findings_carry_different_slugs_and_both_are_acted_on():
    """The premise of the pair below: the second finding is routed under its
    own slug, and a verdict naming both halves acts on both."""
    assert OTHER_SLUG != SLUG
    assert story_inspection.names_a_slug(OTHER_IN_SCOPE_HALF["location"],
                                         (OTHER_SLUG,))
    assert not story_inspection.names_a_slug(OTHER_IN_SCOPE_HALF["location"],
                                             (SLUG,))
    acted, left = story_inspection.acted_on_by(
        [ROUTED_FINDING, OTHER_ROUTED_FINDING],
        repairing(IN_SCOPE_HALF, OTHER_IN_SCOPE_HALF))
    assert acted == [ROUTED_FINDING, OTHER_ROUTED_FINDING]
    assert left == []


def test_the_amendment_moves_only_the_refused_slug_and_keeps_the_other_acted_on(
        tmp_path):
    """Both routed findings acted on by the first verdict; the second verdict
    refuses one. The other stays acted on, the routed set is what it was, and
    the one declined entry is the moved slug's."""
    run_dir = tmp_path / "two-routed"
    routing_both(run_dir)
    account(run_dir, repairing(IN_SCOPE_HALF, OTHER_IN_SCOPE_HALF))
    recorded = json.loads(accounting_bytes(run_dir))
    assert recorded["acted_on_slugs"] == [SLUG, OTHER_SLUG]
    assert recorded["remainder_slugs"] == []

    handed, named = refusing(REFUSED_ON_RETURN)
    assert named == (SLUG,)
    account(run_dir, handed, refused_slugs=named)
    amended = json.loads(accounting_bytes(run_dir))
    assert amended["routed_slugs"] == [SLUG, OTHER_SLUG]
    assert amended["routed"] == 2
    assert amended["acted_on_slugs"] == [OTHER_SLUG]
    assert amended["acted_on"] == 1
    assert amended["remainder_slugs"] == [SLUG]
    assert amended["remainder"] == 1
    assert [one["slug"] for one in amended["declined"]] == [SLUG]
    assert story_inspection.remainder_findings(
        run_dir, inspected.FINDINGS_ARTIFACT, 1) == [ROUTED_FINDING]
    lines = accounting_lines(run_dir)
    assert len(lines) == 2
    assert SLUG in lines[1]
    assert OTHER_SLUG not in lines[1]
    assert "1 acted on by the verdict, 1 left by it" in lines[1]
    # Control for the absence: naming the other slug instead moves the other
    # one, so the amendment follows the refusal and not the fixture's order.
    other_dir = tmp_path / "two-routed-other"
    routing_both(other_dir)
    account(other_dir, repairing(IN_SCOPE_HALF, OTHER_IN_SCOPE_HALF))
    account(other_dir, repairing(), refused_slugs=(OTHER_SLUG,))
    other = json.loads(accounting_bytes(other_dir))
    assert other["acted_on_slugs"] == [SLUG]
    assert other["remainder_slugs"] == [OTHER_SLUG]
    assert OTHER_SLUG in accounting_lines(other_dir)[1]
    assert SLUG not in accounting_lines(other_dir)[1]


def test_the_amendment_keeps_a_slug_the_first_verdict_left_beside_the_moved_one(
        tmp_path):
    """The first verdict acts on one routed finding and declines the other
    with a reason of its own; the second verdict refuses the acted-on one.
    The remainder then carries both, in routed order, and the declined entry
    the first verdict wrote survives with its reason, beside the refusal."""
    run_dir = tmp_path / "one-left-first"
    routing_both(run_dir)
    verifier_reason = "the verifier left the other one alone on purpose"
    first = repairing(IN_SCOPE_HALF)
    first["declined_findings"] = [{"slug": OTHER_SLUG, "reason": verifier_reason}]
    account(run_dir, first)
    recorded = json.loads(accounting_bytes(run_dir))
    assert recorded["acted_on_slugs"] == [SLUG]
    assert recorded["remainder_slugs"] == [OTHER_SLUG]
    first_entry = declined_by_slug(recorded)[OTHER_SLUG]
    assert first_entry == {"slug": OTHER_SLUG, "reason_given": True,
                           "reason": verifier_reason, "dropped": False,
                           "abandoned": False}

    handed, named = refusing(REFUSED_ON_RETURN)
    refusal = handed["declined_findings"][-1]["reason"]
    account(run_dir, handed, refused_slugs=named)
    amended = json.loads(accounting_bytes(run_dir))
    assert amended["acted_on_slugs"] == []
    assert amended["remainder_slugs"] == [SLUG, OTHER_SLUG]
    assert amended["remainder"] == 2
    declined = declined_by_slug(amended)
    assert declined[OTHER_SLUG] == first_entry
    assert declined[SLUG] == {"slug": SLUG, "reason_given": True,
                              "reason": refusal, "dropped": False,
                              "abandoned": False}
    assert amended["declined_with_reason"] == 2
    assert amended["declined_without_reason"] == 0
    assert story_inspection.remainder_findings(
        run_dir, inspected.FINDINGS_ARTIFACT, 1) \
        == [ROUTED_FINDING, OTHER_ROUTED_FINDING]
    assert len(accounting_lines(run_dir)) == 2
    assert "0 acted on by the verdict, 2 left by it" in accounting_lines(run_dir)[1]


# ==========================================================================
# The parenthetical walk is one function both readers call
# ==========================================================================


def calls_in(function: ast.FunctionDef, name: str) -> list[ast.Call]:
    return [node for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == name]


def loops_in(function: ast.FunctionDef) -> list[ast.For]:
    return [node for node in ast.walk(function) if isinstance(node, ast.For)]


def test_both_readers_call_the_one_walk_and_neither_walks_on_its_own():
    tree_source = COORDINATOR_SOURCE
    walk = function_named(tree_source, WALK)
    assert len(loops_in(walk)) == 1
    for reader in ("carries_a_reason", "_entry_paths"):
        function = function_named(tree_source, reader)
        assert len(calls_in(function, WALK)) == 1, reader
        assert loops_in(function) == [], reader
    assert function_named(tree_source, "do_not_modify_bound")
    assert WALK not in PLAN_VALIDATION_SOURCE
    assert plan_validation.carries_a_reason is story_coordinator.carries_a_reason
    # Control: the pre-story readers, handed to the same scan, each walk on
    # their own and call no shared function.
    for reader in (REFERENCE_REASON, REFERENCE_PATHS):
        function = ast.parse(textwrap.dedent(inspect.getsource(reader))).body[0]
        assert len(loops_in(function)) == 1
        assert calls_in(function, WALK) == []


# The two readers as story-183 wrote them, held here as the reference the
# shared walk is compared against — a corpus judged and split by these is
# judged and split "as before". They are ordinary functions of this module
# rather than source run at test time, so the scans holding the suite to
# building no module at runtime read them like any other test code, and the
# AST control above reads their source through inspect. The split boundary
# is the coordinator's own, so the reference differs from today's reader in
# the walk alone.
def reference_carries_a_reason(entry: str) -> bool:
    text = entry.rstrip()
    if not text.endswith(")"):
        return False
    depth = 0
    for position in range(len(text) - 1, -1, -1):
        character = text[position]
        if character == ")":
            depth += 1
        elif character == "(":
            depth -= 1
            if depth == 0:
                reason, before = text[position + 1:-1], text[:position]
                return before.strip() != "" and re.search(r"\w", reason) is not None
    return False


def reference_entry_paths(entry: str) -> list[str]:
    text = entry.rstrip()
    depth = 0
    before = text
    for position in range(len(text) - 1, -1, -1):
        character = text[position]
        if character == ")":
            depth += 1
        elif character == "(":
            depth -= 1
            if depth == 0:
                before = text[:position]
                break
    boundary = story_coordinator._ENTRY_PATH_BOUNDARY
    return [piece.strip() for piece in boundary.split(before) if piece.strip()]


def altered_entry_paths(entry: str) -> list[str]:
    """The reference splitter altered in one detail: it asks for a trailing
    ")" before it walks, which the description's wording of the walk would
    have had it do, and splits the whole entry where there is none. Every
    other line is the reference's."""
    text = entry.rstrip()
    depth = 0
    before = text
    boundary = story_coordinator._ENTRY_PATH_BOUNDARY
    if not text.endswith(")"):
        return [piece.strip() for piece in boundary.split(text) if piece.strip()]
    for position in range(len(text) - 1, -1, -1):
        character = text[position]
        if character == ")":
            depth += 1
        elif character == "(":
            depth -= 1
            if depth == 0:
                before = text[:position]
                break
    return [piece.strip() for piece in boundary.split(before) if piece.strip()]


REFERENCE_REASON, REFERENCE_PATHS = reference_carries_a_reason, reference_entry_paths


def judged_and_split(entry: str) -> tuple:
    return (story_coordinator.carries_a_reason(entry),
            story_coordinator._entry_paths(entry))


def reference_judged_and_split(entry: str) -> tuple:
    return (REFERENCE_REASON(entry), REFERENCE_PATHS(entry))


#: Entries whose shape exercises the walk: a reason carrying nested
#: parentheses, a last parenthetical following an earlier one, a nested one
#: following an earlier one, a parenthetical mid-text, an unbalanced one, a
#: bare path, an empty reason and an empty entry.
NESTED = "src/a.py (see story-025 (plan time) for why)"
LATER = "docs/x/ (first) and docs/y/ (the later one)"
LATER_NESTED = "docs/x/ (first (deep)), docs/y/ and docs/z/ (the later (deeper) one)"
MID_TEXT = "any restriction key (may_not_create, may_only_change) on either definition"
UNBALANCED = "src/b.py the reason)"
CONSTRUCTED = (NESTED, LATER, LATER_NESTED, MID_TEXT, UNBALANCED,
               "src/bare.py", "src/empty.py ()", "", "   ", "(only a reason)",
               "src/c.py (reason) ", "src/d.py ((double))")


@pytest.mark.parametrize("entry", CONSTRUCTED, ids=[repr(one) for one in CONSTRUCTED])
def test_a_constructed_entry_is_judged_and_split_as_before(entry):
    assert judged_and_split(entry) == reference_judged_and_split(entry)


def test_nested_and_later_parentheticals_are_read_at_the_position_both_readers_share():
    """The opening the walk returns is the position both readers read from:
    the text before it is what is split, and what stands either side of it
    is what is judged. Where a different opening would change the answer —
    the inner "(" of a nested reason, the earlier parenthetical — the split
    shows which was taken."""
    walk = getattr(story_coordinator, WALK)
    opening = walk(NESTED)
    assert NESTED[opening:].startswith("(see story-025")
    assert story_coordinator._entry_paths(NESTED) == ["src/a.py"]
    assert story_coordinator.carries_a_reason(NESTED)

    opening = walk(LATER)
    assert LATER[opening:] == "(the later one)"
    assert story_coordinator._entry_paths(LATER) == ["docs/x/ (first)", "docs/y/"]
    assert story_coordinator.carries_a_reason(LATER)

    opening = walk(LATER_NESTED)
    assert LATER_NESTED[opening:] == "(the later (deeper) one)"
    assert story_coordinator._entry_paths(LATER_NESTED) == [
        "docs/x/ (first (deep))", "docs/y/", "docs/z/"]
    assert story_coordinator.carries_a_reason(LATER_NESTED)

    # The reader that decides, over the entry's own text at that position.
    for entry in (NESTED, LATER, LATER_NESTED):
        opening = walk(entry)
        before, reason = entry[:opening], entry[opening + 1:-1]
        assert story_coordinator._entry_paths(entry) == [
            piece.strip() for piece in
            story_coordinator._ENTRY_PATH_BOUNDARY.split(before) if piece.strip()]
        assert story_coordinator.carries_a_reason(entry) == (
            before.strip() != "" and re.search(r"\w", reason) is not None)

    # Where nothing balances, the walk returns None and the whole entry is
    # split; the judge refuses.
    assert walk(UNBALANCED) is None
    assert story_coordinator._entry_paths(UNBALANCED) == [UNBALANCED]
    assert not story_coordinator.carries_a_reason(UNBALANCED)
    assert walk("src/bare.py") is None
    assert walk("") is None
    # The walk does not ask whether the entry ends with ")": that is the
    # judge's question, asked before it walks, and the splitter never asked.
    assert walk(MID_TEXT) == MID_TEXT.index("(")
    assert not story_coordinator.carries_a_reason(MID_TEXT)
    assert story_coordinator._entry_paths(MID_TEXT) == ["any restriction key"]


# ==========================================================================
# Every committed do_not_modify entry is judged and split as before
# ==========================================================================


def committed_entries() -> list[str]:
    """Every do_not_modify entry of every story artifact committed under
    .harness/stories/, parsed as the coordinator parses a story."""
    entries: list[str] = []
    for path in sorted((REPO_ROOT / ".harness" / "stories").glob("*.yaml")):
        parsed = story_parser.parse(path.read_text(encoding="utf-8"), STORY_SCHEMA)
        for entry in (parsed.get("scope") or {}).get("do_not_modify") or ():
            if isinstance(entry, str):
                entries.append(entry)
    return entries


ENTRIES = committed_entries()


def test_the_committed_corpus_carries_the_shapes_the_walk_is_about():
    """The corpus is worth comparing over: it holds reasoned entries, bare
    ones, entries listing several paths, and an entry whose parenthetical
    sits mid-text — the shape the judge refuses and the splitter splits at.
    Nested parentheses are exercised by the constructed entries above; no
    committed reason carries them."""
    assert len(ENTRIES) > 100
    judged = [story_coordinator.carries_a_reason(one) for one in ENTRIES]
    assert any(judged) and not all(judged)
    assert any(len(story_coordinator._entry_paths(one)) > 1 for one in ENTRIES)
    mid_text = [one for one in ENTRIES
                if getattr(story_coordinator, WALK)(one) is not None
                and not one.rstrip().endswith(")")]
    assert mid_text, "no committed entry carries a mid-text parenthetical"
    assert all(len(story_coordinator._entry_paths(one)) == 1
               or story_coordinator._ENTRY_PATH_BOUNDARY.search(one)
               for one in ENTRIES)


def test_every_committed_entry_is_judged_and_split_as_before():
    disagreements = [(one, judged_and_split(one), reference_judged_and_split(one))
                     for one in ENTRIES
                     if judged_and_split(one) != reference_judged_and_split(one)]
    assert disagreements == []


def test_the_comparison_tells_an_altered_reader_apart():
    """Control for the agreement above: a reference altered in one detail —
    the splitter asking for a trailing ")" before it walks, which the
    description's wording of the walk would have had it do — disagrees with
    today's reader over the committed corpus, so the agreement is about the
    readers and not about a comparison that cannot see a difference."""
    altered = altered_entry_paths
    # The alteration is the one line asking for the trailing ")": the two
    # sources differ, and only the reference walks without asking.
    reference_lines = set(inspect.getsource(REFERENCE_PATHS).splitlines())
    altered_lines = set(inspect.getsource(altered).splitlines())
    assert any('endswith(")")' in line for line in altered_lines - reference_lines)
    assert not any('endswith(")")' in line for line in reference_lines)
    assert any(altered(one) != story_coordinator._entry_paths(one) for one in ENTRIES)
    assert altered(MID_TEXT) != story_coordinator._entry_paths(MID_TEXT)
    # And a reference that walks as the shipped one does agrees everywhere.
    assert all(REFERENCE_PATHS(one) == story_coordinator._entry_paths(one)
               for one in ENTRIES)


# ==========================================================================
# What this story left alone
# ==========================================================================


LEFT_ALONE = [
    "workflows/",
    "prompts/",
    "schemas/",
    "orchestration/plan_validation.py",
]


def test_no_workflow_prompt_or_schema_changed_and_plan_validation_is_untouched():
    assert conftest.story_diff(LEFT_ALONE, validation_file=Path(__file__)) == ""
    assert "carries_a_reason = story_coordinator.carries_a_reason" in \
        PLAN_VALIDATION_SOURCE


def test_the_same_comparison_tells_an_edited_left_alone_path_apart(tmp_path):
    respecting = conftest.constructed_story(tmp_path, respected=["prompts/"],
                                            name="prompts-left-alone")
    assert conftest.constructed_story_diff(respecting, ["prompts/"]) == ""
    violating = conftest.constructed_story(tmp_path, violated=["prompts/"],
                                           name="prompts-edited")
    assert conftest.constructed_story_diff(violating, ["prompts/"]) != ""
