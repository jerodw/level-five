"""Independent validation for story-022: a stage's required outputs must be
written by the attempt that ran.

The subject is a *retry that leaves a file alone*, so almost nothing here is
asserted from source. A target repository is built under tmp_path, a fake
agent runner drives it through a retry, and on the second pass one stage
declines to touch one of the artifacts it is required to produce. What the
coordinator does with that is whatever the run does.

The controls are the point of this file. Every claim of the form "this run was
not flagged" is paired with the *same fixture and the same stage* making the
one change that should flag it, so a green result cannot mean the check
stopped looking:

  * "a retry that rewrites its outputs completes" sits beside the identical
    run whose second pass skips one write, which escalates — once per stage
    and per artifact the loaded workflow declares;
  * "a byte-identical rewrite is not flagged" sits beside the same runner with
    that rewrite removed, which is flagged, and beside a direct demonstration
    that such a rewrite moves the modification time and not the size;
  * "a first attempt is not flagged" sits beside a second attempt of the same
    stage over the same artifact, which is;
  * "the escalation consumed no attempt number" sits beside the verifier
    reroute in the same run, where those very counters do move;
  * "the missing reason says nothing about a previous attempt" sits beside the
    stale reason, which does — both read off runs rather than matched against
    a single literal;
  * "no artifact or stage name appears in the check" sits beside the same scan
    run over a copy of the check with such a name planted in it;
  * "exactly one snapshot is taken" sits beside the same scan over a source
    with a second snapshot planted, and beside a counting wrapper reporting
    the snapshots an actual run took;
  * "a passing run raises no escalation" sits beside the skipping run, which
    raises one.

Nothing here invokes a model, and nothing resolves a coordinator out of git
history: the before state is carried by the control of each pair rather than
by an older module.
"""
import ast
import inspect
import json
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

import story_coordinator
import test_story_012_validation as story012
from agent_runner import AgentResult

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
WORKFLOW = json.loads(
    (REPO_ROOT / "workflows" / "story-workflow.json").read_text(encoding="utf-8"))
COORDINATOR_SOURCE = Path(story_coordinator.__file__).read_text(encoding="utf-8")

#: Read off the loaded workflow rather than written here, so this file names
#: no stage the definition does not.
STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
VERIFIER_STAGE = next(s for s in WORKFLOW["stages"] if "on_failure" in s)
VERIFIER = VERIFIER_STAGE["name"]
RETRY_STAGE = VERIFIER_STAGE["on_failure"]["retry_stage"]
RETRY_INDEX = STAGE_NAMES.index(RETRY_STAGE)
VERIFIER_INDEX = STAGE_NAMES.index(VERIFIER)

#: The stages a retry runs a second time: from the stage a failure reroutes
#: to, through the verifier that decides again.
RERUN_STAGE_DECLARATIONS = WORKFLOW["stages"][RETRY_INDEX:VERIFIER_INDEX + 1]

STORY_ID = "story-001"

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


def failing(attempt: int) -> dict:
    """A failing verdict that recommends a retry, stamped with its attempt."""
    return {
        "status": "failed",
        "blocking_issues": [{
            "severity": "high",
            "issue": f"attempt {attempt} did not implement the sample behavior",
            "location": f"src/attempt_{attempt}.py",
            "required_behavior": f"the sample behavior exists after attempt {attempt}",
        }],
        "unverified": [],
        "retry_recommended": True,
    }


# --------------------------------------------------------------------------
# The target repository
#
# Built here rather than taken from the shared fixture because several tests
# below run a control beside their subject, and two runs of one story in one
# target directory are one resumed run.
# --------------------------------------------------------------------------

STORY = """\
story:
  id: story-001
  title: Sample story for coordinator tests
  description: |
    A stand-in story used to exercise the workflow deterministically.

tasks:
  - do the sample work

acceptance_criteria:
  - the sample behavior exists
  - existing behavior is preserved

scope:
  modify:
    - src/
  do_not_modify:
    - rules/

verification_requirements:
  - confirm the sample behavior

constraints:
  - preserve existing behavior
"""

CONFIG = """\
project: freshness-target
workflow: story-workflow
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: echo tests-ok
"""


def write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def write_json(path: Path, payload) -> str:
    return write(path, json.dumps(payload, indent=2) + "\n")


def build_target(root: Path) -> Path:
    write(root / ".harness" / "config.yaml", CONFIG)
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "src" / "app.py", "print('hello')\n")
    (root / ".harness" / "runs").mkdir(parents=True, exist_ok=True)
    (root / ".harness" / "logs").mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    return root


@pytest.fixture
def make_target(tmp_path: Path):
    """A factory, so a test can hold a subject and its control side by side."""
    def make(name: str) -> Path:
        return build_target(tmp_path / name)
    return make


# --------------------------------------------------------------------------
# The fake runner
#
# Every stage writes the artifacts its own declaration in the *loaded*
# workflow requires — never a list written here — under a per-call plan saying,
# for a named artifact, whether this call writes it afresh, rewrites the bytes
# it already holds, leaves it alone, or deletes it.
# --------------------------------------------------------------------------

FRESH = "fresh"      #: write content stamped with this attempt
SAME = "same"        #: rewrite the exact bytes already on disk
SKIP = "skip"        #: do not touch it — the defect this story closes
DELETE = "delete"    #: remove it — the missing case, which is not this one


@dataclass(frozen=True)
class Bookkeeping:
    """The three things an escalation must not consume, read off the run."""

    retry_count: int
    prompts: tuple
    attempts: tuple


def bookkeeping_of(run_dir: Path) -> Bookkeeping:
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    archives = run_dir / "attempts"
    return Bookkeeping(
        retry_count=state["retry_count"],
        prompts=tuple(sorted(p.name for p in run_dir.glob("prompt-*.md"))),
        attempts=tuple(sorted(p.name for p in archives.glob("*"))
                       if archives.is_dir() else ()),
    )


class Runner:
    """A fake agent runner driven by a per-stage, per-call artifact plan.

    It records, at the entry to every stage, the run's attempt bookkeeping —
    which is how "the escalation consumed no attempt number" is checked as a
    fact observed during the run rather than as a number written here.
    """

    def __init__(self, target_root: Path, verdicts: list, plans: dict | None = None,
                 workflow: dict | None = None, story_id: str = STORY_ID):
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.verdicts = list(verdicts)
        self.plans = plans or {}
        self.stages = (workflow or WORKFLOW)["stages"]
        self.calls: list[str] = []
        #: artifact -> the exact text last written, so SAME can reproduce it
        self.written: dict[str, str] = {}
        #: (stage, the bookkeeping the run carried when the stage started)
        self.seen: list[tuple] = []

    def _declaration(self, stage: str) -> dict:
        return next(s for s in self.stages if s["name"] == stage)

    @staticmethod
    def _nth(sequence: list, index: int):
        return sequence[min(index, len(sequence) - 1)]

    def _fresh(self, artifact: str, attempt: int, verdict: dict) -> str:
        path = self.run_dir / artifact
        if artifact == "verification-result.json":
            return write_json(path, verdict)
        if artifact == "changed-files.json":
            return write_json(path, {"modified": ["src/app.py"],
                                     "created": [f"src/attempt_{attempt}.py"],
                                     "deleted": []})
        if artifact == "tester-changed-files.json":
            return write_json(path, {"modified": [],
                                     "created": [f"tests/test_attempt_{attempt}.py"],
                                     "deleted": []})
        if artifact == "test-results.json":
            return write_json(path, {"status": "passed", "tests_written": attempt,
                                     "tests_run": 5, "tests_passed": 5,
                                     "tests_failed": 0, "failures": []})
        return write(path, f"{artifact} written on attempt {attempt}.\n")

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None):
        self.calls.append(stage)
        self.seen.append((stage, bookkeeping_of(self.run_dir)))
        attempt = max(1, self.calls.count(RETRY_STAGE))
        plan = self._nth(self.plans.get(stage, [{}]), self.calls.count(stage) - 1)
        verdict = self._nth(self.verdicts, max(0, self.calls.count(VERIFIER) - 1))

        for artifact in story_coordinator.required_artifacts(self._declaration(stage)):
            mode = plan.get(artifact, FRESH)
            path = self.run_dir / artifact
            if mode == SKIP:
                continue
            if mode == DELETE:
                path.unlink(missing_ok=True)
                self.written.pop(artifact, None)
                continue
            self.written[artifact] = (
                write(path, self.written[artifact]) if mode == SAME
                else self._fresh(artifact, attempt, verdict)
            )

        # The conditional artifact, written exactly where story-012 has the
        # verifier write it. Nothing in this story touches it.
        if stage == VERIFIER and verdict["status"] == "failed":
            write_json(self.run_dir / "retry-guidance.json",
                       story012.guidance_for(attempt))
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path) -> Path:
    return target_root / ".harness" / "runs" / STORY_ID


def state_of(target_root: Path) -> dict:
    return json.loads(
        (run_dir_of(target_root) / "state.json").read_text(encoding="utf-8"))


def messages(target_root: Path) -> list[str]:
    log = (run_dir_of(target_root) / "events.log").read_text(encoding="utf-8")
    return [line.split("] ", 1)[1] for line in log.splitlines() if "] " in line]


def escalation_reason(target_root: Path) -> str:
    """The reason the run escalated, read out of events.log."""
    reasons = [m.split("escalated: ", 1)[1]
               for m in messages(target_root) if m.startswith("escalated: ")]
    assert len(reasons) == 1, reasons
    return reasons[0]


def summary_of(target_root: Path) -> str:
    return (run_dir_of(target_root) / "escalation-summary.md").read_text(
        encoding="utf-8")


def event_kinds(target_root: Path) -> list[str]:
    history = json.loads(
        (run_dir_of(target_root) / "execution-history.json").read_text(
            encoding="utf-8"))
    return [entry["event"] for entry in history]


def drive(target_root: Path, harness_root: Path, plans: dict | None = None,
          verdicts: list | None = None, workflow: dict | None = None):
    """One run of the sample story, returning its exit code and its runner."""
    runner = Runner(target_root, verdicts or [failing(1), PASS], plans,
                    workflow=workflow)
    code = story_coordinator.run_story(
        STORY_ID, harness_root, target_root, runner)
    return code, runner


#: The plan shapes the control pairs are built from: a second pass at the
#: retry stage that leaves one required artifact alone, and the same plan
#: writing it. The artifact is the one story-020 actually shipped stale, taken
#: off the declaration rather than named as a constant of the coordinator's.
STALE_ARTIFACT = story_coordinator.required_artifacts(
    WORKFLOW["stages"][RETRY_INDEX])[-1]
SKIPS_ON_THE_RETRY = {RETRY_STAGE: [{}, {STALE_ARTIFACT: SKIP}]}
WRITES_ON_THE_RETRY = {RETRY_STAGE: [{}, {}]}


# --------------------------------------------------------------------------
# The defect, and the control that shares its shape
# --------------------------------------------------------------------------


@pytest.fixture
def stale_retry(make_target, harness_root):
    target_root = make_target("stale-target")
    code, runner = drive(target_root, harness_root, SKIPS_ON_THE_RETRY)
    return code, runner, target_root


@pytest.fixture
def fresh_retry(make_target, harness_root):
    target_root = make_target("fresh-target")
    code, runner = drive(target_root, harness_root, WRITES_ON_THE_RETRY)
    return code, runner, target_root


def test_a_retry_that_leaves_a_required_output_untouched_escalates(stale_retry):
    code, _, target_root = stale_retry
    assert code == 2
    state = state_of(target_root)
    assert state["status"] == "escalated"
    assert state["current_stage"] == RETRY_STAGE


def test_the_same_run_that_writes_the_output_completes(fresh_retry):
    """The control for the assertion above: one plan entry differs, and it is
    the write. Without it, the escalation could be the fixture's fault."""
    code, _, target_root = fresh_retry
    assert code == 0
    assert state_of(target_root)["status"] == "completed"


def test_the_run_does_not_advance_past_the_stale_stage(stale_retry, fresh_retry):
    """Compared against the control's call sequence rather than against a list
    written here: the two runs agree up to the stale stage, and only the
    control carries on past it."""
    _, stale, _ = stale_retry
    _, fresh, _ = fresh_retry
    assert stale.calls == fresh.calls[:len(stale.calls)]
    assert stale.calls[-1] == RETRY_STAGE
    assert len(fresh.calls) > len(stale.calls)
    assert fresh.calls[len(stale.calls)] != RETRY_STAGE


def test_the_artifact_left_behind_is_the_superseded_attempts(stale_retry):
    """What makes the escalation right rather than merely red: the file at the
    run root still holds attempt 1's content while the run reached attempt 2."""
    _, _, target_root = stale_retry
    assert (run_dir_of(target_root) / STALE_ARTIFACT).read_text(
        encoding="utf-8").endswith("on attempt 1.\n")
    assert state_of(target_root)["retry_count"] == 1


# --------------------------------------------------------------------------
# The reason: stale is not missing
# --------------------------------------------------------------------------


@pytest.fixture
def deleted_retry(make_target, harness_root):
    """The same stage on the same retry, with the artifact removed instead of
    left alone — the condition that already had a reason of its own."""
    target_root = make_target("deleted-target")
    code, runner = drive(target_root, harness_root,
                         {RETRY_STAGE: [{}, {STALE_ARTIFACT: DELETE}]})
    return code, runner, target_root


def test_the_stale_reason_names_the_artifact_and_calls_it_a_previous_attempts(
    stale_retry,
):
    _, _, target_root = stale_retry
    reason = escalation_reason(target_root)
    assert STALE_ARTIFACT in reason
    assert "previous attempt" in reason
    assert reason in summary_of(target_root)


def test_a_genuinely_absent_artifact_still_escalates_for_being_missing(
    deleted_retry,
):
    code, _, target_root = deleted_retry
    assert code == 2
    reason = escalation_reason(target_root)
    assert STALE_ARTIFACT in reason
    assert "did not produce required artifacts" in reason
    assert reason in summary_of(target_root)


def test_the_two_reasons_are_distinguishable_in_both_renderings(
    stale_retry, deleted_retry,
):
    """Asserted on both reasons rather than on the presence of one: they name
    the same artifact, after the same stage, on the same attempt, so what a
    reader has to go on is what they say about it."""
    _, _, stale_root = stale_retry
    _, _, missing_root = deleted_retry
    stale = escalation_reason(stale_root)
    missing = escalation_reason(missing_root)

    assert stale != missing
    assert "previous attempt" in stale
    assert "previous attempt" not in missing
    assert "did not produce" in missing
    assert "did not produce" not in stale

    assert stale in summary_of(stale_root)
    assert missing not in summary_of(stale_root)
    assert missing in summary_of(missing_root)
    assert stale not in summary_of(missing_root)


# --------------------------------------------------------------------------
# The escalation consumes no attempt number
# --------------------------------------------------------------------------


def test_the_stale_escalation_leaves_the_attempt_bookkeeping_exactly_as_it_was(
    stale_retry,
):
    """retry_count, the rendered prompt filenames and the attempts/attempt-N/
    directories, compared between the entry to the stage that escalated and
    the state the run was left in."""
    _, runner, target_root = stale_retry
    stage, at_entry = runner.seen[-1]
    assert stage == RETRY_STAGE
    assert bookkeeping_of(run_dir_of(target_root)) == at_entry


def test_the_same_comparison_reports_the_reroute_that_does_consume_one(
    stale_retry,
):
    """The control for the assertion above. The verifier reroute earlier in
    the very same run moves all three, so a comparison that had stopped being
    able to see a difference would fail here."""
    _, runner, _ = stale_retry
    before_the_reroute = next(b for stage, b in runner.seen if stage == VERIFIER)
    after_the_reroute = runner.seen[-1][1]
    assert after_the_reroute.retry_count != before_the_reroute.retry_count
    assert after_the_reroute.prompts != before_the_reroute.prompts
    assert after_the_reroute.attempts != before_the_reroute.attempts


def test_the_escalation_leaves_no_evidence_naming_an_attempt_that_never_ran(
    stale_retry,
):
    """The reading of the same fact a developer takes from the directory: two
    attempts were prompted, one was archived, and nothing is numbered 3."""
    _, _, target_root = stale_retry
    run_dir = run_dir_of(target_root)
    assert sorted(p.name for p in (run_dir / "attempts").glob("*")) == ["attempt-1"]
    assert sorted(p.name for p in run_dir.glob(f"prompt-{RETRY_STAGE}-*.md")) == [
        f"prompt-{RETRY_STAGE}-attempt-1.md",
        f"prompt-{RETRY_STAGE}-attempt-2.md",
    ]


# --------------------------------------------------------------------------
# A first attempt, and a rewrite with identical content
# --------------------------------------------------------------------------


def test_a_first_attempt_is_not_flagged(make_target, harness_root):
    """The common path, exercised rather than argued: every artifact this run
    writes was absent when its stage started, and nothing escalates."""
    target_root = make_target("first-attempt-target")
    code, runner = drive(target_root, harness_root, verdicts=[PASS])
    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert state_of(target_root)["status"] == "completed"


def test_a_second_attempt_over_the_same_artifact_is_flagged(stale_retry):
    """The control for the claim above: absence before the stage is what makes
    a first attempt safe, so the same stage skipping the same write when the
    artifact does exist beforehand has to be caught."""
    code, _, _ = stale_retry
    assert code == 2


def test_an_artifact_absent_before_the_stage_has_no_signature_entry(tmp_path):
    """Read directly, because it is the whole reason a first attempt is safe."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    before = story_coordinator.artifact_signatures(run_dir, [STALE_ARTIFACT])
    assert before == {}
    write(run_dir / STALE_ARTIFACT, "written by this attempt\n")
    assert story_coordinator.stale_artifacts(
        run_dir, [STALE_ARTIFACT], before) == []

    # The control: the same artifact, present and untouched across the same
    # comparison, which the same call does report.
    present = story_coordinator.artifact_signatures(run_dir, [STALE_ARTIFACT])
    assert story_coordinator.stale_artifacts(
        run_dir, [STALE_ARTIFACT], present) == [STALE_ARTIFACT]


@pytest.fixture
def identical_rewrite(make_target, harness_root):
    target_root = make_target("rewrite-target")
    code, runner = drive(target_root, harness_root,
                         {RETRY_STAGE: [{}, {STALE_ARTIFACT: SAME}]})
    return code, runner, target_root


def test_a_byte_identical_rewrite_is_not_flagged(identical_rewrite):
    """The false-positive case a reader raises as "the stage re-ran and its
    output was the same"."""
    code, _, target_root = identical_rewrite
    assert code == 0
    assert state_of(target_root)["status"] == "completed"


def test_the_rewrite_really_was_byte_identical(identical_rewrite, stale_retry):
    """Otherwise the test above would be about some other write. The content
    the rewriting run left is the content the skipping run left, so the two
    runs differ only in whether those bytes were written again."""
    _, _, rewritten = identical_rewrite
    _, _, skipped = stale_retry
    assert (run_dir_of(rewritten) / STALE_ARTIFACT).read_bytes() == (
        run_dir_of(skipped) / STALE_ARTIFACT).read_bytes()


def test_an_identical_rewrite_moves_the_modification_time_and_not_the_size(
    tmp_path,
):
    """What the signature actually rests on, checked rather than assumed."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    path = run_dir / STALE_ARTIFACT
    write(path, "identical\n")
    before = story_coordinator.artifact_signatures(run_dir, [STALE_ARTIFACT])
    write(path, "identical\n")
    assert story_coordinator.stale_artifacts(
        run_dir, [STALE_ARTIFACT], before) == []
    after = story_coordinator.artifact_signatures(run_dir, [STALE_ARTIFACT])
    assert after[STALE_ARTIFACT][1] == before[STALE_ARTIFACT][1]
    assert after[STALE_ARTIFACT][0] != before[STALE_ARTIFACT][0]


# --------------------------------------------------------------------------
# Every stage's own artifacts, read off that stage's own declaration
# --------------------------------------------------------------------------


RERUN_CASES = [
    (declaration["name"], artifact)
    for declaration in RERUN_STAGE_DECLARATIONS
    for artifact in story_coordinator.required_artifacts(declaration)
]


@pytest.mark.parametrize("stage,artifact", RERUN_CASES)
def test_each_stage_is_held_to_its_own_declared_outputs(
    make_target, harness_root, stage, artifact,
):
    target_root = make_target(f"skip-{stage}-{artifact}")
    code, _ = drive(target_root, harness_root, {stage: [{}, {artifact: SKIP}]})
    assert code == 2
    reason = escalation_reason(target_root)
    assert reason.startswith(f"{stage} left required artifacts unwritten")
    assert artifact in reason
    assert state_of(target_root)["current_stage"] == stage


@pytest.mark.parametrize("stage,artifact", RERUN_CASES)
def test_the_same_stage_writing_that_artifact_completes(
    make_target, harness_root, stage, artifact,
):
    """The control, one per case above: the same stage, the same artifact, the
    same second pass — writing it."""
    target_root = make_target(f"write-{stage}-{artifact}")
    code, _ = drive(target_root, harness_root, {stage: [{}, {artifact: FRESH}]})
    assert code == 0
    assert state_of(target_root)["status"] == "completed"


# --------------------------------------------------------------------------
# A workflow this repository does not ship
# --------------------------------------------------------------------------


PROBE_OUTPUT = "design-notes.md"


@pytest.fixture
def probe_harness(tmp_path: Path):
    """A harness root whose retry stage declares an output the shipped
    workflow does not."""
    root = tmp_path / "probe-harness"
    root.mkdir()
    for directory in ("prompts", "rules", "schemas"):
        shutil.copytree(REPO_ROOT / directory, root / directory)
    workflow = json.loads(json.dumps(WORKFLOW))
    workflow["name"] = "freshness-probe-workflow"
    for stage in workflow["stages"]:
        if stage["name"] == RETRY_STAGE:
            stage["outputs"] = [*stage["outputs"], PROBE_OUTPUT]
    (root / "workflows").mkdir()
    write_json(root / "workflows" / f"{workflow['name']}.json", workflow)
    return root, workflow


def use_probe_workflow(target_root: Path, workflow: dict) -> None:
    config = target_root / ".harness" / "config.yaml"
    write(config, config.read_text(encoding="utf-8").replace(
        "workflow: story-workflow", f"workflow: {workflow['name']}"))


def test_an_output_no_orchestration_code_knows_about_is_covered(
    make_target, probe_harness,
):
    harness, workflow = probe_harness
    target_root = make_target("probe-skip-target")
    use_probe_workflow(target_root, workflow)
    assert PROBE_OUTPUT not in COORDINATOR_SOURCE

    code, _ = drive(target_root, harness, {RETRY_STAGE: [{}, {PROBE_OUTPUT: SKIP}]},
                    workflow=workflow)
    assert code == 2
    reason = escalation_reason(target_root)
    assert PROBE_OUTPUT in reason
    assert "previous attempt" in reason


def test_the_same_probe_run_writing_that_output_completes(
    make_target, probe_harness,
):
    """The control: the coverage came off the declaration, not from a run
    under an unshipped workflow being unable to finish at all."""
    harness, workflow = probe_harness
    target_root = make_target("probe-write-target")
    use_probe_workflow(target_root, workflow)
    code, _ = drive(target_root, harness, WRITES_ON_THE_RETRY, workflow=workflow)
    assert code == 0
    assert state_of(target_root)["status"] == "completed"
    assert (run_dir_of(target_root) / PROBE_OUTPUT).is_file()


# --------------------------------------------------------------------------
# One snapshot, one reader, and no name written in the code
# --------------------------------------------------------------------------


def _function(source: str, name: str) -> ast.FunctionDef:
    return next(node for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == name)


def snapshots_in_run_story(source: str) -> list[str]:
    """The names assigned a fresh signature snapshot inside run_story."""
    assigned = []
    for node in ast.walk(_function(source, "run_story")):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", None) == "artifact_signatures"):
            assigned += [t.id for t in node.targets if isinstance(t, ast.Name)]
    return assigned


def comparisons_in_run_story(source: str) -> list[tuple]:
    """(comparison function, the name passed as its `before` argument) for
    every before/after comparison run_story makes."""
    calls = []
    for node in ast.walk(_function(source, "run_story")):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) in {"artifacts_written_since",
                                                       "stale_artifacts"}):
            before = node.args[-1]
            calls.append((node.func.id, getattr(before, "id", ast.dump(before))))
    return calls


def test_exactly_one_snapshot_is_taken_per_stage_invocation():
    assert snapshots_in_run_story(COORDINATOR_SOURCE) == ["artifacts_before"]


def test_every_comparison_reads_that_one_snapshot():
    calls = comparisons_in_run_story(COORDINATOR_SOURCE)
    assert {name for _, name in calls} == {"artifacts_before"}
    assert [function for function, _ in calls].count("stale_artifacts") == 1


#: The call site both scans above are anchored on, and the one the plant below
#: replaces. Written once so a scan and its control cannot drift apart.
STALE_CALL = "        stale = stale_artifacts(run_dir, required, artifacts_before)"


def test_the_scans_above_report_a_second_snapshot_that_was_planted():
    """The control for both: a second snapshot, and a comparison reading it,
    planted into a copy of the source — which the same two scans do report."""
    planted = COORDINATOR_SOURCE.replace(
        STALE_CALL,
        "        second_before = artifact_signatures(run_dir, required)\n"
        "        stale = stale_artifacts(run_dir, required, second_before)",
        1,
    )
    assert planted != COORDINATOR_SOURCE
    assert snapshots_in_run_story(planted) == ["artifacts_before", "second_before"]
    assert {name for _, name in comparisons_in_run_story(planted)} == {
        "artifacts_before", "second_before"}


def declared_names_in(source: str) -> list[str]:
    """Which stage and artifact names the loaded workflow declares appear as
    string literals in the given source. Comments and docstrings are outside
    the scan by construction: prose may name what code may not."""
    names = set(STAGE_NAMES)
    for stage in WORKFLOW["stages"]:
        names |= set(story_coordinator.required_artifacts(stage))
        names |= set(story_coordinator.conditional_artifacts(stage))
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # a bare string expression is a docstring
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found |= {name for name in names if name in node.value}
    return sorted(found)


def _wrapped(fragment: str) -> str:
    """A source fragment lifted out of run_story, made parseable on its own."""
    return "def _lifted():\n" + textwrap.indent(textwrap.dedent(fragment), "    ")


def check_source() -> str:
    """The freshness check as it is written: the function that reads the
    required set, the function that compares it, and the block in run_story
    that acts on the result."""
    functions = [
        ast.get_source_segment(COORDINATOR_SOURCE, _function(COORDINATOR_SOURCE, name))
        for name in ("required_artifacts", "stale_artifacts")
    ]
    block = STALE_CALL + COORDINATOR_SOURCE.split(STALE_CALL, 1)[1].split(
        "        violation = _schema_violation", 1)[0]
    return "\n".join(functions) + "\n" + _wrapped(block)


def test_no_stage_name_and_no_artifact_name_appears_in_the_check():
    assert declared_names_in(check_source()) == []


def test_the_name_scan_reports_a_name_that_was_planted():
    """The control: the same scan over the same code with one artifact name
    written into it, which it does report."""
    planted = check_source().replace(
        'names = set(stage.get("outputs", []))',
        'names = set(stage.get("outputs", [])) | {"test-results.json"}', 1)
    assert planted != check_source()
    assert declared_names_in(planted) == ["test-results.json"]


def test_the_name_scan_looks_at_the_block_and_not_only_the_functions():
    """Otherwise the assertion above would be about two functions that happen
    to be short. The plant goes into the lifted block."""
    planted = check_source().replace(
        "stale = stale_artifacts(run_dir, required, artifacts_before)",
        'stale = stale_artifacts(run_dir, ["test-results.json"], artifacts_before)',
        1)
    assert planted != check_source()
    assert declared_names_in(planted) == ["test-results.json"]


def test_a_run_takes_one_snapshot_per_stage_invocation(make_target, harness_root):
    """The same claim as a fact about a run rather than about its source. The
    caller frame separates the snapshot run_story takes from the reads the
    comparison helpers make of the current state."""
    target_root = make_target("counting-target")
    original = story_coordinator.artifact_signatures
    callers: list[str] = []

    def counting(run_dir, artifacts):
        callers.append(inspect.currentframe().f_back.f_code.co_name)
        return original(run_dir, artifacts)

    story_coordinator.artifact_signatures = counting
    try:
        code, runner = drive(target_root, harness_root)
    finally:
        story_coordinator.artifact_signatures = original

    assert code == 0
    assert callers.count("run_story") == len(runner.calls)
    # The control for that count: the helpers' own reads are in the same
    # record, so a wrapper that had stopped seeing calls, or that attributed
    # them all to one caller, would not look like this.
    assert set(callers) == {"run_story", "stale_artifacts", "artifacts_written_since"}


# --------------------------------------------------------------------------
# Conditional artifacts, held by the assertions story-012 already wrote
# --------------------------------------------------------------------------


@pytest.fixture
def story_012_retries_exhausted(make_target, harness_root):
    """story-012's own fixture value, rebuilt with story-012's own runner."""
    target_root = make_target("story-012-target")
    runner = story012.RetryRunner(
        target_root,
        [story012.failing_verdict(1), story012.failing_verdict(2),
         story012.failing_verdict(3)],
    )
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target_root, runner) == 2
    return runner, story012.run_dir_of(target_root)


@pytest.mark.parametrize("assertion", [
    story012.test_two_retries_then_an_escalation_produce_exactly_two_entries,
    story012.test_each_entry_carries_the_guidance_written_for_the_following_attempt,
    story012.test_the_entries_carry_different_attempts_findings,
    story012.test_the_archive_an_entry_names_holds_that_attempts_own_artifacts,
    story012.test_each_entry_names_an_archive_directory_that_exists_in_the_run,
], ids=lambda assertion: assertion.__name__)
def test_story_012s_conditional_artifact_assertions_still_hold(
    story_012_retries_exhausted, assertion,
):
    """Re-run unchanged against this implementation: the guidance field, and
    the artifacts a retry record names, are what story-012 says they are."""
    assertion(story_012_retries_exhausted)


def test_the_guidance_is_still_outside_the_set_this_check_governs():
    """The widened snapshot did not move the conditional artifact into the
    required set, which is the one way this story could have changed how it is
    handled. Read off the declarations, for every stage."""
    for stage in WORKFLOW["stages"]:
        conditional = set(story_coordinator.conditional_artifacts(stage))
        required = set(story_coordinator.required_artifacts(stage))
        assert not conditional & required
    assert story_coordinator.conditional_artifacts(VERIFIER_STAGE) == [
        "retry-guidance.json"]
    assert "retry-guidance.json" not in story_coordinator.required_artifacts(
        VERIFIER_STAGE)


def test_a_verifier_that_leaves_its_guidance_alone_is_not_flagged(
    make_target, harness_root,
):
    """The behaviour behind that: the verifier's second pass writes its
    required output and leaves the guidance exactly where the first pass left
    it — untouched across a stage invocation, which for a required artifact is
    the escalating case the parametrized tests above show."""
    target_root = make_target("guidance-target")
    runner = story012.RetryRunner(
        target_root, [story012.failing_verdict(1), story012.PASS])
    guidance = story012.run_dir_of(target_root) / "retry-guidance.json"

    assert story_coordinator.run_story(
        STORY_ID, harness_root, target_root, runner) == 0
    assert guidance.is_file()
    assert runner.calls == STAGE_NAMES[:VERIFIER_INDEX + 1] + STAGE_NAMES[RETRY_INDEX:]


# --------------------------------------------------------------------------
# A run in which every stage writes its outputs
# --------------------------------------------------------------------------


def test_a_run_whose_stages_all_write_is_unaffected(fresh_retry, stale_retry):
    """Same artifacts, same events, same routing, same commit — each stated
    beside the skipping run, which differs in all four."""
    _, fresh_runner, fresh_root = fresh_retry
    _, _, stale_root = stale_retry

    # Routing: attempt 1 through the verifier, then every stage again.
    assert fresh_runner.calls == (
        STAGE_NAMES[:VERIFIER_INDEX + 1] + STAGE_NAMES[RETRY_INDEX:])

    # Events: the escalation the other run raised, and nothing like it here.
    assert "escalated" in event_kinds(stale_root)
    assert "escalated" not in event_kinds(fresh_root)
    assert not any("previous attempt" in message for message in messages(fresh_root))
    assert any("previous attempt" in message for message in messages(stale_root))

    # Artifacts: every declared output present, and no escalation summary.
    for stage in WORKFLOW["stages"]:
        for artifact in story_coordinator.required_artifacts(stage):
            assert (run_dir_of(fresh_root) / artifact).is_file()
    assert not (run_dir_of(fresh_root) / "escalation-summary.md").exists()
    assert (run_dir_of(stale_root) / "escalation-summary.md").is_file()

    # The commit: a completion's, not an escalation's.
    marker = story_coordinator.ESCALATION_COMMIT_MARKER
    assert subject_of(fresh_root) != subject_of(stale_root)
    assert marker not in subject_of(fresh_root)
    assert marker in subject_of(stale_root)


def subject_of(target_root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(target_root), "log", "-1", "--format=%s"],
        capture_output=True, text=True, check=True).stdout.strip()
