"""Independent validation for story-028: routing a retry to the stage that
owns the defect.

The subject is a *decision the coordinator makes at runtime*, so almost
nothing here is asserted from source. A target repository is built under
tmp_path, fake stage agents drive it into each shape the story names, and
what the coordinator actually wrote — the execution history, the retry
history, the escalation summary, the rendered prompts, the run directory
itself — is read back. Where a shape needs a workflow this repository does
not ship (a route to a stage that does not exist, a route that points
forward, a third category), a harness root carrying that workflow is built
beside it and the run is pointed at that.

No category name and no destination stage is written in this file either.
Both come off the loaded workflow definition, for the same reason the
coordinator may not spell them: a test that hard-codes `implementation ->
implementer` passes just as happily against a coordinator that routes
everything to a constant.

Every absence asserted here carries a demonstration that the same check
reports the violation it exists to catch. The absences are the dangerous
half of this story — "no attempt directory was written", "no run directory
exists", "no category name appears in orchestration code", "the ceiling is
defined nowhere else" all pass identically when the check is looking in the
wrong place — so each is paired with the violation constructed against the
same subject and the same check:

  * "a malformed verdict writes no attempts/attempt-N/ and leaves
    retry_count alone" sits beside the routed retry that writes one and
    increments it, and beside a coordinator with those two escalations
    disabled, which writes the directory;
  * "a refused workflow creates no run directory, no state, no log and no
    branch" sits beside the same run under the unmutated workflow, which
    creates all four, and beside a coordinator whose pre-flight is disabled,
    which lets the bad workflow through;
  * "the rendered prompt carries no unresolved placeholder" sits beside the
    template it was rendered from, which does carry them, checked by the
    same regex;
  * "the retry ceiling is defined exactly once in the repository" sits
    beside a copy of the very files it searched with a second definition
    reintroduced, where the same search reports two;
  * "no category name and no routing destination appears in the two
    orchestration modules" sits beside the same module's source with one
    written back in, where the same check reports it;
  * "no model is invoked" is enforced for every test in this file by a guard
    over the one call that would invoke one, and the guard is shown to fire.

Nothing here invokes a model: every run goes through a fake agent runner,
and `no_model` below turns the single subprocess call that would reach one
into a failure.
"""
import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import agent_runner
import harness_config
import schema_validator
import story_coordinator
from agent_runner import AgentResult
from conftest import load_mutant

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
COORDINATOR_PATH = REPO_ROOT / "orchestration" / "story_coordinator.py"
ASSEMBLER_PATH = REPO_ROOT / "orchestration" / "context_assembler.py"

WORKFLOW = harness_config.load_workflow(REPO_ROOT, "story-workflow")
STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]

#: The stage that declares the routing table, found by the declaration rather
#: than by name, so this file names no stage the definition does not.
VERIFIER_STAGE = next(s for s in WORKFLOW["stages"] if "on_failure" in s)
VERIFIER_NAME = VERIFIER_STAGE["name"]
ROUTES = VERIFIER_STAGE["on_failure"]["retry_routing"]
CATEGORIES = sorted(ROUTES)
DESTINATIONS = {category: ROUTES[category]["stage"] for category in CATEGORIES}

#: The clean-clone declaration names both of its artifacts: the result it
#: writes and the stage a failure routes to.
CLEAN_CLONE = VERIFIER_STAGE["clean_clone"]
CLEAN_CLONE_RESULT = CLEAN_CLONE["result"]
CLEAN_CLONE_STAGE = CLEAN_CLONE["retry_stage"]

RULES_PATH = REPO_ROOT / "rules" / "execution-rules.json"
MAX_RETRIES = json.loads(RULES_PATH.read_text(encoding="utf-8"))["max_retries"]

STORY_ID = "story-001"
DEFAULT_BRANCH = "main"

#: An unrecognised category and an undefined stage, built so they cannot
#: collide with anything the workflow declares.
UNKNOWN_CATEGORY = "not-a-" + "-or-".join(CATEGORIES)
UNDEFINED_STAGE = "not-a-stage-" + "-or-".join(STAGE_NAMES)

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}

#: Distinguishes "the guidance this run wrote reached the retried stage" from
#: "some file with the right name was injected".
GUIDANCE_MARK = "the assertion added on attempt one cannot fail"

GUIDANCE = {
    "current_focus": [GUIDANCE_MARK],
    "preserve_behavior": ["the sample behavior"],
    "retry_scope": ["tests/test_sample.py"],
}

#: Sentinel: a verdict that omits retry_target entirely, which is a different
#: shape from one that names a category nothing defines.
OMITTED = object()


def failing(target=OMITTED, *, retry: bool = True) -> dict:
    verdict = {
        "status": "failed",
        "blocking_issues": [{
            "severity": "high",
            "issue": "the sample behavior is not implemented",
            "location": "src/app.py:1",
            "required_behavior": "the sample behavior exists",
        }],
        "unverified": [],
        "retry_recommended": retry,
    }
    if target is not OMITTED:
        verdict["retry_target"] = target
    return verdict


STORY = f"""\
story:
  id: {STORY_ID}
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
workflow: {workflow}
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: {test_command}
"""


# --------------------------------------------------------------------------
# No model, for every test in this file
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    """Turn the one call that would reach a model into a failure.

    `agent_runner.run_agent` is the only place in the harness that spawns
    the CLI, and `subprocess.Popen` is the only call it makes. The guard
    wraps `Popen` rather than replacing it, because `subprocess.run` — which
    every git call below goes through — is built on it; what it refuses is
    the one command that reaches a model. Every run below passes a fake
    runner explicitly, so this should never fire; it exists so "no model was
    invoked" is enforced rather than assumed, and
    `test_the_no_model_guard_fires_when_a_model_is_invoked` shows it can.
    """
    real = agent_runner.subprocess.Popen

    def guarded(command, *args, **kwargs):
        first = command[0] if isinstance(command, (list, tuple)) else command
        if str(first).endswith("claude"):
            raise AssertionError("a model was invoked")
        return real(command, *args, **kwargs)

    monkeypatch.setattr(agent_runner.subprocess, "Popen", guarded)


def test_the_no_model_guard_fires_when_a_model_is_invoked(tmp_path):
    """The control for the guard every other test in this file runs under."""
    with pytest.raises(AssertionError, match="a model was invoked"):
        agent_runner.run_agent("prompt", stage="implementer", cwd=tmp_path,
                               log_path=tmp_path / "agent.log")


# --------------------------------------------------------------------------
# A target repository, a harness root, and a fake runner
# --------------------------------------------------------------------------


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=check)


def build_target(root: Path, *, workflow: str = "story-workflow",
                 test_command: str = "echo tests-ok") -> Path:
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml",
          CONFIG.format(workflow=workflow, test_command=test_command))
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "src" / "app.py", "print('hello')\n")
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    git(root, "branch", "-M", DEFAULT_BRANCH)
    return root


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return build_target(tmp_path / "routing-target")


@pytest.fixture
def harness_root() -> Path:
    return REPO_ROOT


def probe_harness(tmp_path: Path, name: str, mutate) -> Path:
    """A harness root carrying a workflow this repository does not ship.

    Everything but the workflow is the shipped harness — the same prompts,
    the same schemas, the same rules — so a run against it differs from a
    run against this repository in exactly the definition under test.
    """
    root = tmp_path / name
    root.mkdir()
    for directory in ("prompts", "rules", "schemas"):
        shutil.copytree(REPO_ROOT / directory, root / directory)
    workflow = json.loads(json.dumps(WORKFLOW))
    workflow["name"] = name
    mutate(workflow)
    (root / "workflows").mkdir()
    write_json(root / "workflows" / f"{name}.json", workflow)
    return root


def verifier_stage_of(workflow: dict) -> dict:
    return next(s for s in workflow["stages"] if s["name"] == VERIFIER_NAME)


class Runner:
    """A fake agent runner: each stage writes the artifacts it declares.

    The verdicts are consumed one per verifier call, the last repeating, so
    a run is driven into a shape by listing what the verifier says.
    """

    def __init__(self, target_root: Path, verdicts: list | None = None):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.verdicts = verdicts or [PASS]
        self.calls: list[str] = []
        self.prompts: list[tuple[str, str]] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None):
        self.calls.append(stage)
        self.prompts.append((stage, prompt))
        attempt = self.calls.count(stage)

        if stage == "implementer":
            write(self.target_root / "src" / "app.py",
                  f"print('attempt {attempt}')\n")
            write_json(self.run_dir / "changed-files.json",
                       {"modified": ["src/app.py"], "created": [], "deleted": []})
            write(self.run_dir / "implementation-summary.md",
                  f"Implemented on attempt {attempt}.\n")
        elif stage == "tester":
            write_json(self.run_dir / "test-results.json", {
                "status": "passed", "tests_written": 1, "tests_run": 1,
                "tests_passed": 1, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / "tester-changed-files.json",
                       {"modified": [], "created": [], "deleted": []})
        elif stage == VERIFIER_NAME:
            seen = self.calls.count(stage) - 1
            verdict = self.verdicts[min(seen, len(self.verdicts) - 1)]
            write_json(self.run_dir / "verification-result.json", verdict)
            if verdict.get("retry_recommended"):
                write_json(self.run_dir / "retry-guidance.json", GUIDANCE)
        elif stage == "documenter":
            write(self.run_dir / "documentation-report.md", "Nothing.\n")
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path) -> Path:
    return target_root / ".harness" / "runs" / STORY_ID


def state_of(target_root: Path) -> dict:
    return json.loads(
        (run_dir_of(target_root) / "state.json").read_text(encoding="utf-8"))


def history_of(target_root: Path) -> list[dict]:
    return json.loads(
        (run_dir_of(target_root) / "execution-history.json").read_text(
            encoding="utf-8"))


def retry_records_of(target_root: Path) -> list[dict]:
    return json.loads(
        (run_dir_of(target_root) / "retry-history.json").read_text(
            encoding="utf-8"))


def summary_of(target_root: Path) -> str:
    return (run_dir_of(target_root) / "escalation-summary.md").read_text(
        encoding="utf-8")


def prompt_of(target_root: Path, stage: str, attempt: int) -> str:
    return (run_dir_of(target_root) / f"prompt-{stage}-attempt-{attempt}.md"
            ).read_text(encoding="utf-8")


def events(target_root: Path, kind: str) -> list[dict]:
    return [entry for entry in history_of(target_root)
            if entry["event"] == kind]


def routed_retries(target_root: Path) -> list[dict]:
    return [entry for entry in history_of(target_root)
            if entry.get("retry_decision") == "retry"]


def escalation_of(target_root: Path) -> dict:
    escalated = events(target_root, "escalated")
    assert len(escalated) == 1, escalated
    return escalated[0]


# --------------------------------------------------------------------------
# Routing: the category the verifier reports decides where the retry goes
# --------------------------------------------------------------------------


@pytest.mark.parametrize("category", CATEGORIES)
def test_a_failed_verification_routes_to_the_stage_its_category_names(
    target, harness_root, category,
):
    """Both criteria at once, one category per parametrization.

    The destination is read off the loaded workflow, and the assertion is
    made against what the run *recorded* rather than against the expression
    that computed it.
    """
    destination = DESTINATIONS[category]
    runner = Runner(target, [failing(category), PASS])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0

    first_verdict = runner.calls.index(VERIFIER_NAME)
    assert runner.calls[first_verdict + 1] == destination

    retries = routed_retries(target)
    assert [entry["retry_stage"] for entry in retries] == [destination]
    assert [entry["retry_category"] for entry in retries] == [category]


def test_the_two_categories_do_not_route_to_the_same_stage(tmp_path,
                                                           harness_root):
    """The control for the parametrization above.

    A coordinator that ignored the category entirely and routed everything to
    one stage would satisfy one of those two runs. It cannot satisfy both,
    and this states why: the destinations the two runs recorded differ, and
    they differ the way the workflow says they do.
    """
    recorded = {}
    for category in CATEGORIES:
        root = build_target(tmp_path / f"target-{category}")
        runner = Runner(root, [failing(category), PASS])
        assert story_coordinator.run_story(
            STORY_ID, harness_root, root, runner) == 0
        recorded[category] = routed_retries(root)[0]["retry_stage"]

    assert recorded == DESTINATIONS
    assert len(set(recorded.values())) == len(CATEGORIES)


def test_a_coordinator_that_routes_to_a_constant_is_caught(target, harness_root,
                                                           tmp_path):
    """The control for the two assertions above.

    The category lookup is replaced by the workflow's first stage — the
    constant route this story removed, written without naming it. The
    category whose destination is not that stage then routes to the wrong
    one, and both the run's own calls and the route it recorded say so.
    """
    category = next(c for c in CATEGORIES if DESTINATIONS[c] != STAGE_NAMES[0])
    module = load_mutant(
        COORDINATOR_PATH,
        [('destination = routes[target]["stage"]', "destination = stage_names[0]")],
        name="mutant_coordinator_with_a_constant_route", tmp_path=tmp_path)

    runner = Runner(target, [failing(category), PASS])
    assert module.run_story(STORY_ID, harness_root, target, runner) == 0

    first_verdict = runner.calls.index(VERIFIER_NAME)
    assert runner.calls[first_verdict + 1] == STAGE_NAMES[0]
    assert routed_retries(target)[0]["retry_stage"] != DESTINATIONS[category]


@pytest.mark.parametrize("category", CATEGORIES)
def test_retry_history_records_the_destination_the_run_actually_took(
    target, harness_root, category,
):
    """`retry-history.json` names the stage routed to, not a constant, and
    still satisfies the schema it satisfied before."""
    runner = Runner(target, [failing(category), PASS])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0

    records = retry_records_of(target)
    assert [record["retry_stage"] for record in records] == [DESTINATIONS[category]]
    schema = schema_validator.load_schema("retry-history")
    assert schema_validator.validate(records, schema) == []


def test_a_routed_retry_carries_its_category_and_destination_into_the_history(
    target, harness_root,
):
    category = CATEGORIES[0]
    runner = Runner(target, [failing(category), PASS])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0

    entry = routed_retries(target)[0]
    assert entry["retry_category"] == category
    assert entry["retry_stage"] == DESTINATIONS[category]
    assert entry["retry_decision"] == "retry"
    assert entry["retry_reason"]

    schema = schema_validator.load_schema("execution-history")
    assert schema_validator.validate(history_of(target), schema) == []


def test_the_log_and_the_history_remain_two_renderings_of_one_write(
    target, harness_root,
):
    """One `append_event` call per event, in both files, in one order.

    Stated as a count and a per-line correspondence rather than as an
    absence, so a second write path shows up as a mismatch rather than as a
    check with nothing to see.
    """
    runner = Runner(target, [failing(CATEGORIES[0]), PASS])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0

    lines = (run_dir_of(target) / "events.log").read_text(
        encoding="utf-8").splitlines()
    history = history_of(target)
    assert len(lines) == len(history)
    for line, entry in zip(lines, history):
        assert line.endswith(entry["message"])
    assert [entry["sequence"] for entry in history] == list(
        range(1, len(history) + 1))


# --------------------------------------------------------------------------
# A verdict that cannot be routed escalates, and spends nothing
# --------------------------------------------------------------------------


def attempt_dirs(target_root: Path) -> list[str]:
    attempts = run_dir_of(target_root) / "attempts"
    return sorted(p.name for p in attempts.iterdir()) if attempts.is_dir() else []


@pytest.mark.parametrize("verdict, description", [
    (failing(), "no retry_target at all"),
    (failing(UNKNOWN_CATEGORY), "a retry_target nothing defines"),
])
def test_a_verdict_that_cannot_be_routed_escalates_spending_nothing(
    target, harness_root, verdict, description,
):
    runner = Runner(target, [verdict])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 2, description

    assert state_of(target)["status"] == "escalated"
    assert state_of(target)["retry_count"] == 0
    assert attempt_dirs(target) == []
    assert routed_retries(target) == []
    assert not (run_dir_of(target) / "retry-history.json").exists()


def test_a_routed_retry_does_write_the_attempt_directory_and_spends_a_retry(
    target, harness_root,
):
    """The control for the two absences above.

    Same repository, same fake runner, same coordinator: the only difference
    is a verdict that *can* be routed. It increments `retry_count` and writes
    `attempts/attempt-1/`, so the two assertions above are looking at a run
    directory where those things do appear when they are supposed to.
    """
    runner = Runner(target, [failing(CATEGORIES[0]), PASS])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0

    assert state_of(target)["retry_count"] == 1
    assert attempt_dirs(target) == ["attempt-1"]
    assert len(retry_records_of(target)) == 1


@pytest.mark.parametrize("target_value", [OMITTED, UNKNOWN_CATEGORY])
def test_the_escalation_names_the_offending_value_and_every_category_defined(
    target, harness_root, target_value,
):
    """Actionable without opening the workflow definition.

    Read from what the escalation captured — the summary the coordinator
    wrote and the event it appended — rather than from the source that
    composed them.
    """
    runner = Runner(target, [failing(target_value)])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 2

    entry = escalation_of(target)
    summary = summary_of(target)
    for text in (entry["message"], summary):
        for category in CATEGORIES:
            assert category in text, (category, text)
        if target_value is not OMITTED:
            assert target_value in text
        else:
            assert "retry_target" in text


def test_a_verdict_that_cannot_be_routed_escalates_on_that_ground_at_the_ceiling(
    target, harness_root,
):
    """The bug, not the budget.

    The run is driven to the ceiling with routable verdicts first, so the
    ceiling escalation is genuinely available when the malformed verdict
    arrives; the reason recorded is the malformed verdict.
    """
    routable = failing(CATEGORIES[0])
    verdicts = [routable] * MAX_RETRIES + [failing(UNKNOWN_CATEGORY)]
    runner = Runner(target, verdicts)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 2

    state = state_of(target)
    assert state["retry_count"] == MAX_RETRIES
    entry = escalation_of(target)
    assert UNKNOWN_CATEGORY in entry["message"]
    assert entry.get("retry_category") == UNKNOWN_CATEGORY
    assert "ceiling" not in entry["message"]
    assert "ceiling" not in entry["retry_reason"]
    # The ceiling really was reachable on this run: one more routable verdict
    # in its place escalates on the budget instead.
    other = build_target(target.parent / "ceiling-control")
    assert story_coordinator.run_story(
        STORY_ID, harness_root, other, Runner(other, [routable])) == 2
    assert "ceiling" in escalation_of(other)["retry_reason"]


def test_the_escalations_are_what_stops_the_attempt_directory_being_written(
    target, harness_root, tmp_path,
):
    """The control for `attempt_dirs(target) == []` above.

    A coordinator with the two malformed-verdict escalations disabled falls
    through to the routing path, which archives the attempt before it looks
    the category up. So the directory the assertion says is absent is
    present the moment the behaviour is removed, and the lookup that has no
    category to make then fails outright.
    """
    module = load_mutant(
        COORDINATOR_PATH,
        [("elif verdict.get(\"retry_recommended\") and not target:",
          "elif False and verdict.get(\"retry_recommended\") and not target:"),
         ("elif verdict.get(\"retry_recommended\") and target not in routes:",
          "elif False and verdict.get(\"retry_recommended\") and target not in routes:")],
        name="mutant_coordinator_without_route_escalations", tmp_path=tmp_path)

    runner = Runner(target, [failing()])
    with pytest.raises(KeyError):
        module.run_story(STORY_ID, harness_root, target, runner)
    assert attempt_dirs(target) == ["attempt-1"]


# --------------------------------------------------------------------------
# Pre-flight: a route that cannot be followed refuses the run
# --------------------------------------------------------------------------


def created_nothing(target_root: Path) -> list[str]:
    """What a refused run must not have left behind, as a list of violations.

    A list rather than four assertions so the same statement can be made of
    the run that is *supposed* to create them, which is the control.
    """
    run_dir = run_dir_of(target_root)
    problems = []
    if run_dir.exists():
        problems.append(f"a run directory exists at {run_dir}")
    if (run_dir / "state.json").exists():
        problems.append("state.json was written")
    if (run_dir / "events.log").exists():
        problems.append("an event log was appended")
    branch = f"story/{STORY_ID}"
    if git(target_root, "branch", "--list", branch).stdout.strip():
        problems.append(f"branch {branch} was created")
    head = git(target_root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if head != DEFAULT_BRANCH:
        problems.append(f"the repository was left on {head}")
    return problems


def undefined_destination(workflow: dict) -> None:
    verifier_stage_of(workflow)["on_failure"]["retry_routing"][
        CATEGORIES[0]]["stage"] = UNDEFINED_STAGE


def forward_destination(workflow: dict) -> None:
    verifier_stage_of(workflow)["on_failure"]["retry_routing"][
        CATEGORIES[0]]["stage"] = STAGE_NAMES[-1]


def undefined_clean_clone_destination(workflow: dict) -> None:
    verifier_stage_of(workflow)["clean_clone"]["retry_stage"] = UNDEFINED_STAGE


def forward_clean_clone_destination(workflow: dict) -> None:
    verifier_stage_of(workflow)["clean_clone"]["retry_stage"] = STAGE_NAMES[-1]


@pytest.mark.parametrize("name, mutate, offender", [
    ("undefined-route", undefined_destination, CATEGORIES[0]),
    ("undefined-clean-clone", undefined_clean_clone_destination, None),
])
def test_a_route_to_a_stage_the_workflow_does_not_define_is_refused(
    tmp_path, capsys, name, mutate, offender,
):
    harness = probe_harness(tmp_path, name, mutate)
    root = build_target(tmp_path / f"target-{name}", workflow=name)
    runner = Runner(root)

    assert story_coordinator.run_story(STORY_ID, harness, root, runner) == 1

    message = capsys.readouterr().err
    assert UNDEFINED_STAGE in message
    if offender is not None:
        assert offender in message
    assert runner.calls == []
    assert created_nothing(root) == []


@pytest.mark.parametrize("name, mutate, offender", [
    ("forward-route", forward_destination, CATEGORIES[0]),
    ("forward-clean-clone", forward_clean_clone_destination, None),
])
def test_a_route_to_a_stage_at_or_after_the_declaring_one_is_refused(
    tmp_path, capsys, name, mutate, offender,
):
    harness = probe_harness(tmp_path, name, mutate)
    root = build_target(tmp_path / f"target-{name}", workflow=name)
    runner = Runner(root)

    assert story_coordinator.run_story(STORY_ID, harness, root, runner) == 1

    message = capsys.readouterr().err
    assert STAGE_NAMES[-1] in message
    assert "routing forward would skip verification" in message
    if offender is not None:
        assert offender in message
    assert runner.calls == []
    assert created_nothing(root) == []


def test_the_same_run_under_an_unmutated_workflow_creates_all_of_it(tmp_path):
    """The control for `created_nothing` above.

    Same harness-building code, same target-building code, same runner, with
    the routing table left alone: the run directory, the state file, the
    event log and the branch all appear. So the four absences above are
    statements about the refusal rather than about a check that never looked.
    """
    harness = probe_harness(tmp_path, "sound-routing", lambda workflow: None)
    root = build_target(tmp_path / "target-sound", workflow="sound-routing")
    runner = Runner(root, [PASS])

    assert story_coordinator.run_story(STORY_ID, harness, root, runner) == 0

    assert runner.calls
    assert created_nothing(root) == [
        f"a run directory exists at {run_dir_of(root)}",
        "state.json was written",
        "an event log was appended",
        f"branch story/{STORY_ID} was created",
        f"the repository was left on story/{STORY_ID}",
    ]


def test_the_pre_flight_is_what_refuses_the_bad_workflow(tmp_path):
    """The control for both refusals.

    With the pre-flight's answer replaced by "no problems", the same bad
    workflow is no longer refused and the run proceeds to create everything
    the refusal is asserted not to create.
    """
    module = load_mutant(
        COORDINATOR_PATH,
        [("routing_problems = retry_routing_problems(stages)",
          "routing_problems = []")],
        name="mutant_coordinator_without_routing_preflight", tmp_path=tmp_path)

    harness = probe_harness(tmp_path, "unchecked-route", undefined_destination)
    root = build_target(tmp_path / "target-unchecked", workflow="unchecked-route")
    runner = Runner(root, [PASS])

    assert module.run_story(STORY_ID, harness, root, runner) != 1
    assert runner.calls
    assert created_nothing(root) != []


def test_the_routing_check_reports_both_problems_against_data(harness_root):
    """The check itself, over stage lists that are not a real workflow.

    Stated here as well as through a run so the two problems are pinned to
    the two conditions rather than to whichever one a run happens to meet
    first, and so the sound table is shown to produce no problems at all.
    """
    stages = WORKFLOW["stages"]
    assert story_coordinator.retry_routing_problems(stages) == []

    bad = json.loads(json.dumps(WORKFLOW))
    undefined_destination(bad)
    assert len(story_coordinator.retry_routing_problems(bad["stages"])) == 1

    forward = json.loads(json.dumps(WORKFLOW))
    forward_destination(forward)
    problems = story_coordinator.retry_routing_problems(forward["stages"])
    assert len(problems) == 1
    assert "routing forward" in problems[0]


# --------------------------------------------------------------------------
# The clean-clone route comes off the widened declaration
# --------------------------------------------------------------------------


@pytest.fixture
def failing_clean_clone(tmp_path: Path):
    """A harness and a target whose suite passes for the stages and fails in
    the clone, by routing the clean-clone check at a stage that is not the
    one this repository's workflow declares.

    The destination is deliberately *different* from the shipped one, so a
    coordinator that routed clean-clone failures to a constant would send the
    run somewhere else and the assertion would see it.
    """
    other = next(name for name in STAGE_NAMES
                 if name != CLEAN_CLONE_STAGE
                 and STAGE_NAMES.index(name) < STAGE_NAMES.index(VERIFIER_NAME))

    def mutate(workflow: dict) -> None:
        verifier_stage_of(workflow)["clean_clone"]["retry_stage"] = other

    harness = probe_harness(tmp_path, "clean-clone-route", mutate)
    root = build_target(tmp_path / "target-clean-clone",
                        workflow="clean-clone-route",
                        test_command="sh -c 'exit 1'")
    return harness, root, other


def test_a_clean_clone_failure_routes_to_the_stage_its_declaration_names(
    failing_clean_clone,
):
    harness, root, destination = failing_clean_clone
    assert destination != CLEAN_CLONE_STAGE, "the probe must differ from the default"
    runner = Runner(root, [PASS])

    # The check fails on every attempt, so the run spends its retries and
    # escalates on the ceiling — the behaviour it has today, unchanged.
    assert story_coordinator.run_story(STORY_ID, harness, root, runner) == 2

    retries = routed_retries(root)
    assert retries, "the clean-clone failure should have routed a retry"
    assert {entry["retry_stage"] for entry in retries} == {destination}
    assert [record["retry_stage"] for record in retry_records_of(root)] == \
        [destination] * MAX_RETRIES
    assert state_of(root)["retry_count"] == MAX_RETRIES
    assert attempt_dirs(root) == [f"attempt-{n}" for n in range(1, MAX_RETRIES + 1)]
    assert (run_dir_of(root) / CLEAN_CLONE_RESULT).is_file()


def test_removing_the_clean_clone_declaration_disables_the_check(tmp_path):
    """One key turns the whole check on, with no orchestration change.

    The target's test command fails, so a check that ran would reroute and
    eventually escalate. The run completes instead, writes no result
    artifact, and records no clean-clone event.
    """
    harness = probe_harness(
        tmp_path, "no-clean-clone",
        lambda workflow: verifier_stage_of(workflow).pop("clean_clone"))
    root = build_target(tmp_path / "target-no-clean-clone",
                        workflow="no-clean-clone", test_command="sh -c 'exit 1'")
    runner = Runner(root, [PASS])

    assert story_coordinator.run_story(STORY_ID, harness, root, runner) == 0
    assert "documenter" in runner.calls
    assert not (run_dir_of(root) / CLEAN_CLONE_RESULT).exists()
    assert not any(entry["event"].startswith("clean-clone")
                   for entry in history_of(root))


def test_the_result_artifact_name_comes_off_the_declaration_too(tmp_path):
    """The control for the absence above: with the declaration present under
    a name this repository does not ship, the check runs and writes *that*
    file."""
    renamed = "renamed-clean-clone-result.json"

    def mutate(workflow: dict) -> None:
        verifier_stage_of(workflow)["clean_clone"]["result"] = renamed

    harness = probe_harness(tmp_path, "renamed-clean-clone", mutate)
    root = build_target(tmp_path / "target-renamed", workflow="renamed-clean-clone")
    runner = Runner(root, [PASS])

    assert story_coordinator.run_story(STORY_ID, harness, root, runner) == 0
    assert (run_dir_of(root) / renamed).is_file()
    assert not (run_dir_of(root) / CLEAN_CLONE_RESULT).exists()
    assert any(entry["event"].startswith("clean-clone")
               for entry in history_of(root))


# --------------------------------------------------------------------------
# The rendered verifier prompt
# --------------------------------------------------------------------------


PLACEHOLDER = re.compile(r"\{\{[a-z_]+\}\}")


def test_the_rendered_verifier_prompt_names_every_category_it_may_choose(
    target, harness_root,
):
    runner = Runner(target, [PASS])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0

    prompt = prompt_of(target, VERIFIER_NAME, 1)
    for category, route in ROUTES.items():
        line = next((line for line in prompt.splitlines()
                     if category in line and route["stage"] in line), None)
        assert line is not None, (category, route["stage"])
        assert route["when"] in line


def test_the_rendered_verifier_prompt_carries_no_unresolved_placeholder(
    target, harness_root,
):
    runner = Runner(target, [PASS])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0

    assert PLACEHOLDER.search(prompt_of(target, VERIFIER_NAME, 1)) is None


def test_the_placeholder_check_sees_a_placeholder_when_there_is_one():
    """The control for the absence above, against the same regex and the
    template the prompt was rendered from — which does carry them."""
    template = (REPO_ROOT / "prompts" / f"{VERIFIER_NAME}.md").read_text(
        encoding="utf-8")
    assert PLACEHOLDER.search(template) is not None
    assert "{{retry_routes}}" in template


def test_a_third_category_changes_the_prompt_with_no_edit_to_the_template(
    tmp_path, harness_root, target,
):
    """The routes are injected, not restated.

    A workflow with a third category renders a verifier prompt naming it,
    while `prompts/verifier.md` is byte-identical in both harness roots.
    """
    added = "documentation"
    assert added not in ROUTES, "pick a category the shipped workflow lacks"
    when = "the defect is in the documentation this story was to leave behind"

    def mutate(workflow: dict) -> None:
        verifier_stage_of(workflow)["on_failure"]["retry_routing"][added] = {
            "stage": STAGE_NAMES[0],
            "when": when,
        }

    harness = probe_harness(tmp_path, "three-categories", mutate)
    root = build_target(tmp_path / "target-three", workflow="three-categories")

    assert story_coordinator.run_story(
        STORY_ID, harness, root, Runner(root, [PASS])) == 0
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, Runner(target, [PASS])) == 0

    template = (REPO_ROOT / "prompts" / f"{VERIFIER_NAME}.md").read_bytes()
    assert (harness / "prompts" / f"{VERIFIER_NAME}.md").read_bytes() == template

    with_three = prompt_of(root, VERIFIER_NAME, 1)
    with_two = prompt_of(target, VERIFIER_NAME, 1)
    assert any(added in line and STAGE_NAMES[0] in line and when in line
               for line in with_three.splitlines())
    assert added not in with_two
    assert with_three != with_two


# --------------------------------------------------------------------------
# A stage receiving a retry is told it is on one, and why
# --------------------------------------------------------------------------


@pytest.mark.parametrize("category", CATEGORIES)
def test_a_retried_stage_receives_the_guidance_and_the_retry_state(
    target, harness_root, category,
):
    """Holds for whichever stage the category routes to, which is the point:
    the tester had no retry placeholders at all before this story."""
    destination = DESTINATIONS[category]
    runner = Runner(target, [failing(category), PASS])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0

    prompt = prompt_of(target, destination, 2)
    assert GUIDANCE_MARK in prompt
    assert PLACEHOLDER.search(prompt) is None

    state = json.loads(
        re.search(r"\{[^{}]*\"retry_iteration\"[^{}]*\}", prompt, re.S).group(0))
    assert state["retry_iteration"] == 1
    assert state["max_retries"] == MAX_RETRIES
    assert state["retry_category"] == category
    assert state["retry_stage"] == destination


def test_the_first_attempt_is_told_it_is_not_on_a_retry(target, harness_root):
    """The control for the assertion above.

    The same placeholders in the same template render as the
    optional-placeholder convention on attempt one, so the retry state read
    above is something the retry put there rather than something every
    prompt carries.
    """
    runner = Runner(target, [failing(CATEGORIES[0]), PASS])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0

    first = prompt_of(target, DESTINATIONS[CATEGORIES[0]], 1)
    assert "retry_iteration" not in first
    assert "retry_category" not in first
    assert GUIDANCE_MARK not in first


def test_every_stage_that_can_receive_a_retry_declares_the_placeholders(
    harness_root,
):
    """Read off the workflow: whatever a route names must be able to say so."""
    for category, route in ROUTES.items():
        stage = next(s for s in WORKFLOW["stages"] if s["name"] == route["stage"])
        template = (REPO_ROOT / "prompts" / stage["prompt"]).read_text(
            encoding="utf-8")
        assert "{{retry_guidance}}" in template, category
        assert "{{retry_state}}" in template, category


# --------------------------------------------------------------------------
# The orchestration modules name no category and no destination
# --------------------------------------------------------------------------


#: Everything the workflow decides. `verifier` is deliberately not here: it
#: is the stage whose verdict the coordinator reads, not a place any retry is
#: routed to, and the coordinator has named it since long before this story.
FORBIDDEN = set(CATEGORIES) | set(DESTINATIONS.values()) | {CLEAN_CLONE_STAGE}


def hardcoded_names(source: str) -> set[str]:
    """Which forbidden names appear as string literals in this source.

    Literals only: a name inside a comment or a docstring is prose about the
    design, and prose cannot route anything. Comments are not in the tree at
    all, and a docstring is one long constant that never equals a bare stage
    name.
    """
    return {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and node.value in FORBIDDEN
    }


@pytest.mark.parametrize("path", [COORDINATOR_PATH, ASSEMBLER_PATH])
def test_no_category_and_no_destination_is_written_into_orchestration(path):
    assert hardcoded_names(path.read_text(encoding="utf-8")) == set()


@pytest.mark.parametrize("path, anchor", [
    (COORDINATOR_PATH, 'destination = routes[target]["stage"]'),
    (ASSEMBLER_PATH, 'route["stage"]'),
])
def test_the_hardcoded_name_check_reports_a_name_written_back_in(path, anchor):
    """The control for the absence above.

    The same check over the same module with its route lookup replaced by the
    destination it happens to produce today reports exactly that name — so a
    coordinator that stopped reading the workflow would not pass.
    """
    planted = DESTINATIONS[CATEGORIES[0]]
    source = path.read_text(encoding="utf-8")
    assert anchor in source, anchor
    mutated = source.replace(anchor, f'{anchor.split(" = ")[0]} = "{planted}"'
                             if " = " in anchor else f'"{planted}"', 1)
    assert hardcoded_names(mutated) == {planted}


def test_the_routes_the_coordinator_follows_come_off_the_loaded_workflow():
    """The positive half: the derivation returns what the definition says."""
    derived = {
        (route.category, route.stage)
        for route in story_coordinator.context_assembler.retry_routes(
            WORKFLOW["stages"])
    }
    assert derived == {(category, stage) for category, stage in DESTINATIONS.items()}


# --------------------------------------------------------------------------
# The retry ceiling is defined once, found by searching
# --------------------------------------------------------------------------


#: A *definition* of the ceiling: the key bound to a number. A read of it —
#: `rules["max_retries"]` — does not match, and neither does prose naming it.
CEILING_DEFINITION = re.compile(r'"max_retries"\s*:\s*[0-9]')

#: Searched: every tracked file the harness loads as code or configuration.
#: Excluded: the suite, whose fixtures legitimately build rules dictionaries
#: of their own, and the archived runs, which are frozen copies of what past
#: runs wrote rather than definitions anything reads.
SEARCHED_SUFFIXES = (".json", ".py")
EXCLUDED_PREFIXES = ("tests/", ".harness/runs-archive/")


def ceiling_definitions(root: Path) -> list[str]:
    """Where the retry ceiling is defined in a repository, by search.

    Takes a root rather than reading this one, so the identical search can be
    run against a repository in which a second definition exists — which is
    the only way "exactly one" is worth asserting.
    """
    listed = subprocess.run(["git", "-C", str(root), "ls-files"],
                            capture_output=True, text=True, check=True)
    found = []
    for relative in listed.stdout.splitlines():
        if not relative.endswith(SEARCHED_SUFFIXES):
            continue
        if relative.startswith(EXCLUDED_PREFIXES):
            continue
        path = root / relative
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if CEILING_DEFINITION.search(line):
                found.append(f"{relative}:{number}")
    return found


def test_the_retry_ceiling_is_defined_exactly_once_in_the_repository():
    found = ceiling_definitions(REPO_ROOT)
    assert len(found) == 1, found
    assert found[0].startswith(
        RULES_PATH.relative_to(REPO_ROOT).as_posix() + ":")


def scratch_repository(root: Path, sources: list[str]) -> Path:
    """A git repository holding copies of named files from this one."""
    root.mkdir(parents=True)
    for relative in sources:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / relative, destination)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "copy")
    return root


def test_the_single_definition_search_reports_a_second_one_reintroduced(tmp_path):
    """The control for the assertion above.

    Built out of the real files — the rules that hold the ceiling and the
    workflow that used to hold a second copy of it — so what is demonstrated
    is the search finding the duplicate this story removed, in the place it
    was removed from, rather than a duplicate invented somewhere convenient.
    """
    rules = RULES_PATH.relative_to(REPO_ROOT).as_posix()
    workflow = "workflows/story-workflow.json"
    root = scratch_repository(tmp_path / "scratch", [rules, workflow])

    assert len(ceiling_definitions(root)) == 1

    definition = json.loads((root / workflow).read_text(encoding="utf-8"))
    for stage in definition["stages"]:
        if "on_failure" in stage:
            stage["on_failure"]["max_retries"] = MAX_RETRIES
    write_json(root / workflow, definition)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "reintroduce the ceiling")

    reported = ceiling_definitions(root)
    assert len(reported) == 2, reported
    assert any(entry.startswith(workflow + ":") for entry in reported)


def test_the_workflow_declares_no_ceiling_of_its_own():
    """Stated directly as well as by search: no `on_failure` in the shipped
    workflow carries a ceiling, and none carries a default route either."""
    for stage in WORKFLOW["stages"]:
        on_failure = stage.get("on_failure", {})
        assert "max_retries" not in on_failure, stage["name"]
        assert "retry_stage" not in on_failure, stage["name"]


# --------------------------------------------------------------------------
# The schema additions
# --------------------------------------------------------------------------


def schema_of(stem: str) -> dict:
    return json.loads(
        (REPO_ROOT / "schemas" / f"{stem}.schema.json").read_text(encoding="utf-8"))


def test_retry_target_is_optional_and_says_why_in_the_schema_itself():
    schema = schema_of("verification-result")
    assert "retry_target" in schema["properties"]
    assert "retry_target" not in schema["required"]
    assert schema["properties"]["retry_target"]["description"].strip()


def test_a_verdict_naming_a_retry_target_still_satisfies_its_schema():
    """The control: the schema accepts the field rather than merely not
    mentioning it, and still rejects a verdict missing a required one."""
    schema = schema_validator.load_schema("verification-result")
    assert schema_validator.validate(failing(CATEGORIES[0]), schema) == []
    assert schema_validator.validate(PASS, schema) == []
    incomplete = {key: value for key, value in PASS.items() if key != "status"}
    assert schema_validator.validate(incomplete, schema) != []


def test_the_history_schema_can_describe_where_a_retry_went():
    properties = schema_of("execution-history")["items"]["properties"]
    for field in ("retry_category", "retry_stage", "retry_decision", "retry_reason"):
        assert field in properties, field
        assert properties[field]["description"].strip()


def test_no_schema_file_was_added_or_removed_for_this_story():
    """The manifest and the shipped schema set still agree.

    A positive statement of the same fact the constraint makes as an absence:
    every schema the manifest names is present and every present schema is
    named, so a file added or dropped shows up here as a mismatch.
    """
    manifest = json.loads(
        (REPO_ROOT / "schemas" / "manifest.json").read_text(encoding="utf-8"))
    shipped = sorted(path.name[: -len(".schema.json")]
                     for path in (REPO_ROOT / "schemas").glob("*.schema.json"))
    assert sorted(manifest["schemas"]) == shipped
    assert sorted(schema_validator.shipped_schemas()) == shipped
