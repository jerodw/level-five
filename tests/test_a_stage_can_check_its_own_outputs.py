"""Independent validation for story-112: a stage can ask, while its turn is
still open, the question the coordinator asks after it ends.

Three subjects, each exercised as the thing it actually ships as:

  * `scripts/l5-check` is driven **as a program**, in a subprocess, by its own
    absolute path — never through an interpreter and never by importing its
    module and calling `main`. What ships is a command line, and a command that
    answered correctly in-process and was not executable would pass the first
    reading and cost the run the invocation this story exists to save.
  * `hooks/stop_check.py` is driven the same way, with real Stop payloads on
    stdin, because its whole contract is what it writes to stdout and when.
  * `orchestration/output_check.py` is called in-process only where the
    subject is *where its answer comes from* — the three coordinator functions
    it delegates to are moved and the command's answer is observed moving with
    them.

Every name here — stage, artifact, schema, workflow — is derived from a
workflow this module builds through the shared builder in `tests/conftest.py`,
never from `workflows/story-workflow.json`. Whether the shipped definition
grants some stage a budget or renames an artifact has nothing to say about
whether a stage can check its own outputs, and reading the live definition here
would make it say something.

Every absence asserted below is paired with a demonstration that the same check
reports the violation it exists to catch:

  * "the conditional artifact is not reported missing" sits beside the same run
    with that artifact written invalid, which *is* reported;
  * "the baseline round-trips" sits beside the list-valued reading JSON would
    have produced, which finds nothing stale on the identical files — the
    failure this would ship green if the tuple were dropped;
  * "the checker holds no second copy of the three answers" is a source scan
    whose control is that same source with a comparison planted in it, which
    the scan reports;
  * "nothing in the coordinator's decision path reads the persisted baseline"
    is an AST scan whose control is the coordinator's own source with a read
    planted in it, and it sits beside two whole runs — one whose stage
    rewrites that file and one whose stage does not — which escalate
    identically;
  * "the hook emits no decision" is asserted only after the *same* run
    directory and the *same* driver have been shown to emit one, so silence is
    always about the case rather than about the harness driving it;
  * "no interpreter is named" is run beside the same command line with an
    interpreter prefixed, which the same reading reports;
  * "an invocation naming no run directory carries no Stop entry" sits beside
    the invocation that names one, which carries it.

Nothing here invokes a model, and nothing here resolves a commit: the state
before this story is carried by the control of each pair rather than by a
revision.
"""
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import agent_runner
import context_assembler
import harness_config
import output_check
import story_coordinator
from agent_runner import AgentResult
from conftest import HARNESS_ROOT
import conftest


# --------------------------------------------------------------------------
# The workflow these checks are made against
#
# Built rather than loaded. The subject is a mechanism -- can a stage be told
# what it still owes -- which needs *a* stage declaring required outputs, a
# stage declaring a conditional one, and schemas over both. It does not need
# the set of them this repository happens to deploy.
# --------------------------------------------------------------------------


#: A valid instance of each schema the built definition declares, by the schema
#: name the definition names. Written once, here, so an artifact's content is
#: derived from what the fixture said the artifact is rather than from the
#: artifact's name.
SCHEMA_SAMPLES = {
    "changed-files": {"modified": [], "created": [], "deleted": []},
    "test-results": {"tests_written": 1},
    "verification-result": {"status": "passed", "retry_recommended": False},
    "retry-guidance": {
        "current_focus": [{
            "focus": "the behaviour the story asked for",
            "satisfied_when": "the sample behavior exists",
        }],
        "preserve_behavior": ["everything the previous attempt got right"],
        "retry_scope": ["the stage the failure routed to"],
    },
}

WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        max_self_routes=1,
        schemas={conftest.CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        outputs=(conftest.TEST_RESULTS,),
        changed_files=conftest.TESTER_CHANGED_FILES,
        schemas={conftest.TEST_RESULTS: "test-results",
                 conftest.TESTER_CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        retry_routing={"implementation-defect": {
            "stage": conftest.StageRef(0),
            "when": "the behaviour the story asked for is missing"}}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="output-check-workflow",
)

#: A second definition, differing from the one above in one respect: the stage
#: at the same position requires one more artifact. It exists so "the workflow
#: comes from the run's state rather than from the target's configuration" can
#: be asked as a difference in the answer rather than as a reading of the code.
OTHER_WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES,),
        changed_files=conftest.CHANGED_FILES),
    conftest.workflow_stage(
        outputs=(conftest.TEST_RESULTS, conftest.DOCUMENTATION_REPORT),
        changed_files=conftest.TESTER_CHANGED_FILES,
        schemas={conftest.TEST_RESULTS: "test-results",
                 conftest.TESTER_CHANGED_FILES: "changed-files"}),
    name="output-check-other-workflow",
)

STAGES = WORKFLOW["stages"]
#: The stage most of the command's verdicts are asked about: required outputs,
#: every one of them carrying a schema, and no conditional artifact.
SUBJECT = STAGES[1]
SUBJECT_NAME = SUBJECT["name"]
#: The stage carrying a conditional artifact, which is the one case the command
#: must *not* report missing.
CONDITIONAL_STAGE = STAGES[2]
#: The stage a coordinator-driven run leaves an output stale in. It is the one
#: carrying a self-route budget, so the run re-enters it in place before it
#: escalates -- which is the coordinator behaviour this story must not move.
SKIPPING_STAGE = STAGES[0]

REQUIRED = story_coordinator.required_artifacts(SUBJECT)
CONDITIONAL = story_coordinator.conditional_artifacts(CONDITIONAL_STAGE)
assert CONDITIONAL, "the fixture must declare a conditional artifact to check"
#: The artifact a stale case leaves behind, and the one the coordinator-driven
#: runs below decline to rewrite. Read off the declaration.
STALE_ARTIFACT = REQUIRED[-1]
SKIPPED_ARTIFACT = story_coordinator.required_artifacts(SKIPPING_STAGE)[-1]

#: The story the shared target fixture writes.
STORY_ID = "story-001"


@pytest.fixture
def configured_workflow() -> str:
    """Point the shared target fixture at the definition built above."""
    return WORKFLOW["name"]


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A harness root carrying both definitions, and the programs that ship.

    `scripts`, `orchestration` and `hooks` are copied rather than linked
    because the entry point and the hook each resolve their own harness root as
    `Path(__file__).resolve()`, and a symlink would resolve straight back to
    this repository -- loading the shipped workflow and undoing the point of
    building one.
    """
    root = conftest.materialize_workflow(
        WORKFLOW, tmp_path / "check-harness",
        copy=("scripts", "orchestration", "hooks"))
    return conftest.materialize_workflow(OTHER_WORKFLOW, root)


# --------------------------------------------------------------------------
# Putting a run directory into a state a stage could be in
# --------------------------------------------------------------------------


def write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def contents(stage: dict, artifact: str, marker: str = "this attempt") -> str:
    """Valid content for an artifact, derived from the schema its stage names."""
    schema = stage.get("schemas", {}).get(artifact)
    if schema is None:
        return f"{artifact} as written by {marker}\n"
    return json.dumps(SCHEMA_SAMPLES[schema], indent=2) + "\n"


#: Content that parses as JSON and satisfies no schema the fixture declares.
INVALID = json.dumps({"nothing": "this schema asks for"}, indent=2) + "\n"


def write_fresh(path: Path, text: str) -> str:
    """Write a file and leave it unambiguously newer than it was.

    Fixture construction rather than an assertion: a stage that writes a file
    writes it after the snapshot taken at its entry, and saying so explicitly
    makes every case here decide on the property under test rather than on how
    fine the filesystem's clock happens to be. Nothing here reads a clock or
    bounds how long anything took -- the new time is derived from the file's
    own previous one, so it is later by construction on any filesystem.
    """
    previous = path.stat().st_mtime_ns if path.is_file() else 0
    write(path, text)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns,
                       max(stat.st_mtime_ns, previous) + 1_000_000_000))
    return text


def run_dir_of(target_root: Path) -> Path:
    return target_root / ".harness" / "runs" / STORY_ID


def stage_turn(target_root: Path, stage: dict, *, wrote=(), left_behind=(),
               invalid=(), workflow: dict = WORKFLOW,
               current_stage: str | None = None) -> Path:
    """A run directory at the moment a stage's turn is ending.

    `left_behind` is what a previous attempt wrote and is in place *before* the
    baseline is taken, which is the only way the stale case exists at all.
    `wrote` is what this invocation wrote, after it.
    """
    run_dir = run_dir_of(target_root)
    run_dir.mkdir(parents=True, exist_ok=True)
    story_coordinator.save_state(run_dir, story_coordinator.RunState(
        story_id=STORY_ID, branch=f"story/{STORY_ID}",
        current_stage=stage["name"] if current_stage is None else current_stage,
        workflow=workflow["name"]))

    for artifact in left_behind:
        write(run_dir / artifact, contents(stage, artifact, "a previous attempt"))

    watched = (story_coordinator.required_artifacts(stage)
               + story_coordinator.conditional_artifacts(stage))
    story_coordinator.write_output_baseline(
        run_dir, stage["name"],
        story_coordinator.artifact_signatures(run_dir, watched))

    for artifact in wrote:
        write_fresh(run_dir / artifact,
                    INVALID if artifact in invalid else contents(stage, artifact))
    return run_dir


# --------------------------------------------------------------------------
# The command, driven as a program
# --------------------------------------------------------------------------


def entry_point(harness_root: Path) -> Path:
    return harness_root / "scripts" / output_check.SCRIPT_NAME


def run_command(harness_root: Path, run_dir: Path,
                *arguments: str) -> subprocess.CompletedProcess:
    """The entry point, invoked by its own absolute path and its shebang."""
    return subprocess.run(
        [str(entry_point(harness_root)), str(run_dir), *arguments],
        capture_output=True, text=True)


def said(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


@pytest.fixture
def complete(target_root, harness_root) -> Path:
    return stage_turn(target_root, SUBJECT, wrote=REQUIRED)


@pytest.fixture
def one_missing(target_root, harness_root) -> Path:
    return stage_turn(target_root, SUBJECT,
                      wrote=[a for a in REQUIRED if a != STALE_ARTIFACT])


@pytest.fixture
def one_stale(target_root, harness_root) -> Path:
    """Everything written afresh except one artifact, left exactly as a
    previous attempt left it -- present, and not this invocation's."""
    return stage_turn(target_root, SUBJECT, left_behind=[STALE_ARTIFACT],
                      wrote=[a for a in REQUIRED if a != STALE_ARTIFACT])


@pytest.fixture
def one_invalid(target_root, harness_root) -> Path:
    return stage_turn(target_root, SUBJECT, wrote=REQUIRED,
                      invalid=[STALE_ARTIFACT])


def test_the_command_exits_zero_when_every_required_output_is_written(
        harness_root, complete):
    result = run_command(harness_root, complete, SUBJECT_NAME)
    assert result.returncode == 0, said(result)
    assert SUBJECT_NAME in result.stdout
    assert result.stderr == ""


def test_the_command_names_a_required_output_that_was_never_written(
        harness_root, one_missing):
    assert not (one_missing / STALE_ARTIFACT).exists()
    result = run_command(harness_root, one_missing, SUBJECT_NAME)
    assert result.returncode != 0
    assert STALE_ARTIFACT in said(result)


def test_the_command_fails_on_an_output_a_previous_attempt_left_behind(
        harness_root, one_stale):
    """The case this story exists for, driven as itself.

    The artifact is *present* and untouched since the baseline, so a check that
    only ever asked whether the file exists would pass here. That is asserted
    first, so a green result cannot mean the fixture deleted it.
    """
    left = one_stale / STALE_ARTIFACT
    assert left.is_file()
    assert left.read_text(encoding="utf-8") == contents(
        SUBJECT, STALE_ARTIFACT, "a previous attempt")

    result = run_command(harness_root, one_stale, SUBJECT_NAME)
    assert result.returncode != 0
    assert STALE_ARTIFACT in said(result)


def test_the_command_names_an_artifact_that_does_not_satisfy_its_schema(
        harness_root, one_invalid):
    schema = SUBJECT["schemas"][STALE_ARTIFACT]
    result = run_command(harness_root, one_invalid, SUBJECT_NAME)
    assert result.returncode != 0
    assert STALE_ARTIFACT in said(result)
    assert schema in said(result)


def test_the_stale_case_reads_differently_from_the_missing_case(
        target_root, harness_root):
    """Both senses the coordinator distinguishes them by, read off two runs
    rather than matched against a literal: absent is missing, and present-and-
    unwritten is a previous attempt's."""
    missing = said(run_command(
        harness_root,
        stage_turn(target_root, SUBJECT,
                   wrote=[a for a in REQUIRED if a != STALE_ARTIFACT]),
        SUBJECT_NAME))
    stale = said(run_command(
        harness_root,
        stage_turn(target_root, SUBJECT, left_behind=[STALE_ARTIFACT],
                   wrote=[a for a in REQUIRED if a != STALE_ARTIFACT]),
        SUBJECT_NAME))

    assert STALE_ARTIFACT in missing and STALE_ARTIFACT in stale
    assert missing != stale
    assert "previous attempt" in stale
    assert "previous attempt" not in missing


# --------------------------------------------------------------------------
# Conditional artifacts: the one set the command must not report missing
# --------------------------------------------------------------------------


def test_a_conditional_artifact_the_stage_did_not_write_is_not_reported(
        target_root, harness_root):
    run_dir = stage_turn(
        target_root, CONDITIONAL_STAGE,
        wrote=story_coordinator.required_artifacts(CONDITIONAL_STAGE))
    for artifact in CONDITIONAL:
        assert not (run_dir / artifact).exists()

    result = run_command(harness_root, run_dir, CONDITIONAL_STAGE["name"])
    assert result.returncode == 0, said(result)
    for artifact in CONDITIONAL:
        assert artifact not in said(result)


def test_a_conditional_artifact_the_stage_did_write_is_schema_checked(
        target_root, harness_root):
    """The control for the assertion above: the same stage, the same required
    outputs, and the conditional artifact present and invalid -- which the same
    command reports, so its silence above is about the artifact being absent
    rather than about the command not looking at it."""
    conditional = CONDITIONAL[0]
    run_dir = stage_turn(
        target_root, CONDITIONAL_STAGE,
        wrote=(story_coordinator.required_artifacts(CONDITIONAL_STAGE)
               + [conditional]),
        invalid=[conditional])

    result = run_command(harness_root, run_dir, CONDITIONAL_STAGE["name"])
    assert result.returncode != 0
    assert conditional in said(result)


# --------------------------------------------------------------------------
# What the command is told, and what it reads it from
# --------------------------------------------------------------------------


def test_the_stage_defaults_to_the_one_the_state_names_as_current(
        target_root, harness_root):
    run_dir = stage_turn(target_root, SUBJECT,
                         wrote=[a for a in REQUIRED if a != STALE_ARTIFACT])
    named = run_command(harness_root, run_dir, SUBJECT_NAME)
    defaulted = run_command(harness_root, run_dir)
    assert defaulted.returncode == named.returncode != 0
    assert said(defaulted) == said(named)


def test_the_default_follows_the_state_rather_than_a_fixed_stage(
        target_root, harness_root):
    """The control for the default: the same run directory, the same files, and
    a state naming the other stage -- which the command answers about instead."""
    run_dir = stage_turn(
        target_root, CONDITIONAL_STAGE,
        wrote=story_coordinator.required_artifacts(CONDITIONAL_STAGE))
    defaulted = run_command(harness_root, run_dir)
    assert defaulted.returncode == 0, said(defaulted)
    assert CONDITIONAL_STAGE["name"] in defaulted.stdout
    assert SUBJECT_NAME not in defaulted.stdout


def test_the_workflow_comes_from_the_runs_state_not_the_configuration(
        target_root, harness_root):
    """The target is configured to run one definition throughout. The run's
    state names the other, and the command answers under the other."""
    configured = (target_root / ".harness" / "config.yaml").read_text(
        encoding="utf-8")
    assert WORKFLOW["name"] in configured
    assert OTHER_WORKFLOW["name"] not in configured

    run_dir = stage_turn(target_root, SUBJECT, wrote=REQUIRED)
    under_the_state = run_command(harness_root, run_dir, SUBJECT_NAME)
    assert under_the_state.returncode == 0, said(under_the_state)

    same_stage = OTHER_WORKFLOW["stages"][1]
    assert same_stage["name"] == SUBJECT_NAME
    only_the_other_requires = sorted(
        set(story_coordinator.required_artifacts(same_stage)) - set(REQUIRED))
    assert only_the_other_requires

    stage_turn(target_root, same_stage, wrote=REQUIRED,
               workflow=OTHER_WORKFLOW)
    under_the_other = run_command(harness_root, run_dir, SUBJECT_NAME)
    assert under_the_other.returncode != 0
    for artifact in only_the_other_requires:
        assert artifact in said(under_the_other)


# --------------------------------------------------------------------------
# One derivation: the three answers are the coordinator's
#
# Each of these moves a coordinator function and observes the command's answer
# move with it. Nothing here compares the command's answer against a second
# computation of freshness, of the required set, or of schema validity -- a
# second copy would agree with the first for exactly as long as nobody changed
# either.
# --------------------------------------------------------------------------


def check(run_dir: Path, harness_root: Path, stage: str = SUBJECT_NAME):
    """The checker the command and the hook both run, called in place.

    In-process only where the subject is where the answer comes from: the three
    coordinator functions are moved below and the answer is observed moving.
    """
    return output_check.check(run_dir, stage, harness_root=harness_root)


def test_the_freshness_answer_is_the_coordinators(
        monkeypatch, harness_root, complete):
    assert check(complete, harness_root).stale == []

    monkeypatch.setattr(story_coordinator, "stale_artifacts",
                        lambda run_dir, artifacts, baseline: sorted(artifacts))
    moved = check(complete, harness_root)
    assert moved.stale == sorted(REQUIRED)
    assert not moved.complete


def test_the_required_output_list_is_the_coordinators(
        monkeypatch, harness_root, complete):
    invented = "an-artifact-no-declaration-names.json"
    monkeypatch.setattr(story_coordinator, "required_artifacts",
                        lambda stage: [invented])
    assert check(complete, harness_root).missing == [invented]


def test_the_schema_verdict_is_the_coordinators(
        monkeypatch, harness_root, complete):
    monkeypatch.setattr(story_coordinator, "_schema_violation",
                        lambda run_dir, stage: "the coordinator said so")
    moved = check(complete, harness_root)
    assert moved.invalid == "the coordinator said so"
    assert not moved.complete


CHECKER_SOURCE = Path(output_check.__file__).read_text(encoding="utf-8")

#: The shapes a second copy of any of the three answers would have to take: a
#: signature read, a schema validation, or a reading of the declaration keys the
#: required and conditional sets are derived from.
SECOND_COPY_SHAPES = ("st_mtime", "st_size", ".stat()", "schema_validator",
                      "jsonschema", '"outputs"', '"schemas"', "changed_files")


def second_copies_in(source: str) -> list[str]:
    return [shape for shape in SECOND_COPY_SHAPES if shape in source]


def test_the_checker_holds_no_second_copy_of_any_of_the_three():
    assert second_copies_in(CHECKER_SOURCE) == []


def test_the_scan_reports_a_second_copy_planted_in_the_same_source():
    """The control: the same reading over the same source with a freshness
    comparison written into it, which it reports. Without this the assertion
    above would hold just as well for a scan looking at the wrong text."""
    planted = CHECKER_SOURCE.replace(
        "    required = story_coordinator.required_artifacts(stage)",
        "    required = story_coordinator.required_artifacts(stage)\n"
        "    fresh = (run_dir / required[0]).stat().st_mtime\n",
        1)
    assert planted != CHECKER_SOURCE
    assert second_copies_in(planted) != []


def coordinator_functions_called(source: str) -> set[str]:
    """Every `story_coordinator.<name>(...)` call in a source."""
    called = set()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == story_coordinator.__name__):
            called.add(node.func.attr)
    return called


def test_the_three_answers_are_reached_by_calling_the_coordinator():
    """The positive half of the pair above: delegation is not merely the
    absence of a copy, it is these calls."""
    assert {"required_artifacts", "stale_artifacts", "_schema_violation"} <= (
        coordinator_functions_called(CHECKER_SOURCE))


# --------------------------------------------------------------------------
# The persisted baseline
# --------------------------------------------------------------------------


@pytest.fixture
def baselined(target_root, harness_root) -> tuple[Path, dict]:
    """A run directory holding a previous attempt's artifacts, and the
    signatures of them, with nothing touched since."""
    run_dir = stage_turn(target_root, SUBJECT, left_behind=REQUIRED)
    return run_dir, story_coordinator.artifact_signatures(run_dir, REQUIRED)


def test_the_baseline_round_trips_as_the_signatures_it_was_given(baselined):
    run_dir, signatures = baselined
    read_back = story_coordinator.read_output_baseline(run_dir)
    assert read_back.stage == SUBJECT_NAME
    assert read_back.signatures == signatures
    assert set(read_back.signatures) == set(REQUIRED)
    assert all(isinstance(value, tuple) for value in read_back.signatures.values())


def test_signatures_read_back_as_lists_would_find_nothing_stale(baselined):
    """Why the equality above is load-bearing, demonstrated rather than
    described. JSON renders a tuple as a list, and a reader that returned lists
    compares unequal against every signature on unchanged files -- so it would
    report no stale artifact at all and tell a stage it was complete in
    precisely the case the check exists to catch."""
    run_dir, signatures = baselined
    read_back = story_coordinator.read_output_baseline(run_dir)

    as_lists = {name: list(value) for name, value in signatures.items()}
    assert story_coordinator.stale_artifacts(run_dir, REQUIRED, as_lists) == []
    assert story_coordinator.stale_artifacts(
        run_dir, REQUIRED, read_back.signatures) == sorted(REQUIRED)


def test_an_absent_or_unreadable_baseline_reads_as_nothing(baselined, tmp_path):
    run_dir, _ = baselined
    assert story_coordinator.read_output_baseline(run_dir) is not None

    story_coordinator.output_baseline_file(run_dir).write_text(
        "{ this is not json", encoding="utf-8")
    assert story_coordinator.read_output_baseline(run_dir) is None

    story_coordinator.output_baseline_file(run_dir).unlink()
    assert story_coordinator.read_output_baseline(run_dir) is None
    assert story_coordinator.read_output_baseline(tmp_path) is None


def test_the_command_says_it_cannot_check_when_there_is_no_baseline(
        harness_root, complete):
    """A check that could not run must not read as a check that passed."""
    before = run_command(harness_root, complete, SUBJECT_NAME)
    assert before.returncode == 0, said(before)

    story_coordinator.output_baseline_file(complete).unlink()
    after = run_command(harness_root, complete, SUBJECT_NAME)
    assert after.returncode != 0
    assert story_coordinator.OUTPUT_BASELINE in said(after)


def test_the_command_moves_when_the_stage_rewrites_the_baseline(
        harness_root, one_stale):
    """The stage-side half of the seam: the file is what the stage is told
    about itself, so rewriting it changes what it is told. Its coordinator-side
    half -- that rewriting it changes nothing the coordinator decides -- is the
    pair of runs below."""
    assert check(one_stale, harness_root).stale == [STALE_ARTIFACT]

    baseline = story_coordinator.read_output_baseline(one_stale)
    story_coordinator.write_output_baseline(
        one_stale, baseline.stage,
        {name: signature for name, signature in baseline.signatures.items()
         if name != STALE_ARTIFACT})
    assert check(one_stale, harness_root).complete


# --------------------------------------------------------------------------
# Whole runs: the coordinator still decides
# --------------------------------------------------------------------------


#: The category and destination of the first route the built verifier declares,
#: read off the definition through the shared resolution rather than written
#: here. The retry is what brings the skipping stage round a second time, with
#: its first attempt's artifacts already in the run directory -- which is the
#: only way a stale output exists at all.
RETRY_CATEGORY, RETRY_STAGE = conftest.first_retry_route(WORKFLOW)
assert RETRY_STAGE == SKIPPING_STAGE["name"]

PASSING = {"status": "passed", "blocking_issues": [], "unverified": [],
           "retry_recommended": False}
FAILING = {
    "status": "failed",
    "blocking_issues": [{
        "severity": "high",
        "issue": "the sample behavior is missing",
        "location": "src/app.py",
        "required_behavior": "the sample behavior exists",
    }],
    "unverified": [],
    "retry_recommended": True,
    "retry_target": RETRY_CATEGORY,
}


class Runner:
    """A fake agent runner that writes each stage's declared outputs.

    The verifier fails once and routes a retry back to the skipping stage. On
    that second entry, with the first attempt's artifacts already present, the
    runner leaves one of them exactly as it was -- which is the stale case the
    coordinator has escalated on since story-022. `tamper` makes the stage
    rewrite the persisted baseline so its own check would call it complete,
    which must change nothing about where the run goes. `skip=False` is the
    same runner writing that artifact, which is the control.
    """

    def __init__(self, target_root: Path, *, tamper: bool = False,
                 skip: bool = True):
        self.run_dir = run_dir_of(target_root)
        self.tamper = tamper
        self.skip = skip
        self.calls: list[str] = []
        self.grants: list[list[str] | None] = []
        self.prompts: list[str] = []
        self.baselines: list[object] = []

    def _declaration(self, stage: str) -> dict:
        return next(s for s in STAGES if s["name"] == stage)

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None, suite_command=None, run_dir=None):
        self.calls.append(stage)
        self.grants.append(allowed_tools)
        self.prompts.append(prompt)
        self.baselines.append(
            story_coordinator.read_output_baseline(self.run_dir))

        declaration = self._declaration(stage)
        verdict = conftest.answering_guidance(
            PASSING if self.calls.count(conftest.VERIFYING_STAGE) > 1 else FAILING,
            self.run_dir)
        leaves_alone = (self.skip and stage == SKIPPING_STAGE["name"]
                        and self.calls.count(stage) > 1)

        for artifact in story_coordinator.required_artifacts(declaration):
            if leaves_alone and artifact == SKIPPED_ARTIFACT:
                continue
            if declaration.get("schemas", {}).get(artifact) == "verification-result":
                write_fresh(self.run_dir / artifact,
                            json.dumps(verdict, indent=2) + "\n")
                continue
            write_fresh(self.run_dir / artifact, contents(declaration, artifact))

        # The conditional artifact, written where the verdict calls for it and
        # nowhere else -- the case the missing check must not report.
        if stage == conftest.VERIFYING_STAGE and verdict["status"] == "failed":
            for artifact in story_coordinator.conditional_artifacts(declaration):
                write_fresh(self.run_dir / artifact,
                            contents(declaration, artifact))

        if self.tamper:
            baseline = story_coordinator.read_output_baseline(self.run_dir)
            story_coordinator.write_output_baseline(
                self.run_dir, baseline.stage, {})
        return AgentResult(ok=True, result_text=f"{stage} done")


def drive(target_root: Path, harness_root: Path, **kwargs):
    runner = Runner(target_root, **kwargs)
    code = story_coordinator.run_story(
        STORY_ID, harness_root, target_root, runner)
    return code, runner


def outcome_of(target_root: Path) -> dict:
    """Where a run ended, read off the run rather than written here."""
    state = json.loads(
        (run_dir_of(target_root) / "state.json").read_text(encoding="utf-8"))
    return {"status": state["status"],
            "current_stage": state["current_stage"],
            "self_route_count": state["self_route_count"],
            "retry_count": state["retry_count"]}


def escalations_of(target_root: Path) -> list[str]:
    log = (run_dir_of(target_root) / "events.log").read_text(encoding="utf-8")
    return [line.split("escalated: ", 1)[1]
            for line in log.splitlines() if "escalated: " in line]


@pytest.fixture
def untampered(target_root, harness_root):
    code, runner = drive(target_root, harness_root)
    return code, runner, target_root


def test_a_stage_that_leaves_a_required_output_unwritten_is_still_re_entered(
        untampered):
    """The coordinator's own handling, unmoved: the stage runs again in place,
    and the run ends where it ended before this story existed."""
    code, runner, target_root = untampered
    assert code != 0
    assert runner.calls.count(SKIPPING_STAGE["name"]) > 2
    outcome = outcome_of(target_root)
    assert outcome["status"] == "escalated"
    assert outcome["current_stage"] == SKIPPING_STAGE["name"]
    assert outcome["self_route_count"] > 0
    assert any(SKIPPED_ARTIFACT in reason for reason in escalations_of(target_root))


def test_a_stage_that_rewrites_the_baseline_reaches_the_same_end(
        tmp_path, harness_root, untampered, configured_workflow):
    """The same run, differing in one respect: the stage rewrites the persisted
    baseline so that its own check would report it complete. Everything the
    coordinator decided is compared against the run that did not."""
    honest_code, honest_runner, honest_root = untampered
    tampered_root = build_target(tmp_path / "tampered-target", configured_workflow)
    code, runner = drive(tampered_root, harness_root, tamper=True)

    assert code == honest_code
    assert runner.calls == honest_runner.calls
    assert outcome_of(tampered_root) == outcome_of(honest_root)
    assert len(escalations_of(tampered_root)) == len(escalations_of(honest_root))
    assert all(SKIPPED_ARTIFACT in reason
               for reason in escalations_of(tampered_root))


def test_the_same_runner_writing_everything_completes(
        tmp_path, harness_root, configured_workflow):
    """The control for the pair above: one plan difference -- the write -- and
    the run completes, so the escalation they share is about the unwritten
    output rather than about the fixture."""
    root = build_target(tmp_path / "complete-target", configured_workflow)
    code, runner = drive(root, harness_root, skip=False)
    assert code == 0, outcome_of(root)
    assert outcome_of(root)["status"] == "completed"


def build_target(root: Path, workflow_name: str) -> Path:
    """A second target beside the shared fixture's.

    Two runs of one story in one target directory are one resumed run, so a
    module holding a subject and its control side by side needs two roots.
    """
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs", "src"):
        (root / sub).mkdir(parents=True)
    (root / ".harness" / "config.yaml").write_text(
        conftest.CONFIG.format(workflow=workflow_name), encoding="utf-8")
    (root / ".harness" / "stories" / f"{STORY_ID}.yaml").write_text(
        conftest.STORY, encoding="utf-8")
    (root / ".harness" / "standards" / "coding.md").write_text(
        "# Coding Standards\n- keep it simple\n", encoding="utf-8")
    (root / ".harness" / "standards" / "testing.md").write_text(
        "# Testing Standards\n- test everything\n", encoding="utf-8")
    (root / ".harness" / "docs" / "ARCHITECTURE.md").write_text(
        "# Sample Architecture\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    for command in (["git", "init", "-q"],
                    ["git", "config", "user.email", "test@example.com"],
                    ["git", "config", "user.name", "Test"],
                    ["git", "add", "-A"],
                    ["git", "commit", "-q", "-m", "initial"]):
        subprocess.run(command, cwd=root, check=True)
    return root


def test_the_coordinator_writes_the_baseline_at_every_stage_entry(untampered):
    """Written before the stage runs, for the stage that is about to run: every
    invocation found one, and it named the stage being invoked."""
    _, runner, _ = untampered
    assert runner.baselines
    for stage, baseline in zip(runner.calls, runner.baselines):
        assert baseline is not None, stage
        assert baseline.stage == stage


def test_the_baseline_a_re_entered_stage_finds_carries_the_earlier_attempt(
        untampered):
    """The snapshot is taken at entry rather than at the run's start, so the
    second entry to a stage sees what the first left behind -- which is what
    makes the stale answer possible at all."""
    _, runner, _ = untampered
    entries = [baseline for stage, baseline in
               zip(runner.calls, runner.baselines)
               if stage == SKIPPING_STAGE["name"]]
    assert len(entries) > 1
    assert entries[0].signatures == {}
    assert set(entries[1].signatures) == set(
        story_coordinator.required_artifacts(SKIPPING_STAGE))


COORDINATOR_SOURCE = Path(story_coordinator.__file__).read_text(encoding="utf-8")


def calls_of(source: str, name: str) -> int:
    return sum(1 for node in ast.walk(ast.parse(source))
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == name)


def test_the_coordinator_reads_the_persisted_baseline_nowhere():
    """It decides from the snapshot it holds in memory. The written copy is a
    report to the stage and never a second source of truth, so no decision can
    be moved by rewriting it."""
    assert calls_of(COORDINATOR_SOURCE,
                    story_coordinator.read_output_baseline.__name__) == 0
    assert calls_of(COORDINATOR_SOURCE,
                    story_coordinator.write_output_baseline.__name__) == 1


def test_the_scan_reports_a_read_planted_in_the_coordinators_own_source():
    """The control: the same AST reading over the same source with the read
    written into the stage loop, which it finds."""
    planted = COORDINATOR_SOURCE.replace(
        "        write_output_baseline(run_dir, name, artifacts_before)",
        "        write_output_baseline(run_dir, name, artifacts_before)\n"
        "        told = read_output_baseline(run_dir)",
        1)
    assert planted != COORDINATOR_SOURCE
    assert calls_of(planted, story_coordinator.read_output_baseline.__name__) == 1


# --------------------------------------------------------------------------
# The grant, and the invitation
# --------------------------------------------------------------------------


def test_the_grant_reaches_the_runner_and_the_rendered_prompt(
        harness_root, untampered):
    _, runner, _ = untampered
    grant = output_check.grant(harness_root)
    for stage, grants, prompt in zip(runner.calls, runner.grants, runner.prompts):
        assert grants is not None, stage
        assert grant in grants, stage
        assert grant in prompt, stage


def test_no_target_configuration_has_to_declare_that_grant(
        harness_root, untampered):
    """It is appended by the harness, which is what makes the invitation
    runnable in a target that has never heard of it: neither the target's own
    configuration nor the template every new target is created from names it."""
    _, _, target_root = untampered
    grant = output_check.grant(harness_root)
    assert grant not in (target_root / ".harness" / "config.yaml").read_text(
        encoding="utf-8")
    assert output_check.SCRIPT_NAME not in (
        HARNESS_ROOT / "templates" / "config.yaml").read_text(encoding="utf-8")


def test_the_configured_grants_arrive_whole_and_one_is_added(tmp_path):
    configured = ["Bash(grep:*)", "Bash(git show:*)"]
    assert story_coordinator.stage_allowed_tools(
        allowed_tools=configured, harness_root=tmp_path) == (
            configured + [output_check.grant(tmp_path)])
    assert story_coordinator.stage_allowed_tools(
        allowed_tools=None, harness_root=tmp_path) == [
            output_check.grant(tmp_path)]


def context_for(target_root: Path, **kwargs) -> dict:
    return context_assembler.build_context(
        story_text=conftest.STORY,
        story={},
        run_dir=run_dir_of(target_root),
        target_root=target_root,
        harness_root=HARNESS_ROOT,
        config=harness_config.load_config(target_root),
        rules=harness_config.load_rules(HARNESS_ROOT),
        workflow=WORKFLOW,
        retry_count=0,
        **kwargs)


def test_the_resolved_command_is_composed_for_a_stage(target_root):
    context = context_for(target_root, stage=SUBJECT_NAME)
    command = context["output_check_command"]
    assert command == output_check.command_line(
        HARNESS_ROOT, run_dir_of(target_root), SUBJECT_NAME)
    assert command.split()[0] == str(entry_point(HARNESS_ROOT))
    assert Path(command.split()[0]).is_absolute()
    assert str(run_dir_of(target_root)) in command
    assert SUBJECT_NAME in command


def test_omitting_the_stage_renders_what_it_rendered_before(target_root):
    """The optional-placeholder convention: a caller that does not name a stage
    gets None, which renders as the literal None -- exactly what every
    placeholder with nothing behind it has always rendered as."""
    without = context_for(target_root)
    assert without["output_check_command"] is None
    with_stage = context_for(target_root, stage=SUBJECT_NAME)
    assert with_stage["output_check_command"] is not None
    assert {key: value for key, value in without.items()
            if key not in ("output_check_command", "harness_layer")} == {
        key: value for key, value in with_stage.items()
        if key not in ("output_check_command", "harness_layer")}


HARNESS_LAYER = (HARNESS_ROOT / "prompts" / "harness-layer.md").read_text(
    encoding="utf-8")


def test_the_partial_carries_the_resolved_command_and_calls_it_optional(
        target_root):
    """The shipped partial is the subject here: what this harness tells every
    stage is a fact about what it ships, and asserting it needs the artifact
    that ships."""
    context = context_for(target_root, stage=SUBJECT_NAME)
    command = context["output_check_command"]

    placeholders = set(re.findall(r"\{\{(\w+)\}\}", HARNESS_LAYER))
    carried = {key for key, value in context.items()
               if value == command and key in placeholders}
    assert carried, placeholders

    rendered = context["harness_layer"]
    assert command in rendered
    assert "{{" not in rendered
    words = rendered.lower()
    assert "optional" in words
    assert "coordinator" in words
    assert "decides" in words


def test_the_partial_renders_without_a_command_for_a_caller_that_names_no_stage(
        target_root):
    """The control for the render above: the same partial, the same context
    assembly, and no stage -- which renders the literal None and leaves no
    placeholder behind, which is what it did before the placeholder existed."""
    rendered = context_for(target_root)["harness_layer"]
    assert "{{" not in rendered
    assert str(entry_point(HARNESS_ROOT)) not in rendered


# --------------------------------------------------------------------------
# No interpreter is named
# --------------------------------------------------------------------------


#: The commands that would run the entry point as an argument rather than as a
#: program. A path whose final component is one of these, or begins `python`,
#: is naming an interpreter.
INTERPRETERS = ("py", "uv", "uvx", "pipenv", "poetry", "sh", "bash", "env")


def interpreters_named(text: str) -> list[str]:
    named = []
    for token in re.findall(r"[A-Za-z0-9._/\\+-]+", text):
        name = Path(token).name
        if name.startswith("python") or name in INTERPRETERS:
            named.append(token)
    return sorted(set(named))


def shipped_command_lines(tmp_path: Path) -> dict[str, str]:
    """Every place this story writes the entry point or the hook down."""
    run_dir = tmp_path / "runs" / STORY_ID
    return {
        "the rendered command line": output_check.command_line(
            HARNESS_ROOT, run_dir, SUBJECT_NAME),
        "the appended grant": output_check.grant(HARNESS_ROOT),
        "the hook declaration": agent_runner.guard_settings(
            run_dir=run_dir, stage=SUBJECT_NAME),
    }


def test_nothing_this_story_writes_down_names_an_interpreter(tmp_path):
    for where, text in shipped_command_lines(tmp_path).items():
        assert text, where
        assert interpreters_named(text) == [], where


def test_the_same_reading_reports_an_interpreter_put_in_front(tmp_path):
    """The control: each of those with an interpreter prefixed, which the same
    reading reports -- so the silence above is about the text rather than about
    a reading that finds nothing anywhere."""
    for where, text in shipped_command_lines(tmp_path).items():
        assert interpreters_named(f"{sys.executable} {text}") != [], where


@pytest.mark.parametrize("relative", [
    f"scripts/{output_check.SCRIPT_NAME}",
    f"hooks/{agent_runner.STOP_NAME}",
])
def test_what_ships_is_executable_and_carries_its_own_shebang(relative):
    """Which is what makes an interpreter unnecessary rather than merely
    unwritten."""
    path = HARNESS_ROOT / relative
    assert path.is_file(), path
    assert os.access(path, os.X_OK), path
    assert path.read_text(encoding="utf-8").startswith("#!"), path


# --------------------------------------------------------------------------
# The Stop hook, driven as a program
# --------------------------------------------------------------------------


def stop_payload(**fields) -> str:
    payload = {"session_id": "story-112-validation",
               "hook_event_name": agent_runner.STOP_EVENT}
    payload.update(fields)
    return json.dumps(payload)


def run_hook(harness_root: Path, run_dir: Path, stage: str,
             stdin: str | None) -> subprocess.CompletedProcess:
    """The hook, run as a program. `stdin=None` gives it nothing to read."""
    hook = harness_root / "hooks" / agent_runner.STOP_NAME
    if stdin is None:
        return subprocess.run([str(hook), str(run_dir), stage],
                              stdin=subprocess.DEVNULL,
                              capture_output=True, text=True)
    return subprocess.run([str(hook), str(run_dir), stage], input=stdin,
                          capture_output=True, text=True)


def hook_decision(harness_root: Path, run_dir: Path, stage: str,
                  stdin: str | None) -> dict | None:
    """What the hook decided, or None when it decided nothing.

    Silence is the fail-open answer and is a different outcome from a decision;
    it is never conflated here with "did not block".
    """
    result = run_hook(harness_root, run_dir, stage, stdin)
    assert result.returncode == 0, (result.returncode, result.stderr)
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def test_the_hook_blocks_a_turn_ending_with_a_required_output_unwritten(
        harness_root, one_stale):
    decision = hook_decision(harness_root, one_stale, SUBJECT_NAME,
                             stop_payload())
    assert decision is not None
    assert decision["decision"] == "block"
    assert STALE_ARTIFACT in decision["reason"]


def test_the_hook_blocks_at_most_once(harness_root, one_stale):
    """A stage that genuinely cannot write the file must be able to end its
    turn: the second stop carries an active stop hook, and the hook says
    nothing whatever the outputs look like, leaving the coordinator's existing
    self-route to handle it exactly as it does today."""
    first = hook_decision(harness_root, one_stale, SUBJECT_NAME, stop_payload())
    assert first is not None

    again = hook_decision(harness_root, one_stale, SUBJECT_NAME,
                          stop_payload(stop_hook_active=True))
    assert again is None


def test_the_hook_says_nothing_when_every_required_output_is_written(
        harness_root, complete):
    assert hook_decision(harness_root, complete, SUBJECT_NAME,
                         stop_payload()) is None


#: Each failure path the hook's bias covers, as a change to what it is given.
#: `stdin` of None is nothing to read at all; a mutator rewrites the run
#: directory under it.
FAIL_OPEN_CASES = {
    "nothing on stdin": (None, None),
    "a payload that is not JSON": ("{ this is not json", None),
    "a payload that is not an object": ("[]", None),
    "no baseline to compare against": (
        stop_payload(),
        lambda run_dir: story_coordinator.output_baseline_file(run_dir).unlink()),
    "an unreadable state.json": (
        stop_payload(),
        lambda run_dir: (run_dir / "state.json").write_text(
            "{ not json either", encoding="utf-8")),
    "a workflow the harness root does not hold": (
        stop_payload(),
        lambda run_dir: (run_dir / "state.json").write_text(
            json.dumps({**json.loads(
                (run_dir / "state.json").read_text(encoding="utf-8")),
                "workflow": "a-workflow-nothing-defines"}),
            encoding="utf-8")),
}


@pytest.mark.parametrize("case", sorted(FAIL_OPEN_CASES))
def test_the_hook_fails_open(case, harness_root, one_stale):
    """Every failure path yields no decision rather than a block, so a defect
    in the hook stops no run.

    Each case carries its own control: the same run directory and the same
    driver are shown to emit a decision *first*, so the silence that follows is
    about the failure introduced and not about a driver that never speaks.
    """
    assert hook_decision(harness_root, one_stale, SUBJECT_NAME,
                         stop_payload()) is not None

    stdin, mutate = FAIL_OPEN_CASES[case]
    if mutate is not None:
        mutate(one_stale)
    assert hook_decision(harness_root, one_stale, SUBJECT_NAME, stdin) is None


# --------------------------------------------------------------------------
# Where the hook is registered, and where it is not
# --------------------------------------------------------------------------


def declaration(**kwargs) -> dict:
    rendered = agent_runner.guard_settings(**kwargs)
    assert rendered is not None
    assert agent_runner.STOP_PLACEHOLDER not in rendered
    assert agent_runner.STOP_ARGUMENTS_PLACEHOLDER not in rendered
    return json.loads(rendered)["hooks"]


def test_an_invocation_naming_no_run_directory_gets_the_guard_and_nothing_else(
        tmp_path):
    """The planner, the workflow selector and the inspector name no run
    directory, and are given the declaration they were given before this story:
    the Bash guard registered, and no Stop entry at all."""
    for kwargs in ({}, {"stage": SUBJECT_NAME},
                   {"run_dir": tmp_path / "runs" / STORY_ID}):
        hooks = declaration(**kwargs)
        assert agent_runner.STOP_EVENT not in hooks, kwargs
        commands = [hook["command"] for entry in hooks["PreToolUse"]
                    for hook in entry["hooks"]]
        assert len(commands) == 1
        assert Path(commands[0]).resolve() == (
            HARNESS_ROOT / "hooks" / agent_runner.GUARD_NAME).resolve()


def test_a_stage_invocation_is_given_the_stop_entry(tmp_path):
    """The control for the assertion above: both arguments given, and the entry
    appears -- naming the hook by its own path, with the run directory and the
    stage after it."""
    run_dir = tmp_path / "runs" / STORY_ID
    hooks = declaration(run_dir=run_dir, stage=SUBJECT_NAME)
    commands = [hook["command"] for entry in hooks[agent_runner.STOP_EVENT]
                for hook in entry["hooks"]]
    assert len(commands) == 1
    named, *arguments = commands[0].split()
    assert Path(named).resolve() == (
        HARNESS_ROOT / "hooks" / agent_runner.STOP_NAME).resolve()
    assert arguments == [str(run_dir), SUBJECT_NAME]
    assert "PreToolUse" in hooks


class FakePopen:
    """Enough of Popen for run_agent, recording what it was built with."""

    calls: list[list[str]] = []

    def __init__(self, cmd, **kwargs):
        FakePopen.calls.append(list(cmd))
        self.stdin = open(os.devnull, "w")
        self.stdout = iter([json.dumps({"type": "result", "result": "done"}) + "\n"])

    def wait(self):
        self.stdin.close()
        return 0


def built_settings(monkeypatch, tmp_path, **kwargs) -> dict:
    FakePopen.calls = []
    monkeypatch.setattr(agent_runner.subprocess, "Popen", FakePopen)
    agent_runner.run_agent(
        "prompt", stage=SUBJECT_NAME, cwd=tmp_path,
        log_path=tmp_path / "agent.log", permission_mode="acceptEdits",
        model=None, allowed_tools=["Bash(grep:*)"], **kwargs)
    assert len(FakePopen.calls) == 1
    cmd = FakePopen.calls[0]
    return json.loads(cmd[cmd.index("--settings") + 1])["hooks"]


def test_the_runner_registers_the_hook_only_when_it_is_given_a_run_directory(
        monkeypatch, tmp_path):
    run_dir = tmp_path / "runs" / STORY_ID
    with_run_dir = built_settings(monkeypatch, tmp_path, run_dir=run_dir)
    assert agent_runner.STOP_EVENT in with_run_dir

    without = built_settings(monkeypatch, tmp_path)
    assert agent_runner.STOP_EVENT not in without
    assert without["PreToolUse"] == with_run_dir["PreToolUse"]
