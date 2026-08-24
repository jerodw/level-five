"""A minor, correct finding has somewhere to go.

A verifier that notices something real but too small to fail a run records it
in `correctable_findings`, and the coordinator re-enters the workflow at the
stage the workflow's `correction_pass` declaration names, runs through to
verification again, and completes. That is a retry's shape minus everything a
retry spends: no `retry_count`, no `attempts/attempt-N/` archive, no
`retry-history.json` entry, and the verdict that routed it still stands.

The entry stage is declared rather than derived from a finding's category, and
the clean-clone check runs after the pass rather than before it — both
story-067's, and both driven here as runs rather than argued from source. What
a finding's category still does is name the kind of prose; one the workflow
does not declare still escalates.

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
    a run that carries a renamed artifact all the way to the run directory;
  * "the clean-clone check ran once on a run that took a pass" sits beside the
    same run's two passing verdicts, and beside a run whose every passing
    verdict does produce a check;
  * "a refused declaration created no run state" sits beside the same harness
    with the declaration repaired, which creates all of it;
  * "the statement no longer tells the stage to establish the suite" sits
    beside the clause it used to carry, constructed here in the test, which the
    same check reports.
"""
import json
import shutil
import subprocess
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


def shipped_correction_stage() -> dict:
    """The stage this repository's own workflow enters a correction pass at.

    A live harness artifact and a legitimate subject: the cases that use it ask
    what *this repository* permits and instructs the stage it ships a pass to,
    which cannot be asked of a fixture. Every name is still derived — the
    declaration names the stage and the stage names its own prompt.
    """
    shipped = conftest.shipped_workflow()
    declaration = story_coordinator.correction_pass_declaration(
        shipped["stages"])
    return next(stage for stage in shipped["stages"]
                if stage["name"] == declaration[ENTRY_KEY])

#: The artifact the fixture's declaration names. Deliberately not the name this
#: repository deploys: the record reaching the run directory under this name is
#: what says the coordinator reads the name off the declaration rather than
#: carrying one of its own.
CORRECTION_ARTIFACT = "correction-probe.json"

#: The workflow these runs execute. Four stages, because "the entry stage is
#: the declared one rather than the one a category routes to" needs categories
#: routing somewhere other than where the declaration points, and a verifier to
#: route from. The clean-clone declaration is here because the correction pass
#: is read before that check and the check runs on the verification the pass
#: returns to, which is an ordering this module asserts.
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
        correction_pass={"result": CORRECTION_ARTIFACT, "budget": 1,
                         "stage": StageRef(2)},
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

#: Where a pass enters, read off the declaration rather than written here. Every
#: assertion about an entry point below derives it from this, so a fixture whose
#: declaration moved would move the assertions with it rather than failing.
ENTRY_KEY = "stage"
DECLARED_ENTRY = CORRECTION[ENTRY_KEY]

#: The declared categories whose route is somewhere other than the declared
#: entry. They are what makes "declared rather than derived" observable: a
#: verdict carrying one of these enters at the declared stage and not at the one
#: its category names. Derived, so the premise below can check it holds.
DIVERTED_CATEGORIES = [category for category in ROUTES
                       if ROUTES[category]["stage"] != DECLARED_ENTRY]

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
BEHAVIOUR_FINDING = {
    "location": "src/app.py - the docstring of the sample function",
    "finding": "MARKER-BEHAVIOUR the docstring wraps mid-word",
    "correction": "MARKER-BEHAVIOUR-FIX rewrap the line",
    "category": BEHAVIOUR_CATEGORY,
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

#: One finding per declared category, keyed by the category the finding itself
#: carries rather than by a second spelling of it. A case that wants "a verdict
#: categorised for this category" looks it up here.
FINDING_FOR = {finding["category"]: finding
               for finding in (BEHAVIOUR_FINDING, CHECKS_FINDING, RECORD_FINDING)}

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
                 allowed_tools=None, max_budget_usd=None):
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

    The finding names a category that routes somewhere other than the declared
    entry, so a run that re-entered where the category points would be visibly
    different from this one. The workflow re-enters at the declared stage and
    runs forward to verification again, which passes carrying nothing.
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


def test_the_declared_entry_is_a_stage_the_fixture_defines():
    """The premise every entry assertion rests on: the declaration names one of
    the workflow's own stages, and it names one that is not the verifier — a
    pass that re-entered there would run nothing before judging itself."""
    assert DECLARED_ENTRY in STAGE_NAMES
    assert DECLARED_ENTRY != VERIFYING


def test_declared_categories_route_somewhere_other_than_the_declared_entry():
    """The premise "declared rather than derived" rests on. If every category
    routed where the declaration points, a run entering at the declared stage
    would be indistinguishable from one entering at the category's route, and
    the cases below would prove nothing.

    More than one, so the claim is not carried by a single category, and each
    named because a fixture that grew a route to the declared entry should fail
    here rather than turn those cases silently vacuous.
    """
    assert len(DIVERTED_CATEGORIES) >= 2
    for category in DIVERTED_CATEGORIES:
        assert destination_of(category) != DECLARED_ENTRY, category
    assert set(FINDING_FOR) == set(ROUTES)


# --------------------------------------------------------------------------
# The routed pass
# --------------------------------------------------------------------------


def test_the_declared_stage_runs_again_within_the_same_run(corrected_run):
    code, runner, run_dir = corrected_run
    resumed = STAGE_NAMES[STAGE_NAMES.index(DECLARED_ENTRY):]

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
    assert routed[0]["retry_stage"] == DECLARED_ENTRY
    assert schema_validator.validate(history_of(run_dir), HISTORY_SCHEMA) == []


def test_the_record_the_coordinator_wrote_is_in_the_run_directory(corrected_run):
    _, _, run_dir = corrected_run
    record = record_of(run_dir)

    assert record["pass"] == 1
    assert record["attempt"] == 1
    assert record["stage"] == DECLARED_ENTRY
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


def test_the_finding_reaches_the_entered_stage_in_its_rendered_prompt(
    corrected_run,
):
    """The record on disk and the prompt the stage was given say the same
    thing. Read off the run directory, which is where a reader of a finished
    run meets it."""
    _, runner, run_dir = corrected_run
    prompt = rendered_prompt(run_dir, DECLARED_ENTRY)

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
    first, second = runner.prompts[DECLARED_ENTRY]

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
    assert (run_dir / story_coordinator.prompt_file(DECLARED_ENTRY, 1)).is_file()
    assert not (run_dir / story_coordinator.prompt_file(DECLARED_ENTRY, 2)).exists()


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
    assert runner.guidance[DECLARED_ENTRY] == [[], []]


@pytest.fixture
def retry_run_to_the_entered_stage(target_root, harness_root):
    """A retry routed to the stage the declaration names.

    The category whose route *is* the declared entry, so the control below is
    about the same stage entered the other way rather than about some other
    stage that happens to carry guidance.
    """
    category = next(c for c in ROUTES if destination_of(c) == DECLARED_ENTRY)
    return drive(target_root, harness_root, [failing_into(category), PASS])


def test_a_retry_routed_to_the_same_stage_does_carry_guidance(
    retry_run_to_the_entered_stage,
):
    """The control beside it: the same stage, entered the other way, has
    guidance in force. An empty list above is therefore a fact about the
    correction pass rather than about where the test is looking."""
    _, runner, _ = retry_run_to_the_entered_stage
    entries = runner.guidance[DECLARED_ENTRY]
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
# The destination the workflow declares
# --------------------------------------------------------------------------


def entry_shape(entry: str) -> list[str]:
    """The stages a run entering at `entry` invokes: all of them, then that one
    and everything after it."""
    return [*STAGE_NAMES, *STAGE_NAMES[STAGE_NAMES.index(entry):]]


@pytest.mark.parametrize("category", sorted(ROUTES))
def test_every_declared_category_enters_at_the_declared_stage(
    target_root, harness_root, category,
):
    """Driven as a run per category rather than by calling the routing
    function, because what the story changed is where a run goes.

    Parametrized over the whole table, so a category whose route is elsewhere
    and one whose route is the declared entry are both asserted to end up in
    the same place — and a category added to the fixture is covered without a
    case being written for it.
    """
    code, runner, run_dir = drive(
        target_root, harness_root, [passing_with(FINDING_FOR[category]), PASS])

    assert code == 0
    assert record_of(run_dir)["stage"] == DECLARED_ENTRY
    assert runner.calls == entry_shape(DECLARED_ENTRY)


@pytest.mark.parametrize("category", sorted(ROUTES))
def test_no_stage_before_the_declared_entry_is_invoked_by_a_pass(
    target_root, harness_root, category,
):
    """The other half of the same claim, and the one story-063 paid for: a
    finding categorised for an earlier stage does not cost that stage an
    invocation. Every stage before the declared entry runs once under every
    category the workflow declares."""
    _, runner, _ = drive(
        target_root, harness_root, [passing_with(FINDING_FOR[category]), PASS])

    for stage in STAGE_NAMES[:STAGE_NAMES.index(DECLARED_ENTRY)]:
        assert runner.calls.count(stage) == 1, stage
    assert runner.calls.count(DECLARED_ENTRY) == 2


def test_a_workflow_naming_a_different_stage_enters_at_that_stage(
    target_root, tmp_path,
):
    """The declaration is what chooses, demonstrated by changing only it.

    A probe workflow whose declaration names a stage that is neither the
    fixture's declared entry nor the route of the category the finding carries,
    so the run entering there can be explained by nothing except the
    declaration. Orchestration source is untouched: the same coordinator module
    every other case here drives.
    """
    category = DIVERTED_CATEGORIES[0]
    elsewhere = next(name for name in STAGE_NAMES
                     if name not in (DECLARED_ENTRY, destination_of(category),
                                     VERIFYING))
    harness = probe_harness(
        tmp_path, target_root, "declared-elsewhere",
        lambda stage: stage.__setitem__(
            "correction_pass", {**stage["correction_pass"],
                                ENTRY_KEY: elsewhere}))

    code, runner, run_dir = drive(
        target_root, harness, [passing_with(FINDING_FOR[category]), PASS])

    assert code == 0
    assert record_of(run_dir)["stage"] == elsewhere
    assert runner.calls == entry_shape(elsewhere)


def test_two_categories_enter_at_the_declared_stage_in_one_pass(
    target_root, harness_root,
):
    """A verdict naming two categories takes one pass, not one apiece, and the
    findings reach the entered stage in the order the verdict recorded them."""
    code, runner, run_dir = drive(
        target_root, harness_root,
        [passing_with(RECORD_FINDING, CHECKS_FINDING), PASS])

    assert code == 0
    assert record_of(run_dir)["stage"] == DECLARED_ENTRY
    assert runner.calls == entry_shape(DECLARED_ENTRY)
    assert record_of(run_dir)["findings"] == [RECORD_FINDING, CHECKS_FINDING]
    assert events_of(run_dir).count("correction-pass-routed") == 1


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
# One clean-clone check, and it runs after the pass
# --------------------------------------------------------------------------

#: What the check writes into the event stream, either way. Read from the
#: coordinator's own vocabulary at the assertions below rather than being
#: recomposed: these are the two kinds `_clean_clone_passed` and
#: `_clean_clone_failed` append.
CLEAN_CLONE_PASSED = "clean-clone-passed"
CLEAN_CLONE_FAILED = "clean-clone-failed"
ROUTED = "correction-pass-routed"

#: A suite that fails once the pass has written this file, and passes while it
#: is absent. story-053's case in miniature: a stage's prose edit left the tree
#: in a state the suite does not survive, which the check in a fresh clone is
#: what catches.
BREAKAGE = "broken-by-the-correction-pass.txt"
SUITE_BROKEN_BY = f"sh -c '! test -f {BREAKAGE}'"


def clean_clone_events(run_dir: Path) -> list[str]:
    return [event for event in events_of(run_dir)
            if event in (CLEAN_CLONE_PASSED, CLEAN_CLONE_FAILED)]


class BreakingRunner(Runner):
    """The ordinary runner, plus a correction pass that breaks the suite.

    The breakage is written on the entered stage's *second* invocation, which
    is the one the pass caused, so the tree the check meets before the pass
    would have passed and the tree it meets after it does not.
    """

    def __call__(self, prompt, **kwargs):
        result = super().__call__(prompt, **kwargs)
        if (kwargs["stage"] == DECLARED_ENTRY
                and self.calls.count(DECLARED_ENTRY) == 2):
            (self.target_root / BREAKAGE).write_text("", encoding="utf-8")
        return result


def test_a_run_that_takes_a_pass_runs_the_clean_clone_check_once(corrected_run):
    """Two passing verdicts, one check: the verdict that routed the pass
    skipped it and the verification the pass returned to ran it.

    `verification_iterations` is asserted beside the count, because "once" is
    only interesting against a run that reached the check's precondition
    twice — otherwise a coordinator that had stopped running the check at all
    would satisfy it.
    """
    _, _, run_dir = corrected_run
    assert read_state(run_dir)["verification_iterations"] == 2
    assert clean_clone_events(run_dir) == [CLEAN_CLONE_PASSED]
    record = json.loads(
        (run_dir / conftest.CLEAN_CLONE_RESULT).read_text(encoding="utf-8"))
    assert record["ran"] is True
    assert record["exit_code"] == 0


def test_that_check_runs_after_the_pass_rather_than_before_it(corrected_run):
    """Ordering off the event stream, which is where the run records it."""
    _, _, run_dir = corrected_run
    events = events_of(run_dir)
    assert events.index(ROUTED) < events.index(CLEAN_CLONE_PASSED)


def test_a_passing_verdict_that_routes_no_pass_does_run_the_check(
    uncorrected_run,
):
    """The control for "once": a passing verdict ordinarily produces a check,
    so the corrected run's single check across two passing verdicts is one
    verdict skipping it rather than the check having gone missing."""
    _, _, run_dir = uncorrected_run
    assert read_state(run_dir)["verification_iterations"] == 1
    assert clean_clone_events(run_dir) == [CLEAN_CLONE_PASSED]


def test_a_run_that_takes_no_pass_meets_the_check_where_it_always_did(
    uncorrected_run,
):
    """After the passing verdict, before the run completes — the position the
    reordering was not allowed to cost anything."""
    code, _, run_dir = uncorrected_run
    events = events_of(run_dir)
    assert code == 0
    assert events.index("verification-passed") < events.index(CLEAN_CLONE_PASSED)
    assert events.index(CLEAN_CLONE_PASSED) < events.index("story-completed")
    assert ROUTED not in events


def test_a_workflow_declaring_no_correction_pass_meets_it_there_too(
    target_root, tmp_path,
):
    """The switch read at the ordering's end: with the declaration gone the
    check sits exactly where it sits on any other passing verdict, even for a
    verdict that carries findings."""
    harness = without_the_declaration(tmp_path, target_root,
                                      "no-correction-pass-ordering")
    code, _, run_dir = drive(target_root, harness,
                             [passing_with(CHECKS_FINDING)])
    events = events_of(run_dir)

    assert code == 0
    assert clean_clone_events(run_dir) == [CLEAN_CLONE_PASSED]
    assert events.index("verification-passed") < events.index(CLEAN_CLONE_PASSED)
    assert events.index(CLEAN_CLONE_PASSED) < events.index("story-completed")


def test_a_spent_budget_still_runs_the_check(exhausted_run):
    """The fall-through the reordering had to preserve: the verification that
    records the findings the run declines to correct is checked like any
    other, so a spent budget does not leave a run unchecked."""
    _, _, run_dir = exhausted_run
    events = events_of(run_dir)
    assert clean_clone_events(run_dir) == [CLEAN_CLONE_PASSED]
    assert events.index("correction-pass-recorded") < events.index(
        CLEAN_CLONE_PASSED)
    assert events.index(CLEAN_CLONE_PASSED) < events.index("story-completed")


def test_a_correction_that_breaks_the_suite_is_caught_after_the_pass(
    target_root, harness_root,
):
    """The check the pass no longer runs in front of is the one that catches
    it. The pass edits the tree into a state the configured suite fails in,
    and the check that follows the pass reports it and reroutes as a
    clean-clone failure does on any other run."""
    configure(target_root, test_command=SUITE_BROKEN_BY)
    runner = BreakingRunner(
        target_root,
        [passing_with(CHECKS_FINDING), *[PASS] * (MAX_RETRIES + 2)])
    code = story_coordinator.run_story(
        "story-001", harness_root, target_root, runner)
    run_dir = run_dir_of(target_root)
    events = events_of(run_dir)

    assert code == 2
    assert ROUTED in events
    assert events.index(ROUTED) < events.index(CLEAN_CLONE_FAILED)
    assert CLEAN_CLONE_PASSED not in events
    failure = next(e for e in history_of(run_dir)
                   if e["event"] == CLEAN_CLONE_FAILED)
    assert failure["retry_decision"] == "retry"
    assert failure["retry_stage"] == VERIFIER_STAGE["clean_clone"]["retry_stage"]
    assert read_state(run_dir)["retry_count"] >= 1


def test_the_same_runner_against_an_unbroken_suite_completes(
    target_root, harness_root,
):
    """The control: `BreakingRunner` is the ordinary runner plus one file, and
    with the suite indifferent to that file the run passes its check and
    completes. So the failure above is the breakage rather than the runner."""
    runner = BreakingRunner(target_root, [passing_with(CHECKS_FINDING), PASS])
    code = story_coordinator.run_story(
        "story-001", harness_root, target_root, runner)
    run_dir = run_dir_of(target_root)

    assert code == 0
    assert (target_root / BREAKAGE).is_file()
    assert clean_clone_events(run_dir) == [CLEAN_CLONE_PASSED]


# --------------------------------------------------------------------------
# Pre-flight: a declaration with nowhere to enter refuses the run
# --------------------------------------------------------------------------

UNDEFINED_STAGE = "a-stage-this-workflow-does-not-define"


def created_nothing(target_root: Path, branch: str) -> list[str]:
    """What a refused run must not have left behind, as a list of violations.

    A list rather than an assertion apiece, so the same statement can be made
    of the run that is supposed to create all of it — which is the control.
    """
    run_dir = run_dir_of(target_root)
    problems = []
    if run_dir.exists():
        problems.append("a run directory exists")
    if (run_dir / "state.json").exists():
        problems.append("state.json was written")
    if (run_dir / "events.log").exists():
        problems.append("an event stream was written")
    if head_of(target_root) != branch:
        problems.append(f"the repository was left on {head_of(target_root)}")
    return problems


def head_of(target_root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(target_root), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()


def naming(entry):
    """A mutation setting the declaration's entry stage to `entry`."""
    return lambda stage: stage.__setitem__(
        "correction_pass", {**stage["correction_pass"], ENTRY_KEY: entry})


def test_a_declaration_naming_a_stage_the_workflow_does_not_define_is_refused(
    target_root, tmp_path, capsys,
):
    """Refused at pre-flight, before a run directory exists — the defect is in
    the definition and every run under it carries it, so discovering it only
    when a passing verdict happens to carry a finding is discovering it after
    the whole workflow has been spent."""
    before = head_of(target_root)
    harness = probe_harness(tmp_path, target_root, "undefined-entry",
                            naming(UNDEFINED_STAGE))
    runner = Runner(target_root, [PASS])

    code = story_coordinator.run_story(
        "story-001", harness, target_root, runner)

    message = capsys.readouterr().err
    assert code == 1
    assert UNDEFINED_STAGE in message
    for name in STAGE_NAMES:
        assert name in message, name
    assert runner.calls == []
    assert created_nothing(target_root, before) == []


def test_the_same_harness_naming_a_defined_stage_creates_all_of_it(
    target_root, tmp_path,
):
    """The control for `created_nothing`: the same probe-built harness, the
    same target and the same runner, with the entry stage repaired."""
    before = head_of(target_root)
    harness = probe_harness(tmp_path, target_root, "defined-entry",
                            naming(DECLARED_ENTRY))
    code, runner, _ = drive(target_root, harness, [PASS])

    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert created_nothing(target_root, before) == [
        "a run directory exists",
        "state.json was written",
        "an event stream was written",
        "the repository was left on story/story-001",
    ]


def test_the_check_reports_a_declaration_with_no_entry_stage_at_all():
    """The same defect said differently, over stage lists that are not a real
    workflow so the accepted and refused declarations are pinned to the
    declarations rather than to whichever a run meets first."""
    stages = [{"name": "alpha"},
              {"name": "beta", "correction_pass": {"result": ARTIFACT,
                                                   "budget": 1}}]
    problems = story_coordinator.correction_pass_problems(stages)
    assert len(problems) == 1
    assert "alpha" in problems[0]


def test_the_check_accepts_a_workflow_that_declares_no_correction_pass():
    """The switch, at pre-flight: a definition carrying no declaration is not
    checked, which is what lets removing the key disable the mechanism."""
    assert story_coordinator.correction_pass_problems(
        [{"name": "alpha"}, {"name": "beta"}]) == []
    assert story_coordinator.correction_pass_problems(
        WORKFLOW["stages"]) == []


# --------------------------------------------------------------------------
# Words and not behaviour
# --------------------------------------------------------------------------

#: Clauses that would put the suite on the stage rather than on the coordinator.
#: The correction pass is entered by a stage whose turn is shorter than the
#: target's suite takes to run, which is what story-063 discovered by spending
#: an invocation and two self-routes on a line wrap.
SUITE_DEMANDS = (
    "the suite must pass",
    "must pass unchanged",
    "run the suite",
    "run the full suite",
    "the tests must pass",
)

#: The clause the statement used to carry, written here rather than resolved
#: out of this repository's history: the check below has to be shown capable of
#: reporting one, and a sentence constructed in the test demonstrates that with
#: nothing to move under it.
THE_REMOVED_CLAUSE = (
    "nothing you change here may alter what any test asserts about the "
    "system, and the suite must pass unchanged across this pass."
)


#: The instruction the pass carries about the one artifact it never edits.
#: ASCII throughout, so it survives being written into a prompt as JSON.
THE_STORY_ARTIFACT_CLAUSE = "approved story artifact is never edited"


def flowed(text: str) -> str:
    """`text` with its line wrapping removed.

    A prompt template is a wrapped document, so a clause a test looks for is
    as likely to straddle a newline as not, and an assertion against the raw
    text would be answering "was this clause wrapped here" rather than "is
    this clause present". Every prose assertion below reads through this.
    """
    return " ".join(text.split())


def suite_demands(text: str) -> list[str]:
    """The clauses in `text` that tell its reader to establish the suite."""
    lowered = flowed(text).lower()
    return [clause for clause in SUITE_DEMANDS if clause in lowered]


def test_the_check_for_that_instruction_reports_the_clause_it_looks_for():
    """The control every absence below leans on. Without this, a check that
    had stopped matching anything would report every text as clean."""
    assert suite_demands(THE_REMOVED_CLAUSE)
    assert suite_demands("correct the words and nothing else") == []


def test_the_rendered_statement_does_not_ask_the_stage_to_prove_the_suite():
    statement = story_coordinator.correction_pass_statement(DECLARED_ENTRY)
    assert suite_demands(statement) == []


def test_the_rendered_statement_names_the_check_that_confirms_it_instead():
    """What replaces the removed clause: the constraint still stated, and the
    clean-clone check named as what establishes it, after the pass."""
    statement = story_coordinator.correction_pass_statement(DECLARED_ENTRY)
    assert "never behaviour" in statement
    assert "clean-clone check" in statement
    assert "after this pass" in statement


def test_the_rendered_statement_tells_the_stage_to_leave_the_story_alone():
    statement = story_coordinator.correction_pass_statement(DECLARED_ENTRY)
    assert "approved story artifact is never edited" in statement


def test_the_statement_the_run_handed_the_entered_stage_is_that_one(
    corrected_run,
):
    """Read off the record the run wrote and the prompt the stage was given,
    so the three assertions above are about what a stage actually met rather
    than about a function nothing calls."""
    _, _, run_dir = corrected_run
    statement = record_of(run_dir)["statement"]
    assert statement == story_coordinator.correction_pass_statement(
        DECLARED_ENTRY)
    # A clause of it rather than the whole: the record reaches the prompt as
    # JSON, so a statement carrying an em dash is escaped there and comparing
    # the two strings would be comparing encodings.
    assert THE_STORY_ARTIFACT_CLAUSE in rendered_prompt(run_dir, DECLARED_ENTRY)


def correction_paragraph(prompt: str) -> str:
    """The correction-pass paragraph of a shipped prompt template.

    Bounded at the placeholder the paragraph introduces, so what is read is
    that paragraph rather than the whole file — an assertion that some clause
    is absent from a document says much less than one that it is absent from
    the paragraph that would carry it.
    """
    field = f"{{{{{CORRECTION_FIELD}}}}}"
    assert field in prompt
    head = prompt[:prompt.index(field)]
    return head[head.rindex("\n\n"):]


def verifier_field_section() -> str:
    """What the shipped verifier prompt says from the field's first mention on.

    The shipped template is the subject: what tells the verifier what may go in
    `correctable_findings` is this repository's own instruction to it.
    """
    prompt = (REPO_ROOT / "prompts" / "verifier.md").read_text(encoding="utf-8")
    assert "correctable_findings" in prompt
    return prompt[prompt.index("correctable_findings"):]


def test_the_shipped_entered_stages_prompt_makes_the_same_two_changes():
    """The prompt and the coordinator's statement have to agree, because the
    stage reads both. The shipped template is the subject here: what this
    repository tells that stage is the thing being asserted.
    """
    stage = shipped_correction_stage()
    paragraph = flowed(correction_paragraph(
        (REPO_ROOT / "prompts" / stage["prompt"]).read_text(encoding="utf-8")))
    assert suite_demands(paragraph) == []
    assert "clean-clone check" in paragraph
    assert "after the pass" in paragraph
    assert THE_STORY_ARTIFACT_CLAUSE in paragraph


def test_the_shipped_verifier_prompt_forbids_a_finding_against_the_story():
    """The other end of the same instruction: a finding the entered stage is
    told not to act on is one the verifier is told not to record."""
    section = flowed(verifier_field_section())
    assert "approved story artifact" in section
    assert "Do not record one against the approved story artifact" in section


def test_the_constraint_is_stated_where_the_verifier_reads_what_may_go_in_the_field():
    """The shipped verifier prompt is the subject here: it is what tells the
    stage what belongs in the field, so this reads what this repository
    ships."""
    section = flowed(verifier_field_section())
    # What a verifier has to know before it records a finding: what the pass
    # may change, what stays in the field it is not, that a finding is one a
    # single stage can close, and what it may not be recorded against.
    assert "never behaviour" in section
    assert "words alone" in section
    assert "unverified" in section
    assert "one stage" in section
    assert "approved story artifact" in section


def test_the_constraint_is_stated_in_the_schema_the_verifier_writes_to():
    """And in the schema description beside the field, which is the other
    place the verifier is told what may go in it. That the correction changes
    words and never behaviour did not move when the question of who proves it
    did."""
    description = VERDICT_SCHEMA["properties"]["correctable_findings"][
        "description"]
    assert "never to behaviour" in description
    assert "words alone" in description


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


# --------------------------------------------------------------------------
# The stage this repository ships a pass to may make story-063's own edit
#
# story-063's finding was a docstring reflow in orchestration source — outside
# the files the entered stage ordinarily writes, which is why the run that met
# it categorised it elsewhere and re-entered elsewhere. Under the declared
# entry it goes to the shipped stage regardless, so what has to hold is that
# the edit is one that stage is both permitted and instructed to make. The
# subject here is what this repository ships, which is why these read it.
# --------------------------------------------------------------------------

BLOCKED_PATHS = json.loads(
    (REPO_ROOT / "rules" / "execution-rules.json").read_text(encoding="utf-8")
)["blocked_paths"]

#: The file story-063's finding named, relative to the repository root. A path
#: rather than a claim about a path: the two cases below ask whether the stage
#: may write here, and both are answered from the shipped declarations.
STORY_063_LOCATION = Path(story_coordinator.__file__).name
STORY_063_PATH = f"orchestration/{STORY_063_LOCATION}"


def blocked(path: str) -> list[str]:
    """The blocked-path prefixes `path` falls under."""
    return [prefix for prefix in BLOCKED_PATHS if path.startswith(prefix)]


def test_the_edit_falls_under_no_blocked_path():
    """The first half of "permitted". The control is a path built from a
    blocked prefix, which the same check reports — so an empty list is a fact
    about the location rather than about a check that matches nothing."""
    assert blocked(STORY_063_PATH) == []
    assert blocked(f"{BLOCKED_PATHS[0]}something.txt") == [BLOCKED_PATHS[0]]


def test_the_stage_the_pass_enters_at_is_governed_by_no_create_restriction():
    """The second half. `may_not_create` is what stops a stage writing under a
    prefix, and the stage the shipped declaration enters at declares none —
    which is what makes the correction its own work rather than a compromise.

    The control is the shipped stages that do declare one: without it, a
    coordinator that had renamed the key would read as unrestricted here.
    """
    shipped = conftest.shipped_workflow()
    restricted = [stage["name"] for stage in shipped["stages"]
                  if stage.get("may_not_create")]
    assert shipped_correction_stage().get("may_not_create") is None
    assert restricted
    assert shipped_correction_stage()["name"] not in restricted


def test_that_stage_is_instructed_to_correct_whatever_the_finding_names():
    """"Instructed", read off the paragraph the stage meets: the instruction
    is about the words a finding names and says nothing about which file they
    are in, so a docstring in orchestration source is inside it."""
    stage = shipped_correction_stage()
    paragraph = correction_paragraph(
        (REPO_ROOT / "prompts" / stage["prompt"]).read_text(encoding="utf-8"))
    assert "the words each finding names and nothing else" in paragraph
