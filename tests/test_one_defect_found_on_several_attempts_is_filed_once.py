"""story-156 validation: one defect found on several attempts of a run is
filed once.

A run that takes several attempts inspects its change once per attempt, and
each attempt used to file what its verdict declined as soon as the verdict was
in. story-155 took three attempts and filed thirteen briefs that were six
defects: a defect re-found on attempt two and three was filed again each time,
because a brief is filed under the slug, the category and the path set, and
all three are the Inspector's output on a second reading — rephrased for the
same reason a title is. This story stops a run filing against itself by filing
once, at completion, from the final attempt: nothing an inspection finds during
a run reaches the queue until the run completes.

The subjects, in the order the criteria state them:

  * **the criterion itself** — the assertion story-155 would have failed. A
    run of three attempts whose three inspections each report one finding
    about the change under a different slug, a different category and a
    different path set, and whose three verdicts each decline it, leaves
    exactly one entry in the queue, and it is the final attempt's. Beside it,
    a run of one attempt over that same final reading files the same entry,
    under the same key — which is the story's claim that three attempts file
    exactly what one attempt over the shipped code would have filed.

  * **the two instants.** The queue as each judging stage saw it holds nothing
    the run's inspections found, about the change or beside it; the queue as
    the run left it holds the final attempt's held findings and remainder and
    none of the earlier attempts'.

  * **what only an earlier attempt found** leaves no entry.

  * **a re-found finding** is routed to the verifier again on the retry rather
    than dropped as already queued — the property story-152 chose and this
    story reverses on purpose.

  * **escalation and resume.** A run that escalates leaves the queue exactly
    as it found it, with its held and accounting artifacts still in the run
    directory; the same run resumed and completed files its own final
    attempt's, once, and the resume moved every earlier artifact under the
    entry it closed. Since story-198 it files that closed entry's own final
    attempt too, and says on the completion line how many came from there:
    what the resume moved aside used to be stranded, so a finding a verifier
    had looked at and declined before an interruption never reached the
    backlog. What that entry's *earlier* attempts held is still superseded,
    which is this module's rule applied within the entry rather than across
    the run.

  * **the one call and the one event.** Everything filed at completion reaches
    the queue through `inspection.file_findings`, and the event that says so
    is of kind `inspection-filed`, carries `filed` and `dropped`, and names each
    drop reason with its count.

  * **what is said before completion** is true when written: the accounting
    events carry `filed` and `dropped` at zero and say how many were held, and
    no line written before the run completes says a finding was filed.

  * **the records.** Both history schemas declare the kind, and the cross-run
    inspection log receives one filing record per completed run that filed or
    dropped anything.

  * **the controls.** Under a workflow declaring no inspection, and under one
    whose file cap is unset, the run's events.log is what it would be with the
    completion filing removed altogether.

  * **the guarantee.** The entry point is total in the shape of the three
    beside it, reaches no model, and is called once from the coordinator by a
    bare statement.

Every absence asserted here carries a demonstration that it can fail:

  * "the earlier attempts' findings are not in the queue" sits beside the final
    attempt's, which the same reading finds there, and beside attempt 1's
    findings record and accounting, which show the finding was found and
    recorded before it went unfiled;
  * "the queue is empty at every judging" sits beside the queue as the run
    left it, read by the same helper, which is full;
  * "an attempt-1-only finding leaves no entry and no filing event" sits
    beside the three-attempt run, where the same readings find one of each;
  * "an escalated run leaves the queue empty and writes no filing event" sits
    beside its own resume, where the same queue and the same events fill;
  * "nothing reaches the queue except through `file_findings`" is shown by
    replacing that one call, after which the queue that was full is empty;
  * "no line before completion says a finding was filed" sits beside the
    completion line, which the same search does match;
  * "the completion filing adds nothing to events.log under the two control
    workflows" sits beside the declaring workflow with findings, where removing
    it changes the log;
  * "the coordinator's one call site reads nothing" sits beside the planted
    call sites the sibling module already shows the scan reporting.

Nothing here reaches a model: the sibling module's guard on
`agent_runner.run_agent` is installed for every test here too, and the stages
run through its fake runner. Nothing here resolves a baseline out of git; the
one repository each run reads is the one its fixture built.

The workflow these runs execute is the sibling module's built definition,
materialized with a retry ceiling this module owns — three attempts need two
retries, and a ceiling read off the shipped rules would make the criterion a
fact about the deployment. Every stage name, artifact name and category below
is derived from that definition or from the schema, never written here.
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import json
import re
import subprocess
from pathlib import Path

import pytest

import agent_runner
import conftest
import harness_config
import inspection
import outbox
import schema_validator
import story_coordinator
import story_inspection
from test_inspection_findings_reach_the_story import (  # noqa: F401 - fixtures used by name
    CHANGED_FILE,
    CHANGED_TEST,
    COORDINATOR_SOURCE,
    CROSS_RUN_SCHEMA,
    FINDINGS_ARTIFACT,
    INSPECTION_LOG,
    JUDGING,
    PASS,
    RETRY_CATEGORY,
    SEVERITY_ENUM,
    SIBLING_FILE,
    STORY_ID,
    WORKFLOW,
    Inspector,
    Run,
    Runner,
    accounting,
    accounting_path,
    build_target,
    events_of_kind,
    failing_into,
    filed_events,
    filings,
    filings_of,
    held_record,
    materialize,
    no_model,
    pre_stage_calls_in,
    rendered_findings,
    without_the_declaration,
)

#: The ceiling these runs are held to, owned here. Two retries is the smallest
#: number that reaches a third attempt, which is what the criterion is about.
MAX_RETRIES = 2
RULES = {
    "max_retries": MAX_RETRIES,
    "require_verifier_pass": True,
    "blocked_paths": [".git/", ".harness/runs/", "rules/"],
}

def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


# ==========================================================================
# The findings: one defect, read three times
# ==========================================================================


BRIEF_SCHEMA = schema_validator.load_schema(inspection.BRIEF_SCHEMA)
CATEGORIES = BRIEF_SCHEMA["properties"]["category"]["enum"]


def drifting(reading: int, paths: list[str], marker: str) -> dict:
    """One finding about the change, as the Inspector's `reading`-th look at
    the same defect: a slug, a category and a path set of its own, which is
    exactly the drift that made a brief filed per attempt a brief filed once
    per attempt under keys nothing could match."""
    return {
        "title": f"zzz: {marker}",
        "slug": f"zzz-reading-{reading}",
        "body": f"{marker}: {' and '.join(paths)} disagree",
        "category": CATEGORIES[reading % len(CATEGORIES)],
        "severity": min(SEVERITY_ENUM),
        "confidence": BRIEF_SCHEMA["properties"]["confidence"]["enum"][0],
        "effort": BRIEF_SCHEMA["properties"]["effort"]["enum"][0],
        "workflow": WORKFLOW["name"],
        "paths": list(paths),
    }


#: Three readings of one defect in the change. Each names a changed file, so
#: the partition routes every one of them to the verifier; each differs from
#: the others in every part of the identity a brief is filed under.
FIRST = drifting(1, [CHANGED_FILE], "MARKER-FIRST the change is wrong")
SECOND = drifting(2, [CHANGED_FILE, SIBLING_FILE],
                  "MARKER-SECOND the change still disagrees with its neighbour")
THIRD = drifting(3, [CHANGED_FILE, CHANGED_TEST],
                 "MARKER-THIRD the change and its test still disagree")
READINGS = (FIRST, SECOND, THIRD)


def beside(reading: int, marker: str) -> dict:
    """One finding naming only the file beside the change, so the partition
    holds it rather than routing it, under a slug of its own per reading."""
    found = drifting(reading, [SIBLING_FILE], marker)
    return {**found, "slug": f"zzz-beside-reading-{reading}"}


BESIDE_FIRST = beside(1, "MARKER-BESIDE-FIRST the neighbour was always wrong")
BESIDE_SECOND = beside(2, "MARKER-BESIDE-SECOND the neighbour is still wrong")
BESIDE_THIRD = beside(3, "MARKER-BESIDE-THIRD the neighbour remains wrong")
BESIDES = (BESIDE_FIRST, BESIDE_SECOND, BESIDE_THIRD)

EVERY_MARKER = tuple(one["body"] for one in READINGS + BESIDES)


def test_the_three_readings_are_three_identities_and_all_about_the_change():
    """The premise the criterion rests on. If two readings shared an identity
    the queue would hold one entry for them by the outbox's own dedupe, and
    the criterion would be vacuous; if one named no changed file it would be
    held rather than routed, and the verdicts would have nothing to decline."""
    identities = [json.dumps(inspection.identity(one), sort_keys=True)
                  for one in READINGS]
    assert len(set(identities)) == len(READINGS), identities
    assert len({one["slug"] for one in READINGS}) == len(READINGS)
    assert len({one["category"] for one in READINGS}) == len(READINGS)
    assert len({tuple(sorted(inspection.bare_paths(one)))
                for one in READINGS}) == len(READINGS)
    own, others = story_inspection.about_the_change(
        [_Found(one) for one in READINGS], (CHANGED_FILE, CHANGED_TEST))
    assert [one.finding for one in own] == list(READINGS)
    assert others == []
    # And the ones beside the change are the other way, so the two-instants
    # case below reads both kinds.
    own, others = story_inspection.about_the_change(
        [_Found(one) for one in BESIDES], (CHANGED_FILE, CHANGED_TEST))
    assert own == []
    assert [one.finding for one in others] == list(BESIDES)


class _Found:
    def __init__(self, found: dict):
        self.finding = found


# ==========================================================================
# Driving a run whose Inspector reads differently each attempt
# ==========================================================================


class Drifting(Inspector):
    """The sibling module's fake Inspector, answering a different list on each
    invocation — which is what an Inspector reading a changed tree does, and
    what a fixed list cannot show."""

    def __init__(self, target: Path, journal: Path, per_attempt):
        super().__init__(target, journal)
        self.per_attempt = [list(one) for one in per_attempt]

    def __call__(self, prompt, **keywords):
        self.findings = self.per_attempt[len(self.invocations)]
        return super().__call__(prompt, **keywords)


@pytest.fixture
def harness(tmp_path) -> Path:
    return materialize(WORKFLOW, tmp_path / "filed-once-harness", rules=RULES)


@pytest.fixture
def target(tmp_path) -> Path:
    return build_target(tmp_path / "filed-once-target")


def drive(target: Path, harness: Path, tmp_path: Path, *, per_attempt,
          verdicts, name: str = "journal") -> Run:
    """One run, with the Inspector answering `per_attempt[n]` on its n-th
    invocation. Installed in place of the guard the imported autouse fixture
    put there, which restores the original when the test ends."""
    journal = tmp_path / f"{name}.txt"
    inspector = Drifting(target, journal, per_attempt)
    agent_runner.run_agent = inspector
    runner = Runner(target, journal, list(verdicts))
    code = story_coordinator.run_story(
        STORY_ID, harness, target, runner, sleep=lambda _seconds: None)
    return Run(code, target, journal, inspector, runner)


@pytest.fixture
def driven(target, harness, tmp_path):
    def make(**kwargs) -> Run:
        return drive(target, harness, tmp_path, **kwargs)
    return make


#: The verdicts that take a run to its third attempt and complete it there,
#: each declining whatever was routed to it: none names a slug.
THREE_ATTEMPTS = [failing_into(RETRY_CATEGORY), failing_into(RETRY_CATEGORY),
                  PASS]

#: The verdicts that spend the whole allowance and escalate.
TO_THE_CEILING = [failing_into(RETRY_CATEGORY)] * (MAX_RETRIES + 1)


def markers_in(entries: list[dict]) -> list[str]:
    """Which of this module's findings a list of queue entries carries, by
    the finding's own words rather than by key."""
    text = [json.dumps(entry) for entry in entries]
    return sorted(marker for marker in EVERY_MARKER
                  if any(marker in entry for entry in text))


def entries_carrying(run: Run, marker: str) -> list[dict]:
    return [entry for entry in run.queue if marker in json.dumps(entry)]


def keys_of(entries: list[dict]) -> list[str]:
    return sorted(entry["key"] for entry in entries)


# ==========================================================================
# The criterion
# ==========================================================================


def test_three_attempts_each_declining_a_rephrased_finding_file_it_once(
        driven, filings):
    """The assertion story-155 would have failed.

    Three attempts, three readings of one defect under three identities,
    three verdicts declining it: the queue holds exactly one entry, it is the
    final attempt's reading, and it got there in one filing call. Attempt 1's
    and attempt 2's readings were found, routed and recorded — which is the
    control that says their absence from the queue is the deferral's doing
    and not a run that never found them.
    """
    run = driven(per_attempt=[[FIRST], [SECOND], [THIRD]],
                 verdicts=THREE_ATTEMPTS)

    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.state["retry_count"] == MAX_RETRIES
    assert len(run.inspector.invocations) == 3

    assert len(run.queue) == 1
    assert markers_in(run.queue) == [THIRD["body"]]
    assert entries_carrying(run, FIRST["body"]) == []
    assert entries_carrying(run, SECOND["body"]) == []
    # Found and recorded on the attempts that found them, then superseded.
    assert run.findings_record(1)["findings"] == [FIRST]
    assert run.findings_record(2)["findings"] == [SECOND]
    assert accounting(run, 1)["remainder_slugs"] == [FIRST["slug"]]
    assert accounting(run, 2)["remainder_slugs"] == [SECOND["slug"]]
    assert accounting(run, 3)["remainder_slugs"] == [THIRD["slug"]]
    # One filing call, carrying the final reading and nothing else.
    assert filings == [[THIRD["slug"]]]
    assert filings_of(filings, FIRST) == 0
    assert filings_of(filings, SECOND) == 0


def test_three_attempts_file_what_one_attempt_over_the_same_reading_files(
        tmp_path, harness):
    """The claim behind the criterion: the final attempt's inspection read the
    code that shipped, so a run of three attempts files exactly what a run of
    one attempt over that same reading would have — the same entry under the
    same key."""
    three = drive(build_target(tmp_path / "three"), harness, tmp_path,
                  per_attempt=[[FIRST], [SECOND], [THIRD]],
                  verdicts=THREE_ATTEMPTS, name="three-journal")
    one = drive(build_target(tmp_path / "one"), harness, tmp_path,
                per_attempt=[[THIRD]], verdicts=[PASS], name="one-journal")

    assert three.code == one.code == 0
    assert three.state["retry_count"] == MAX_RETRIES
    assert one.state["retry_count"] == 0
    assert keys_of(three.queue) == keys_of(one.queue)
    assert len(keys_of(one.queue)) == 1
    assert markers_in(three.queue) == markers_in(one.queue) == [THIRD["body"]]


# ==========================================================================
# The two instants
# ==========================================================================


def test_the_queue_is_empty_at_every_judging_and_holds_the_final_attempts_after(
        driven):
    """Both kinds of finding on every attempt. Each judging stage saw an empty
    queue — nothing an inspection found reached it, about the change or beside
    it — and the run left the final attempt's held finding and remainder in it
    and none of the earlier attempts'."""
    run = driven(per_attempt=[[FIRST, BESIDE_FIRST], [SECOND, BESIDE_SECOND],
                              [THIRD, BESIDE_THIRD]],
                 verdicts=THREE_ATTEMPTS)

    assert run.code == 0
    assert run.runner.queue_at_judging == [[], [], []]
    assert markers_in(run.queue) == sorted([THIRD["body"], BESIDE_THIRD["body"]])
    assert len(run.queue) == 2
    # Held on the attempts that held them, then superseded.
    for attempt, held in ((1, BESIDE_FIRST), (2, BESIDE_SECOND),
                          (3, BESIDE_THIRD)):
        assert held_record(run, attempt)["findings"] == [held]
        assert held_record(run, attempt)["attempt"] == attempt


def test_a_finding_only_an_earlier_attempt_found_leaves_no_entry(driven):
    """Attempt 1 finds it, attempts 2 and 3 find nothing: no entry at
    completion, and no filing event either, because the final attempt had
    nothing to file. The control is attempt 1's own record, which shows it was
    found, routed, declined and recorded as the remainder — and the run above,
    where the same readings find an entry and an event."""
    run = driven(per_attempt=[[FIRST, BESIDE_FIRST], [], []],
                 verdicts=THREE_ATTEMPTS)

    assert run.code == 0
    assert run.state["retry_count"] == MAX_RETRIES
    assert run.queue == []
    assert filed_events(run) == []
    assert run.findings_record(1)["findings"] == [FIRST]
    assert held_record(run, 1)["findings"] == [BESIDE_FIRST]
    assert accounting(run, 1)["remainder"] == 1
    # Attempts 2 and 3 each held nothing, under a record of their own, which
    # is what supersedes attempt 1's.
    assert held_record(run, 2)["findings"] == []
    assert held_record(run, 3)["findings"] == []


def test_a_retry_re_finding_a_finding_routes_it_to_the_verifier_again(driven):
    """The property story-152 chose, reversed on purpose: nothing is queued
    mid-run for the retry's inspection to drop the re-found finding against,
    so attempt 2 routes it again, writes it in its own findings record, renders
    it to the judge again and accounts for it under attempt 2."""
    run = driven(per_attempt=[[FIRST], [FIRST]],
                 verdicts=[failing_into(RETRY_CATEGORY), PASS])

    assert run.code == 0
    assert run.state["retry_count"] == 1
    assert run.findings_record(1)["findings"] == [FIRST]
    assert run.findings_record(2)["findings"] == [FIRST]
    rendered = json.loads(
        rendered_findings(run.runner.prompts[JUDGING][1]).strip())
    assert rendered["attempt"] == 2
    assert rendered["findings"] == [FIRST]
    assert accounting(run, 2)["routed"] == 1
    assert accounting(run, 2)["remainder_slugs"] == [FIRST["slug"]]
    assert not any(inspection.ALREADY_QUEUED in line for line in run.messages)
    assert run.runner.queue_at_judging == [[], []]
    # And once, at completion.
    assert len(entries_carrying(run, FIRST["body"])) == 1


# ==========================================================================
# Escalation, and the resume after it
# ==========================================================================


def ready_to_resume(run: Run, marker: str) -> None:
    """What a resume asks of a developer: a change in the run's tree, so the
    unchanged-since-escalation guard has something to see, committed so the
    clean-tree pre-flight is satisfied."""
    (run.tree / CHANGED_FILE).write_text(
        f"def changed():\n    return 11  # {marker}\n", encoding="utf-8")
    git(run.tree, "add", "-A")
    git(run.tree, "commit", "-q", "--allow-empty", "-m", f"decided: {marker}")


def held_files(run: Run, under: Path | None = None) -> list[str]:
    root = under or run.run_dir
    return sorted(path.name for path in root.glob(
        story_inspection.held_artifact_file(FINDINGS_ARTIFACT, "*")))


def accounting_files(run: Run, under: Path | None = None) -> list[str]:
    root = under or run.run_dir
    return sorted(path.name for path in root.glob(
        story_inspection.accounting_artifact_file(FINDINGS_ARTIFACT, "*")))


def test_an_escalated_run_leaves_the_queue_as_it_found_it_and_keeps_its_artifacts(
        driven, filings):
    """Three attempts, all failing: the run escalates, nothing was filed, no
    filing call was made, no filing event was written, and every attempt's
    held and accounting artifact is still in the run directory as evidence."""
    run = driven(per_attempt=[[FIRST, BESIDE_FIRST], [SECOND, BESIDE_SECOND],
                              [THIRD, BESIDE_THIRD]],
                 verdicts=TO_THE_CEILING)

    assert run.code == 2
    assert run.state["status"] == "escalated"
    assert run.state["retry_count"] == MAX_RETRIES
    assert run.runner.queue_at_judging == [[], [], []]
    assert run.queue == []
    assert filings == []
    assert filed_events(run) == []
    assert held_files(run) == sorted(
        story_inspection.held_artifact_file(FINDINGS_ARTIFACT, n)
        for n in (1, 2, 3))
    assert accounting_files(run) == sorted(
        story_inspection.accounting_artifact_file(FINDINGS_ARTIFACT, n)
        for n in (1, 2, 3))


def test_a_run_resumed_after_an_escalation_files_its_own_final_attempts_once(
        target, harness, tmp_path, filings):
    """The control for the absences above, and a criterion in its own right.

    The escalated run is resumed and completes on its first attempt: the
    queue, empty at the escalation, now holds that attempt's held finding and
    remainder through one filing call. The resume moved the earlier entry's
    held artifacts under resumes/resume-1/ beside its findings records and
    accountings, and the resumed entry's attempt 1 wrote its own at the root.

    **And the earlier entry's final reading, which story-198 stopped
    stranding.** Everything the interrupted entry wrote was moved into that
    entry's directory and the filing looked only at the current one, so a
    finding raised before the interruption — including one a verifier had
    looked at and reasonably declined, which is exactly what is supposed to
    become a backlog item — reached the backlog from nowhere. Its copy was
    sitting in the archive with nothing to go and get it. So the queue holds
    the earlier entry's attempt 3 as well as this entry's attempt 1, and the
    completion line says how many came from there.

    What it still does not hold is that entry's attempts 1 and 2: an entry
    contributes its own final attempt and no earlier reading of it, which is
    the rule of this whole module applied a second time. Their absence is not
    a claim about a filing that did nothing, because the same reading of the
    same queue finds attempt 3's two beside them.
    """
    escalated = drive(target, harness, tmp_path,
                      per_attempt=[[FIRST, BESIDE_FIRST], [SECOND, BESIDE_SECOND],
                                   [THIRD, BESIDE_THIRD]],
                      verdicts=TO_THE_CEILING, name="escalated-journal")
    assert escalated.state["status"] == "escalated"
    before = held_files(escalated)

    fourth = drifting(4, [CHANGED_FILE], "MARKER-FOURTH the change is wrong")
    beside_fourth = beside(4, "MARKER-BESIDE-FOURTH the neighbour is wrong")
    ready_to_resume(escalated, "the developer's change")
    resumed = drive(target, harness, tmp_path,
                    per_attempt=[[fourth, beside_fourth]], verdicts=[PASS],
                    name="resumed-journal")

    assert resumed.code == 0
    assert resumed.state["status"] == "completed"
    assert resumed.state["resume_count"] == 1
    assert resumed.state["retry_count"] == 0
    assert len(resumed.inspector.invocations) == 1
    assert resumed.runner.queue_at_judging == [[]]

    text = [json.dumps(entry) for entry in resumed.queue]
    assert len(text) == 4
    assert any(fourth["body"] in entry for entry in text)
    assert any(beside_fourth["body"] in entry for entry in text)
    # The earlier entry's own final attempt, recovered rather than stranded,
    # and its two superseded readings still left where they were.
    assert markers_in(resumed.queue) == sorted([THIRD["body"],
                                                BESIDE_THIRD["body"]])
    assert len(filings) == 1
    assert sorted(filings[0]) == sorted(
        [beside_fourth["slug"], fourth["slug"],
         BESIDE_THIRD["slug"], THIRD["slug"]])
    assert len(filed_events(resumed)) == 1
    assert filed_events(resumed)[0]["filed"] == 4
    assert "from earlier entries of this run: 2" in \
        filed_events(resumed)[0]["message"], filed_events(resumed)

    closed = story_coordinator.entry_dir(resumed.run_dir, 1)
    assert closed.is_dir()
    assert held_files(resumed, closed) == before
    assert accounting_files(resumed, closed) == sorted(
        story_inspection.accounting_artifact_file(FINDINGS_ARTIFACT, n)
        for n in (1, 2, 3))
    for attempt in (1, 2, 3):
        assert (closed / story_inspection.findings_artifact_file(
            FINDINGS_ARTIFACT, attempt)).is_file()
    assert held_files(resumed) == [
        story_inspection.held_artifact_file(FINDINGS_ARTIFACT, 1)]
    assert held_record(resumed, 1)["findings"] == [beside_fourth]
    assert accounting(resumed, 1)["remainder_slugs"] == [fourth["slug"]]


# ==========================================================================
# The one call, and the one event
# ==========================================================================


def test_everything_filed_at_completion_reaches_the_queue_through_file_findings(
        tmp_path, harness, monkeypatch):
    """Shown by taking the call away: with `inspection.file_findings` replaced
    by one that files nothing, the queue a run of the same findings fills is
    empty, and the filing event says so in its counts — so every entry the
    unreplaced run made came through that call and no other."""
    filed = drive(build_target(tmp_path / "filed"), harness, tmp_path,
                  per_attempt=[[FIRST, BESIDE_FIRST]], verdicts=[PASS],
                  name="filed-journal")
    assert filed.code == 0
    assert len(filed.queue) == 2

    def files_nothing(target_root, found, *args, **keywords):
        return (), []

    monkeypatch.setattr(inspection, "file_findings", files_nothing)
    unfiled = drive(build_target(tmp_path / "unfiled"), harness, tmp_path,
                    per_attempt=[[FIRST, BESIDE_FIRST]], verdicts=[PASS],
                    name="unfiled-journal")
    assert unfiled.code == 0
    assert unfiled.queue == []
    assert [one["filed"] for one in filed_events(unfiled)] == [0]


def test_the_completion_filing_is_one_call_to_file_findings_and_names_no_queue():
    """The body of the completion filing, read as source: exactly one call to
    `inspection.file_findings`, and no call that would reach the queue by any
    other name. The control beside it is a planted body that enqueues
    directly, which the same reading reports."""
    source = conftest.function_source(
        Path(story_inspection.__file__).read_text(encoding="utf-8"),
        "_file_at_completion")
    assert filing_calls(source) == [("inspection", "file_findings")]
    assert enqueue_calls(source) == []

    planted = ("def _file_at_completion(target_root):\n"
               "    outbox.enqueue(outbox.queue_dir(target_root), {}, {})\n"
               "    return inspection.file_findings(target_root, [], 1)\n")
    assert enqueue_calls(planted) == [("outbox", "enqueue")]


def _dotted_calls(source: str, attr: str) -> list[tuple[str, str]]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == attr
                and isinstance(node.func.value, ast.Name)):
            found.append((node.func.value.id, node.func.attr))
    return found


def filing_calls(source: str) -> list[tuple[str, str]]:
    return _dotted_calls(source, "file_findings")


def enqueue_calls(source: str) -> list[tuple[str, str]]:
    return _dotted_calls(source, "enqueue")


def test_the_filing_event_carries_the_counts_and_names_each_drop_reason(
        tmp_path, harness):
    """Two held findings against a cap of one: the event is of kind
    `inspection-filed`, carries filed and dropped, and its message names the
    reason the second went with its count."""
    run = drive(build_target(tmp_path / "capped",
                             **{inspection.MAX_FINDINGS_KEY: 1}),
                harness, tmp_path,
                per_attempt=[[BESIDE_FIRST, BESIDE_SECOND]], verdicts=[PASS],
                name="capped-journal")

    assert run.code == 0
    assert len(run.queue) == 1
    events = filed_events(run)
    assert len(events) == 1
    assert events[0]["event"] == story_inspection.FILED_EVENT == "inspection-filed"
    assert events[0]["filed"] == 1
    assert events[0]["dropped"] == 1
    assert f"{inspection.PAST_THE_CAP}: 1" in events[0]["message"]
    assert "1 filed" in events[0]["message"]
    assert "1 dropped" in events[0]["message"]
    assert events[0]["message"] in run.messages


def test_the_filing_event_is_neither_an_inspection_nor_an_accounting(driven):
    """A reader counting inspections or accountings does not count the filing:
    its entry carries neither the inspection's fields nor the accounting's."""
    run = driven(per_attempt=[[FIRST]], verdicts=[PASS])

    events = filed_events(run)
    assert len(events) == 1
    for absent in ("routed", "acted_on", "invocations", "mode", "dedupe_ran",
                   "cost_usd", "scope_files"):
        assert events[0].get(absent) is None, (absent, events[0])
    assert events_of_kind(run, story_inspection.ACCOUNTING_EVENT)[0]["routed"] == 1


# ==========================================================================
# What is said before completion is true when written
# ==========================================================================


def test_every_accounting_event_says_held_and_carries_filed_and_dropped_at_zero(
        driven):
    """One accounting per attempt, each with filed and dropped at zero and a
    message saying how many the verdict left for the run's completion — and no entry
    before the filing carries a filed count above zero, in its field or in its
    words. The control is the filing entry itself, which the same two readings
    do match."""
    run = driven(per_attempt=[[FIRST], [SECOND], [THIRD]],
                 verdicts=THREE_ATTEMPTS)

    accounted = events_of_kind(run, story_inspection.ACCOUNTING_EVENT)
    assert len(accounted) == 3
    for event in accounted:
        assert event["filed"] == 0
        assert event["dropped"] == 0
        assert "1 left by it for the run's completion" in event["message"]
        assert "0 filed" in event["message"]

    history = story_coordinator.load_history(run.run_dir)
    kinds = [one["event"] for one in history]
    assert kinds.count(story_inspection.FILED_EVENT) == 1
    at = kinds.index(story_inspection.FILED_EVENT)
    before, filing = history[:at], history[at]
    assert accounted[-1] in before
    assert all(not one.get("filed") for one in before), \
        [one for one in before if one.get("filed")]
    assert not any(says_filed(one["message"]) for one in before), \
        [one["message"] for one in before if says_filed(one["message"])]
    assert filing["filed"] == 1
    assert says_filed(filing["message"])


def says_filed(message: str) -> bool:
    """Whether a line claims a finding was filed: a count above zero beside
    the word, or the verb with no count at all."""
    return bool(re.search(r"\b[1-9]\d* filed\b", message)) \
        or bool(re.search(r"\bfiled as briefs\b", message))


# ==========================================================================
# The records
# ==========================================================================


EXECUTION_SCHEMA = schema_validator.load_schema("execution-history")


def event_kinds(schema: dict) -> list[str]:
    return schema["properties"]["event"]["enum"]


def test_both_history_schemas_declare_the_filing_beside_the_accounting():
    """What this harness ships, read as the subject: the execution history's
    closed enum names the kind, and the cross-run schema routes it to the log
    the inspection's own kind goes to and to no other."""
    kind = story_inspection.FILED_EVENT
    execution_kinds = event_kinds(EXECUTION_SCHEMA["items"])
    assert kind in execution_kinds
    assert story_inspection.ACCOUNTING_EVENT in execution_kinds
    for name, declared in CROSS_RUN_SCHEMA["properties"].items():
        kinds = event_kinds(declared["items"])
        assert (kind in kinds) == (name == INSPECTION_LOG), name


def filing_records(run: Run) -> list[dict]:
    """The cross-run log's filing records, told apart from the other two
    kinds by carrying neither the inspection's fields nor the accounting's."""
    return [one for one in run.log_records
            if "routed" not in one and "invocations" not in one]


def test_the_cross_run_log_receives_one_filing_record_per_run_that_filed(
        tmp_path, harness):
    """One record for the three-attempt run, carrying what was filed and
    dropped and conforming to the log's declaration; none for the run whose
    final attempt had nothing to file, which is the control."""
    filed = drive(build_target(tmp_path / "filed"), harness, tmp_path,
                  per_attempt=[[FIRST], [SECOND], [THIRD]],
                  verdicts=THREE_ATTEMPTS, name="filed-journal")
    records = filing_records(filed)
    assert len(records) == 1
    assert records[0]["filed"] == 1
    assert records[0]["dropped"] == 0
    assert records[0]["story_id"] == STORY_ID
    assert schema_validator.validate(
        records, CROSS_RUN_SCHEMA["properties"][INSPECTION_LOG]) == []
    # Durable: the record is committed on the branch, under a subject of the
    # filing's own rather than the post-story inspection's.
    assert git(filed.tree, "status", "--porcelain").stdout.strip() == ""
    subjects = git(filed.tree, "log", "--format=%s").stdout.splitlines()
    assert story_inspection.FILED_COMMIT_SUBJECT.format(story_id=STORY_ID) \
        in subjects
    assert story_inspection.COMMIT_SUBJECT.format(story_id=STORY_ID) \
        not in subjects

    nothing = drive(build_target(tmp_path / "nothing"), harness, tmp_path,
                    per_attempt=[[FIRST], [], []], verdicts=THREE_ATTEMPTS,
                    name="nothing-journal")
    assert nothing.code == 0
    assert filing_records(nothing) == []
    assert story_inspection.FILED_COMMIT_SUBJECT.format(story_id=STORY_ID) \
        not in git(nothing.tree, "log", "--format=%s").stdout.splitlines()


# ==========================================================================
# The controls: nothing changes where nothing is declared or the cap is unset
# ==========================================================================


@pytest.fixture
def completion_filing(monkeypatch):
    """A switch on the completion filing: `calls` counts how often the
    coordinator reached it, and `off()` replaces it with nothing at all."""
    class Switch:
        def __init__(self):
            self.calls = 0
            self.original = story_inspection.file_at_completion

        def counting(self, *args, **keywords):
            self.calls += 1
            return self.original(*args, **keywords)

        def off(self):
            monkeypatch.setattr(story_inspection, "file_at_completion",
                                lambda *args, **keywords: None)

    switch = Switch()
    monkeypatch.setattr(story_inspection, "file_at_completion",
                        switch.counting)
    return switch


def normalised(run: Run) -> list[str]:
    """events.log with its timestamps and the target's own path taken out, so
    two runs of two targets can be compared line for line."""
    return [line.replace(str(run.target), "<target>")
            .replace(str(run.tree), "<tree>")
            for line in run.messages]


def test_under_a_workflow_declaring_no_inspection_the_filing_is_not_reached(
        tmp_path, completion_filing):
    """The coordinator does not reach the completion filing at all, and the
    events.log is line for line what it is with the filing removed. The
    control below is the declaring workflow, where it is reached."""
    first = build_target(tmp_path / "first")
    harness = without_the_declaration(tmp_path, first)
    with_it = drive(first, harness, tmp_path, per_attempt=[[FIRST]],
                    verdicts=[PASS], name="with-journal")
    assert with_it.code == 0
    assert completion_filing.calls == 0

    completion_filing.off()
    without_it = drive(build_target(tmp_path / "second"), harness, tmp_path,
                       per_attempt=[[FIRST]], verdicts=[PASS],
                       name="without-journal")
    assert without_it.code == 0
    assert normalised(with_it) == normalised(without_it)
    assert filed_events(with_it) == []
    # The clause both lines share, rather than either one's wording: an
    # absence asserted against one phrasing stops failing the moment that
    # phrasing moves, which is how this control was left passing vacuously
    # once the accounting line stopped saying "held".
    assert not any("for the run's completion" in line
                   for line in with_it.messages)


def test_under_an_unset_file_cap_the_filing_writes_nothing(
        tmp_path, harness, completion_filing, no_model):
    """The cap unset is the inspection off: no invocation, nothing held, and
    the completion filing — reached, because the workflow declares the
    inspection — writes no event and no artifact, so the events.log is line
    for line what it is with the filing removed."""
    def switched_off(name: str) -> Run:
        target = build_target(tmp_path / name,
                              **{story_inspection.MAX_FILES_KEY: ""})
        journal = tmp_path / f"{name}-journal.txt"
        code = story_coordinator.run_story(
            STORY_ID, harness, target, Runner(target, journal, [PASS]),
            sleep=lambda _seconds: None)
        return Run(code, target, journal, Inspector(target, journal), None)

    with_it = switched_off("with")
    assert with_it.code == 0
    assert no_model.calls == 0
    assert completion_filing.calls == 1
    assert held_files(with_it) == []
    assert filed_events(with_it) == []

    completion_filing.off()
    without_it = switched_off("without")
    assert without_it.code == 0
    assert normalised(with_it) == normalised(without_it)


def test_under_the_declaring_workflow_removing_the_filing_changes_the_log(
        tmp_path, harness, completion_filing):
    """The control for the two equalities above: the same comparison where
    the filing has something to do reports the difference, so the equalities
    are a fact about those two configurations rather than about a comparison
    that cannot see the filing."""
    with_it = drive(build_target(tmp_path / "with"), harness, tmp_path,
                    per_attempt=[[FIRST]], verdicts=[PASS],
                    name="with-journal")
    assert completion_filing.calls == 1
    assert len(filed_events(with_it)) == 1

    completion_filing.off()
    without_it = drive(build_target(tmp_path / "without"), harness, tmp_path,
                       per_attempt=[[FIRST]], verdicts=[PASS],
                       name="without-journal")
    assert normalised(with_it) != normalised(without_it)
    assert filed_events(without_it) == []
    assert without_it.queue == []


# ==========================================================================
# The guarantee: total, and decided on by nothing
# ==========================================================================


def test_the_completion_filing_offers_no_way_to_stop_a_run(
        target, harness, tmp_path, no_model):
    """Its signature in the terms the three entry points beside it are held
    to: everything the post-story sibling takes except the runner — it
    invokes nothing — no parameter by which a caller could be told to stop,
    and None answered on a run directory that holds nothing, with no model
    reached."""
    parameters = inspect_module.signature(
        story_inspection.file_at_completion).parameters
    sibling = inspect_module.signature(
        story_inspection.inspect_after_story).parameters
    assert list(parameters) == [
        "run_dir", "target_root", "config", "harness_root", "story_id",
        "stages"]
    assert set(sibling) - set(parameters) == {"runner"}
    assert set(parameters) <= set(sibling)

    empty = tmp_path / "holds-nothing"
    empty.mkdir()
    answered = story_inspection.file_at_completion(
        empty, target, harness_config.load_config(target), harness, STORY_ID,
        WORKFLOW["stages"])
    assert answered is None
    assert no_model.calls == 0
    assert outbox.entry_files(outbox.queue_dir(target)) == []
    assert not (empty / "events.log").exists()


def test_a_failure_inside_the_completion_filing_changes_no_status_and_no_code(
        tmp_path, harness, monkeypatch):
    """The fault constructed inside the body, and the run indifferent to it:
    the same status and the same exit code as the run beside it where the
    filing worked, with the failure printed and recorded in events.log under
    the filing's own kind, and the queue left empty."""
    working = drive(build_target(tmp_path / "working"), harness, tmp_path,
                    per_attempt=[[FIRST]], verdicts=[PASS],
                    name="working-journal")
    assert working.code == 0
    assert len(working.queue) == 1

    def cannot_read(*args, **keywords):
        raise RuntimeError("MARKER-FAULT the held artifact cannot be read")

    monkeypatch.setattr(story_inspection, "held_findings", cannot_read)
    faulted = drive(build_target(tmp_path / "faulted"), harness, tmp_path,
                    per_attempt=[[FIRST]], verdicts=[PASS],
                    name="faulted-journal")

    assert faulted.code == working.code == 0
    assert faulted.state["status"] == working.state["status"] == "completed"
    assert faulted.runner.calls == working.runner.calls
    assert faulted.queue == []
    events = filed_events(faulted)
    assert len(events) == 1
    assert "could not run" in events[0]["message"]
    assert "MARKER-FAULT" in events[0]["message"]
    assert (events[0]["filed"], events[0]["dropped"]) == (0, 0)
    # The failure record was appended to the tracked cross-run log after the
    # completion commit, so it must be committed under the filing's own
    # subject: a tree left dirty here is what the next run's clean-tree
    # pre-flight refuses.
    assert git(faulted.tree, "status", "--porcelain").stdout.strip() == ""
    subject = git(faulted.tree, "log", "-1", "--format=%s").stdout.strip()
    assert subject == story_inspection.FILED_COMMIT_SUBJECT.format(
        story_id=STORY_ID)


def test_an_inspection_that_raises_on_a_later_attempt_supersedes_the_earlier_one(
        driven, monkeypatch):
    """Attempt 1 inspects, routes FIRST and holds BESIDE_FIRST; attempt 2's
    inspection raises before it reads anything. The failed attempt still
    writes an empty held artifact of its own, so the completion filing finds
    attempt 2 and not attempt 1 — filing nothing rather than filing attempt
    1's findings against a tree the retry replaced — and the run completes
    with a clean tree. Attempt 1's records are the control that says there
    was something to supersede."""
    original = story_inspection._inspect_before_stage
    calls = []

    def raising_on_the_second_call(*args, **keywords):
        calls.append(None)
        if len(calls) == 2:
            raise RuntimeError("MARKER-RAISED the inspection could not read")
        return original(*args, **keywords)

    monkeypatch.setattr(story_inspection, "_inspect_before_stage",
                        raising_on_the_second_call)
    run = driven(per_attempt=[[FIRST, BESIDE_FIRST], [FIRST, BESIDE_FIRST]],
                 verdicts=[failing_into(RETRY_CATEGORY), PASS])

    assert run.code == 0
    assert run.state["retry_count"] == 1
    assert len(calls) == 2
    assert story_inspection.held_findings(
        run.run_dir, FINDINGS_ARTIFACT, 2) == []
    assert story_inspection.held_findings(
        run.run_dir, FINDINGS_ARTIFACT, 1) == [BESIDE_FIRST]
    assert accounting(run, 1)["remainder_slugs"] == [FIRST["slug"]]
    assert run.queue == []
    assert filed_events(run) == []
    assert git(run.tree, "status", "--porcelain").stdout.strip() == ""


def test_the_coordinator_calls_the_filing_once_and_reads_nothing_from_it():
    """One call site, a bare statement, so nothing it answers can become a
    decision. The sibling module shows the same scan reporting planted call
    sites that read the answer."""
    calls = pre_stage_calls_in(
        COORDINATOR_SOURCE, story_inspection.file_at_completion)
    assert len(calls) == 1
    assert [node for node in calls if not isinstance(node, ast.Expr)] == []


def test_the_held_artifacts_name_is_keyed_by_the_attempt_and_matched_by_no_other_glob():
    """Two attempts cannot land on one name, and neither the findings
    artifact's glob nor the accounting's matches a held artifact — so a reader
    listing either does not count a held artifact among them, and the
    completion filing's own glob finds the held ones and nothing else."""
    import fnmatch

    held = {story_inspection.held_artifact_file(FINDINGS_ARTIFACT, n)
            for n in (1, 2, 3)}
    assert len(held) == 3
    for other in (story_inspection.findings_artifact_file,
                  story_inspection.accounting_artifact_file):
        glob = other(FINDINGS_ARTIFACT, "*")
        assert not any(fnmatch.fnmatch(name, glob) for name in held), glob
        # The control: that glob does match its own names.
        assert fnmatch.fnmatch(other(FINDINGS_ARTIFACT, 1), glob)
    held_glob = story_inspection.held_artifact_file(FINDINGS_ARTIFACT, "*")
    assert all(fnmatch.fnmatch(name, held_glob) for name in held)
    assert not fnmatch.fnmatch(
        story_inspection.accounting_artifact_file(FINDINGS_ARTIFACT, 1),
        held_glob)
