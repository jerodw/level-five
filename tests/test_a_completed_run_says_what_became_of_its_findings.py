"""story-213 validation: a completed run says what became of every finding it
made.

A run looks at the code it has just written, fixes some of the problems it
finds, decides others belong to somebody else and sends them to the backlog,
and throws a few away as too small to be worth anyone's time. The report a
developer reads at the end of the run said none of that: they met it later, in
the backlog, without the run's side of the story. And one outcome had no account
anywhere — a run is allowed a bounded number of attempts at fixing a defect it
found in its own work, and when that allowance ran out the defect went nowhere:
nothing was filed, the run reported success, and the durable record actively
said the run had dealt with it.

So the report now carries an account of what became of every finding, naming
each one individually: the ones the run fixed, the ones it is sending to the
backlog and on what grounds, the ones it discarded, and the ones it abandoned
when an allowance ran out. The false record is corrected, which is also what
lets the existing machinery file an abandoned finding like any other one the
verdict left, and the count of what a run abandoned is carried into the
repository's committed history so it outlives the run's own working files.

The subjects, in the order the criteria state them:

  * **the account in a completed run's report**, driven as whole runs rather
    than as a derivation in isolation. One run reaches every disposition a run
    can reach without a spent allowance — a finding it corrected, one it is
    sending on with the ground the verdict gave, one it is sending on where the
    verdict gave none, one it discarded, and one its inspection made about a
    file beside the change — and the account's four clauses are read back out
    of the report the run left.

  * **the abandonment.** Two runs spend an allowance: one meets both allowances
    on the verdicts its own passes return to, which is the amending path, and
    one meets an allowance already spent on the first verdict of a later
    attempt, which is the writing path. In both, what the allowance abandoned is
    recorded nowhere as acted on, is filed at the run's completion through the
    same call every other producer files through, and is named in the report
    with the allowance that ran out as its ground.

  * **the durable record.** The count travels into the committed cross-run
    history under the story, is told apart from the three kinds of record beside
    it by the fields it carries, and is still readable once the run directory
    has been deleted.

  * **what the count covers and what the account covers**, which are not the
    same set. The verifier may name a finding of its own in the same array as
    one the inspection routed, and a spent allowance abandons both: the count
    in the committed history is over every finding the verdict named, while the
    account, the accounting and the filing are about the routed ones, because a
    finding nothing routed has no routed finding for them to be about. One run
    drives exactly that verdict, and the texts a reader consults to learn what
    the number means are held to saying which set each covers.

  * **a pass the run never reached**, which abandons nothing. The clean-clone
    check sits between the two pass blocks, so a verdict whose check fails is
    routed away before the repair block; and a verdict the correction block
    routes on defers its repairable findings rather than leaving them. Each is
    driven as a run, each beside a run differing from it in one respect only,
    where the same spent allowance *is* met and recorded.

  * **the two accounting paths**, which record one abandonment identically. A
    spent allowance can be met on the verdict an attempt is first accounted on
    or on a later call about that attempt, and a finding abandoned by either
    records no reason, says none was given and carries the allowance's own
    words in the marker beside it — with a run in which the verdict *did*
    decline the finding as the control.

  * **the shipped texts** that stated the superseded rule: the two coordinator
    docstrings, the two schema descriptions of the field that carries the count,
    and the two spent-allowance test docstrings.

Every absence asserted here is demonstrated to be able to fail:

  * "a run that abandoned nothing writes no abandonment clause and no
    abandonment record" sits beside the two runs that did abandon something,
    where the same readings find both;
  * "a run under a workflow declaring no inspection carries no account" is a
    byte comparison against the report of a run under the same definition with
    the inspection declared, whose report differs from it by the account section
    and by nothing else — and sits beside the same renderer handed an account
    that says an inspection was made, which does render a section, so the
    absence is the guard deciding rather than a section that cannot be written;
  * "the account names no disposition the run did not reach" is asserted as an
    equality between the dispositions each report names and the ones its run
    reached, over three runs that reached three different sets of them;
  * "a run whose inspection routed nothing says so" sits beside the run that
    routed something, where the same reading does not find that sentence;
  * "the report states no count of what was filed" sits beside the completion
    event of the same run, which does carry one;
  * "no shipped text still says the event stream is the whole of what becomes of
    an abandoned finding" sits beside each of those same texts with the sentence
    planted back into it, which the same reading reports — and beside two
    accurate sentences carrying one half of that claim each, which the same
    reading does not report, so the silence is the claim being absent rather
    than a phrase this repository happens not to have written lately.

Nothing here reaches a model: the sibling module's guard on
`agent_runner.run_agent` is installed for every test here too, and every run
goes through a fake stage runner.

The workflow these runs execute is the sibling module's built definition with a
repair pass declared beside the correction pass it already declares, so a single
run can meet both allowances — which is what a story about what a spent
allowance leaves behind needs and what no definition in the suite had. A second
probe adds a clean-clone check to that one, because the check is what sits
between the two pass blocks and a run cannot be routed out of a gap that is not
there. Their stage names, artifact names, retry table and inspection
declaration are the sibling's, derived and never written here. What this
repository deploys is `tests/test_shipped_workflow_is_valid.py`'s subject.

`.harness/docs/ARCHITECTURE.md` is not asserted on: this story's plan assigns
its two passages to the documenter, the stage that runs after this one. The
sweep below therefore reads `orchestration/` and `schemas/`, and the two test
docstrings this story names, which are all in the tree by the end of this
stage's turn.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import shutil
from pathlib import Path

import pytest

import conftest
import schema_validator
import story_coordinator
import story_inspection

import test_inspection_findings_reach_the_story as inspected
import test_one_defect_found_on_several_attempts_is_filed_once as filed_once
from test_inspection_findings_reach_the_story import (  # noqa: F401 - fixtures used by name
    filings,
    no_model,
)

REPO_ROOT = conftest.HARNESS_ROOT
TESTS_DIR = REPO_ROOT / "tests"

STORY_ID = inspected.STORY_ID

#: The report a completed run leaves, by the name `_complete` writes it under.
COMPLETION_REPORT = "completion-report.md"

#: The heading the account is rendered under, and the field the two
#: spent-allowance events carry the count in. Spelled once each; every
#: assertion below derives from these.
FINDINGS_HEADING = "## Findings"
ABANDONED = "abandoned"

#: The event kinds the readings below look for by name — the check that sits
#: between the two pass blocks failing, each pass meeting its spent allowance,
#: and the correction pass routing. Each is held against the execution
#: history's own enum in the premise test, so a renamed kind fails there rather
#: than leaving a reading that quietly matches nothing.
CLEAN_CLONE_FAILED = "clean-clone-failed"
REPAIR_RECORDED = "repair-pass-recorded"
CORRECTION_RECORDED = "correction-pass-recorded"
CORRECTION_ROUTED = "correction-pass-routed"


# ==========================================================================
# The workflow these runs execute, and the target they execute in
# ==========================================================================


#: The artifact this module's repair pass names — deliberately not a name this
#: repository deploys, on the terms the sibling module's own declarations are
#: named: a record reaching the run directory under this name is what says the
#: coordinator reads the name off the declaration rather than carrying one.
REPAIR_ARTIFACT = "repair-probe.json"

#: One pass of each kind, which is the smallest allowance a run can spend and
#: then be asked for again. Every case below needs an allowance that is already
#: spent, and a larger one only makes the run longer without making anything
#: about the spending different. Owned here rather than inherited, so the
#: sequences below stay exact whatever the sibling declares for itself.
ALLOWANCE = 1


def with_both_allowances(definition: dict) -> dict:
    """`definition` with a repair pass declared beside its correction pass, and
    both allowances set to `ALLOWANCE`.

    The probe-workflow idiom the clean-clone, correction-pass and inspection
    modules already use: a definition derived from a built one by mutating the
    declarations the tests are about, rather than a second copy of four stages.

    The *name* is the sibling's, unchanged. A finding names the workflow it was
    found under, as a brief must, and a name the harness does not define is
    dropped before the filing — so a renamed probe would drop every finding
    below and every case here would be about the drop instead.
    """
    built = json.loads(json.dumps(definition))
    for stage in built["stages"]:
        if "correction_pass" not in stage:
            continue
        stage["correction_pass"] = {**stage["correction_pass"],
                                    "budget": ALLOWANCE}
        stage["repair_pass"] = {"result": REPAIR_ARTIFACT, "budget": ALLOWANCE}
    return built


WORKFLOW = with_both_allowances(inspected.WORKFLOW)


def with_a_clean_clone_check(definition: dict) -> dict:
    """`definition` with a clean-clone check declared on the stage that judges,
    routing a failure to the definition's first stage.

    The same probe idiom again, for the one thing the definition above cannot
    say: the check sits between the correction block and the repair block, and
    a failed check takes the retry route before the repair block is reached.
    Without a check declared there is no *between* for a run to be routed out
    of, so what a pass the run never reached leaves recorded could not be
    driven at all. The destination is read off the definition rather than
    written, as every other stage name here is.
    """
    built = json.loads(json.dumps(definition))
    for stage in built["stages"]:
        if "repair_pass" not in stage:
            continue
        stage["clean_clone"] = {"result": conftest.CLEAN_CLONE_RESULT,
                                "retry_stage": built["stages"][0]["name"]}
    return built


CHECKED_WORKFLOW = with_a_clean_clone_check(WORKFLOW)

#: The stage the definition declares the inspection and both passes on, and the
#: artifact the inspection writes, both found through the coordinator's own
#: readers rather than by position.
DECLARATION = story_coordinator.inspection_declaration(WORKFLOW["stages"])
FINDINGS_ARTIFACT = DECLARATION["result"]
JUDGING_STAGE = next(stage for stage in WORKFLOW["stages"]
                     if stage.get("inspection"))
#: The categories a finding of either kind may name, read off the definition
#: rather than written, so what counts as routable here is what the coordinator
#: routes on.
ROUTES = JUDGING_STAGE["on_failure"]["retry_routing"]
RETRY_CATEGORY = inspected.RETRY_CATEGORY

CROSS_RUN_SCHEMA = schema_validator.load_schema("cross-run-history")
HISTORY_SCHEMA = schema_validator.load_schema("execution-history")


@pytest.fixture
def harness(tmp_path) -> Path:
    """A harness root carrying the definition above, under a retry ceiling this
    module does not own but reaches for by name: a ceiling read off the shipped
    rules would make the retried run below a fact about the deployment."""
    return inspected.materialize(WORKFLOW, tmp_path / "account-harness",
                                 rules=filed_once.RULES)


@pytest.fixture
def target(tmp_path) -> Path:
    return inspected.build_target(tmp_path / "account-target",
                                  workflow=WORKFLOW["name"])


def test_the_definition_declares_the_inspection_and_both_allowances_together():
    """The premise every run below rests on. The inspection, the correction
    pass and the repair pass are declared on one stage, so one verdict can both
    name a routed finding and meet a spent allowance; each allowance is one, so
    one pass spends it; and the repair pass has the retry table its findings'
    categories resolve against."""
    assert FINDINGS_ARTIFACT == inspected.FINDINGS_ARTIFACT
    assert JUDGING_STAGE["name"] == inspected.JUDGING
    assert JUDGING_STAGE["correction_pass"]["budget"] == ALLOWANCE
    assert JUDGING_STAGE["repair_pass"]["budget"] == ALLOWANCE
    assert JUDGING_STAGE["repair_pass"]["result"] == REPAIR_ARTIFACT
    assert RETRY_CATEGORY in JUDGING_STAGE["on_failure"]["retry_routing"]
    # And the shipped pre-flight accepts it, so a run refused for the
    # definition's shape could not be mistaken for one of the cases below.
    assert story_coordinator.repair_pass_problems(WORKFLOW["stages"]) == []
    assert story_coordinator.correction_pass_problems(WORKFLOW["stages"]) == []


def test_the_checked_definition_is_the_same_one_with_a_check_between_them():
    """The premise the pair about a pass the run never reached rests on: the
    check is declared on the same stage as both allowances, so it really does
    sit between the two blocks, and its failure routes somewhere the definition
    declares. Everything else is the definition above, so the two runs differ
    in the check and in nothing else."""
    checked = next(stage for stage in CHECKED_WORKFLOW["stages"]
                   if stage.get("inspection"))
    assert checked["name"] == JUDGING_STAGE["name"]
    assert checked["clean_clone"]["retry_stage"] == \
        CHECKED_WORKFLOW["stages"][0]["name"]
    assert story_coordinator.repair_pass_problems(
        CHECKED_WORKFLOW["stages"]) == []
    assert [stage for stage in CHECKED_WORKFLOW["stages"]
            if "clean_clone" in stage] == [checked]
    without = json.loads(json.dumps(CHECKED_WORKFLOW))
    for stage in without["stages"]:
        stage.pop("clean_clone", None)
    assert without == WORKFLOW


def test_the_event_kinds_this_module_looks_for_are_kinds_the_history_declares():
    """Every kind named above is one the execution history's own enum carries,
    so a reading that finds none of them below is a run that did not take that
    route rather than a name nothing has answered to since it was renamed."""
    declared = HISTORY_SCHEMA["items"]["properties"]["event"]["enum"]
    for kind in (CLEAN_CLONE_FAILED, REPAIR_RECORDED, CORRECTION_RECORDED,
                 CORRECTION_ROUTED):
        assert kind in declared, kind


# ==========================================================================
# The findings, and the verdicts that decide what becomes of each
# ==========================================================================


CHANGED = inspected.CHANGED_FILE
BESIDE_THE_CHANGE = inspected.SIBLING_FILE


def finding(slug: str, paths: list[str], marker: str) -> dict:
    """One conforming finding of the sibling module's shape, naming the
    workflow these runs execute — which is what a brief is planned under and
    what the filing drops a finding for not naming."""
    return {**inspected.finding(slug, paths, marker),
            "workflow": WORKFLOW["name"]}


FIXED = finding("zzz-account-fixed", [CHANGED],
                "MARKER-FIXED the change itself is wrong")
GROUNDED = finding("zzz-account-grounded", [CHANGED],
                   "MARKER-GROUNDED the change is wrong another way")
SILENT = finding("zzz-account-silent", [CHANGED],
                 "MARKER-SILENT the change is wrong a third way")
DISCARDED = finding("zzz-account-discarded", [CHANGED],
                    "MARKER-DISCARDED the change has a typo")
BESIDE = finding("zzz-account-beside", [BESIDE_THE_CHANGE],
                 "MARKER-BESIDE the file beside it was always wrong")

#: The two a spent allowance abandons, one per allowance.
FOR_CORRECTION = finding("zzz-account-for-correction", [CHANGED],
                         "MARKER-FOR-CORRECTION the wording is stale")
FOR_REPAIR = finding("zzz-account-for-repair", [CHANGED],
                     "MARKER-FOR-REPAIR the helper misses a case")

#: Two readings of the change on two attempts, so the retried run's first
#: attempt and its second are about different findings and the account can say
#: which of them ended where.
FIRST_LOOK = finding("zzz-account-first-look", [CHANGED],
                     "MARKER-FIRST-LOOK the change is wrong")
SECOND_LOOK = finding("zzz-account-second-look", [CHANGED],
                      "MARKER-SECOND-LOOK the change is still wrong")

#: What a verdict says when it declines a finding, and when it judges one not
#: worth filing. Distinctive, so a search of a report is looking for *these*
#: words, and free of digits, so the assertion that the account states no count
#: is about the account rather than about a ground that happens to carry one.
GROUND = "MARKER-GROUND the fix is a design decision the plan did not make"
DROP_WORDS = "MARKER-DROP a one-word typo nobody should plan a story for"


def declined(found: dict, *, reason: str = GROUND, dropped: str = "") -> dict:
    """A `declined_findings` entry for `found`, optionally judging it not worth
    filing."""
    entry = {"slug": found["slug"], "reason": reason}
    if dropped:
        entry["not_worth_filing"] = dropped
    return entry


def correcting(found: dict) -> dict:
    """A correctable entry naming `found`'s slug — the verdict acting on it."""
    return inspected.naming_the_slug(found)


def repairing_entry(found: dict) -> dict:
    """A repairable entry naming `found`'s slug, likewise."""
    return inspected.naming_the_slug(found, inspected.REPAIRABLE_FINDING)


def passing(*, correctable=(), repairable=(), declining=()) -> dict:
    """A passing verdict carrying exactly what the caller asked for, so a
    verdict that names no correctable finding carries no such key at all and the
    coordinator's correction block falls through as it does for a real one."""
    verdict = dict(inspected.PASS)
    if correctable:
        verdict["correctable_findings"] = [dict(one) for one in correctable]
    if repairable:
        verdict["repairable_findings"] = [dict(one) for one in repairable]
    if declining:
        verdict["declined_findings"] = [dict(one) for one in declining]
    return verdict


# ==========================================================================
# Reading the account back out of the report the run left
# ==========================================================================


#: How the account opens each disposition's clause. A section a developer reads
#: is prose, so what it says is what is asserted; every reading below derives
#: from these rather than searching for a slug anywhere in the report.
CLAUSES = {
    "repaired": "Repaired or corrected within the run: ",
    "backlog": "Sent to the backlog: ",
    "discarded": "Discarded as not worth filing: ",
    "abandoned": ("Abandoned because a pass allowance was spent, and sent to "
                  "the backlog with the rest: "),
}

#: The three sentences the account writes instead of naming findings.
NOTHING_TO_ROUTE = "found nothing to route to the story"
NOT_KNOWN_YET = "is not known when this report is written"
COULD_NOT_BE_DERIVED = "could not be derived"

#: What the closing sentence points at instead of a count of briefs filed.
WHERE_THE_OUTCOME_IS = ("events.log", "inspection log", "committed history")


def report_of(run) -> str:
    return (run.run_dir / COMPLETION_REPORT).read_text(encoding="utf-8")


def account_section(report: str) -> str:
    """The whole of the account as the report carries it — heading, body and the
    blank line that closes it — so removing it from a report leaves exactly what
    a report carrying no account carries.

    Bounded by the next `##` heading rather than by that heading's name, so the
    slice moves with the report instead of going quietly wrong when a section is
    added after it.
    """
    marker = f"{FINDINGS_HEADING}\n"
    if marker not in report:
        return ""
    start = report.index(marker)
    following = report.index("\n## ", start + len(marker))
    return report[start:following + 1]


def account_body(report: str) -> list[str]:
    """The account's paragraphs, the heading dropped."""
    section = account_section(report)
    if not section:
        return []
    return [one for one in section.split("\n", 1)[1].split("\n\n") if one.strip()]


def named_in(report: str, clause: str) -> dict[str, str]:
    """Each finding one clause of the account names, as slug to the ground the
    clause states for it — read back out of the rendered prose, so what is
    asserted is what a developer reads."""
    opening = CLAUSES[clause]
    paragraph = next((one for one in account_body(report)
                      if one.startswith(opening)), "")
    if not paragraph:
        return {}
    named: dict[str, str] = {}
    for part in paragraph[len(opening):].strip().removesuffix(".").split("; "):
        slug, _, ground = part.partition(" (")
        named[slug] = ground.removesuffix(")")
    return named


def dispositions(report: str) -> set[str]:
    """Which dispositions the account names findings under. An equality against
    this is what says the account names no disposition its run did not reach."""
    return {name for name, opening in CLAUSES.items()
            if any(one.startswith(opening) for one in account_body(report))}


def markers_in(run, *found: dict) -> list[str]:
    """The marker text of each of `found` that the run's queue holds as the run
    left it — read off the entries themselves, so what is asserted is that the
    brief carrying that finding's own words reached the queue."""
    entries = [json.dumps(entry) for entry in run.queue]
    return sorted(one["body"] for one in found
                  if any(one["body"] in entry for entry in entries))


def abandonment_records(run) -> list[dict]:
    """The cross-run inspection log's records of what the run abandoned, told
    apart from the three kinds beside them by the field they carry rather than
    by prose — which is how the log's own declaration says they are told
    apart, the event kind not being projected."""
    return [one for one in run.log_records if ABANDONED in one]


# ==========================================================================
# A completed run accounts for every finding it made
# ==========================================================================


@pytest.fixture
def every_disposition(target, harness, tmp_path, filings):
    """One run reaching every disposition a run reaches without spending an
    allowance.

    Five findings: one the verdict acts on by naming its slug for correction,
    one it declines with a ground of its own, one it says nothing about, one it
    judges not worth filing, and one its inspection made about the file beside
    the change, which was never put to the verdict at all. The correction pass
    the first one routes returns a verdict carrying nothing, and the run
    completes.
    """
    run = inspected.drive(
        target, harness, tmp_path,
        findings=[FIXED, GROUNDED, SILENT, DISCARDED, BESIDE],
        verdicts=[passing(correctable=[correcting(FIXED)],
                          declining=[declined(GROUNDED),
                                     declined(DISCARDED, dropped=DROP_WORDS)]),
                  inspected.PASS],
        name="every-disposition")
    assert run.code == 0, run.messages
    assert run.state["status"] == "completed"
    return run


def test_the_account_names_every_finding_the_run_made_and_what_became_of_it(
        every_disposition):
    """The first criterion, end to end. Every finding the inspection made is
    named in the account — the four it routed to the story and the one it made
    about the file beside the change — each exactly once, and each under the
    disposition the run reached for it."""
    report = report_of(every_disposition)
    assert FINDINGS_HEADING in report

    assert set(named_in(report, "repaired")) == {FIXED["slug"]}
    assert set(named_in(report, "backlog")) == {
        GROUNDED["slug"], SILENT["slug"], BESIDE["slug"]}
    assert set(named_in(report, "discarded")) == {DISCARDED["slug"]}
    assert named_in(report, "abandoned") == {}

    # Once each, across the clauses: a finding named under two dispositions
    # would be one finding accounted for twice.
    everywhere = [slug for clause in CLAUSES
                  for slug in named_in(report, clause)]
    assert sorted(everywhere) == sorted(set(everywhere))
    assert set(everywhere) == {one["slug"] for one in
                              (FIXED, GROUNDED, SILENT, DISCARDED, BESIDE)}


def test_the_account_carries_the_ground_the_verdict_recorded_or_says_it_gave_none(
        every_disposition):
    """The third criterion. A finding being sent on is accounted with the
    verdict's own words where it gave them, with the record that it gave none
    where it did not, and with the fact that it was never put to the verdict
    where the inspection made it about a file beside the change."""
    backlog = named_in(report_of(every_disposition), "backlog")
    assert backlog[GROUNDED["slug"]] == GROUND
    assert backlog[SILENT["slug"]] == "the verdict recorded no ground"
    assert backlog[BESIDE["slug"]] == "about a file beside the change"
    # The discarded one carries the verdict's own words for discarding it.
    assert named_in(report_of(every_disposition), "discarded") == {
        DISCARDED["slug"]: DROP_WORDS}
    # And the one the run fixed carries no ground at all: it was not left, so
    # there is no reason for leaving it to state.
    assert named_in(report_of(every_disposition), "repaired") == {
        FIXED["slug"]: ""}


def test_the_account_agrees_with_the_artifacts_the_run_directory_holds(
        every_disposition):
    """The account is read off the records the run already wrote rather than
    split a second time for the report: every part of it is compared against
    the accounting artifact and the held record of the same attempt, which are
    what the filing at completion reads."""
    report = report_of(every_disposition)
    recorded = inspected.accounting(every_disposition)
    held = inspected.held_record(every_disposition)

    assert set(named_in(report, "repaired")) == set(recorded["acted_on_slugs"])
    assert set(named_in(report, "discarded")) == set(recorded["dropped_slugs"])
    left = set(recorded["remainder_slugs"]) - set(recorded["dropped_slugs"])
    beside = {one["slug"] for one in held["findings"]}
    assert set(named_in(report, "backlog")) == left | beside

    # And the grounds are the ones the accounting recorded, entry by entry,
    # rather than a second reading of the verdict the run no longer holds.
    grounds = {one["slug"]: one.get("reason", "")
               for one in recorded["declined"]}
    for slug, ground in named_in(report, "backlog").items():
        if slug in beside:
            continue
        assert ground == (grounds[slug] or "the verdict recorded no ground")


def test_the_report_and_the_filing_agree_about_what_is_being_sent_on(
        every_disposition, filings):
    """What the account says is going to the backlog is what the filing at
    completion hands to the one call every producer files through — asserted
    against the slugs that call was given rather than against the queue, which
    cannot say how many times a finding was offered."""
    report = report_of(every_disposition)
    offered = sorted(slug for call in filings for slug in call)
    assert offered == sorted(named_in(report, "backlog"))
    # Nothing the account named under another disposition was offered.
    for clause in ("repaired", "discarded"):
        for slug in named_in(report, clause):
            assert slug not in offered, (clause, slug)


def test_the_report_says_where_the_filings_outcome_is_recorded_and_not_what_it_was(
        every_disposition):
    """The fourth criterion. The report is written and committed before the
    filing runs, so the account states no count of what reached the queue; it
    says where that outcome is recorded instead.

    The control is the completion event of the same run, which does carry a
    count — so the absence here is the report declining to claim something it
    cannot know rather than a run in which nothing counted anything.
    """
    report = report_of(every_disposition)
    section = account_section(report)
    assert NOT_KNOWN_YET in section
    for where in WHERE_THE_OUTCOME_IS:
        assert where in section, where
    # No count of briefs: the number of findings being sent on appears nowhere
    # in the section, and neither does any other digit, every ground this run's
    # verdict recorded being free of them.
    assert not any(character.isdigit() for character in section), section

    (filed,) = inspected.filed_events(every_disposition)
    assert filed["filed"] == len(named_in(report, "backlog"))


def test_a_run_that_abandoned_nothing_writes_no_abandonment_anywhere(
        every_disposition):
    """The ninth criterion, and the control in the other direction for both
    abandonment readings below: this run spent no allowance, so the account
    carries no abandonment clause, the cross-run history carries no abandonment
    record, and the run's own history carries no entry with the count."""
    report = report_of(every_disposition)
    assert CLAUSES["abandoned"] not in report
    assert abandonment_records(every_disposition) == []
    assert [one for one in story_coordinator.load_history(
        every_disposition.run_dir) if ABANDONED in one] == []
    assert dispositions(report) == {"repaired", "backlog", "discarded"}


# ==========================================================================
# What a spent allowance abandons
# ==========================================================================


@pytest.fixture
def both_allowances_spent(target, harness, tmp_path, filings):
    """A run that meets both allowances, each on the verdict its own pass
    returned to — the amending path, where the accounting for the attempt is
    already written and a later verdict of the same attempt is what discovers
    the allowance is gone.

    The first verdict asks for one routed finding to be corrected and another to
    be repaired, so both are recorded as acted on, and the correction pass it
    routes spends that allowance. The verdict the pass returns to asks for both
    again: the correction allowance is now spent, so that finding is abandoned,
    and the repair pass routes on the same verdict and spends the other
    allowance. The verdict *it* returns to asks for the repair again, meets the
    spent allowance, and the second finding is abandoned too.
    """
    asking_for_both = passing(correctable=[correcting(FOR_CORRECTION)],
                              repairable=[repairing_entry(FOR_REPAIR)])
    run = inspected.drive(
        target, harness, tmp_path,
        findings=[FOR_CORRECTION, FOR_REPAIR],
        verdicts=[asking_for_both, asking_for_both,
                  passing(repairable=[repairing_entry(FOR_REPAIR)])],
        name="both-allowances-spent")
    assert run.code == 0
    assert run.state["status"] == "completed"
    # The premise: each allowance was spent once and met once.
    assert run.state["correction_pass_count"] == ALLOWANCE
    assert run.state["repair_pass_count"] == ALLOWANCE
    return run


def test_a_finding_a_spent_allowance_abandoned_is_recorded_nowhere_as_acted_on(
        both_allowances_spent):
    """The fifth criterion's first half. The verdict named both slugs for a pass
    and neither pass could be run, so the accounting records neither as acted
    on: both are in the remainder, each marked as abandoned and carrying the
    allowance that ran out as the ground it was left with."""
    recorded = inspected.accounting(both_allowances_spent)
    assert recorded["acted_on_slugs"] == []
    assert recorded["acted_on"] == 0
    assert sorted(recorded["remainder_slugs"]) == sorted(
        [FOR_CORRECTION["slug"], FOR_REPAIR["slug"]])
    assert sorted(recorded["abandoned_slugs"]) == sorted(
        recorded["remainder_slugs"])
    assert recorded["abandoned_by_spent_allowance"] == 2
    entries = {one["slug"]: one for one in recorded["declined"]}
    for slug in recorded["remainder_slugs"]:
        assert entries[slug][ABANDONED] is True, slug
        assert entries[slug]["abandoned_because"], slug
    # The two grounds are each about their own allowance, so a reader is told
    # which one ran out rather than only that one did.
    assert entries[FOR_CORRECTION["slug"]]["abandoned_because"] != \
        entries[FOR_REPAIR["slug"]]["abandoned_because"]
    for slug in recorded["remainder_slugs"]:
        assert str(ALLOWANCE) in entries[slug]["abandoned_because"], slug


def test_an_abandoned_finding_is_filed_at_completion_like_every_other(
        both_allowances_spent, filings):
    """The fifth criterion's second half. Nothing about the filing is special:
    both abandoned findings reach the one call every producer files through, in
    one call, and both are in the queue the run left — which is what puts them
    under the same severity floor, the same brief cap and the same duplicate
    detection as every other brief."""
    offered = [sorted(call) for call in filings]
    assert offered == [sorted([FOR_CORRECTION["slug"], FOR_REPAIR["slug"]])]
    assert markers_in(both_allowances_spent, FOR_CORRECTION, FOR_REPAIR) == \
        sorted([FOR_CORRECTION["body"], FOR_REPAIR["body"]])
    (filed,) = inspected.filed_events(both_allowances_spent)
    assert filed["filed"] == 2
    assert filed["dropped"] == 0


def test_the_account_names_each_abandoned_finding_with_the_allowance_that_ran_out(
        both_allowances_spent):
    """The second criterion for the fourth disposition. Each abandoned finding
    is named individually under its own clause, with the ground the accounting
    recorded, and the account names no other disposition — this run repaired
    nothing, sent nothing on for any other reason and discarded nothing."""
    report = report_of(both_allowances_spent)
    named = named_in(report, "abandoned")
    recorded = {one["slug"]: one.get("abandoned_because", "")
                for one in inspected.accounting(both_allowances_spent)["declined"]}
    assert set(named) == {FOR_CORRECTION["slug"], FOR_REPAIR["slug"]}
    for slug, ground in named.items():
        assert ground == recorded[slug], slug
    assert dispositions(report) == {"abandoned"}
    # The clause says they are being sent on rather than lost, which is what
    # the correction to the false record bought.
    assert "sent to the backlog" in CLAUSES["abandoned"]
    assert NOT_KNOWN_YET in account_section(report)


def test_the_committed_history_records_how_many_the_run_abandoned(
        both_allowances_spent):
    """The sixth criterion. One record per spent allowance reaches the story's
    own segment of the committed cross-run history, each naming how many were
    abandoned, and each is told apart from the three kinds of record in that log
    by the fields it carries: the inspection's own carries its invocations, the
    accounting's what was routed, the filing's what was filed, and this one the
    count and none of those."""
    records = abandonment_records(both_allowances_spent)
    assert [one[ABANDONED] for one in records] == [1, 1]
    for record in records:
        assert record["story_id"] == STORY_ID
        for absent in ("routed", "acted_on", "invocations", "mode",
                       "dedupe_ran", "filed", "dropped"):
            assert absent not in record, record
    # And the kinds beside it in the same log carry what it does not, which is
    # what makes "told apart by the fields" a statement about this log rather
    # than about a log holding one kind of line.
    others = [one for one in both_allowances_spent.log_records
              if ABANDONED not in one]
    assert others, "this run wrote no other kind of record to compare against"
    for record in others:
        assert ABANDONED not in record
    assert schema_validator.validate(
        both_allowances_spent.log_records,
        CROSS_RUN_SCHEMA["properties"][inspected.INSPECTION_LOG]) == []
    # The run's own history says the same thing and still validates, so the
    # field the count travels in is one the execution history declares.
    history = story_coordinator.load_history(both_allowances_spent.run_dir)
    assert [one[ABANDONED] for one in history if ABANDONED in one] == [1, 1]
    assert schema_validator.validate(history, HISTORY_SCHEMA) == []


def test_the_abandonment_record_outlives_the_run_directory(
        both_allowances_spent):
    """The seventh criterion. The run directory is gitignored and the story's
    cleanup deletes it, so the record is read again with it gone — off the paths
    the writer-side resolver names, in the tree the run worked in, and from a
    commit the run made rather than from an uncommitted file."""
    run = both_allowances_spent
    recorded = story_inspection.record_paths(run.tree, {}, STORY_ID)
    assert recorded, "this target routes the kind to no log at all"
    logs = [one for one in recorded if Path(one).name == inspected.INSPECTION_LOG]
    assert logs, recorded
    committed = inspected._git(run.tree, "log", "--name-only", "--format=",
                               ).stdout.split()
    assert set(logs) <= set(committed), (logs, committed)
    assert inspected._git(run.tree, "status", "--porcelain").stdout.strip() == ""

    before = abandonment_records(run)
    assert before
    shutil.rmtree(run.run_dir)
    assert not run.run_dir.exists()
    assert abandonment_records(run) == before


@pytest.fixture
def retried_harness(tmp_path) -> Path:
    """A second harness root carrying the same definition, for the retried run
    below.

    A run is refused where its story already ended, so two fixtures that each
    drive a whole run cannot share a target — the second meets the refusal at
    the run the first left completed. The pair below therefore stands on ground
    of its own, exactly as `checked_target` and `checked_harness` do, so a test
    can hold one run's accounting beside the other's. The definition and the
    rules are the ones beside it, so the two runs differ in where they happened
    and in nothing else.
    """
    return inspected.materialize(WORKFLOW, tmp_path / "retried-harness",
                                 rules=filed_once.RULES)


@pytest.fixture
def retried_target(tmp_path) -> Path:
    return inspected.build_target(tmp_path / "retried-target",
                                  workflow=WORKFLOW["name"])


@pytest.fixture
def allowance_already_spent(retried_target, retried_harness, tmp_path, filings):
    """A run that meets an allowance already spent on the *first* verdict of a
    later attempt — the writing path, where the accounting for the attempt does
    not exist yet and the split is made with the abandonment already known.

    The allowance is cumulative across attempts, which is what makes this
    reachable: the first attempt's verdict asks for its own finding to be
    corrected and spends the pass, the verdict the pass returns to fails and the
    run is retried, and the second attempt's inspection reads the change again
    and finds a different defect. The verdict on that attempt asks for it to be
    corrected too and there is nothing left to correct with.
    """
    run = filed_once.drive(
        retried_target, retried_harness, tmp_path,
        per_attempt=[[FIRST_LOOK], [SECOND_LOOK]],
        verdicts=[passing(correctable=[correcting(FIRST_LOOK)]),
                  inspected.failing_into(RETRY_CATEGORY),
                  passing(correctable=[correcting(SECOND_LOOK)])],
        name="allowance-already-spent")
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.state["retry_count"] == 1
    assert run.state["correction_pass_count"] == ALLOWANCE
    return run


def test_an_allowance_spent_on_an_earlier_attempt_abandons_the_later_finding(
        allowance_already_spent, filings):
    """The writing path of the accounting, driven as a run. The second
    attempt's verdict named its routed finding for a correction and no pass
    could be run, so that attempt's accounting — written for the first time on
    that verdict rather than amended — records it as left and abandoned, and the
    filing sends it on."""
    run = allowance_already_spent
    later = inspected.accounting(run, 2)
    assert later["acted_on_slugs"] == []
    assert later["remainder_slugs"] == [SECOND_LOOK["slug"]]
    assert later["abandoned_slugs"] == [SECOND_LOOK["slug"]]
    assert later["abandoned_by_spent_allowance"] == 1
    # The earlier attempt's own accounting is untouched by any of it: its
    # verdict did act on its finding, and a pass was run for it.
    earlier = inspected.accounting(run, 1)
    assert earlier["acted_on_slugs"] == [FIRST_LOOK["slug"]]
    assert earlier["abandoned_slugs"] == []
    assert [slug for call in filings for slug in call] == [SECOND_LOOK["slug"]]


def test_the_account_of_a_retried_run_says_which_attempts_finding_ended_where(
        allowance_already_spent):
    """The account reads every attempt the filing reads, so a finding an earlier
    attempt's verdict acted on is named as repaired and the later one a spent
    allowance abandoned is named as abandoned — two dispositions and no
    others."""
    report = report_of(allowance_already_spent)
    assert named_in(report, "repaired") == {FIRST_LOOK["slug"]: ""}
    assert set(named_in(report, "abandoned")) == {SECOND_LOOK["slug"]}
    assert dispositions(report) == {"repaired", "abandoned"}
    assert abandonment_records(allowance_already_spent)[0][ABANDONED] == 1


# ==========================================================================
# One abandonment, recorded the same way whichever path recorded it
#
# The accounting is written on the first verdict of an attempt and amended by a
# later call about the same attempt, and a spent allowance can be met at either
# moment. The two paths are different code, and a finding abandoned by one has
# to be indistinguishable from the same finding abandoned by the other: the run
# counts how many of its left findings were declined with a reason and how many
# without, and a field carrying the verifier's own words must never hold words
# the verifier did not write.
# ==========================================================================


def declined_entry(recorded: dict, slug: str) -> dict:
    """One accounting `declined` entry, by the slug it is about."""
    return next(one for one in recorded["declined"] if one["slug"] == slug)


def test_both_accounting_paths_record_an_abandoned_finding_the_same_way(
        both_allowances_spent, allowance_already_spent):
    """One routed finding abandoned by a spent allowance the verdict declined
    by nothing, recorded on each path, compared field for field.

    The amending path is the run above that met each allowance on the verdict
    its own pass returned to; the writing path is the retried run that met one
    already spent on the first verdict of a later attempt. What refused the
    pass was the run's allowance rather than the verdict, so on both the entry
    records no reason and says none was given, and the allowance's own words
    are in the marker beside it — which is what keeps the run's two counts of
    how its left findings were declined meaning one thing however the allowance
    was met.

    The control is the run below, where the verdict *did* decline the abandoned
    finding with words of its own: the same path then records a reason and says
    one was given, so the agreement here is the two paths agreeing rather than
    a field that is always false.
    """
    amended = declined_entry(inspected.accounting(both_allowances_spent),
                             FOR_CORRECTION["slug"])
    written = declined_entry(inspected.accounting(allowance_already_spent, 2),
                             SECOND_LOOK["slug"])

    assert set(amended) == set(written)
    assert amended["reason_given"] is False
    assert written["reason_given"] is False
    assert "reason" not in amended
    assert "reason" not in written
    assert amended["dropped"] is written["dropped"] is False
    assert amended[ABANDONED] is written[ABANDONED] is True
    for entry in (amended, written):
        assert str(ALLOWANCE) in entry["abandoned_because"], entry

    # And the counts the boolean feeds say the same thing on both: neither run
    # recorded its abandoned finding as declined with a reason.
    for recorded in (inspected.accounting(both_allowances_spent),
                     inspected.accounting(allowance_already_spent, 2)):
        assert recorded["declined_with_reason"] == 0
        assert recorded["declined_without_reason"] == len(recorded["declined"])


@pytest.fixture
def abandoned_and_declined(target, harness, tmp_path, filings):
    """A run whose spent correction allowance abandons a routed finding the
    verdict also declined by name, with a ground of its own.

    The first verdict asks for the finding to be corrected and spends the
    allowance; the verdict the pass returns to asks again and declines it in
    the same breath, which is a verifier saying both why it wants the fix and
    why it is content to leave it. The allowance is gone either way, so the
    finding is abandoned and the entry has both things to record.
    """
    asking = passing(correctable=[correcting(FOR_CORRECTION)])
    run = inspected.drive(
        target, harness, tmp_path, findings=[FOR_CORRECTION],
        verdicts=[asking,
                  passing(correctable=[correcting(FOR_CORRECTION)],
                          declining=[declined(FOR_CORRECTION)])],
        name="abandoned-and-declined")
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.state["correction_pass_count"] == ALLOWANCE
    return run


def test_an_abandoned_finding_the_verdict_declined_records_both_grounds(
        abandoned_and_declined):
    """The control for the agreement above, and the rule it protects: the
    verdict's own words go in the reason, the allowance's in the marker beside
    it, and neither holds the other's. The report reads the allowance, because
    what a reader wants to know about a finding nothing could be run for is
    that nothing could be run for it."""
    entry = declined_entry(inspected.accounting(abandoned_and_declined),
                           FOR_CORRECTION["slug"])
    assert entry["reason_given"] is True
    assert entry["reason"] == GROUND
    assert entry[ABANDONED] is True
    assert str(ALLOWANCE) in entry["abandoned_because"]
    assert GROUND not in entry["abandoned_because"]
    assert named_in(report_of(abandoned_and_declined), "abandoned") == {
        FOR_CORRECTION["slug"]: entry["abandoned_because"]}


# ==========================================================================
# A pass the run never reached abandons nothing
#
# Two blocks decide what a spent allowance leaves behind, and the clean-clone
# check sits between them: a verdict whose check fails takes the retry route
# before the repair block is reached, and a verdict the correction block routes
# on defers its repairable findings to the verification that pass returns to.
# In both, the repair pass's allowance is never met, so nothing may be recorded
# as abandoned by it — recording one would be exactly the false record this
# story exists to remove, written the other way round.
# ==========================================================================


@pytest.fixture
def checked_harness(tmp_path) -> Path:
    """A harness root carrying the definition with the check declared, under
    the same retry ceiling the harness beside it is held to."""
    return inspected.materialize(CHECKED_WORKFLOW, tmp_path / "checked-harness",
                                 rules=filed_once.RULES)


@pytest.fixture
def checked_target(tmp_path) -> Path:
    return inspected.build_target(tmp_path / "checked-target",
                                  workflow=CHECKED_WORKFLOW["name"])


def asking_for_a_repair() -> dict:
    """The verdict both runs below meet the repair allowance on: passing, and
    naming the routed finding for a repair."""
    return passing(repairable=[repairing_entry(FOR_REPAIR)])


def failing_the_check_on(monkeypatch, which: int) -> list:
    """Make the `which`-th clean-clone check of a run report a failure while
    the real check still runs underneath.

    The substitution `tests/test_suite_run_shadow.py` makes for the same seam:
    the real check builds its clone, runs the suite and writes its record, and
    the only thing altered is the exit code the routing reads. Returns the list
    the real results are appended to, so a test can say what the suite really
    did and how many checks the run made.
    """
    real = story_coordinator.clean_clone_check
    ran: list = []

    def substituting(*args, **keywords):
        result = real(*args, **keywords)
        ran.append(result)
        if len(ran) == which:
            return dataclasses.replace(result, exit_code=1)
        return result

    monkeypatch.setattr(story_coordinator, "clean_clone_check", substituting)
    return ran


@pytest.fixture
def the_check_failed(checked_target, checked_harness, tmp_path, filings,
                     monkeypatch):
    """A run whose repair allowance is spent and whose clean-clone check fails
    on the verdict that would have met it.

    The first verdict asks for the routed finding to be repaired and the pass
    routes, spending the allowance. The verdict that pass returns to asks
    again, and the check on it fails — so the run takes the retry route above
    the repair block and never reaches the block where the spent allowance is
    met. Its second attempt passes plainly and the run completes.
    """
    checks = failing_the_check_on(monkeypatch, 2)
    run = inspected.drive(
        checked_target, checked_harness, tmp_path, findings=[FOR_REPAIR],
        verdicts=[asking_for_a_repair(), asking_for_a_repair(),
                  inspected.PASS],
        name="the-check-failed")
    assert run.code == 0
    assert run.state["status"] == "completed"
    # The premises: the allowance really was spent, the run really was sent
    # back, and the suite the real check ran was green every time — so what
    # routed the verdict away is the substituted exit code and not a red tree.
    assert run.state["repair_pass_count"] == ALLOWANCE
    assert run.state["retry_count"] == 1
    assert [one.ran for one in checks] == [True, True, True]
    assert [one.exit_code for one in checks] == [0, 0, 0]
    return run


def test_a_verdict_whose_check_failed_abandons_nothing(the_check_failed):
    """The verdict named a routed finding for a repair with that allowance
    already spent, and the check between the two blocks routed it away — so the
    run never reached the block that meets the allowance, and nothing anywhere
    records the finding as abandoned by a pass the run did not reach."""
    run = the_check_failed
    recorded = inspected.accounting(run, 1)
    assert recorded["abandoned_slugs"] == []
    assert recorded["abandoned_by_spent_allowance"] == 0
    assert [one for one in recorded["declined"] if one[ABANDONED]] == []
    assert [one for one in story_coordinator.load_history(run.run_dir)
            if ABANDONED in one] == []
    assert abandonment_records(run) == []
    assert named_in(report_of(run), "abandoned") == {}
    # And the route it took instead, so the silence above is the failed check
    # deciding: the check failed, and no spent-allowance record was written.
    kinds = [one["event"] for one in story_coordinator.load_history(run.run_dir)]
    assert CLEAN_CLONE_FAILED in kinds
    assert REPAIR_RECORDED not in kinds
    # The finding is not lost for it: the later attempt's verdict left it, and
    # the account says it is going to the backlog like any other left finding.
    assert set(named_in(report_of(run), "backlog")) == {FOR_REPAIR["slug"]}
    assert dispositions(report_of(run)) == {"backlog"}


@pytest.fixture
def the_check_passed(checked_target, checked_harness, tmp_path, filings):
    """The control for the run above: the same definition, the same finding and
    the same two verdicts, with nothing substituted — so the check passes on
    the second verdict too, the run reaches the repair block, and the allowance
    it meets there is met."""
    run = inspected.drive(
        checked_target, checked_harness, tmp_path, findings=[FOR_REPAIR],
        verdicts=[asking_for_a_repair(), asking_for_a_repair()],
        name="the-check-passed")
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.state["repair_pass_count"] == ALLOWANCE
    assert run.state["retry_count"] == 0
    return run


def test_the_same_verdict_with_the_check_passing_does_record_the_abandonment(
        the_check_passed, filings):
    """The control in the other direction. Identical to the run above in every
    respect but the exit code the routing read, and here the repair block is
    reached: the allowance it meets is recorded, the accounting moves the
    finding out of acted on and marks it abandoned, the count reaches the
    committed history and the account names it. So the silence beside this is
    the failed check's doing rather than a run in which nothing could have been
    abandoned."""
    run = the_check_passed
    recorded = inspected.accounting(run, 1)
    assert recorded["acted_on_slugs"] == []
    assert recorded["abandoned_slugs"] == [FOR_REPAIR["slug"]]
    assert declined_entry(recorded, FOR_REPAIR["slug"])[ABANDONED] is True
    (record,) = abandonment_records(run)
    assert record[ABANDONED] == 1
    assert set(named_in(report_of(run), "abandoned")) == {FOR_REPAIR["slug"]}
    assert [slug for call in filings for slug in call] == [FOR_REPAIR["slug"]]
    kinds = [one["event"] for one in story_coordinator.load_history(run.run_dir)]
    assert REPAIR_RECORDED in kinds
    assert CLEAN_CLONE_FAILED not in kinds


@pytest.fixture
def the_correction_pass_routed(target, harness, tmp_path, filings):
    """A run whose spent repair allowance is named again on a verdict the
    correction block routes on.

    The first verdict asks for one routed finding to be repaired and the pass
    routes, spending that allowance. The verdict it returns to asks for the
    repair again and asks for a second routed finding to be corrected, and the
    correction allowance is untouched — so the correction block routes and the
    run leaves the verdict there. The repairable finding is deferred to the
    verification that pass returns to rather than left, and the block that
    would meet the spent repair allowance is never reached on this verdict
    either. The verdict the correction pass returns to carries nothing and the
    run completes.
    """
    run = inspected.drive(
        target, harness, tmp_path, findings=[FOR_REPAIR, FOR_CORRECTION],
        verdicts=[asking_for_a_repair(),
                  passing(correctable=[correcting(FOR_CORRECTION)],
                          repairable=[repairing_entry(FOR_REPAIR)]),
                  inspected.PASS],
        name="the-correction-pass-routed")
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.state["repair_pass_count"] == ALLOWANCE
    assert run.state["correction_pass_count"] == ALLOWANCE
    return run


def test_a_verdict_the_correction_pass_routes_abandons_no_repairable_finding(
        the_correction_pass_routed, the_check_passed):
    """The deferral, held by driving a run because the predicate no longer
    states it.

    Which block a run reaches is the run's business: the correction block
    routes and the repair block below it is not reached, so a verdict naming a
    repairable finding with that allowance spent leaves nothing recorded as
    abandoned by it. The finding was deferred rather than left, and a record
    saying a spent allowance abandoned it would be untrue of a pass the run
    never asked for.

    The control is the run beside it, where the same spent repair allowance is
    met on a verdict no pass routed away from: there the block is reached and
    the abandonment is recorded, so the absence here is the routing deciding.
    """
    run = the_correction_pass_routed
    recorded = inspected.accounting(run, 1)
    assert recorded["abandoned_slugs"] == []
    assert recorded["acted_on_slugs"] == [FOR_REPAIR["slug"]]
    assert abandonment_records(run) == []
    assert named_in(report_of(run), "abandoned") == {}
    kinds = [one["event"] for one in story_coordinator.load_history(run.run_dir)]
    assert CORRECTION_ROUTED in kinds
    assert REPAIR_RECORDED not in kinds

    assert abandonment_records(the_check_passed) != []
    assert inspected.accounting(the_check_passed, 1)["abandoned_slugs"] == \
        [FOR_REPAIR["slug"]]


# ==========================================================================
# The correction pass's own version of the same question
#
# The repair pass's abandonment is recorded at its block because the clean-clone
# check between the two blocks can route a verdict away before the block is
# reached. The correction pass's is recorded above the accounting instead, and
# what makes that safe is not that nothing can end the run in between — two
# refusals could, and each would have left a finding recorded as abandoned by a
# pass the run never reached, with no spent-budget event beside it. It is that
# both refusals are now made above the accounting as well, so nothing the run
# refuses about a verdict happens after the record of that verdict is written.
#
# Driven rather than read off the source, because where the refusals sit
# relative to the record is a property of what a run leaves behind: a reading of
# the code would go on passing over a third refusal added between them.
# ==========================================================================


#: A category the definition's routing table does not declare, and a path shaped
#: like the directory the changed file sits in. Either makes a verdict one the
#: routing cannot route, which is what stops a run before its correction block;
#: the directory is derived from the file rather than written, so a fixture that
#: moved the change would move with it.
UNDECLARED_CATEGORY = "zzz-account-no-such-category"
CHANGED_DIRECTORY = CHANGED.rsplit("/", 1)[0] + "/"

#: One row per shape a verdict can be unroutable in, each the conforming
#: correctable entry differing in the one field that shape is about.
UNROUTABLE = {
    "a category the table does not declare": {"category": UNDECLARED_CATEGORY},
    "a path shaped like a directory": {"path": CHANGED_DIRECTORY},
}


def test_the_unroutable_shapes_really_are_ones_this_definition_refuses():
    """The premise the pair below rests on: the category is one the definition
    does not declare, the path is a directory rather than a file, and the entry
    each is made from is one this definition routes when neither is applied."""
    assert UNDECLARED_CATEGORY not in ROUTES
    assert CHANGED_DIRECTORY.endswith("/")
    assert CHANGED.startswith(CHANGED_DIRECTORY)
    assert inspected.CORRECTABLE_FINDING["category"] in ROUTES
    for how in UNROUTABLE.values():
        assert story_coordinator._malformed_findings(
            [{**inspected.CORRECTABLE_FINDING, **how}], [], ROUTES) is not None
    assert story_coordinator._malformed_findings(
        [dict(inspected.CORRECTABLE_FINDING)], [], ROUTES) is None


def asking_again_beside(entry, target, harness, tmp_path, name):
    """A run whose correction allowance is spent by its first verdict and whose
    second asks for the same routed finding again beside `entry`.

    The first verdict names the routed finding for a correction and the pass
    routes, spending the allowance and leaving the attempt accounted with that
    finding acted on. The second names it again — so the correction block would
    meet the spent allowance and abandon it — and carries `entry` beside it,
    which is what decides whether the run reaches that block at all.
    """
    return inspected.drive(
        target, harness, tmp_path, findings=[FOR_CORRECTION],
        verdicts=[passing(correctable=[correcting(FOR_CORRECTION)]),
                  passing(correctable=[correcting(FOR_CORRECTION), entry])],
        name=name)


@pytest.mark.parametrize("shape", sorted(UNROUTABLE))
def test_a_verdict_the_routing_refuses_abandons_nothing(
        shape, target, harness, tmp_path, filings):
    """The refusal is made before the record, so the attempt's accounting says
    nothing about a pass the run never reached.

    The verdict passes, names the routed finding for a correction with that
    allowance already spent, and carries an entry the routing cannot route. The
    run escalates on the entry — and it escalates above the accounting, so the
    finding the first verdict acted on is still recorded as acted on, nothing is
    recorded as abandoned, and no spent-budget event was written for a block the
    run never entered.
    """
    run = asking_again_beside(
        {**inspected.CORRECTABLE_FINDING, **UNROUTABLE[shape]},
        target, harness, tmp_path, f"unroutable-{shape.replace(' ', '-')}")

    assert run.code == 2
    assert run.state["status"] == "escalated"
    recorded = inspected.accounting(run, 1)
    assert recorded["abandoned_slugs"] == []
    assert recorded["abandoned_by_spent_allowance"] == 0
    assert [one for one in recorded["declined"] if one[ABANDONED]] == []
    # And the record the first verdict wrote is untouched: it acted on the
    # finding and a pass was run for it, which the escalation says nothing
    # about either way.
    assert recorded["acted_on_slugs"] == [FOR_CORRECTION["slug"]]
    kinds = [one["event"] for one in story_coordinator.load_history(run.run_dir)]
    assert CORRECTION_RECORDED not in kinds
    assert kinds[-1] == "escalated"
    # Nothing durable says the run abandoned anything, and an escalated run
    # leaves no completion report for an account to be rendered into.
    assert abandonment_records(run) == []
    assert not (run.run_dir / COMPLETION_REPORT).exists()


def test_the_same_verdict_the_routing_can_route_does_record_the_abandonment(
        target, harness, tmp_path, filings):
    """The control. The same two verdicts with the second entry's category one
    the definition declares: the run reaches the correction block, meets the
    spent allowance there and records the abandonment, so the silence above is
    the refusal deciding rather than a run in which nothing could have been
    abandoned.

    The count covers both entries the refused pass would have acted on where the
    accounting covers the routed one, which is the split the section below is
    about — asserted here so the control is about the whole record rather than
    about the one finding it shares with the case above.
    """
    run = asking_again_beside(dict(inspected.CORRECTABLE_FINDING),
                              target, harness, tmp_path, "routable-beside")

    assert run.code == 0
    assert run.state["status"] == "completed"
    recorded = inspected.accounting(run, 1)
    assert recorded["acted_on_slugs"] == []
    assert recorded["abandoned_slugs"] == [FOR_CORRECTION["slug"]]
    assert recorded["abandoned_by_spent_allowance"] == 1
    assert declined_entry(recorded, FOR_CORRECTION["slug"])[ABANDONED] is True
    kinds = [one["event"] for one in story_coordinator.load_history(run.run_dir)]
    assert CORRECTION_RECORDED in kinds
    (record,) = abandonment_records(run)
    assert record[ABANDONED] == 2
    assert set(named_in(report_of(run), "abandoned")) == {FOR_CORRECTION["slug"]}


# ==========================================================================
# An allowance nobody declared stops the run rather than reading as spent
#
# `pass_budget` answers zero for a budget that is absent or unusable, and every
# reader then reads that as an allowance with nothing left — so a definition
# defect would complete the run and record every finding the verdict named for
# a correction as abandoned by an allowance nobody ever declared. That is a run
# continuing in a degraded state, which this repository's coding standard says
# to fail loudly on instead. A *declared* zero is a different thing and stays
# accepted: it is the switch read at its other end, and what it records is true.
# ==========================================================================


#: Each way a declared correction budget can fail to be a count, and the one
#: way it can be a count that routes nothing. `True` is here because
#: `isinstance(True, int)` holds, so a bare integer test would read a declared
#: `true` as an allowance of one.
UNUSABLE_BUDGETS = {"absent": None, "a bool": True, "not an integer": "1",
                    "negative": -1}


def with_the_budget(definition: dict, budget) -> dict:
    """`definition` with its correction pass's budget set to `budget`, or with
    the key absent where `budget` is None — the shape a definition takes when
    nobody declared an allowance at all."""
    built = json.loads(json.dumps(definition))
    for stage in built["stages"]:
        if "correction_pass" not in stage:
            continue
        declaration = {**stage["correction_pass"]}
        if budget is None:
            declaration.pop("budget", None)
        else:
            declaration["budget"] = budget
        stage["correction_pass"] = declaration
    return built


@pytest.mark.parametrize("shape", sorted(UNUSABLE_BUDGETS))
def test_an_unusable_correction_budget_is_refused_at_pre_flight(shape):
    """Each unusable shape is reported, naming the value, so a developer is told
    what the declaration carries rather than only that something is wrong."""
    problems = story_coordinator.correction_pass_problems(
        with_the_budget(WORKFLOW, UNUSABLE_BUDGETS[shape])["stages"])
    assert len(problems) == 1
    assert repr(UNUSABLE_BUDGETS[shape]) in problems[0]


def test_a_declared_zero_is_accepted_where_an_absent_budget_is_not():
    """The control, and the asymmetry with the repair pass beside it. A declared
    zero says no pass may be routed, which is a setting a target can want and
    which the findings-abandoned record then reports truthfully; an absent one
    says nobody declared an allowance, and reading that as spent is the defect.
    The repair pass refuses zero, because there a zero is a declaration that
    should have been left out."""
    assert story_coordinator.correction_pass_problems(
        with_the_budget(WORKFLOW, 0)["stages"]) == []
    assert story_coordinator.correction_pass_problems(
        with_the_budget(WORKFLOW, ALLOWANCE)["stages"]) == []
    assert story_coordinator.correction_pass_problems(
        with_the_budget(WORKFLOW, None)["stages"]) != []
    assert story_coordinator.repair_pass_problems(
        WORKFLOW["stages"]) == []


def test_a_run_under_an_unusable_correction_budget_stops_before_any_stage(
        target, tmp_path, capsys):
    """Driven, because what the check is worth is what a run does with it: the
    run is refused before a stage is invoked and before a run directory exists,
    so the defect costs a refused run rather than the whole workflow it would
    otherwise have spent before recording an abandonment nothing could act on.
    """
    harness = inspected.materialize(with_the_budget(WORKFLOW, None),
                                    tmp_path / "no-budget-harness",
                                    rules=filed_once.RULES)
    runner = inspected.Runner(target, tmp_path / "no-budget.txt",
                              [inspected.PASS])

    code = story_coordinator.run_story(STORY_ID, harness, target, runner)

    assert code == 1
    assert "correction_pass" in capsys.readouterr().err
    assert runner.calls == []
    assert not conftest.run_dir_for(target, STORY_ID).exists()


#: The three that decide whether a declared pass budget is usable — the reading
#: every run is routed by, and the two pre-flights that refuse a declaration
#: carrying none. One predicate behind all three, so a definition refused under
#: one reading and admitted under another is not a state this coordinator can
#: reach; only what each makes of a declared *zero* is its own.
BUDGET_READERS = ("pass_budget", "correction_pass_problems",
                  "repair_pass_problems")
BUDGET_PREDICATE = "_is_a_pass_budget"


def calls_named(source: str, function: str, called: str) -> list:
    """Every call `function` makes to `called`, read off the coordinator's own
    source rather than off its behaviour, because what this is about is where
    the question is asked rather than what it answers today."""
    tree = ast.parse(conftest.function_source(source, function))
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == called]


def test_one_predicate_decides_whether_a_declared_budget_is_usable():
    """Each of the three asks the shared predicate and none asks the type of a
    declared budget itself.

    A second spelling of "is this a count" is a second answer that can come to
    disagree, and the disagreement is the expensive kind: a run refused for a
    budget another reader was happy with, or admitted for one nothing can
    spend. `isinstance` is what the predicate exists to do, so a reader making
    that call is a reader deciding for itself.
    """
    for name in BUDGET_READERS:
        assert calls_named(COORDINATOR_SOURCE, name, BUDGET_PREDICATE), name
        assert calls_named(COORDINATOR_SOURCE, name, "isinstance") == [], name


def test_the_same_reading_reports_a_reader_that_decides_for_itself():
    """The control: the shape this story replaced, over which the same reading
    finds no call to the predicate and two type tests of its own — so the
    silence above is the coordinator's rather than a reading that has stopped
    looking."""
    planted = (
        "def pass_budget(declaration):\n"
        "    budget = declaration.get('budget')\n"
        "    if isinstance(budget, bool) or not isinstance(budget, int):\n"
        "        return 0\n"
        "    return budget\n"
    )
    assert calls_named(planted, "pass_budget", BUDGET_PREDICATE) == []
    assert len(calls_named(planted, "pass_budget", "isinstance")) == 2


def test_the_same_harness_with_a_usable_budget_runs_the_whole_workflow(
        target, tmp_path, filings):
    """The control for the refusal above: the same definition, the same target
    and the same runner with the budget declared, so what stopped the run there
    is the declaration rather than anything else about the fixture."""
    run = inspected.drive(target,
                          inspected.materialize(with_the_budget(WORKFLOW,
                                                                ALLOWANCE),
                                                tmp_path / "budget-harness",
                                                rules=filed_once.RULES),
                          tmp_path, findings=[FOR_CORRECTION],
                          verdicts=[inspected.PASS], name="usable-budget")

    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.run_dir.is_dir()


# ==========================================================================
# What the count covers, and what the account covers
#
# The verifier may name a finding of its own in the same array as one the
# inspection routed to the story, and a spent allowance abandons both. The
# count in the committed history is over every finding the verdict named; the
# account, the accounting and the filing are about routed findings, because a
# finding nothing routed has no routed finding for them to be about. That is a
# gap rather than a defect in the count — a verifier's own finding reaches no
# backlog by any route — and what this holds is that the count says so and the
# account does not pretend otherwise.
# ==========================================================================


#: An entry the verifier raised itself: a correctable finding whose location
#: names no routed slug, which is the whole of what makes it the verifier's.
#: The sibling module's own entry, unchanged.
THE_VERIFIERS_OWN = inspected.CORRECTABLE_FINDING


@pytest.fixture
def a_verifiers_own_finding_abandoned(target, harness, tmp_path, filings):
    """A run whose spent correction allowance abandons two findings: one the
    inspection routed to the story, and one the verifier raised itself.

    Both verdicts name both. The first spends the allowance on a pass that
    routes; the second meets it spent, so the pass cannot be run for either and
    the record of the spent allowance counts both.
    """
    asking = passing(correctable=[correcting(FOR_CORRECTION),
                                  dict(THE_VERIFIERS_OWN)])
    run = inspected.drive(
        target, harness, tmp_path, findings=[FOR_CORRECTION],
        verdicts=[asking, asking], name="the-verifiers-own")
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.state["correction_pass_count"] == ALLOWANCE
    # The premise: the entry the verifier raised names no routed slug, so
    # nothing downstream can be about a routed finding for it.
    assert not story_inspection.names_a_slug(
        THE_VERIFIERS_OWN["location"], (FOR_CORRECTION["slug"],))
    return run


def test_the_count_covers_a_finding_the_account_has_no_routed_finding_for(
        a_verifiers_own_finding_abandoned, filings):
    """The count is over both; the account, the accounting and the filing carry
    the routed one. What records the verifier's own instead is the run's own
    event, which names it — and that, with the count, is the whole of it."""
    run = a_verifiers_own_finding_abandoned
    (record,) = abandonment_records(run)
    assert record[ABANDONED] == 2

    report = report_of(run)
    assert set(named_in(report, "abandoned")) == {FOR_CORRECTION["slug"]}
    assert THE_VERIFIERS_OWN["finding"] not in account_section(report)
    assert inspected.accounting(run)["abandoned_slugs"] == \
        [FOR_CORRECTION["slug"]]
    assert [slug for call in filings for slug in call] == \
        [FOR_CORRECTION["slug"]]

    (event,) = [one for one in story_coordinator.load_history(run.run_dir)
                if ABANDONED in one]
    assert event[ABANDONED] == 2
    assert THE_VERIFIERS_OWN["finding"] in event["message"]
    assert FOR_CORRECTION["slug"] in event["message"]


def test_a_run_whose_verdict_named_only_routed_findings_counts_only_those(
        both_allowances_spent):
    """The control for the number above: a run whose verdicts named nothing but
    routed findings has each record counting exactly what its account names, so
    the two in the record beside this one is the verifier's own entry being
    counted rather than the count being over something else entirely."""
    run = both_allowances_spent
    counted = sum(one[ABANDONED] for one in abandonment_records(run))
    assert counted == len(named_in(report_of(run), "abandoned"))
    assert counted == len(inspected.accounting(run)["abandoned_slugs"])


# ==========================================================================
# The predicate that decides which routed slugs an allowance abandons
# ==========================================================================


def state_with(*, corrections: int = 0, repairs: int = 0):
    return story_coordinator.RunState(
        story_id=STORY_ID, branch=f"story/{STORY_ID}",
        correction_pass_count=corrections, repair_pass_count=repairs)


ROUTED = (FOR_CORRECTION["slug"], FOR_REPAIR["slug"])


CORRECTION, REPAIR = "correction_pass", "repair_pass"


def abandoned_by(verdict: dict, state, kind: str,
                 stage: dict = None) -> dict:
    return story_coordinator.abandoned_routed_slugs(
        JUDGING_STAGE if stage is None else stage, verdict, state, ROUTED,
        kind=kind)


def test_a_verdict_that_did_not_pass_abandons_nothing():
    """Both pass blocks are on the passing route, so a failed verdict meets no
    allowance and leaves nothing behind for one."""
    failed = inspected.failing_naming(FOR_CORRECTION)
    spent = state_with(corrections=ALLOWANCE, repairs=ALLOWANCE)
    for kind in (CORRECTION, REPAIR):
        assert abandoned_by(failed, spent, kind) == {}, kind


def test_an_allowance_with_something_left_abandons_nothing():
    """The control for every case below: the same verdict against a state that
    has not spent the allowance abandons nothing, because the pass will run."""
    asking = passing(correctable=[correcting(FOR_CORRECTION)])
    assert abandoned_by(asking, state_with(), CORRECTION) == {}


def test_a_spent_correction_allowance_abandons_the_slugs_it_was_asked_for():
    """And only those: a slug nothing routed cannot be abandoned, because there
    is no routed finding for the verdict's entry to have been about."""
    asking = passing(correctable=[correcting(FOR_CORRECTION)])
    abandoned = abandoned_by(asking, state_with(corrections=ALLOWANCE),
                             CORRECTION)
    assert set(abandoned) == {FOR_CORRECTION["slug"]}
    assert str(ALLOWANCE) in abandoned[FOR_CORRECTION["slug"]]

    stray = passing(correctable=[inspected.CORRECTABLE_FINDING])
    assert abandoned_by(stray, state_with(corrections=ALLOWANCE),
                        CORRECTION) == {}


def test_the_predicate_answers_about_the_pass_it_is_asked_about():
    """One verdict naming a finding for each pass, against a state that has
    spent both: asked about the correction pass it names that finding alone,
    asked about the repair pass the other alone, and the two grounds name
    their own allowance so a reader is told which one ran out.

    Asking per pass is what took the ordering between the coordinator's two
    blocks out of this predicate, and the two facts below are that removal:
    the repair pass is answered about on the same terms whether or not the
    correction block would route this verdict away, because which block the
    run reaches is the run's business and not this reading's. What the
    deferral and the clean-clone route between the blocks actually leave
    recorded is held by driving a run rather than here — see the fixtures
    above, where each allowance is met on the verdict its own pass returned
    to.
    """
    asking_for_both = passing(correctable=[correcting(FOR_CORRECTION)],
                              repairable=[repairing_entry(FOR_REPAIR)])
    both_spent = state_with(corrections=ALLOWANCE, repairs=ALLOWANCE)
    named = {kind: abandoned_by(asking_for_both, both_spent, kind)
             for kind in (CORRECTION, REPAIR)}
    assert set(named[CORRECTION]) == {FOR_CORRECTION["slug"]}
    assert set(named[REPAIR]) == {FOR_REPAIR["slug"]}
    assert named[CORRECTION][FOR_CORRECTION["slug"]] != \
        named[REPAIR][FOR_REPAIR["slug"]]
    for kind, abandoned in named.items():
        for ground in abandoned.values():
            assert str(ALLOWANCE) in ground, kind
    # The same question with the correction allowance unspent — the state in
    # which the correction block routes and the repair block is not reached —
    # answers about the repair pass exactly as above.
    assert abandoned_by(asking_for_both, state_with(repairs=ALLOWANCE),
                        REPAIR) == named[REPAIR]


def test_a_stage_declaring_neither_pass_abandons_nothing():
    """A run under a definition that declares no pass has no allowance to
    spend, so there is nothing for a spent one to abandon — which is what keeps
    every such run exactly what it was."""
    stage = {key: value for key, value in JUDGING_STAGE.items()
             if key not in (CORRECTION, REPAIR)}
    asking_for_both = passing(correctable=[correcting(FOR_CORRECTION)],
                              repairable=[repairing_entry(FOR_REPAIR)])
    for kind in (CORRECTION, REPAIR):
        assert abandoned_by(
            asking_for_both,
            state_with(corrections=ALLOWANCE, repairs=ALLOWANCE),
            kind, stage) == {}, kind


# ==========================================================================
# A run with nothing to account for, and a run with no account to make
# ==========================================================================


def test_a_run_whose_inspection_routed_nothing_says_so_in_the_account(
        target, harness, tmp_path):
    """The eighth criterion's second half. An account omitted where there was
    nothing to account for would be indistinguishable from a report written
    before the account existed, so the section is written and says what
    happened.

    The control is the run beside it, where the same reading does not find that
    sentence — so the sentence is this run's emptiness rather than a search that
    matches anything.
    """
    run = inspected.drive(target, harness, tmp_path, findings=[],
                          name="nothing-to-route")

    assert run.code == 0
    report = report_of(run)
    assert FINDINGS_HEADING in report
    assert dispositions(report) == set()
    assert NOTHING_TO_ROUTE in account_section(report)
    # Nothing was sent on, so the report points at no filing outcome either.
    assert NOT_KNOWN_YET not in account_section(report)


def test_a_run_that_routed_something_does_not_say_it_found_nothing(
        every_disposition):
    """The control for the sentence above."""
    assert NOTHING_TO_ROUTE not in report_of(every_disposition)


def test_a_workflow_declaring_no_inspection_leaves_the_report_it_left_before(
        target, harness, tmp_path):
    """The eighth criterion's first half, as a comparison rather than as a
    reading of the code.

    Two runs of the same story, under two harness roots carrying the same
    definition and differing only in whether the inspection is declared: the
    report of the run with no inspection is byte for byte the other's with the
    account section removed, so the account is the whole of the difference this
    story makes to a report — and there is nothing else in it for a run that
    has no findings to account for.

    The two targets are separate so neither run reads the other's tree, and the
    findings and the verdict are the same, so what differs between the runs is
    the declaration.
    """
    inspecting = inspected.drive(
        target, harness, tmp_path, findings=[GROUNDED],
        verdicts=[passing(declining=[declined(GROUNDED)])],
        name="inspection-declared")
    undeclared = with_both_allowances(inspected.WORKFLOW)
    for stage in undeclared["stages"]:
        stage.pop("inspection", None)
    assert story_coordinator.inspection_declaration(undeclared["stages"]) == {}
    without = inspected.drive(
        inspected.build_target(tmp_path / "no-inspection-target",
                               workflow=undeclared["name"]),
        inspected.materialize(undeclared, tmp_path / "no-inspection-harness"),
        tmp_path, findings=[GROUNDED],
        verdicts=[passing(declining=[declined(GROUNDED)])],
        name="inspection-undeclared")

    assert (inspecting.code, without.code) == (0, 0)
    section = account_section(report_of(inspecting))
    assert section and GROUNDED["slug"] in section
    assert FINDINGS_HEADING not in report_of(without)
    assert report_of(without) == report_of(inspecting).replace(section, "", 1)

    # And nothing this story writes reaches the undeclared run's own history
    # either: the field the count travels in appears on no entry, and the note
    # the account writes when it cannot be derived appears in no message.
    history = story_coordinator.load_history(without.run_dir)
    assert history
    assert [one for one in history if ABANDONED in one] == []
    assert [one for one in history
            if COULD_NOT_BE_DERIVED in one["message"]] == []


def test_the_renderer_does_write_a_section_for_an_account_that_was_made():
    """The control for the absence above: the same renderer, handed an account
    saying an inspection was made and naming nothing, writes a section. So the
    undeclared run's missing heading is the guard on that flag deciding rather
    than a section the renderer could never produce."""
    render = story_coordinator._findings_account_section
    assert render(story_inspection.FindingsAccount(inspected=False)) == ""
    made = render(story_inspection.FindingsAccount(inspected=True))
    assert FINDINGS_HEADING in made
    assert NOTHING_TO_ROUTE in made


# ==========================================================================
# An account that could not be derived
# ==========================================================================


FAULT = "MARKER-FAULT the ground of a finding cannot be read"


def test_the_report_is_still_written_when_the_account_cannot_be_derived(
        target, harness, tmp_path, filings, monkeypatch, capsys):
    """The tenth criterion. A fault constructed inside the derivation — in the
    one reading the account makes that the filing does not, so the filing is
    left exactly as it is — and the run indifferent to it: the report is
    written, carries every other section, says the account could not be derived
    and why, and the run completes with the exit code it returns today. The
    reason reaches the run's own events log and stderr, where the rest of the
    inspection machinery reports its failures.

    The control is the run beside it, driven first with nothing patched, where
    the same readings find an account and no such sentence anywhere.
    """
    working = inspected.drive(
        target, harness, tmp_path, findings=[GROUNDED],
        verdicts=[passing(declining=[declined(GROUNDED)])], name="derived")
    assert working.code == 0
    assert COULD_NOT_BE_DERIVED not in report_of(working)
    assert named_in(report_of(working), "backlog") == {GROUNDED["slug"]: GROUND}
    filed_when_working = [sorted(call) for call in filings]

    def cannot_be_read(*args, **keywords):
        raise RuntimeError(FAULT)

    monkeypatch.setattr(story_inspection, "_ground_of", cannot_be_read)
    faulted = inspected.drive(
        inspected.build_target(tmp_path / "not-derived-target",
                               workflow=WORKFLOW["name"]),
        harness, tmp_path, findings=[GROUNDED],
        verdicts=[passing(declining=[declined(GROUNDED)])], name="not-derived")

    assert faulted.code == working.code == 0
    assert faulted.state["status"] == "completed"
    report = report_of(faulted)
    # Written, and whole: every heading the working run's report carried is
    # here, so the fault cost the account and nothing else.
    for heading in [one.split("\n", 1)[0] for one in
                    report_of(working).split("\n## ")[1:]]:
        assert f"## {heading}" in report, heading
    section = account_section(report)
    assert COULD_NOT_BE_DERIVED in section
    assert FAULT in section
    assert dispositions(report) == set()

    messages = [one for one in faulted.messages
                if COULD_NOT_BE_DERIVED in one and FAULT in one]
    assert len(messages) == 1, faulted.messages
    assert FAULT in capsys.readouterr().err

    # The filing is untouched by it, which is what makes this a fault in the
    # account alone: the same finding reached the same call it reached in the
    # run above.
    assert [sorted(call) for call in filings] == \
        filed_when_working + filed_when_working


# ==========================================================================
# One derivation, rendered by the coordinator
# ==========================================================================


INSPECTION_SOURCE = (REPO_ROOT / "orchestration" / "story_inspection.py"
                     ).read_text(encoding="utf-8")
COORDINATOR_SOURCE = (REPO_ROOT / "orchestration" / "story_coordinator.py"
                      ).read_text(encoding="utf-8")


#: The name the coordinator imports the derivation's module under. A module
#: name rather than a stage, artifact or prefix name, and the one the calls
#: below are qualified by.
DERIVATION_MODULE = story_inspection.__name__


def called_in(source: str, function: str) -> set[str]:
    """Every name one top-level function calls, bare or qualified, by the name
    at the call rather than by what qualifies it."""
    tree = ast.parse(conftest.function_source(source, function))
    called = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
        elif isinstance(node.func, ast.Name):
            called.add(node.func.id)
    return called


def reaches_into(source: str, function: str,
                 module: str = DERIVATION_MODULE) -> set[str]:
    """Every call one top-level function makes into `module` by that name, so a
    method on a local of the same spelling is not mistaken for one."""
    tree = ast.parse(conftest.function_source(source, function))
    return {node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == module}


def test_the_account_and_the_filing_go_through_one_assembly():
    """What the run-level agreement above rests on: the account and the filing
    at completion read the run directory through the same function, so they
    cannot come to disagree about which attempts and which earlier entries are
    being read."""
    shared = called_in(INSPECTION_SOURCE, "_file_at_completion") \
        & called_in(INSPECTION_SOURCE, "account_for_the_report")
    assert "_assemble" in shared


def test_the_coordinator_renders_the_account_and_derives_none_of_it():
    """The account is derived where the filing it must agree with is derived,
    and the coordinator renders what it is handed: the two functions that render
    it reach into the inspection module for nothing at all, and `_complete` asks
    for the account through the one call."""
    assert "account_for_the_report" in reaches_into(COORDINATOR_SOURCE,
                                                    "_complete")
    for renderer in ("_findings_account_section", "_named_findings"):
        assert reaches_into(COORDINATOR_SOURCE, renderer) == set(), renderer
    # And the control for those two empty sets: the same reading over the one
    # function that *does* reach for the account finds it, so an empty set is
    # the renderers reaching for nothing rather than a reading that has stopped
    # seeing calls.
    assert reaches_into(COORDINATOR_SOURCE, "_complete") != set()


# ==========================================================================
# No shipped text still says the event stream is the whole of it
# ==========================================================================


#: What the superseded texts said: that a finding a spent allowance left is
#: named in the event stream, where a developer meets it — which was untrue of
#: a stream living in a run directory the story's cleanup deletes.
#:
#: Held as the two things one sentence had to say to be making that claim,
#: rather than as either phrase anywhere in a file, because each half on its
#: own is ordinary English this repository already writes about unrelated
#: things: `orchestration/worktrees.py` says what a developer meets on every
#: `ls`, `orchestration/inspection.py` says where a developer meets a refile,
#: and the escalation's own comments say which renderings of the event stream
#: its commit carries. A reading looking for either would report the next
#: accurate sentence written that way in a swept directory, and report it with
#: a message about an abandoned finding — so it would redden the suite for
#: something this rule has nothing to say about. The conjunction is asked
#: sentence by sentence for the same reason: a file carrying both halves in
#: two unrelated sentences is making neither claim.
SUPERSEDED = ("event stream", "a developer meets")

#: The sentence the two writers' docstrings carried, as they carried it. It
#: exists to be planted into a text and read back, which needs a sentence and
#: not a commit, so it is written here rather than resolved out of this
#: repository's history: nothing moves under it when something is rebased, and
#: the assertion beside it holds it to carrying both halves above.
SUPERSEDED_SENTENCE = (
    "A second verdict still carrying findings is not an error and routes "
    "nowhere, so the findings are named in the event stream, where a "
    "developer meets them, and the run completes."
)

#: Two accurate sentences carrying one half each about something that is not
#: an abandoned finding — the run's cost total, which
#: `.harness/docs/ARCHITECTURE.md` states in almost these words today, and the
#: escalation's two commits. Written here rather than read out of the tree, so
#: the control is about the reading rather than about whichever sentence some
#: document happens to carry this week. The reading must report neither, which
#: is the whole of what says it reports the claim rather than a phrase.
UNRELATED_BUT_ACCURATE = (
    "The run total is reported where a developer meets the run's end, in the "
    "completion report's own cost section.",
    "Both renderings of the event stream are inside the commit the escalation "
    "makes, so a repository that tracks its run directory is not left dirty.",
)

#: What each of those texts says instead, and what makes the absence above
#: worth asserting rather than being an absence for any reason at all: the
#: account a developer reads is in the completion report, and the finding is
#: going to the backlog like every other one the verdict left. Two claims every
#: one of the six makes; what each of them says *beyond* them differs, because a
#: docstring, a schema description and a test docstring are not owed the same
#: detail, and a needle per text would be this module carrying a copy of each.
INSTEAD = ("completion report", "backlog")

#: What the texts declaring the count say about which findings it covers, which
#: is a distinction none of them drew before and which a reader of the
#: committed history needs: the count is over every finding the verdict named
#: for the pass its allowance refused, and the verifier may raise one of those
#: itself as readily as the inspection may route one to the story. The account,
#: the accounting and the filing carry the routed ones alone, because a finding
#: nothing routed has no routed finding for them to be about.
COUNTS_EVERY_NAMED = "counts every finding the verdict named"
THE_ROUTED_ONES = "routed"

#: Where this repository's own shipped source and contracts live. The
#: architecture document is deliberately not among them: its two passages are
#: this story's to correct and the documenter's to write, and that stage runs
#: after this one, so a sweep including it would be red when the suite runs
#: after this stage's turn and green afterwards — which would say nothing about
#: either stage's work. The verifier confirms that document.
SWEPT_DIRECTORIES = ("orchestration", "schemas")


def flowed(text: str) -> str:
    """`text` with its line wrapping removed, so a sentence that straddles a
    newline — which every one of these did, being wrapped source — is still
    found."""
    return " ".join(text.split())


def sentences(text: str) -> list[str]:
    """`text` flowed and cut where its sentences end, so the reading below is
    over one claim at a time.

    A file is not a claim: two unrelated sentences supplying one half of the
    superseded one each say nothing about an abandoned finding, and a reading
    over the whole text could not tell that from a sentence making the claim.
    """
    return [one for one in flowed(text).split(". ") if one.strip()]


def says_the_event_stream_is_the_whole(text: str) -> bool:
    """Whether `text` still claims the event stream is where a developer meets
    a finding a spent pass allowance abandoned — one sentence of it saying both
    halves of that claim, rather than the file carrying them somewhere."""
    return any(all(half in one for half in SUPERSEDED) for one in sentences(text))


def planted_with(text: str, sentence: str) -> str:
    """`text` with `sentence` appended as a sentence of its own.

    The boundary is put in deliberately: appended flush, a planted sentence
    joins whatever the text's own tail was in mid-sentence, and the reading
    above would then be asked about a run-on the plant and the file share
    between them instead of about the plant.
    """
    return f"{text.rstrip().rstrip('.')}.\n\n{sentence}"


def swept_texts() -> dict[str, str]:
    """Every shipped source file and contract under the directories above, keyed
    by its path relative to this repository. Found by walking rather than by a
    listing, so a passage nobody thought to name is still read."""
    found = {}
    for directory in SWEPT_DIRECTORIES:
        for path in sorted((REPO_ROOT / directory).rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            if path.suffix not in (".py", ".json"):
                continue
            found[str(path.relative_to(REPO_ROOT))] = path.read_text(
                encoding="utf-8")
    return found


#: The two modules holding the spent-allowance case of one pass each, and how
#: the test in each of them is found: by the name it ends in rather than by
#: being listed, so the docstring read is the one about the event stream and a
#: module that renamed that test fails here rather than being read for some
#: other text. They are read by name rather than swept, because this module
#: holds the superseded sentence itself in order to look for it and a sweep of
#: `tests/` would report its own searcher.
SPENT_ALLOWANCE_TESTS = ("test_correction_pass.py", "test_repair_pass.py")
SPENT_ALLOWANCE_TEST_SUFFIX = "_recorded_in_the_event_stream"

#: The two functions that record a spent allowance.
SPENT_ALLOWANCE_WRITERS = ("_correction_pass_recorded", "_repair_pass_recorded")


def spent_allowance_test_docstring(module: str) -> str:
    """The docstring of the one test in `module` about what the event stream
    records when an allowance is spent, found by the suffix above."""
    tree = ast.parse((TESTS_DIR / module).read_text(encoding="utf-8"))
    found = [ast.get_docstring(node) or "" for node in tree.body
             if isinstance(node, ast.FunctionDef)
             and node.name.endswith(SPENT_ALLOWANCE_TEST_SUFFIX)]
    assert len(found) == 1, (module, found)
    return found[0]


def schema_text(name: str) -> str:
    """One shipped schema's text, read as text: the subject here is what the
    document says rather than what it validates."""
    return (REPO_ROOT / "schemas" / f"{name}.schema.json").read_text(
        encoding="utf-8")


def abandoned_descriptions() -> dict[str, str]:
    """What each schema declaring the count says the field is for, keyed by the
    schema's name."""
    history = json.loads(schema_text("execution-history"))
    cross_run = json.loads(schema_text("cross-run-history"))
    return {
        "execution-history":
            history["items"]["properties"][ABANDONED]["description"],
        "cross-run-history":
            cross_run["properties"][inspected.INSPECTION_LOG]["items"][
                "properties"][ABANDONED]["description"],
    }


def texts_declaring_the_count() -> dict[str, str]:
    """The shipped texts that declare the field carrying the count, keyed by
    where each is: the docstrings of the two writers that append it, and the
    two schema descriptions of the field itself.

    These are the texts a reader consults to learn what the number means, so
    they are the ones that owe the distinction between what the count covers
    and what the account covers. The two test docstrings below are not among
    them: what they are about is that the run's own event stream names each
    finding, which is true of a routed finding and a verifier's own alike.
    """
    texts = {name: conftest.function_source(COORDINATOR_SOURCE, name)
             for name in SPENT_ALLOWANCE_WRITERS}
    for name, description in abandoned_descriptions().items():
        texts[f"{name}:{ABANDONED}"] = description
    return texts


def superseded_texts() -> dict[str, str]:
    """Every text this story brings into line, keyed by where it is. The
    docstrings of the two writers, the two schema descriptions of the field that
    carries the count, and the two spent-allowance test docstrings."""
    texts = dict(texts_declaring_the_count())
    for name in SPENT_ALLOWANCE_TESTS:
        texts[name] = spent_allowance_test_docstring(name)
    return texts


def test_no_shipped_source_or_contract_says_the_event_stream_is_the_whole():
    """The eleventh criterion, swept rather than listed: no file under the
    directories above still carries the sentence, wherever it might have been
    said. The control beside this one shows the same reading reporting it."""
    carrying = sorted(relative for relative, text in swept_texts().items()
                      if says_the_event_stream_is_the_whole(text))
    assert carrying == []


def test_each_text_this_story_brings_into_line_says_the_new_rule_instead():
    """The named half of the same criterion, and what makes its absence worth
    asserting: each of these texts says where the durable record is and that
    the finding is filed, rather than saying nothing at all."""
    texts = superseded_texts()
    assert len(texts) == len(SPENT_ALLOWANCE_WRITERS) \
        + len(SPENT_ALLOWANCE_TESTS) + len(abandoned_descriptions())
    for where, text in texts.items():
        assert not says_the_event_stream_is_the_whole(text), where
        for says in INSTEAD:
            assert says in flowed(text), (where, says)


def test_the_texts_declaring_the_count_say_which_findings_it_covers():
    """What the run beside this one is the other half of. The count and the
    account cover different sets, and each text a reader consults to learn what
    the number means says so: the count is over every finding the verdict named
    for the refused pass, and the routed ones are what the account, the
    accounting and the filing carry.

    Asserted as presence, which needs no control: a text that stopped saying it
    fails here on its own. What the texts must not do is claim the account
    names every finding the count covers, and saying which set each covers is
    how they stop doing that.
    """
    texts = texts_declaring_the_count()
    assert len(texts) == len(SPENT_ALLOWANCE_WRITERS) \
        + len(abandoned_descriptions())
    for where, text in texts.items():
        assert COUNTS_EVERY_NAMED in flowed(text), where
        assert THE_ROUTED_ONES in flowed(text), where


def test_the_reading_reports_the_superseded_sentence_where_it_is_planted():
    """The control for both absences above. Each subject, with the sentence
    planted back into it, is handed to the same reading, which reports it — so
    the silence above is these texts having been brought into line rather than a
    reading that has stopped seeing anything.

    The sweep is controlled the same way, over a copy of every swept file, so
    the walk that found nothing is shown to find something in each of them.
    """
    assert all(half in SUPERSEDED_SENTENCE for half in SUPERSEDED)
    for where, text in superseded_texts().items():
        assert says_the_event_stream_is_the_whole(
            planted_with(text, SUPERSEDED_SENTENCE)), where

    swept = swept_texts()
    assert swept, "the sweep read nothing at all"
    planted = {relative: planted_with(text, SUPERSEDED_SENTENCE)
               for relative, text in swept.items()}
    assert sorted(relative for relative, text in planted.items()
                  if says_the_event_stream_is_the_whole(text)) == sorted(swept)


def test_the_reading_reports_neither_half_of_the_claim_on_its_own():
    """The control in the other direction, and what the sweep above would be
    worth without it: a reading looking for one half alone passes this module's
    own absence today and reports the next accurate sentence somebody writes.

    Each sentence below carries one half about something that is not an
    abandoned finding — the run's cost total, and the escalation's two commits.
    The reading reports neither on its own, and reports neither when it is
    planted into a swept file, so the silence over this repository is the
    conjunction deciding rather than a sweep that has stopped seeing.
    """
    swept = swept_texts()
    assert swept, "the sweep read nothing at all"
    for sentence in UNRELATED_BUT_ACCURATE:
        # The premise: it really does carry one half, so a reading looking for
        # that half alone *would* report it.
        carried = [half for half in SUPERSEDED if half in sentence]
        assert len(carried) == 1, sentence
        assert not says_the_event_stream_is_the_whole(sentence), sentence
        reported = sorted(
            relative for relative, text in swept.items()
            if says_the_event_stream_is_the_whole(planted_with(text, sentence)))
        assert reported == [], (sentence, reported)
