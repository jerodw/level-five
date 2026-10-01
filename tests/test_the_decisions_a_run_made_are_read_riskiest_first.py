"""The decisions a run made on the developer's behalf are read on the surfaces
a reviewer already reads, riskiest first.

Since story-219 a run may settle a design question its approved plan left
open, provided it names an existing source that answers it, and each such
decision — with any rejection of it by the verification that follows — is
written to a decision log committed with the story. Nobody read that log unless
they already knew it was there: the report a finished run leaves said nothing
about decisions, and neither did the assistant a developer asks how a story
went. This story puts the decisions on both, ordered so the ones most costly to
get wrong are read first.

The subjects, in the order the criteria state them:

  * **the report's decisions part**, driven end to end under the fake runner
    against the decided-repair workflow story-219's module builds. Each
    decision is named after everything the findings section said before, with
    the finding it was made for, its question, answer, source, rationale, blast
    radius and confidence, and the decisions are listed costliest first, then
    least sure, then as made — shown on decisions made in an order other than
    that one, with a crossing pair the costlier and surer of which still comes
    first, and with a tie kept in the order it was made;
  * **a rejected decision**, listed in its place and marked rejected with the
    verification's reason, while its finding is still named among those sent
    to the backlog — and a finding decided twice, whose rejection lands on the
    decision it rejected and not on the one made after it;
  * **the report against the log**: what the decisions part names, and which
    entries it marks rejected, is read against the decision log the same run
    committed, and a decision the coordinator refused for naming no source is
    in neither;
  * **the control**: a run whose verdicts carry no decision leaves the report
    a run identical to it but for the decisions leaves, with the decisions
    part removed — and decisions made before a retry are still named after it;
  * **a workflow declaring no inspection**, whose run names its decision and
    says nothing about an inspection, and whose run without the decision has
    no account at all;
  * **a fault in reading the decisions**, under either workflow, which leaves
    the report written, saying the account could not be derived and why, and
    the run completing as it would have;
  * **the assistant's prompt**, read as `scripts/l5-assist` hands it to a
    session, which states the duty, and which is then held against a driven
    run: where it sends the agent for a story's decisions is where that run
    committed them, the configuration key it names is the one that moves them,
    the fields it orders by are fields the committed records carry, and before
    the story merges it sends the agent to the story's branch in the tree that
    run worked in, which is not the tree the run was invoked from;
  * **what the story left alone**, against this module's own story range.

Every absence asserted here is demonstrated to be able to fail:

  * "a decision no verification rejected carries no rejected mark" sits beside
    the rejected entry of the same report, which the same reading finds marked;
  * "a run that recorded no decision renders no decisions part" sits beside
    the run differing from it only in the decisions, whose part the same
    reading finds;
  * "a refused decision is not listed" sits beside the decision of the same
    verdict that cited its source, which the same reading finds listed — and
    the refused decision's question is still found in the section, in the
    clause sending its finding to the backlog;
  * "the run declaring no inspection says nothing about one" sits beside the
    same verdicts under the inspecting workflow, whose section the same reading
    finds naming its inspection; and its run without the decision, which has no
    findings heading, sits beside the run with it, which has one;
  * "a faulted reading names no decision" sits beside the same run unfaulted;
  * "the tree a run was invoked from holds no decision log before the story
    merges" sits beside the tree the run worked in, which the same path finds
    holding the log the run committed;
  * "the decisions part writes no count" sits beside the rest of the same
    report, which the same predicate finds carrying a number;
  * "the renderers reach into the inspection module for nothing" sits beside
    `_complete`, which the same reading finds reaching for the account;
  * "the story left these paths and functions alone and recorded no new event
    kind" sits beside a path and a function this story did change, which the
    same comparisons report, and beside copies of the source carrying one more
    emitter, which the same scans report; and "every sentence the
    section wrote before is written still, in its order" sits beside the same
    reading in the other direction, which finds the words this story added
    missing from the baseline.

Nothing here reaches a model: the sibling module's guard on
`agent_runner.run_agent` is installed for every test, and the stages run
through its fake runner. Every stage, category and artifact name is derived
from the workflow story-219's module builds or from the run that workflow
drove. `.harness/docs/ARCHITECTURE.md` is not asserted on: this story assigns
it to the documenter, the stage that runs after this one. What cannot be tested
here is whether a real assistant opens each cited source and judges it fairly:
that is the agent's judgement, and the prompt's words are what this module
holds.
"""
from __future__ import annotations

import ast
import functools
import re
import shutil
from pathlib import Path

import pytest

import conftest
import harness_config
import story_coordinator
import story_inspection
from conftest import BASELINE, ENDPOINT

import test_a_completed_run_says_what_became_of_its_findings as accounted
import test_a_finding_a_source_settles_is_repaired_under_a_recorded_decision as decided
import test_inspection_findings_reach_the_story as inspected
from test_inspection_findings_reach_the_story import (  # noqa: F401 - fixtures used by name
    filings,
    no_model,
)
from test_a_routed_finding_is_repaired_unless_the_verdict_says_why import (
    paragraphs,
    sentences,
)
from test_a_small_finding_is_repaired_or_dropped_never_filed import sentence_carrying
from test_every_story_and_brief_opens_in_plain_english import assist_prompt
from test_execution_history import PLANTED_EMITTER, kind_spellings

THIS_MODULE = Path(__file__)

STORY_ID = decided.STORY_ID
SLUG = decided.SLUG
OTHER_SLUG = decided.OTHER_SLUG

#: The field a repairable finding carries its decision under, the field only a
#: rejection's record carries, and the levels a decision is ordered by —
#: each read off story-219's module, which reads them off the schemas.
DECISION = decided.DECISION
REJECTION = decided.REJECTION
BLAST_RADIUS = decided.BLAST_RADIUS
CONFIDENCE = decided.CONFIDENCE
LEVEL_FIELDS = (BLAST_RADIUS, CONFIDENCE)
LEVELS = list(decided.LEVELS)
LOW, MEDIUM, HIGH = LEVELS

#: The field a decision record names its finding under — the location of the
#: repairable finding it was made for — and the decision's own text fields. The
#: answer is the one a decision is told apart by: two decisions on one finding
#: may share the finding and the question, and never the answer.
FINDING = "finding"
QUESTION = "question"
ANSWER = "answer"
TEXT_FIELDS = (FINDING, *(field for field in decided.CHOSEN
                          if field not in LEVEL_FIELDS))

#: How a rendered entry is read for its levels and for being rejected:
#: the first level word after each field's label, which a report may spell
#: with a space or with the field's own underscore, and the word itself.
LEVEL_WORD = re.compile(r"\b(?:%s)\b" % "|".join(map(re.escape, LEVELS)))
REJECTED_MARK = re.compile(r"\brejected\b", re.IGNORECASE)


def label_of(field: str) -> re.Pattern:
    return re.compile(field.replace("_", "[ _]"), re.IGNORECASE)


# ==========================================================================
# The decisions these runs make
# ==========================================================================


def decided_by(name: str, *, blast: str, confidence: str) -> dict:
    """A decision whose every text carries `name`, so a search of a report or a
    log is looking for this decision's own words. No text carries a level
    word, a digit, or the word the rejected mark is read by, so what the
    readers below find in an entry is the rendering's and not the decision's."""
    return {
        QUESTION: f"MARKER-QUESTION-{name} which shape does the helper take",
        ANSWER: f"MARKER-ANSWER-{name} the shape its neighbour takes",
        decided.SOURCE: (f"MARKER-SOURCE-{name} {inspected.SIBLING_FILE}, whose "
                         f"helper takes that shape"),
        "rationale": f"MARKER-RATIONALE-{name} the neighbour settles the shape",
        CONFIDENCE: confidence,
        BLAST_RADIUS: blast,
    }


def raised_with(name: str, decision: dict) -> dict:
    """A repairable finding the verifier raised itself about the changed file,
    naming no routed slug, carrying `decision`."""
    return {
        "path": inspected.CHANGED_FILE,
        "location": f"{inspected.CHANGED_FILE} - MARKER-WHERE-{name}",
        "finding": f"MARKER-FINDING-{name} the helper misses a case",
        "correction": f"MARKER-FIX-{name} handle the case",
        "category": decided.CATEGORY,
        DECISION: dict(decision),
    }


def as_recorded(finding: dict) -> dict:
    """The decision a repairable finding carries, in the shape the decision log
    records it: named for the finding's location."""
    return {FINDING: finding["location"], **finding[DECISION]}


#: The decisions the central run makes, keyed by the order it makes them in.
#: The levels are chosen so that no simpler rule gives the order the report
#: must: listing them as made, least sure first, or by the levels summed each
#: gives a different sequence from `RISKIEST_FIRST`.
DECISIONS_MADE = {
    name: raised_with(name, decided_by(name, blast=blast, confidence=confidence))
    for name, blast, confidence in (
        ("made-first", LOW, HIGH),
        ("made-second", LOW, LOW),
        ("made-third", MEDIUM, LOW),
        ("made-fourth", MEDIUM, MEDIUM),
        ("made-fifth", HIGH, HIGH),
        ("made-sixth", LOW, HIGH),
    )
}
MADE = list(DECISIONS_MADE)

#: The order the report must give them, written out rather than computed, so
#: the rule is not implemented a second time here: the costliest, which the run
#: was sure of; then those equally costly below it, the less sure first; then
#: the cheapest, the least sure first and the pair equal on both as made.
RISKIEST_FIRST = ["made-fifth", "made-third", "made-fourth", "made-second",
                  "made-first", "made-sixth"]

#: The pair in which the costlier decision is the one the run was surer of,
#: made after the other; and the pair equal on both levels.
CROSSING = ("made-fifth", "made-third")
TIED = ("made-first", "made-sixth")


def rank(level: str) -> int:
    return LEVELS.index(level)


def repairing_with(*names: str, decisions: dict = DECISIONS_MADE) -> dict:
    return inspected.repairing(*(decisions[name] for name in names))


# ==========================================================================
# Reading the decisions back out of the report and the log
# ==========================================================================


#: What the findings section said before decisions existed: the paragraphs
#: opening with one of story-213's clauses, and the sentences it writes in
#: place of naming findings.
SAID_BEFORE = (accounted.NOTHING_TO_ROUTE, accounted.NOT_KNOWN_YET,
               accounted.COULD_NOT_BE_DERIVED)


def said_before(paragraph: str) -> bool:
    return (paragraph.startswith(tuple(accounted.CLAUSES.values()))
            or any(sentence in paragraph for sentence in SAID_BEFORE))


def decisions_part(report: str) -> str:
    """The paragraphs the findings section closes on after the last one it
    would have written before decisions existed — empty where it closes on
    one of those. A decision rendered among the existing paragraphs rather than
    after them is therefore not in this part, and is not found by the readers
    below."""
    body = accounted.account_body(report)
    last = max((index for index, paragraph in enumerate(body)
                if said_before(paragraph)), default=-1)
    return "\n\n".join(body[last + 1:])


def entries_in(part: str, decisions: list[dict]) -> list[tuple[dict, str]]:
    """Each of `decisions` the part names, with the text of its entry, in the
    order the part gives them.

    An entry is found by its answer, which is the decision's alone. It begins
    at the first of its decision's texts after the entry before it and ends
    where the next begins, so a finding and a question two decisions share are
    each read in their own entry — and whatever the rendering writes about a
    decision after its texts, a rejection's reason included, falls in that
    decision's entry and no other. That holds only when `decisions` holds every
    decision the part names: the last entry read runs to the end of the part."""
    answers = [one[ANSWER] for one in decisions]
    assert len(set(answers)) == len(answers), answers
    anchored = sorted((part.find(one[ANSWER]), index)
                      for index, one in enumerate(decisions)
                      if one[ANSWER] in part)
    starts: list[tuple[int, int]] = []
    floor = 0
    for at, index in anchored:
        found = [part.find(decisions[index][field], floor)
                 for field in TEXT_FIELDS]
        starts.append((min(where for where in found if 0 <= where <= at), index))
        floor = at + len(decisions[index][ANSWER])
    ends = [start for start, _ in starts[1:]] + [len(part)]
    return [(decisions[index], part[start:end])
            for (start, index), end in zip(starts, ends)]


def level_named(entry: str, field: str) -> str | None:
    """The level an entry gives `field`: the first level word after the
    field's label."""
    label = label_of(field).search(entry)
    if label is None:
        return None
    level = LEVEL_WORD.search(entry, label.end())
    return level.group(0) if level else None


def marked_rejected(entry: str) -> bool:
    return bool(REJECTED_MARK.search(entry))


def report_of(run) -> str:
    return accounted.report_of(run)


def entries_by_name(run, decisions: dict[str, dict]) -> dict[str, str]:
    """The entry the run's report gives each of `decisions`, by name, in the
    order the report gives them. Every decision the run made is passed, so
    each entry ends where the next one begins rather than running on into it."""
    recorded = {name: as_recorded(finding) for name, finding in decisions.items()}
    named = {one[ANSWER]: name for name, one in recorded.items()}
    return {named[one[ANSWER]]: text for one, text in
            entries_in(decisions_part(report_of(run)), list(recorded.values()))}


def reported(run, decisions: dict[str, dict]) -> list[str]:
    """The names of `decisions` in the order the run's report gives them."""
    return list(entries_by_name(run, decisions))


def logged(run) -> tuple[list[dict], list[dict]]:
    """The decision log the run committed, as its decision records and its
    rejection records, each in the order written."""
    records = decided.decision_records(run)
    return ([one for one in records if REJECTION not in one],
            [one for one in records if REJECTION in one])


def made_order(run, decisions: dict[str, dict]) -> list[str]:
    """The names of `decisions` in the order the run recorded them."""
    named = {finding[DECISION][ANSWER]: name for name, finding in decisions.items()}
    return [named[one[ANSWER]] for one in logged(run)[0]]


def carries_a_number(text: str) -> bool:
    return any(character.isdigit() for character in text)


def the_report_names_what_the_log_holds(run) -> None:
    """The decisions part names every decision record the run's committed log
    holds, each once and with its own words and levels, and marks rejected
    exactly the decisions a rejection record names, each with that record's
    reason. Read off the log, so a decision the log does not hold is found by
    nothing here."""
    path = decided.decision_log_path(run)
    assert decided.committed_as_it_stands(run, path)
    decisions, rejections = logged(run)
    assert decisions
    entries = entries_in(decisions_part(report_of(run)), decisions)
    assert sorted(one[ANSWER] for one, _ in entries) == \
        sorted(one[ANSWER] for one in decisions)
    for decision, entry in entries:
        # One label per entry, so no decision the log does not hold is
        # written inside an entry the log does.
        assert len(label_of(BLAST_RADIUS).findall(entry)) == 1, entry
        for field in TEXT_FIELDS:
            assert decision[field] in entry, (field, entry)
        for field in LEVEL_FIELDS:
            assert level_named(entry, field) == decision[field], (field, entry)
    marked = [(one, entry) for one, entry in entries if marked_rejected(entry)]
    assert len(marked) == len(rejections), marked
    for rejection in rejections:
        assert any(rejection[FINDING] == one[FINDING]
                   and rejection[QUESTION] == one[QUESTION]
                   and rejection[REJECTION] in entry
                   for one, entry in marked), rejection


# ==========================================================================
# The decisions and the workflows are what they claim to be
# ==========================================================================


def test_the_decisions_are_made_in_an_order_the_report_must_not_keep():
    """The premises every order below rests on. Each decision is the shape a
    verdict's decision takes, on a finding naming no routed slug and the one
    category the workflow routes. The report's order is not the order made.
    The costlier of the crossing pair is also the surer and was made later, and
    summing the levels would rank the pair the other way, which is what makes
    blast radius leading the thing it shows; the tie is equal on both levels.
    And the decision log declares both levels in the vocabulary the verdict
    uses, so the order the oracle reads them in is the one the report reads
    them in."""
    for finding in DECISIONS_MADE.values():
        assert set(finding[DECISION]) == set(decided.CHOSEN)
        assert finding["category"] in decided.ROUTES
        assert not story_inspection.names_a_slug(finding["location"],
                                                 (SLUG, OTHER_SLUG))
    assert {QUESTION, ANSWER} <= set(decided.CHOSEN)
    assert MADE != RISKIEST_FIRST
    assert sorted(MADE) == sorted(RISKIEST_FIRST)

    surer, unsurer = (DECISIONS_MADE[name][DECISION] for name in CROSSING)
    assert rank(surer[BLAST_RADIUS]) > rank(unsurer[BLAST_RADIUS])
    assert rank(surer[CONFIDENCE]) > rank(unsurer[CONFIDENCE])
    assert MADE.index(CROSSING[1]) < MADE.index(CROSSING[0])

    def summed(decision: dict) -> int:
        return (rank(decision[BLAST_RADIUS])
                + len(LEVELS) - 1 - rank(decision[CONFIDENCE]))
    assert summed(unsurer) > summed(surer)

    first, second = (DECISIONS_MADE[name][DECISION] for name in TIED)
    assert [first[field] for field in LEVEL_FIELDS] == \
        [second[field] for field in LEVEL_FIELDS]
    assert MADE.index(TIED[0]) < MADE.index(TIED[1])

    declared = decided.DECLARATIONS[decided.DECISION_LOG]["properties"]
    for field in LEVEL_FIELDS:
        assert declared[field]["enum"] == LEVELS, field


def uninspected_workflow(budget: int = 2) -> dict:
    """story-219's decided-repair workflow with its inspection taken out: the
    repair pass and the retry table stay on the stage that judged, so the
    verifier can still decide on a finding of its own. The name is kept, for
    the reason story-213's no-inspection control keeps it — the target
    configures the workflow by name."""
    workflow = decided.deciding_workflow(budget)
    for stage in workflow["stages"]:
        stage.pop("inspection", None)
    return workflow


def materialize_uninspected(root: Path) -> Path:
    """The workflow above written where a run finds it, with the templates
    story-219's module renders the repair pass record through."""
    workflow = uninspected_workflow()
    built = inspected.materialize(workflow, root, rules=decided.RULES)
    for stage in workflow["stages"]:
        (built / "prompts" / stage["prompt"]).write_text(
            decided.PROMPTS[stage["name"]], encoding="utf-8")
    return built


def fresh_uninspected(tmp_path: Path, name: str) -> tuple[Path, Path]:
    return (inspected.build_target(tmp_path / f"{name}-target",
                                   workflow=decided.WORKFLOW_NAME),
            materialize_uninspected(tmp_path / f"{name}-harness"))


def test_the_uninspected_workflow_declares_a_repair_pass_and_no_inspection():
    """The premise of the runs declaring no inspection: the coordinator's own
    readers find no inspection and the same repair pass, and the pre-flight
    accepts the definition, so a run refused for its shape cannot be mistaken
    for one of the cases below."""
    stages = uninspected_workflow()["stages"]
    assert story_coordinator.inspection_declaration(stages) == {}
    assert story_coordinator.repair_pass_declaration(stages)["result"] \
        == decided.REPAIR_ARTIFACT
    assert story_coordinator.repair_pass_problems(stages) == []


# ==========================================================================
# Every decision is named, riskiest first
# ==========================================================================


@pytest.fixture
def decided_out_of_order(tmp_path):
    """The central run: the inspection routes nothing; the first verdict
    repairs the first half of `DECISIONS_MADE` — findings of the verifier's own,
    each under its decision — the verdict that pass returns to repairs the
    rest, and the one after passes. The decisions are made in `MADE` order,
    across both passes."""
    target, harness = decided.fresh(tmp_path, "out-of-order")
    run = decided.drive(target, harness, tmp_path,
                        [repairing_with(*MADE[:3]), repairing_with(*MADE[3:]),
                         inspected.PASS],
                        findings=(), name="out-of-order")
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.state["repair_pass_count"] == 2
    return run


def test_every_decision_is_named_with_its_fields_after_what_the_section_said(
        decided_out_of_order):
    """The first criterion. Inside the findings section, after the sentence it
    writes for a run whose inspection routed nothing, every decision has an
    entry naming the finding it was made for, its question, answer, source and
    rationale, and the blast radius and confidence it was recorded with."""
    report = report_of(decided_out_of_order)
    section = accounted.account_section(report)
    assert accounted.NOTHING_TO_ROUTE in section
    part = decisions_part(report)
    assert part
    assert section.index(part) > section.index(accounted.NOTHING_TO_ROUTE)

    entries = entries_in(part, [as_recorded(one) for one in DECISIONS_MADE.values()])
    assert len(entries) == len(DECISIONS_MADE)
    for decision, entry in entries:
        for field in TEXT_FIELDS:
            assert decision[field] in entry, (field, entry)
        for field in LEVEL_FIELDS:
            assert level_named(entry, field) == decision[field], (field, entry)


def test_the_decisions_are_listed_costliest_first_then_least_sure_then_as_made(
        decided_out_of_order):
    """The second criterion. The run recorded the decisions in the order it
    made them, which is not the order the report must give, and the report
    gives that order: the costliest decision first though the run was sure of
    it and made it after a cheaper one it was unsure of, and two decisions
    equal on both levels in the order they were made."""
    assert made_order(decided_out_of_order, DECISIONS_MADE) == MADE
    order = reported(decided_out_of_order, DECISIONS_MADE)
    assert order == RISKIEST_FIRST
    assert order.index(CROSSING[0]) < order.index(CROSSING[1])
    assert order.index(TIED[0]) < order.index(TIED[1])


def test_the_decisions_part_writes_no_count(decided_out_of_order):
    """The decisions part names each decision and counts none of them: none of
    the decisions' own texts carries a digit, so a digit in the part would be a
    number the rendering wrote. The control is the rest of the same report,
    which carries the story's number and the run's retry count, found by the
    same predicate."""
    report = report_of(decided_out_of_order)
    part = decisions_part(report)
    for finding in DECISIONS_MADE.values():
        for value in as_recorded(finding).values():
            assert not carries_a_number(value), value
    assert part
    assert not carries_a_number(part), part
    assert carries_a_number(report.replace(part, "", 1))


def test_decisions_made_before_a_retry_are_named_after_it(tmp_path):
    """The same decisions, the first half made on an attempt whose returning
    verification fails it and the rest on the attempt after: the report the
    completed run leaves names all of them, in the same order."""
    target, harness = decided.fresh(tmp_path, "retried")
    run = decided.drive(target, harness, tmp_path,
                        [repairing_with(*MADE[:3]),
                         inspected.failing_into(decided.CATEGORY),
                         repairing_with(*MADE[3:]), inspected.PASS],
                        findings=(), name="retried")
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.state["retry_count"] == 1
    assert made_order(run, DECISIONS_MADE) == MADE
    assert reported(run, DECISIONS_MADE) == RISKIEST_FIRST


# ==========================================================================
# A rejected decision keeps its place
# ==========================================================================


#: Decisions on one verdict, made cheapest first: one on a finding the
#: verifier raised itself, one on the repair of the routed finding — the
#: decision the returning verification rejects — and a costlier one of the
#: verifier's own. The rejected one belongs between the other two.
REJECTED_IN_ITS_PLACE = {
    "cheap": raised_with("cheap", decided_by("cheap", blast=LOW, confidence=LOW)),
    "routed": decided.deciding(decided_by("routed", blast=MEDIUM,
                                          confidence=MEDIUM)),
    "costly": raised_with("costly", decided_by("costly", blast=HIGH,
                                               confidence=MEDIUM)),
}
REJECTED_RISKIEST_FIRST = ["costly", "routed", "cheap"]


@pytest.fixture
def rejected_in_its_place(tmp_path):
    """The inspection routes story-219's finding; the first verdict repairs it
    and findings of the verifier's own, each under its decision; the
    verification the pass returns to passes and rejects the routed finding's
    decision by its slug."""
    target, harness = decided.fresh(tmp_path, "rejected-in-its-place")
    run = decided.drive(target, harness, tmp_path,
                        [repairing_with(*REJECTED_IN_ITS_PLACE,
                                        decisions=REJECTED_IN_ITS_PLACE),
                         decided.rejecting(SLUG)],
                        name="rejected-in-its-place")
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert made_order(run, REJECTED_IN_ITS_PLACE) == list(REJECTED_IN_ITS_PLACE)
    assert len(logged(run)[1]) == 1
    return run


def test_a_rejected_decision_is_listed_in_its_place_marked_with_the_reason(
        rejected_in_its_place):
    """The third criterion. The rejected decision sits where its levels put
    it, marked rejected and carrying the verification's reason; the decisions
    no verification rejected carry neither — the rejected entry of the same
    report, read by the same readers, is the control for that absence. And the
    routed finding behind it is named among those sent to the backlog, on the
    ground the accounting recorded, which carries the rejection."""
    run = rejected_in_its_place
    entries = entries_by_name(run, REJECTED_IN_ITS_PLACE)
    assert list(entries) == REJECTED_RISKIEST_FIRST

    rejected = entries["routed"]
    assert marked_rejected(rejected), rejected
    assert decided.REJECTION_REASON in rejected
    for name in ("cheap", "costly"):
        standing = entries[name]
        assert not marked_rejected(standing), standing
        assert decided.REJECTION_REASON not in standing

    entry = decided.declined_entry(inspected.accounting(run), SLUG)
    assert decided.REJECTION_REASON in entry["reason"]
    assert accounted.named_in(report_of(run), "backlog") == {SLUG: entry["reason"]}


def test_the_report_names_exactly_what_the_runs_decision_log_holds(
        rejected_in_its_place):
    """The fourth criterion's first half, read off the decision log the same
    run committed rather than off the verdicts it was given: every decision
    record is named once, with its own words and levels, and the entries marked
    rejected are exactly the ones a rejection record names."""
    the_report_names_what_the_log_holds(rejected_in_its_place)


#: The same finding decided twice on the same question: the first decision,
#: cheap and sure, is rejected by the verification its pass returns to, which
#: makes a second, costly and unsure, in the same verdict.
DECIDED_FIRST = decided_by("decided-first", blast=LOW, confidence=HIGH)
DECIDED_AGAIN = {**decided_by("decided-again", blast=HIGH, confidence=LOW),
                 QUESTION: DECIDED_FIRST[QUESTION]}


def test_a_finding_decided_twice_is_named_twice_and_rejected_where_it_was(
        tmp_path):
    """The log holds the first decision, its rejection and the second decision,
    in that order, all naming one finding and one question. The report names
    both decisions, the second first because it is the costlier, and the
    rejection and its reason sit on the first alone — which is where a
    rejection paired with the decision recorded before it puts them, and where
    neither pairing it with the latest such decision nor with every one of them
    would."""
    target, harness = decided.fresh(tmp_path, "decided-twice")
    first, again = decided.deciding(DECIDED_FIRST), decided.deciding(DECIDED_AGAIN)
    run = decided.drive(target, harness, tmp_path,
                        [inspected.repairing(first),
                         decided.rejecting(SLUG, inspected.repairing(again)),
                         inspected.PASS],
                        name="decided-twice")
    assert run.code == 0
    assert run.state["repair_pass_count"] == 2

    records = decided.decision_records(run)
    assert [REJECTION in one for one in records] == [False, True, False]
    assert {(one[FINDING], one[QUESTION]) for one in records} == \
        {(first["location"], DECIDED_FIRST[QUESTION])}
    assert [one[ANSWER] for one in logged(run)[0]] == \
        [DECIDED_FIRST[ANSWER], DECIDED_AGAIN[ANSWER]]

    part = decisions_part(report_of(run))
    entries = entries_in(part, [as_recorded(first), as_recorded(again)])
    assert [one[ANSWER] for one, _ in entries] == \
        [DECIDED_AGAIN[ANSWER], DECIDED_FIRST[ANSWER]]
    (_, made_again), (_, made_first) = entries
    assert marked_rejected(made_first), made_first
    assert decided.REJECTION_REASON in made_first
    assert not marked_rejected(made_again), made_again
    assert decided.REJECTION_REASON not in made_again
    assert part.count(decided.REJECTION_REASON) == 1
    the_report_names_what_the_log_holds(run)


#: A decision on the routed finding's repair that names no source, which the
#: coordinator refuses, beside a decision of the verifier's own that cites one.
REFUSED = {key: value for key, value in
           decided_by("refused", blast=HIGH, confidence=LOW).items()
           if key != decided.SOURCE}
STANDING = raised_with("standing", decided_by("standing", blast=LOW,
                                              confidence=MEDIUM))


def test_a_decision_the_coordinator_refused_is_not_listed_and_its_finding_is_sent_on(
        tmp_path):
    """The fourth criterion's second half. The refused decision was never acted
    on: it is in neither the log nor the decisions part, and its answer is
    nowhere in the report, while the decision of the same verdict that cited
    its source is in both, read by the same readers. Its question is still in
    the section, in the clause sending its finding to the backlog with the
    refusal as its ground, as that clause read before this story."""
    target, harness = decided.fresh(tmp_path, "refused")
    run = decided.drive(target, harness, tmp_path,
                        [inspected.repairing(decided.deciding(REFUSED), STANDING),
                         inspected.PASS],
                        name="refused")
    assert run.code == 0
    assert [one[ANSWER] for one in logged(run)[0]] == \
        [STANDING[DECISION][ANSWER]]
    the_report_names_what_the_log_holds(run)

    report = report_of(run)
    part = decisions_part(report)
    assert STANDING[DECISION][ANSWER] in part
    assert STANDING[DECISION][QUESTION] in part
    assert REFUSED[ANSWER] not in report
    assert REFUSED[QUESTION] not in part
    assert REFUSED[QUESTION] in accounted.account_section(report)

    entry = decided.declined_entry(inspected.accounting(run), SLUG)
    refusals = {one["message"] for one in
                inspected.events_of_kind(run, decided.DECISION_REFUSED)}
    assert entry["reason"] in refusals
    assert f"{accounted.CLAUSES['backlog']}{SLUG} ({entry['reason']})." in \
        accounted.account_section(report)


# ==========================================================================
# The control: a run that recorded no decision
# ==========================================================================


PAIRED_ROUTED = decided_by("paired-routed", blast=MEDIUM, confidence=HIGH)
PAIRED_OWN = decided_by("paired-own", blast=HIGH, confidence=LOW)


def paired_verdict(*, deciding: bool) -> dict:
    """A verdict repairing the routed finding and a finding of the verifier's
    own, and declining the second routed finding with a ground. Two calls
    differing in `deciding` differ in whether the repairs carry decisions and
    in nothing else."""
    own = raised_with("paired-own", PAIRED_OWN)
    if not deciding:
        own = {key: value for key, value in own.items() if key != DECISION}
    return {
        **inspected.repairing(
            decided.deciding(PAIRED_ROUTED if deciding else None), own),
        "declined_findings": [accounted.declined(decided.OTHER_ROUTED)],
    }


def test_a_run_that_recorded_no_decision_leaves_the_report_it_leaves_today(
        tmp_path, filings):
    """The fifth criterion, as a comparison between two driven runs. The run
    whose verdicts carry no decision renders no decisions part; the run whose
    verdicts carry them renders one naming both — the control for that
    absence — and its report with that part removed is the first run's, byte
    for byte. So a decisions part written unconditionally, or an existing
    clause the decisions changed, fails here. Both reports carry the clauses
    the comparison is over, and the filing at completion is the same for both
    runs, because decisions change what the report says and nothing about
    what is sent on."""
    findings = [decided.ROUTED_FINDING, decided.OTHER_ROUTED]
    deciding_target, deciding_harness = decided.fresh(tmp_path, "deciding")
    with_decisions = decided.drive(
        deciding_target, deciding_harness, tmp_path,
        [paired_verdict(deciding=True), inspected.PASS],
        findings=findings, name="deciding")
    filed_with_decisions = [sorted(call) for call in filings]
    plain_target, plain_harness = decided.fresh(tmp_path, "plain")
    without = decided.drive(
        plain_target, plain_harness, tmp_path,
        [paired_verdict(deciding=False), inspected.PASS],
        findings=findings, name="plain")
    assert with_decisions.code == without.code == 0
    assert logged(without) == ([], [])

    plain = report_of(without)
    assert decisions_part(plain) == ""
    assert accounted.named_in(plain, "repaired") == {SLUG: ""}
    assert accounted.named_in(plain, "backlog") == {OTHER_SLUG: accounted.GROUND}

    decided_report = report_of(with_decisions)
    part = decisions_part(decided_report)
    assert part
    assert len(entries_in(part, [as_recorded(one) for one in
                                 paired_verdict(deciding=True)
                                 ["repairable_findings"]])) == 2
    assert decided_report.replace(part + "\n\n", "", 1) == plain

    assert [sorted(call) for call in filings] == \
        filed_with_decisions + filed_with_decisions


# ==========================================================================
# A workflow declaring no inspection
# ==========================================================================


UNSEEN = raised_with("unseen", decided_by("unseen", blast=MEDIUM,
                                          confidence=LOW))


def test_a_run_declaring_no_inspection_names_its_decision_and_no_inspection(
        tmp_path):
    """The sixth criterion's first half. Under the workflow declaring a repair
    pass and no inspection, a verdict repairing a finding the verifier raised
    itself under a decision leaves a findings section holding that decision and
    nothing the section said before, naming no inspection. The same verdicts
    under the inspecting workflow leave a section that does name its
    inspection — the control for that absence — and the same decisions part."""
    verdicts = [inspected.repairing(UNSEEN), inspected.PASS]
    uninspected = decided.drive(*fresh_uninspected(tmp_path, "uninspected"),
                                tmp_path, verdicts, findings=(),
                                name="uninspected")
    inspecting = decided.drive(*decided.fresh(tmp_path, "inspecting"),
                               tmp_path, verdicts, findings=(),
                               name="inspecting")
    assert uninspected.code == inspecting.code == 0

    report = report_of(uninspected)
    section = accounted.account_section(report)
    assert section
    body = accounted.account_body(report)
    assert not any(said_before(paragraph) for paragraph in body)
    assert decisions_part(report) == "\n\n".join(body)
    (entry,) = [text for _, text in entries_in(decisions_part(report),
                                               [as_recorded(UNSEEN)])]
    for field in TEXT_FIELDS:
        assert as_recorded(UNSEEN)[field] in entry, field
    assert "inspection" not in section.lower()

    assert "inspection" in accounted.account_section(report_of(inspecting)).lower()
    assert decisions_part(report_of(inspecting)) == decisions_part(report)


def test_the_same_run_without_the_decision_leaves_the_report_it_leaves_today(
        tmp_path):
    """The sixth criterion's second half. The run declaring no inspection whose
    verdict repairs the same finding with no decision has no findings section;
    the run carrying the decision has one, the control for that absence, and
    its report with the section removed is the first run's, byte for byte."""
    plain_finding = {key: value for key, value in UNSEEN.items()
                     if key != DECISION}
    without = decided.drive(*fresh_uninspected(tmp_path, "no-decision"),
                            tmp_path,
                            [inspected.repairing(plain_finding), inspected.PASS],
                            findings=(), name="no-decision")
    deciding = decided.drive(*fresh_uninspected(tmp_path, "a-decision"),
                             tmp_path,
                             [inspected.repairing(UNSEEN), inspected.PASS],
                             findings=(), name="a-decision")
    assert without.code == deciding.code == 0

    plain = report_of(without)
    assert accounted.FINDINGS_HEADING not in plain
    decided_report = report_of(deciding)
    assert accounted.FINDINGS_HEADING in decided_report
    assert decided_report.replace(accounted.account_section(decided_report),
                                  "", 1) == plain


# ==========================================================================
# A fault in reading the decisions
# ==========================================================================


FAULT = "MARKER-FAULT the decisions this run recorded cannot be read"


@pytest.mark.parametrize("declares_an_inspection", [True, False],
                         ids=["inspecting", "declaring no inspection"])
def test_a_fault_reading_the_decisions_leaves_the_report_written_and_the_run_complete(
        declares_an_inspection, tmp_path, monkeypatch, capsys):
    """The seventh criterion. The same verdicts driven twice: once as they are,
    and once with the reading of the decisions raising. The faulted run
    completes with the exit code the first returned, its report carries every
    heading the first carried, and its findings section says the account could
    not be derived and why, naming none of the decisions the first run's report
    names — that report is the control for the absence. The reason reaches the
    run's events log once and stderr."""
    build = decided.fresh if declares_an_inspection else fresh_uninspected
    named = ("made-first", "made-fifth")
    verdicts = [repairing_with(*named), inspected.PASS]
    working = decided.drive(*build(tmp_path, "read"), tmp_path, verdicts,
                            findings=(), name="read")
    assert working.code == 0
    working_report = report_of(working)
    assert accounted.COULD_NOT_BE_DERIVED not in working_report
    for name in named:
        assert DECISIONS_MADE[name][DECISION][ANSWER] in decisions_part(working_report)

    def cannot_be_read(*args, **keywords):
        raise RuntimeError(FAULT)

    monkeypatch.setattr(story_inspection, "decisions_recorded", cannot_be_read)
    faulted = decided.drive(*build(tmp_path, "unread"), tmp_path, verdicts,
                            findings=(), name="unread")

    assert faulted.code == working.code
    assert faulted.state["status"] == "completed"
    report = report_of(faulted)
    for heading in [one.split("\n", 1)[0]
                    for one in working_report.split("\n## ")[1:]]:
        assert f"## {heading}" in report, heading
    section = accounted.account_section(report)
    assert accounted.COULD_NOT_BE_DERIVED in section
    assert FAULT in section
    for name in named:
        assert DECISIONS_MADE[name][DECISION][ANSWER] not in report

    messages = [one for one in faulted.messages
                if accounted.COULD_NOT_BE_DERIVED in one and FAULT in one]
    assert len(messages) == 1, faulted.messages
    assert FAULT in capsys.readouterr().err


# ==========================================================================
# The renderers derive nothing
# ==========================================================================


SECTION_RENDERER = "_findings_account_section"


def renderers(source: str) -> set[str]:
    """The function rendering the findings section, and every function of the
    same module it calls, however deep — so a helper written for the decisions
    is held to the rule whatever it is named."""
    defined = {node.name for node in ast.parse(source).body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    seen: set[str] = set()
    waiting = [SECTION_RENDERER]
    while waiting:
        name = waiting.pop()
        if name in seen:
            continue
        seen.add(name)
        waiting.extend(accounted.called_in(source, name) & defined)
    return seen


def test_every_renderer_of_the_section_reaches_into_the_inspection_module_for_nothing():
    """The account is derived in the inspection module and the coordinator
    renders what it is handed, decisions included. The control is `_complete`,
    which the same reading finds asking for the account."""
    source = accounted.COORDINATOR_SOURCE
    found = renderers(source)
    assert SECTION_RENDERER in found
    for name in found:
        assert accounted.reaches_into(source, name) == set(), name
    assert accounted.reaches_into(source, "_complete") != set()


# ==========================================================================
# The assistant's prompt, as l5-assist hands it to a session
# ==========================================================================


#: The words the duty opens on, and the heading the assistant's
#: responsibilities are listed under.
BEHALF = "on the developer's behalf"
RESPONSIBILITIES = "Your responsibilities are to:"


def decisions_passage(prompt: str) -> str:
    """The paragraphs stating the duty, flowed into one: the paragraph saying
    whose behalf the decisions were made on, and each one after it that is
    still about decisions."""
    found = paragraphs(prompt)
    start = next(index for index, one in enumerate(found) if BEHALF in one)
    passage = [found[start]]
    for one in found[start + 1:]:
        if "decision" not in one:
            break
        passage.append(one)
    return " ".join(passage)


def first_responsibility(prompt: str) -> str:
    listed = prompt.split(RESPONSIBILITIES, 1)[1]
    return " ".join(listed.split("\n- ")[1].split())


def in_order(text: str, *phrases: str) -> bool:
    at = 0
    for phrase in phrases:
        at = text.find(phrase, at)
        if at < 0:
            return False
        at += len(phrase)
    return True


@pytest.fixture
def duty(monkeypatch) -> str:
    return decisions_passage(assist_prompt(monkeypatch))


#: What the duty tells the assistant, each found by the words of the one
#: sentence saying it and held to the words that sentence must also carry.
DUTIES = {
    "reports the decisions of a run it reviews":
        ("report the decisions", ("review", BEHALF)),
    "says where the decision log is kept":
        (decided.DECISION_LOG, (f"{harness_config.DEFAULT_HISTORY_DIR}/",)),
    "says the log outlives the run directory":
        ("outlives the run directory", ("committed", "completion report")),
    "notes a source that does not support the choice":
        ("open the source it cites", ("supports the choice", "where it does not")),
    "gives no verdict on a decision":
        ("no verdict on whether a decision was right", ()),
    "says so when the run recorded none":
        ("recorded no decision", ("say so",)),
    "cites the files it read":
        ("Cite the files you read", ()),
}


@pytest.mark.parametrize("duty_said", sorted(DUTIES))
def test_the_assistant_is_told_to_report_a_reviewed_storys_decisions(
        duty_said, duty):
    """The eighth criterion, one statement at a time, read off the prompt the
    launcher hands to a session."""
    opening, carried = DUTIES[duty_said]
    sentence = sentence_carrying(duty, opening)
    for words in carried:
        assert words in sentence, (words, sentence)


def test_the_assistant_is_told_the_order_the_report_uses(duty):
    """The order in words: costliest first by the blast radius field, then
    least sure by the confidence field, then as made — and the levels in the
    vocabulary's own order, with a level outside it first."""
    rule = sentence_carrying(duty, "the order the completion report uses")
    assert in_order(rule, "costliest", BLAST_RADIUS, "least sure", CONFIDENCE,
                    "the order they were made in"), rule
    levels = sentence_carrying(duty, ", ".join(LEVELS))
    assert "outside" in levels and "first" in levels, levels


def test_the_assistants_first_responsibility_names_the_history_directory(
        monkeypatch):
    """Where a run's record lives once its own files are gone, named beside the
    directories the assistant already reads."""
    responsibility = first_responsibility(assist_prompt(monkeypatch))
    assert f"{harness_config.DEFAULT_HISTORY_DIR}/" in responsibility, \
        responsibility


def test_the_prompt_sends_the_assistant_where_a_driven_run_committed_its_decisions(
        rejected_in_its_place, duty):
    """The ninth criterion, against a run driven under the fake runner rather
    than a log written for the test. The sentence naming the log's file names
    the history directory the run committed into, followed by a segment
    standing for the story, which is the story's id in the run; the log is
    committed there and is unchanged once the run directory is deleted. The
    fields the prompt orders by are fields every committed decision record
    carries, with values among the levels it names; every record carries the
    fields the prompt says it does; and a rejection record, which the prompt
    names by the field it carries, names the finding and question of a
    decision recorded before it."""
    run = rejected_in_its_place
    path = decided.decision_log_path(run)
    records = decided.decision_records(run)
    decisions, rejections = logged(run)
    assert decisions and rejections
    assert decided.committed_as_it_stands(run, path)

    kept = sentence_carrying(duty, path.name)
    history = path.parent.parent.relative_to(run.tree).as_posix()
    segment = re.search(re.escape(history) + r"/(<[^<>/\s]+>)/", kept)
    assert segment, (history, kept)
    assert path.parent.name == STORY_ID
    described = (segment.group(0) + path.name).replace(segment.group(1), STORY_ID)
    assert described == path.relative_to(run.tree).as_posix()

    shutil.rmtree(run.run_dir)
    assert not run.run_dir.exists()
    assert decided.decision_records(run) == records

    rule = sentence_carrying(duty, "the order the completion report uses")
    levels = sentence_carrying(duty, ", ".join(LEVELS))
    for field in LEVEL_FIELDS:
        assert field in rule, field
        for record in decisions:
            assert re.search(rf"\b{re.escape(record[field])}\b", levels), record
    for record in decisions:
        for field in TEXT_FIELDS:
            assert field in record, (field, record)

    assert REJECTION in duty
    for rejection in rejections:
        earlier = records[:records.index(rejection)]
        assert any(one[FINDING] == rejection[FINDING]
                   and one[QUESTION] == rejection[QUESTION]
                   for one in earlier if REJECTION not in one), rejection


#: The words the duty sends the assistant by before a story merges: the
#: condition, where the log is then, and what its absence from the tree a
#: session stands in does not show.
UNMERGED = "Until the story merges"
ON_THE_BRANCH = "the story's branch"
IN_THE_RUN_TREE = "the working tree the run used"
MISSING_HERE = "missing from the tree you stand in"
NOT_EVIDENCE = "is not evidence that the run recorded none"


def path_the_prompt_names(duty: str) -> str:
    """The decision log's path as the sentence naming its file gives it,
    relative to the tree a session stands in: the directory written before the
    segment standing for the story, that segment as this story's id, and the
    file."""
    kept = sentence_carrying(duty, decided.DECISION_LOG)
    written = re.search(r"(\S+)/(<[^<>/\s]+>)/", kept)
    assert written, kept
    return f"{written.group(1)}/{STORY_ID}/{decided.DECISION_LOG}"


def test_before_the_story_merges_the_assistant_looks_where_the_run_worked(
        rejected_in_its_place, duty):
    """A run that recorded decisions, read before its story merges. The
    premise is the case this part of the duty exists for: the run worked in a
    tree other than the one it was invoked from, which is where a session the
    developer starts in their own checkout stands, and at the path the prompt
    names that tree holds no decision log while the run's tree holds the one
    the run committed. The run's tree, read at the same path, is the control
    for the absence. The duty, as l5-assist hands it to a session, then says
    that until the story merges its decision log is on the story's branch in
    the tree the run used, and that a log missing from the tree the session
    stands in is not evidence that the run recorded none."""
    run = rejected_in_its_place
    assert run.tree.resolve() != run.target.resolve()
    named = path_the_prompt_names(duty)
    path = decided.decision_log_path(run)
    assert path.relative_to(run.tree).as_posix() == named
    assert decided.committed_as_it_stands(run, path)
    assert logged(run)[0]
    assert (run.tree / named).is_file()
    assert not (run.target / named).exists()

    located = [one for one in sentences(duty)
               if all(words in one for words in
                      (UNMERGED, ON_THE_BRANCH, IN_THE_RUN_TREE))]
    assert located, duty
    absent = sentence_carrying(duty, NOT_EVIDENCE)
    for words in (UNMERGED, MISSING_HERE, ON_THE_BRANCH, IN_THE_RUN_TREE):
        assert words in absent, (words, absent)


#: Where the run below is configured to keep its history, a directory no
#: default names.
RELOCATED = "kept-elsewhere/history"


def configuration_key(sentence: str) -> str:
    """The one configuration key a sentence names, spelled as keys are."""
    keys = set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", sentence))
    assert len(keys) == 1, keys
    return keys.pop()


def test_the_configuration_key_the_prompt_names_is_the_one_that_moves_the_log(
        tmp_path, duty):
    """The prompt says the history directory is the default unless the
    target's configuration sets a key. A target setting that key, as the
    prompt spells it, to a directory of its own has its run commit the
    decision log there, under the story's segment — so the key the assistant
    is told to look for is the one the run obeys."""
    key = configuration_key(sentence_carrying(duty, decided.DECISION_LOG))
    target = inspected.build_target(tmp_path / "relocated-target",
                                    workflow=decided.WORKFLOW_NAME,
                                    **{key: RELOCATED})
    harness = decided.materialize(tmp_path / "relocated-harness")
    run = decided.drive(target, harness, tmp_path,
                        [repairing_with(MADE[0]), inspected.PASS],
                        findings=(), name="relocated")
    assert run.code == 0
    path = decided.decision_log_path(run)
    assert path.relative_to(run.tree).as_posix() == \
        f"{RELOCATED}/{STORY_ID}/{decided.DECISION_LOG}"
    assert [one[ANSWER] for one in decided.decision_records(run)] == \
        [DECISIONS_MADE[MADE[0]][DECISION][ANSWER]]
    assert decided.committed_as_it_stands(run, path)


# ==========================================================================
# What this story left alone
# ==========================================================================


COORDINATOR = "orchestration/story_coordinator.py"
INSPECTION = "orchestration/story_inspection.py"


@functools.lru_cache(maxsize=None)
def text_at(relative: str, bound: str) -> str:
    """One file's text at one end of this module's own story range, through the
    shared resolution, read once however many cases ask."""
    return conftest.repository_file_at(relative, validation_file=THIS_MODULE,
                                       bound=bound)


#: The paths the story's scope.do_not_modify names as whole files: what a run
#: may decide and how a verification is told of it, what a decision record
#: carries, the launcher and the status reader, the filing, the README, and
#: the test modules whose assertions keep holding as they stand.
#: tests/test_execution_history.py is named there too and is not here: the
#: decisions' reader is the first reader of a run's execution history outside
#: the coordinator, so that module's guard on which modules read the history
#: was widened in this run's repair pass, under a scope extension the
#: verification recorded. What the scope protected it for — that no event
#: kind, field or log was added — is held here instead: the schemas declaring
#: the fields and the logs stay in this list, and the event kinds are compared
#: further down.
LEFT_ALONE = [
    "prompts/story-verifier.md",
    "prompts/refactor-verifier.md",
    "schemas/cross-run-history.schema.json",
    "schemas/execution-history.schema.json",
    "schemas/verification-result.schema.json",
    "scripts/l5-assist",
    "scripts/l5-status",
    "orchestration/run_status.py",
    "orchestration/inspection.py",
    "README.md",
    "tests/test_a_completed_run_says_what_became_of_its_findings.py",
    "tests/test_a_finding_a_source_settles_is_repaired_under_a_recorded_decision.py",
    "tests/test_cross_run_history.py",
]

#: A path this story did change, for the comparison's control.
CHANGED_HERE = ["prompts/assist.md"]


def test_the_paths_the_story_was_to_leave_alone_are_untouched():
    assert conftest.story_diff(LEFT_ALONE, validation_file=THIS_MODULE) == ""


def test_the_same_comparison_reports_a_path_this_story_did_change():
    """Control for the absence above, over the same range: the assistant's
    prompt, which this story rewrote, carries a diff."""
    assert conftest.story_diff(CHANGED_HERE, validation_file=THIS_MODULE) != ""


#: The functions the story's scope.do_not_modify names within files it does
#: change: the decision machinery and append_event, which together say what is
#: recorded; the escalation summary; the renderer of the existing clauses; and
#: the filing at completion.
LEFT_ALONE_FUNCTIONS = (
    (COORDINATOR, "decision_check"),
    (COORDINATOR, "decision_refusal_reason"),
    (COORDINATOR, "_decision_refused"),
    (COORDINATOR, "_decision_recorded"),
    (COORDINATOR, "_decision_rejected"),
    (COORDINATOR, "decision_rejection_reason"),
    (COORDINATOR, "judge_rejections"),
    (COORDINATOR, "append_event"),
    (COORDINATOR, "escalation_summary"),
    (COORDINATOR, "_escalate"),
    (COORDINATOR, "_named_findings"),
    (INSPECTION, "_assemble"),
    (INSPECTION, "file_at_completion"),
    (INSPECTION, "_file_at_completion"),
)


@pytest.mark.parametrize("relative,name", LEFT_ALONE_FUNCTIONS,
                         ids=[name for _, name in LEFT_ALONE_FUNCTIONS])
def test_the_functions_the_story_was_to_leave_alone_read_as_they_did(
        relative, name):
    assert conftest.function_source(text_at(relative, BASELINE), name) == \
        conftest.function_source(text_at(relative, ENDPOINT), name)


def test_the_same_comparison_reports_a_function_this_story_did_change():
    """Control for the comparison above: the section's renderer, which this
    story taught to render decisions, reads differently across the same
    range."""
    assert conftest.function_source(text_at(COORDINATOR, BASELINE),
                                    SECTION_RENDERER) != \
        conftest.function_source(text_at(COORDINATOR, ENDPOINT),
                                 SECTION_RENDERER)


def test_no_event_kind_was_added_and_none_is_spelled_by_a_second_function():
    """Nothing new is recorded: every event kind the coordinator spells, and
    the functions spelling it, are what they were. The control is a copy of the
    coordinator carrying a second emitter of a decision kind, which the same
    scan tells apart."""
    before = kind_spellings(text_at(COORDINATOR, BASELINE))
    assert before
    assert kind_spellings(text_at(COORDINATOR, ENDPOINT)) == before
    planted = text_at(COORDINATOR, ENDPOINT) + PLANTED_EMITTER.format(
        kind=decided.DECISION_RECORDED)
    assert kind_spellings(planted) != before


def appended_events(source: str) -> int:
    """How many calls to append_event a source makes, bare or qualified."""
    return sum(1 for node in ast.walk(ast.parse(source))
               if isinstance(node, ast.Call)
               and (getattr(node.func, "id", None) == "append_event"
                    or getattr(node.func, "attr", None) == "append_event"))


@pytest.mark.parametrize("relative", [COORDINATOR, INSPECTION])
def test_neither_module_appends_an_event_it_did_not_append_before(relative):
    """The reading of the decisions writes nothing: each module makes the calls
    to append_event it made at this story's baseline and no more. The control
    is the same count over a copy carrying one more call, which differs."""
    before = appended_events(text_at(relative, BASELINE))
    assert before
    assert appended_events(text_at(relative, ENDPOINT)) == before
    planted = text_at(relative, ENDPOINT) + PLANTED_EMITTER.format(
        kind=decided.DECISION_RECORDED)
    assert appended_events(planted) == before + 1


def written_words(source: str, name: str) -> list[str]:
    """Every piece of literal text one function writes, in the order it writes
    them, its docstring left out."""
    function = ast.parse(conftest.function_source(source, name)).body[0]
    statements = function.body
    if statements and isinstance(statements[0], ast.Expr) \
            and isinstance(statements[0].value, ast.Constant):
        statements = statements[1:]
    pieces = [node for statement in statements for node in ast.walk(statement)
              if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    return [node.value for node in
            sorted(pieces, key=lambda node: (node.lineno, node.col_offset))]


def kept_in_order(words: list[str], within: list[str]) -> bool:
    """Whether `words` appear in `within`, each after the one before it."""
    remaining = iter(within)
    return all(any(word == other for other in remaining) for word in words)


def test_every_sentence_the_section_wrote_before_is_written_still_in_its_order():
    """The section's existing account — the four clauses and the sentences
    beside them — is written in the words and the order it was written in at
    this story's baseline, with the decisions added and nothing taken away or
    moved. The control is the same reading in the other direction, which finds
    the words this story added missing from the baseline."""
    before = written_words(text_at(COORDINATOR, BASELINE), SECTION_RENDERER)
    after = written_words(text_at(COORDINATOR, ENDPOINT), SECTION_RENDERER)
    assert before
    for opening in accounted.CLAUSES.values():
        assert opening in before, opening
    assert kept_in_order(before, after)
    assert not kept_in_order(after, before)
