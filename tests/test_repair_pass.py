"""A finding about the story's own change is fixed by the stage that owns it.

A verifier that wants a finding fixed had two routes, and most findings fit
neither. A correctable finding re-enters at the stage the `correction_pass`
declaration names and is bounded to the words alone; a blocking issue fails the
verdict and spends a retry. The repair pass is the third route: a passing
verdict may carry `repairable_findings`, each naming a path, a location, the
finding, the correction and a retry category, and the coordinator resolves the
category through the verifier stage's `retry_routing` table — exactly as it
resolves a failed verdict's `retry_target` — re-enters at the earliest stage in
workflow order among the resolved stages, and runs forward to verification
again. It has the correction pass's cost and the retry routing's reach: no
`retry_count`, no `attempts/attempt-N/` archive, no `retry-history.json` entry,
and the passing verdict that routed it still stands. Each stage entered is
granted the paths of the findings whose category resolved to it, for that
attempt alone.

Because a repair may change behaviour, it is built on a verified tree: it
routes only after the clean-clone check on the routing verdict has passed, and
the verification it returns to runs the check again. That is the opposite
ordering from the correction pass, which routes before the check, and both
orderings are driven here as runs rather than argued from source.

Every claim below is read off what a real run wrote: the stages the fake
runner was invoked for, the event stream, `state.json`, the run directory and
the prompts the coordinator rendered. Nothing here invokes a model.

The workflow those runs execute is built by `tests/conftest.py`'s builder and
materialized into a harness root this module owns, for the reason story-048
established: the subject is *the mechanism*, and the stage list, the retry
categories and the artifact names are inputs to it. Every name below still
comes off a definition rather than being spelled at an assertion — it is the
fixture's definition rather than the shipped one. The cases whose subject
genuinely is what this repository ships — both shipped definitions declare the
pass, every shipped prompt carries the slot, the shipped verifier prompts bound
the field — read the shipped artifacts and say so.

Every absence asserted here carries a demonstration that it can fail:

  * "the pass spends no retry budget" — `retry_count` unchanged, no attempt
    archive, no retry-history entry — sits beside `retry_run`, a run through
    the same fixture that does spend all three;
  * "the guidance in force is empty at the entry to the repaired stage" sits
    beside a retry-routed entry to the *same* stage, where it is not empty;
  * "a workflow declaring no repair pass runs a verdict carrying findings as
    it ran one carrying none" is a comparison of two runs under the
    non-declaring workflow, and sits beside the same comparison under the
    declaring workflow, which must report a difference;
  * "no stage is invoked past the budget" sits beside the exhausted verdict's
    findings being recorded as an event, so a spent budget is distinguishable
    from a mechanism that never ran;
  * "an undeclared category escalates" sits beside a declared one that routes;
  * "the clean-clone check ran before the pass" sits beside a run whose check
    fails, which takes the retry route and writes no pass record;
  * "a granted edit is neither reverted nor an ownership violation" sits beside
    the same edit with nothing granted, which is reverted or escalates, and
    beside the same stage re-entered on a later attempt, which inherits nothing;
  * "no name this mechanism routes on is written in orchestration source" sits
    beside the same names being present in the workflow this module built;
  * "a refused declaration created no run state" sits beside the same harness
    with the declaration repaired, which creates all of it;
  * "the schema accepts a well-formed finding" sits beside an entry missing
    each required field in turn, which it refuses.
"""
import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

import context_assembler
import harness_config
import schema_validator
import story_coordinator
import story_parser
from agent_runner import AgentResult
from conftest import StageRef, workflow_stage
import conftest

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
COORDINATOR_SOURCE = Path(story_coordinator.__file__).read_text(encoding="utf-8")

#: The artifact the fixture's declaration names. Deliberately not the name this
#: repository deploys: the record reaching the run directory under this name is
#: what says the coordinator reads the name off the declaration rather than
#: carrying one of its own.
REPAIR_ARTIFACT = "repair-probe.json"
CORRECTION_ARTIFACT = "correction-probe.json"

#: The revert check the writing stage declares, so a repair pass's grant to
#: that stage is observable at run level: an ungranted edit outside its
#: confinement is decided by the check, a granted one is exempt from it.
REVERT_ARTIFACT = "revert-probe.json"
CHECKED = {"result": REVERT_ARTIFACT, "baseline": "stage-baseline",
           "discarded": "discarded"}

#: Where the writing stage is confined to. Its ordinary edit is inside it;
#: the files a repair finding grants below are outside it.
CONFINED_TO = "src/"
ORDINARY_FILE = "src/app.py"

#: The workflow these runs execute. Four stages, because "the entry stage is
#: the earliest among the resolved stages" needs categories routing to more
#: than one stage, and "the correction pass is untouched" needs the two passes
#: declared side by side on one verifier. The clean-clone declaration is here
#: because the repair pass routes only after that check, which is an ordering
#: this module asserts. The correction pass names the last writing stage and
#: a budget of one, so the two budgets can be shown to be spent separately.
WORKFLOW = conftest.build_workflow(
    workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"},
        may_only_change=(CONFINED_TO,),
        revert_check=CHECKED),
    workflow_stage(
        outputs=(conftest.TEST_RESULTS, conftest.TESTER_CHANGED_FILES),
        changed_files=conftest.TESTER_CHANGED_FILES,
        schemas={conftest.TEST_RESULTS: "test-results",
                 conftest.TESTER_CHANGED_FILES: "changed-files"}),
    workflow_stage(
        outputs=(conftest.DOCUMENTATION_REPORT,
                 conftest.DOCUMENTER_CHANGED_FILES),
        changed_files=conftest.DOCUMENTER_CHANGED_FILES,
        schemas={conftest.DOCUMENTER_CHANGED_FILES: "changed-files"}),
    workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        clean_clone={"result": conftest.CLEAN_CLONE_RESULT,
                     "retry_stage": StageRef(0)},
        correction_pass={"result": CORRECTION_ARTIFACT, "budget": 1,
                         "stage": StageRef(2)},
        repair_pass={"result": REPAIR_ARTIFACT, "budget": 2},
        retry_routing={
            "the-behaviour": {"stage": StageRef(0),
                              "when": "the behaviour the story asked for is missing"},
            "the-checks": {"stage": StageRef(1),
                           "when": "the validation does not hold the behaviour"},
            "the-record": {"stage": StageRef(2),
                           "when": "the documents do not describe what shipped"},
        }),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="repair-pass-workflow",
)

STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, DOCUMENTING, VERIFYING = STAGE_NAMES

VERIFIER_STAGE = next(s for s in WORKFLOW["stages"] if "repair_pass" in s)
ROUTES = VERIFIER_STAGE["on_failure"]["retry_routing"]
BEHAVIOUR_CATEGORY, CHECKS_CATEGORY, RECORD_CATEGORY = list(ROUTES)
REPAIR = VERIFIER_STAGE["repair_pass"]
ARTIFACT = REPAIR["result"]
BUDGET = REPAIR["budget"]
CORRECTION = VERIFIER_STAGE["correction_pass"]
CORRECTION_ENTRY = CORRECTION["stage"]

#: A category the fixture deliberately does not declare, which is the whole of
#: what makes it undeclared. Asserted rather than assumed below.
UNDECLARED_CATEGORY = "a-category-this-workflow-does-not-declare"

MAX_RETRIES = json.loads(
    (REPO_ROOT / "rules" / "execution-rules.json").read_text(encoding="utf-8")
)["max_retries"]

VERDICT_SCHEMA = schema_validator.load_schema("verification-result")
HISTORY_SCHEMA = schema_validator.load_schema("execution-history")

#: The field a repairable finding is recorded under, and the field the
#: correction pass reads. Spelled once each; every verdict built below and
#: every schema assertion derives from these.
REPAIRABLE = "repairable_findings"
CORRECTABLE = "correctable_findings"
REQUIRED_OF_A_FINDING = {"path", "location", "finding", "correction", "category"}

#: The events this mechanism appends, as the execution-history schema
#: enumerates them, beside the ones it is asserted against.
ROUTED = "repair-pass-routed"
RECORDED = "repair-pass-recorded"
GRANT_EVENT = "repair-pass-grant-applied"
CORRECTION_ROUTED = "correction-pass-routed"
CLEAN_CLONE_PASSED = "clean-clone-passed"
CLEAN_CLONE_FAILED = "clean-clone-failed"
REVERTED_EVENT = "revert-check-reverted"
PERMITTED_EVENT = "revert-check-permitted"


def destination_of(category: str) -> str:
    """Where the fixture's table routes a category. Read, never written."""
    return ROUTES[category]["stage"]


#: Files outside the writing stage's confinement that a repair finding may
#: grant it. Committed into the target by `governed_target` below, so a revert
#: has a baseline to restore them to.
NOTES = "NOTES.md"
OTHER_NOTES = "OTHER-NOTES.md"
NEW_FILE = "docs/NEW.md"
NOTES_AT_HEAD = "# Notes\n\nThe notes say three things.\n"
NOTES_REPAIRED = "# Notes\n\nThe notes say three things, and now say them.\n"
NOTES_REWORDED_AGAIN = "# Notes\n\nReworded again on a retry.\n"
OTHER_NOTES_AT_HEAD = "# Other notes\n"
OTHER_NOTES_REWORDED = "# Other notes, reworded by a pass not granted them.\n"

#: Distinctive text so a search of a rendered prompt is looking for these
#: findings rather than for any sentence about repairs. A finding is matched by
#: its own words, which is what "the finding reached the stage" means.
BEHAVIOUR_FINDING = {
    "path": NOTES,
    "location": f"{NOTES} - the second paragraph",
    "finding": "MARKER-BEHAVIOUR the notes say three things and name none",
    "correction": "MARKER-BEHAVIOUR-FIX say them",
    "category": BEHAVIOUR_CATEGORY,
}
CHECKS_FINDING = {
    "path": "tests/test_sample.py",
    "location": "tests/test_sample.py::test_the_sample - the absence assertion",
    "finding": "MARKER-CHECKS the absence assertion has no negative control",
    "correction": "MARKER-CHECKS-FIX add the control beside it",
    "category": CHECKS_CATEGORY,
}
RECORD_FINDING = {
    "path": ".harness/docs/ARCHITECTURE.md",
    "location": ".harness/docs/ARCHITECTURE.md - the routing section",
    "finding": "MARKER-RECORD the section names a stage that was renamed",
    "correction": "MARKER-RECORD-FIX name the stage the workflow declares today",
    "category": RECORD_CATEGORY,
}
UNKNOWN_FINDING = {
    "path": "src/app.py",
    "location": "src/app.py - the module comment",
    "finding": "MARKER-UNKNOWN the comment describes behaviour the code lost",
    "correction": "MARKER-UNKNOWN-FIX describe what the code does now",
    "category": UNDECLARED_CATEGORY,
}

#: One finding per declared category, keyed by the category the finding itself
#: carries rather than by a second spelling of it.
FINDING_FOR = {finding["category"]: finding
               for finding in (BEHAVIOUR_FINDING, CHECKS_FINDING, RECORD_FINDING)}

#: A correctable finding, for the cases about the two passes being
#: independent. Categorised for a category that routes somewhere other than
#: the declared correction entry, so a run that entered where the category
#: points would be visibly different from one that entered where the
#: declaration points.
CORRECTABLE_FINDING = {
    "path": "src/app.py",
    "location": "src/app.py - the docstring of the sample function",
    "finding": "MARKER-CORRECTABLE the docstring wraps mid-word",
    "correction": "MARKER-CORRECTABLE-FIX rewrap the line",
    "category": BEHAVIOUR_CATEGORY,
}

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


def repairing(*findings: dict) -> dict:
    return {**PASS, REPAIRABLE: [dict(f) for f in findings]}


def correcting(*findings: dict) -> dict:
    return {**PASS, CORRECTABLE: [dict(f) for f in findings]}


def failing_into(category: str) -> dict:
    return {
        "status": "failed",
        "blocking_issues": [{
            "severity": "high", "issue": "sample behavior missing",
            "location": "src/app.py",
            "required_behavior": "sample behavior exists",
        }],
        "unverified": [], "retry_recommended": True, "retry_target": category,
    }


# --------------------------------------------------------------------------
# Fixture plumbing
# --------------------------------------------------------------------------


#: The context fields the coordinator injects the two pass records under.
#: `conftest.BUILT_PROMPT_FIELDS` predates them and the fixture says a module
#: needing a field it does not list passes its own template, which is what
#: this does. Spelled once, here, and derived from here by every assertion
#: that looks for a record in a prompt.
REPAIR_FIELD = "repair_pass_result"
CORRECTION_FIELD = "correction_pass_result"

PROMPTS = {
    name: (conftest.built_stage_prompt(name)
           + f"{CORRECTION_FIELD}:\n{{{{{CORRECTION_FIELD}}}}}\n\n"
           + f"{REPAIR_FIELD}:\n{{{{{REPAIR_FIELD}}}}}\n")
    for name in STAGE_NAMES
}


@pytest.fixture
def configured_workflow() -> str:
    """Point the shared target fixture at the definition built above."""
    return WORKFLOW["name"]


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A harness root carrying that definition, so every case below drives a
    real coordinator loading a real file."""
    return conftest.materialize_workflow(WORKFLOW,
                                         tmp_path / "repair-pass-harness",
                                         prompts=PROMPTS)


@pytest.fixture
def governed_target(target_root: Path) -> Path:
    """The shared target with the files a repair finding may grant, committed
    so the writing stage's baseline holds them and a revert has something to
    restore."""
    write(target_root / NOTES, NOTES_AT_HEAD)
    write(target_root / OTHER_NOTES, OTHER_NOTES_AT_HEAD)
    conftest.commit_setup(target_root, "the files a repair finding may name")
    return target_root


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_dir_of(target_root: Path, story_id: str = "story-001") -> Path:
    return conftest.run_dir_for(target_root, story_id)


def history_of(run_dir: Path) -> list[dict]:
    return read_json(run_dir / "execution-history.json")


def events_of(run_dir: Path) -> list[str]:
    return [entry["event"] for entry in history_of(run_dir)]


def events_of_kind(run_dir: Path, kind: str, stage: str | None = None) -> list[dict]:
    return [entry for entry in history_of(run_dir)
            if entry["event"] == kind
            and (stage is None or entry.get("stage") == stage)]


def read_state(run_dir: Path) -> dict:
    return read_json(run_dir / "state.json")


def record_path(run_dir: Path, number: int = 1, artifact: str = ARTIFACT) -> Path:
    return run_dir / story_coordinator.repair_pass_result_file(artifact, number)


def record_of(run_dir: Path, number: int = 1, artifact: str = ARTIFACT) -> dict:
    return read_json(record_path(run_dir, number, artifact))


def rendered_prompt(run_dir: Path, stage: str, attempt: int = 1) -> str:
    """The prompt one stage was given, read back off the run directory,
    through the coordinator's own name-shaping function."""
    return (run_dir / story_coordinator.prompt_file(stage, attempt)).read_text(
        encoding="utf-8")


def paths_named_in(message: str, candidates: tuple[str, ...]) -> set[str]:
    """Which of the candidate paths a message names as a whole token. A
    plain substring test cannot tell NOTES.md from the tail of OTHER-NOTES.md,
    so a name counts only when nothing that could be part of a path — a
    word character, a dot or a hyphen — sits on either side of it."""
    return {candidate for candidate in candidates
            if re.search(rf"(?<![\w.-]){re.escape(candidate)}(?![\w.-])",
                         message)}


def content(run_dir: Path, relative: str) -> str:
    """A file in the tree the run worked in, which holds its run directory
    under the configured runs_dir — so the tree is two levels above it."""
    return (run_dir.parents[2] / relative).read_text(encoding="utf-8")


def clean_clone_events(run_dir: Path) -> list[str]:
    return [event for event in events_of(run_dir)
            if event in (CLEAN_CLONE_PASSED, CLEAN_CLONE_FAILED)]


# --------------------------------------------------------------------------
# The writing stage's edits on a pass, each with the record for it
# --------------------------------------------------------------------------


def the_named_file(tree: Path) -> dict:
    """The repair the pass asked for, in the file the finding named."""
    write(tree / NOTES, NOTES_REPAIRED)
    return {"modified": [NOTES], "created": [], "deleted": []}


def the_named_file_again(tree: Path) -> dict:
    """A later edit to the same file with different words, so undoing it is
    observable."""
    write(tree / NOTES, NOTES_REWORDED_AGAIN)
    return {"modified": [NOTES], "created": [], "deleted": []}


def the_named_file_and_another(tree: Path) -> dict:
    """The same repair, and a second governed file no finding named."""
    write(tree / NOTES, NOTES_REPAIRED)
    write(tree / OTHER_NOTES, OTHER_NOTES_REWORDED)
    return {"modified": [NOTES, OTHER_NOTES], "created": [], "deleted": []}


def a_new_file(tree: Path) -> dict:
    """A file created outside the confinement, which the ownership check
    decides rather than the revert check."""
    write(tree / NEW_FILE, "created on a repair pass\n")
    return {"modified": [], "created": [NEW_FILE], "deleted": []}


class Runner:
    """A fake agent runner that writes each stage's declared artifacts.

    It records, per invocation, the prompt it was handed and the guidance the
    coordinator had in force at that moment — read off `state.json` through
    the shared helper at the moment of the call, because "the guidance in
    force *at the entry to* the stage" is a question about that instant.

    `edits` is the list of edit functions the writing stage's invocations
    make, in order — None for an invocation that changes nothing beyond its
    ordinary `src/app.py` — so a run that re-enters the writing stage can have
    it do something different the second time. Every invocation's record
    names the ordinary file beside whatever the edit function touched: the
    first invocation changed it, a repair pass makes no new attempt, and the
    changed-files completeness check's tree signature is first-seen-wins
    within an attempt — so a re-entered invocation that recorded the pass's
    edit alone would be reported for the ordinary file still held by the
    tree, and the run would stop before any grant was decided.
    """

    def __init__(self, target_root: Path, verdicts: list[dict],
                 edits: list | None = None, story_id: str = "story-001"):
        self.target_root = target_root
        self.run_dir = run_dir_of(target_root, story_id)
        self.verdicts = list(verdicts)
        self.edits = list(edits or [])
        self.calls: list[str] = []
        self.prompts: dict[str, list[str]] = {}
        self.guidance: dict[str, list[list[str]]] = {}

    def __call__(self, prompt, *, stage, cwd, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None, run_dir=None, **declared):
        self.calls.append(stage)
        self.prompts.setdefault(stage, []).append(prompt)
        self.guidance.setdefault(stage, []).append(
            conftest.guidance_in_force(self.run_dir))
        if stage == WRITING:
            edit = self.edits.pop(0) if self.edits else None
            if edit:
                record = edit(Path(cwd))
                record["modified"] = [ORDINARY_FILE] + [
                    path for path in record["modified"] if path != ORDINARY_FILE]
            else:
                (Path(cwd) / ORDINARY_FILE).write_text(
                    "print('hello')\n# the story's change\n", encoding="utf-8")
                record = {"modified": [ORDINARY_FILE], "created": [],
                          "deleted": []}
            write_json(self.run_dir / conftest.CHANGED_FILES, record)
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Did the work.\n", encoding="utf-8")
        elif stage == VALIDATING:
            write_json(self.run_dir / conftest.TEST_RESULTS,
                       {"tests_written": 2})
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES,
                       {"modified": [], "created": ["tests/test_app.py"],
                        "deleted": []})
        elif stage == DOCUMENTING:
            (self.run_dir / conftest.DOCUMENTATION_REPORT).write_text(
                "No changes needed.\n", encoding="utf-8")
            write_json(self.run_dir / conftest.DOCUMENTER_CHANGED_FILES,
                       {"modified": [], "created": [], "deleted": []})
        elif stage == VERIFYING:
            verdict = conftest.answering_guidance(
                self.verdicts.pop(0), self.run_dir)
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, verdict)
            if verdict["status"] == "failed":
                write_json(self.run_dir / conftest.RETRY_GUIDANCE, {
                    "current_focus": [{
                        "focus": "fix the sample behavior",
                        "satisfied_when": "the sample behavior exists",
                    }],
                    "preserve_behavior": ["existing behavior"],
                    "retry_scope": ["src/app.py"],
                })
        return AgentResult(ok=True, result_text=f"{stage} done")


def drive(target_root: Path, harness: Path, verdicts: list[dict],
          edits: list | None = None):
    """One run, returning its exit code, its runner and its run directory."""
    runner = Runner(target_root, verdicts, edits)
    code = story_coordinator.run_story(
        "story-001", harness, target_root, runner)
    return code, runner, run_dir_of(target_root)


def probe_harness(tmp_path: Path, target_root: Path, name: str, mutate) -> Path:
    """A harness root carrying the built definition with the verifier mutated,
    and `target_root` configured to run it — the idiom the correction-pass and
    clean-clone modules use for their own declarations."""
    workflow = json.loads(json.dumps(WORKFLOW))
    for stage in workflow["stages"]:
        if stage["name"] == VERIFYING:
            mutate(stage)
    workflow["name"] = name
    root = conftest.materialize_workflow(workflow, tmp_path / name,
                                         prompts=PROMPTS)
    configure(target_root, workflow=name)
    return root


def second_target(target_root: Path, tmp_path: Path) -> Path:
    """A copy of the target repository, so two coordinators can be compared:
    a run refuses to re-run a story in a target that has already run it."""
    other = tmp_path / "second-target"
    shutil.copytree(target_root, other)
    return other


def configure(target_root: Path, **overrides) -> None:
    """Rewrite the target's config keys, adding those it does not carry."""
    path = target_root / ".harness" / "config.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    for key, value in overrides.items():
        rendered = f"{key}: {value}"
        for index, line in enumerate(lines):
            if line.startswith(f"{key}:"):
                lines[index] = rendered
                break
        else:
            lines.append(rendered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    conftest.commit_setup(target_root, "configure the target for this test")


def entry_shape(entry: str) -> list[str]:
    """The stages a run entering at `entry` invokes: all of them, then that one
    and everything after it."""
    return [*STAGE_NAMES, *STAGE_NAMES[STAGE_NAMES.index(entry):]]


@pytest.fixture
def repaired_run(target_root, harness_root):
    """The central run: a passing verdict carrying one repairable finding whose
    category the table maps to the first stage. The workflow re-enters there
    and runs forward to verification again, which passes carrying nothing."""
    return drive(target_root, harness_root,
                 [repairing(BEHAVIOUR_FINDING), PASS])


@pytest.fixture
def retry_run(target_root, harness_root):
    """The control every "spends nothing" assertion needs: a run that spends.
    A failed verdict naming the same category routes a retry to the same
    stage, so the two runs differ in the mechanism under test and in nothing
    else."""
    return drive(target_root, harness_root,
                 [failing_into(BEHAVIOUR_CATEGORY), PASS])


@pytest.fixture
def unrepaired_run(target_root, harness_root):
    """A passing verdict carrying no findings, under the declaring workflow."""
    return drive(target_root, harness_root, [PASS])


# --------------------------------------------------------------------------
# The fixture is what it claims to be
# --------------------------------------------------------------------------


def test_the_undeclared_category_really_is_undeclared():
    assert UNDECLARED_CATEGORY not in ROUTES
    assert set(ROUTES) == {BEHAVIOUR_CATEGORY, CHECKS_CATEGORY, RECORD_CATEGORY}
    assert set(FINDING_FOR) == set(ROUTES)


def test_the_declaration_names_no_entry_stage_of_its_own():
    """The premise "the stage comes off the routing table" rests on: the
    declaration carries a result and a budget and nothing that chooses a
    stage, where the correction pass beside it does."""
    assert "stage" not in REPAIR
    assert "stage" in CORRECTION
    assert BUDGET >= 2, "the budget cases need more than one pass"


def test_the_categories_route_to_more_than_one_stage_and_none_to_the_verifier():
    """The premise "earliest in workflow order" rests on: if every category
    routed to one stage there would be nothing to order, and a category
    routing to the verifier would re-enter a pass that runs nothing before
    judging itself."""
    destinations = {destination_of(category) for category in ROUTES}
    assert len(destinations) >= 2
    assert VERIFYING not in destinations
    for destination in destinations:
        assert destination in STAGE_NAMES


def test_the_granted_files_are_outside_the_writing_stages_confinement():
    """What makes the grant observable: the files the findings name are
    governed for the stage they resolve to, and the ordinary edit is not."""
    (confinement,) = story_coordinator.restrictions_on(WORKFLOW["stages"][0])
    assert confinement.sense == story_coordinator.CONFINEMENT
    assert confinement.governs(NOTES)
    assert confinement.governs(OTHER_NOTES)
    assert confinement.governs(NEW_FILE)
    assert not confinement.governs("src/app.py")
    assert destination_of(BEHAVIOUR_FINDING["category"]) == WRITING


# --------------------------------------------------------------------------
# The routed pass
# --------------------------------------------------------------------------


def test_the_stage_the_category_resolves_to_runs_again_within_the_same_run(
    repaired_run,
):
    code, runner, run_dir = repaired_run
    entry = destination_of(BEHAVIOUR_FINDING["category"])

    assert code == 0
    assert runner.calls == entry_shape(entry)
    assert read_state(run_dir)["status"] == "completed"
    assert (run_dir / "completion-report.md").is_file()


def test_the_run_records_the_pass_in_its_own_event_stream(repaired_run):
    _, _, run_dir = repaired_run
    routed = events_of_kind(run_dir, ROUTED)
    assert len(routed) == 1
    assert routed[0]["stage"] == VERIFYING
    assert routed[0]["retry_stage"] == destination_of(BEHAVIOUR_CATEGORY)
    assert schema_validator.validate(history_of(run_dir), HISTORY_SCHEMA) == []


def test_the_record_the_coordinator_wrote_is_in_the_run_directory(repaired_run):
    """The numbered record: the pass, the attempt, the entry stage, each
    finding with the stage its category resolved to, and the statement."""
    _, _, run_dir = repaired_run
    record = record_of(run_dir)

    assert record["pass"] == 1
    assert record["attempt"] == 1
    assert record["stage"] == destination_of(BEHAVIOUR_CATEGORY)
    assert record["findings"] == [
        {**BEHAVIOUR_FINDING, "stage": destination_of(BEHAVIOUR_CATEGORY)}]
    assert record["statement"] == story_coordinator.repair_pass_statement(
        record["stage"])


def test_the_finding_reaches_the_entered_stage_in_its_rendered_prompt(
    repaired_run,
):
    _, runner, run_dir = repaired_run
    entry = destination_of(BEHAVIOUR_CATEGORY)
    prompt = rendered_prompt(run_dir, entry)

    for words in (BEHAVIOUR_FINDING["location"], BEHAVIOUR_FINDING["finding"],
                  BEHAVIOUR_FINDING["correction"]):
        assert words in prompt, words
    assert "{{" not in prompt
    # And in the control: the first rendering of the same stage carried
    # nothing of it, so the finding is there because the pass put it there.
    first, second = runner.prompts[entry]
    assert BEHAVIOUR_FINDING["finding"] not in first
    assert BEHAVIOUR_FINDING["finding"] in second


def test_every_stage_the_pass_runs_forward_through_is_told_of_it(repaired_run):
    """The record is injected into every stage prompt, not only the entered
    one: a stage running because the workflow runs forward from the entry is
    told it has nothing to repair rather than left to guess why it is running."""
    _, runner, _ = repaired_run
    entry = destination_of(BEHAVIOUR_CATEGORY)
    for stage in STAGE_NAMES[STAGE_NAMES.index(entry):]:
        first, second = runner.prompts[stage]
        assert BEHAVIOUR_FINDING["finding"] not in first, stage
        assert BEHAVIOUR_FINDING["finding"] in second, stage


def test_the_statement_says_what_the_pass_is_and_is_not():
    """What a stage most needs to know, read off the coordinator's own
    statement: that no retry was spent, that only the named files are its to
    change, that it is not a licence to revisit the work, and that the
    clean-clone check runs again."""
    statement = story_coordinator.repair_pass_statement(WRITING)
    assert WRITING in statement
    assert "not a retry" in statement
    assert "no retry budget was spent" in statement
    assert "not a licence to revisit the work" in statement
    assert "clean-clone check" in statement
    assert "nothing else" in statement


# --------------------------------------------------------------------------
# The pass spends nothing a retry spends
# --------------------------------------------------------------------------


def test_the_pass_leaves_the_retry_budget_untouched(repaired_run):
    _, _, run_dir = repaired_run
    state = read_state(run_dir)
    assert state["retry_count"] == 0
    assert state["repair_pass_count"] == 1
    assert not (run_dir / "attempts").exists()
    assert not (run_dir / "retry-history.json").exists()


def test_a_run_that_does_spend_the_retry_budget_shows_all_three(retry_run):
    """The control beside it: each absence above is asserted here as a
    presence, against a run through the same fixture that routed a retry to
    the same stage."""
    code, _, run_dir = retry_run
    assert code == 0
    state = read_state(run_dir)
    assert state["retry_count"] == 1
    assert state["repair_pass_count"] == 0
    assert (run_dir / "attempts" / "attempt-1").is_dir()
    assert (run_dir / "retry-history.json").is_file()


def test_the_pass_takes_no_attempt_number_of_its_own(repaired_run):
    _, _, run_dir = repaired_run
    entry = destination_of(BEHAVIOUR_CATEGORY)
    assert record_of(run_dir)["attempt"] == 1
    assert (run_dir / story_coordinator.prompt_file(entry, 1)).is_file()
    assert not (run_dir / story_coordinator.prompt_file(entry, 2)).exists()


def test_the_pass_does_not_turn_the_passing_verdict_into_a_failing_one(
    repaired_run,
):
    """Read off the archived iteration, which is the verdict as it was judged
    rather than as the run directory's live artifact ended up."""
    _, _, run_dir = repaired_run
    archived = read_json(run_dir / "verification" / "iteration-1.json")
    assert archived["status"] == "passed"
    assert archived[REPAIRABLE] == [BEHAVIOUR_FINDING]
    assert read_state(run_dir)["verification_iterations"] == 2


def test_a_failed_verdict_archives_as_failed(retry_run):
    """Control: the archive records the verdict's own status."""
    _, _, run_dir = retry_run
    archived = read_json(run_dir / "verification" / "iteration-1.json")
    assert archived["status"] == "failed"


def test_a_run_that_takes_no_pass_counts_none(unrepaired_run):
    _, _, run_dir = unrepaired_run
    assert read_state(run_dir)["repair_pass_count"] == 0
    assert not record_path(run_dir).exists()


def test_a_state_file_written_before_the_field_existed_still_loads(tmp_path):
    """The field is defaulted so a resumed run's state.json, written before
    this story, reads as having taken no pass — and a saved count survives a
    round trip so a resumed run cannot spend the pass again."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state = story_coordinator.RunState(story_id="story-001",
                                       branch="story/story-001")
    story_coordinator.save_state(run_dir, state)
    written = read_state(run_dir)
    del written["repair_pass_count"]
    write_json(run_dir / "state.json", written)
    assert story_coordinator.load_state(run_dir).repair_pass_count == 0

    state.repair_pass_count = BUDGET
    story_coordinator.save_state(run_dir, state)
    assert story_coordinator.load_state(run_dir).repair_pass_count == BUDGET


# --------------------------------------------------------------------------
# The guidance in force
# --------------------------------------------------------------------------


def test_no_guidance_is_in_force_at_the_entry_to_the_repaired_stage(
    repaired_run,
):
    _, runner, _ = repaired_run
    assert runner.guidance[destination_of(BEHAVIOUR_CATEGORY)] == [[], []]


def test_a_retry_routed_to_the_same_stage_does_carry_guidance(retry_run):
    """The control beside it: the same stage, entered the other way, has
    guidance in force."""
    _, runner, _ = retry_run
    entries = runner.guidance[destination_of(BEHAVIOUR_CATEGORY)]
    assert entries[0] == []
    assert entries[1] != []


# --------------------------------------------------------------------------
# The clean-clone check runs before the pass, and again after it
# --------------------------------------------------------------------------


def test_the_pass_routes_only_after_the_clean_clone_check_has_passed(
    repaired_run,
):
    """Ordering off the event stream: the check on the routing verdict passed
    first, then the pass routed, then the check on the repaired tree passed
    again. Two passing verdicts, two checks."""
    _, _, run_dir = repaired_run
    events = events_of(run_dir)
    assert read_state(run_dir)["verification_iterations"] == 2
    assert clean_clone_events(run_dir) == [CLEAN_CLONE_PASSED, CLEAN_CLONE_PASSED]
    first_check = events.index(CLEAN_CLONE_PASSED)
    second_check = events.index(CLEAN_CLONE_PASSED, first_check + 1)
    assert first_check < events.index(ROUTED) < second_check
    assert events.index(ROUTED) < events.index("story-completed")


def test_a_run_that_takes_no_pass_runs_the_check_once(unrepaired_run):
    """The control for "twice": one passing verdict, one check, where it
    always was — after the verdict and before the run completes."""
    _, _, run_dir = unrepaired_run
    events = events_of(run_dir)
    assert clean_clone_events(run_dir) == [CLEAN_CLONE_PASSED]
    assert events.index("verification-passed") < events.index(CLEAN_CLONE_PASSED)
    assert events.index(CLEAN_CLONE_PASSED) < events.index("story-completed")
    assert ROUTED not in events


#: A suite that fails wherever it runs, so the clean-clone check on the
#: routing verdict fails and the retry route above the repair block is what
#: the run takes.
SUITE_ALWAYS_RED = "sh -c 'exit 1'"


def test_a_verdict_whose_clean_clone_check_fails_takes_the_retry_route_instead(
    target_root, harness_root,
):
    """The other half of the ordering: a verdict carrying a repairable finding
    whose check fails never reaches the repair block. It routes as any
    clean-clone failure does — a retry, spent — and no pass record is written
    for it. Enough verdicts are supplied for the check to fail on every one
    until the ceiling is reached."""
    configure(target_root, test_command=SUITE_ALWAYS_RED)
    code, _, run_dir = drive(
        target_root, harness_root,
        [repairing(BEHAVIOUR_FINDING), *[PASS] * (MAX_RETRIES + 2)])
    events = events_of(run_dir)

    assert code == 2
    assert CLEAN_CLONE_FAILED in events
    assert CLEAN_CLONE_PASSED not in events
    assert ROUTED not in events
    assert not record_path(run_dir).exists()
    failure = events_of_kind(run_dir, CLEAN_CLONE_FAILED)[0]
    assert failure["retry_decision"] == "retry"
    assert failure["retry_stage"] == VERIFIER_STAGE["clean_clone"]["retry_stage"]
    state = read_state(run_dir)
    assert state["retry_count"] >= 1
    assert state["repair_pass_count"] == 0


def test_the_same_verdict_under_a_green_suite_does_route(repaired_run):
    """The control: the identical verdict with the check passing is the one
    that routed above, so the absence is the failed check's doing."""
    _, _, run_dir = repaired_run
    assert ROUTED in events_of(run_dir)
    assert record_path(run_dir).is_file()


# --------------------------------------------------------------------------
# The destination comes off the routing table
# --------------------------------------------------------------------------


@pytest.mark.parametrize("category", sorted(ROUTES))
def test_every_declared_category_enters_at_the_stage_its_route_names(
    target_root, harness_root, category,
):
    """Driven as a run per category rather than by calling the routing
    function, because what the story changed is where a run goes. Unlike the
    correction pass, the category chooses."""
    code, runner, run_dir = drive(
        target_root, harness_root, [repairing(FINDING_FOR[category]), PASS])

    assert code == 0
    assert record_of(run_dir)["stage"] == destination_of(category)
    assert runner.calls == entry_shape(destination_of(category))
    for stage in STAGE_NAMES[:STAGE_NAMES.index(destination_of(category))]:
        assert runner.calls.count(stage) == 1, stage


def test_two_categories_enter_once_at_the_earliest_of_their_stages(
    target_root, harness_root,
):
    """A verdict naming two categories takes one pass that enters at the
    earliest stage in workflow order among the resolved ones and runs forward
    through the other. Each finding in the record carries its own stage, in
    the order the verdict recorded them."""
    later, earlier = RECORD_FINDING, CHECKS_FINDING
    assert STAGE_NAMES.index(destination_of(later["category"])) > \
        STAGE_NAMES.index(destination_of(earlier["category"])), "the premise"

    code, runner, run_dir = drive(
        target_root, harness_root, [repairing(later, earlier), PASS])

    assert code == 0
    record = record_of(run_dir)
    assert record["stage"] == destination_of(earlier["category"])
    assert record["findings"] == [
        {**later, "stage": destination_of(later["category"])},
        {**earlier, "stage": destination_of(earlier["category"])},
    ]
    assert runner.calls == entry_shape(destination_of(earlier["category"]))
    assert events_of(run_dir).count(ROUTED) == 1


def test_the_same_category_enters_at_the_declared_stage_under_the_correction_pass(
    target_root, harness_root,
):
    """The contrast that says the two passes are different mechanisms: the
    same category, carried as a correctable finding, enters at the stage the
    correction declaration names rather than at the one the category routes
    to — and spends the correction budget, not the repair one."""
    category = CHECKS_FINDING["category"]
    assert destination_of(category) != CORRECTION_ENTRY, "the premise"
    code, runner, run_dir = drive(
        target_root, harness_root, [correcting(CHECKS_FINDING), PASS])

    assert code == 0
    assert runner.calls == entry_shape(CORRECTION_ENTRY)
    state = read_state(run_dir)
    assert state["correction_pass_count"] == 1
    assert state["repair_pass_count"] == 0
    assert not record_path(run_dir).exists()


def test_repair_destination_resolves_each_finding_and_orders_the_entry():
    """The routing function directly, over the fixture's own table, so the
    two halves of the answer — each finding's stage and the entry — are
    pinned to the table rather than to whichever a run meets first."""
    routing = story_coordinator.repair_destination(
        [RECORD_FINDING, CHECKS_FINDING], ROUTES, STAGE_NAMES)
    assert routing.unknown == []
    assert routing.stage == destination_of(CHECKS_CATEGORY)
    assert [one["stage"] for one in routing.resolved] == [
        destination_of(RECORD_CATEGORY), destination_of(CHECKS_CATEGORY)]

    refused = story_coordinator.repair_destination(
        [CHECKS_FINDING, UNKNOWN_FINDING], ROUTES, STAGE_NAMES)
    assert refused.stage is None
    assert refused.resolved == []
    assert refused.unknown == [UNDECLARED_CATEGORY]


# --------------------------------------------------------------------------
# The two passes are independent
# --------------------------------------------------------------------------


def test_a_correction_pass_and_a_repair_pass_spend_their_own_budgets(
    target_root, harness_root,
):
    """One run that takes both: a correction pass on the first verdict, a
    repair pass on the verification it returned to. Each count is one, the
    retry count is zero, and both records are in the run directory."""
    code, runner, run_dir = drive(
        target_root, harness_root,
        [correcting(CORRECTABLE_FINDING), repairing(BEHAVIOUR_FINDING), PASS])

    assert code == 0
    state = read_state(run_dir)
    assert state["correction_pass_count"] == 1
    assert state["repair_pass_count"] == 1
    assert state["retry_count"] == 0
    assert (run_dir / story_coordinator.correction_pass_result_file(
        CORRECTION_ARTIFACT, 1)).is_file()
    assert record_path(run_dir).is_file()
    events = events_of(run_dir)
    assert events.index(CORRECTION_ROUTED) < events.index(ROUTED)
    assert runner.calls == [*STAGE_NAMES,
                            *STAGE_NAMES[STAGE_NAMES.index(CORRECTION_ENTRY):],
                            *STAGE_NAMES[STAGE_NAMES.index(
                                destination_of(BEHAVIOUR_CATEGORY)):]]


def test_the_correction_pass_is_still_bounded_to_the_words_and_its_own_stage():
    """The correction pass's statement and declaration are as they were: it
    still says never behaviour, still names the clean-clone check as running
    after it, and its declaration still carries its own stage. The repair
    statement says the opposite about ordering, which is the difference."""
    correction = story_coordinator.correction_pass_statement(CORRECTION_ENTRY)
    repair = story_coordinator.repair_pass_statement(WRITING)
    assert "never behaviour" in correction
    assert "never behaviour" not in repair
    assert "clean-clone check on that verdict passed" in repair
    assert CORRECTION["stage"] == CORRECTION_ENTRY


# --------------------------------------------------------------------------
# A workflow declaring no repair pass routes nowhere
# --------------------------------------------------------------------------


def shape_of(code: int, runner: Runner, run_dir: Path) -> tuple:
    """What "the same run" means: the exit code, the stages invoked, the event
    stream and the retry count, compared whole."""
    return (code, tuple(runner.calls), tuple(events_of(run_dir)),
            read_state(run_dir)["retry_count"])


def without_the_declaration(tmp_path: Path, target_root: Path,
                            name: str = "no-repair-pass") -> Path:
    return probe_harness(tmp_path, target_root, name,
                         lambda stage: stage.pop("repair_pass"))


def test_a_workflow_declaring_no_repair_pass_runs_a_verdict_with_findings_as_one_without(
    target_root, tmp_path,
):
    """The criterion: with the key absent, a verdict carrying repairable
    findings routes nowhere and its event stream is the one a verdict
    carrying none produces."""
    other = second_target(target_root, tmp_path)
    plain = shape_of(*drive(target_root,
                            without_the_declaration(tmp_path, target_root),
                            [PASS]))
    carrying = shape_of(*drive(other,
                               without_the_declaration(tmp_path, other,
                                                       "no-repair-pass-other"),
                               [repairing(BEHAVIOUR_FINDING)]))
    assert plain == carrying
    assert plain[1] == tuple(STAGE_NAMES)
    assert not any(e.startswith("repair-pass") for e in plain[2])


def test_that_comparison_reports_a_difference_under_the_declaring_workflow(
    target_root, harness_root, tmp_path,
):
    """Control: the same two verdicts under the declaring workflow differ in
    the stages invoked and in the event stream, so the equality above is a
    fact about the missing key rather than about a comparison that cannot
    see the pass."""
    other = second_target(target_root, tmp_path)
    plain = shape_of(*drive(target_root, harness_root, [PASS]))
    carrying = shape_of(*drive(other, harness_root,
                               [repairing(BEHAVIOUR_FINDING), PASS]))
    assert plain != carrying
    assert plain[1] != carrying[1]
    assert plain[2] != carrying[2]


def test_removing_the_declaration_writes_no_record_and_completes(
    target_root, tmp_path,
):
    harness = without_the_declaration(tmp_path, target_root)
    code, runner, run_dir = drive(target_root, harness,
                                  [repairing(BEHAVIOUR_FINDING)])
    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert read_state(run_dir)["status"] == "completed"
    assert read_state(run_dir)["repair_pass_count"] == 0
    assert not record_path(run_dir).exists()
    assert clean_clone_events(run_dir) == [CLEAN_CLONE_PASSED]


# --------------------------------------------------------------------------
# The budget
# --------------------------------------------------------------------------


@pytest.fixture
def exhausted_run(target_root, harness_root):
    """Every verdict of this run carries a finding, so the bound is the only
    thing that ends it."""
    return drive(target_root, harness_root,
                 [repairing(BEHAVIOUR_FINDING)] * (BUDGET + 1))


def test_the_declared_budget_bounds_the_passes_per_run(exhausted_run):
    code, runner, run_dir = exhausted_run
    assert code == 0
    state = read_state(run_dir)
    assert state["repair_pass_count"] == BUDGET
    assert state["retry_count"] == 0
    assert events_of(run_dir).count(ROUTED) == BUDGET
    assert state["status"] == "completed"
    assert runner.calls.count(VERIFYING) == BUDGET + 1


def test_no_stage_is_invoked_past_the_budget(exhausted_run):
    """The verdict after the last pass carries findings and routes nowhere:
    no stage runs again, and the run completes without a further invocation."""
    _, runner, _ = exhausted_run
    counted = Counter(runner.calls)
    assert max(counted.values()) == BUDGET + 1


def test_the_spent_budgets_findings_are_recorded_in_the_event_stream(
    exhausted_run,
):
    """The bound is not silence: what the run declines to repair is named
    where a developer meets it, once, on the verdict the budget refused."""
    _, _, run_dir = exhausted_run
    recorded = events_of_kind(run_dir, RECORDED)
    assert len(recorded) == 1
    assert BEHAVIOUR_FINDING["finding"] in recorded[0]["message"]
    assert BEHAVIOUR_FINDING["location"] in recorded[0]["message"]
    events = events_of(run_dir)
    assert events.index(RECORDED) < events.index("story-completed")
    assert schema_validator.validate(history_of(run_dir), HISTORY_SCHEMA) == []


def test_each_pass_writes_its_own_numbered_record_and_the_refused_one_writes_none(
    exhausted_run,
):
    _, _, run_dir = exhausted_run
    for number in range(1, BUDGET + 1):
        assert record_of(run_dir, number)["pass"] == number, number
    assert not record_path(run_dir, BUDGET + 1).exists()


def test_a_run_within_the_budget_records_nothing_as_refused(repaired_run):
    """The control for the recorded event: a run that took one pass and whose
    next verdict carried nothing has no refusal to record."""
    _, _, run_dir = repaired_run
    assert events_of_kind(run_dir, RECORDED) == []


# --------------------------------------------------------------------------
# An undeclared category escalates
# --------------------------------------------------------------------------


@pytest.fixture
def unknown_category_run(target_root, harness_root):
    return drive(target_root, harness_root, [repairing(UNKNOWN_FINDING)])


def test_a_finding_naming_an_undeclared_category_escalates_the_run(
    unknown_category_run,
):
    code, runner, run_dir = unknown_category_run
    assert code == 2
    assert runner.calls == STAGE_NAMES
    assert read_state(run_dir)["status"] == "escalated"
    assert not (run_dir / "completion-report.md").exists()


def test_the_escalation_names_the_unknown_category_and_the_declared_ones(
    unknown_category_run,
):
    _, _, run_dir = unknown_category_run
    entry = history_of(run_dir)[-1]
    assert entry["event"] == "escalated"
    assert UNDECLARED_CATEGORY in entry["message"]
    for category in ROUTES:
        assert category in entry["message"], category
    assert UNDECLARED_CATEGORY in (run_dir / "escalation-summary.md").read_text(
        encoding="utf-8")


def test_that_escalation_spends_no_retry_budget_and_archives_nothing(
    unknown_category_run,
):
    _, _, run_dir = unknown_category_run
    state = read_state(run_dir)
    assert state["retry_count"] == 0
    assert state["repair_pass_count"] == 0
    assert not (run_dir / "attempts").exists()
    assert not (run_dir / "retry-history.json").exists()
    assert not record_path(run_dir).exists()


def test_the_escalation_comes_after_the_clean_clone_check_passed(
    unknown_category_run,
):
    """The block sits below the check, so even the refusal is made on a
    verified tree: the check ran and passed before the category was looked
    up."""
    _, _, run_dir = unknown_category_run
    events = events_of(run_dir)
    assert clean_clone_events(run_dir) == [CLEAN_CLONE_PASSED]
    assert events.index(CLEAN_CLONE_PASSED) < events.index("escalated")


def test_a_finding_naming_a_declared_category_routes_instead(repaired_run):
    code, _, run_dir = repaired_run
    assert code == 0
    assert "escalated" not in events_of(run_dir)


def test_one_undeclared_category_among_declared_ones_still_escalates(
    target_root, harness_root,
):
    """A verdict that could have been routed on its other finding is refused
    rather than partially obeyed."""
    code, _, run_dir = drive(
        target_root, harness_root,
        [repairing(BEHAVIOUR_FINDING, UNKNOWN_FINDING)])
    assert code == 2
    assert UNDECLARED_CATEGORY in history_of(run_dir)[-1]["message"]
    assert ROUTED not in events_of(run_dir)
    assert read_state(run_dir)["repair_pass_count"] == 0


# --------------------------------------------------------------------------
# The grant: the paths the findings name, the stage they resolve to, the
# attempt of the pass
# --------------------------------------------------------------------------


@pytest.fixture
def granted_run(governed_target, harness_root):
    """A repair pass to the writing stage on which it edits the file the
    finding named and one no finding named."""
    return drive(governed_target, harness_root,
                 [repairing(BEHAVIOUR_FINDING), PASS],
                 [None, the_named_file_and_another])


def test_the_granted_edit_lands_and_the_other_is_undone(granted_run):
    """The named file holds the pass's words; the other holds what the stage
    found, its words are under the discarded directory, and the revert record
    names it alone."""
    code, _, run_dir = granted_run
    assert code == 0
    assert content(run_dir, NOTES) == NOTES_REPAIRED
    assert content(run_dir, OTHER_NOTES) == OTHER_NOTES_AT_HEAD
    record = read_json(run_dir / REVERT_ARTIFACT)
    assert record["paths"] == [OTHER_NOTES]
    assert record["permitted"] is False
    (reverted,) = events_of_kind(run_dir, REVERTED_EVENT, WRITING)
    # The message names the ungranted file and not the granted one, matched
    # as whole tokens because the granted name is a substring of the other.
    assert paths_named_in(reverted["message"], (NOTES, OTHER_NOTES)) == {OTHER_NOTES}
    # Control for the absence half: the same matcher does see the granted
    # file when a message names it beside the other, so the set above being
    # short of NOTES.md is not the matcher failing to look.
    assert paths_named_in(
        f"the suite passes with {NOTES} and {OTHER_NOTES} reverted",
        (NOTES, OTHER_NOTES)) == {NOTES, OTHER_NOTES}
    assert (run_dir / CHECKED["discarded"] / OTHER_NOTES).read_text(
        encoding="utf-8") == OTHER_NOTES_REWORDED


def test_the_grant_is_an_event_before_the_checks(granted_run):
    _, _, run_dir = granted_run
    kinds = events_of(run_dir)
    (grant,) = events_of_kind(run_dir, GRANT_EVENT)
    assert grant["stage"] == WRITING
    assert NOTES in grant["message"]
    assert WRITING in grant["message"]
    assert kinds.index(GRANT_EVENT) < kinds.index(REVERTED_EVENT)
    assert f"may change {NOTES}" in (run_dir / "events.log").read_text(
        encoding="utf-8")
    assert schema_validator.validate(history_of(run_dir), HISTORY_SCHEMA) == []


def test_a_first_entry_before_the_pass_is_granted_nothing(
    governed_target, harness_root,
):
    """The control for the grant: the writing stage's first invocation,
    before any pass, edits the same file and is decided on it."""
    code, _, run_dir = drive(governed_target, harness_root, [PASS],
                             [the_named_file])
    assert code == 0
    record = read_json(run_dir / REVERT_ARTIFACT)
    assert record["paths"] == [NOTES]
    assert record["permitted"] is False
    assert content(run_dir, NOTES) == NOTES_AT_HEAD
    assert events_of_kind(run_dir, GRANT_EVENT) == []


def test_the_grant_reader_is_bounded_to_the_stage_and_the_attempt(granted_run):
    _, _, run_dir = granted_run
    state = story_coordinator.load_state(run_dir)
    stages = WORKFLOW["stages"]
    granted = story_coordinator.repair_pass_grants(
        run_dir, stages, state, WRITING, 1)
    assert granted == [NOTES]
    assert story_coordinator.grant_covers(granted, NOTES)
    assert not story_coordinator.grant_covers(granted, OTHER_NOTES)
    # A later attempt of the same stage, and another stage on the same
    # attempt, are each granted nothing.
    assert story_coordinator.repair_pass_grants(
        run_dir, stages, state, WRITING, 2) == []
    for other in STAGE_NAMES:
        if other != WRITING:
            assert story_coordinator.repair_pass_grants(
                run_dir, stages, state, other, 1) == [], other


def test_each_entered_stage_is_granted_only_its_own_findings_paths(tmp_path):
    """Two findings resolving to two stages in one record: each stage is
    granted the path of the finding that resolved to it and not the other's.
    Against a record written the way the coordinator writes one."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    routing = story_coordinator.repair_destination(
        [RECORD_FINDING, CHECKS_FINDING], ROUTES, STAGE_NAMES)
    write_json(record_path(run_dir), {
        "pass": 1, "attempt": 1, "stage": routing.stage,
        "findings": routing.resolved,
        "statement": story_coordinator.repair_pass_statement(routing.stage),
    })
    state = story_coordinator.RunState(story_id="story-001",
                                       branch="story/story-001")
    state.repair_pass_count = 1
    stages = WORKFLOW["stages"]

    assert story_coordinator.repair_pass_grants(
        run_dir, stages, state, destination_of(RECORD_CATEGORY), 1) \
        == [RECORD_FINDING["path"]]
    assert story_coordinator.repair_pass_grants(
        run_dir, stages, state, destination_of(CHECKS_CATEGORY), 1) \
        == [CHECKS_FINDING["path"]]
    assert story_coordinator.repair_pass_grants(
        run_dir, stages, state, destination_of(BEHAVIOUR_CATEGORY), 1) == []
    # The control: a state that has taken no pass reads the same record as
    # granting nothing, so the grant is the pass's and not the file's.
    state.repair_pass_count = 0
    assert story_coordinator.repair_pass_grants(
        run_dir, stages, state, destination_of(RECORD_CATEGORY), 1) == []


def test_the_grant_exempts_the_edit_from_both_checks_and_nothing_else(tmp_path):
    """The two checks the grant is read by, asked directly with the writing
    stage's own restrictions: with the grant the named file is neither a
    governed edit nor an ownership violation, an ungranted governed file is
    still governed, and with nothing granted the named file is governed too."""
    stage = WORKFLOW["stages"][0]
    restrictions = story_coordinator.restrictions_on(stage)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_json(run_dir / stage["changed_files"],
               {"modified": [NOTES, OTHER_NOTES], "created": [],
                "deleted": []})

    granted = [BEHAVIOUR_FINDING["path"]]
    assert story_coordinator.governed_edits(
        run_dir, stage["changed_files"], restrictions, granted).paths \
        == (OTHER_NOTES,)
    assert story_coordinator._ownership_violation(
        run_dir, stage["changed_files"], restrictions, granted) is None
    assert story_coordinator.governed_edits(
        run_dir, stage["changed_files"], restrictions, []).paths \
        == (NOTES, OTHER_NOTES)

    write_json(run_dir / stage["changed_files"],
               {"modified": [], "created": [NEW_FILE], "deleted": []})
    assert story_coordinator._ownership_violation(
        run_dir, stage["changed_files"], restrictions, [NEW_FILE]) is None
    violation = story_coordinator._ownership_violation(
        run_dir, stage["changed_files"], restrictions, [])
    assert violation is not None
    assert violation.path == NEW_FILE


def test_a_granted_creation_passes_the_ownership_check_and_an_ungranted_one_stops_the_run(
    governed_target, harness_root, tmp_path,
):
    """The ownership check at run level: the same creation on a pass, once
    granted by a finding naming it and once not."""
    other = second_target(governed_target, tmp_path)
    granted = {**BEHAVIOUR_FINDING, "path": NEW_FILE,
               "location": f"{NEW_FILE} - the whole file"}
    code, _, run_dir = drive(governed_target, harness_root,
                             [repairing(granted), PASS], [None, a_new_file])
    assert code == 0
    assert read_state(run_dir)["status"] == "completed"
    assert content(run_dir, NEW_FILE) == "created on a repair pass\n"
    (grant,) = events_of_kind(run_dir, GRANT_EVENT)
    assert NEW_FILE in grant["message"]

    code, _, run_dir = drive(other, harness_root,
                             [repairing(BEHAVIOUR_FINDING), PASS],
                             [None, a_new_file])
    assert code == 2
    assert read_state(run_dir)["status"] == "escalated"
    assert NEW_FILE in history_of(run_dir)[-1]["message"]


@pytest.fixture
def retried_after_pass(governed_target, harness_root):
    """A pass, then a failing verdict routing a retry that re-enters the
    writing stage on attempt 2, where it edits the file the pass named."""
    return drive(
        governed_target, harness_root,
        [repairing(BEHAVIOUR_FINDING), failing_into(BEHAVIOUR_CATEGORY), PASS],
        [None, the_named_file, the_named_file_again])


def test_a_stage_re_entered_on_a_later_attempt_inherits_no_grant(
    retried_after_pass,
):
    """One grant, on the pass; none on the retry, whose edit to the same file
    is decided and undone. Read in order off the history, so the one reverted
    event is shown to be the retry's rather than the pass's."""
    code, runner, run_dir = retried_after_pass
    assert code == 0, runner.calls
    assert runner.calls.count(WRITING) == 3
    state = story_coordinator.load_state(run_dir)
    assert state.retry_count == 1
    assert state.repair_pass_count == 1
    assert record_of(run_dir)["attempt"] == 1

    ordered = [entry["event"] for entry in history_of(run_dir)
               if entry.get("stage") == WRITING
               and entry.get("event") in (GRANT_EVENT, REVERTED_EVENT,
                                          PERMITTED_EVENT)]
    assert ordered == [GRANT_EVENT, REVERTED_EVENT]
    (reverted,) = events_of_kind(run_dir, REVERTED_EVENT, WRITING)
    assert NOTES in reverted["message"]
    assert (run_dir / CHECKED["discarded"] / NOTES).read_text(
        encoding="utf-8") == NOTES_REWORDED_AGAIN
    assert content(run_dir, NOTES) != NOTES_REWORDED_AGAIN
    # The pass's attempt wrote no revert record; the retry's holds the live one.
    archived = story_coordinator.attempt_dir(run_dir, 1)
    assert archived.is_dir()
    assert not (archived / REVERT_ARTIFACT).exists()
    assert (run_dir / REVERT_ARTIFACT).is_file()


# --------------------------------------------------------------------------
# Neither a stage name nor a category nor an artifact name lives in the source
# --------------------------------------------------------------------------


def test_no_name_this_mechanism_routes_on_is_written_in_orchestration_source():
    """Every name comes off the loaded definition. The control is the same
    names being present in the workflow this module built."""
    definition = json.dumps(WORKFLOW)
    names = [ARTIFACT, *ROUTES,
             *(n for n in STAGE_NAMES if n != conftest.VERIFYING_STAGE)]
    for name in names:
        assert name not in COORDINATOR_SOURCE, name
        assert name in definition, name


def test_the_artifact_name_travels_from_the_declaration_to_the_run_directory(
    target_root, tmp_path,
):
    renamed = "a-differently-named-repair-record.json"
    assert renamed not in COORDINATOR_SOURCE
    harness = probe_harness(
        tmp_path, target_root, "renamed-repair-pass",
        lambda stage: stage.__setitem__(
            "repair_pass", {**stage["repair_pass"], "result": renamed}))

    code, _, run_dir = drive(target_root, harness,
                             [repairing(BEHAVIOUR_FINDING), PASS])

    assert code == 0
    assert record_path(run_dir, 1, renamed).is_file()
    assert not record_path(run_dir, 1, ARTIFACT).exists()


def test_the_budget_comes_off_the_declaration_too(target_root, tmp_path):
    """A budget of one under a definition that otherwise declares two: one
    pass, and the second verdict's findings recorded rather than routed."""
    harness = probe_harness(
        tmp_path, target_root, "one-repair-pass",
        lambda stage: stage.__setitem__(
            "repair_pass", {**stage["repair_pass"], "budget": 1}))

    code, runner, run_dir = drive(
        target_root, harness, [repairing(BEHAVIOUR_FINDING)] * 2)

    assert code == 0
    assert events_of(run_dir).count(ROUTED) == 1
    assert events_of(run_dir).count(RECORDED) == 1
    assert read_state(run_dir)["repair_pass_count"] == 1
    assert runner.calls.count(VERIFYING) == 2


# --------------------------------------------------------------------------
# Pre-flight: a declaration that cannot be spent or cannot route refuses the run
# --------------------------------------------------------------------------


def created_nothing(target_root: Path, branch: str) -> list[str]:
    """What a refused run must not have left behind, as a list of violations,
    so the same statement can be made of the run that is supposed to create
    all of it — which is the control."""
    run_dir = run_dir_of(target_root)
    problems = []
    if run_dir.exists():
        problems.append("a run directory exists")
    if (run_dir / "state.json").exists():
        problems.append("state.json was written")
    if (run_dir / "events.log").exists():
        problems.append("an event stream was written")
    tree = conftest.run_root_for(target_root, "story-001")
    standing = head_of(tree) if tree.is_dir() else head_of(target_root)
    if standing != branch:
        problems.append(f"the repository was left on {standing}")
    return problems


def head_of(target_root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(target_root), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()


def budgeted(budget):
    """A mutation setting the declaration's budget to `budget`."""
    return lambda stage: stage.__setitem__(
        "repair_pass", {**stage["repair_pass"], "budget": budget})


def without_a_routing_table(stage: dict) -> None:
    """A mutation leaving the declaration in place with no table to resolve
    a category through."""
    stage.pop("on_failure")


@pytest.mark.parametrize("budget", [0, -1, "two", 1.5, True, None],
                         ids=["zero", "negative", "a word", "a fraction",
                              "a bool", "null"])
def test_a_budget_that_is_not_a_positive_integer_is_refused_before_any_run_state(
    target_root, tmp_path, capsys, budget,
):
    before = head_of(target_root)
    harness = probe_harness(tmp_path, target_root, "bad-repair-budget",
                            budgeted(budget))
    runner = Runner(target_root, [PASS])

    code = story_coordinator.run_story(
        "story-001", harness, target_root, runner)

    message = capsys.readouterr().err
    assert code == 1
    assert "repair_pass" in message
    assert "positive integer" in message
    assert VERIFYING in message
    assert runner.calls == []
    assert created_nothing(target_root, before) == []


def test_a_declaration_on_a_stage_with_no_routing_table_is_refused_too(
    target_root, tmp_path, capsys,
):
    before = head_of(target_root)
    harness = probe_harness(tmp_path, target_root, "no-routing-table",
                            without_a_routing_table)
    runner = Runner(target_root, [PASS])

    code = story_coordinator.run_story(
        "story-001", harness, target_root, runner)

    message = capsys.readouterr().err
    assert code == 1
    assert "retry_routing" in message
    assert VERIFYING in message
    assert runner.calls == []
    assert created_nothing(target_root, before) == []


def test_the_same_harness_with_the_declaration_repaired_creates_all_of_it(
    target_root, tmp_path,
):
    """The control for `created_nothing`: the same probe-built harness with
    the budget repaired creates the run directory, the state, the event
    stream and the branch."""
    before = head_of(target_root)
    harness = probe_harness(tmp_path, target_root, "repaired-budget",
                            budgeted(BUDGET))
    code, runner, _ = drive(target_root, harness, [PASS])

    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert created_nothing(target_root, before) == [
        "a run directory exists",
        "state.json was written",
        "an event stream was written",
        "the repository was left on story/story-001",
    ]


def test_the_check_reports_both_problems_over_a_stage_list_that_is_not_a_workflow():
    """The same defects said directly, over stage lists that are not a real
    workflow, so the accepted and refused declarations are pinned to the
    declarations rather than to whichever a run meets first."""
    problems = story_coordinator.repair_pass_problems([
        {"name": "alpha"},
        {"name": "beta", "repair_pass": {"result": ARTIFACT, "budget": 0}}])
    assert len(problems) == 2
    assert all("beta" in problem for problem in problems)
    assert any("positive integer" in problem for problem in problems)
    assert any("retry_routing" in problem for problem in problems)

    accepted = [{"name": "alpha"},
                {"name": "beta", "repair_pass": {"result": ARTIFACT, "budget": 1},
                 "on_failure": {"retry_routing": {
                     "a-category": {"stage": "alpha", "when": "whenever"}}}}]
    assert story_coordinator.repair_pass_problems(accepted) == []


def test_the_check_accepts_a_workflow_that_declares_no_repair_pass():
    """The switch, at pre-flight: a definition carrying no declaration is not
    checked."""
    assert story_coordinator.repair_pass_problems(
        [{"name": "alpha"}, {"name": "beta"}]) == []
    assert story_coordinator.repair_pass_problems(WORKFLOW["stages"]) == []


# --------------------------------------------------------------------------
# The schema
# --------------------------------------------------------------------------


def test_the_verification_result_schema_declares_the_field_as_optional():
    """Presence is the signal: the field is not required, and each entry
    requires exactly the five keys the record copies."""
    assert REPAIRABLE in VERDICT_SCHEMA["properties"]
    assert REPAIRABLE not in VERDICT_SCHEMA.get("required", [])
    item = VERDICT_SCHEMA["properties"][REPAIRABLE]["items"]
    assert set(item["required"]) == REQUIRED_OF_A_FINDING
    assert schema_validator.validate(repairing(BEHAVIOUR_FINDING),
                                     VERDICT_SCHEMA) == []
    assert schema_validator.validate(
        repairing(BEHAVIOUR_FINDING, CHECKS_FINDING), VERDICT_SCHEMA) == []
    assert schema_validator.validate(PASS, VERDICT_SCHEMA) == []


@pytest.mark.parametrize("missing", sorted(REQUIRED_OF_A_FINDING))
def test_an_entry_missing_any_required_field_is_refused(missing):
    """Control: the schema check above must be able to fail, on each field."""
    broken = repairing({k: v for k, v in BEHAVIOUR_FINDING.items()
                        if k != missing})
    assert schema_validator.validate(broken, VERDICT_SCHEMA), missing


def test_the_schema_description_says_when_the_field_is_read():
    """The other place the verifier is told what the field is for: read only
    on a passing verdict, after the clean-clone check, presence as the
    signal, and a category checked with retry_target's strictness."""
    description = VERDICT_SCHEMA["properties"][REPAIRABLE]["description"]
    assert "passing verdict" in description
    assert "clean-clone check" in description
    assert "presence" in description.lower()
    assert "retry_routing" in description
    assert "retry_target" in description
    assert "escalates" in description
    category = VERDICT_SCHEMA["properties"][REPAIRABLE]["items"][
        "properties"]["category"]["description"]
    assert "retry_routing" in category


# --------------------------------------------------------------------------
# The record reaches every stage prompt through the context assembler
# --------------------------------------------------------------------------


def shipped_definitions() -> dict[str, dict]:
    """Every workflow this repository ships, loaded the way a run loads it.
    The subject here is what ships, which is why these read it."""
    return {name: conftest.shipped_workflow(REPO_ROOT, name)
            for name in harness_config.workflow_names(REPO_ROOT)}


def shipped_prompts() -> list[str]:
    """Every prompt template a shipped stage names, derived from the
    definitions rather than listed."""
    return sorted({stage["prompt"] for definition in shipped_definitions().values()
                   for stage in definition["stages"]})


def test_every_shipped_stage_prompt_carries_the_slot():
    """Derived from both shipped definitions: every stage of every shipped
    workflow is told of a pass, because a pass runs forward through every
    stage after the one it enters."""
    slot = f"{{{{{REPAIR_FIELD}}}}}"
    prompts = shipped_prompts()
    assert prompts
    for prompt in prompts:
        text = (REPO_ROOT / "prompts" / prompt).read_text(encoding="utf-8")
        assert text.count(slot) == 1, prompt
        assert f"{{{{{CORRECTION_FIELD}}}}}" in text, prompt


def build_shipped_context(target_root: Path, **passes) -> dict:
    """A context built the way the coordinator builds one, against the
    shipped workflow, with whichever pass records the caller supplies."""
    harness_root = REPO_ROOT
    config = harness_config.load_config(target_root)
    rules = harness_config.load_rules(harness_root)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml"
                  ).read_text(encoding="utf-8")
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    return context_assembler.build_context(
        story_text=story_text,
        story=story_parser.parse(story_text,
                                 schema_validator.load_schema("story")),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=config,
        rules=rules,
        workflow=conftest.shipped_workflow(REPO_ROOT, "story-workflow"),
        retry_count=0,
        **passes,
    )


def test_the_assembler_renders_the_record_into_the_slot_and_none_without_it(
    target_root,
):
    """The record text supplied is what the slot renders, in every shipped
    template; omitted, the slot renders None and nothing else moves."""
    record = json.dumps({"pass": 1, "attempt": 1, "stage": "a-stage",
                         "findings": [BEHAVIOUR_FINDING],
                         "statement": "MARKER-STATEMENT"})
    with_record = build_shipped_context(target_root, repair_pass_result=record)
    without = build_shipped_context(target_root)
    assert with_record[REPAIR_FIELD] == record
    assert without[REPAIR_FIELD] is None

    for prompt in shipped_prompts():
        template = context_assembler.load_template(REPO_ROOT, prompt)
        rendered = context_assembler.render(template, with_record)
        assert BEHAVIOUR_FINDING["finding"] in rendered, prompt
        assert "MARKER-STATEMENT" in rendered, prompt
        assert "{{" not in rendered, prompt
        plain = context_assembler.render(template, without)
        assert BEHAVIOUR_FINDING["finding"] not in plain, prompt
        assert "{{" not in plain, prompt


def test_the_rendered_prompt_of_a_run_that_took_a_pass_carries_the_record_verbatim(
    repaired_run,
):
    """End to end, off the run directory: the record the coordinator wrote is
    the text the entered stage's prompt carries after the slot's label."""
    _, _, run_dir = repaired_run
    entry = destination_of(BEHAVIOUR_CATEGORY)
    prompt = rendered_prompt(run_dir, entry)
    assert record_path(run_dir).read_text(encoding="utf-8") in prompt
    assert f"{REPAIR_FIELD}:\nNone" not in prompt


def test_the_rendered_prompt_of_a_run_that_took_none_renders_none(
    unrepaired_run,
):
    _, _, run_dir = unrepaired_run
    for stage in STAGE_NAMES:
        assert f"{REPAIR_FIELD}:\nNone" in rendered_prompt(run_dir, stage), stage


# --------------------------------------------------------------------------
# What this repository ships: the declaration and the bound
# --------------------------------------------------------------------------


def shipped_verifier_prompts() -> list[str]:
    """The prompt of the stage carrying the declaration, in each shipped
    definition, so the bound is read where the verifier reads it."""
    prompts = []
    for definition in shipped_definitions().values():
        (stage,) = [stage for stage in definition["stages"]
                    if stage.get("repair_pass")]
        prompts.append(stage["prompt"])
    return prompts


def flowed(text: str) -> str:
    """`text` with its line wrapping removed, so a clause that straddles a
    newline is still found."""
    return " ".join(text.split())


def bounding_section(prompt: str) -> str:
    """What the shipped verifier prompt says from the start of the paragraph
    that first names the field to the next artifact it introduces. Bounded
    at the paragraph rather than at the mention, because the sentence that
    says what such a finding is precedes the sentence that names where it
    goes."""
    text = (REPO_ROOT / "prompts" / prompt).read_text(encoding="utf-8")
    assert REPAIRABLE in text, prompt
    mention = text.index(REPAIRABLE)
    start = text.rindex("\n\n", 0, mention)
    end = text.index("retry-guidance.json", mention)
    return flowed(text[start:end])


@pytest.mark.parametrize("prompt", shipped_verifier_prompts())
def test_each_shipped_verifier_prompt_bounds_what_may_go_in_the_field(prompt):
    """What a verifier has to know before it records one: judged correct, too
    small to fail the run, one stage in the file it names, not to the words
    alone, the category spelled as the table declares it, routed after the
    clean-clone check, and not a licence to revisit the work."""
    section = bounding_section(prompt)
    assert "too small to fail the run" in section
    assert "one stage" in section
    assert "words alone" in section
    assert "not to prose alone" in section
    assert "category" in section
    assert "spelled exactly as" in section
    assert "clean-clone check" in section
    assert "not a licence to revisit the work" in section
    assert "escalates" in section


def test_the_bound_is_stated_beside_the_correctable_findings_one():
    """Beside, not instead: both shipped verifier prompts still carry the
    correctable-findings section, and the correctable one still says words
    and never behaviour."""
    for prompt in shipped_verifier_prompts():
        text = flowed((REPO_ROOT / "prompts" / prompt).read_text(encoding="utf-8"))
        assert text.index(CORRECTABLE) < text.index(REPAIRABLE), prompt
        assert "never behaviour" in text, prompt
