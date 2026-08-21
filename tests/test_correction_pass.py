"""A minor, correct finding has somewhere to go.

A verifier that notices something real but too small to fail a run records it
in `correctable_findings`, and the coordinator re-enters the workflow at the
earliest stage the categories those findings name route to, runs through to
verification again, and completes. That is a retry's shape minus everything a
retry spends: no `retry_count`, no `attempts/attempt-N/` archive, no
`retry-history.json` entry, and the verdict that routed it still stands.

Every claim below is read off what a real run wrote. The runs go through
`story_coordinator.run_story` with a fake agent runner rather than through the
branch that routes them, so what is asserted is the coordinator's own
behaviour: the stages it invoked, the event stream, `state.json`, the run
directory listing and the prompts it rendered. Nothing here invokes a model.

The workflow those runs execute is built by `tests/conftest.py`'s builder and
materialized into a harness root this module owns, for the reason story-048
established: the subject is *the mechanism*, and the stage list, the retry
categories and the artifact names are inputs to it. Deriving them from what
this repository deploys would make a deployment fact into something this
module enforces. Every name below still comes off a definition rather than
being spelled at an assertion — it is the fixture's definition rather than the
shipped one.

Every absence asserted here carries a demonstration that it can fail:

  * "the pass spends no retry budget" — `retry_count` unchanged, no attempt
    archive, no retry-history entry — sits beside `retry_run`, a run through
    the same fixture that does spend all three;
  * "the guidance in force is empty at the entry to the corrected stage" sits
    beside a retry-routed entry to the *same* stage, where it is not empty;
  * "a verdict carrying no findings runs exactly as it did before this story"
    is a comparison against the same run under a workflow that declares no
    correction pass, and sits beside the same comparison with findings
    present, which must report a difference;
  * "no third invocation of any stage" sits beside the second pass being
    recorded as an event, so an exhausted budget is distinguishable from a
    mechanism that never ran;
  * "an undeclared category escalates" sits beside a declared one that routes;
  * "removing the declaration disables the mechanism" sits beside the same
    verdict under the declaring workflow, which routes;
  * "no name for this mechanism is written into orchestration source" sits
    beside the same names being present in the workflow definition, and beside
    a run that carries a renamed artifact all the way to the run directory.
"""
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

import schema_validator
import story_coordinator
from agent_runner import AgentResult
from conftest import StageRef, workflow_stage
import conftest

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
COORDINATOR_SOURCE = Path(story_coordinator.__file__).read_text(encoding="utf-8")

#: The artifact the fixture's declaration names. Deliberately not the name this
#: repository deploys: the record reaching the run directory under this name is
#: what says the coordinator reads the name off the declaration rather than
#: carrying one of its own.
CORRECTION_ARTIFACT = "correction-probe.json"

#: The workflow these runs execute. Four stages, because "the earliest stage in
#: workflow order among the categories the findings name" needs at least two
#: destinations to be earlier or later than each other, and a verifier to route
#: from. The clean-clone declaration is here because the correction pass is read
#: after that check has passed, which is an ordering this module asserts.
WORKFLOW = conftest.build_workflow(
    workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"}),
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
        correction_pass={"result": CORRECTION_ARTIFACT, "budget": 1},
        retry_routing={
            "the-behaviour": {"stage": StageRef(0),
                              "when": "the behaviour the story asked for is missing"},
            "the-checks": {"stage": StageRef(1),
                           "when": "the validation does not hold the behaviour"},
            "the-record": {"stage": StageRef(2),
                           "when": "the documents do not describe what shipped"},
        }),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="correction-pass-workflow",
)

STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, DOCUMENTING, VERIFYING = STAGE_NAMES

VERIFIER_STAGE = next(s for s in WORKFLOW["stages"] if "correction_pass" in s)
ROUTES = VERIFIER_STAGE["on_failure"]["retry_routing"]
BEHAVIOUR_CATEGORY, CHECKS_CATEGORY, RECORD_CATEGORY = list(ROUTES)
CORRECTION = VERIFIER_STAGE["correction_pass"]
ARTIFACT = CORRECTION["result"]
BUDGET = CORRECTION["budget"]

#: A category the fixture deliberately does not declare, which is the whole of
#: what makes it undeclared. Asserted rather than assumed, so a definition that
#: grew this route would fail here rather than turning the escalation cases
#: into routing cases nobody noticed.
UNDECLARED_CATEGORY = "a-category-this-workflow-does-not-declare"

MAX_RETRIES = json.loads(
    (REPO_ROOT / "rules" / "execution-rules.json").read_text(encoding="utf-8")
)["max_retries"]

CORRECTION_SCHEMA = schema_validator.load_schema("correction-pass")
VERDICT_SCHEMA = schema_validator.load_schema("verification-result")
HISTORY_SCHEMA = schema_validator.load_schema("execution-history")


def destination_of(category: str) -> str:
    """Where the fixture's table routes a category. Read, never written."""
    return ROUTES[category]["stage"]


#: Distinctive text so a search of a rendered prompt is looking for these
#: findings rather than for any sentence about corrections. A finding is
#: matched by its own words, which is what "the finding reached the stage"
#: means.
CHECKS_FINDING = {
    "location": "tests/test_sample.py::test_the_sample - the module docstring",
    "finding": "MARKER-CHECKS the docstring says three assertions and four follow",
    "correction": "MARKER-CHECKS-FIX delete the count and say 'the assertions below'",
    "category": CHECKS_CATEGORY,
}
RECORD_FINDING = {
    "location": ".harness/docs/ARCHITECTURE.md - the routing section",
    "finding": "MARKER-RECORD the paragraph names a stage that was renamed",
    "correction": "MARKER-RECORD-FIX name the stage the workflow declares today",
    "category": RECORD_CATEGORY,
}
UNKNOWN_FINDING = {
    "location": "src/app.py - the module comment",
    "finding": "MARKER-UNKNOWN the comment describes behaviour the code lost",
    "correction": "MARKER-UNKNOWN-FIX describe what the code does now",
    "category": UNDECLARED_CATEGORY,
}

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


def passing_with(*findings: dict) -> dict:
    return {**PASS, "correctable_findings": [dict(f) for f in findings]}


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


#: The context field the coordinator injects a correction-pass record under.
#: `conftest.BUILT_PROMPT_FIELDS` predates it and the fixture says a module
#: needing a field it does not list passes its own template, which is what this
#: does — rather than widening the shared list and changing what every other
#: module's built prompts render. Spelled once, here, and derived from there by
#: every assertion that looks for the record in a prompt.
CORRECTION_FIELD = "correction_pass_result"

PROMPTS = {
    name: (conftest.built_stage_prompt(name)
           + f"{CORRECTION_FIELD}:\n{{{{{CORRECTION_FIELD}}}}}\n")
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
                                         tmp_path / "correction-pass-harness",
                                         prompts=PROMPTS)


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_dir_of(target_root: Path, story_id: str = "story-001") -> Path:
    return target_root / ".harness" / "runs" / story_id


def history_of(run_dir: Path) -> list[dict]:
    return json.loads(
        (run_dir / "execution-history.json").read_text(encoding="utf-8"))


def events_of(run_dir: Path) -> list[str]:
    return [entry["event"] for entry in history_of(run_dir)]


def read_state(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text(encoding="utf-8"))


def record_of(run_dir: Path, number: int = 1, artifact: str = ARTIFACT) -> dict:
    return json.loads(
        (run_dir / story_coordinator.correction_pass_result_file(
            artifact, number)).read_text(encoding="utf-8"))


def rendered_prompt(run_dir: Path, stage: str, attempt: int = 1) -> str:
    """The prompt one stage was given, read back off the run directory.

    Through the coordinator's own name-shaping function rather than a second
    spelling of it here, so what this reads cannot drift from what was written.
    """
    return (run_dir / story_coordinator.prompt_file(stage, attempt)).read_text(
        encoding="utf-8")


class Runner:
    """A fake agent runner that writes each stage's declared artifacts.

    It also records, per invocation, the prompt it was handed and the guidance
    the coordinator had in force at that moment — read off `state.json` through
    the shared helper, which is where the coordinator puts the routing input.
    Recorded at the moment of the call rather than read afterwards, because
    "the guidance in force *at the entry to* the stage" is a question about
    that instant and a later read answers a different one.
    """

    def __init__(self, target_root: Path, verdicts: list[dict],
                 story_id: str = "story-001"):
        self.target_root = target_root
        self.run_dir = run_dir_of(target_root, story_id)
        self.verdicts = list(verdicts)
        self.calls: list[str] = []
        self.prompts: dict[str, list[str]] = {}
        self.guidance: dict[str, list[list[str]]] = {}

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None):
        self.calls.append(stage)
        self.prompts.setdefault(stage, []).append(prompt)
        self.guidance.setdefault(stage, []).append(
            conftest.guidance_in_force(self.run_dir))
        if stage == WRITING:
            (self.target_root / "src" / "app.py").write_text(
                "print('hello')\n# the story's change\n", encoding="utf-8")
            write_json(self.run_dir / conftest.CHANGED_FILES,
                       {"modified": ["src/app.py"], "created": [], "deleted": []})
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Did the work.\n", encoding="utf-8")
        elif stage == VALIDATING:
            write_json(self.run_dir / conftest.TEST_RESULTS, {
                "status": "passed", "tests_written": 2, "tests_run": 5,
                "tests_passed": 5, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES,
                       {"modified": [], "created": ["tests/test_app.py"],
                        "deleted": []})
        elif stage == DOCUMENTING:
            (self.run_dir / conftest.DOCUMENTATION_REPORT).write_text(
                "No changes needed.\n", encoding="utf-8")
            write_json(self.run_dir / conftest.DOCUMENTER_CHANGED_FILES,
                       {"modified": [], "created": [], "deleted": []})
        elif stage == VERIFYING:
            # A failed verdict accounts for the guidance in force for the
            # attempt it judges, reporting every entry unmet — the ordinary
            # under-delivery case, which routes as it always has.
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


def drive(target_root: Path, harness: Path, verdicts: list[dict]):
    """One run, returning its exit code, its runner and its run directory."""
    runner = Runner(target_root, verdicts)
    code = story_coordinator.run_story(
        "story-001", harness, target_root, runner)
    return code, runner, run_dir_of(target_root)


def probe_harness(tmp_path: Path, target_root: Path, name: str, mutate) -> Path:
    """A harness root carrying the built definition with the verifier mutated,
    and `target_root` configured to run it.

    The same idiom the clean-clone module uses for its own declaration: a probe
    workflow derived from the fixture by changing the single declaration the
    test is about, so "removing the key disables the mechanism" is driven as a
    run rather than argued from source.
    """
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
    """A copy of the target repository, so two coordinators can be compared.

    A run refuses to re-run a story in a target that has already run it
    (story-027), and what these comparisons are between is two coordinators
    given the same story rather than one target given two stories.
    """
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


@pytest.fixture
def corrected_run(target_root, harness_root):
    """The central run: a passing verdict carrying one correctable finding.

    The finding names the category that routes to the validating stage, so the
    workflow re-enters there and runs forward to verification again, which
    passes carrying nothing.
    """
    return drive(target_root, harness_root,
                 [passing_with(CHECKS_FINDING), PASS])


@pytest.fixture
def retry_run(target_root, harness_root):
    """The control every "spends nothing" assertion needs: a run that spends.

    A failed verdict naming the same category routes a retry to the same stage,
    so the two runs differ in the mechanism under test and in nothing else.
    """
    return drive(target_root, harness_root,
                 [failing_into(CHECKS_CATEGORY), PASS])


@pytest.fixture
def uncorrected_run(target_root, harness_root):
    """A passing verdict carrying no findings, under the declaring workflow."""
    return drive(target_root, harness_root, [PASS])


# --------------------------------------------------------------------------
# The fixture is what it claims to be
# --------------------------------------------------------------------------


def test_the_undeclared_category_really_is_undeclared():
    """The premise the escalation cases rest on. If the fixture declared this
    category they would be testing routing rather than refusal."""
    assert UNDECLARED_CATEGORY not in ROUTES
    assert set(ROUTES) == {BEHAVIOUR_CATEGORY, CHECKS_CATEGORY, RECORD_CATEGORY}


def test_the_fixtures_two_correctable_categories_route_to_different_stages():
    """The premise the earliest-stage case rests on: the two destinations are
    distinguishable, and the record's is the earlier of them."""
    checks, record = destination_of(CHECKS_CATEGORY), destination_of(RECORD_CATEGORY)
    assert checks != record
    assert STAGE_NAMES.index(checks) < STAGE_NAMES.index(record)


# --------------------------------------------------------------------------
# The routed pass
# --------------------------------------------------------------------------


def test_the_destination_stage_runs_again_within_the_same_run(corrected_run):
    code, runner, run_dir = corrected_run
    destination = destination_of(CHECKS_CATEGORY)
    resumed = STAGE_NAMES[STAGE_NAMES.index(destination):]

    assert code == 0
    assert runner.calls == [*STAGE_NAMES, *resumed]
    assert read_state(run_dir)["status"] == "completed"
    assert (run_dir / "completion-report.md").is_file()


def test_the_run_records_the_pass_in_its_own_event_stream(corrected_run):
    _, _, run_dir = corrected_run
    routed = [e for e in history_of(run_dir)
              if e["event"] == "correction-pass-routed"]
    assert len(routed) == 1
    assert routed[0]["stage"] == VERIFYING
    assert routed[0]["retry_stage"] == destination_of(CHECKS_CATEGORY)
    assert schema_validator.validate(history_of(run_dir), HISTORY_SCHEMA) == []


def test_the_pass_is_read_after_the_clean_clone_check_has_passed(corrected_run):
    """Ordering off the event stream, which is where the run records it."""
    _, _, run_dir = corrected_run
    events = events_of(run_dir)
    assert events.index("verification-passed") < events.index("clean-clone-passed")
    assert events.index("clean-clone-passed") < events.index("correction-pass-routed")


def test_a_clean_clone_failure_routes_before_any_correction_pass_is_read(
    target_root, harness_root,
):
    """The control for that ordering: when the check fails the verdict routes
    as a clean-clone failure and no correction pass is read at all, even though
    the same verdict carries a finding."""
    configure(target_root, test_command="sh -c 'exit 1'")
    code, _, run_dir = drive(
        target_root, harness_root,
        [passing_with(CHECKS_FINDING)] * (MAX_RETRIES + 1))

    assert code == 2
    events = events_of(run_dir)
    assert "clean-clone-failed" in events
    assert "correction-pass-routed" not in events
    assert not (run_dir / story_coordinator.correction_pass_result_file(
        ARTIFACT, 1)).exists()


def test_the_record_the_coordinator_wrote_is_in_the_run_directory(corrected_run):
    _, _, run_dir = corrected_run
    record = record_of(run_dir)

    assert record["pass"] == 1
    assert record["attempt"] == 1
    assert record["stage"] == destination_of(CHECKS_CATEGORY)
    # Copied verbatim: the stage acts on the words the verifier wrote.
    assert record["findings"] == [CHECKS_FINDING]


def test_that_record_validates_against_the_schema_the_manifest_registers(
    corrected_run,
):
    _, _, run_dir = corrected_run
    manifest = json.loads(
        (REPO_ROOT / "schemas" / "manifest.json").read_text(encoding="utf-8"))
    assert "correction-pass" in manifest["schemas"]
    assert schema_validator.validate(record_of(run_dir), CORRECTION_SCHEMA) == []


def test_a_record_missing_a_required_field_is_reported_by_that_schema():
    """Control: the validation above must be able to fail. A record with the
    destination stripped out is exactly the record a coordinator that routed
    somewhere without saying where would have written."""
    record = {"pass": 1, "attempt": 1, "stage": VALIDATING,
              "findings": [CHECKS_FINDING]}
    assert schema_validator.validate(record, CORRECTION_SCHEMA) == []
    assert schema_validator.validate(
        {k: v for k, v in record.items() if k != "stage"}, CORRECTION_SCHEMA)
    assert schema_validator.validate(
        {**record, "findings": [{k: v for k, v in CHECKS_FINDING.items()
                                 if k != "category"}]}, CORRECTION_SCHEMA)


def test_the_finding_reaches_the_destination_stage_in_its_rendered_prompt(
    corrected_run,
):
    """The record on disk and the prompt the stage was given say the same
    thing. Read off the run directory, which is where a reader of a finished
    run meets it."""
    _, runner, run_dir = corrected_run
    destination = destination_of(CHECKS_CATEGORY)
    prompt = rendered_prompt(run_dir, destination)

    for words in (CHECKS_FINDING["location"], CHECKS_FINDING["finding"],
                  CHECKS_FINDING["correction"]):
        assert words in prompt, words
    assert "{{" not in prompt


def test_the_same_stage_was_told_nothing_of_a_pass_on_its_first_invocation(
    corrected_run,
):
    """The control the assertion above needs: the finding is in the second
    rendering because the pass put it there, not because the template says
    those words whatever happens."""
    _, runner, _ = corrected_run
    destination = destination_of(CHECKS_CATEGORY)
    first, second = runner.prompts[destination]

    assert CHECKS_FINDING["finding"] not in first
    assert CHECKS_FINDING["finding"] in second


def test_the_verifier_that_runs_after_the_pass_is_told_of_it(corrected_run):
    """The stage that judges the corrected work sees why it is judging it
    again — the second verification's prompt carries the record, the first's
    does not."""
    _, runner, _ = corrected_run
    first, second = runner.prompts[VERIFYING]
    assert CHECKS_FINDING["finding"] not in first
    assert CHECKS_FINDING["finding"] in second


# --------------------------------------------------------------------------
# The pass spends nothing a retry spends
# --------------------------------------------------------------------------


def test_the_pass_leaves_the_retry_budget_untouched(corrected_run):
    _, _, run_dir = corrected_run
    assert read_state(run_dir)["retry_count"] == 0
    assert not (run_dir / "attempts").exists()
    assert not (run_dir / "retry-history.json").exists()


def test_a_run_that_does_spend_the_retry_budget_shows_all_three(retry_run):
    """The control beside it. Each absence above is asserted here as a
    presence, against a run through the same fixture that routed a retry to
    the same stage — so the three assertions are looking at the right places
    and would report a pass that spent any of them."""
    code, _, run_dir = retry_run
    assert code == 0
    assert read_state(run_dir)["retry_count"] == 1
    assert (run_dir / "attempts" / "attempt-1").is_dir()
    assert (run_dir / "retry-history.json").is_file()


def test_the_pass_takes_no_attempt_number_of_its_own(corrected_run):
    """The re-run happens within the attempt the verdict judged, which is what
    the record says and what the prompt filenames say."""
    _, _, run_dir = corrected_run
    assert record_of(run_dir)["attempt"] == 1
    destination = destination_of(CHECKS_CATEGORY)
    assert (run_dir / story_coordinator.prompt_file(destination, 1)).is_file()
    assert not (run_dir / story_coordinator.prompt_file(destination, 2)).exists()


def test_the_pass_does_not_turn_the_passing_verdict_into_a_failing_one(
    corrected_run,
):
    """Read off the archived iteration, which is the verdict as it was judged
    rather than as the run directory's live artifact ended up."""
    _, _, run_dir = corrected_run
    archived = json.loads(
        (run_dir / "verification" / "iteration-1.json").read_text(encoding="utf-8"))
    assert archived["status"] == "passed"
    assert archived["correctable_findings"] == [CHECKS_FINDING]
    assert read_state(run_dir)["verification_iterations"] == 2


def test_a_failed_verdict_archives_as_failed(retry_run):
    """Control: the archive really does record the verdict's own status, so
    the assertion above is not reading a field that says "passed" whatever
    happened."""
    _, _, run_dir = retry_run
    archived = json.loads(
        (run_dir / "verification" / "iteration-1.json").read_text(encoding="utf-8"))
    assert archived["status"] == "failed"


def test_the_state_counts_the_pass_and_is_saved_with_the_rest(corrected_run):
    _, _, run_dir = corrected_run
    state = read_state(run_dir)
    assert state["correction_pass_count"] == 1
    assert state["retry_count"] == 0


def test_a_run_that_takes_no_pass_counts_none(uncorrected_run):
    _, _, run_dir = uncorrected_run
    assert read_state(run_dir)["correction_pass_count"] == 0


# --------------------------------------------------------------------------
# The guidance in force
# --------------------------------------------------------------------------


def test_no_guidance_is_in_force_at_the_entry_to_the_corrected_stage(
    corrected_run,
):
    """The passing verdict that routed the pass wrote no guidance, so the
    stage is entered with none in force — read off `state.json` at the moment
    the stage was invoked."""
    _, runner, _ = corrected_run
    destination = destination_of(CHECKS_CATEGORY)
    assert runner.guidance[destination] == [[], []]


def test_a_retry_routed_to_the_same_stage_does_carry_guidance(retry_run):
    """The control beside it: the same stage, entered the other way, has
    guidance in force. An empty list above is therefore a fact about the
    correction pass rather than about where the test is looking."""
    _, runner, _ = retry_run
    destination = destination_of(CHECKS_CATEGORY)
    entries = runner.guidance[destination]
    assert entries[0] == []
    assert entries[1] != []


# --------------------------------------------------------------------------
# A verdict carrying nothing is the run it always was
# --------------------------------------------------------------------------


def shape_of(code: int, runner: Runner, run_dir: Path) -> tuple:
    """What "the same run" means: the exit code, the stages invoked, the event
    stream and the retry count. Compared whole rather than one field at a
    time, so a difference anywhere in it is reported."""
    return (code, tuple(runner.calls), tuple(events_of(run_dir)),
            read_state(run_dir)["retry_count"])


def without_the_declaration(tmp_path: Path, target_root: Path,
                            name: str = "no-correction-pass") -> Path:
    """The same workflow with the correction-pass declaration removed, which
    is the coordinator with this branch disabled: the key is the switch."""
    return probe_harness(tmp_path, target_root, name,
                         lambda stage: stage.pop("correction_pass"))


def test_a_verdict_carrying_no_findings_runs_as_it_did_before_this_story(
    target_root, harness_root, tmp_path,
):
    """The declaring run compared against the same run with the mechanism
    switched off. Same exit code, same stages, same events, same retry count."""
    other = second_target(target_root, tmp_path)
    with_mechanism = shape_of(*drive(target_root, harness_root, [PASS]))
    without = shape_of(*drive(other, without_the_declaration(tmp_path, other),
                              [PASS]))
    assert with_mechanism == without


def test_that_comparison_reports_a_difference_when_findings_are_present(
    target_root, harness_root, tmp_path,
):
    """Control: the comparison above must be able to fail. The same two
    coordinators given a verdict that *does* carry a finding differ in the
    stages invoked and in the event stream."""
    other = second_target(target_root, tmp_path)
    verdicts = [passing_with(CHECKS_FINDING), PASS]
    with_mechanism = shape_of(*drive(target_root, harness_root, list(verdicts)))
    without = shape_of(*drive(other, without_the_declaration(tmp_path, other),
                              list(verdicts)))
    assert with_mechanism != without
    assert with_mechanism[1] != without[1]
    assert with_mechanism[2] != without[2]


def test_removing_the_declaration_disables_the_mechanism_entirely(
    target_root, tmp_path,
):
    """Driven as a run against a workflow carrying no such declaration, with
    the same coordinator module every other case here uses."""
    harness = without_the_declaration(tmp_path, target_root)
    code, runner, run_dir = drive(target_root, harness,
                                  [passing_with(CHECKS_FINDING)])
    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert read_state(run_dir)["status"] == "completed"
    assert not any(e.startswith("correction-pass") for e in events_of(run_dir))
    assert not (run_dir / story_coordinator.correction_pass_result_file(
        ARTIFACT, 1)).exists()


def test_the_same_verdict_under_the_declaring_workflow_does_route(corrected_run):
    """The control beside it: the verdict the run above completed on is one
    the declaring workflow re-enters at a stage."""
    _, runner, run_dir = corrected_run
    assert runner.calls != STAGE_NAMES
    assert "correction-pass-routed" in events_of(run_dir)


# --------------------------------------------------------------------------
# One pass per run
# --------------------------------------------------------------------------


@pytest.fixture
def exhausted_run(target_root, harness_root):
    """Every verdict of this run carries a finding, so the bound is the only
    thing that ends it."""
    return drive(target_root, harness_root,
                 [passing_with(CHECKS_FINDING)] * (BUDGET + 1))


def test_only_one_correction_pass_runs_per_run(exhausted_run):
    code, runner, run_dir = exhausted_run
    assert code == 0
    assert read_state(run_dir)["correction_pass_count"] == BUDGET
    assert events_of(run_dir).count("correction-pass-routed") == BUDGET
    assert read_state(run_dir)["status"] == "completed"


def test_no_stage_is_invoked_a_third_time(exhausted_run):
    _, runner, _ = exhausted_run
    counted = Counter(runner.calls)
    assert max(counted.values()) == 2
    assert counted[VERIFYING] == 2


def test_the_second_verdicts_findings_are_recorded_in_the_event_stream(
    exhausted_run,
):
    """The bound is not silence: what the run declines to correct is named
    where a developer meets it."""
    _, _, run_dir = exhausted_run
    recorded = [e for e in history_of(run_dir)
                if e["event"] == "correction-pass-recorded"]
    assert len(recorded) == 1
    assert CHECKS_FINDING["finding"] in recorded[0]["message"]
    assert CHECKS_FINDING["location"] in recorded[0]["message"]
    assert schema_validator.validate(history_of(run_dir), HISTORY_SCHEMA) == []


def test_the_spent_budget_writes_no_second_record(exhausted_run):
    _, _, run_dir = exhausted_run
    assert (run_dir / story_coordinator.correction_pass_result_file(
        ARTIFACT, 1)).is_file()
    assert not (run_dir / story_coordinator.correction_pass_result_file(
        ARTIFACT, 2)).exists()


# --------------------------------------------------------------------------
# The earliest destination in workflow order
# --------------------------------------------------------------------------


def test_two_categories_enter_at_the_earliest_stage_and_reach_the_other(
    target_root, harness_root,
):
    """A verdict naming both categories enters at the earlier destination and
    reaches the later one on the way back to verification — one pass rather
    than one pass apiece. The findings are given later-first, so "earliest"
    cannot be satisfied by taking the first one named."""
    code, runner, run_dir = drive(
        target_root, harness_root,
        [passing_with(RECORD_FINDING, CHECKS_FINDING), PASS])

    earliest = destination_of(CHECKS_CATEGORY)
    later = destination_of(RECORD_CATEGORY)
    assert code == 0
    assert record_of(run_dir)["stage"] == earliest
    assert runner.calls == [*STAGE_NAMES,
                            *STAGE_NAMES[STAGE_NAMES.index(earliest):]]
    assert runner.calls.count(later) == 2
    assert record_of(run_dir)["findings"] == [RECORD_FINDING, CHECKS_FINDING]


def test_a_verdict_naming_only_the_later_category_enters_there(
    target_root, harness_root,
):
    """The control: the destination follows the categories named rather than
    always being the earliest stage that has a route."""
    code, runner, run_dir = drive(
        target_root, harness_root, [passing_with(RECORD_FINDING), PASS])

    later = destination_of(RECORD_CATEGORY)
    assert code == 0
    assert record_of(run_dir)["stage"] == later
    assert runner.calls == [*STAGE_NAMES,
                            *STAGE_NAMES[STAGE_NAMES.index(later):]]
    assert runner.calls.count(destination_of(CHECKS_CATEGORY)) == 1


# --------------------------------------------------------------------------
# An undeclared category escalates
# --------------------------------------------------------------------------


@pytest.fixture
def unknown_category_run(target_root, harness_root):
    return drive(target_root, harness_root, [passing_with(UNKNOWN_FINDING)])


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
    """Rather than routing it somewhere by default, which is the drift the
    routing table exists to remove."""
    _, _, run_dir = unknown_category_run
    entry = history_of(run_dir)[-1]
    assert entry["event"] == "escalated"
    assert UNDECLARED_CATEGORY in entry["message"]
    for category in ROUTES:
        assert category in entry["message"], category
    assert UNDECLARED_CATEGORY in (run_dir / "escalation-summary.md").read_text(
        encoding="utf-8")


def test_that_escalation_spends_no_retry_budget_either(unknown_category_run):
    _, _, run_dir = unknown_category_run
    assert read_state(run_dir)["retry_count"] == 0
    assert not (run_dir / "attempts").exists()
    assert not (run_dir / "retry-history.json").exists()
    assert not (run_dir / story_coordinator.correction_pass_result_file(
        ARTIFACT, 1)).exists()


def test_a_finding_naming_a_declared_category_routes_instead(corrected_run):
    """The control beside the escalation: the only difference between the two
    verdicts is whether the workflow declares the category they name."""
    code, _, run_dir = corrected_run
    assert code == 0
    assert read_state(run_dir)["status"] == "completed"
    assert "escalated" not in events_of(run_dir)


def test_one_undeclared_category_among_declared_ones_still_escalates(
    target_root, harness_root,
):
    """A verdict that could have been routed on its other finding is refused
    rather than partially obeyed."""
    code, _, run_dir = drive(
        target_root, harness_root,
        [passing_with(CHECKS_FINDING, UNKNOWN_FINDING)])
    assert code == 2
    assert UNDECLARED_CATEGORY in history_of(run_dir)[-1]["message"]
    assert "correction-pass-routed" not in events_of(run_dir)


# --------------------------------------------------------------------------
# Neither a stage name nor a category nor an artifact name lives in the source
# --------------------------------------------------------------------------


def test_no_name_this_mechanism_routes_on_is_written_in_orchestration_source():
    """Every name comes off the loaded definition. The control is the same
    names being present in the workflow this module built, so an assertion
    that finds nothing in the source is looking for names that exist."""
    definition = json.dumps(WORKFLOW)
    names = [ARTIFACT, *ROUTES,
             *(n for n in STAGE_NAMES if n != conftest.VERIFYING_STAGE)]
    for name in names:
        assert name not in COORDINATOR_SOURCE, name
        assert name in definition, name


def test_the_artifact_name_travels_from_the_declaration_to_the_run_directory(
    target_root, tmp_path,
):
    """The behavioural half: a name nothing in orchestration knows about is
    what the run writes, and the fixture's own name is then absent."""
    renamed = "a-differently-named-correction-record.json"
    assert renamed not in COORDINATOR_SOURCE
    harness = probe_harness(
        tmp_path, target_root, "renamed-correction-pass",
        lambda stage: stage.__setitem__(
            "correction_pass", {**stage["correction_pass"], "result": renamed}))

    code, _, run_dir = drive(target_root, harness,
                             [passing_with(CHECKS_FINDING), PASS])

    assert code == 0
    assert (run_dir / story_coordinator.correction_pass_result_file(
        renamed, 1)).is_file()
    assert not (run_dir / story_coordinator.correction_pass_result_file(
        ARTIFACT, 1)).exists()


def test_the_declared_budget_is_what_bounds_the_run(target_root, tmp_path):
    """The budget comes off the declaration too: a workflow declaring none of
    them routes nothing, which is the same switch read at its other end."""
    harness = probe_harness(
        tmp_path, target_root, "zero-budget-correction-pass",
        lambda stage: stage.__setitem__(
            "correction_pass", {**stage["correction_pass"], "budget": 0}))

    code, runner, run_dir = drive(target_root, harness,
                                  [passing_with(CHECKS_FINDING)])

    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert "correction-pass-routed" not in events_of(run_dir)
    assert "correction-pass-recorded" in events_of(run_dir)


# --------------------------------------------------------------------------
# Words and not behaviour
# --------------------------------------------------------------------------


def test_the_suite_the_run_checks_passes_unchanged_across_the_pass(
    corrected_run,
):
    """The clean-clone check runs the target's configured suite once per
    verification, so a run that took a correction pass ran it twice — and both
    runs passed. The pass changed prose and left the suite where it was."""
    _, _, run_dir = corrected_run
    passes = [e for e in history_of(run_dir) if e["event"] == "clean-clone-passed"]
    assert len(passes) == 2
    record = json.loads(
        (run_dir / conftest.CLEAN_CLONE_RESULT).read_text(encoding="utf-8"))
    assert record["ran"] is True
    assert record["exit_code"] == 0


def test_the_constraint_is_stated_where_the_verifier_reads_what_may_go_in_the_field():
    """The shipped verifier prompt is the subject here: it is what tells the
    stage what belongs in the field, so this reads what this repository
    ships."""
    prompt = (REPO_ROOT / "prompts" / "verifier.md").read_text(encoding="utf-8")
    field = "correctable_findings"
    assert field in prompt
    section = prompt[prompt.index(field):]
    # The three things a verifier has to know before it records a finding:
    # what the pass may change, what stays in the field it is not, and that a
    # finding is one a single stage can close.
    assert "never behaviour" in section
    assert "words alone" in section
    assert "unverified" in section
    assert "one stage" in section


def test_the_verification_result_schema_declares_the_field_as_optional():
    """Presence is the signal: the field is not required, and it carries no
    sibling key saying whether it means anything."""
    field = "correctable_findings"
    assert field in VERDICT_SCHEMA["properties"]
    assert field not in VERDICT_SCHEMA.get("required", [])
    item = VERDICT_SCHEMA["properties"][field]["items"]
    assert set(item["required"]) == {"location", "finding", "correction",
                                     "category"}
    assert schema_validator.validate(passing_with(CHECKS_FINDING),
                                     VERDICT_SCHEMA) == []
    assert schema_validator.validate(PASS, VERDICT_SCHEMA) == []


def test_a_verdict_whose_finding_omits_a_required_field_is_reported():
    """Control: the schema check above must be able to fail."""
    broken = passing_with({k: v for k, v in CHECKS_FINDING.items()
                           if k != "correction"})
    assert schema_validator.validate(broken, VERDICT_SCHEMA)
