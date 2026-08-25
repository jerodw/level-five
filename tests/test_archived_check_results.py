"""story-075: a superseded attempt keeps the check results that judged it.

Before this story `archivable_artifacts` collected three sources — each stage's
outputs, its `changed_files` record, and the keys of its `schemas` map. A check
result is declared in none of them, so no revert-check, suite-run,
claim-support, clean-clone or census result was ever archived: a retry
overwrote each in place and what the harness decided about the superseded
attempt was unrecoverable. This story adds the fourth source and, for a record
that points at a whole-output file of its own, brings that file into the
archive and repoints the archived copy at it.

The subject is *what a retry preserves*, so almost nothing here is asserted
from source. A target repository is built under `tmp_path` with a real suite
the coordinator really runs, a fake agent runner drives it through a failing
verdict and then a passing one, and what is asserted is what that run left in
the run directory.

The workflow those runs execute is built by `tests/conftest.py`'s builder and
materialized into a harness root this module owns. The mechanism is the
subject; the stage list and the check artifact names are inputs to it, and
reading the shipped definition here would turn a deployment fact — which
stages this repository declares checks on, and under which names — into
something this module enforces. Every name is still derived rather than
written: from the fixture's declarations, exactly as it would have been derived
from the shipped ones. The artifact names the fixture declares are deliberately
names this repository does not deploy, so a record found under one of them in a
run directory got there off the declaration.

Every absence asserted here carries a demonstration that it can fail:

  * "the check results are collected off the declarations rather than listed"
    is shown by a probe workflow carrying a check declaration this repository
    does not ship, whose result is archived with no change to harness source,
    and sits beside the same run driven by a coordinator with the fourth
    source taken out, where it is not;
  * "the archived record's pointer resolves to the superseded attempt's
    output" sits beside the same run driven by a coordinator with the pointer
    following taken out, where the archived record still points at the output
    the later attempt wrote;
  * "the clean-clone result is not in the superseded attempt's archive"
    sits beside `archive_attempt` asked for that same name over a directory
    where the file exists, which archives it;
  * "the live root record and the live output file are unchanged by the
    archive" is a byte comparison, and sits beside the same comparison against
    the archived copy, which does differ — so the comparison is one that can
    see a rewrite;
  * "no check name, artifact name or stage name is written into the collecting
    or archiving code" sits beside the same scan over that code with one of
    those names planted in it, which reports it;
  * "the declared correction-pass name names no file" sits beside the
    pass-numbered name the same run does write, which does.

`.harness/docs/ARCHITECTURE.md` is not asserted on: this story's plan assigns
it to the documenter, the stage that runs after this one.

Nothing here invokes a model: every run goes through a fake agent runner.
"""
import json
import shlex
import sys
from pathlib import Path

import pytest

import conftest
from conftest import StageRef, load_mutant, workflow_stage
import story_coordinator
from agent_runner import AgentResult

# The target builder and its file helpers are tests/test_self_routing_retry.py's
# — a repository with a configurable workflow and a configurable test command,
# which is exactly what these runs need. Reused rather than copied so a
# regression in it reddens both files.
from test_self_routing_retry import build_target, write, write_json

# The docstring-stripped reader of one archiving function's own body is
# tests/test_attempt_archiving.py's. Reused for the same reason: the
# no-name-is-written scan lives in two modules and should read the code the
# same way in both.
from test_attempt_archiving import _archive_code_body as archiving_code_body

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
COORDINATOR_PATH = REPO_ROOT / "orchestration" / "story_coordinator.py"
COORDINATOR_SOURCE = COORDINATOR_PATH.read_text(encoding="utf-8")

STORY_ID = "story-001"


# --------------------------------------------------------------------------
# The target's suite
#
# Two files decide it: one under the prefix the writing stage declares it may
# not create, and one outside that prefix. The suite exits zero exactly when
# they agree, and the writing stage writes the same attempt-stamped token into
# both. That is what makes one target drive every check at once:
#
#   * the suite the coordinator runs after the validating stage is green,
#     because the stage left the two agreeing;
#   * the revert check is *permitted*, because restoring the governed file to
#     the state the stage found it in leaves it disagreeing with the token
#     outside the prefix, and the suite goes red — which is the definition of
#     an edit that was needed;
#   * every suite run's whole output names the attempt that produced it, so an
#     archived output file can be told apart from the one that superseded it
#     rather than merely being present under the right name.
# --------------------------------------------------------------------------

GOVERNED_PREFIX = "checks/"
GOVERNED_FILE = "checks/keep.txt"
TOKEN_FILE = "state/token.txt"
INITIAL_TOKEN = "the-state-the-stage-found"

#: What the writing stage writes into both files, stamped with its attempt.
def token_for(attempt: int) -> str:
    return f"attempt-{attempt}"


CHECK_SCRIPT = f'''\
"""The target's whole suite, as far as this module's runs are concerned."""
import pathlib
import sys

governed = pathlib.Path("{GOVERNED_FILE}").read_text(encoding="utf-8").strip()
token = pathlib.Path("{TOKEN_FILE}").read_text(encoding="utf-8").strip()
print(f"governed={{governed}}")
print(f"token={{token}}")
sys.exit(0 if governed == token else 1)
'''

#: The configured command, spelled as an interpreter invocation rather than a
#: shell builtin: the checks run the command directly, not through a shell.
TEST_COMMAND = shlex.join([sys.executable, "check.py"])


# --------------------------------------------------------------------------
# The workflow these runs execute
#
# Every check artifact name below is the fixture's own and appears nowhere in
# the coordinator, which is what makes "the name came off the declaration"
# checkable.
# --------------------------------------------------------------------------

REVERT_ARTIFACT = "revert-probe-result.json"
SUITE_ARTIFACT = "suite-probe-result.json"
CLAIM_ARTIFACT = "claim-probe-result.json"
CLEAN_CLONE_ARTIFACT = "clean-clone-probe-result.json"
CORRECTION_ARTIFACT = "correction-probe.json"

#: A check this repository does not ship, declared under a key the coordinator
#: knows nothing about. Nothing runs it; the stage that declares it writes the
#: record, exactly as a stage under a workflow the harness did not anticipate
#: would. Its presence in the archive is the proof that the collection is
#: derived from the declarations rather than listed.
PARITY_KEY = "parity_check"
PARITY_ARTIFACT = "parity-probe-result.json"

RETRY_CATEGORY = "the-behaviour"

WORKFLOW = conftest.build_workflow(
    workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        may_not_create=(GOVERNED_PREFIX,),
        revert_check={"result": REVERT_ARTIFACT, "baseline": "stage-baseline"},
        schemas={conftest.CHANGED_FILES: "changed-files"}),
    workflow_stage(
        outputs=(conftest.TEST_RESULTS, conftest.TESTER_CHANGED_FILES),
        changed_files=conftest.TESTER_CHANGED_FILES,
        suite_run={"result": SUITE_ARTIFACT},
        schemas={conftest.TEST_RESULTS: "test-results",
                 conftest.TESTER_CHANGED_FILES: "changed-files"}),
    workflow_stage(
        outputs=(conftest.DOCUMENTATION_REPORT,
                 conftest.DOCUMENTER_CHANGED_FILES),
        changed_files=conftest.DOCUMENTER_CHANGED_FILES,
        claim_support={"result": CLAIM_ARTIFACT},
        schemas={conftest.DOCUMENTER_CHANGED_FILES: "changed-files"}),
    workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        clean_clone={"result": CLEAN_CLONE_ARTIFACT,
                     "retry_stage": StageRef(0)},
        correction_pass={"result": CORRECTION_ARTIFACT, "budget": 1,
                         "stage": StageRef(2)},
        retry_routing={RETRY_CATEGORY: {
            "stage": StageRef(0),
            "when": "the behaviour the story asked for is missing"}}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="archived-check-results-workflow",
)

STAGES = WORKFLOW["stages"]
STAGE_NAMES = [stage["name"] for stage in STAGES]
WRITING, VALIDATING, DOCUMENTING, VERIFYING = STAGE_NAMES

VERIFIER_STAGE = next(s for s in STAGES if "correction_pass" in s)
CORRECTION = VERIFIER_STAGE["correction_pass"]
CORRECTION_BUDGET = CORRECTION["budget"]
CORRECTION_ENTRY = CORRECTION["stage"]

#: The results of the checks the coordinator itself runs during a failed
#: attempt, read off the declarations rather than listed. The clean-clone
#: result is deliberately not among them: it is written only after a passing
#: verdict, so a superseded attempt never produced one.
CHECKS_A_FAILED_ATTEMPT_RUNS = [
    STAGES[0]["revert_check"]["result"],
    STAGES[1]["suite_run"]["result"],
    STAGES[2]["claim_support"]["result"],
]

#: The whole-output companion each suite-shaped record points at, named through
#: the coordinator's own name-shaping function rather than by a second spelling
#: of it here.
REVERT_OUTPUT = story_coordinator.suite_output_file(REVERT_ARTIFACT)
SUITE_OUTPUT = story_coordinator.suite_output_file(SUITE_ARTIFACT)


def test_the_fixture_declares_a_check_on_every_stage_that_runs_one():
    """The premises every case below rests on, stated so a change to the
    fixture reddens here rather than quietly emptying the assertions."""
    assert [s["name"] for s in STAGES if "revert_check" in s] == [WRITING]
    assert [s["name"] for s in STAGES if "suite_run" in s] == [VALIDATING]
    assert [s["name"] for s in STAGES if "claim_support" in s] == [DOCUMENTING]
    assert [s["name"] for s in STAGES if "clean_clone" in s] == [VERIFYING]
    assert STAGES[0]["may_not_create"] == [GOVERNED_PREFIX]
    assert CORRECTION_ENTRY == DOCUMENTING


@pytest.mark.parametrize("artifact", [
    REVERT_ARTIFACT, SUITE_ARTIFACT, CLAIM_ARTIFACT, CLEAN_CLONE_ARTIFACT,
    CORRECTION_ARTIFACT, PARITY_ARTIFACT,
])
def test_every_declared_check_name_is_one_the_harness_does_not_carry(artifact):
    """What makes "the name comes off the declaration" checkable: none of these
    appears in the coordinator, so a record found under one of them in a run
    directory got there from the workflow."""
    assert artifact not in COORDINATOR_SOURCE


# --------------------------------------------------------------------------
# The target, and the fake runner that drives it
# --------------------------------------------------------------------------


def build_check_target(root: Path, *, workflow: str = WORKFLOW["name"]) -> Path:
    """A target repository whose configured suite is the script above."""
    build_target(root, workflow=workflow, test_command=TEST_COMMAND)
    write(root / "check.py", CHECK_SCRIPT)
    write(root / GOVERNED_FILE, f"{INITIAL_TOKEN}\n")
    write(root / TOKEN_FILE, f"{INITIAL_TOKEN}\n")
    conftest.commit_setup(root, "the suite this target runs")
    return root


@pytest.fixture
def target_root(tmp_path: Path) -> Path:
    return build_check_target(tmp_path / "archived-checks-target")


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A harness root carrying the definition built above, so every case below
    drives a real coordinator loading a real file."""
    return conftest.materialize_workflow(
        WORKFLOW, tmp_path / "archived-checks-harness")


PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


def failing_verdict(attempt: int) -> dict:
    """A failing verdict whose text names the attempt that produced it."""
    return {
        "status": "failed",
        "blocking_issues": [{
            "severity": "high",
            "issue": f"attempt {attempt} did not implement the sample behavior",
            "location": "src/app.py",
            "required_behavior": "the sample behavior exists",
        }],
        "unverified": [],
        "retry_recommended": True,
        "retry_target": RETRY_CATEGORY,
    }


def finding(marker: str) -> dict:
    """A correctable finding whose words name the verdict that carried it."""
    return {
        "location": ".harness/docs/ARCHITECTURE.md - the routing section",
        "finding": f"MARKER-{marker} the paragraph names a stage that was renamed",
        "correction": f"MARKER-{marker}-FIX name the stage the workflow declares",
        "category": RETRY_CATEGORY,
    }


def passing_with(*findings: dict) -> dict:
    return {**PASS, "correctable_findings": [dict(one) for one in findings]}


class Runner:
    """A fake agent runner that writes each stage's declared artifacts.

    Every artifact it writes comes off the stage's declaration in the *loaded*
    workflow rather than off a list here, and every one of them carries the
    attempt that wrote it — so an archived copy can be told apart from the copy
    that superseded it rather than merely being present under the right name.

    The writing stage also writes the attempt's token into the two files the
    target's suite compares, which is what makes the suite green where the
    stage left the tree and red with the governed one reverted.
    """

    def __init__(self, target_root: Path, verdicts: list[dict],
                 workflow: dict | None = None,
                 extra_outputs: dict[str, tuple[str, ...]] | None = None):
        self.target_root = Path(target_root)
        self.run_dir = run_dir_of(target_root)
        self.stages = (workflow or WORKFLOW)["stages"]
        self.verdicts = list(verdicts)
        self.extra_outputs = dict(extra_outputs or {})
        self.attempt = 0
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None):
        self.calls.append(stage)
        if stage == WRITING:
            self.attempt = self.calls.count(stage)
            token = token_for(self.attempt)
            write(self.target_root / GOVERNED_FILE, f"{token}\n")
            write(self.target_root / TOKEN_FILE, f"{token}\n")
        declaration = next(s for s in self.stages if s["name"] == stage)
        for artifact in story_coordinator.required_artifacts(declaration):
            self._write(artifact, stage)
        for name in self.extra_outputs.get(stage, ()):
            write(self.run_dir / name,
                  f"{name} written on attempt {self.attempt}\n")
        return AgentResult(ok=True, result_text=f"{stage} done")

    def _verdict(self) -> dict:
        seen = self.calls.count(VERIFYING) - 1
        return conftest.answering_guidance(
            self.verdicts[min(seen, len(self.verdicts) - 1)], self.run_dir)

    def _write(self, artifact: str, stage: str) -> None:
        path = self.run_dir / artifact
        if artifact == conftest.VERIFICATION_RESULT:
            verdict = self._verdict()
            write_json(path, verdict)
            if verdict.get("retry_recommended"):
                write_json(self.run_dir / conftest.RETRY_GUIDANCE, {
                    "current_focus": [{
                        "focus": f"guidance issued after attempt {self.attempt}",
                        "satisfied_when": "the next attempt closes it",
                    }],
                    "preserve_behavior": ["the existing behavior"],
                    "retry_scope": ["src/"],
                })
        elif artifact == conftest.CHANGED_FILES:
            # The two files the writing stage really did edit, one of them
            # under the prefix it declared it may not create — which is what
            # gives the revert check something to decide.
            write_json(path, {"modified": [GOVERNED_FILE, TOKEN_FILE],
                              "created": [], "deleted": []})
        elif artifact.endswith("changed-files.json"):
            write_json(path, {"modified": [], "created": [], "deleted": []})
        elif artifact == conftest.TEST_RESULTS:
            write_json(path, {"tests_written": self.attempt})
        else:
            write(path, f"{artifact} written on attempt {self.attempt}.\n")


def run_dir_of(target_root: Path) -> Path:
    return Path(target_root) / ".harness" / "runs" / STORY_ID


def drive(target_root: Path, harness: Path, verdicts: list[dict],
          workflow: dict | None = None, extra_outputs=None,
          coordinator=story_coordinator):
    """One run, returning its exit code, its runner and its run directory."""
    runner = Runner(target_root, verdicts, workflow, extra_outputs)
    code = coordinator.run_story(STORY_ID, harness, target_root, runner)
    return code, runner, run_dir_of(target_root)


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def archive_of(run_dir: Path, attempt: int = 1) -> Path:
    return story_coordinator.attempt_dir(run_dir, attempt)


def names_in(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def pointer_of(record: Path) -> Path:
    """Where a record's `output_path` leads, as a resolved path.

    Resolved at both ends wherever one of these is compared with a path a test
    built, because the temporary directory a run is driven under is reached
    through a symlink on some platforms and an unresolved comparison would
    fail for that rather than for anything about the archive.
    """
    return Path(read_json(record)["output_path"]).resolve()


@pytest.fixture
def retry_then_pass(target_root, harness_root):
    """The central run: one failing verdict, then a passing one.

    Attempt 1 produced every check result a failed attempt produces, and
    attempt 2 wrote over each of them at the run-directory root.
    """
    code, runner, run_dir = drive(
        target_root, harness_root, [failing_verdict(1), PASS])
    assert code == 0, "the shape was meant to complete on the second attempt"
    assert runner.calls == [*STAGE_NAMES, *STAGE_NAMES]
    return runner, run_dir


# --------------------------------------------------------------------------
# What the superseded attempt's archive now holds
# --------------------------------------------------------------------------


def expected_attempt_1_archive() -> list[str]:
    """Everything attempt 1 produced, derived from the fixture's declarations.

    The stage artifacts the archive already carried before this story, the
    conditional guidance the failing verdict wrote, the results of the checks a
    failed attempt runs, and the whole-output companion each suite-shaped
    result points at.
    """
    stage_artifacts = {
        artifact for stage in STAGES
        for artifact in story_coordinator.required_artifacts(stage)
    }
    return sorted(stage_artifacts | {conftest.RETRY_GUIDANCE}
                  | set(CHECKS_A_FAILED_ATTEMPT_RUNS)
                  | {REVERT_OUTPUT, SUITE_OUTPUT})


def test_the_archive_holds_every_check_result_attempt_1_produced(retry_then_pass):
    """The story's motivating case, established by driving a real retry rather
    than by reading `archivable_artifacts`."""
    _, run_dir = retry_then_pass
    archive = archive_of(run_dir)
    assert archive.is_dir()
    for artifact in CHECKS_A_FAILED_ATTEMPT_RUNS:
        assert (archive / artifact).is_file(), artifact


def test_the_archive_holds_exactly_what_attempt_1_produced(retry_then_pass):
    """Stated as the whole set, so a name that stopped being archived reddens
    here — which is what holds "everything archived before this story is still
    archived" and "the layout beneath the attempt directory is unchanged"."""
    _, run_dir = retry_then_pass
    assert names_in(archive_of(run_dir)) == expected_attempt_1_archive()


def test_the_archived_check_results_describe_attempt_1(retry_then_pass):
    """Present under the right name is not enough: each archived record has to
    be the one attempt 1 produced."""
    _, run_dir = retry_then_pass
    archive = archive_of(run_dir)

    revert = read_json(archive / REVERT_ARTIFACT)
    assert revert["paths"] == [GOVERNED_FILE]
    assert revert["permitted"] is True
    suite = read_json(archive / SUITE_ARTIFACT)
    assert suite["exit_code"] == 0
    assert token_for(1) in suite["output_tail"]
    assert token_for(2) not in suite["output_tail"]


def test_the_live_root_copies_describe_attempt_2(retry_then_pass):
    """The archive copies rather than moves, and what stays at the root is the
    current attempt's — which is where every existing reader looks."""
    _, run_dir = retry_then_pass
    for artifact in [*CHECKS_A_FAILED_ATTEMPT_RUNS, REVERT_OUTPUT, SUITE_OUTPUT]:
        assert (run_dir / artifact).is_file(), artifact
    suite = read_json(run_dir / SUITE_ARTIFACT)
    assert token_for(2) in suite["output_tail"]
    assert token_for(1) not in suite["output_tail"]


def test_the_root_output_file_still_holds_the_current_attempts_output(
    retry_then_pass,
):
    """The live companion keeps its canonical name and its contents: only the
    archived copy's pointer was rewritten, and nothing was moved."""
    _, run_dir = retry_then_pass
    root_output = run_dir / SUITE_OUTPUT
    assert token_for(2) in root_output.read_text(encoding="utf-8")
    assert pointer_of(run_dir / SUITE_ARTIFACT) == root_output.resolve()


# --------------------------------------------------------------------------
# The pointer an archived record carries
# --------------------------------------------------------------------------


def test_the_archived_record_points_at_the_archived_output(retry_then_pass):
    """Reading the archived record leads to the output of the attempt that
    record belongs to, rather than to whatever the next attempt wrote over."""
    _, run_dir = retry_then_pass
    archive = archive_of(run_dir)

    pointer = pointer_of(archive / SUITE_ARTIFACT)
    assert pointer == (archive / SUITE_OUTPUT).resolve()
    assert pointer.is_file()
    output = pointer.read_text(encoding="utf-8")
    assert token_for(1) in output
    assert token_for(2) not in output


def test_the_revert_checks_own_output_travels_with_its_record(retry_then_pass):
    """The pointer is followed off the record's own field rather than off a
    filename, so every suite-shaped record gets it — not only the one the
    story's description happened to name."""
    _, run_dir = retry_then_pass
    archive = archive_of(run_dir)
    pointer = pointer_of(archive / REVERT_ARTIFACT)
    assert pointer == (archive / REVERT_OUTPUT).resolve()
    assert pointer.is_file()


#: The pointer following, as it stands in `archive_attempt`. Taking it out is
#: what the control below drives, so an archived record that led to the *later*
#: attempt's output is shown to be a state this suite can report.
POINTER_FOLLOWING = """\
        companion = _archive_companion_output(run_dir, archived_copy, destination)
        if companion is not None:
            archived.append(companion)
"""


@pytest.fixture
def coordinator_without_the_pointer_following(tmp_path: Path):
    return load_mutant(COORDINATOR_PATH, [(POINTER_FOLLOWING, "")],
                       name="coordinator_without_pointer_following",
                       tmp_path=tmp_path)


def test_without_the_pointer_following_the_archived_record_leads_elsewhere(
    target_root, harness_root, coordinator_without_the_pointer_following,
):
    """The control for the case above. Driven by today's coordinator with the
    pointer following taken out, the same run archives the record alone and
    leaves it pointing at the run-directory root — where attempt 2's output now
    is. That is the state the assertion above exists to catch."""
    code, _, run_dir = drive(
        target_root, harness_root, [failing_verdict(1), PASS],
        coordinator=coordinator_without_the_pointer_following)
    assert code == 0

    archive = archive_of(run_dir)
    assert not (archive / SUITE_OUTPUT).exists()
    pointer = pointer_of(archive / SUITE_ARTIFACT)
    assert pointer == (run_dir / SUITE_OUTPUT).resolve()
    assert token_for(2) in pointer.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The names are derived from the declarations, not listed
# --------------------------------------------------------------------------


def probe_workflow() -> dict:
    """The built definition with a check declaration this repository does not
    ship added to the validating stage. Nothing else differs."""
    workflow = json.loads(json.dumps(WORKFLOW))
    workflow["name"] = "archived-check-results-probe-workflow"
    for stage in workflow["stages"]:
        if stage["name"] == VALIDATING:
            stage[PARITY_KEY] = {"result": PARITY_ARTIFACT}
    return workflow


PROBE = probe_workflow()


@pytest.fixture
def probe_harness_root(tmp_path: Path) -> Path:
    return conftest.materialize_workflow(PROBE, tmp_path / "probe-harness")


@pytest.fixture
def probe_target_root(tmp_path: Path) -> Path:
    return build_check_target(tmp_path / "probe-target", workflow=PROBE["name"])


def drive_the_probe(target_root: Path, harness: Path, coordinator):
    return drive(target_root, harness, [failing_verdict(1), PASS],
                 workflow=PROBE,
                 extra_outputs={VALIDATING: (PARITY_ARTIFACT,)},
                 coordinator=coordinator)


def test_a_check_declaration_the_repository_does_not_ship_archives_itself(
    probe_target_root, probe_harness_root,
):
    """The proof that the collection is derived: orchestration/story_coordinator.py
    is not touched, only the workflow definition the run loads."""
    assert PARITY_ARTIFACT in story_coordinator.archivable_artifacts(
        PROBE["stages"])

    code, _, run_dir = drive_the_probe(
        probe_target_root, probe_harness_root, story_coordinator)
    assert code == 0

    archived = archive_of(run_dir) / PARITY_ARTIFACT
    assert archived.read_text(encoding="utf-8") == (
        f"{PARITY_ARTIFACT} written on attempt 1\n")


#: The fourth source, as it stands in `archivable_artifacts`. Taking it out is
#: what the control below drives.
FOURTH_SOURCE = """\
        for value in stage.values():
            if isinstance(value, dict) and isinstance(value.get("result"), str):
                names.add(value["result"])
"""


@pytest.fixture
def coordinator_without_the_fourth_source(tmp_path: Path):
    return load_mutant(COORDINATOR_PATH, [(FOURTH_SOURCE, "")],
                       name="coordinator_without_fourth_source",
                       tmp_path=tmp_path)


def test_without_the_fourth_source_no_check_result_is_archived_at_all(
    probe_target_root, probe_harness_root, coordinator_without_the_fourth_source,
):
    """The control for both cases above: the same runs, driven by today's
    coordinator with the fourth source taken out, archive the stage artifacts
    and none of the check results — which is the state before this story."""
    code, _, run_dir = drive_the_probe(
        probe_target_root, probe_harness_root,
        coordinator_without_the_fourth_source)
    assert code == 0

    archive = archive_of(run_dir)
    assert not (archive / PARITY_ARTIFACT).exists()
    for artifact in CHECKS_A_FAILED_ATTEMPT_RUNS:
        assert not (archive / artifact).exists(), artifact
    # The stage artifacts are still there, so the mutant archived something
    # and the absences above are about the check results rather than about a
    # run that archived nothing at all.
    assert (archive / conftest.IMPLEMENTATION_SUMMARY).is_file()


#: The functions this story's constraint governs: no check name, artifact name
#: or stage name may be written into any of them.
ARCHIVING_FUNCTIONS = ["archivable_artifacts", "archive_attempt",
                       "_archive_companion_output"]

#: Every name the constraint forbids, derived from the fixture's definition
#: exactly as the coordinator derives what it archives.
FORBIDDEN_NAMES = sorted(
    set(STAGE_NAMES)
    | set(story_coordinator.archivable_artifacts(PROBE["stages"]))
    | {REVERT_OUTPUT, SUITE_OUTPUT}
)


def planted(code: str, name: str) -> str:
    """That code with one forbidden name written into it."""
    return code + f'\n_planted = "{name}"\n'


@pytest.mark.parametrize("function", ARCHIVING_FUNCTIONS)
def test_no_check_artifact_or_stage_name_is_written_into_the_archiving_code(
    function,
):
    code = archiving_code_body(function)
    for name in FORBIDDEN_NAMES:
        assert name not in code, name


@pytest.mark.parametrize("function", ARCHIVING_FUNCTIONS)
def test_the_same_scan_reports_a_name_planted_in_that_code(function):
    """The control for the case above: the scan is one that can report a
    violation, rather than one that has stopped seeing anything."""
    code = planted(archiving_code_body(function), SUITE_ARTIFACT)
    reported = [name for name in FORBIDDEN_NAMES if name in code]
    assert reported == [SUITE_ARTIFACT]


# --------------------------------------------------------------------------
# A check result the superseded attempt did not produce
# --------------------------------------------------------------------------


def test_the_clean_clone_result_is_asked_for_and_skipped(retry_then_pass):
    """It is written only after a passing verdict, so the failed attempt never
    produced one. The archive skips it rather than failing, exactly as an
    absent stage artifact already is skipped."""
    _, run_dir = retry_then_pass
    assert CLEAN_CLONE_ARTIFACT in story_coordinator.archivable_artifacts(STAGES)
    assert not (archive_of(run_dir) / CLEAN_CLONE_ARTIFACT).exists()
    # And the run did produce one, on the attempt whose verdict passed — so the
    # absence above is about the attempt rather than about a check that never
    # ran under this fixture at all.
    assert (run_dir / CLEAN_CLONE_ARTIFACT).is_file()


def test_the_same_name_is_archived_when_the_attempt_did_produce_it(tmp_path: Path):
    """The control for the case above: `archive_attempt` asked for that same
    name over a directory where the file exists copies it. The absence is
    therefore about what attempt 1 wrote, not about a name nothing looks for."""
    write(tmp_path / CLEAN_CLONE_ARTIFACT, '{"exit_code": 0}\n')
    archived = story_coordinator.archive_attempt(
        tmp_path, [CLEAN_CLONE_ARTIFACT], 1)
    assert archived == [CLEAN_CLONE_ARTIFACT]
    assert (story_coordinator.attempt_dir(tmp_path, 1)
            / CLEAN_CLONE_ARTIFACT).is_file()


# --------------------------------------------------------------------------
# archive_attempt in isolation: what it returns, and what it leaves alone
# --------------------------------------------------------------------------


@pytest.fixture
def record_and_its_output(tmp_path: Path) -> Path:
    """A run directory holding one suite-shaped record and the file it points
    at, as a check leaves them."""
    output = tmp_path / SUITE_OUTPUT
    write(output, "the whole output of the run this record describes\n")
    write_json(tmp_path / SUITE_ARTIFACT,
               {"ran": True, "exit_code": 0, "output_path": str(output)})
    return tmp_path


def test_archive_attempt_returns_every_name_it_copied(record_and_its_output):
    """The companion is in the returned list beside the record's own name."""
    archived = story_coordinator.archive_attempt(
        record_and_its_output, [SUITE_ARTIFACT, "absent.json"], 4)
    assert archived == [SUITE_ARTIFACT, SUITE_OUTPUT]
    assert names_in(story_coordinator.attempt_dir(record_and_its_output, 4)) == \
        sorted([SUITE_ARTIFACT, SUITE_OUTPUT])


def test_the_archive_leaves_the_live_record_and_output_byte_for_byte(
    record_and_its_output,
):
    """The archive copies rather than moves, and rewrites the pointer in the
    archived copy alone.

    The control is in the same case: the archived copy *is* compared with the
    same bytes and does differ, so a comparison that would go green whatever
    happened is not what the two assertions about the live files rest on.
    """
    run_dir = record_and_its_output
    record_before = (run_dir / SUITE_ARTIFACT).read_bytes()
    output_before = (run_dir / SUITE_OUTPUT).read_bytes()

    story_coordinator.archive_attempt(run_dir, [SUITE_ARTIFACT], 1)

    assert (run_dir / SUITE_ARTIFACT).read_bytes() == record_before
    assert (run_dir / SUITE_OUTPUT).read_bytes() == output_before
    archive = story_coordinator.attempt_dir(run_dir, 1)
    assert (archive / SUITE_ARTIFACT).read_bytes() != record_before
    assert (archive / SUITE_OUTPUT).read_bytes() == output_before


@pytest.mark.parametrize("payload", [
    "this is not JSON at all\n",
    '["a record that is not an object"]\n',
    '{"ran": true, "exit_code": 0}\n',
    '{"ran": true, "output_path": "nothing-here.txt"}\n',
    '{"ran": true, "output_path": ""}\n',
])
def test_a_record_with_no_followable_pointer_is_copied_verbatim(
    tmp_path: Path, payload: str,
):
    """A record that is not JSON, is not an object, carries no pointer, or
    whose pointer names nothing that exists is copied exactly as before."""
    write(tmp_path / CLAIM_ARTIFACT, payload)
    archived = story_coordinator.archive_attempt(tmp_path, [CLAIM_ARTIFACT], 2)
    assert archived == [CLAIM_ARTIFACT]
    destination = story_coordinator.attempt_dir(tmp_path, 2)
    assert names_in(destination) == [CLAIM_ARTIFACT]
    assert (destination / CLAIM_ARTIFACT).read_text(encoding="utf-8") == payload


def test_a_pointer_outside_the_run_directory_is_left_where_it_points(
    tmp_path: Path,
):
    """A record naming a file outside the run directory names one this archive
    has no claim on: nothing is copied and nothing is rewritten."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outside = tmp_path / "elsewhere.txt"
    write(outside, "output this archive has no claim on\n")
    write_json(run_dir / SUITE_ARTIFACT, {"output_path": str(outside)})

    archived = story_coordinator.archive_attempt(run_dir, [SUITE_ARTIFACT], 1)
    assert archived == [SUITE_ARTIFACT]
    destination = story_coordinator.attempt_dir(run_dir, 1)
    assert names_in(destination) == [SUITE_ARTIFACT]
    assert read_json(destination / SUITE_ARTIFACT)["output_path"] == str(outside)


def test_a_relative_pointer_is_read_against_the_run_directory(tmp_path: Path):
    """The records the coordinator writes carry absolute paths; a relative one
    is resolved against the run directory rather than against whatever
    directory the archive happened to be called from."""
    write(tmp_path / SUITE_OUTPUT, "the whole output\n")
    write_json(tmp_path / SUITE_ARTIFACT, {"output_path": SUITE_OUTPUT})

    archived = story_coordinator.archive_attempt(tmp_path, [SUITE_ARTIFACT], 1)
    assert archived == [SUITE_ARTIFACT, SUITE_OUTPUT]
    destination = story_coordinator.attempt_dir(tmp_path, 1)
    assert pointer_of(destination / SUITE_ARTIFACT) == \
        (destination / SUITE_OUTPUT).resolve()


# --------------------------------------------------------------------------
# The attempt directory's naming and layout are unchanged
# --------------------------------------------------------------------------


def test_the_attempt_directory_is_named_and_placed_as_it_was(retry_then_pass):
    """Settled by earlier stories and unchanged here: one directory per
    superseded attempt, under `attempts/`, flat, named for the attempt number
    the rendered prompts already use."""
    _, run_dir = retry_then_pass
    attempts = run_dir / "attempts"
    assert [path.name for path in attempts.iterdir()] == ["attempt-1"]
    assert archive_of(run_dir) == attempts / "attempt-1"
    assert all(path.is_file() for path in archive_of(run_dir).iterdir())
    assert (run_dir / story_coordinator.prompt_file(WRITING, 1)).is_file()
    # Attempt 2 passed, so it was never superseded and is not archived.
    assert not (attempts / "attempt-2").exists()


def test_the_resume_archive_is_built_on_the_same_collection():
    """`interrupted_attempt_artifacts` reads through `archivable_artifacts`, so
    a resume's archive carries the check results for the same reason a retry's
    does — with no second collection to keep in step."""
    collected = story_coordinator.archivable_artifacts(STAGES)
    interrupted = story_coordinator.interrupted_attempt_artifacts(STAGES, 1)
    for artifact in CHECKS_A_FAILED_ATTEMPT_RUNS:
        assert artifact in collected, artifact
        assert artifact in interrupted, artifact


# --------------------------------------------------------------------------
# What happens to a correction pass's record: established, and unchanged
#
# The run below spends the correction budget on its first verdict, then takes a
# retry, then meets a third verdict that would spend it again. That is the only
# shape in which "a retry does not reset the count, so no record is ever
# overwritten" is a fact about the run rather than about a counter nobody moved.
# --------------------------------------------------------------------------

FIRST_FINDING = finding("FIRST")
LATER_FINDING = finding("LATER")


@pytest.fixture
def correction_then_retry(target_root, harness_root):
    code, runner, run_dir = drive(target_root, harness_root, [
        passing_with(FIRST_FINDING),   # spends the pass
        failing_verdict(1),            # takes the retry
        passing_with(LATER_FINDING),   # the budget is already spent
    ])
    assert code == 0
    return runner, run_dir


def correction_record_name(number: int) -> str:
    """The name a pass's record is written under, through the coordinator's own
    name-shaping function rather than by a second spelling of it here."""
    return story_coordinator.correction_pass_result_file(
        CORRECTION_ARTIFACT, number)


def test_a_pass_records_itself_under_a_name_keyed_by_its_pass_number(
    correction_then_retry,
):
    """The positive half, and the control for the absence below: the record the
    run wrote is under the pass-numbered name, not the declared one."""
    _, run_dir = correction_then_retry
    record = run_dir / correction_record_name(1)
    assert record.is_file()
    assert record.name != CORRECTION_ARTIFACT
    assert read_json(record)["pass"] == 1
    assert read_json(record)["findings"] == [FIRST_FINDING]


def test_the_declared_correction_name_names_no_file_and_is_skipped(
    correction_then_retry,
):
    """The declared name is collected like any other check result and skipped
    like any other absent artifact, because nothing is ever written under it."""
    _, run_dir = correction_then_retry
    assert CORRECTION_ARTIFACT in story_coordinator.archivable_artifacts(STAGES)
    assert not (run_dir / CORRECTION_ARTIFACT).exists()
    assert not (archive_of(run_dir) / CORRECTION_ARTIFACT).exists()


def test_the_retry_does_not_reset_the_count_so_the_record_is_not_overwritten(
    correction_then_retry,
):
    """The whole of what this story establishes about the correction pass, held
    against a run rather than argued: the count survives the retry, the later
    verdict's findings spend nothing, and pass 1's record still holds pass 1's
    words."""
    _, run_dir = correction_then_retry
    state = read_json(run_dir / "state.json")
    assert state["retry_count"] == 1
    assert state["correction_pass_count"] == CORRECTION_BUDGET == 1

    record = read_json(run_dir / correction_record_name(1))
    assert record["findings"] == [FIRST_FINDING]
    assert LATER_FINDING not in record["findings"]
    assert not (run_dir / correction_record_name(2)).exists()


def test_the_pass_entered_at_the_declared_stage_and_spent_no_attempt(
    correction_then_retry,
):
    """No correction-pass behaviour changed. Stated as what the run did: the
    pass re-entered at the stage the declaration names, and the one archived
    attempt is the retry's rather than the pass's."""
    runner, run_dir = correction_then_retry
    entry_points = runner.calls[len(STAGE_NAMES):]
    assert entry_points[:2] == [CORRECTION_ENTRY, VERIFYING]
    assert [path.name for path in (run_dir / "attempts").iterdir()] == ["attempt-1"]
