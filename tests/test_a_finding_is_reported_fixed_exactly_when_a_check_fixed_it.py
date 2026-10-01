"""A finding is reported as fixed exactly when a check in its attempt fixed
it, and what the run cannot fix reaches the backlog.

When a run checks its own work, problems turn up two ways: the inspection
finds some in the change and hands them to the checking stage, and the
checking stage finds some itself. Within one attempt the check can run several
times — once, and again after each fixing pass it asks for. Before this story
the first of those checks settled what became of the inspection's findings,
and a later check could only move one from fixed to the backlog, never back;
the checking stage's own findings were recorded nowhere but in a count. So a
defect fixed by a later check was still sent to the backlog, and a defect the
run could not fix was sometimes lost.

Now the latest check of the attempt that deals with a finding decides what
became of it, by one rule applied to both kinds of finding. This module holds
that rule by driving whole runs under the fake runner, each case beside a
control run that differs from it in the one thing the case is about:

  * **a rejected design choice** — a finding whose decision the returning
    check rejected is fixed only when that check or a later one repairs it
    again under a corrected decision; a repair carrying no decision is the
    restoring a rejection asks for and leaves it for the backlog; a corrected
    decision rejected in turn leaves it on that later rejection; and a
    corrected repair the run has no pass left for is abandoned. The decision
    log holds all of it, in order, and the rejection's own record is what it
    was;
  * **a rejection meeting another cause** — where the verdict that rejects a
    decision also refuses part of the fix or meets a spent allowance, the
    left entry records the rejection whichever cause left the finding, so a
    later repair carrying no decision does not bring it back and one under a
    corrected decision does;
  * **a postponed repair** — the check a words-only correction returns to must
    answer each repair that correction postponed. Asked for again, it is
    repaired, a repair of the verifier's own included where only its
    classification carries its slug; declined by name, it is left on the
    decline's terms; left unanswered, that check runs again in place once,
    spending no retry and told which repair it owes; and unanswered still,
    or with no re-run left, it is declined without a reason;
  * **a finding an earlier check left** — passed over, declined, or judged
    not worth filing — is fixed once a later check of the attempt names it in
    an entry the coordinator accepts;
  * **a part of a fix the run refused** stays owed until a later check makes
    that very part, while a decision refused for naming no source owes
    nothing and is fixed by any later naming;
  * **the checking stage's own findings** — one the run leaves is filed once
    at completion, through the one filing call, as a brief built from the
    classification the checking stage gave it, and named in the report by
    its own slug; one carrying no classification is dropped with that reason
    named; and a run that leaves none accounts, files and reports exactly as
    a run that never raised one;
  * **the rule itself**, driven as a function: written once and applied alike
    to a routed finding and to one of the checking stage's own, both writers
    of a left entry recording the same fields for the same situation, a
    later check that changes nothing writing nothing, and every move a later
    check makes reported in an accounting event naming the finding, the
    direction and the cause;
  * **what this harness ships** — both verifier prompts, in the same words as
    each other, the verdict, correction-pass and self-route schemas, the two
    shipped workflows' budgets and reasons, and the testing standard — read as
    the subject they are, with no configured value held to a number;
  * **what the story left alone**, across this module's own story range.

Every absence asserted here sits beside the run, the copy or the constructed
history that shows the same reading can see the thing it says is absent:

  * "the finding is not filed" and "nothing is left" beside the control run
    that leaves the same finding, read by the same readers;
  * "no send-back" beside the run whose unanswered repair is sent back;
  * "a run leaving none of its own findings carries no record of them" beside
    the run that leaves one, where the same readers find it;
  * "a later check that changes nothing writes nothing" beside the same call
    with a verdict that moves a finding, which rewrites the artifact and says
    so;
  * "no shipped text says the superseded rule" beside a shipped text with the
    superseded sentence planted back into it, which the same reading reports;
  * "the story left these paths alone" and "the history schemas declare the
    kinds and fields they declared" beside constructed histories and a copy
    with a kind added, which the same comparisons tell apart.

Nothing here reaches a model: the sibling module's guard on
`agent_runner.run_agent` is installed for every test, and every stage runs
through its fake runner. Every stage, category and artifact name is derived
from the workflow this module builds on the sibling modules' builders. What
cannot be tested is whether a real verifier answers a postponed repair or
classifies its own finding well: that judgement is the returning check's, and
the reviewer's.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import conftest
import inspection
import schema_validator
import story_coordinator
import story_inspection
from conftest import BASELINE, ENDPOINT

import test_a_completed_run_says_what_became_of_its_findings as reported
import test_a_finding_a_source_settles_is_repaired_under_a_recorded_decision as decided
import test_a_repair_reaches_outside_the_storys_scope_within_bounds as bounds
import test_inspection_findings_reach_the_story as inspected
from test_a_routed_finding_is_repaired_unless_the_verdict_says_why import (
    REPAIRABLE_PHRASE,
    flowed,
    paragraph_containing,
    sentences,
    shipped_prompt_texts,
)
from test_a_small_finding_is_repaired_or_dropped_never_filed import (
    sentence_carrying,
)
from test_inspection_findings_reach_the_story import (  # noqa: F401 - fixtures used by name
    filings,
    no_model,
)
from test_shipped_workflow_is_valid import REASON_SUFFIX, states_the_number

THIS_MODULE = Path(__file__)
REPO_ROOT = conftest.HARNESS_ROOT

STORY_ID = inspected.STORY_ID
JUDGING = inspected.JUDGING
CATEGORY = inspected.RETRY_CATEGORY
PASS = inspected.PASS

DECISION = story_coordinator.DECISION
REJECTED = story_coordinator.REJECTED_DECISIONS
UNANSWERED = story_coordinator.UNANSWERED_POSTPONED_REPAIRS
BUDGET_KEY = story_coordinator.SELF_ROUTE_BUDGET_KEY
BOOKKEEPING_KEY = story_coordinator.BOOKKEEPING_SELF_ROUTE_BUDGET_KEY
CLASSIFICATION = story_inspection.CLASSIFICATION
UNCLASSIFIED = story_inspection.UNCLASSIFIED_OWN_FINDING

VERDICT_SCHEMA = schema_validator.load_schema("verification-result")
BRIEF_SCHEMA = schema_validator.load_schema(inspection.BRIEF_SCHEMA)
CORRECTION_SCHEMA = schema_validator.load_schema("correction-pass")
SELF_ROUTE_SCHEMA = schema_validator.load_schema("self-route-result")

#: The run-level events these readings look for by kind, held against the
#: execution history's own enum in the premise test.
SELF_ROUTED = "self-routed"
DECISION_REJECTED = decided.DECISION_REJECTED
REJECTION = decided.REJECTION

# ==========================================================================
# The workflow these runs execute
# ==========================================================================

#: The name the workflow is written under, and which every finding names: an
#: inspection drops a finding naming a workflow the harness does not define.
WORKFLOW_NAME = "a-check-that-fixed-it-workflow"

#: The allowances these runs execute under, this module's own rather than
#: anything this repository deploys. Three passes of each kind is the most any
#: case below takes; one re-run of the judging stage for a send-back is what
#: "sent back once" needs; and one re-run for anything else is declared only
#: so the judging stage splits its budget the way the shipped one does.
PASSES = 3
SEND_BACKS = 1
FAILURE_BUDGET = 1


def checking_workflow(*, repairs: int = PASSES, corrections: int = PASSES,
                      send_backs: int = SEND_BACKS) -> dict:
    """story-219's decided-repair workflow — the sibling inspection module's
    definition with a repair pass declared beside its correction pass — with
    both allowances and the judging stage's re-run budgets set here."""
    workflow = decided.deciding_workflow(repairs)
    for stage in workflow["stages"]:
        if stage.get("inspection"):
            stage["correction_pass"] = {**stage["correction_pass"],
                                        "budget": corrections}
            stage[BUDGET_KEY] = FAILURE_BUDGET
            stage[BOOKKEEPING_KEY] = send_backs
    workflow["name"] = WORKFLOW_NAME
    return workflow


def fresh(tmp_path: Path, name: str, **budgets) -> tuple[Path, Path]:
    """A target and a harness of their own, so one test can hold a run and
    its control side by side: a target that has run a story refuses to run it
    again. The templates are story-219's, which render the repair pass
    record."""
    workflow = checking_workflow(**budgets)
    harness = inspected.materialize(workflow, tmp_path / f"{name}-harness",
                                    rules=decided.RULES)
    for stage in workflow["stages"]:
        (harness / "prompts" / stage["prompt"]).write_text(
            decided.PROMPTS[stage["name"]], encoding="utf-8")
    target = inspected.build_target(tmp_path / f"{name}-target",
                                    workflow=WORKFLOW_NAME)
    return target, harness


def drive(target: Path, harness: Path, tmp_path: Path, verdicts: list[dict],
          *, findings=None, name: str = "journal"):
    return inspected.drive(
        target, harness, tmp_path,
        findings=[FOUND] if findings is None else list(findings),
        verdicts=verdicts, name=name)


def run_of(tmp_path: Path, name: str, verdicts: list[dict], *, findings=None,
           **budgets):
    """One run under a fresh target and harness, which completed."""
    name = name.replace(" ", "-")
    target, harness = fresh(tmp_path, name, **budgets)
    run = drive(target, harness, tmp_path, verdicts, findings=findings,
                name=name)
    assert run.code == 0, run.messages
    assert run.state["status"] == "completed"
    return run


# ==========================================================================
# The findings, the entries and the verdicts
# ==========================================================================

#: The sibling's two routed findings, naming the workflow above.
FOUND = {**inspected.OWN_FINDING, "workflow": WORKFLOW_NAME}
OTHER_FOUND = {**inspected.BOTH_FINDING, "workflow": WORKFLOW_NAME}
SLUG = FOUND["slug"]
OTHER_SLUG = OTHER_FOUND["slug"]


def repair_of(found: dict, decision: dict | None = None) -> dict:
    """A repairable entry naming `found`'s slug, carrying `decision` — a copy,
    so no two verdicts share one — or carrying none."""
    entry = inspected.naming_the_slug(found, inspected.REPAIRABLE_FINDING)
    if decision is None:
        return entry
    return {**entry, DECISION: json.loads(json.dumps(decision))}


def correction_of(found: dict) -> dict:
    return inspected.naming_the_slug(found)


def verdict(*, correct=(), repair=(), reject=None, decline=()) -> dict:
    """A passing verdict carrying exactly what the caller asked for."""
    built = dict(PASS)
    if correct:
        built["correctable_findings"] = [dict(one) for one in correct]
    if repair:
        built["repairable_findings"] = [dict(one) for one in repair]
    if reject:
        built[REJECTED] = [{"slug": slug, "reason": reason}
                           for slug, reason in reject.items()]
    if decline:
        built["declined_findings"] = [dict(one) for one in decline]
    return built


#: The decision story-219's runs make, and a corrected one answering the same
#: question from a different source — so the decision log pairs a rejection
#: with the decision it rejected by finding and question, as it does for any
#: finding decided twice.
CHOSEN = decided.CHOSEN
CORRECTED = {
    **CHOSEN,
    "answer": "MARKER-CORRECTED-ANSWER raise when the case is missing, as "
              "the helper the first source named does",
    "source": f"MARKER-CORRECTED-SOURCE {inspected.SIBLING_FILE}, whose helper "
              f"raises for the missing case",
    "rationale": "MARKER-CORRECTED-RATIONALE the source the first decision "
                 "cited raises rather than returning a default",
}
REJECTION_REASON = decided.REJECTION_REASON
SECOND_REJECTION = ("MARKER-SECOND-REJECTION the corrected source raises for "
                    "another case, so it does not settle this one either")
DECLINE_REASON = "MARKER-DECLINE the fix is a design decision the plan did not make"
DROP_WORDS = "MARKER-DROP a one-word typo nobody should plan a story for"

#: A repair of the verifier's own carrying no classification and naming no
#: slug — story-219's — which routes a pass whatever becomes of the routed
#: finding, so two runs differing in what they say of the routed finding take
#: the same passes.
UNRELATED = decided.UNRELATED

#: A finding of the verifier's own, carrying a slug of its own in its location
#: and the classification a brief needs, in a brief's own vocabulary read off
#: schemas/story-brief.schema.json.
OWN_SLUG = "zzz-the-other-helper-handles-the-missing-case"
OWN_CLASSIFICATION = {
    "slug": OWN_SLUG,
    "title": "MARKER-OWN-TITLE The helper beside the change handles the "
             "missing case",
    "category": BRIEF_SCHEMA["properties"]["category"]["enum"][-1],
    "severity": max(BRIEF_SCHEMA["properties"]["severity"]["enum"]),
    "confidence": BRIEF_SCHEMA["properties"]["confidence"]["enum"][-1],
    "effort": BRIEF_SCHEMA["properties"]["effort"]["enum"][0],
}
OWN = {**UNRELATED, "location": f"{UNRELATED['location']} ({OWN_SLUG})",
       CLASSIFICATION: OWN_CLASSIFICATION}
#: The same finding as the verifier would write it without a classification.
UNCLASSIFIED_OWN = {key: value for key, value in OWN.items()
                    if key != CLASSIFICATION}


# ==========================================================================
# Reading a run back
# ==========================================================================


def accounting(run) -> dict:
    return inspected.accounting(run)


def left_entry(run, slug: str) -> dict:
    return {one["slug"]: one for one in accounting(run)["declined"]}[slug]


def own_left(run) -> dict:
    """The checking stage's own findings the attempt's accounting records as
    left, by key — empty where the artifact carries none."""
    return {one["slug"]: one for one in accounting(run).get("own_left", ())}


def report(run) -> str:
    return reported.report_of(run)


def named(run, clause: str) -> dict:
    """story-213's reading of one clause of the completion report's account:
    slug to the ground the clause states for it."""
    return reported.named_in(report(run), clause)


def clause(run, name: str) -> str:
    """One clause of the account as rendered, whole — for a ground carrying
    the clause's own separator, which the slug-by-slug reading splits."""
    opening = reported.CLAUSES[name]
    return next((one for one in reported.account_body(report(run))
                 if one.startswith(opening)), "")


def later_moves(run) -> list[str]:
    """The accounting events a later verdict of the attempt wrote: every one
    after the first, which is the first verdict's split."""
    return [event["message"] for event in inspected.accounting_events(run)][1:]


def filed_once(filings, slug: str) -> int:
    return inspected.filings_of(filings, {"slug": slug})


def self_routes(run) -> list[dict]:
    """Each self-route record the run left, oldest first."""
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(run.run_dir.glob(
                story_coordinator.self_route_result_file(JUDGING, "*", "*")))]


def notes_naming(run, phrase: str) -> list[str]:
    return [line for line in run.messages if phrase in line]


# ==========================================================================
# The fixture is what it claims to be
# ==========================================================================


def test_the_fixture_declares_both_passes_and_a_split_budget_on_the_judge():
    """The premise every run below rests on: the stage carrying the inspection
    declares both passes, both re-run budgets, and a routing table every
    entry below names; the pre-flight accepts all of it; and the send-back
    cause spends the record-keeping budget."""
    stages = checking_workflow()["stages"]
    judging = next(stage for stage in stages if stage.get("inspection"))
    assert judging["name"] == JUDGING
    assert judging["correction_pass"]["budget"] == PASSES
    assert judging["repair_pass"]["budget"] == PASSES
    assert judging[BOOKKEEPING_KEY] == SEND_BACKS
    assert story_coordinator.repair_pass_problems(stages) == []
    assert story_coordinator.correction_pass_problems(stages) == []
    assert story_coordinator.self_route_problems(stages) == []
    assert CATEGORY in judging["on_failure"]["retry_routing"]
    for entry in (repair_of(FOUND), correction_of(OTHER_FOUND), UNRELATED, OWN):
        assert entry["category"] == CATEGORY
    assert UNANSWERED in story_coordinator.SELF_ROUTE_FAILURES
    assert UNANSWERED in story_coordinator.BOOKKEEPING_SELF_ROUTE_FAILURES


def test_the_findings_are_named_as_the_rule_reads_them():
    """The routed repairs name their slugs; the verifier's own finding names
    none of them, carries a slug of its own and is followed by it; and the
    unclassified copy is followed by its location, which is all that can be
    read of it."""
    routed = (SLUG, OTHER_SLUG)
    assert story_inspection.names_a_slug(repair_of(FOUND)["location"], (SLUG,))
    assert not story_inspection.names_a_slug(repair_of(FOUND)["location"],
                                             (OTHER_SLUG,))
    assert not story_inspection.names_a_slug(OWN["location"], routed)
    assert story_inspection.own_finding_key(OWN, routed) == OWN_SLUG
    assert story_inspection.own_finding_key(UNCLASSIFIED_OWN, routed) == \
        UNCLASSIFIED_OWN["location"]
    assert story_inspection.own_finding_key(repair_of(FOUND), routed) == ""


def test_the_kinds_these_readings_look_for_are_kinds_the_history_declares():
    declared = schema_validator.load_schema("execution-history")[
        "items"]["properties"]["event"]["enum"]
    for kind in (SELF_ROUTED, DECISION_REJECTED,
                 story_inspection.ACCOUNTING_EVENT,
                 story_inspection.FILED_EVENT):
        assert kind in declared, kind


# ==========================================================================
# A rejected decision, and the repair that follows it
# ==========================================================================


def rejected_and(repair: dict, *, rejection: str = REJECTION_REASON) -> dict:
    """The verdict a repair pass returns to: it rejects the decision the
    routed finding's repair rested on and carries `repair` beside it."""
    return verdict(repair=[repair], reject={SLUG: rejection})


def test_a_corrected_decision_on_the_rejecting_verification_counts_as_acted_on(
        tmp_path, filings):
    """The verification the repair pass returns to rejects the decision and,
    in the same verdict, repairs the finding again under a corrected decision
    the coordinator accepts. The finding is acted on, nothing is filed, and
    the report names it among the findings repaired within the run.

    Its control is the run below, whose rejecting verdict differs from this
    one only in carrying no decision on its repair."""
    run = run_of(tmp_path, "corrected-at-once", [
        verdict(repair=[repair_of(FOUND, CHOSEN)]),
        rejected_and(repair_of(FOUND, CORRECTED)),
        PASS])
    assert run.state["repair_pass_count"] == 2
    recorded = accounting(run)
    assert recorded["acted_on_slugs"] == [SLUG]
    assert recorded["remainder_slugs"] == []
    assert filed_once(filings, SLUG) == 0
    assert inspected.routed_markers(run) == []
    assert SLUG in named(run, "repaired")
    assert SLUG not in named(run, "backlog")


def test_the_same_rejection_answered_by_restoring_alone_leaves_the_finding(
        tmp_path, filings):
    """The control: the rejecting verdict restores the files with a repair
    carrying no decision. The finding is left with the rejection as its
    ground, recorded as rejected, filed once and named among the findings
    sent to the backlog."""
    run = run_of(tmp_path, "restored", [
        verdict(repair=[repair_of(FOUND, CHOSEN)]),
        rejected_and(repair_of(FOUND)),
        PASS])
    recorded = accounting(run)
    assert recorded["acted_on_slugs"] == []
    assert recorded["remainder_slugs"] == [SLUG]
    entry = left_entry(run, SLUG)
    assert entry["decision_rejected"] is True
    assert REJECTION_REASON in entry["reason"]
    assert filed_once(filings, SLUG) == 1
    assert inspected.routed_markers(run) == [FOUND["body"]]
    assert named(run, "backlog")[SLUG] == entry["reason"]


def test_a_later_verification_repairing_under_a_corrected_decision_brings_it_back(
        tmp_path, filings):
    """The rejecting verdict restores alone, which leaves the finding; the
    verification after that repairs it under a corrected decision, which
    brings it back. Each move is said in an accounting event naming the
    finding, the direction and the cause, and nothing is filed."""
    run = run_of(tmp_path, "corrected-later", [
        verdict(repair=[repair_of(FOUND, CHOSEN)]),
        rejected_and(repair_of(FOUND)),
        verdict(repair=[repair_of(FOUND, CORRECTED)]),
        PASS])
    assert run.state["repair_pass_count"] == 3
    assert accounting(run)["acted_on_slugs"] == [SLUG]
    assert accounting(run)["remainder_slugs"] == []
    out, back = later_moves(run)
    for move in (out, back):
        assert SLUG in move
    assert "moved from acted on to left" in out
    assert "rejected the decision" in out
    assert "moved from left to acted on" in back
    assert "corrected decision" in back
    assert filed_once(filings, SLUG) == 0
    assert SLUG in named(run, "repaired")


def test_the_decision_log_holds_the_decision_its_rejection_and_the_correction(
        tmp_path):
    """In the order they were made: the first decision, its rejection, and
    the corrected decision — paired by finding and question as any finding
    decided twice is."""
    run = run_of(tmp_path, "logged", [
        verdict(repair=[repair_of(FOUND, CHOSEN)]),
        rejected_and(repair_of(FOUND)),
        verdict(repair=[repair_of(FOUND, CORRECTED)]),
        PASS])
    first, rejection, corrected = decided.decision_records(run)
    for record in (first, rejection, corrected):
        assert record["finding"] == repair_of(FOUND)["location"]
        assert record["question"] == CHOSEN["question"]
    assert first["answer"] == CHOSEN["answer"]
    assert REJECTION not in first
    assert rejection[REJECTION] == REJECTION_REASON
    assert corrected["answer"] == CORRECTED["answer"]
    assert corrected["source"] == CORRECTED["source"]
    assert REJECTION not in corrected


def without_the_moment(record: dict) -> dict:
    return {key: value for key, value in record.items()
            if key not in ("timestamp", "sequence")}


def test_the_rejection_is_recorded_as_it_is_when_the_verdict_only_restores(
        tmp_path):
    """The rejection's event and its decision-log record are the same in the
    run whose rejecting verdict repairs under a corrected decision as in the
    run whose rejecting verdict only restores, timestamps aside."""
    corrected = run_of(tmp_path, "rejected-then-corrected", [
        verdict(repair=[repair_of(FOUND, CHOSEN)]),
        rejected_and(repair_of(FOUND, CORRECTED)), PASS])
    restored = run_of(tmp_path, "rejected-then-restored", [
        verdict(repair=[repair_of(FOUND, CHOSEN)]),
        rejected_and(repair_of(FOUND)), PASS])
    (corrected_event,) = inspected.events_of_kind(corrected, DECISION_REJECTED)
    (restored_event,) = inspected.events_of_kind(restored, DECISION_REJECTED)
    assert without_the_moment(corrected_event) == \
        without_the_moment(restored_event)
    corrected_rejection = [one for one in decided.decision_records(corrected)
                           if REJECTION in one]
    restored_rejection = [one for one in decided.decision_records(restored)
                          if REJECTION in one]
    assert len(corrected_rejection) == len(restored_rejection) == 1
    assert without_the_moment(corrected_rejection[0]) == \
        without_the_moment(restored_rejection[0])


def test_a_repair_after_a_rejection_carrying_no_decision_never_brings_it_back(
        tmp_path, filings):
    """The rejecting verdict restores, and the verification after it names
    the finding again in a repair carrying no decision: the finding stays
    left on both, filed once with the rejection as its ground and named
    among the findings sent to the backlog. Its control is the run above
    whose third verdict differs in carrying the corrected decision."""
    run = run_of(tmp_path, "never-corrected", [
        verdict(repair=[repair_of(FOUND, CHOSEN)]),
        rejected_and(repair_of(FOUND)),
        verdict(repair=[repair_of(FOUND)]),
        PASS])
    assert run.state["repair_pass_count"] == 3
    recorded = accounting(run)
    assert recorded["remainder_slugs"] == [SLUG]
    entry = left_entry(run, SLUG)
    assert entry["decision_rejected"] is True
    assert REJECTION_REASON in entry["reason"]
    (out,) = later_moves(run)
    assert "moved from acted on to left" in out
    assert filed_once(filings, SLUG) == 1
    assert named(run, "backlog")[SLUG] == entry["reason"]


def test_a_corrected_decision_rejected_in_turn_leaves_it_on_the_later_rejection(
        tmp_path, filings):
    """The verification after the corrected repair rejects the corrected
    decision too: the finding is left with that later rejection as its
    ground, not the first. Its control is the run whose verification after
    the corrected repair rejected nothing, where the finding is acted on."""
    run = run_of(tmp_path, "rejected-twice", [
        verdict(repair=[repair_of(FOUND, CHOSEN)]),
        rejected_and(repair_of(FOUND, CORRECTED)),
        rejected_and(repair_of(FOUND), rejection=SECOND_REJECTION),
        PASS])
    entry = left_entry(run, SLUG)
    assert entry["decision_rejected"] is True
    assert SECOND_REJECTION in entry["reason"]
    assert REJECTION_REASON not in entry["reason"]
    assert filed_once(filings, SLUG) == 1
    assert named(run, "backlog")[SLUG] == entry["reason"]


@pytest.mark.parametrize("passes", [1, 2], ids=["no pass left", "a pass left"])
def test_a_corrected_repair_the_run_has_no_pass_for_is_abandoned(
        tmp_path, filings, passes):
    """The rejecting verdict repairs under a corrected decision, and the run
    has no repair pass left to run it: the finding is left as abandoned,
    with the spent allowance as its ground, like any finding a spent
    allowance abandons. The same verdicts with one pass more are the
    control, where the corrected repair runs and the finding is acted on."""
    verdicts = [verdict(repair=[repair_of(FOUND, CHOSEN)]),
                rejected_and(repair_of(FOUND, CORRECTED))]
    if passes > 1:
        verdicts.append(PASS)
    run = run_of(tmp_path, f"corrected-with-{passes}", verdicts,
                 repairs=passes)
    recorded = accounting(run)
    if passes == 1:
        assert recorded["abandoned_slugs"] == [SLUG]
        entry = left_entry(run, SLUG)
        assert entry["abandoned"] is True
        assert str(passes) in entry["abandoned_because"]
        assert filed_once(filings, SLUG) == 1
        assert named(run, "abandoned") == {SLUG: entry["abandoned_because"]}
    else:
        assert recorded["acted_on_slugs"] == [SLUG]
        assert recorded["abandoned_slugs"] == []
        assert filed_once(filings, SLUG) == 0


# ==========================================================================
# A repair a correction pass postponed
# ==========================================================================

#: The verdict that postpones: a correction of one routed finding beside a
#: repair of the other, so the correction pass routes and the repair is
#: deferred to the verification it returns to.
BOTH_KINDS = verdict(correct=[correction_of(OTHER_FOUND)],
                     repair=[repair_of(FOUND)])
BOTH_ROUTED = [FOUND, OTHER_FOUND]


def test_a_postponed_repair_asked_for_again_is_repaired(tmp_path, filings):
    """The verification the correction pass returns to names the postponed
    repair again: the repair pass routes, the finding is acted on and
    nothing is sent back. The runs below, whose returning verification does
    not ask for it again, are the control."""
    run = run_of(tmp_path, "asked-again", [
        BOTH_KINDS, verdict(repair=[repair_of(FOUND)]), PASS],
        findings=BOTH_ROUTED)
    assert run.state["correction_pass_count"] == 1
    assert run.state["repair_pass_count"] == 1
    assert run.state["retry_count"] == 0
    assert self_routes(run) == []
    assert inspected.events_of_kind(run, SELF_ROUTED) == []
    assert sorted(accounting(run)["acted_on_slugs"]) == sorted([SLUG, OTHER_SLUG])
    assert filed_once(filings, SLUG) == 0


def test_a_postponed_repair_declined_by_name_is_left_on_the_declines_terms(
        tmp_path, filings):
    """The returning verification declines the postponed repair by name with
    its reason: the finding is left with that reason, filed once and named
    among the findings sent to the backlog, and nothing is sent back."""
    run = run_of(tmp_path, "declined-by-name", [
        BOTH_KINDS,
        verdict(decline=[{"slug": SLUG, "reason": DECLINE_REASON}])],
        findings=BOTH_ROUTED)
    assert self_routes(run) == []
    assert accounting(run)["acted_on_slugs"] == [OTHER_SLUG]
    entry = left_entry(run, SLUG)
    assert entry["reason_given"] is True
    assert entry["reason"] == DECLINE_REASON
    assert entry["abandoned"] is False
    (move,) = later_moves(run)
    assert SLUG in move and "moved from acted on to left" in move
    assert "postponed" in move
    assert filed_once(filings, SLUG) == 1
    assert named(run, "backlog") == {SLUG: DECLINE_REASON}


def test_a_postponed_repair_declined_as_not_worth_filing_is_dropped(
        tmp_path, filings):
    """A decline carrying not_worth_filing is a drop on any verdict, the one
    a correction pass returns to included: nothing is filed for it and the
    report names it among the discarded."""
    run = run_of(tmp_path, "declined-as-a-drop", [
        BOTH_KINDS,
        verdict(decline=[{"slug": SLUG, "reason": DECLINE_REASON,
                          "not_worth_filing": DROP_WORDS}])],
        findings=BOTH_ROUTED)
    entry = left_entry(run, SLUG)
    assert entry["dropped"] is True
    assert entry["not_worth_filing"] == DROP_WORDS
    assert filed_once(filings, SLUG) == 0
    assert named(run, "discarded") == {SLUG: DROP_WORDS}


def test_an_unanswered_postponed_repair_sends_the_verification_back_once(
        tmp_path, filings):
    """The returning verification answers nothing. It runs again in place,
    once, spending the record-keeping budget and no retry, under the new
    cause and with a statement naming the unanswered repair, which the re-run
    verification is shown. That one asks for the repair, the repair pass
    routes and the finding is acted on."""
    run = run_of(tmp_path, "sent-back", [
        BOTH_KINDS, PASS, verdict(repair=[repair_of(FOUND)]), PASS],
        findings=BOTH_ROUTED)
    assert run.state["retry_count"] == 0
    (record,) = self_routes(run)
    assert record["failure"] == UNANSWERED
    assert record["stage"] == JUDGING
    assert SLUG in record["statement"]
    assert repair_of(FOUND)["location"] in record["statement"]
    assert schema_validator.validate(record, SELF_ROUTE_SCHEMA) == []
    (event,) = inspected.events_of_kind(run, SELF_ROUTED)
    assert event["stage"] == JUDGING
    assert BOOKKEEPING_KEY in event["message"]

    record_text = (run.run_dir / story_coordinator.self_route_result_file(
        JUDGING, 1, 1)).read_text(encoding="utf-8")
    prompts = run.runner.prompts[JUDGING]
    assert record_text not in prompts[1]
    assert record_text in prompts[2]

    assert run.state["repair_pass_count"] == 1
    assert sorted(accounting(run)["acted_on_slugs"]) == sorted([SLUG, OTHER_SLUG])
    assert later_moves(run) == []
    assert filed_once(filings, SLUG) == 0


def test_a_repair_still_unanswered_after_the_send_back_is_declined_without_a_reason(
        tmp_path, filings):
    """The re-run verification answers nothing either. It is not sent back a
    second time and the run does not escalate: the repair is declined
    without a reason, said in the run's events, and the finding is left,
    filed once and named among the findings sent to the backlog. The run
    above, whose re-run verification asks for it, is the control."""
    run = run_of(tmp_path, "still-unanswered", [BOTH_KINDS, PASS, PASS],
                 findings=BOTH_ROUTED)
    assert run.state["retry_count"] == 0
    assert [one["failure"] for one in self_routes(run)] == [UNANSWERED]
    assert run.runner.calls.count(JUDGING) == 3
    assert any(SLUG in line for line in
               notes_naming(run, "treated as declined without a reason"))
    entry = left_entry(run, SLUG)
    assert entry["reason_given"] is False
    assert entry["abandoned"] is False
    (move,) = later_moves(run)
    assert SLUG in move and "moved from acted on to left" in move
    assert filed_once(filings, SLUG) == 1
    assert SLUG in named(run, "backlog")


def test_with_no_re_run_left_an_unanswered_repair_is_declined_at_once(
        tmp_path, filings):
    """The same returning verdict under a judging stage declaring no re-run
    for record-keeping: nothing is sent back, nothing escalates, and the
    repair is declined without a reason on that verdict. The send-back run
    above, differing in the budget, is the control."""
    run = run_of(tmp_path, "no-re-run-left", [BOTH_KINDS, PASS],
                 findings=BOTH_ROUTED, send_backs=0)
    assert self_routes(run) == []
    assert run.runner.calls.count(JUDGING) == 2
    assert any(SLUG in line for line in
               notes_naming(run, "treated as declined without a reason"))
    assert left_entry(run, SLUG)["reason_given"] is False
    assert filed_once(filings, SLUG) == 1


#: A repair of the verifier's own whose classification carries its slug and
#: whose location does not — the slug given once rather than in both places
#: the prompts ask for it — and the verdict that postpones it behind a
#: correction of the routed finding.
UNSPELLED_OWN = {**UNRELATED, CLASSIFICATION: OWN_CLASSIFICATION}
POSTPONING_OWN = verdict(correct=[correction_of(FOUND)], repair=[UNSPELLED_OWN])


def test_the_unspelled_repair_is_followed_by_the_slug_its_classification_carries():
    """The premise of the two runs below: the location does not spell the
    slug, so only the classification can tie the entry to the finding it is
    about, and the one reading of naming does."""
    assert not story_inspection.names_key(UNSPELLED_OWN["location"], OWN_SLUG)
    assert story_inspection.own_finding_key(UNSPELLED_OWN, (SLUG,)) == OWN_SLUG
    assert story_inspection.entry_names_key(UNSPELLED_OWN, OWN_SLUG)


def test_a_postponed_repair_of_its_own_asked_for_again_by_its_slug_is_repaired(
        tmp_path, filings):
    """A correction pass postpones the verifier's own repair, and the
    verification it returns to asks for it again in the identical entry,
    whose location does not spell the slug its classification carries. That
    answers it: nothing is sent back, no retry is spent, the repair pass
    routes, and the run leaves nothing of the verifier's own to account or
    file. The run below, whose returning verification names nothing, is the
    control, where the same readers find the send-back, the left finding and
    its brief."""
    run = run_of(tmp_path, "own-asked-again", [
        POSTPONING_OWN, verdict(repair=[UNSPELLED_OWN]), PASS])
    assert run.state["correction_pass_count"] == 1
    assert run.state["repair_pass_count"] == 1
    assert run.state["retry_count"] == 0
    assert self_routes(run) == []
    assert inspected.events_of_kind(run, SELF_ROUTED) == []
    assert run.runner.calls.count(JUDGING) == 3
    assert own_left(run) == {}
    assert filed_once(filings, OWN_SLUG) == 0


def test_the_same_postponed_repair_of_its_own_left_unanswered_is_sent_back(
        tmp_path, filings):
    """The control: the returning verification, and the one it is sent back
    to, name nothing. It is sent back once under the new cause with a
    statement naming the repair by the slug its classification carries; the
    repair is then declined without a reason, and the finding is left under
    that slug and filed once from its classification."""
    run = run_of(tmp_path, "own-unanswered", [POSTPONING_OWN, PASS, PASS])
    (record,) = self_routes(run)
    assert record["failure"] == UNANSWERED
    assert OWN_SLUG in record["statement"]
    assert run.state["retry_count"] == 0
    assert run.state["repair_pass_count"] == 0
    assert run.runner.calls.count(JUDGING) == 3
    own = own_left(run)[OWN_SLUG]
    assert own["reason_given"] is False
    assert filed_once(filings, OWN_SLUG) == 1
    assert own_brief_in_the_queue(run)["title"] == OWN_CLASSIFICATION["title"]


# ==========================================================================
# A later verification names a finding an earlier one left
# ==========================================================================

#: The three ways the first verdict of an attempt leaves a routed finding it
#: repairs nothing of: saying nothing about it, declining it, and judging it
#: not worth filing. Each also repairs the verifier's own unrelated finding,
#: so a pass routes and there is a later verification to name it.
FIRST_LOOKS = {
    "passed over": verdict(repair=[UNRELATED]),
    "declined": verdict(repair=[UNRELATED],
                        decline=[{"slug": SLUG, "reason": DECLINE_REASON}]),
    "judged not worth filing": verdict(
        repair=[UNRELATED],
        decline=[{"slug": SLUG, "reason": DECLINE_REASON,
                  "not_worth_filing": DROP_WORDS}]),
}


@pytest.mark.parametrize("first", sorted(FIRST_LOOKS))
def test_a_finding_the_first_verification_left_is_fixed_by_a_later_one(
        tmp_path, filings, first):
    """The verification the first repair pass returns to names the finding
    in a repair the coordinator accepts: it is acted on, not filed, named
    among the findings repaired within the run, and the move is said."""
    run = run_of(tmp_path, f"later-{first}", [
        FIRST_LOOKS[first], verdict(repair=[repair_of(FOUND)]), PASS])
    recorded = accounting(run)
    assert recorded["acted_on_slugs"] == [SLUG]
    assert recorded["remainder_slugs"] == []
    (back,) = later_moves(run)
    assert SLUG in back and "moved from left to acted on" in back
    assert filed_once(filings, SLUG) == 0
    assert inspected.routed_markers(run) == []
    assert SLUG in named(run, "repaired")
    assert SLUG not in named(run, "discarded")


@pytest.mark.parametrize("first", sorted(FIRST_LOOKS))
def test_the_same_finding_a_later_verification_does_not_name_stays_as_left(
        tmp_path, filings, first):
    """The control: the later verification repairs the verifier's own
    finding again and names nothing of the routed one, so it keeps what the
    first verdict decided — its reason and its drop included — and is filed
    unless that verdict judged it not worth filing."""
    run = run_of(tmp_path, f"unnamed-{first}", [
        FIRST_LOOKS[first], verdict(repair=[UNRELATED]), PASS])
    assert run.state["repair_pass_count"] == 2
    entry = left_entry(run, SLUG)
    assert later_moves(run) == []
    dropped = first == "judged not worth filing"
    assert entry["dropped"] is dropped
    assert entry["reason_given"] is (first != "passed over")
    assert filed_once(filings, SLUG) == (0 if dropped else 1)
    assert SLUG in named(run, "discarded" if dropped else "backlog")


# ==========================================================================
# A part of the fix the run refused
# ==========================================================================


@pytest.fixture
def frozen_target(tmp_path):
    """A target whose story names a directory in scope.do_not_modify with a
    reason, through story-183's narrowed story — so an extension into it is
    refused until a verdict judges the reason to speak to another concern."""
    def make(name: str) -> tuple[Path, Path]:
        target, harness = fresh(tmp_path, name)
        path = target / ".harness" / "stories" / f"{STORY_ID}.yaml"
        path.write_text(bounds.DECLARED_STORY, encoding="utf-8")
        conftest.commit_setup(target, "narrow the story's scope for this run")
        return target, harness
    return make


def reaching(path: str, marker: str) -> dict:
    """A repair of the routed finding reaching `path` with an extension."""
    return inspected.naming_the_slug(
        FOUND, bounds.extending(path, CATEGORY, marker))


#: The fix spans a file no bound applies to and a file under the reasoned
#: entry; the second is refused until a verdict lifts the bound.
REACHABLE = reaching("docs/NOTES.md", "REACHABLE")
FROZEN = reaching(bounds.FROZEN_FILE, "FROZEN")
LIFTED = bounds.judging(FROZEN, bounds.SPEAKS_ELSEWHERE)

#: What the returning verification makes of the fix, by the part each makes.
SECOND_LOOKS = {
    "makes the refused part": verdict(repair=[LIFTED]),
    "makes only the other part": verdict(repair=[REACHABLE]),
    "has the refused part refused again": verdict(repair=[FROZEN]),
}


@pytest.mark.parametrize("second", sorted(SECOND_LOOKS))
def test_a_refused_part_stays_owed_until_a_later_verification_makes_it(
        frozen_target, tmp_path, filings, second):
    """The first verdict's fix spans two files and a bound refuses one of
    them, which leaves the finding with that file owed. Only the run whose
    later verification makes that very file in an accepted entry brings it
    back; naming the finding for the other file again, or for the refused
    file in an entry refused again, leaves it with the file still owed."""
    name = f"refused-{second}".replace(" ", "-")
    target, harness = frozen_target(name)
    run = drive(target, harness, tmp_path,
                [verdict(repair=[REACHABLE, FROZEN]), SECOND_LOOKS[second],
                 PASS], name=name)
    assert run.code == 0
    assert run.state["status"] == "completed"
    refusals = inspected.events_of_kind(run, bounds.EXTENSION_REFUSED)
    assert refusals
    if second == "makes the refused part":
        assert accounting(run)["acted_on_slugs"] == [SLUG]
        (back,) = later_moves(run)
        assert "moved from left to acted on" in back
        assert bounds.FROZEN_FILE in back
        assert filed_once(filings, SLUG) == 0
        assert SLUG in named(run, "repaired")
    else:
        entry = left_entry(run, SLUG)
        assert entry["refused_files"] == [bounds.FROZEN_FILE]
        assert entry["reason"] in {one["message"] for one in refusals}
        assert later_moves(run) == []
        assert filed_once(filings, SLUG) == 1
        assert SLUG in named(run, "backlog")


@pytest.mark.parametrize("named_later", [True, False],
                         ids=["named later", "not named later"])
def test_a_finding_whose_decision_was_refused_is_fixed_by_any_later_naming(
        tmp_path, filings, named_later):
    """A decision naming no source is refused and takes every file of its
    fix out of the pass, but owes no file: the finding is left with no file
    recorded against it, and a later verification naming it in an accepted
    entry brings it back. The run whose later verification names nothing is
    the control, where it is filed."""
    verdicts = [decided.the_verdict(decided.SOURCELESS["absent"])]
    if named_later:
        verdicts.append(verdict(repair=[repair_of(FOUND)]))
    verdicts.append(PASS)
    run = run_of(tmp_path, f"sourceless-{named_later}", verdicts)
    assert inspected.events_of_kind(run, decided.DECISION_REFUSED)
    if named_later:
        assert accounting(run)["acted_on_slugs"] == [SLUG]
        (back,) = later_moves(run)
        assert SLUG in back and "moved from left to acted on" in back
        assert filed_once(filings, SLUG) == 0
    else:
        entry = left_entry(run, SLUG)
        assert entry["refused_files"] == []
        assert entry["reason_given"] is True
        assert filed_once(filings, SLUG) == 1


# ==========================================================================
# A rejection meeting a refusal or a spent allowance on one verdict
# ==========================================================================

#: What the verification after the rejecting one makes of the finding: a
#: repair carrying no decision, which is the restoring a rejection asks for,
#: or the identical repair under a corrected decision the coordinator accepts.
LATER_DECISIONS = {"without a decision": None,
                   "under a corrected decision": CORRECTED}


def with_decision(entry: dict, decision: dict | None) -> dict:
    """`entry` carrying a copy of `decision`, or as it is where that is
    None."""
    if decision is None:
        return dict(entry)
    return {**entry, DECISION: json.loads(json.dumps(decision))}


@pytest.mark.parametrize("later", sorted(LATER_DECISIONS))
def test_a_rejection_beside_a_refused_part_is_answered_only_under_a_decision(
        frozen_target, tmp_path, filings, later):
    """The verification a decided repair returns to rejects the decision,
    and on that same verdict a bound refuses the part of the fix reaching a
    frozen file, which leaves the finding by the refusal. The verification
    after it makes that very part. Made with no decision, the finding stays
    left — the refused part is made, but only a decision answers the
    rejection — and is filed once and named among the findings sent to the
    backlog. The control is the identical repair under a corrected decision:
    the finding is acted on, its move back says it was repaired under a
    corrected decision and made the refused file, and nothing is filed."""
    name = f"rejected-and-refused-{later}".replace(" ", "-")
    target, harness = frozen_target(name)
    run = drive(target, harness, tmp_path, [
        verdict(repair=[with_decision(REACHABLE, CHOSEN)]),
        verdict(repair=[REACHABLE, FROZEN], reject={SLUG: REJECTION_REASON}),
        verdict(repair=[with_decision(LIFTED, LATER_DECISIONS[later])]),
        PASS], name=name)
    assert run.code == 0, run.messages
    assert run.state["status"] == "completed"
    assert run.state["repair_pass_count"] == 3
    assert inspected.events_of_kind(run, bounds.EXTENSION_REFUSED)
    assert len(inspected.events_of_kind(run, DECISION_REJECTED)) == 1
    out, *back = later_moves(run)
    assert SLUG in out and "moved from acted on to left" in out
    if LATER_DECISIONS[later] is None:
        entry = left_entry(run, SLUG)
        assert entry["decision_rejected"] is True
        assert back == []
        assert accounting(run)["remainder_slugs"] == [SLUG]
        assert filed_once(filings, SLUG) == 1
        assert SLUG in named(run, "backlog")
    else:
        (back,) = back
        assert SLUG in back and "moved from left to acted on" in back
        assert "corrected decision" in back
        assert bounds.FROZEN_FILE in back
        assert accounting(run)["acted_on_slugs"] == [SLUG]
        assert filed_once(filings, SLUG) == 0
        assert SLUG in named(run, "repaired")


@pytest.mark.parametrize("later", sorted(LATER_DECISIONS))
def test_a_rejection_beside_a_spent_allowance_is_answered_only_under_a_decision(
        tmp_path, filings, later):
    """The verification a decided repair returns to rejects the decision,
    and on that same verdict asks for a correction of the finding that the
    spent correction allowance abandons — beside a repair of its own, which
    routes a pass so there is a later verification. That one repairs the
    finding again. With no decision the finding stays left as abandoned, is
    filed once and named among the findings abandoned; the control, the
    identical repair under a corrected decision, acts on it and files
    nothing."""
    run = run_of(tmp_path, f"rejected-and-abandoned-{later}", [
        verdict(correct=[correction_of(FOUND)]),
        verdict(repair=[repair_of(FOUND, CHOSEN)]),
        verdict(correct=[correction_of(FOUND)], repair=[UNRELATED],
                reject={SLUG: REJECTION_REASON}),
        verdict(repair=[repair_of(FOUND, LATER_DECISIONS[later])]),
        PASS], corrections=1)
    assert run.state["correction_pass_count"] == 1
    assert run.state["repair_pass_count"] == 3
    assert len(inspected.events_of_kind(run, DECISION_REJECTED)) == 1
    out, *back = later_moves(run)
    assert SLUG in out and "moved from acted on to left" in out
    if LATER_DECISIONS[later] is None:
        entry = left_entry(run, SLUG)
        assert entry["abandoned"] is True
        assert entry["decision_rejected"] is True
        assert back == []
        assert filed_once(filings, SLUG) == 1
        assert SLUG in named(run, "abandoned")
    else:
        (back,) = back
        assert SLUG in back and "corrected decision" in back
        assert accounting(run)["acted_on_slugs"] == [SLUG]
        assert accounting(run)["abandoned_slugs"] == []
        assert filed_once(filings, SLUG) == 0
        assert SLUG in named(run, "repaired")


# ==========================================================================
# A finding the checking stage raised itself
# ==========================================================================


def own_brief_in_the_queue(run) -> dict:
    (entry,) = [one["payload"] for one in run.queue
                if one["payload"].get("slug") == OWN_SLUG]
    return entry


def test_an_abandoned_finding_of_its_own_is_filed_from_its_classification(
        tmp_path, filings):
    """The verifier asks for a repair of its own finding with no pass left:
    the run leaves it, files it once through the one filing call as a brief
    built from the classification the verifier gave it — its title, its
    slug, its category, severity, confidence and effort — the finding and
    the correction it describes, its path, why the run left it and the
    run's workflow, and names it by its own slug among the findings
    abandoned."""
    run = run_of(tmp_path, "own-abandoned", [
        verdict(repair=[repair_of(FOUND)]), verdict(repair=[OWN])], repairs=1)
    (own,) = own_left(run).values()
    assert own["slug"] == OWN_SLUG
    assert own["abandoned"] is True
    assert own["finding"] == OWN
    assert accounting(run)["acted_on_slugs"] == [SLUG]
    assert accounting(run)["abandoned_slugs"] == []

    assert filed_once(filings, OWN_SLUG) == 1
    brief = own_brief_in_the_queue(run)
    for field in ("title", "slug", "category", "severity", "confidence",
                  "effort"):
        assert brief[field] == OWN_CLASSIFICATION[field], field
    assert brief["paths"] == [OWN["path"]]
    assert brief["workflow"] == WORKFLOW_NAME
    for words in (OWN["finding"], OWN["correction"], own["abandoned_because"]):
        assert words in brief["body"], words

    assert named(run, "abandoned") == {OWN_SLUG: own["abandoned_because"]}
    (filed,) = inspected.filed_events(run)
    assert filed["filed"] == 1
    assert UNCLASSIFIED not in filed["message"]


def test_the_same_finding_without_a_classification_is_dropped_with_that_reason(
        tmp_path, filings):
    """The control: identical but for the classification. Nothing is filed
    for it and nothing made up stands in for it: the run's completion line
    counts the drop under that reason, and the report names the finding by
    the key it was followed by among the discarded, with that reason and
    why the run left it."""
    run = run_of(tmp_path, "own-unclassified", [
        verdict(repair=[repair_of(FOUND)]), verdict(repair=[UNCLASSIFIED_OWN])],
        repairs=1)
    key = story_inspection.own_finding_key(UNCLASSIFIED_OWN, (SLUG,))
    (own,) = own_left(run).values()
    assert own["slug"] == key
    assert filed_once(filings, OWN_SLUG) == 0
    assert filed_once(filings, key) == 0
    assert [one for one in run.queue
            if one["payload"].get("paths") == [OWN["path"]]] == []
    (filed,) = inspected.filed_events(run)
    assert filed["filed"] == 0
    assert filed["dropped"] == 1
    assert UNCLASSIFIED in filed["message"]
    discarded = clause(run, "discarded")
    assert f"{key} ({UNCLASSIFIED}" in discarded
    assert own["abandoned_because"] in discarded
    assert named(run, "abandoned") == {}


def test_a_rejected_decision_on_a_finding_of_its_own_is_filed_like_a_routed_one(
        tmp_path, filings):
    """The verifier's own decided repair, rejected by the verification its
    pass returns to by the slug of its own, is left with the rejection as
    its ground and filed from its classification; the report names it by
    that slug among the findings sent to the backlog. The run whose
    rejection names the finding by its path, matching nothing, is the
    control: nothing of the verifier's own is left or filed there."""
    decided_own = {**OWN, DECISION: dict(CHOSEN)}
    run = run_of(tmp_path, "own-rejected", [
        verdict(repair=[repair_of(FOUND), decided_own]),
        verdict(reject={OWN_SLUG: REJECTION_REASON})])
    own = own_left(run)[OWN_SLUG]
    assert own["decision_rejected"] is True
    assert REJECTION_REASON in own["reason"]
    assert filed_once(filings, OWN_SLUG) == 1
    assert own["reason"] in own_brief_in_the_queue(run)["body"]
    assert named(run, "backlog") == {OWN_SLUG: own["reason"]}

    unmatched = run_of(tmp_path, "own-rejected-by-path", [
        verdict(repair=[repair_of(FOUND), decided_own]),
        verdict(reject={OWN["path"]: REJECTION_REASON})])
    assert own_left(unmatched) == {}
    assert filed_once(filings, OWN_SLUG) == 1
    assert inspected.filed_events(unmatched) == []


def test_a_run_that_leaves_none_of_its_own_reads_as_a_run_that_raised_none(
        tmp_path, filings):
    """The verifier's own finding is repaired within the run. The accounting,
    its events, the filing and the report's account are then exactly those
    of a run whose second verdict raised no finding of its own at all — no
    record of the checking stage's own findings, no completion line, nothing
    named. The abandoned run above, where the same readers find each of
    them, is the control."""
    own_fixed = run_of(tmp_path, "own-fixed", [
        verdict(repair=[repair_of(FOUND)]), verdict(repair=[OWN]), PASS],
        repairs=2)
    none_raised = run_of(tmp_path, "none-raised", [
        verdict(repair=[repair_of(FOUND)]), verdict(repair=[repair_of(FOUND)]),
        PASS], repairs=2)
    assert "own_left" not in accounting(own_fixed)
    assert set(accounting(own_fixed)) == set(accounting(none_raised))
    for key, value in accounting(none_raised).items():
        assert accounting(own_fixed)[key] == value, key
    assert [one["message"] for one in inspected.accounting_events(own_fixed)] \
        == [one["message"] for one in inspected.accounting_events(none_raised)]
    assert inspected.filed_events(own_fixed) == \
        inspected.filed_events(none_raised) == []
    assert decided.findings_section(own_fixed) == \
        decided.findings_section(none_raised)
    assert OWN_SLUG not in report(own_fixed)
    assert filed_once(filings, OWN_SLUG) == 0


# ==========================================================================
# The rule, driven as a function
# ==========================================================================


#: The file a bound refuses below, and the ground a cause leaves a finding on.
REFUSED_FILE = "docs/refused.md"
GROUND = "MARKER-GROUND what the coordinator recorded about it"


def accounting_dir(tmp_path: Path, name: str, *routed: dict) -> Path:
    """A run directory holding the findings record the accounting reads,
    routing `routed`."""
    run_dir = tmp_path / name
    run_dir.mkdir()
    story_inspection.write_findings(run_dir, inspected.FINDINGS_ARTIFACT, 1,
                                    STORY_ID, ran=True, findings=list(routed))
    return run_dir


def account(run_dir: Path, verdict_: dict, **facts) -> None:
    story_inspection.account_after_verdict(
        run_dir, run_dir / "target", {}, run_dir / "harness", STORY_ID,
        artifact=inspected.FINDINGS_ARTIFACT, attempt=1, verdict=verdict_,
        **facts)


def artifact_of(run_dir: Path) -> dict:
    return json.loads((run_dir / story_inspection.accounting_artifact_file(
        inspected.FINDINGS_ARTIFACT, 1)).read_text(encoding="utf-8"))


def artifact_bytes(run_dir: Path) -> bytes:
    return (run_dir / story_inspection.accounting_artifact_file(
        inspected.FINDINGS_ARTIFACT, 1)).read_bytes()


def accounting_lines(run_dir: Path) -> list[str]:
    """The accounting's lines in the run's events.log, in order, told apart
    by the label every one of them opens with."""
    return [line for line in (run_dir / "events.log").read_text(
        encoding="utf-8").splitlines() if "accounting for the findings" in line]


def test_a_later_verdict_that_changes_nothing_writes_nothing(tmp_path):
    """The first verdict leaves the routed finding; a later one saying
    nothing about it leaves the artifact byte for byte and appends no event.
    The control is the same call with a verdict naming it, which rewrites the
    artifact and appends one event naming the finding, the direction and the
    cause."""
    run_dir = accounting_dir(tmp_path, "nothing-changes", FOUND)
    account(run_dir, PASS)
    before = artifact_bytes(run_dir)
    lines = accounting_lines(run_dir)
    assert artifact_of(run_dir)["remainder_slugs"] == [SLUG]

    account(run_dir, verdict(correct=[correction_of(OTHER_FOUND)]))
    account(run_dir, PASS)
    assert artifact_bytes(run_dir) == before
    assert accounting_lines(run_dir) == lines

    account(run_dir, verdict(repair=[repair_of(FOUND)]))
    assert artifact_bytes(run_dir) != before
    assert artifact_of(run_dir)["acted_on_slugs"] == [SLUG]
    (moved,) = accounting_lines(run_dir)[len(lines):]
    assert SLUG in moved
    assert "moved from left to acted on" in moved
    assert "named in an entry the coordinator accepted" in moved


#: Each cause a verdict can leave a finding on, as the facts the coordinator
#: hands the accounting and the verdict it hands them beside.
CAUSES = {
    "refused for a bound": (
        verdict(decline=[{"slug": SLUG, "reason": GROUND}]),
        {"refused_slugs": (SLUG,), "refused_files": {SLUG: [REFUSED_FILE]}}),
    "a spent allowance": (
        verdict(repair=[repair_of(FOUND)]), {"abandoned": {SLUG: GROUND}}),
    "a rejected decision": (
        verdict(reject={SLUG: GROUND}), {"rejected": {SLUG: GROUND}}),
    "a declined postponed repair": (
        verdict(decline=[{"slug": SLUG, "reason": GROUND}]),
        {"postponed": (SLUG,)}),
}


@pytest.mark.parametrize("cause", sorted(CAUSES))
def test_both_writers_record_a_left_finding_alike(tmp_path, cause):
    """A finding left on the first verdict of an attempt and the same finding
    left by the same cause on a later verdict, after the first acted on it,
    carry the same entry field for field — the files owed and whether a
    decision was rejected included, explicitly on both. The later path's
    move is said in an event naming the cause."""
    leaving, facts = CAUSES[cause]
    first = accounting_dir(tmp_path, "left-first", FOUND)
    account(first, leaving, **facts)
    later = accounting_dir(tmp_path, "left-later", FOUND)
    account(later, verdict(repair=[repair_of(FOUND)]))
    assert artifact_of(later)["acted_on_slugs"] == [SLUG]
    account(later, leaving, **facts)

    (written_first,) = artifact_of(first)["declined"]
    (written_later,) = artifact_of(later)["declined"]
    assert written_first == written_later
    for field in ("refused_files", "decision_rejected"):
        assert field in written_first, field
    (moved,) = accounting_lines(later)[1:]
    assert SLUG in moved and "moved from acted on to left" in moved


def entry_of(run_dir: Path, key: str) -> dict | None:
    """What the accounting records of one finding, routed or the checking
    stage's own: its left entry, or None where it stands acted on."""
    recorded = artifact_of(run_dir)
    for entry in list(recorded["declined"]) + list(recorded.get("own_left", ())):
        if entry["slug"] == key:
            return {name: value for name, value in entry.items()
                    if name not in ("slug", "finding")}
    return None


def test_one_rule_decides_a_routed_finding_and_one_of_the_checking_stages_own(
        tmp_path):
    """The same verdicts, naming a routed finding and a finding of the
    checking stage's own side by side and handing the accounting the same
    facts about each, leave and bring back both at the same steps with the
    same entries: acted on under a decision, left by its rejection, still
    left by a repair carrying no decision, back under a corrected decision,
    left again with a file a bound refused owed, and back once that file is
    made."""
    run_dir = accounting_dir(tmp_path, "both-kinds", FOUND)
    own_key = OWN_SLUG
    owns = {"own_findings": {own_key: OWN}}

    def both(decision=None, path=None):
        routed, own = repair_of(FOUND, decision), dict(OWN)
        own.pop(DECISION, None)
        if decision is not None:
            own[DECISION] = json.loads(json.dumps(decision))
        if path is not None:
            routed, own = {**routed, "path": path}, {**own, "path": path}
        return verdict(repair=[routed, own])

    keys = (SLUG, own_key)
    steps = [
        (both(CHOSEN), {"decided": keys}, None),
        (verdict(reject={SLUG: GROUND, own_key: GROUND}),
         {"rejected": {SLUG: GROUND, own_key: GROUND}}, True),
        (both(), {}, True),
        (both(CORRECTED), {"decided": keys}, None),
        (verdict(), {"refused_slugs": keys,
                     "refused_files": {key: [REFUSED_FILE] for key in keys}},
         True),
        (both(path=REFUSED_FILE), {}, None),
    ]
    for number, (step, facts, left) in enumerate(steps, start=1):
        account(run_dir, step, **owns, **facts)
        routed, own = entry_of(run_dir, SLUG), entry_of(run_dir, own_key)
        assert routed == own, (number, routed, own)
        assert (routed is not None) is bool(left), (number, routed)


#: Each cause that can leave a finding on the very verdict that rejects its
#: decision: the verdict as the coordinator hands it over, the facts beside
#: the rejection, and the file a later repair must make where a bound refused
#: one.
BESIDE_A_REJECTION = {
    "a part refused for a bound": (
        verdict(decline=[{"slug": SLUG, "reason": GROUND}],
                reject={SLUG: GROUND}),
        {"refused_slugs": (SLUG,), "refused_files": {SLUG: [REFUSED_FILE]}},
        REFUSED_FILE),
    "a decision refused for naming no source": (
        verdict(decline=[{"slug": SLUG, "reason": GROUND}],
                reject={SLUG: GROUND}),
        {"refused_slugs": (SLUG,)}, None),
    "a spent allowance": (
        verdict(repair=[repair_of(FOUND)], reject={SLUG: GROUND}),
        {"abandoned": {SLUG: GROUND}}, None),
}


@pytest.mark.parametrize("later", sorted(LATER_DECISIONS))
@pytest.mark.parametrize("cause", sorted(BESIDE_A_REJECTION))
def test_the_left_entry_records_a_rejection_whichever_cause_left_it(
        tmp_path, cause, later):
    """A decided repair is acted on, and the next verdict rejects its
    decision while a refusal or a spent allowance leaves it too. Whichever
    cause the rule reads first, the entry records the rejection. The verdict
    after that names the finding in an accepted repair, making any refused
    file as well. With no decision it changes nothing and writes nothing;
    the control, the same repair under an accepted decision, brings it back
    and says so."""
    rejecting_verdict, facts, owed = BESIDE_A_REJECTION[cause]
    run_dir = accounting_dir(tmp_path, "beside-a-rejection", FOUND)
    account(run_dir, verdict(repair=[repair_of(FOUND, CHOSEN)]),
            decided=(SLUG,))
    assert entry_of(run_dir, SLUG) is None
    account(run_dir, rejecting_verdict, rejected={SLUG: GROUND}, **facts)
    assert entry_of(run_dir, SLUG)["decision_rejected"] is True

    decision = LATER_DECISIONS[later]
    repair = repair_of(FOUND, decision)
    if owed:
        repair = {**repair, "path": owed}
    before, lines = artifact_bytes(run_dir), accounting_lines(run_dir)
    account(run_dir, verdict(repair=[repair]),
            decided=(SLUG,) if decision else ())
    if decision is None:
        assert artifact_bytes(run_dir) == before
        assert accounting_lines(run_dir) == lines
        assert entry_of(run_dir, SLUG)["decision_rejected"] is True
    else:
        assert entry_of(run_dir, SLUG) is None
        (back,) = accounting_lines(run_dir)[len(lines):]
        assert SLUG in back and "moved from left to acted on" in back


# ==========================================================================
# The verifier prompts this harness ships
# ==========================================================================


def repair_slot(text: str) -> str:
    return decided.repair_slot_paragraph(text)


def repairable_passage(text: str) -> str:
    return paragraph_containing(text, REPAIRABLE_PHRASE)


#: The passage the inspection's findings are rendered beneath, found by the
#: sentence this story wrote into it.
LATER_NAMING = "On a returning verification, naming a finding"


def inspection_passage(text: str) -> str:
    return paragraph_containing(text, LATER_NAMING)


@pytest.mark.parametrize("name", sorted(shipped_prompt_texts()))
def test_each_prompt_says_what_each_response_to_a_rejection_does(name):
    """Restoring alone leaves the finding for the backlog; repairing it again
    under a corrected decision counts it as acted on; a repair naming it with
    no decision is the restoring and not a fresh repair."""
    slot = repair_slot(shipped_prompt_texts()[name])
    leaves = sentence_carrying(slot, "leaves the finding, routed or")
    assert "with the rejection as its ground" in leaves
    responses = sentence_carrying(slot, "Restoring alone leaves the finding")
    assert "filed as a brief" in responses
    assert "corrected choice" in responses
    assert f"`{DECISION}`" in responses
    assert "counts it as acted on" in responses
    assert "with no decision is read as the restoring" in responses


@pytest.mark.parametrize("name", sorted(shipped_prompt_texts()))
def test_each_prompt_says_every_postponed_repair_must_be_answered(name):
    """The returning verification answers each postponed repair by asking
    for it again or declining it by name, and one left unanswered sends it
    back once, after which it is declined without a reason and filed."""
    passage = repairable_passage(shipped_prompt_texts()[name])
    answer = sentence_carrying(passage, "must answer each of them")
    assert "deferred_repairable_findings" in answer
    assert "ask for it again" in answer
    assert "decline it by name in declined_findings" in answer
    unanswered = sentence_carrying(passage, "sent back once")
    assert "leaves one unanswered" in unanswered
    assert "treated as declined without a reason" in unanswered
    assert "deferred rather than lost" not in passage


@pytest.mark.parametrize("name", sorted(shipped_prompt_texts()))
def test_each_prompt_asks_every_finding_of_its_own_for_a_slug_and_a_classification(
        name):
    """Every finding the verifier raises itself, decided or not, names a slug
    of its own and carries the classification a brief needs, and the prompt
    says why: one the run leaves is filed from those words, and one left
    without them is dropped."""
    passage = repairable_passage(shipped_prompt_texts()[name])
    own = sentence_carrying(passage, "Every finding you raise yourself")
    assert "decided or not" in own
    assert "a slug of your own" in own
    assert f"`{CLASSIFICATION}`" in own
    for judgement in ("title", "category", "severity", "confidence", "effort"):
        assert judgement in own, judgement
    assert "filed as a brief from those words" in own
    assert "dropped rather than filed" in own


@pytest.mark.parametrize("name", sorted(shipped_prompt_texts()))
def test_each_prompt_says_a_later_naming_counts(name):
    """On a returning verification, naming a finding counts even where an
    earlier verdict of the attempt left it; a refused part counts only once
    made; a finding left unnamed keeps what was decided."""
    passage = inspection_passage(shipped_prompt_texts()[name])
    later = sentence_carrying(passage, LATER_NAMING)
    assert "counts even where an earlier verdict of this attempt left it" in later
    assert "counts only once that very part is made" in later
    assert "keeps what was decided before" in later
    filed = sentence_carrying(passage, "after your verdict")
    assert "the latest verification of this attempt to deal with it left" in filed


#: The words that find each sentence this story wrote, by the passage holding
#: it — the reading the parity assertion compares.
STORY_SENTENCES = {
    "repair slot": (repair_slot, ("leaves the finding, routed or",
                                  "Restoring alone leaves the finding")),
    "repairable": (repairable_passage, ("Every finding you raise yourself",
                                        "must answer each of them",
                                        "sent back once")),
    "inspection": (inspection_passage, (LATER_NAMING, "after your verdict")),
}


def story_sentences(text: str) -> tuple:
    return tuple(
        (key, tuple(sentence_carrying(read(text), needle) for needle in needles))
        for key, (read, needles) in STORY_SENTENCES.items())


def test_this_storys_sentences_read_the_same_in_both_prompts():
    """Both shipped verifier prompts carry every sentence this story wrote, in
    the same words — each found, so the equality is between found sentences
    rather than between two empty readings."""
    texts = shipped_prompt_texts()
    assert len(texts) > 1, texts
    readings = {story_sentences(text) for text in texts.values()}
    assert len(readings) == 1, readings


def test_the_comparison_tells_a_prompt_whose_sentence_drifted_apart():
    """Control for the equality above: one prompt with one of this story's
    sentences reworded on a line it occupies reads differently through the
    same reading."""
    texts = shipped_prompt_texts()
    name = sorted(texts)[0]
    drifted = texts[name].replace("leaves one unanswered is sent back once",
                                  "leaves one unanswered at all is sent back "
                                  "once", 1)
    assert drifted != texts[name]
    readings = {story_sentences(text) for text in {**texts, name: drifted}.values()}
    assert len(readings) == 2


# ==========================================================================
# The schemas this harness ships
# ==========================================================================

#: The judgements a classification carries in a brief's own vocabulary.
VOCABULARY = ("category", "severity", "confidence", "effort")


def finding_items() -> dict:
    """The two kinds of finding a verdict carries, by field."""
    properties = VERDICT_SCHEMA["properties"]
    return {field: properties[field]["items"]
            for field in ("correctable_findings", "repairable_findings")}


def vocabulary_differences(classification: dict, brief: dict) -> list[str]:
    """Each judgement whose declared values the classification and the brief
    schema do not agree on."""
    return [name for name in VOCABULARY
            if classification["properties"][name].get("enum")
            != brief["properties"][name].get("enum")
            or classification["properties"][name].get("type")
            != brief["properties"][name].get("type")]


@pytest.mark.parametrize("field", sorted(finding_items()))
def test_a_finding_may_carry_a_classification_in_a_briefs_vocabulary(field):
    """Optional on the finding, which requires what it required, and no
    property of it required either: an incomplete one costs the brief, never
    the verdict. Its slug and title are words, and its category, severity,
    confidence and effort are declared exactly as a brief declares them."""
    item = finding_items()[field]
    classification = item["properties"][CLASSIFICATION]
    assert CLASSIFICATION not in item.get("required", [])
    assert classification["type"] == "object"
    assert not classification.get("required")
    for name in ("slug", "title"):
        assert classification["properties"][name]["type"] == "string", name
    assert vocabulary_differences(classification, BRIEF_SCHEMA) == []


def test_the_vocabulary_comparison_reports_a_value_the_brief_does_not_declare():
    """Control for the equality above: the same comparison against a copy of
    the brief schema with one category added reports the category."""
    classification = finding_items()["repairable_findings"]["properties"][
        CLASSIFICATION]
    widened = json.loads(json.dumps(BRIEF_SCHEMA))
    widened["properties"]["category"]["enum"].append("a-category-of-no-brief")
    assert vocabulary_differences(classification, widened) == ["category"]


def test_a_verdict_carrying_a_classification_validates_with_or_without_one():
    """Every verdict built above, and the same verdict with an incomplete
    classification, validates — while one carrying a judgement outside the
    vocabulary is refused as malformed."""
    for built in (verdict(repair=[OWN]), verdict(repair=[UNCLASSIFIED_OWN]),
                  verdict(correct=[OWN]), BOTH_KINDS,
                  verdict(repair=[{**OWN, CLASSIFICATION: {"slug": OWN_SLUG}}])):
        assert schema_validator.validate(built, VERDICT_SCHEMA) == []
    outside = {**OWN, CLASSIFICATION: {**OWN_CLASSIFICATION,
                                       "severity": "the highest"}}
    assert schema_validator.validate(verdict(repair=[outside]), VERDICT_SCHEMA)


def description(*path: str, schema: dict = VERDICT_SCHEMA) -> str:
    node = schema
    for key in path:
        node = node[key]
    return flowed(node["description"])


def test_the_verdict_schema_states_the_rule_where_each_field_is_described():
    """The rejection says a finding comes back only under a decision and a
    repair with none is the restoring; the decision and its source say a
    refused decision's finding is filed unless a later verification names it;
    the declined findings say the latest verification decides, a later naming
    counts, a refused part counts once made, and a postponed repair is
    declined by naming it there or sent back once."""
    properties = VERDICT_SCHEMA["properties"]
    rejected = description("properties", REJECTED)
    assert "comes back only under a decision" in rejected
    assert "read as the restoring" in rejected
    assert "routed to this story or raised by you" in rejected
    reason = flowed(properties[REJECTED]["items"]["properties"]["reason"][
        "description"])
    assert "unless a later repair in the same attempt names it" in reason
    item = properties["repairable_findings"]["items"]["properties"]
    assert "unless a later verification of the attempt names it" in flowed(
        item[DECISION]["description"])
    assert "unless a later verification of the attempt names it" in flowed(
        item[DECISION]["properties"]["source"]["description"])
    declined = description("properties", "declined_findings")
    assert "the latest verification of the attempt that deals with a finding " \
        "decides what became of it" in declined
    assert "even where an earlier verdict of the attempt left it" in declined
    assert "for each refused file" in declined
    assert "sends that verification back once" in declined


def test_the_correction_record_says_each_postponed_repair_must_be_answered():
    deferred = flowed(CORRECTION_SCHEMA["properties"][
        story_coordinator.DEFERRED_REPAIRABLE_FINDINGS]["description"])
    assert "must answer every repair it carries" in deferred
    assert "sent back once" in deferred
    assert "treated as declined without a reason, never escalated" in deferred


def test_the_self_route_record_declares_the_new_cause_and_what_it_is():
    failure = SELF_ROUTE_SCHEMA["properties"]["failure"]
    assert UNANSWERED in failure["enum"]
    said = flowed(failure["description"])
    assert UNANSWERED in said
    assert "sent back once and never escalates" in said


#: Sentences each shipped text said before this story and none may say now,
#: each a rule stated without the exception this story made: that every
#: finding the first verdict left is filed, that a postponed repair cannot be
#: lost, that a rejected or a refused decision's finding is filed whatever a
#: later verification does, and that a finding the checking stage raised
#: itself reaches no brief. Carried as literals, so the reading needs nothing
#: resolved out of history to know what it is looking for.
SUPERSEDED = (
    "every finding here whose slug no entry names is filed as a brief",
    "every routed finding whose slug no blocking issue, correctable finding "
    "or repairable finding names",
    "the repairable findings are deferred rather than lost",
    "sends a routed finding to the backlog with the rejection as its ground",
    "a routed finding whose decision is rejected is left by the run with the "
    "rejection as its ground and filed at the run's completion",
    "a routed finding is declined with the refusal as its ground and filed at "
    "the run's completion",
    "is refused by the coordinator and its finding filed, so",
    "no brief is filed for it",
    "no brief filed for it",
)


def shipped_texts() -> dict[str, str]:
    """Every prompt template and every schema this repository ships, by
    path, flowed — the texts a stage, a schema's reader or a developer meets
    the rule in."""
    found = sorted((REPO_ROOT / "prompts").glob("*.md")) \
        + sorted((REPO_ROOT / "schemas").glob("*.json"))
    return {path.relative_to(REPO_ROOT).as_posix():
            flowed(path.read_text(encoding="utf-8")) for path in found}


def superseded_in(text: str) -> list[str]:
    return [sentence for sentence in SUPERSEDED if sentence in text]


@pytest.mark.parametrize("path", sorted(shipped_texts()))
def test_no_shipped_text_states_the_superseded_rule(path):
    """No prompt and no schema still says, without the exception this story
    made, that a finding is filed whatever a later verification did, that a
    postponed repair cannot be lost, or that a finding the checking stage
    raised itself reaches no brief.

    The two history schemas are read like every other schema. Their
    descriptions of the abandonment count said the last of these — that a
    finding the verifier raised itself has no brief filed for it — which
    this story makes untrue. The story's scope names both files to be left
    alone, for a reason saying what that protects: the kinds and fields the
    history records carry. That is held below, by a comparison of what each
    declares that a rewritten description leaves equal, so correcting the
    sentence is open to the run without touching what the entry protects."""
    assert superseded_in(shipped_texts()[path]) == []


def test_the_reading_reports_each_superseded_sentence_planted_back():
    """Control for the absences above: each sentence written back into a
    shipped text is reported by the same reading, so an empty result is the
    sentence being absent rather than a reading that finds nothing."""
    texts = shipped_texts()
    assert texts
    host = texts[sorted(texts)[0]]
    for sentence in SUPERSEDED:
        assert superseded_in(f"{host} {sentence}.") == [sentence]


# ==========================================================================
# The workflows and the standard this harness ships
# ==========================================================================


def shipped_checking_stages() -> dict[str, dict]:
    """The stage declaring an inspection in each workflow this repository
    ships, by workflow — the stage the passes and the send-back belong to.
    Found through the declarations rather than named."""
    found = {}
    for path in sorted((REPO_ROOT / "workflows").glob("*.json")):
        stages = json.loads(path.read_text(encoding="utf-8"))["stages"]
        (stage,) = [one for one in stages if one.get("inspection")]
        found[path.stem] = stage
    return found


#: What each reason has to say about the number it explains: that it guards
#: against runaway processing rather than measuring the work.
RUNAWAY = "runaway"


@pytest.mark.parametrize("name", sorted(shipped_checking_stages()))
def test_each_pass_budget_records_that_it_guards_against_runaway_processing(name):
    """Both passes are declared with a budget the pre-flight accepts and a
    reason beside it stating that number, whatever it is, and saying the
    number is a guard against runaway processing. No number is held here:
    the budgets are configured values."""
    stage = shipped_checking_stages()[name]
    for kind in ("correction_pass", "repair_pass"):
        declaration = stage[kind]
        reason = declaration.get("budget" + REASON_SUFFIX, "")
        assert states_the_number(reason, declaration["budget"]), (name, kind)
        assert RUNAWAY in reason.lower(), (name, kind)


@pytest.mark.parametrize("name", sorted(shipped_checking_stages()))
def test_the_checking_stage_declares_a_send_back_budget_of_its_own(name):
    """The checking stage splits its re-run budget, so a send-back spends the
    record-keeping half and never the re-run kept for a crashed invocation,
    and its reason states its number, names the send-back and says it guards
    against runaway processing."""
    stage = shipped_checking_stages()[name]
    assert BOOKKEEPING_KEY in stage
    assert BUDGET_KEY in stage
    reason = stage.get(BOOKKEEPING_KEY + REASON_SUFFIX, "")
    assert states_the_number(reason, stage[BOOKKEEPING_KEY]), name
    assert "postponed" in reason
    assert RUNAWAY in reason.lower()


def test_the_testing_standard_says_no_test_pins_a_configurable_value():
    standard = flowed((REPO_ROOT / ".harness" / "standards" / "testing.md")
                      .read_text(encoding="utf-8"))
    rule = sentence_carrying(standard, "No test pins a configurable value")
    assert rule
    reads = sentence_carrying(standard, "reads the value from where it is declared")
    assert "supplies its own in a fixture" in reads
    held = sentence_carrying(standard, "against an older revision of itself")
    assert "the revisions of the story that made it" in held
    assert "never to the current file" in held


# ==========================================================================
# What this story left alone
# ==========================================================================

#: The paths the story's own scope.do_not_modify names that no fix of this
#: story's may touch, resolved through the shared story range so the
#: comparison stays honest once the story commits. The two history schemas the
#: same list names are held below by what their entry's reason says it
#: protects — the kinds and fields they declare — rather than by an empty
#: diff, because a description there stating the superseded rule is a text
#: this story makes untrue.
LEFT_ALONE = [
    "prompts/assist.md",
    "prompts/story-implementer.md",
    "prompts/story-tester.md",
    "prompts/documenter.md",
    "prompts/refactor-implementer.md",
    "prompts/inspector.md",
    "rules/execution-rules.json",
    "orchestration/inspection.py",
    "schemas/story-brief.schema.json",
    "tests/test_the_decisions_a_run_made_are_read_riskiest_first.py",
]


def test_the_prompts_the_rules_the_filing_and_the_brief_are_untouched():
    assert conftest.story_diff(LEFT_ALONE, validation_file=THIS_MODULE) == ""


def test_the_same_comparison_tells_an_edited_path_apart(tmp_path):
    """Control for the absence above, over histories built here: one story
    leaves those paths alone and one edits them, and the identical call is
    empty for the first and names every path for the second."""
    respecting = conftest.constructed_story(tmp_path, respected=LEFT_ALONE,
                                            name="left-alone")
    assert conftest.constructed_story_diff(respecting, LEFT_ALONE) == ""
    violating = conftest.constructed_story(tmp_path, violated=LEFT_ALONE,
                                           name="edited")
    reported_paths = conftest.constructed_story_diff(violating, LEFT_ALONE)
    for path in LEFT_ALONE:
        assert path in reported_paths, path


#: The two history schemas, and the shape of what each declares: every event
#: kind, and every field a record may carry.
HISTORY_SCHEMAS = ("schemas/execution-history.schema.json",
                   "schemas/cross-run-history.schema.json")


def declared_shape(schema: dict) -> dict:
    """Every property set and enum a schema declares, by where it sits,
    descriptions left out — the kinds and fields its records may carry."""
    shape: dict = {}

    def walk(node, where: str) -> None:
        if isinstance(node, dict):
            if "properties" in node:
                shape[f"{where}/properties"] = sorted(node["properties"])
            if "enum" in node:
                shape[f"{where}/enum"] = list(node["enum"])
            if "required" in node:
                shape[f"{where}/required"] = sorted(node["required"])
            for key, value in node.items():
                if key != "description":
                    walk(value, f"{where}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{where}/{index}")

    walk(schema, "")
    return shape


@pytest.mark.parametrize("relative", HISTORY_SCHEMAS)
def test_the_history_schemas_declare_the_kinds_and_fields_they_declared(relative):
    """No event kind and no history field is added or removed across this
    story's own range: every move is reported in the accounting and
    self-route kinds already declared."""
    before = json.loads(conftest.repository_file_at(
        relative, validation_file=THIS_MODULE, bound=BASELINE))
    after = json.loads(conftest.repository_file_at(
        relative, validation_file=THIS_MODULE, bound=ENDPOINT))
    assert declared_shape(before) == declared_shape(after)


def test_the_shape_comparison_reports_a_kind_added():
    """Control for the equality above: the baseline execution history with
    one kind added, and with one field added, each reads differently by the
    same comparison — while a description rewritten reads the same."""
    relative = HISTORY_SCHEMAS[0]
    before = json.loads(conftest.repository_file_at(
        relative, validation_file=THIS_MODULE, bound=BASELINE))
    grown = json.loads(json.dumps(before))
    grown["items"]["properties"]["event"]["enum"].append("a-kind-no-run-writes")
    assert declared_shape(grown) != declared_shape(before)
    widened = json.loads(json.dumps(before))
    widened["items"]["properties"]["a-field-no-run-writes"] = {"type": "string"}
    assert declared_shape(widened) != declared_shape(before)
    reworded = json.loads(json.dumps(before))
    reworded["description"] = "a description written again"
    assert declared_shape(reworded) == declared_shape(before)


#: The completion report's layout and its decisions part, by the function that
#: renders each.
RENDERING = ("_findings_account_section", "_named_findings", "_named_decisions")


@pytest.mark.parametrize("name", RENDERING)
def test_the_completion_reports_layout_and_decisions_are_what_they_were(name):
    """Compared across this module's own story range: the account a report
    renders, and how it renders the decisions, read as they did before."""
    relative = "orchestration/story_coordinator.py"
    before = conftest.function_source_at(relative, name,
                                         validation_file=THIS_MODULE,
                                         bound=BASELINE)
    after = conftest.function_source_at(relative, name,
                                        validation_file=THIS_MODULE,
                                        bound=ENDPOINT)
    assert before == after


def test_the_rendering_comparison_reports_a_function_that_moved():
    """Control for the comparison above: the baseline text with one word of
    a clause changed reads differently from the endpoint."""
    relative = "orchestration/story_coordinator.py"
    before = conftest.repository_file_at(relative, validation_file=THIS_MODULE,
                                         bound=BASELINE)
    after = conftest.repository_file_at(relative, validation_file=THIS_MODULE,
                                        bound=ENDPOINT)
    moved = before.replace("Sent to the backlog: ", "Filed for later: ", 1)
    assert moved != before
    assert conftest.function_source(moved, RENDERING[0]) != \
        conftest.function_source(after, RENDERING[0])
