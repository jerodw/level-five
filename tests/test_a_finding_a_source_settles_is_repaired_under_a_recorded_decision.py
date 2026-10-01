"""A finding whose fix an existing source already settles is repaired in the
run, under a decision recorded for review.

A run that checks its own work often finds a real defect whose fix needs a
choice between reasonable designs that the approved plan never made. Before
this story such a finding went to the backlog even when the answer was already
written down — in a repository standard, an architecture document, or the way
the code already solves the same problem somewhere else. Now the verification
may make that choice and repair the finding inside the run, on one condition:
it names the source the answer comes from. The choice is written to a decision
log committed with the story's history, the verification the repair returns to
is shown it and may reject it, and a decision that names no source is never
acted on — its finding goes to the backlog exactly as before.

The subjects, in the order the criteria state them:

  * **the verdict schema** accepts a repairable finding carrying a decision,
    whose source is deliberately not required, and a verdict carrying
    rejected_decisions — and still accepts every verdict it accepted before;
  * **the decided repair**, driven end to end under the fake runner against the
    inspection fixture `test_inspection_findings_reach_the_story` builds, with
    a repair pass declared on its judging stage: routed exactly as the same
    repair carrying no decision is, recorded once in the decision log under the
    story's own segment of the history, committed and still there after the
    run directory is deleted, and handed to the verification the pass returns
    to in the prompt that verification was given;
  * **the refusal** of a decision whose source is absent or blank, through the
    seam a refused scope extension takes: every file of its fix leaves the
    pass, the routed finding is declined with the refusal as its ground and
    filed at completion, and the rest of the verdict is routed;
  * **the rejection**, on a passing verdict and on a failing one: a record of
    its own beside the decision's untouched one, and the routed finding left by
    the run with the rejection as its ground and filed at completion — while a
    rejection naming a decision the returning pass did not carry changes
    nothing, and a decision on a finding the verifier raised itself, named by
    the slug of its own its location carries, is rejected into the log too and,
    since story-221, leaves that finding as well, under a key of its own and
    outside the routed counts;
  * **the completion report's existing account**, which names a decision that
    stood among the findings repaired and a rejected one among those sent to
    the backlog, with the report's rendering itself unchanged;
  * **nothing routes on confidence or blast radius**, a decision spends the
    allowance any repairable finding spends, and a correction pass's deferral
    records nothing;
  * **what this harness ships** — both verifier prompts, in the same words as
    each other, the schemas' descriptions, the event kinds and the history
    declaration — read as the subject they are;
  * **what the story left alone**, against this module's own story range.

Every absence asserted here is demonstrated to be able to fail:

  * the refused run's "no grant, no record, no decision log" sits beside the
    run whose verdict differs from it only in the source, which grants every
    file and records the decision, read by the same readers;
  * "a run deciding nothing writes no decision log" sits beside the decided run
    of the same test, whose log the same reader finds;
  * "an unmatched rejection changes nothing" and "a later verification cannot
    reject an earlier pass's decision" sit beside the run whose rejection names
    the decision the returning pass carried, which records it and moves the
    finding;
  * a rejection of the verifier's own decision "moves no routed finding" sits
    beside the routed rejection, which moves one, and that rejection reaching
    the log by its slug — and leaving the verifier's own finding — sits beside
    the same run naming the finding by its path, which records no rejection and
    leaves nothing;
  * "a decision meeting a spent allowance records nothing" sits beside the
    same verdicts under an allowance one pass larger, which records it; "a
    deferred decision records nothing" sits beside the run whose returning
    verification raises it again, which records it once;
  * "the decision log is committed as it stands" sits beside the same reader
    over a line written after the commit, which it reports;
  * "both prompts read the same" sits beside a copy of one with a sentence
    reworded, which the same comparison tells apart;
  * "the story left these paths alone" and "the report's rendering did not
    move" sit beside constructed histories and a mutated baseline the same
    comparisons tell apart.

Nothing here reaches a model: the sibling module's guard on
`agent_runner.run_agent` is installed for every test, and the stages run
through its fake runner. Every stage, category and artifact name is derived
from the workflow this module builds. What cannot be tested is whether a real
verifier cites a source that actually settles its question, or rejects a
decision when it should: that judgement is the returning verification's and
the reviewer's, which is the weak independence the story names.
"""
from __future__ import annotations

import inspect
import itertools
import json
import shutil
from pathlib import Path

import pytest

import conftest
import harness_config
import inspection
import schema_validator
import story_coordinator
import story_inspection
from conftest import BASELINE, ENDPOINT

import test_inspection_findings_reach_the_story as inspected
from test_inspection_findings_reach_the_story import (  # noqa: F401 - fixtures used by name
    filings,
    no_model,
)
from test_a_routed_finding_is_repaired_unless_the_verdict_says_why import (
    DEFAULT_OPENING,
    REPAIRABLE_PHRASE,
    every_shape_accepted_before,
    flowed,
    paragraph_containing,
    sentences,
    shipped_prompt_texts,
    verifier_prompts,
)
from test_a_small_finding_is_repaired_or_dropped_never_filed import (
    GROUNDS,
    GROUNDS_PHRASE,
    sentence_carrying,
)
from test_execution_history import default_kind, kind_spellings
from test_repair_pass import (
    CORRECTION_ROUTED,
    GRANT_EVENT,
    REQUIRED_OF_A_FINDING,
    ROUTED,
    paths_named_in,
)
from test_repair_pass import RECORDED as ALLOWANCE_SPENT

THIS_MODULE = Path(__file__)
COORDINATOR_SOURCE = Path(story_coordinator.__file__).read_text(encoding="utf-8")

STORY_ID = inspected.STORY_ID
STAGE_NAMES = inspected.STAGE_NAMES
WRITING, VALIDATING, JUDGING = STAGE_NAMES
CATEGORY = inspected.RETRY_CATEGORY
PASS = inspected.PASS
repairing = inspected.repairing

VERDICT_SCHEMA = schema_validator.load_schema("verification-result")
HISTORY_SCHEMA = schema_validator.load_schema("execution-history")
CROSS_RUN_SCHEMA = schema_validator.load_schema(
    story_coordinator.CROSS_RUN_HISTORY_SCHEMA)

#: Each declared log's filename mapped to the shape of a record in it, read
#: straight off the history declaration.
DECLARATIONS = {name: log["items"]
                for name, log in CROSS_RUN_SCHEMA["properties"].items()}
EVENT = story_coordinator.HISTORY_EVENT_PROPERTY

#: The field a repairable finding carries a decision under, and the verdict's
#: field naming the decisions it rejects — read off the coordinator's own
#: constants so the two cannot drift.
REPAIRABLE = "repairable_findings"
DECISION = story_coordinator.DECISION
REJECTED = story_coordinator.REJECTED_DECISIONS

REPAIRABLE_ITEM = VERDICT_SCHEMA["properties"][REPAIRABLE]["items"]
DECISION_SHAPE = REPAIRABLE_ITEM["properties"][DECISION]

#: The three decision fields an assertion below singles out: the one the
#: coordinator refuses on, and the two nothing routes on.
SOURCE = "source"
CONFIDENCE = "confidence"
BLAST_RADIUS = "blast_radius"
LEVELS = DECISION_SHAPE["properties"][CONFIDENCE]["enum"]

#: The events this story adds, as the execution-history schema enumerates
#: them. No run-level assertion below spells one except through these names.
DECISION_RECORDED = "decision-recorded"
DECISION_REJECTED = "decision-rejected"
DECISION_REFUSED = "decision-refused"
NEW_KINDS = (DECISION_RECORDED, DECISION_REJECTED, DECISION_REFUSED)

#: The field only a rejection's record carries, and the log told apart by it,
#: so this module writes no log filename of its own.
REJECTION = "rejection"
DECISION_LOG = next(name for name, shape in DECLARATIONS.items()
                    if REJECTION in shape["properties"])

# ==========================================================================
# The workflow these runs execute
# ==========================================================================

#: The sibling module's inspection workflow with a repair pass declared on the
#: stage its inspection judges. The names are this module's own rather than
#: anything this repository deploys: a record reaching the run directory under
#: this artifact name is what says the coordinator read it off the declaration.
WORKFLOW_NAME = "decided-repair-workflow"
REPAIR_ARTIFACT = "decided-repair.json"

#: The context field the coordinator injects a repair pass record under. The
#: sibling module's templates predate it, so this module adds it to them.
REPAIR_FIELD = "repair_pass_result"

PROMPTS = {name: (inspected.PROMPTS[name]
                  + f"\n{REPAIR_FIELD}:\n{{{{{REPAIR_FIELD}}}}}\n")
           for name in STAGE_NAMES}

#: The ceiling these runs execute under, the module's own because one case
#: must reach a second attempt.
RULES = {
    "max_retries": 2,
    "require_verifier_pass": True,
    "blocked_paths": [".git/", ".harness/runs/", ".harness/history/",
                      ".harness/stories/"],
}


def deciding_workflow(budget: int = 2) -> dict:
    """The sibling's workflow with a repair pass of `budget` passes declared
    on the stage carrying its inspection."""
    workflow = json.loads(json.dumps(inspected.WORKFLOW))
    for stage in workflow["stages"]:
        if stage.get("inspection"):
            stage["repair_pass"] = {"result": REPAIR_ARTIFACT, "budget": budget}
    workflow["name"] = WORKFLOW_NAME
    return workflow


#: The table the judging stage routes repairable findings through.
ROUTES = next(stage for stage in deciding_workflow()["stages"]
              if stage.get("inspection"))["on_failure"]["retry_routing"]


def materialize(root: Path, *, budget: int = 2) -> Path:
    """The workflow written where a run finds it, through the sibling's own
    materializer, with templates that render the repair pass record."""
    workflow = deciding_workflow(budget)
    built = inspected.materialize(workflow, root, rules=RULES)
    for stage in workflow["stages"]:
        (built / "prompts" / stage["prompt"]).write_text(
            PROMPTS[stage["name"]], encoding="utf-8")
    return built


@pytest.fixture
def harness(tmp_path: Path) -> Path:
    return materialize(tmp_path / "decided-repair-harness")


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return inspected.build_target(tmp_path / "decided-repair-target",
                                  workflow=WORKFLOW_NAME)


def fresh(tmp_path: Path, name: str, *, budget: int = 2) -> tuple[Path, Path]:
    """A target and a harness of their own, so one test can hold two runs side
    by side: a target that has run a story refuses to run it again."""
    return (inspected.build_target(tmp_path / f"{name}-target",
                                   workflow=WORKFLOW_NAME),
            materialize(tmp_path / f"{name}-harness", budget=budget))


# ==========================================================================
# The findings, the decision and the verdicts
# ==========================================================================

#: The sibling's routed findings, naming the workflow these runs execute: an
#: inspection drops a finding naming a workflow the harness does not define.
ROUTED_FINDING = {**inspected.OWN_FINDING, "workflow": WORKFLOW_NAME}
OTHER_ROUTED = {**inspected.BOTH_FINDING, "workflow": WORKFLOW_NAME}
SLUG = ROUTED_FINDING["slug"]
OTHER_SLUG = OTHER_ROUTED["slug"]

#: A decision citing a source. Each text is distinctive, so a search of a
#: prompt, an event or a record is looking for this decision's own words.
CHOSEN = {
    "question": "MARKER-QUESTION does the helper return a default or raise "
                "when the case is missing",
    "answer": "MARKER-ANSWER return the default, as the helper beside it does",
    SOURCE: f"MARKER-SOURCE {inspected.SIBLING_FILE}, whose helper already "
            f"returns a default for the same case",
    "rationale": "MARKER-RATIONALE the codebase answers this once already, "
                 "and raising appears nowhere beside it",
    CONFIDENCE: LEVELS[1],
    BLAST_RADIUS: LEVELS[0],
}

#: The same decision with its source absent, empty and blank — the three ways
#: a decision can name no source the schema still accepts.
SOURCELESS = {
    "absent": {key: value for key, value in CHOSEN.items() if key != SOURCE},
    "empty": {**CHOSEN, SOURCE: ""},
    "blank": {**CHOSEN, SOURCE: " \n\t "},
}


def deciding(decision=CHOSEN) -> dict:
    """The routed finding's repair, its location naming the slug, carrying
    `decision` as given — a copy, so no two verdicts share one — or carrying
    none, where `decision` is None."""
    finding = inspected.naming_the_slug(ROUTED_FINDING,
                                        inspected.REPAIRABLE_FINDING)
    if decision is None:
        return finding
    return {**finding, DECISION: json.loads(json.dumps(decision))}


DECIDED = deciding()

#: The second file of the same fix: its location names the same routed slug,
#: so a refused decision takes it out of the pass too.
SIBLING_HALF = {
    "path": inspected.CHANGED_TEST,
    "location": f"{inspected.CHANGED_TEST} ({SLUG})",
    "finding": "MARKER-SIBLING-HALF the check holds the behaviour replaced",
    "correction": "MARKER-SIBLING-HALF-FIX hold the behaviour the fix chose",
    "category": CATEGORY,
}

#: A repair of the verifier's own, tied to no routed slug, so it routes
#: whatever becomes of the decided fix.
UNRELATED = {
    "path": inspected.SIBLING_FILE,
    "location": f"{inspected.SIBLING_FILE} - the other helper",
    "finding": "MARKER-UNRELATED the other helper misses a case",
    "correction": "MARKER-UNRELATED-FIX handle the case",
    "category": CATEGORY,
}

#: The same repair of the verifier's own, carrying the decision.
OWN_DECIDED = {**UNRELATED, DECISION: dict(CHOSEN)}

#: The second routed finding's repair, carrying no decision.
OTHER_REPAIR = {
    "path": inspected.SIBLING_FILE,
    "location": f"{inspected.SIBLING_FILE} ({OTHER_SLUG})",
    "finding": "MARKER-OTHER the helper beside the change disagrees with it",
    "correction": "MARKER-OTHER-FIX make the two agree",
    "category": CATEGORY,
}

REJECTION_REASON = ("MARKER-REJECTION the source names a helper that raises, "
                    "so it does not say what the decision says")


def the_verdict(decision: dict | None = CHOSEN) -> dict:
    """The verdict the refusal and its control share: the decided repair, the
    second file of the same fix, and a repair of the verifier's own. Two calls
    differing in `decision` differ in nothing else."""
    return repairing(deciding(decision), SIBLING_HALF, UNRELATED)


def rejecting(slug: str, base: dict = PASS) -> dict:
    return {**base, REJECTED: [{"slug": slug, "reason": REJECTION_REASON}]}


# ==========================================================================
# Driving a run, and reading one back
# ==========================================================================


def drive(target: Path, harness: Path, tmp_path: Path, verdicts: list[dict],
          *, findings=None, name: str = "journal"):
    """One run of the sibling's fixture. The inspection routes the one finding
    unless the caller says otherwise."""
    return inspected.drive(
        target, harness, tmp_path,
        findings=[ROUTED_FINDING] if findings is None else list(findings),
        verdicts=verdicts, name=name)


def kinds(run) -> list[str]:
    return [entry["event"] for entry in story_coordinator.load_history(run.run_dir)]


def pass_record(run, number: int = 1) -> dict:
    return json.loads((run.run_dir / story_coordinator.repair_pass_result_file(
        REPAIR_ARTIFACT, number)).read_text(encoding="utf-8"))


def granted(run, path: str) -> list[dict]:
    """The repair-pass grant events naming `path` as a whole token."""
    return [event for event in inspected.events_of_kind(run, GRANT_EVENT)
            if paths_named_in(event["message"], (path,))]


def decision_log_path(run) -> Path:
    """Where this story's decision log is, through the writer-side resolver
    the run itself resolves its segment through."""
    config = harness_config.load_config(run.tree)
    return harness_config.history_segment(
        harness_config.history_dir(run.tree, config), STORY_ID) / DECISION_LOG


def decision_records(run) -> list[dict]:
    path = decision_log_path(run)
    if not path.is_file():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def projected(event: dict) -> dict | None:
    """What the history declaration makes of one entry for the decision log —
    the projection every log is written through."""
    return story_coordinator.history_record(
        event, [], STORY_ID, DECLARATIONS[DECISION_LOG])


def committed_as_it_stands(run, path: Path) -> bool:
    """Whether `path` is in the run tree's history and carries no change since
    it was committed — so what is on disk is what was committed."""
    relative = path.relative_to(run.tree).as_posix()
    logged = inspected._git(run.tree, "log", "--format=%H", "--",
                            relative).stdout.split()
    changed = inspected._git(run.tree, "status", "--porcelain", "--",
                             relative).stdout.strip()
    return bool(logged) and not changed


def declined_entry(recorded: dict, slug: str) -> dict:
    return {one["slug"]: one for one in recorded["declined"]}[slug]


def findings_section(run) -> str:
    """The completion report's account of the run's findings, as rendered."""
    report = (run.run_dir / "completion-report.md").read_text(encoding="utf-8")
    assert "## Findings\n" in report, report
    return report.split("## Findings\n", 1)[1].split("\n## ", 1)[0]


def entry_shape(entry: str) -> list[str]:
    """The stages a run that takes one pass entering at `entry` invokes."""
    return [*STAGE_NAMES, *STAGE_NAMES[STAGE_NAMES.index(entry):]]


def what_the_pass_did(run) -> dict:
    """Everything a run's repair pass decided, and what the accounting and the
    filing recorded of it, read off one run — so two runs can be compared
    whole. The decision itself is taken off the findings the record carries,
    because whether one was carried is what the comparison is about."""
    record = pass_record(run)
    recorded = inspected.accounting(run)
    return {
        "calls": list(run.runner.calls),
        "status": run.state["status"],
        "retry_count": run.state["retry_count"],
        "repair_pass_count": run.state["repair_pass_count"],
        "entered": record["stage"],
        "carried": [{key: value for key, value in one.items() if key != DECISION}
                    for one in record["findings"]],
        "granted": sorted((event["stage"], event["message"]) for event in
                          inspected.events_of_kind(run, GRANT_EVENT)),
        "acted_on": recorded["acted_on_slugs"],
        "remainder": recorded["remainder_slugs"],
        "declined": recorded["declined"],
        "filed": [event["filed"] for event in inspected.filed_events(run)],
        "queued": inspected.routed_markers(run),
    }


# ==========================================================================
# The fixture is what it claims to be
# ==========================================================================


def test_the_fixture_declares_a_repair_pass_on_the_stage_its_inspection_judges():
    """The premises every run below rests on: the judging stage declares both,
    every repair the runs carry names the one category the table routes, and
    the decided repair and the second file of its fix name the routed slug
    while the verifier's own repair does not."""
    stages = deciding_workflow()["stages"]
    assert story_coordinator.repair_pass_declaration(stages)["result"] \
        == REPAIR_ARTIFACT
    assert story_coordinator.inspection_declaration(stages)
    judging = next(stage for stage in stages if stage.get("inspection"))
    assert judging["name"] == JUDGING
    assert ROUTES[CATEGORY]["stage"] == WRITING
    for finding in (DECIDED, SIBLING_HALF, UNRELATED, OTHER_REPAIR):
        assert finding["category"] == CATEGORY
    assert set(CHOSEN) == set(DECISION_SHAPE["properties"])
    assert story_inspection.names_a_slug(DECIDED["location"], (SLUG,))
    assert story_inspection.names_a_slug(SIBLING_HALF["location"], (SLUG,))
    assert not story_inspection.names_a_slug(UNRELATED["location"], (SLUG,))
    assert story_inspection.names_a_slug(OTHER_REPAIR["location"], (OTHER_SLUG,))
    assert not story_inspection.names_a_slug(OTHER_REPAIR["location"], (SLUG,))


# ==========================================================================
# The verdict schema
# ==========================================================================


#: The confidence vocabulary a brief already uses, which the decision reuses.
BRIEF_CONFIDENCE = schema_validator.load_schema(
    inspection.BRIEF_SCHEMA)["properties"][CONFIDENCE]["enum"]


def test_a_repairable_finding_may_carry_a_decision_whose_source_is_not_required():
    """Optional on the finding, which still requires what it required; every
    field the decision records is declared, and every one but the source is
    required — a sourceless decision is the coordinator's to refuse, not the
    validator's."""
    assert DECISION in REPAIRABLE_ITEM["properties"]
    assert set(REPAIRABLE_ITEM["required"]) == REQUIRED_OF_A_FINDING
    assert DECISION_SHAPE["type"] == "object"
    assert set(DECISION_SHAPE["required"]) == set(CHOSEN) - {SOURCE}
    assert SOURCE in DECISION_SHAPE["properties"]
    for field in set(CHOSEN) - {CONFIDENCE, BLAST_RADIUS}:
        assert DECISION_SHAPE["properties"][field]["type"] == "string", field
    for field in (CONFIDENCE, BLAST_RADIUS):
        assert DECISION_SHAPE["properties"][field]["enum"] == BRIEF_CONFIDENCE


@pytest.mark.parametrize("decision", [CHOSEN, *SOURCELESS.values()],
                         ids=["citing a source", *SOURCELESS])
def test_a_verdict_carrying_a_decision_validates_with_or_without_its_source(
        decision):
    assert schema_validator.validate(the_verdict(decision), VERDICT_SCHEMA) == []


@pytest.mark.parametrize("field", sorted(set(CHOSEN) - {SOURCE}))
def test_a_decision_missing_any_other_field_is_refused_as_malformed(field):
    """The rest of the decision is the schema's to hold, and the refusal names
    the missing field. The control is the whole decision, accepted by the same
    call."""
    short = {key: value for key, value in CHOSEN.items() if key != field}
    errors = schema_validator.validate(the_verdict(short), VERDICT_SCHEMA)
    assert errors
    assert any(field in error for error in errors), errors
    assert schema_validator.validate(the_verdict(CHOSEN), VERDICT_SCHEMA) == []


@pytest.mark.parametrize("decision", [
    {**CHOSEN, CONFIDENCE: "certain"},
    {**CHOSEN, BLAST_RADIUS: "none at all"},
    {**CHOSEN, SOURCE: 3},
    "return the default",
], ids=["a confidence outside the vocabulary", "a blast radius outside it",
        "a source that is not a string", "a decision that is not an object"])
def test_a_malformed_decision_is_refused_by_the_schema(decision):
    """Only a source that is absent or records no words is the coordinator's
    to refuse; a decision of the wrong shape is a malformed verdict."""
    assert schema_validator.validate(the_verdict(decision), VERDICT_SCHEMA)


def test_a_verdict_may_reject_decisions_whether_it_passes_or_fails():
    """Optional at the top level, each entry naming a slug and a reason, and
    accepted on a failing verdict as on a passing one. Each entry missing
    either field is refused, beside the whole entry accepted."""
    assert REJECTED in VERDICT_SCHEMA["properties"]
    assert REJECTED not in VERDICT_SCHEMA.get("required", [])
    item = VERDICT_SCHEMA["properties"][REJECTED]["items"]
    assert set(item["required"]) == {"slug", "reason"}
    for base in (PASS, inspected.failing_into(CATEGORY)):
        assert schema_validator.validate(rejecting(SLUG, base),
                                         VERDICT_SCHEMA) == []
    for missing in ("slug", "reason"):
        entry = {key: value for key, value in
                 {"slug": SLUG, "reason": REJECTION_REASON}.items()
                 if key != missing}
        errors = schema_validator.validate({**PASS, REJECTED: [entry]},
                                           VERDICT_SCHEMA)
        assert errors, missing


@pytest.mark.parametrize("verdict", every_shape_accepted_before(),
                         ids=lambda v: v["status"] + ":" + ",".join(
                             sorted(k for k in v if k not in PASS)))
def test_every_verdict_accepted_before_is_accepted_still(verdict):
    """Adding the two fields refuses nothing that validated before, and each
    earlier shape still validates with a rejection beside its other fields."""
    assert schema_validator.validate(verdict, VERDICT_SCHEMA) == []
    assert schema_validator.validate(rejecting(SLUG, verdict),
                                     VERDICT_SCHEMA) == []


def test_the_decision_description_says_why_its_source_is_not_required():
    """What a verifier meeting the field has to know: where the source comes
    from, why leaving it out is refused by the coordinator rather than by the
    schema, that a decision never removes what was approved, and that nothing
    routes on the two levels."""
    description = flowed(DECISION_SHAPE["description"])
    assert "deliberately absent" in description
    assert "refused by the coordinator" in description
    assert "malformed verdict" in description
    assert "never remove any of it" in description
    assert "rejected_decisions" in description
    assert "Nothing routes on confidence or blast_radius" in description
    for kind in ("a repository standard", "a configured architecture document",
                 "the way this codebase already solves the same problem"):
        assert kind in description, kind


def test_the_rejection_description_says_what_is_judged_and_who_undoes_it():
    description = flowed(VERDICT_SCHEMA["properties"][REJECTED]["description"])
    assert "whether the source says what the decision says" in description
    assert "takes anything away from what was approved" in description
    assert "does not undo the change itself" in description
    assert "failing verdict" in description
    assert "record of its own" in description


#: The description of a declined finding's reason, where the grounds are
#: stated for the schema's reader.
REASON_DESCRIPTION = flowed(VERDICT_SCHEMA["properties"]["declined_findings"]
                            ["items"]["properties"]["reason"]["description"])


def test_the_declined_reason_states_the_split_grounds():
    """Every ground the prompts state is in the description, and the choice a
    source settles is named as what is *not* a ground, in the sentence that
    sends it to a decision instead."""
    for ground in GROUNDS:
        assert ground in REASON_DESCRIPTION, ground
    settled = [one for one in sentences(REASON_DESCRIPTION)
               if "an existing source settles" in one]
    assert len(settled) == 1, settled
    assert "is not a reason" in settled[0]
    assert "decision" in settled[0]


#: The clause every statement of the approved rule that a fix to the words
#: alone carries no decision is found by, in the prompts and the reason alike.
WORDS_ALONE = "a fix to the words alone that needs such a choice"


def test_the_schema_keeps_a_fix_to_the_words_alone_free_of_any_decision():
    """The approved rule, said once in the declined reason beside the sentence
    sending a settled choice to a decision, and said in the decision's own
    description, where a verifier about to attach one reads it: a decision
    rides only on a repairable finding, so a fix to the words alone needing a
    choice is declined and filed."""
    reason = sentence_carrying(REASON_DESCRIPTION, WORDS_ALONE)
    assert "rides only on a repairable finding" in reason
    assert "declined and filed" in reason
    decision = sentence_carrying(flowed(DECISION_SHAPE["description"]),
                                 "A fix to the words alone carries no decision")
    assert "declined and filed" in decision


def test_the_schema_says_how_a_decision_on_the_verifiers_own_finding_is_named():
    """A finding the verifier raised itself carries no routed slug, so the
    decision's description says to give a decided one a slug of its own, and
    the rejection's slug is described as either kind."""
    assert "a slug of your own" in flowed(DECISION_SHAPE["description"])
    slug = (VERDICT_SCHEMA["properties"][REJECTED]["items"]["properties"]
            ["slug"]["description"])
    assert "the slug of its own" in flowed(slug)


# ==========================================================================
# The refusal, as functions
# ==========================================================================


def decided_check(*findings: dict, routed=(SLUG,)):
    """The refusal asked over findings no extension bound refused, through the
    fixture's own table — the shape the coordinator hands it."""
    return story_coordinator.decision_check(
        story_coordinator.ScopeExtensionCheck(accepted=list(findings),
                                              refused=[]),
        ROUTES, routed)


@pytest.mark.parametrize("missing", sorted(SOURCELESS))
def test_a_sourceless_decision_takes_every_file_of_its_fix_out_of_the_pass(
        missing):
    """The decided finding and every finding naming its routed slug are
    refused, each against the decided finding and the stage its category
    resolves to; the verifier's own repair stays. The control is the same
    findings citing the source, which are all accepted."""
    findings = the_verdict(SOURCELESS[missing])[REPAIRABLE]
    checked = decided_check(*findings)
    assert checked.accepted == [findings[2]]
    assert [one.finding for one in checked.refused] == findings[:2]
    for one in checked.refused:
        assert isinstance(one, story_coordinator.RefusedDecision)
        assert one.stage == WRITING
        assert one.decided is findings[0]

    citing = the_verdict(CHOSEN)[REPAIRABLE]
    accepted = decided_check(*citing)
    assert accepted.accepted == citing
    assert accepted.refused == []


def test_a_sourceless_decision_tied_to_no_routed_slug_refuses_itself_alone():
    """A decision on a finding the verifier raised itself has no routed slug
    tying anything to it, so a finding naming the routed slug stays."""
    own = {**UNRELATED, DECISION: SOURCELESS["absent"]}
    checked = decided_check(own, SIBLING_HALF)
    assert [one.finding for one in checked.refused] == [own]
    assert checked.accepted == [SIBLING_HALF]


def test_a_refused_decision_is_declined_through_the_seam_a_refused_extension_takes():
    """The verdict handed to the accounting drops every refused file and
    declines the routed slug with the refusal as its reason, in the one
    wording `refusal_reason` composes — which names the decided finding and
    the question it left open."""
    verdict = the_verdict(SOURCELESS["absent"])
    checked = decided_check(*verdict[REPAIRABLE])
    assert story_coordinator.refused_routed_slugs(checked, (SLUG,)) == (SLUG,)
    handed = story_coordinator.verdict_after_refusals(verdict, checked, (SLUG,))
    assert handed[REPAIRABLE] == [UNRELATED]
    reasons = {story_coordinator.refusal_reason(one) for one in checked.refused}
    assert {one["slug"] for one in handed["declined_findings"]} == {SLUG}
    for one in handed["declined_findings"]:
        assert one["reason"] in reasons
    assert schema_validator.validate(handed, VERDICT_SCHEMA) == []
    first = story_coordinator.refusal_reason(checked.refused[0])
    assert DECIDED["location"] in first
    assert CHOSEN["question"] in first


def test_the_refusal_decides_alike_at_every_confidence_and_blast_radius():
    """Nothing routes on the two levels: at every pairing of them, a decision
    citing its source is accepted whole and one naming none is refused on
    exactly the same findings."""
    outcomes = set()
    for confidence, blast in itertools.product(LEVELS, LEVELS):
        for decision in (CHOSEN, SOURCELESS["absent"]):
            levelled = {**decision, CONFIDENCE: confidence, BLAST_RADIUS: blast}
            findings = the_verdict(levelled)[REPAIRABLE]
            checked = decided_check(*findings)
            outcomes.add((
                SOURCE in decision,
                tuple(findings.index(one) for one in checked.accepted),
                tuple(findings.index(one.finding) for one in checked.refused),
            ))
    assert outcomes == {(True, (0, 1, 2), ()), (False, (2,), (0, 1))}


# ==========================================================================
# The decided repair, driven
# ==========================================================================


def test_a_decided_repair_routes_exactly_as_the_same_repair_without_a_decision(
        tmp_path, filings):
    """Two runs whose verdicts differ only in the decision: the same stages
    entered, the same pass taken, the same files granted, the same accounting
    and the same filing. What the decided run does besides is record the
    decision — once — and its pass statement says the question is settled.
    The plain run's missing decision log is controlled by the decided run's,
    read by the same reader."""
    decided_target, decided_harness = fresh(tmp_path, "decided")
    plain_target, plain_harness = fresh(tmp_path, "plain")
    decided = drive(decided_target, decided_harness, tmp_path,
                    [the_verdict(CHOSEN), PASS], name="decided")
    plain = drive(plain_target, plain_harness, tmp_path,
                  [the_verdict(None), PASS], name="plain")
    assert decided.code == plain.code == 0

    did = what_the_pass_did(decided)
    assert did == what_the_pass_did(plain)
    assert did["status"] == "completed"
    assert did["repair_pass_count"] == 1
    assert did["retry_count"] == 0
    assert did["entered"] == WRITING
    assert did["calls"] == entry_shape(WRITING)
    assert did["acted_on"] == [SLUG]
    assert did["remainder"] == []
    for finding in (DECIDED, SIBLING_HALF, UNRELATED):
        (grant,) = granted(decided, finding["path"])
        assert grant["stage"] == WRITING
    assert inspected.filings_of(filings, ROUTED_FINDING) == 0

    assert [kind for kind in kinds(decided) if kind != DECISION_RECORDED] \
        == kinds(plain)
    assert kinds(decided).count(DECISION_RECORDED) == 1
    assert decision_records(plain) == []
    assert len(decision_records(decided)) == 1

    checked = pass_record(decided)[story_coordinator.CLEAN_CLONE_CHECKED]
    assert pass_record(plain)["statement"] == \
        story_coordinator.repair_pass_statement(WRITING,
                                                clean_clone_checked=checked)
    assert pass_record(decided)["statement"] == \
        story_coordinator.repair_pass_statement(WRITING,
                                                clean_clone_checked=checked,
                                                decided=True)


@pytest.fixture
def decided_run(target, harness, tmp_path):
    """The central run: the verdict carrying the decided repair, the second
    file of its fix and a repair of the verifier's own, then a clean verdict."""
    return drive(target, harness, tmp_path, [the_verdict(CHOSEN), PASS])


def test_the_decision_reaches_the_decision_log_as_one_record(decided_run):
    """One event where the pass is routed, carrying the decision's own words,
    the finding it was made for and the stage whose verdict made it, and one
    record in the decision log under the story's own segment carrying the
    same — with the event's own timestamp, so it was written when the pass was
    routed. Nothing is written at the root of the history directory, which the
    segment's own file controls for."""
    run = decided_run
    assert run.code == 0
    (event,) = inspected.events_of_kind(run, DECISION_RECORDED)
    assert event["stage"] == JUDGING
    assert event["finding"] == DECIDED["location"]
    for field, value in CHOSEN.items():
        assert event[field] == value, field
    (routed,) = inspected.events_of_kind(run, ROUTED)
    assert event["sequence"] > routed["sequence"]

    (record,) = decision_records(run)
    assert record == {"story_id": STORY_ID, "timestamp": event["timestamp"],
                      "stage": JUDGING, "finding": DECIDED["location"],
                      **CHOSEN}
    assert record == projected(event)
    assert REJECTION not in record

    path = decision_log_path(run)
    assert path.parent.name == STORY_ID
    history = path.parent.parent
    assert path.is_file()
    assert not (history / DECISION_LOG).exists()
    assert schema_validator.validate(
        story_coordinator.load_history(run.run_dir), HISTORY_SCHEMA) == []


def test_the_decision_record_is_committed_and_outlives_the_run_directory(
        decided_run):
    """The record is in the run tree's history as it stands, and deleting the
    run directory — every artifact the decision was made from — leaves it
    exactly as it was. The control is a line written after the commit, which
    the same reader reports."""
    run = decided_run
    path = decision_log_path(run)
    before = decision_records(run)
    assert len(before) == 1
    assert committed_as_it_stands(run, path)

    shutil.rmtree(run.run_dir)
    assert not run.run_dir.exists()
    assert decision_records(run) == before
    assert committed_as_it_stands(run, path)

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"story_id": STORY_ID}) + "\n")
    assert not committed_as_it_stands(run, path)


def test_the_returning_verification_is_handed_the_decision_in_its_prompt(
        decided_run):
    """Read off the prompt file the run wrote, which the returning entry to
    the judging stage overwrote: it carries the pass record verbatim, and with
    it the decision as the record holds it. The first entry's prompt, taken
    before any pass existed, carries none of it."""
    run = decided_run
    assert run.runner.calls.count(JUDGING) == 2
    record_text = (run.run_dir / story_coordinator.repair_pass_result_file(
        REPAIR_ARTIFACT, 1)).read_text(encoding="utf-8")
    (carried,) = [one for one in pass_record(run)["findings"]
                  if DECISION in one]
    assert carried[DECISION] == CHOSEN
    assert carried["location"] == DECIDED["location"]

    prompt = run.prompt(JUDGING)
    assert record_text in prompt
    for field in ("question", "answer", SOURCE, "rationale"):
        assert CHOSEN[field] in prompt, field

    first = run.runner.prompts[JUDGING][0]
    assert f"{REPAIR_FIELD}:\nNone" in first
    assert CHOSEN["question"] not in first


def test_the_report_names_a_decision_that_stood_among_the_repaired(decided_run):
    """The report's existing account: the routed finding repaired under a
    decision that stood is named as repaired within the run, and nothing is
    sent on. The rejected run below is the control for the absence, read by
    the same reader."""
    section = findings_section(decided_run)
    assert f"Repaired or corrected within the run: {SLUG}." in section
    assert "Sent to the backlog" not in section


# ==========================================================================
# The refusal, driven — beside the decided run above as its control
# ==========================================================================


@pytest.mark.parametrize("missing", sorted(SOURCELESS))
def test_a_decision_naming_no_source_is_refused_and_its_finding_filed(
        missing, target, harness, tmp_path, filings):
    """The verdict is not malformed — the schema accepts it and the verifier
    is not asked again — so the coordinator refuses the decision instead: no
    file of the decided fix is granted, nothing is recorded or logged, both
    findings of the fix are refused in the run's events, the routed finding is
    declined with the refusal as its ground and filed at completion, and the
    verifier's own repair is routed while the run goes on to complete.

    Its control is `decided_run`, whose verdict differs from this one only in
    the source: there every file is granted, the decision is recorded and the
    finding is acted on, read by the same readers below."""
    verdict = the_verdict(SOURCELESS[missing])
    assert schema_validator.validate(verdict, VERDICT_SCHEMA) == []
    run = drive(target, harness, tmp_path, [verdict, PASS])
    assert run.code == 0
    assert run.state["status"] == "completed"
    # The judging stage entered twice — its verdict and the one the pass
    # returns to — and no third time, which is what running it again in place
    # on a malformed verdict would have added.
    assert run.runner.calls == entry_shape(WRITING)

    assert run.state["repair_pass_count"] == 1
    assert pass_record(run)["findings"] == [{**UNRELATED, "stage": WRITING}]
    assert granted(run, DECIDED["path"]) == []
    assert granted(run, SIBLING_HALF["path"]) == []
    (grant,) = granted(run, UNRELATED["path"])
    assert grant["stage"] == WRITING

    refusals = inspected.events_of_kind(run, DECISION_REFUSED)
    assert len(refusals) == 2
    for refusal, finding in zip(refusals, (DECIDED, SIBLING_HALF)):
        assert refusal["stage"] == JUDGING
        assert finding["location"] in refusal["message"]
        assert paths_named_in(refusal["message"], (finding["path"],))
        assert WRITING in refusal["message"]
        assert CHOSEN["question"] in refusal["message"]
    assert inspected.events_of_kind(run, DECISION_RECORDED) == []
    assert decision_records(run) == []
    assert not decision_log_path(run).exists()

    recorded = inspected.accounting(run)
    assert recorded["routed"] == 1
    assert recorded["acted_on"] == 0
    assert recorded["remainder_slugs"] == [SLUG]
    entry = declined_entry(recorded, SLUG)
    assert entry["reason_given"] is True
    assert entry["reason"] in {one["message"] for one in refusals}
    assert inspected.filings_of(filings, ROUTED_FINDING) == 1
    assert inspected.routed_markers(run) == [ROUTED_FINDING["body"]]
    assert f"Sent to the backlog: {SLUG} ({entry['reason']})." in \
        findings_section(run)


def test_the_same_verdict_citing_its_source_grants_every_file_and_records_it(
        decided_run, filings):
    """The control for every absence the refusal asserts, through the same
    readers: the decided fix's files are granted, nothing is refused, the
    decision is recorded and logged, the finding is acted on and nothing is
    filed."""
    run = decided_run
    assert run.code == 0
    assert pass_record(run)["findings"] == [
        {**finding, "stage": WRITING}
        for finding in (DECIDED, SIBLING_HALF, UNRELATED)]
    for finding in (DECIDED, SIBLING_HALF):
        (grant,) = granted(run, finding["path"])
        assert grant["stage"] == WRITING
    assert inspected.events_of_kind(run, DECISION_REFUSED) == []
    assert len(inspected.events_of_kind(run, DECISION_RECORDED)) == 1
    assert len(decision_records(run)) == 1
    assert decision_log_path(run).exists()
    recorded = inspected.accounting(run)
    assert recorded["acted_on_slugs"] == [SLUG]
    assert recorded["remainder_slugs"] == []
    assert inspected.filings_of(filings, ROUTED_FINDING) == 0


# ==========================================================================
# The rejection, driven
# ==========================================================================


def rejected_run(target: Path, harness: Path, tmp_path: Path, slug: str):
    """Two findings routed; the first verdict repairs one under the decision
    and the other without one; the verification the pass returns to passes,
    rejecting whatever `slug` names. Runs differing only in `slug` are each
    other's control."""
    return drive(target, harness, tmp_path,
                 [repairing(DECIDED, OTHER_REPAIR), rejecting(slug)],
                 findings=[ROUTED_FINDING, OTHER_ROUTED])


def test_a_rejection_is_a_record_of_its_own_and_leaves_the_finding_for_the_backlog(
        target, harness, tmp_path, filings):
    """The verification the pass returned to rejects the decision behind the
    first routed finding's repair. The rejection is one event and one record
    of its own beside the decision's, which is left exactly as its own event
    projects; the accounting moves that finding from acted on to left, with
    the rejection as its ground, and says so; it is filed at completion, and
    the second routed finding, repaired without a decision, is not."""
    run = rejected_run(target, harness, tmp_path, SLUG)
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.state["repair_pass_count"] == 1

    (recorded,) = inspected.events_of_kind(run, DECISION_RECORDED)
    (rejected,) = inspected.events_of_kind(run, DECISION_REJECTED)
    assert rejected["sequence"] > recorded["sequence"]
    assert rejected["stage"] == JUDGING
    assert rejected["finding"] == DECIDED["location"]
    assert rejected["question"] == CHOSEN["question"]
    assert rejected[REJECTION] == REJECTION_REASON

    decision, rejection = decision_records(run)
    assert decision == projected(recorded)
    assert decision == {"story_id": STORY_ID, "timestamp": recorded["timestamp"],
                        "stage": JUDGING, "finding": DECIDED["location"],
                        **CHOSEN}
    assert rejection == {"story_id": STORY_ID,
                         "timestamp": rejected["timestamp"], "stage": JUDGING,
                         "finding": DECIDED["location"],
                         "question": CHOSEN["question"],
                         REJECTION: REJECTION_REASON}

    accounted = inspected.accounting(run)
    assert accounted["acted_on_slugs"] == [OTHER_SLUG]
    assert accounted["remainder_slugs"] == [SLUG]
    entry = declined_entry(accounted, SLUG)
    assert entry["reason_given"] is True
    assert entry["dropped"] is False
    assert REJECTION_REASON in entry["reason"]
    assert CHOSEN["question"] in entry["reason"]
    events = inspected.accounting_events(run)
    assert len(events) == 2
    assert SLUG in events[-1]["message"]
    assert REJECTION_REASON in events[-1]["message"]

    assert inspected.filings_of(filings, ROUTED_FINDING) == 1
    assert inspected.filings_of(filings, OTHER_ROUTED) == 0
    assert inspected.routed_markers(run) == [ROUTED_FINDING["body"]]


def test_the_report_names_a_rejected_decision_among_those_sent_to_the_backlog(
        target, harness, tmp_path):
    """The report's existing account, unchanged in its rendering: the finding
    whose decision was rejected is sent to the backlog with the rejection as
    its ground, and the one repaired without a decision is repaired."""
    run = rejected_run(target, harness, tmp_path, SLUG)
    entry = declined_entry(inspected.accounting(run), SLUG)
    section = findings_section(run)
    assert f"Sent to the backlog: {SLUG} ({entry['reason']})." in section
    assert f"Repaired or corrected within the run: {OTHER_SLUG}." in section


def note_naming_the_rejection(run) -> dict:
    """The one entry of the run's events carrying the rejection's words, which
    is how a rejection naming no carried decision is said."""
    (note,) = [entry for entry in story_coordinator.load_history(run.run_dir)
               if REJECTION_REASON in entry.get("message", "")]
    return note


def test_a_rejection_naming_no_decision_the_pass_carried_changes_nothing(
        target, harness, tmp_path, filings):
    """The same run with the rejection naming the second routed finding, whose
    repair the pass carried without a decision: nothing reaches the decision
    log but the decision, no accounting moves, nothing is filed, and the
    rejection is said in the run's events in a note of the default kind. The
    run above, differing only in the slug, is the control."""
    run = rejected_run(target, harness, tmp_path, OTHER_SLUG)
    assert run.code == 0
    assert inspected.events_of_kind(run, DECISION_REJECTED) == []
    (recorded,) = inspected.events_of_kind(run, DECISION_RECORDED)
    assert decision_records(run) == [projected(recorded)]

    accounted = inspected.accounting(run)
    assert sorted(accounted["acted_on_slugs"]) == sorted([SLUG, OTHER_SLUG])
    assert accounted["remainder_slugs"] == []
    assert len(inspected.accounting_events(run)) == 1
    assert inspected.filings_of(filings, ROUTED_FINDING) == 0
    assert inspected.filings_of(filings, OTHER_ROUTED) == 0

    note = note_naming_the_rejection(run)
    assert note["event"] == default_kind(COORDINATOR_SOURCE)
    assert note["stage"] == JUDGING
    assert OTHER_SLUG in note["message"]


def test_a_later_verification_cannot_reject_an_earlier_passs_decision(
        target, harness, tmp_path, filings):
    """The first pass carries the decision; the verification it returns to
    takes a second pass on a repair of its own; the verification that one
    returns to rejects the first pass's decision by its slug. A rejection is
    matched only against the pass the judging verification returned from, so
    nothing is recorded, nothing moves and nothing is filed."""
    run = drive(target, harness, tmp_path,
                [repairing(DECIDED), repairing(UNRELATED), rejecting(SLUG)])
    assert run.code == 0
    assert run.state["repair_pass_count"] == 2
    assert not any(DECISION in one for one in pass_record(run, 2)["findings"])
    assert inspected.events_of_kind(run, DECISION_REJECTED) == []
    assert len(decision_records(run)) == 1
    assert inspected.accounting(run)["acted_on_slugs"] == [SLUG]
    assert len(inspected.accounting_events(run)) == 1
    assert inspected.filings_of(filings, ROUTED_FINDING) == 0
    assert SLUG in note_naming_the_rejection(run)["message"]


def test_a_rejection_on_a_failing_verdict_is_honoured_too(
        target, harness, tmp_path, filings):
    """The verification the pass returns to fails the attempt and rejects the
    decision in the same verdict. The rejection is recorded beside the
    decision, the first attempt's accounting leaves the finding with the
    rejection as its ground, and the retry runs; the finding reaches the
    backlog at completion through the final attempt, which found it again."""
    failing = rejecting(SLUG, inspected.failing_into(CATEGORY))
    run = drive(target, harness, tmp_path,
                [repairing(DECIDED), failing, PASS])
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.state["retry_count"] == 1

    (rejected,) = inspected.events_of_kind(run, DECISION_REJECTED)
    assert rejected[REJECTION] == REJECTION_REASON
    decision, rejection = decision_records(run)
    assert REJECTION not in decision
    assert rejection[REJECTION] == REJECTION_REASON

    first = inspected.accounting(run, 1)
    assert first["acted_on_slugs"] == []
    assert first["remainder_slugs"] == [SLUG]
    assert REJECTION_REASON in declined_entry(first, SLUG)["reason"]
    assert inspected.filings_of(filings, ROUTED_FINDING) == 1


#: A slug of the verifier's own, distinct from every routed slug, which a
#: decided finding it raised itself names in its location so the verification
#: the pass returns to can name its decision.
OWN_SLUG = "own-default-helper"

#: The verifier's own decided repair, its location naming that slug.
OWN_DECIDED_NAMED = {**OWN_DECIDED,
                     "location": f"{UNRELATED['location']} ({OWN_SLUG})"}


def own_rejected_run(target: Path, harness: Path, tmp_path: Path, named: str):
    """The first verdict repairs the routed finding without a decision, beside
    the verifier's own repair carrying one; the verification the pass returns
    to passes, rejecting whatever `named` names. Runs differing only in
    `named` are each other's control."""
    return drive(target, harness, tmp_path,
                 [repairing(deciding(None), OWN_DECIDED_NAMED),
                  rejecting(named)])


def test_a_decision_on_the_verifiers_own_finding_is_rejected_by_its_own_slug(
        target, harness, tmp_path, filings):
    """The returning verification rejects the decision by the slug the
    verifier gave its own decided finding. The rejection is one event and one
    record of its own beside the decision's, which is left as its own event
    projects. No routed finding stands behind it, so the routed finding is
    still acted on, its counts are not amended and nothing is filed for it —
    the routed rejection above, whose accounting the same readers find
    amended, is the control for that absence.

    Since story-221 the verifier's own finding is left by the rejection on
    the terms a routed one is: the accounting carries it under its own key,
    `story_inspection.own_finding_key`, with the rejection as its ground. It
    carries no classification, so it is dropped at completion with that
    reason rather than filed, and the account names it among the discarded.
    The run below, whose rejection names the finding by its path, leaves
    nothing of the kind and is the control."""
    assert OWN_SLUG not in {SLUG, OTHER_SLUG}
    assert story_inspection.names_a_slug(OWN_DECIDED_NAMED["location"],
                                         (OWN_SLUG,))
    assert not story_inspection.names_a_slug(OWN_DECIDED_NAMED["location"],
                                             (SLUG, OTHER_SLUG))
    run = own_rejected_run(target, harness, tmp_path, OWN_SLUG)
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.state["repair_pass_count"] == 1

    (recorded,) = inspected.events_of_kind(run, DECISION_RECORDED)
    (rejected,) = inspected.events_of_kind(run, DECISION_REJECTED)
    assert recorded["finding"] == OWN_DECIDED_NAMED["location"]
    assert rejected["sequence"] > recorded["sequence"]
    assert rejected["stage"] == JUDGING
    assert rejected["finding"] == OWN_DECIDED_NAMED["location"]
    assert rejected[REJECTION] == REJECTION_REASON

    decision, rejection = decision_records(run)
    assert decision == projected(recorded)
    assert REJECTION not in decision
    assert rejection == {"story_id": STORY_ID,
                         "timestamp": rejected["timestamp"], "stage": JUDGING,
                         "finding": OWN_DECIDED_NAMED["location"],
                         "question": CHOSEN["question"],
                         REJECTION: REJECTION_REASON}

    accounted = inspected.accounting(run)
    assert accounted["acted_on_slugs"] == [SLUG]
    assert accounted["remainder_slugs"] == []
    assert len(inspected.accounting_events(run)) == 1
    assert inspected.filings_of(filings, ROUTED_FINDING) == 0
    assert inspected.routed_markers(run) == []

    key = story_inspection.own_finding_key(OWN_DECIDED_NAMED, (SLUG,))
    (own,) = accounted["own_left"]
    assert own["slug"] == key
    assert own["decision_rejected"] is True
    assert REJECTION_REASON in own["reason"]
    assert own["finding"]["location"] == OWN_DECIDED_NAMED["location"]
    assert own["finding"]["finding"] == OWN_DECIDED_NAMED["finding"]
    (filed,) = inspected.filed_events(run)
    assert story_inspection.UNCLASSIFIED_OWN_FINDING in filed["message"]
    assert filed["filed"] == 0
    discarded = next(one for one in findings_section(run).split("\n\n")
                     if one.startswith("Discarded as not worth filing: "))
    assert f"{key} ({story_inspection.UNCLASSIFIED_OWN_FINDING}" in discarded


def test_a_rejection_naming_the_verifiers_own_finding_by_its_path_matches_nothing(
        target, harness, tmp_path, filings):
    """Control for the run above: the same pair of verdicts, with the
    rejection naming the decided finding by its path rather than by the slug
    its location carries. A path is not a slug, so the same readers find no
    rejection event, a decision log holding the decision alone, and the
    rejection's words said in a note of the default kind — which is what the
    run above would have left had its slug matched nothing."""
    path = OWN_DECIDED_NAMED["path"]
    assert not story_inspection.names_a_slug(OWN_DECIDED_NAMED["location"],
                                             (path,))
    run = own_rejected_run(target, harness, tmp_path, path)
    assert run.code == 0
    assert inspected.events_of_kind(run, DECISION_REJECTED) == []
    (recorded,) = inspected.events_of_kind(run, DECISION_RECORDED)
    assert decision_records(run) == [projected(recorded)]

    note = note_naming_the_rejection(run)
    assert note["event"] == default_kind(COORDINATOR_SOURCE)
    assert note["stage"] == JUDGING
    assert path in note["message"]
    assert inspected.accounting(run)["acted_on_slugs"] == [SLUG]
    assert "own_left" not in inspected.accounting(run)
    assert inspected.filed_events(run) == []
    assert inspected.filings_of(filings, ROUTED_FINDING) == 0


# ==========================================================================
# Nothing routes on confidence or blast radius
# ==========================================================================


def test_verdicts_differing_only_in_confidence_and_blast_radius_run_alike(
        tmp_path, filings):
    """The lowest and the highest of both levels: the same stages, the same
    pass, the same grants, accounting and filing, and the same events. The
    two records differ in the levels and in nothing the run decided."""
    runs = {}
    for level in (LEVELS[0], LEVELS[-1]):
        level_target, level_harness = fresh(tmp_path, f"level-{level}")
        levelled = {**CHOSEN, CONFIDENCE: level, BLAST_RADIUS: level}
        runs[level] = drive(level_target, level_harness, tmp_path,
                            [the_verdict(levelled), PASS],
                            name=f"level-{level}")
    low, high = runs[LEVELS[0]], runs[LEVELS[-1]]
    assert low.code == high.code == 0
    assert what_the_pass_did(low) == what_the_pass_did(high)
    assert kinds(low) == kinds(high)
    assert inspected.filings_of(filings, ROUTED_FINDING) == 0

    (low_record,) = decision_records(low)
    (high_record,) = decision_records(high)
    differing = {key for key in low_record if low_record[key] != high_record[key]}
    assert {CONFIDENCE, BLAST_RADIUS} <= differing
    assert differing <= {CONFIDENCE, BLAST_RADIUS, "timestamp"}


# ==========================================================================
# The allowance a decision spends, and a correction pass's deferral
# ==========================================================================


def test_a_decision_meeting_a_spent_allowance_is_abandoned_as_any_finding_is(
        tmp_path):
    """Under an allowance of one pass, a returning verification raising the
    decided repair of the verifier's own meets the spent allowance: it is
    abandoned in the same words, with the same count and the same events as
    the same repair carrying no decision, and nothing reaches the decision
    log. The control for that absence is the test below."""
    seen = {}
    for name, finding in (("decided", OWN_DECIDED), ("plain", UNRELATED)):
        spent_target, spent_harness = fresh(tmp_path, f"spent-{name}", budget=1)
        run = drive(spent_target, spent_harness, tmp_path,
                    [repairing(inspected.REPAIRABLE_FINDING), repairing(finding)],
                    findings=(), name=f"spent-{name}")
        assert run.code == 0
        assert run.state["status"] == "completed"
        assert run.state["repair_pass_count"] == 1
        (spent,) = inspected.events_of_kind(run, ALLOWANCE_SPENT)
        assert finding["location"] in spent["message"]
        assert spent["abandoned"] == 1
        seen[name] = (run, spent["message"])
    decided, plain = seen["decided"], seen["plain"]
    assert decided[1] == plain[1]
    assert kinds(decided[0]) == kinds(plain[0])
    assert DECISION_RECORDED not in kinds(decided[0])
    assert decision_records(decided[0]) == []


def test_the_same_decision_within_the_allowance_is_recorded_where_its_pass_routes(
        tmp_path):
    """The same verdicts under an allowance of two: the second pass carries
    the decided repair, and the decision is recorded once, after that pass is
    routed and not before."""
    within_target, within_harness = fresh(tmp_path, "within", budget=2)
    run = drive(within_target, within_harness, tmp_path,
                [repairing(inspected.REPAIRABLE_FINDING), repairing(OWN_DECIDED),
                 PASS], findings=(), name="within")
    assert run.code == 0
    assert run.state["repair_pass_count"] == 2
    assert inspected.events_of_kind(run, ALLOWANCE_SPENT) == []
    first, second = inspected.events_of_kind(run, ROUTED)
    (recorded,) = inspected.events_of_kind(run, DECISION_RECORDED)
    assert first["sequence"] < second["sequence"] < recorded["sequence"]
    assert recorded["finding"] == OWN_DECIDED["location"]
    assert decision_records(run) == [projected(recorded)]


#: A verdict carrying a correctable finding beside the decided repair, which
#: is routed as a correction pass with the repair deferred.
CORRECTING_AND_DECIDING = {
    **PASS,
    "correctable_findings": [dict(inspected.CORRECTABLE_FINDING)],
    REPAIRABLE: [dict(OWN_DECIDED)],
}


def test_a_deferred_decision_is_recorded_once_its_repair_pass_routes(tmp_path):
    """The correction pass defers the decided repair; the verification it
    returns to raises it again, and only then is a repair pass routed and the
    decision recorded — once."""
    deferred_target, deferred_harness = fresh(tmp_path, "raised-again")
    run = drive(deferred_target, deferred_harness, tmp_path,
                [CORRECTING_AND_DECIDING, repairing(OWN_DECIDED), PASS],
                findings=(), name="raised-again")
    assert run.code == 0
    (corrected,) = inspected.events_of_kind(run, CORRECTION_ROUTED)
    (repaired,) = inspected.events_of_kind(run, ROUTED)
    (recorded,) = inspected.events_of_kind(run, DECISION_RECORDED)
    assert corrected["sequence"] < repaired["sequence"] < recorded["sequence"]
    assert len(decision_records(run)) == 1


def test_a_deferred_decision_never_raised_again_records_nothing(tmp_path):
    """The same correction pass, with the verification it returns to raising
    nothing: no repair pass routes and nothing is recorded. The run above is
    the control.

    Since story-221 that verification owes the postponed repair an answer and
    gives none. This module's judging stage declares no re-run to send it
    back with, so the repair is treated as declined without a reason — said
    in the run's events and left in the accounting under the verifier's own
    key — and the run completes on that verdict rather than escalating."""
    deferred_target, deferred_harness = fresh(tmp_path, "deferred")
    run = drive(deferred_target, deferred_harness, tmp_path,
                [CORRECTING_AND_DECIDING, PASS], findings=(), name="deferred")
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert len(inspected.events_of_kind(run, CORRECTION_ROUTED)) == 1
    assert inspected.events_of_kind(run, ROUTED) == []
    assert inspected.events_of_kind(run, DECISION_RECORDED) == []
    assert decision_records(run) == []

    assert inspected.events_of_kind(run, "self-routed") == []
    key = story_inspection.own_finding_key(OWN_DECIDED)
    assert any("treated as declined without a reason" in line and key in line
               for line in run.messages), run.messages
    (own,) = inspected.accounting(run)["own_left"]
    assert own["slug"] == key
    assert own["reason_given"] is False


# ==========================================================================
# The event kinds and the history declaration this harness ships
# ==========================================================================


def test_each_new_kind_is_declared_and_spelled_in_exactly_one_function():
    """The three kinds are in the execution-history enum, and each is spelled
    at one emitter of the coordinator. The control plants a second emitter of
    one of them into a copy of the source, which the same scan reports."""
    allowed = HISTORY_SCHEMA["items"]["properties"][EVENT]["enum"]
    spellings = kind_spellings(COORDINATOR_SOURCE)
    for kind in NEW_KINDS:
        assert kind in allowed, kind
        assert len(spellings.get(kind, ())) == 1, (kind, spellings.get(kind))
    doubled = COORDINATOR_SOURCE + (
        f"\n\ndef _a_second_writer(run_dir):\n"
        f"    append_event(run_dir, 'again', kind={DECISION_RECORDED!r})\n")
    assert len(kind_spellings(doubled)[DECISION_RECORDED]) == 2


def test_the_decision_kinds_reach_the_decision_log_alone_and_a_refusal_none():
    """The decision log's enum names the recorded and rejected kinds, no other
    log names either, and the refused kind is named by no log at all — so a
    refused decision is in the run's events and nowhere in the history."""
    routing = {log: set(shape["properties"][EVENT]["enum"])
               for log, shape in DECLARATIONS.items()}
    assert routing[DECISION_LOG] == {DECISION_RECORDED, DECISION_REJECTED}
    for log, routed in routing.items():
        assert DECISION_REFUSED not in routed, log
        if log != DECISION_LOG:
            assert not routed & {DECISION_RECORDED, DECISION_REJECTED}, log


def test_every_field_a_decision_record_carries_is_one_append_event_accepts():
    """Every field the decision log declares is either derived by the
    projection — the story from the run directory's name, the time from the
    entry's own stamp — or a field append_event takes and the execution
    history declares."""
    accepted = set(inspect.signature(story_coordinator.append_event).parameters)
    declared = set(HISTORY_SCHEMA["items"]["properties"])
    carried = set(DECLARATIONS[DECISION_LOG]["properties"]) - {EVENT}
    derived = {"story_id", "timestamp"}
    assert derived <= carried
    assert "timestamp" in declared
    for field in carried - derived:
        assert field in accepted, field
        assert field in declared, field


def test_the_history_declaration_says_why_a_decision_record_copies_its_words():
    """The statement that a record is a summary and never a copy stands, and
    the decision log is named as its one exception with the reason; the log's
    own description says it is append-only and routed on by nothing; and the
    reserved adjudication log stays reserved, beside the decision log, which
    is declared."""
    whole = flowed(CROSS_RUN_SCHEMA["description"])
    assert "A record is a summary and never a copy" in whole
    exception = [one for one in sentences(whole) if "decision log" in one]
    assert exception
    assert any("own words" in one for one in exception), exception
    assert any("run directory" in one for one in exception), exception
    log = flowed(CROSS_RUN_SCHEMA["properties"][DECISION_LOG]["description"])
    assert "append-only" in log
    assert "never rewritten" in log
    assert "Nothing routes" in log
    reserved = "adjudication-log.jsonl"
    assert reserved in whole
    assert reserved not in DECLARATIONS
    assert DECISION_LOG in DECLARATIONS


# ==========================================================================
# The verifier prompts this harness ships
# ==========================================================================


def repair_slot_paragraph(text: str) -> str:
    """The paragraph introducing the rendered repair pass record — the last
    one before its slot — flowed."""
    marker = f"{{{{{REPAIR_FIELD}}}}}"
    return flowed(text[:text.index(marker)].rstrip().split("\n\n")[-1])


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_each_prompt_splits_the_ground_a_routed_finding_may_be_declined_on(
        prompt: Path):
    """A choice the plan does not make and no source settles stays a ground,
    and so does a fix the plan's own words bar — which want opposite
    remedies, the second the plan changed — while a choice an existing source
    settles is not a ground and is repaired under a recorded decision."""
    default = paragraph_containing(prompt.read_text(encoding="utf-8"),
                                   DEFAULT_OPENING)
    grounds = sentence_carrying(default, GROUNDS_PHRASE)
    for ground in GROUNDS:
        assert ground in grounds, ground
    remedies = sentence_carrying(default, "opposite remedies")
    assert "plan changed rather than a decision made" in remedies
    assert "declined and filed" in remedies
    settled = sentence_carrying(default, "an existing source settles is not a reason")
    for kind in ("a repository standard", "a configured architecture document",
                 "the way this codebase already solves the same problem"):
        assert kind in settled, kind
    assert "repaired under a recorded decision" in settled


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_each_prompt_keeps_a_fix_to_the_words_alone_free_of_any_decision(
        prompt: Path):
    """The triage paragraph sends a settled choice to a decision, and says
    once that a decision rides only on a repairable finding, so a fix to the
    words alone needing such a choice is declined and filed."""
    default = paragraph_containing(prompt.read_text(encoding="utf-8"),
                                   DEFAULT_OPENING)
    words_alone = sentence_carrying(default, WORDS_ALONE)
    assert "rides only on a repairable finding" in words_alone
    assert "declined and filed" in words_alone


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_each_prompt_says_how_a_decided_repair_is_recorded(prompt: Path):
    """The repairable paragraph names the field and what it records, that a
    decision never removes what was approved, and that a choice no source
    settles is declined rather than decided, a sourceless decision refused."""
    repairable = paragraph_containing(prompt.read_text(encoding="utf-8"),
                                      REPAIRABLE_PHRASE)
    recording = sentence_carrying(repairable, f"`{DECISION}`")
    for part in ("the question", "the answer", "the source that settles it",
                 "why this answer rather than another", "how sure you are",
                 "how costly a wrong answer would be to undo"):
        assert part in recording, part
    bounded = sentence_carrying(repairable, "never remove any of it")
    assert "a choice no source settles is declined rather than decided" in bounded
    assert "a decision naming no source is refused" in bounded
    assert "every file of its fix leaves the pass" in bounded


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_each_prompt_tells_the_returning_verification_how_to_judge_a_decision(
        prompt: Path):
    """The paragraph introducing the repair pass record says what the
    verification it reaches judges of each decision, where it rejects one,
    that the coordinator records the rejection and does not undo the change,
    which routes undo it, and that only that record's decisions are its to
    reject."""
    slot = repair_slot_paragraph(prompt.read_text(encoding="utf-8"))
    judging = sentence_carrying(slot, "whether the source says what the decision says")
    assert "whether a source settles the question at all" in judging
    assert "takes anything away from what was approved" in judging
    naming = sentence_carrying(slot, REJECTED)
    assert "whether your verdict passes or fails" in naming
    assert "does not undo the change itself" in naming
    undoing = sentence_carrying(slot, "Undo it with the routes you already have")
    assert "a repairable finding restoring the files" in undoing
    assert "a failed verdict and a retry" in undoing
    assert "Only the decisions this record carries can be rejected here" in slot


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_each_prompt_says_how_a_decision_on_the_verifiers_own_finding_is_named(
        prompt: Path):
    """A finding the verifier raised itself carries no routed slug, so the
    repairable paragraph says, once, to give a decided one a slug of its own,
    and the returning verification is told it may reject by that slug."""
    text = prompt.read_text(encoding="utf-8")
    repairable = paragraph_containing(text, REPAIRABLE_PHRASE)
    own = sentence_carrying(repairable, "a slug of your own")
    assert "distinct from every slug routed to this story" in own
    naming = sentence_carrying(repair_slot_paragraph(text), REJECTED)
    assert "the slug of your own" in naming


#: The passages this story writes in, each by the reading that finds it, and
#: the words that find this story's sentences within them.
PASSAGES = {
    "default": lambda text: paragraph_containing(text, DEFAULT_OPENING),
    "repairable": lambda text: paragraph_containing(text, REPAIRABLE_PHRASE),
    "repair-pass slot": repair_slot_paragraph,
}
NEEDLES = (
    "no existing source settles",
    "opposite remedies",
    "an existing source settles is not a reason",
    WORDS_ALONE,
    f"`{DECISION}`",
    "never remove any of it",
    "a slug of your own",
    "whether the source says what the decision says",
    REJECTED,
    "Undo it with the routes you already have",
    "Only the decisions this record carries",
)


def decision_sentences(text: str) -> tuple:
    """Every sentence of the walked passages carrying one of the needles,
    passage by passage — the reading the parity assertion compares."""
    return tuple(
        (key, tuple(one for one in sentences(read(text))
                    if any(needle in one for needle in NEEDLES)))
        for key, read in PASSAGES.items())


def test_this_storys_sentences_read_the_same_in_both_prompts():
    """The refactor workflow's verifier prompt carries the same sentences in
    the same words. Each passage is asserted to carry some, so equality is
    between found sentences rather than between two empty readings."""
    texts = shipped_prompt_texts()
    assert len(texts) > 1, texts
    found = {name: decision_sentences(text) for name, text in texts.items()}
    for name, passages in found.items():
        for key, carried in passages:
            assert carried, (name, key)
        carried_needles = {needle for _, carried in passages
                           for one in carried for needle in NEEDLES
                           if needle in one}
        assert carried_needles == set(NEEDLES), (name, set(NEEDLES) - carried_needles)
    assert len(set(found.values())) == 1, found


def test_the_comparison_tells_a_prompt_whose_sentence_drifted_apart():
    """Control for the equality above: a copy of one shipped prompt with one
    of this story's sentences reworded on a line it occupies whole, put back
    through the same reading beside the other prompt, reads differently."""
    texts = shipped_prompt_texts()
    name = sorted(texts)[0]
    drifted = texts[name].replace("refine what was approved",
                                  "refine what was agreed", 1)
    assert drifted != texts[name]
    found = {decision_sentences(text)
             for text in {**texts, name: drifted}.values()}
    assert len(found) == 2


# ==========================================================================
# What this story left alone
# ==========================================================================


#: The paths the story's own scope.do_not_modify names as files or
#: directories outside tests/, resolved through the shared story range so the
#: comparison stays honest once the story commits. The two test modules that
#: entry also names are not here: their checks keep holding as they stand,
#: which is what running them says.
LEFT_ALONE = [
    "prompts/assist.md",
    "prompts/story-implementer.md",
    "prompts/story-tester.md",
    "prompts/documenter.md",
    "prompts/refactor-implementer.md",
    "prompts/inspector.md",
    "workflows/",
    "orchestration/inspection.py",
]


def test_the_assistant_the_writing_stages_and_both_workflows_are_untouched():
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
    reported = conftest.constructed_story_diff(violating, LEFT_ALONE)
    for path in LEFT_ALONE:
        assert path.rstrip("/") in reported, path


#: The completion report's rendering, by the file and the function that hold
#: each part of it.
RENDERING = (
    ("orchestration/story_coordinator.py", "_findings_account_section"),
    ("orchestration/story_coordinator.py", "_named_findings"),
    ("orchestration/story_inspection.py", "account_for_the_report"),
)


def rendering_moved(before: str, after: str, name: str) -> bool:
    """Whether the function `name` reads differently in two texts of its
    file."""
    return conftest.function_source(before, name) != \
        conftest.function_source(after, name)


@pytest.mark.parametrize("relative,name", RENDERING,
                         ids=[name for _, name in RENDERING])
def test_the_completion_reports_rendering_is_what_it_was(relative, name):
    """Compared across this module's own story range: the account a report
    renders is the one it rendered before decisions existed."""
    before = conftest.repository_file_at(relative, validation_file=THIS_MODULE,
                                         bound=BASELINE)
    after = conftest.repository_file_at(relative, validation_file=THIS_MODULE,
                                        bound=ENDPOINT)
    assert not rendering_moved(before, after, name)


def test_the_rendering_comparison_reports_a_function_that_moved():
    """Control for the comparison above: the baseline text with one word of
    the report's heading changed reads differently from the endpoint, by the
    same comparison."""
    relative, name = RENDERING[0]
    before = conftest.repository_file_at(relative, validation_file=THIS_MODULE,
                                         bound=BASELINE)
    after = conftest.repository_file_at(relative, validation_file=THIS_MODULE,
                                        bound=ENDPOINT)
    moved = before.replace("Sent to the backlog: ", "Filed for later: ", 1)
    assert moved != before
    assert rendering_moved(moved, after, name)
