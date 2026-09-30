"""A pass's grant ends with the pass, and a malformed verdict stops before the
expensive part.

A passing verdict that records small findings it wants fixed re-runs part of
the workflow: a correction pass for fixes to the words alone, a repair pass
for a one-line fix in a file one stage owns. Story-193 tightens what was
loose around the repair pass, and each tightening is driven here as a run
through the sibling module's fixture rather than argued from source:

  * a verdict carrying both kinds of finding routes the correction pass and
    records the repairable findings it defers — in the routed event and in
    the correction record the returning verifier is handed — rather than
    forgetting them;
  * a category the routing table does not define, in either kind, and a
    path ending in a slash, in either kind, escalate before the clean-clone
    check spends a suite run and before any pass record is written;
  * every pass record carries both pass counts at the moment it was
    written, and a record is live only while its attempt and both counts
    still match the state's — so a grant made by one pass ends when a pass
    of the other kind routes, and the record is injected into a prompt only
    while it is live;
  * the repair record says whether a clean-clone check ran on the routing
    verdict, and the statement the entered stage reads says the check passed
    only where it did;
  * a repairable finding under a path the execution rules block for every
    stage is refused before the pass is routed, with or without a
    scope_extension, and accounted as declined with that bound as its
    reason;
  * a pass record the state says exists and that is absent or unreadable
    raises rather than silently granting nothing;
  * `grant_covers` and `_inside` are one reading of what a slash-less value
    covers.

Every absence asserted here carries a demonstration that it can fail:

  * "no clean-clone event and no pass record for a malformed verdict" sits
    beside the same verdict with the defect removed, which runs the check
    and writes the record;
  * "the clean-clone command was never invoked" is read off a log the
    configured command itself appends to, and sits beside a routed run whose
    log does hold an invocation from a fresh clone;
  * "no grant event at the re-entered stage's later invocation" sits beside
    the grant event at the invocation the pass made, in the same run, and
    beside the same file being reverted at the later invocation — an edit the
    grant would have let stand;
  * "None is rendered after a retry moved the attempt" sits beside the
    record being rendered within the pass, in the same run;
  * "the conditional statement does not say the check passed" sits beside
    the same record under the declaring workflow, which does;
  * "a slot paragraph does not assert the suite passed" is checked with the
    same test against the clause the prompts used to carry, which it reports;
  * "a record is not live" is decided by one predicate driven over each way
    a record can stop matching, beside the record that matches.

`.harness/docs/ARCHITECTURE.md` is not asserted on: this story's plan assigns
it to the documenter, which runs after this stage.

The runs below execute the workflow `tests/test_repair_pass.py` builds, for
the reason that module gives: the subject is the mechanism, and the stage
list, the categories and the artifact names are inputs to it, derived from
the fixture's definition and never spelled at an assertion. The cases whose
subject is what this repository ships — the verifier prompts, the
writing-stage slot paragraphs and the two schemas — read the shipped
artifacts and say so.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

import conftest
import harness_config
import schema_validator
import story_coordinator
from agent_runner import AgentResult
import test_inspection_findings_reach_the_story as inspected
from test_inspection_findings_reach_the_story import (  # noqa: F401 - fixtures used by name
    filings,
    no_model,
)
from test_a_repair_reaches_outside_the_storys_scope_within_bounds import (  # noqa: F401 - fixtures used by name
    DECLINED_ARTIFACT,
    EXTENSION,
    EXTENSION_REFUSED,
    ROUTED_FINDING,
    RULES,
    SEALED,
    SEALED_FILE,
    UNSEALED_FILE,
    check,
    confinement_of,
    declined_by_slug,
    inspecting_harness,
    inspecting_target,
    routed_repair,
    without_the_field,
)
from test_a_routed_finding_is_repaired_unless_the_verdict_says_why import (
    CORRECTABLE_PHRASE,
    REPAIRABLE_PHRASE,
    paragraph_containing,
    without_the_permitted_divergence,
)
from test_repair_pass import (  # noqa: F401 - the fixtures the runs configure
    BEHAVIOUR_CATEGORY,
    BEHAVIOUR_FINDING,
    CLEAN_CLONE_PASSED,
    CORRECTABLE,
    CORRECTABLE_FINDING,
    CORRECTION_ARTIFACT,
    CORRECTION_ENTRY,
    CORRECTION_FIELD,
    CORRECTION_GRANT_EVENT,
    CORRECTION_ROUTED,
    DOCUMENTING,
    GRANT_EVENT,
    HISTORY_SCHEMA,
    NOTES,
    NOTES_REPAIRED,
    NOTES_REWORDED_AGAIN,
    PASS,
    PROMPTS,
    RECORD_CATEGORY,
    REPAIR_FIELD,
    REPAIRABLE,
    ROUTED,
    ROUTES,
    STAGE_NAMES,
    UNDECLARED_CATEGORY,
    UNKNOWN_FINDING,
    VERDICT_SCHEMA,
    VERIFYING,
    WORKFLOW,
    WRITING,
    Runner,
    clean_clone_events,
    configure,
    configured_workflow,
    content,
    correcting,
    entry_shape,
    events_of,
    events_of_kind,
    failing_into,
    governed_target,
    grants_per_invocation,
    harness_root,
    history_of,
    read_json,
    read_state,
    record_of,
    record_path,
    repairing,
    run_dir_of,
    shipped_prompts,
    shipped_verifier_prompts,
    the_named_file,
    the_named_file_again,
    write,
    write_json,
)

REPO_ROOT = conftest.HARNESS_ROOT
CORRECTION_SCHEMA = schema_validator.load_schema("correction-pass")

#: The keys story-193 added to the pass records, read off the coordinator's
#: own constants where it exports one so the two cannot drift, and spelled
#: once here where it does not.
DEFERRED = story_coordinator.DEFERRED_REPAIRABLE_FINDINGS
CLEAN_CLONE_CHECKED = story_coordinator.CLEAN_CLONE_CHECKED
CORRECTION_COUNT = "correction_pass_count"
REPAIR_COUNT = "repair_pass_count"

#: The event the revert check appends when it undoes an edit, as the
#: execution-history schema enumerates it.
REVERTED_EVENT = "revert-check-reverted"
STAGE_STARTED = "stage-started"

#: The clause the four writing-stage slot paragraphs carried before this
#: story, asserted unconditionally where the code ships under a workflow that
#: may declare no clean-clone check at all. Kept as the control for the
#: absence assertion over the shipped paragraphs.
THE_REMOVED_CLAUSE = "suite also passed where the code ships"
SLOT_OPENING = "Repair pass — present only"

#: What the repair statement says when a clean-clone check ran, and must not
#: say when none did.
CHECK_PASSED = "clean-clone check on that verdict passed"
CHECK_AGAIN = "runs the clean-clone check again"


def both_kinds(*correctable: dict, repairable: tuple[dict, ...]) -> dict:
    """A passing verdict carrying both kinds of finding."""
    return {**PASS,
            CORRECTABLE: [dict(one) for one in correctable],
            REPAIRABLE: [dict(one) for one in repairable]}


def correction_record_path(run_dir: Path, number: int = 1) -> Path:
    return run_dir / story_coordinator.correction_pass_result_file(
        CORRECTION_ARTIFACT, number)


def correction_record_of(run_dir: Path, number: int = 1) -> dict:
    return read_json(correction_record_path(run_dir, number))


def drive(target_root: Path, harness: Path, verdicts: list[dict],
          edits: list | None = None, documenter_edits: list | None = None):
    """One run, returning its exit code, its runner and its run directory —
    the sibling's driver with a runner that can also edit as the documenter."""
    runner = DocumentingRunner(target_root, verdicts, edits, documenter_edits)
    code = story_coordinator.run_story(
        "story-001", harness, target_root, runner)
    return code, runner, run_dir_of(target_root)


class DocumentingRunner(Runner):
    """The sibling's fake runner, with the documenting stage able to edit.

    `documenter_edits` is the list of edit functions the documenting stage's
    invocations make, in order — None for an invocation that changes nothing
    — so a run that re-enters the documenter twice, once by each kind of
    pass, can have it edit the same governed file both times and the two
    dispositions be compared.
    """

    def __init__(self, target_root: Path, verdicts: list[dict],
                 edits: list | None = None,
                 documenter_edits: list | None = None):
        super().__init__(target_root, verdicts, edits)
        self.documenter_edits = list(documenter_edits or [])

    def __call__(self, prompt, *, stage, cwd, **keywords):
        if stage != DOCUMENTING:
            return super().__call__(prompt, stage=stage, cwd=cwd, **keywords)
        self.calls.append(stage)
        self.prompts.setdefault(stage, []).append(prompt)
        self.guidance.setdefault(stage, []).append(
            conftest.guidance_in_force(self.run_dir))
        edit = self.documenter_edits.pop(0) if self.documenter_edits else None
        record = (edit(Path(cwd)) if edit
                  else {"modified": [], "created": [], "deleted": []})
        (self.run_dir / conftest.DOCUMENTATION_REPORT).write_text(
            "Documented the work.\n", encoding="utf-8")
        write_json(self.run_dir / conftest.DOCUMENTER_CHANGED_FILES, record)
        return AgentResult(ok=True, result_text=f"{stage} done")


def per_invocation(run_dir: Path, stage: str, kind: str) -> list[list[str]]:
    """The messages of every `kind` event `stage` appended, grouped by the
    invocation of that stage they were appended in — the sibling's
    `grants_per_invocation` for an event that names no granted path, so an
    assertion can say what happened at the stage's second run as distinct
    from its first."""
    grouped: list[list[str]] = []
    for entry in history_of(run_dir):
        if entry.get("stage") != stage:
            continue
        if entry["event"] == STAGE_STARTED:
            grouped.append([])
        elif entry["event"] == kind and grouped:
            grouped[-1].append(entry["message"])
    return grouped


def probe_harness_named(tmp_path: Path, target_root: Path, name: str,
                        mutate, rules: dict | None = None) -> Path:
    """A harness root carrying the sibling's definition with one stage
    mutated, and `target_root` configured to run it. `mutate` is handed every
    stage and decides which it changes."""
    workflow = json.loads(json.dumps(WORKFLOW))
    for stage in workflow["stages"]:
        mutate(stage)
    workflow["name"] = name
    root = conftest.materialize_workflow(workflow, tmp_path / name,
                                         prompts=PROMPTS, rules=rules)
    configure(target_root, workflow=name)
    return root


# --------------------------------------------------------------------------
# A verdict carrying both kinds routes the correction pass and defers the rest
# --------------------------------------------------------------------------


@pytest.fixture
def both_kinds_run(target_root, harness_root):
    """A passing verdict carrying one correctable and one repairable finding,
    then a verdict carrying nothing."""
    return drive(target_root, harness_root,
                 [both_kinds(CORRECTABLE_FINDING,
                             repairable=(BEHAVIOUR_FINDING,)), PASS])


def test_both_kinds_route_the_correction_pass_and_no_repair_pass(both_kinds_run):
    code, runner, run_dir = both_kinds_run
    assert code == 0
    assert read_state(run_dir)["status"] == "completed"
    state = read_state(run_dir)
    assert state["correction_pass_count"] == 1
    assert state["repair_pass_count"] == 0
    events = events_of(run_dir)
    assert CORRECTION_ROUTED in events
    assert ROUTED not in events
    assert runner.calls == entry_shape(CORRECTION_ENTRY)
    assert not record_path(run_dir).exists()


def test_the_routed_event_names_each_deferred_finding_by_its_location(
    both_kinds_run,
):
    _, _, run_dir = both_kinds_run
    (routed,) = events_of_kind(run_dir, CORRECTION_ROUTED)
    assert "deferring" in routed["message"]
    assert BEHAVIOUR_FINDING["location"] in routed["message"]
    assert schema_validator.validate(history_of(run_dir), HISTORY_SCHEMA) == []


def test_a_verdict_carrying_one_kind_defers_nothing_in_its_event(
    target_root, harness_root,
):
    """Control: the same event for a verdict carrying correctable findings
    alone names no deferral, so the clause above is the repairable finding's
    doing."""
    _, _, run_dir = drive(target_root, harness_root,
                          [correcting(CORRECTABLE_FINDING), PASS])
    (routed,) = events_of_kind(run_dir, CORRECTION_ROUTED)
    assert "deferring" not in routed["message"]
    assert BEHAVIOUR_FINDING["location"] not in routed["message"]
    assert DEFERRED not in correction_record_of(run_dir)


def test_the_correction_record_carries_the_deferred_findings_verbatim(
    both_kinds_run,
):
    _, _, run_dir = both_kinds_run
    record = correction_record_of(run_dir)
    assert record[DEFERRED] == [BEHAVIOUR_FINDING]
    assert record["findings"] == [CORRECTABLE_FINDING]
    assert schema_validator.validate(record, CORRECTION_SCHEMA) == []


def test_the_returning_verifier_is_handed_the_deferred_findings(both_kinds_run):
    """The record the coordinator wrote, deferred findings and all, is the
    text the verifier's second prompt carries; its first carried nothing."""
    _, runner, run_dir = both_kinds_run
    first, second = runner.prompts[VERIFYING]
    assert BEHAVIOUR_FINDING["finding"] not in first
    assert correction_record_path(run_dir).read_text(encoding="utf-8") in second
    assert BEHAVIOUR_FINDING["finding"] in second
    assert DEFERRED in second


# --------------------------------------------------------------------------
# A malformed verdict escalates before the clean-clone check
# --------------------------------------------------------------------------


def assert_escalated_before_the_check(run, naming: str) -> None:
    """What every malformed verdict has in common: the run escalated naming
    the defect, no clean-clone event of any kind was appended, no pass routed,
    no pass record of either kind was written, and nothing a retry spends was
    spent."""
    code, runner, run_dir = run
    assert code == 2
    assert runner.calls == STAGE_NAMES
    assert read_state(run_dir)["status"] == "escalated"
    last = history_of(run_dir)[-1]
    assert last["event"] == "escalated"
    assert naming in last["message"]
    assert naming in (run_dir / "escalation-summary.md").read_text(
        encoding="utf-8")
    events = events_of(run_dir)
    assert clean_clone_events(run_dir) == []
    assert CORRECTION_ROUTED not in events
    assert ROUTED not in events
    assert not correction_record_path(run_dir).exists()
    assert not record_path(run_dir).exists()
    state = read_state(run_dir)
    assert state["retry_count"] == 0
    assert state["correction_pass_count"] == 0
    assert state["repair_pass_count"] == 0
    assert not (run_dir / "attempts").exists()


def test_a_defined_correctable_beside_an_undefined_repairable_escalates(
    target_root, harness_root,
):
    """The verdict that used to take the correction pass and silently forget
    its repairable half: now it stops naming the unknown category, before the
    correction pass routes and before the check runs."""
    run = drive(target_root, harness_root,
                [both_kinds(CORRECTABLE_FINDING, repairable=(UNKNOWN_FINDING,))])
    assert_escalated_before_the_check(run, UNDECLARED_CATEGORY)
    _, _, run_dir = run
    assert "repairable" in history_of(run_dir)[-1]["message"]


def test_the_same_verdict_with_the_category_defined_routes_and_runs_the_check(
    both_kinds_run,
):
    """Control for the absences above: with the repairable finding's category
    defined the correction pass routes, its record is written and the
    clean-clone check runs after it."""
    _, _, run_dir = both_kinds_run
    events = events_of(run_dir)
    assert CORRECTION_ROUTED in events
    assert clean_clone_events(run_dir) == [CLEAN_CLONE_PASSED]
    assert events.index(CORRECTION_ROUTED) < events.index(CLEAN_CLONE_PASSED)
    assert correction_record_path(run_dir).is_file()


def cwd_logging_command(log: Path) -> str:
    """A test command that appends the directory it ran in to `log`, so
    whether the clean-clone check invoked it is read off what the command
    itself wrote rather than off an event."""
    return f"sh -c 'pwd >> {log}'"


def clone_invocations(log: Path) -> list[str]:
    """The invocations the log records from inside a fresh clone, told apart
    by the prefix the coordinator builds its clean-clone scratch under."""
    if not log.is_file():
        return []
    return [line for line in log.read_text(encoding="utf-8").splitlines()
            if "l5-clean-clone-" in line]


def test_an_undefined_repairable_category_never_invokes_the_clean_clone_command(
    target_root, harness_root, tmp_path,
):
    log = tmp_path / "invocations.txt"
    configure(target_root, test_command=cwd_logging_command(log))
    run = drive(target_root, harness_root, [repairing(UNKNOWN_FINDING)])
    assert_escalated_before_the_check(run, UNDECLARED_CATEGORY)
    assert clone_invocations(log) == []


def test_the_same_command_is_invoked_from_a_clone_when_the_verdict_routes(
    target_root, harness_root, tmp_path,
):
    """Control: the identical command under a well-formed verdict is invoked
    from inside a fresh clone, so the empty list above is the escalation's
    doing and not a log nothing writes to."""
    log = tmp_path / "invocations.txt"
    configure(target_root, test_command=cwd_logging_command(log))
    code, _, run_dir = drive(target_root, harness_root,
                             [repairing(BEHAVIOUR_FINDING), PASS])
    assert code == 0
    assert clone_invocations(log) != []
    assert CLEAN_CLONE_PASSED in events_of(run_dir)


DIRECTORY_SHAPED_REPAIRABLE = {**BEHAVIOUR_FINDING, "path": "docs/",
                               "location": "docs/ - every page"}
DIRECTORY_SHAPED_CORRECTABLE = {**CORRECTABLE_FINDING, "path": "src/",
                                "location": "src/ - every module comment"}


def test_a_repairable_path_ending_in_a_slash_escalates_naming_the_path(
    target_root, harness_root,
):
    run = drive(target_root, harness_root,
                [repairing(DIRECTORY_SHAPED_REPAIRABLE)])
    assert_escalated_before_the_check(run, DIRECTORY_SHAPED_REPAIRABLE["path"])
    _, _, run_dir = run
    assert "trailing slash" in history_of(run_dir)[-1]["message"]


def test_a_correctable_path_ending_in_a_slash_escalates_naming_the_path(
    target_root, harness_root,
):
    run = drive(target_root, harness_root,
                [correcting(DIRECTORY_SHAPED_CORRECTABLE)])
    assert_escalated_before_the_check(run, DIRECTORY_SHAPED_CORRECTABLE["path"])


def test_a_directory_shaped_path_among_both_kinds_escalates_too(
    target_root, harness_root,
):
    """The verdict that would otherwise route the correction pass: the slash
    on its repairable half stops it before the pass and before the check."""
    run = drive(target_root, harness_root,
                [both_kinds(CORRECTABLE_FINDING,
                            repairable=(DIRECTORY_SHAPED_REPAIRABLE,))])
    assert_escalated_before_the_check(run, DIRECTORY_SHAPED_REPAIRABLE["path"])


def test_the_same_findings_without_the_slash_route(target_root, harness_root):
    """Control: the identical finding naming one file routes, writes its
    record and runs the check — so the escalations above are the slash's."""
    one_file = {**DIRECTORY_SHAPED_REPAIRABLE, "path": "docs/page.md"}
    code, _, run_dir = drive(target_root, harness_root,
                             [repairing(one_file), PASS])
    assert code == 0
    assert ROUTED in events_of(run_dir)
    assert record_of(run_dir)["findings"] == [
        {**one_file, "stage": ROUTES[one_file["category"]]["stage"]}]
    assert CLEAN_CLONE_PASSED in events_of(run_dir)


#: Directory-shaped paths a bound would also refuse, driven under the
#: scope-extension module's rules. The first is the blocked prefix itself,
#: slash-terminated and carrying no extension; the second is a directory
#: outside the writing stage's confinement, carrying an extension the
#: restriction bound refuses. A slash-terminated value is a prefix every bound
#: reads as under it, so asked of the verdict after refusals the
#: directory-shaped question never saw these: each was refused with an event
#: and the run completed, where the prompts say the slash stops the run. The
#: third is the second naming one file, the control that shows the restriction
#: bound is what would have taken it.
SEALED_DIRECTORY = {**BEHAVIOUR_FINDING, "path": SEALED,
                    "location": f"{SEALED} - every page",
                    "category": RECORD_CATEGORY}
RESTRICTED_DIRECTORY = {
    **BEHAVIOUR_FINDING, "path": "docs/", "location": "docs/ - every page",
    EXTENSION: "MARKER-RESTRICTED-REASON the story changed the rule; "
               "docs/ states it",
}
RESTRICTED_FILE = {**RESTRICTED_DIRECTORY, "path": "docs/page.md",
                   "location": "docs/page.md - the first line"}


def test_the_directory_shaped_findings_are_ones_a_bound_would_refuse():
    """The premise: without the fix, each directory-shaped finding is taken
    by a bound. The blocked-path bound takes the sealed prefix with no
    extension in play; the restriction bound takes the directory the writing
    stage's confinement governs, and takes the one-file control the same way.
    """
    assert EXTENSION not in SEALED_DIRECTORY
    assert SEALED_DIRECTORY["path"].endswith("/")
    assert story_coordinator.is_blocked(SEALED, RULES["blocked_paths"])
    (refused,) = check(SEALED_DIRECTORY).refused
    assert SEALED in refused.bound
    assert "execution rules" in refused.bound

    assert EXTENSION in RESTRICTED_DIRECTORY
    assert RESTRICTED_DIRECTORY["path"].endswith("/")
    assert ROUTES[RESTRICTED_DIRECTORY["category"]]["stage"] == WRITING
    assert not story_coordinator.is_blocked(RESTRICTED_DIRECTORY["path"],
                                            RULES["blocked_paths"])
    confinement = confinement_of(WRITING)
    assert confinement.governs(RESTRICTED_DIRECTORY["path"])
    assert confinement.governs(RESTRICTED_FILE["path"])
    for finding in (RESTRICTED_DIRECTORY, RESTRICTED_FILE):
        (refused,) = check(finding).refused
        assert confinement.wording in refused.bound
    assert not RESTRICTED_FILE["path"].endswith("/")


def test_a_slash_terminated_path_under_the_blocked_prefix_escalates_rather_than_refusing(
    target_root, sealed_harness,
):
    """The directory-shaped question is asked of the findings as the verifier
    wrote them: the blocked prefix itself, slash-terminated, escalates naming
    the path with no refusal event, no pass routed and no record written. The
    slash-less SEALED_PLAIN runs below, which are refused with an event and
    complete, are the control."""
    run = drive(target_root, sealed_harness, [repairing(SEALED_DIRECTORY)])
    assert_escalated_before_the_check(run, SEALED_DIRECTORY["path"])
    _, _, run_dir = run
    assert events_of_kind(run_dir, EXTENSION_REFUSED) == []
    assert "trailing slash" in history_of(run_dir)[-1]["message"]


def test_a_slash_terminated_path_the_restriction_governs_escalates_rather_than_refusing(
    target_root, sealed_harness,
):
    """The same for an extension the restriction bound would refuse: the
    slash stops the run before the bounds are asked."""
    run = drive(target_root, sealed_harness, [repairing(RESTRICTED_DIRECTORY)])
    assert_escalated_before_the_check(run, RESTRICTED_DIRECTORY["path"])
    _, _, run_dir = run
    assert events_of_kind(run_dir, EXTENSION_REFUSED) == []
    assert "trailing slash" in history_of(run_dir)[-1]["message"]


def test_the_same_extension_naming_one_file_is_refused_by_the_restriction(
    target_root, sealed_harness,
):
    """Control for the absence of a refusal event above: the identical
    extension naming one file under the confinement is refused by the
    restriction bound, with the event, and the run completes."""
    code, runner, run_dir = drive(target_root, sealed_harness,
                                  [repairing(RESTRICTED_FILE)])
    assert code == 0, runner.calls
    assert runner.calls == STAGE_NAMES
    assert read_state(run_dir)["status"] == "completed"
    assert "escalated" not in events_of(run_dir)
    (refused,) = events_of_kind(run_dir, EXTENSION_REFUSED)
    assert refused["stage"] == VERIFYING
    assert confinement_of(WRITING).wording in refused["message"]
    assert RESTRICTED_FILE["path"] in refused["message"]
    assert ROUTED not in events_of(run_dir)
    assert not record_path(run_dir).exists()


def test_the_malformed_check_is_decided_as_a_function_over_the_fixtures_table():
    """The same defects said directly: an unknown category in either kind is
    named, a slash-terminated path in either kind is named, a category is
    asked before a path, and well-formed findings of both kinds pass."""
    malformed = story_coordinator._malformed_findings
    assert malformed([CORRECTABLE_FINDING], [BEHAVIOUR_FINDING], ROUTES) is None
    assert malformed([], [], ROUTES) is None

    unknown = malformed([CORRECTABLE_FINDING], [UNKNOWN_FINDING], ROUTES)
    assert UNDECLARED_CATEGORY in unknown
    assert "repairable" in unknown
    unknown = malformed([{**CORRECTABLE_FINDING, "category": UNDECLARED_CATEGORY}],
                        [], ROUTES)
    assert UNDECLARED_CATEGORY in unknown
    assert "correctable" in unknown

    shaped = malformed([], [DIRECTORY_SHAPED_REPAIRABLE], ROUTES)
    assert DIRECTORY_SHAPED_REPAIRABLE["path"] in shaped
    assert "repairable" in shaped
    shaped = malformed([DIRECTORY_SHAPED_CORRECTABLE], [], ROUTES)
    assert DIRECTORY_SHAPED_CORRECTABLE["path"] in shaped
    assert "correctable" in shaped

    # A verdict carrying both defects is refused for the category, which is
    # asked first, and the path is not what the wording names.
    both = malformed([], [{**DIRECTORY_SHAPED_REPAIRABLE,
                           "category": UNDECLARED_CATEGORY}], ROUTES)
    assert UNDECLARED_CATEGORY in both
    assert DIRECTORY_SHAPED_REPAIRABLE["path"] not in both


# --------------------------------------------------------------------------
# Every pass record carries both counts, and a record is live only while
# its attempt and both counts match the state's
# --------------------------------------------------------------------------


def test_every_record_a_run_writes_carries_both_counts_and_validates(
    target_root, harness_root,
):
    """A correction pass then a repair pass: the correction record was
    written when the run had taken one correction pass and no repair pass,
    the repair record when it had taken one of each."""
    code, _, run_dir = drive(
        target_root, harness_root,
        [correcting(CORRECTABLE_FINDING), repairing(BEHAVIOUR_FINDING), PASS])
    assert code == 0
    correction = correction_record_of(run_dir)
    repair = record_of(run_dir)
    assert (correction[CORRECTION_COUNT], correction[REPAIR_COUNT]) == (1, 0)
    assert (repair[CORRECTION_COUNT], repair[REPAIR_COUNT]) == (1, 1)
    assert repair[CLEAN_CLONE_CHECKED] is True
    assert CLEAN_CLONE_CHECKED not in correction
    assert DEFERRED not in repair
    assert schema_validator.validate(correction, CORRECTION_SCHEMA) == []
    assert schema_validator.validate(repair, CORRECTION_SCHEMA) == []


@pytest.mark.parametrize("key, wrong", [
    (CORRECTION_COUNT, "1"),
    (REPAIR_COUNT, 1.5),
    (DEFERRED, {"path": NOTES}),
    (CLEAN_CLONE_CHECKED, "yes"),
], ids=["a count that is a string", "a count that is a fraction",
        "deferred findings that are not a list", "a flag that is a word"])
def test_the_schema_refuses_each_new_key_in_the_wrong_shape(key, wrong):
    """Control for the validations above: the schema declares the new keys
    with a type, so a record carrying one in the wrong shape is reported."""
    record = {"pass": 1, "attempt": 1, "stage": CORRECTION_ENTRY,
              "findings": [CORRECTABLE_FINDING],
              CORRECTION_COUNT: 1, REPAIR_COUNT: 0,
              "statement": story_coordinator.correction_pass_statement(
                  CORRECTION_ENTRY)}
    assert schema_validator.validate(record, CORRECTION_SCHEMA) == []
    assert schema_validator.validate({**record, key: wrong}, CORRECTION_SCHEMA), key


def state_with(correction: int, repair: int, retries: int = 0):
    state = story_coordinator.RunState(story_id="story-001",
                                       branch="story/story-001")
    state.correction_pass_count = correction
    state.repair_pass_count = repair
    state.retry_count = retries
    return state


def test_one_predicate_decides_whether_a_record_is_live():
    """The record matches when its attempt is the one running and both
    recorded counts equal the state's; each of the three moving is enough to
    end it, and a record carrying no counts is not live."""
    live = story_coordinator.pass_record_is_live
    record = {"attempt": 1, CORRECTION_COUNT: 1, REPAIR_COUNT: 1}
    assert live(record, state_with(1, 1), 1)
    assert not live(record, state_with(1, 1), 2)
    assert not live(record, state_with(2, 1), 1)
    assert not live(record, state_with(1, 2), 1)
    assert not live({"attempt": 1}, state_with(0, 0), 1)
    assert not live({"attempt": 1, CORRECTION_COUNT: 1}, state_with(1, 0), 1)


@pytest.fixture
def documenting_harness(tmp_path, governed_target):
    """The sibling's definition with the documenting stage confined to a
    directory the notes are outside of, and a revert check declared on it, so
    a grant to the documenter is observable at run level: an ungranted edit
    to the notes is decided by the check, a granted one is exempt from it."""
    def confine_the_documenter(stage: dict) -> None:
        if stage["name"] == DOCUMENTING:
            stage["may_only_change"] = ["docs/"]
            stage["revert_check"] = {"result": "documenter-revert-probe.json",
                                     "baseline": "documenter-baseline",
                                     "discarded": "documenter-discarded"}
    return probe_harness_named(tmp_path, governed_target,
                               "pass-bounded-grants", confine_the_documenter)


#: The correctable finding the correction pass grants the documenter: the
#: notes, outside the documenter's confinement above.
NOTES_CORRECTABLE = {**CORRECTABLE_FINDING, "path": NOTES,
                     "location": f"{NOTES} - the second paragraph"}
#: The repairable finding the repair pass grants the documenter: the same
#: file, under the category the table routes to the documenting stage.
NOTES_REPAIRABLE = {**BEHAVIOUR_FINDING, "category": RECORD_CATEGORY}


def test_the_premises_of_the_two_sequences():
    """The correction pass enters at the documenting stage, the record
    category routes there too, the behaviour category routes earlier, and
    the notes are outside the confinement the probe gives the documenter."""
    assert CORRECTION_ENTRY == DOCUMENTING
    assert ROUTES[RECORD_CATEGORY]["stage"] == DOCUMENTING
    assert ROUTES[BEHAVIOUR_CATEGORY]["stage"] == WRITING
    assert STAGE_NAMES.index(WRITING) < STAGE_NAMES.index(DOCUMENTING)
    assert not NOTES.startswith("docs/")


@pytest.fixture
def correction_then_repair(governed_target, documenting_harness):
    """A correction pass granting the documenter the notes, then a repair
    pass in the same attempt re-entering at the writing stage and running
    forward through the documenter. The documenter edits the notes on both
    re-entries."""
    return drive(governed_target, documenting_harness,
                 [correcting(NOTES_CORRECTABLE), repairing(BEHAVIOUR_FINDING),
                  PASS],
                 documenter_edits=[None, the_named_file, the_named_file_again])


def test_a_correction_grant_ends_when_a_repair_pass_routes(correction_then_repair):
    """At the documenter's run inside the correction pass the notes were
    granted and the edit landed; at its run inside the repair pass the same
    file was governed — no grant event, and the edit reverted."""
    code, runner, run_dir = correction_then_repair
    assert code == 0, runner.calls
    assert runner.calls == [*STAGE_NAMES,
                            *STAGE_NAMES[STAGE_NAMES.index(CORRECTION_ENTRY):],
                            *STAGE_NAMES[STAGE_NAMES.index(WRITING):]]
    assert runner.calls.count(DOCUMENTING) == 3
    state = read_state(run_dir)
    assert state["correction_pass_count"] == 1
    assert state["repair_pass_count"] == 1
    assert state["retry_count"] == 0

    assert grants_per_invocation(run_dir, DOCUMENTING, CORRECTION_GRANT_EVENT) \
        == [[], [NOTES], []]
    assert grants_per_invocation(run_dir, DOCUMENTING, GRANT_EVENT) == [[], [], []]
    reverted = per_invocation(run_dir, DOCUMENTING, REVERTED_EVENT)
    assert [len(one) for one in reverted] == [0, 0, 1]
    assert NOTES in reverted[2][0]
    # The later edit's words are under the discarded directory and not in
    # the tree: the revert restores what the stage originally found, which
    # is the baseline's first-seen rule and not this story's subject.
    assert content(run_dir, NOTES) != NOTES_REWORDED_AGAIN
    assert (run_dir / "documenter-discarded" / NOTES).read_text(
        encoding="utf-8") == NOTES_REWORDED_AGAIN
    assert schema_validator.validate(history_of(run_dir), HISTORY_SCHEMA) == []


def test_the_reader_agrees_with_the_run_about_the_documenters_third_run(
    correction_then_repair,
):
    """The exempt list the checks read, asked directly for the state the
    documenter's third run ran under: the correction record grants nothing,
    and the control is the state the second run ran under, where it does."""
    _, _, run_dir = correction_then_repair
    stages = WORKFLOW["stages"]
    assert story_coordinator.correction_pass_grants(
        run_dir, stages, state_with(1, 1), DOCUMENTING, 1) == []
    assert story_coordinator.correction_pass_grants(
        run_dir, stages, state_with(1, 0), DOCUMENTING, 1) == [NOTES]


@pytest.fixture
def repair_then_correction(governed_target, documenting_harness):
    """The mirror: a repair pass granting the documenter the notes, then a
    correction pass in the same attempt re-entering at the documenter."""
    return drive(governed_target, documenting_harness,
                 [repairing(NOTES_REPAIRABLE), correcting(CORRECTABLE_FINDING),
                  PASS],
                 documenter_edits=[None, the_named_file, the_named_file_again])


def test_a_repair_grant_ends_when_a_correction_pass_routes(repair_then_correction):
    code, runner, run_dir = repair_then_correction
    assert code == 0, runner.calls
    assert runner.calls == [*STAGE_NAMES,
                            *STAGE_NAMES[STAGE_NAMES.index(DOCUMENTING):],
                            *STAGE_NAMES[STAGE_NAMES.index(CORRECTION_ENTRY):]]
    state = read_state(run_dir)
    assert state["correction_pass_count"] == 1
    assert state["repair_pass_count"] == 1

    assert grants_per_invocation(run_dir, DOCUMENTING, GRANT_EVENT) \
        == [[], [NOTES], []]
    assert grants_per_invocation(run_dir, DOCUMENTING, CORRECTION_GRANT_EVENT) \
        == [[], [], [CORRECTABLE_FINDING["path"]]]
    reverted = per_invocation(run_dir, DOCUMENTING, REVERTED_EVENT)
    assert [len(one) for one in reverted] == [0, 0, 1]
    assert NOTES in reverted[2][0]
    assert content(run_dir, NOTES) != NOTES_REWORDED_AGAIN
    assert (run_dir / "documenter-discarded" / NOTES).read_text(
        encoding="utf-8") == NOTES_REWORDED_AGAIN

    stages = WORKFLOW["stages"]
    assert story_coordinator.repair_pass_grants(
        run_dir, stages, state_with(1, 1), DOCUMENTING, 1) == []
    assert story_coordinator.repair_pass_grants(
        run_dir, stages, state_with(0, 1), DOCUMENTING, 1) == [NOTES]


def test_a_grant_within_its_own_pass_still_lands(governed_target, documenting_harness):
    """Control for the reverts above: the same documenter edit on a pass no
    other pass follows lands and is never decided, so the reverts are the
    later pass's doing and not the confinement refusing every edit."""
    code, _, run_dir = drive(governed_target, documenting_harness,
                             [correcting(NOTES_CORRECTABLE), PASS],
                             documenter_edits=[None, the_named_file])
    assert code == 0
    assert per_invocation(run_dir, DOCUMENTING, REVERTED_EVENT) == [[], []]
    assert content(run_dir, NOTES) == NOTES_REPAIRED


# --------------------------------------------------------------------------
# The prompt injection follows the same liveness
# --------------------------------------------------------------------------


def rendered_none(field: str, prompt: str) -> bool:
    return f"{field}:\nNone" in prompt


def test_a_stage_after_a_retry_is_told_none_and_a_stage_within_the_pass_the_record(
    target_root, harness_root,
):
    """A correction pass, then a failing verdict routing a retry that moves
    the attempt: every stage on the later attempt renders None for both
    fields, where the documenter's run within the pass rendered the record."""
    code, runner, run_dir = drive(
        target_root, harness_root,
        [correcting(CORRECTABLE_FINDING), failing_into(BEHAVIOUR_CATEGORY), PASS])
    assert code == 0, runner.calls
    assert read_state(run_dir)["retry_count"] == 1
    record = correction_record_path(run_dir).read_text(encoding="utf-8")

    first, within_the_pass, after_the_retry = runner.prompts[CORRECTION_ENTRY]
    assert rendered_none(CORRECTION_FIELD, first)
    assert record in within_the_pass
    assert not rendered_none(CORRECTION_FIELD, within_the_pass)
    assert rendered_none(CORRECTION_FIELD, after_the_retry)
    assert CORRECTABLE_FINDING["finding"] not in after_the_retry
    for stage in STAGE_NAMES:
        last = runner.prompts[stage][-1]
        assert rendered_none(CORRECTION_FIELD, last), stage
        assert rendered_none(REPAIR_FIELD, last), stage


def test_the_repair_record_is_rendered_within_its_pass_and_not_after_a_retry(
    governed_target, harness_root,
):
    code, runner, run_dir = drive(
        governed_target, harness_root,
        [repairing(BEHAVIOUR_FINDING), failing_into(BEHAVIOUR_CATEGORY), PASS])
    assert code == 0, runner.calls
    record = record_path(run_dir).read_text(encoding="utf-8")
    first, within_the_pass, after_the_retry = runner.prompts[WRITING]
    assert rendered_none(REPAIR_FIELD, first)
    assert record in within_the_pass
    assert rendered_none(REPAIR_FIELD, after_the_retry)
    assert BEHAVIOUR_FINDING["finding"] not in after_the_retry


def test_a_correction_record_is_not_rendered_into_the_repair_pass_that_follows(
    target_root, harness_root,
):
    """Within one attempt: after the repair pass routes, the correction
    record is no longer live, so the stages the repair pass runs are told
    None for it and the repair record for the other field."""
    code, runner, run_dir = drive(
        target_root, harness_root,
        [correcting(CORRECTABLE_FINDING), repairing(BEHAVIOUR_FINDING), PASS])
    assert code == 0, runner.calls
    correction = correction_record_path(run_dir).read_text(encoding="utf-8")
    repair = record_path(run_dir).read_text(encoding="utf-8")
    first, within_correction, within_repair = runner.prompts[DOCUMENTING]
    assert correction in within_correction
    assert rendered_none(REPAIR_FIELD, within_correction)
    assert rendered_none(CORRECTION_FIELD, within_repair)
    assert repair in within_repair


# --------------------------------------------------------------------------
# The repair record says whether a clean-clone check ran
# --------------------------------------------------------------------------


def test_the_statement_is_conditional_on_the_flag():
    checked = story_coordinator.repair_pass_statement(WRITING, clean_clone_checked=True)
    unchecked = story_coordinator.repair_pass_statement(WRITING, clean_clone_checked=False)
    assert checked == story_coordinator.repair_pass_statement(WRITING)
    assert CHECK_PASSED in checked
    assert CHECK_AGAIN in checked
    assert CHECK_PASSED not in unchecked
    assert CHECK_AGAIN not in unchecked
    assert "No clean-clone check ran" in unchecked
    # What both say, whatever the flag.
    for statement in (checked, unchecked):
        assert WRITING in statement
        assert "not a retry" in statement
        assert "no retry budget was spent" in statement
        assert "not a licence to revisit the work" in statement
        assert "nothing else" in statement


@pytest.fixture
def undeclared_check_harness(tmp_path, target_root):
    """The sibling's definition with the clean-clone declaration removed
    from the verifier and the repair pass left declared — the shape the
    pre-flight still accepts."""
    return probe_harness_named(
        tmp_path, target_root, "repair-pass-without-clean-clone",
        lambda stage: stage.pop("clean_clone", None))


def test_under_no_clean_clone_declaration_the_record_says_none_ran(
    target_root, undeclared_check_harness,
):
    code, runner, run_dir = drive(target_root, undeclared_check_harness,
                                  [repairing(BEHAVIOUR_FINDING), PASS])
    assert code == 0, runner.calls
    assert clean_clone_events(run_dir) == []
    record = record_of(run_dir)
    assert record[CLEAN_CLONE_CHECKED] is False
    assert record["statement"] == story_coordinator.repair_pass_statement(
        record["stage"], clean_clone_checked=False)
    assert CHECK_PASSED not in record["statement"]
    (routed,) = events_of_kind(run_dir, ROUTED)
    assert "no clean-clone check declared" in routed["message"]
    assert "clean-clone check passed" not in routed["message"]
    assert schema_validator.validate(record, CORRECTION_SCHEMA) == []
    # The entered stage reads that record, and so that statement: the file's
    # own text is what the slot renders, so the statement is looked for as
    # the file carries it and by a phrase JSON does not escape.
    _, second = runner.prompts[WRITING]
    assert record_path(run_dir).read_text(encoding="utf-8") in second
    assert "No clean-clone check ran on that verdict" in second
    assert CHECK_PASSED not in second


def test_under_the_declaring_workflow_the_record_says_the_check_passed(
    target_root, harness_root,
):
    """Control: the identical verdict under the declaring definition records
    the check as run and the statement says it passed."""
    code, runner, run_dir = drive(target_root, harness_root,
                                  [repairing(BEHAVIOUR_FINDING), PASS])
    assert code == 0
    record = record_of(run_dir)
    assert record[CLEAN_CLONE_CHECKED] is True
    assert record["statement"] == story_coordinator.repair_pass_statement(
        record["stage"], clean_clone_checked=True)
    assert CHECK_PASSED in record["statement"]
    (routed,) = events_of_kind(run_dir, ROUTED)
    assert "clean-clone check passed" in routed["message"]
    _, second = runner.prompts[WRITING]
    assert record_path(run_dir).read_text(encoding="utf-8") in second
    assert CHECK_PASSED in second
    assert "No clean-clone check ran on that verdict" not in second


def writing_stage_prompts() -> list[str]:
    """Every shipped stage prompt that is not a verifier's: the stages a
    repair pass enters or runs forward through."""
    verifiers = set(shipped_verifier_prompts())
    found = [prompt for prompt in shipped_prompts() if prompt not in verifiers]
    assert found
    return found


@pytest.mark.parametrize("prompt", writing_stage_prompts())
def test_the_shipped_slot_paragraph_does_not_assert_the_suite_passed(prompt):
    text = (REPO_ROOT / "prompts" / prompt).read_text(encoding="utf-8")
    paragraph = paragraph_containing(text, SLOT_OPENING)
    assert f"{{{{{REPAIR_FIELD}}}}}" in paragraph
    assert THE_REMOVED_CLAUSE not in paragraph
    assert "statement" in paragraph
    assert "only where it was run" in paragraph


def test_the_clause_check_reports_the_wording_the_paragraphs_used_to_carry():
    """Control: the same test over the pre-story paragraph reports the
    clause, so the absence above is the edit's doing."""
    before = ("Repair pass — present only when you are running because a "
              "passing verification, on a tree whose suite also passed where "
              "the code ships, recorded findings it judges correct.")
    assert THE_REMOVED_CLAUSE in paragraph_containing(before, SLOT_OPENING)


# --------------------------------------------------------------------------
# A blocked path is refused of every repairable finding
# --------------------------------------------------------------------------


@pytest.fixture
def sealed_harness(tmp_path) -> Path:
    """The sibling's definition under the scope-extension module's rules,
    which block a prefix the shipped rules do not."""
    return conftest.materialize_workflow(
        WORKFLOW, tmp_path / "sealed-harness", prompts=PROMPTS, rules=RULES)


#: A repairable finding under the blocked prefix, carrying no extension, and
#: the same finding out from under it.
SEALED_PLAIN = {**BEHAVIOUR_FINDING, "path": SEALED_FILE,
                "location": f"{SEALED_FILE} - the first line",
                "category": RECORD_CATEGORY}
UNSEALED_PLAIN = {**SEALED_PLAIN, "path": UNSEALED_FILE,
                  "location": f"{UNSEALED_FILE} - the first line"}


def test_the_plain_findings_carry_no_extension_and_one_is_blocked():
    assert EXTENSION not in SEALED_PLAIN
    assert EXTENSION not in UNSEALED_PLAIN
    assert story_coordinator.is_blocked(SEALED_FILE, RULES["blocked_paths"])
    assert not story_coordinator.is_blocked(UNSEALED_FILE, RULES["blocked_paths"])
    assert ROUTES[SEALED_PLAIN["category"]]["stage"] == DOCUMENTING


def test_the_check_refuses_a_blocked_path_with_or_without_an_extension():
    """As a function: the blocked-path bound is asked of every finding, and
    the other two bounds still of extension findings alone — the notes under
    the confined stage's category pass without the field."""
    (refused,) = check(SEALED_PLAIN).refused
    assert refused.finding == SEALED_PLAIN
    assert refused.stage == DOCUMENTING
    assert SEALED in refused.bound
    assert "execution rules" in refused.bound
    assert check(SEALED_PLAIN).accepted == []
    assert check(UNSEALED_PLAIN).accepted == [UNSEALED_PLAIN]
    assert check(UNSEALED_PLAIN).refused == []
    # The restriction bound is still an extension's alone.
    assert check(BEHAVIOUR_FINDING).accepted == [BEHAVIOUR_FINDING]


def test_a_blocked_path_without_an_extension_is_refused_before_the_pass_routes(
    target_root, sealed_harness,
):
    code, runner, run_dir = drive(target_root, sealed_harness,
                                  [repairing(SEALED_PLAIN)])
    assert code == 0, runner.calls
    assert runner.calls == STAGE_NAMES
    assert read_state(run_dir)["status"] == "completed"
    assert "escalated" not in events_of(run_dir)
    (refused,) = events_of_kind(run_dir, EXTENSION_REFUSED)
    assert refused["stage"] == VERIFYING
    assert refused["message"].startswith("scope extension refused")
    assert SEALED in refused["message"]
    assert "execution rules" in refused["message"]
    assert SEALED_PLAIN["location"] in refused["message"]
    assert ROUTED not in events_of(run_dir)
    assert read_state(run_dir)["repair_pass_count"] == 0
    assert not record_path(run_dir).exists()
    assert schema_validator.validate(history_of(run_dir), HISTORY_SCHEMA) == []


def test_the_rest_of_the_pass_routes_without_the_refused_finding(
    target_root, sealed_harness,
):
    code, runner, run_dir = drive(target_root, sealed_harness,
                                  [repairing(SEALED_PLAIN, BEHAVIOUR_FINDING), PASS])
    assert code == 0, runner.calls
    assert len(events_of_kind(run_dir, EXTENSION_REFUSED)) == 1
    assert record_of(run_dir)["findings"] == [
        {**BEHAVIOUR_FINDING, "stage": WRITING}]
    events = events_of(run_dir)
    assert events.index(EXTENSION_REFUSED) < events.index(ROUTED)


def test_the_same_finding_out_from_under_the_prefix_routes(
    target_root, sealed_harness,
):
    """Control: the identical finding with its path moved out from under the
    blocked prefix routes, is recorded and refuses nothing."""
    code, runner, run_dir = drive(target_root, sealed_harness,
                                  [repairing(UNSEALED_PLAIN), PASS])
    assert code == 0, runner.calls
    assert events_of_kind(run_dir, EXTENSION_REFUSED) == []
    assert runner.calls == entry_shape(DOCUMENTING)
    assert record_of(run_dir)["findings"] == [
        {**UNSEALED_PLAIN, "stage": DOCUMENTING}]


def test_a_blocked_path_without_an_extension_is_declined_with_the_bound_as_its_reason(
    inspecting_target, inspecting_harness, tmp_path, filings,
):
    """Through the inspection module's workflow, so the refusal has an
    accounting to reach: a routed finding answered by a repairable finding
    under the blocked prefix, carrying no extension, is declined with the
    refusal as its reason and filed at completion."""
    sealed = without_the_field(routed_repair(SEALED_FILE, "SEALED-PLAIN"))
    assert EXTENSION not in sealed
    run = inspected.drive(inspecting_target, inspecting_harness, tmp_path,
                          findings=[ROUTED_FINDING],
                          verdicts=[repairing(sealed)], name="refused-plain")
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.state["repair_pass_count"] == 0
    assert not (run.run_dir / story_coordinator.repair_pass_result_file(
        DECLINED_ARTIFACT, 1)).exists()

    (refused,) = inspected.events_of_kind(run, EXTENSION_REFUSED)
    assert SEALED in refused["message"]
    recorded = inspected.accounting(run)
    assert recorded["acted_on"] == 0
    assert recorded["remainder_slugs"] == [ROUTED_FINDING["slug"]]
    entry = declined_by_slug(recorded)[ROUTED_FINDING["slug"]]
    assert entry["reason_given"] is True
    assert entry["reason"] == refused["message"]
    assert SEALED in entry["reason"]
    assert recorded["declined_with_reason"] == 1
    assert recorded["declined_without_reason"] == 0
    assert inspected.filings_of(filings, ROUTED_FINDING) == 1


# --------------------------------------------------------------------------
# A record the state says exists and that cannot be read raises
# --------------------------------------------------------------------------


def undeclaring(stages: list[dict]) -> list[dict]:
    """The fixture's stages with neither pass declared."""
    return [{key: value for key, value in stage.items()
             if key not in ("correction_pass", "repair_pass")}
            for stage in stages]


def readers(run_dir: Path, stages: list[dict], state, stage: str):
    """Each reader the criterion names, called the way the coordinator calls
    it, as a list of thunks so the same set is asked every question below."""
    return [
        lambda: story_coordinator.correction_pass_grants(
            run_dir, stages, state, stage, 1),
        lambda: story_coordinator._repair_pass_findings(
            run_dir, stages, state, stage, 1),
        lambda: story_coordinator.repair_pass_grants(
            run_dir, stages, state, stage, 1),
    ]


def test_an_absent_record_raises_when_the_state_says_a_pass_was_taken(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    stages = WORKFLOW["stages"]
    for read in readers(run_dir, stages, state_with(1, 1), DOCUMENTING):
        with pytest.raises(RuntimeError):
            read()


@pytest.mark.parametrize("text", ["{ not json", "[]", "null"],
                         ids=["unparseable", "a list", "null"])
def test_an_unreadable_record_raises_when_the_state_says_a_pass_was_taken(
    tmp_path, text,
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write(correction_record_path(run_dir), text)
    write(record_path(run_dir), text)
    stages = WORKFLOW["stages"]
    for read in readers(run_dir, stages, state_with(1, 1), DOCUMENTING):
        with pytest.raises(RuntimeError):
            read()


def test_the_same_readers_grant_nothing_for_a_zero_count_or_an_undeclared_pass(
    tmp_path,
):
    """The early returns stand: with the count zero, or with the workflow
    declaring no pass, an absent record is nothing owed rather than a defect."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    for read in readers(run_dir, WORKFLOW["stages"], state_with(0, 0), DOCUMENTING):
        assert read() == []
    for read in readers(run_dir, undeclaring(WORKFLOW["stages"]),
                        state_with(1, 1), DOCUMENTING):
        assert read() == []


def test_a_readable_live_record_is_read_by_the_same_readers(tmp_path):
    """Control: the same readers over a record written the way the
    coordinator writes one, for a live state, grant the paths — so the
    raises above are the missing record's doing."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_json(correction_record_path(run_dir), {
        "pass": 1, "attempt": 1, "stage": DOCUMENTING,
        "findings": [CORRECTABLE_FINDING],
        CORRECTION_COUNT: 1, REPAIR_COUNT: 1,
        "statement": story_coordinator.correction_pass_statement(DOCUMENTING)})
    write_json(record_path(run_dir), {
        "pass": 1, "attempt": 1, "stage": DOCUMENTING,
        "findings": [{**NOTES_REPAIRABLE, "stage": DOCUMENTING}],
        CORRECTION_COUNT: 1, REPAIR_COUNT: 1, CLEAN_CLONE_CHECKED: True,
        "statement": story_coordinator.repair_pass_statement(DOCUMENTING)})
    grants, findings, repair_grants = readers(
        run_dir, WORKFLOW["stages"], state_with(1, 1), DOCUMENTING)
    assert grants() == [CORRECTABLE_FINDING["path"]]
    assert findings() == [{**NOTES_REPAIRABLE, "stage": DOCUMENTING}]
    assert repair_grants() == [NOTES]
    # And nothing once the attempt moves past the record: the same readers
    # for the same state, asked about a later attempt.
    assert story_coordinator.correction_pass_grants(
        run_dir, WORKFLOW["stages"], state_with(1, 1), DOCUMENTING, 2) == []
    assert story_coordinator._repair_pass_findings(
        run_dir, WORKFLOW["stages"], state_with(1, 1), DOCUMENTING, 2) == []
    assert story_coordinator.repair_pass_grants(
        run_dir, WORKFLOW["stages"], state_with(1, 1), DOCUMENTING, 2) == []


# --------------------------------------------------------------------------
# grant_covers and _inside are one reading
# --------------------------------------------------------------------------


VALUES = ("docs/", "docs/page.md", "NOTES.md")
PATHS = ("docs/page.md", "docs/sub/page.md", "docs", "docs-extra/page.md",
         "NOTES.md", "OTHER-NOTES.md", "src/NOTES.md")


@pytest.mark.parametrize("value", VALUES)
@pytest.mark.parametrize("path", PATHS)
def test_grant_covers_and_inside_give_one_answer(value, path):
    assert story_coordinator.grant_covers([value], path) == \
        story_coordinator._inside(value, path)


def test_the_one_reading_is_a_slash_for_a_directory_and_one_file_otherwise():
    """What the answer is, so the equality above is not two functions that
    are equally wrong: a slash-terminated value covers what is under it and
    not a sibling, a slash-less value covers itself alone."""
    inside = story_coordinator._inside
    assert inside("docs/", "docs/page.md")
    assert inside("docs/", "docs/sub/page.md")
    assert not inside("docs/", "docs")
    assert not inside("docs/", "docs-extra/page.md")
    assert inside("NOTES.md", "NOTES.md")
    assert not inside("NOTES.md", "OTHER-NOTES.md")
    assert not inside("NOTES.md", "src/NOTES.md")
    assert not inside("docs/page.md", "docs/page.md/extra")


def test_one_is_defined_in_terms_of_the_other():
    covers = inspected.executable_source(
        inspect.getsource(story_coordinator.grant_covers))
    inside = inspected.executable_source(
        inspect.getsource(story_coordinator._inside))
    assert ("_inside" in covers) != ("grant_covers" in inside)


# --------------------------------------------------------------------------
# What this repository ships: the verifier prompts and the schemas
# --------------------------------------------------------------------------


def shipped_text(prompt: str) -> str:
    return (REPO_ROOT / "prompts" / prompt).read_text(encoding="utf-8")


def test_every_shipped_definition_contributes_a_verifier_prompt():
    """The premise of "both prompts": each shipped definition declares the
    repair pass on one stage whose prompt is a file, and the two definitions
    are read through the sibling's derivation rather than named."""
    prompts = shipped_verifier_prompts()
    assert len(prompts) == len(harness_config.workflow_names(REPO_ROOT))
    for prompt in prompts:
        assert (REPO_ROOT / "prompts" / prompt).is_file(), prompt


@pytest.mark.parametrize("prompt", shipped_verifier_prompts())
def test_the_repairable_section_states_the_three_bounds(prompt):
    section = paragraph_containing(shipped_text(prompt), REPAIRABLE_PHRASE)
    # A verdict carrying both kinds routes the correction pass and defers
    # the repairable findings, named in the returning verification's record.
    assert "both kinds" in section
    assert "routed as a correction pass" in section
    assert "deferred" in section
    assert DEFERRED in section
    assert "returning verification" in section
    # No path the execution rules block for every stage.
    assert "may not name any path the execution rules block for every stage" \
        in section
    # One file, never a directory; a trailing slash stops the run.
    assert "one file, never a directory" in section
    assert "trailing slash" in section
    assert "stops the run" in section


@pytest.mark.parametrize("prompt", shipped_verifier_prompts())
def test_the_correctable_section_says_a_trailing_slash_stops_the_run(prompt):
    section = paragraph_containing(shipped_text(prompt), CORRECTABLE_PHRASE)
    assert "one file, never a directory" in section
    assert "trailing slash" in section
    assert "stops the run" in section


def repairable_section_shared_by_both(prompt: str) -> str:
    """The repairable section of one shipped verifier prompt, with the
    statements the parity walk permits one of them alone to carry set aside.

    Set aside through the sibling that declares which sentences those are,
    rather than through a judgement written here: story-214 gave one prompt a
    statement closing a ground for declining, and story-215 gave the same prompt
    the instruction that asks for the assertion a repaired fix needs. Both are
    in this paragraph and both are true of one workflow alone, so the equality
    below is read with them out of the way — which leaves every sentence of
    shared mechanism in the comparison, as the control below shows. What the
    three bounds above assert is untouched by this: none of them is stated in a
    permitted sentence, so that test reads the paragraph whole as it always did.
    """
    return without_the_permitted_divergence(
        paragraph_containing(shipped_text(prompt), REPAIRABLE_PHRASE))


def test_the_two_shipped_repairable_sections_read_identically():
    sections = {repairable_section_shared_by_both(prompt)
                for prompt in shipped_verifier_prompts()}
    assert len(sections) == 1


#: A clause both shipped prompts carry in this paragraph, in a sentence neither
#: is permitted to diverge on, and one word of it reworded. Rewording it is the
#: drift setting the permitted sentences aside must not have stopped reporting.
SHARED_IN_THE_SECTION = "not a licence to revisit"
REWORDED_IN_THE_SECTION = "not a permit to revisit"


def test_the_comparison_still_reports_a_sentence_the_two_prompts_share_reworded():
    """Control for the equality above: with one word of a shared sentence
    changed in a copy of one shipped prompt, the same reading finds two
    different sections. Green above therefore says the sections agree rather
    than that setting the permitted sentences aside left nothing to compare.
    """
    prompt = shipped_verifier_prompts()[0]
    original = repairable_section_shared_by_both(prompt)
    drifted = shipped_text(prompt).replace(SHARED_IN_THE_SECTION,
                                           REWORDED_IN_THE_SECTION, 1)
    assert drifted != shipped_text(prompt)
    assert without_the_permitted_divergence(
        paragraph_containing(drifted, REPAIRABLE_PHRASE)) != original


@pytest.mark.parametrize("field", [CORRECTABLE, REPAIRABLE])
def test_the_schema_path_descriptions_say_a_directory_is_refused_and_the_grant_is_bounded(
    field,
):
    description = VERDICT_SCHEMA["properties"][field]["items"][
        "properties"]["path"]["description"]
    assert "never a directory" in description
    assert "refused" in description
    assert "bounded to the pass that made it" in description


def test_the_repairable_path_description_names_the_blocked_path_bound():
    description = VERDICT_SCHEMA["properties"][REPAIRABLE]["items"][
        "properties"]["path"]["description"]
    assert "block for every stage" in description
    assert "declined" in description
