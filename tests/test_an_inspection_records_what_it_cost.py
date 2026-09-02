"""story-101 validation: an inspection records what it cost.

The Inspector already ran under a cost ceiling and nothing accumulated what an
inspection actually spent, so there was no corpus to tune that ceiling against.
This story starts one: both modes append a line to the declared cross-run log
carrying what the inspection cost, which mode it was, how large its scope was
and how many findings came back and were filed, and a narrow-mode inspection
additionally appends its cost to that run's cost.json without charging it to
the allowance the run ceiling reads.

The subjects are kept apart deliberately:

  * **what each mode records.** Both modes are driven against a fake runner
    that reports a known figure, and the record each leaves is read back out of
    the log the cross-run history declaration routes this kind to — found by
    that declaration rather than by a filename written here — and held to the
    shape that declaration declares.

  * **the figure being carried rather than re-derived.** The fake runner writes
    an agent log carrying a *different* cost and reports its own, so a record
    carrying the reported figure is evidence that nothing read the log back.
    Beside it, a scan of both producing modules for a read of anything whose
    expression names a log, shown able to report one.

  * **the run's two records.** The entry a narrow-mode inspection appends to
    cost.json, beside the stage invocations and carrying the entry index the
    run records for the current entry, and the live allowance on state.json
    that is deliberately not moved by it.

  * **the ceiling that is not charged.** A run whose recorded spend sits at the
    edge of its declared ceiling completes and is inspected, with a control run
    whose *stage* spend crosses the same ceiling, which stops — so the edge run
    completing is the inspection not being charged rather than a ceiling that
    has stopped biting.

  * **absent, never zero.** An invocation reporting no cost leaves the field
    off the record and adds no cost entry, in both modes.

  * **the guarded writes.** The record write is made to raise for the
    inspection's own entry and for nothing else, in both modes, and neither
    mode may notice.

Every absence asserted here carries a demonstration that it can fail:

  * "a broad-mode record carries no story_id" sits beside a narrow-mode record
    read the same way out of the same log, which carries one;
  * "no cost_usd field where nothing was reported" sits beside the same
    inspection under a runner that reports one, where the field is there;
  * "no cost.json entry where nothing was reported" sits beside the same run
    under a reporting runner, which adds one;
  * "the edge run is not reported as over its ceiling" sits beside a control
    run whose stages alone cross it, which is stopped and says so;
  * "nothing reads an agent log back for the cost" sits beside planted sources
    that do, which the same scan reports;
  * "nothing assigns the inspection's cost to the live allowance" sits beside a
    planted assignment, which the same scan reports;
  * "the record commit staged nothing else" sits beside a file the fake
    inspector really did change, which must be left in the working tree;
  * "this story changed no ceiling declaration" sits beside a constructed story
    that did change one, which the same predicate reports.

Nothing here reaches a model: `agent_runner.run_agent` is replaced for every
test in this module by a fake that fails the test if it is called without
having been installed deliberately. The only history resolved out of this
repository's own commit graph is the one assertion whose subject *is* this
repository — that this story moved no ceiling declaration — and it goes
through the shared baseline resolution rather than through a second one.
"""
from __future__ import annotations

import ast
import json
import subprocess
from datetime import datetime
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
from agent_runner import AgentResult

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]

STORY_ID = "story-001"

PASSED = {"status": "passed", "blocking_issues": [], "unverified": [],
          "retry_recommended": False}


# ==========================================================================
# The workflow, the rules and the target
# ==========================================================================


#: The definition these runs execute. Built rather than resolved: whether an
#: inspection's cost is recorded is a property of the harness, and the workflow
#: a run walks is an input to it — reading the shipped one here would turn
#: granting a stage a budget into something this module reddens.
WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result"}),
    name="inspection-cost-workflow",
)

WRITING, VERIFYING = [stage["name"] for stage in WORKFLOW["stages"]]

#: The rule set these runs execute under: the fixture's own, so what a run does
#: with a blocked path is decided by a declaration this module wrote rather
#: than by the prefixes this repository happens to deploy.
FIXTURE_RULES = {
    "max_retries": 2,
    "require_verifier_pass": True,
    "blocked_paths": [".git/", ".harness/runs/", "rules/"],
}

SOURCE_DIR = "src/"
TESTS_DIR = "tests/"
CHANGED_SOURCE = f"{SOURCE_DIR}a.py"
SIBLING_SOURCE = f"{SOURCE_DIR}b.py"
A_TEST_FILE = f"{TESTS_DIR}t_a.py"

TRACKED = {
    CHANGED_SOURCE: "def a():\n    return 1\n",
    SIBLING_SOURCE: "def b():\n    return 2\n",
    A_TEST_FILE: "def check_a():\n    assert True\n",
}

#: Larger than the whole expansion, so no run below is silently trimmed.
ROOMY_CAP = 60

#: What the fake invocations report spending. Distinct figures, so that a
#: broad-mode inspection's total is a sum of what its invocations reported
#: rather than either one of them, and neither is a round number that could be
#: arrived at by accident.
FIRST_COST = 0.17
SECOND_COST = 0.26

#: What the fake writes into the agent log it is handed — a figure nothing may
#: record, so a record carrying it would say a log had been read back.
DECOY_COST = 99.99

SETUP_SUBJECT = "the tree this inspection starts from"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


@pytest.fixture
def harness(tmp_path) -> Path:
    """A harness root carrying the built workflow, the fixture rules and an
    Inspector template this module wrote."""
    root = conftest.materialize_workflow(
        WORKFLOW, tmp_path / "inspection-cost-harness", rules=FIXTURE_RULES)
    (root / "prompts" / inspection.INSPECTOR_PROMPT).write_text(
        "# a template this module wrote\n\n"
        "scope:\n{{scope}}\n\nscope_paths:\n{{scope_paths}}\n\n"
        "findings_path:\n{{findings_path}}\n",
        encoding="utf-8")
    return root


def build_target(root: Path, **config_keys) -> Path:
    """A target repository a run and an inspection can both execute in.

    The same shape `conftest.target_root` builds — its config and its story,
    read off conftest so neither is spelled twice — with the source layout
    above added and the run directory, the log directory and the queue ignored.

    The run directory is ignored for the reason this harness ignores its own:
    an inspection appends to that run's events.log *after* the completion
    commit, so a target tracking its run directory would be left holding a
    modified events.log by the story's own requirement that the record reach
    it. The history directory is deliberately *not* ignored, because the record
    being committed is one of the things this module is about.
    """
    for sub in (".harness/standards", ".harness/stories", ".harness/docs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    config = conftest.CONFIG.format(workflow=WORKFLOW["name"])
    config += f"source_dirs:\n  - {SOURCE_DIR}\n"
    config += "".join(f"{key}: {value}\n"
                      for key, value in sorted(config_keys.items()))
    (root / ".harness" / "config.yaml").write_text(config, encoding="utf-8")
    (root / ".harness" / "stories" / f"{STORY_ID}.yaml").write_text(
        conftest.STORY, encoding="utf-8")
    (root / ".harness" / "standards" / "coding.md").write_text(
        "# Coding Standards\n- keep it simple\n", encoding="utf-8")
    (root / ".harness" / "standards" / "testing.md").write_text(
        "# Testing Standards\n- test everything\n", encoding="utf-8")
    (root / ".harness" / "docs" / "ARCHITECTURE.md").write_text(
        "# Sample Architecture\n", encoding="utf-8")
    for relative, text in TRACKED.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    (root / ".gitignore").write_text(
        "".join(f"{one}\n" for one in
                (".harness/runs/", ".harness/logs/",
                 "/".join(outbox.QUEUE_DIR))),
        encoding="utf-8")

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", SETUP_SUBJECT)
    return root


@pytest.fixture
def target(tmp_path) -> Path:
    """A target whose runs inspect what they changed."""
    return build_target(tmp_path / "inspected",
                        **{story_inspection.MAX_FILES_KEY: ROOMY_CAP})


# ==========================================================================
# The fake inspector, installed in place of the agent runner
# ==========================================================================


def finding(ordinal: int = 1, **overrides) -> dict:
    """One conforming finding, naming the workflow this fixture defines."""
    schema = schema_validator.load_schema(inspection.BRIEF_SCHEMA)
    found = {
        "title": f"zzz: the {ordinal}th thing this change left behind",
        "slug": f"zzz-finding-{ordinal}",
        "body": f"{CHANGED_SOURCE}:1 disagrees with {SIBLING_SOURCE}:1",
        "category": schema["properties"]["category"]["enum"][0],
        "severity": min(schema["properties"]["severity"]["enum"]),
        "confidence": schema["properties"]["confidence"]["enum"][0],
        "effort": schema["properties"]["effort"]["enum"][0],
        "workflow": WORKFLOW["name"],
        "paths": [CHANGED_SOURCE],
    }
    found.update(overrides)
    return found


class Inspector:
    """Stands in for `agent_runner.run_agent` for either mode's invocation.

    It reaches no model. It records what it was handed, writes the findings the
    caller supplied, **writes an agent log carrying `DECOY_COST`**, and reports
    whatever cost the caller asked this invocation to report.

    The decoy is the point of the log: the figure it carries is one no record
    may hold, so a record carrying the reported figure instead is evidence that
    the cost was carried off the result rather than parsed back out of the
    harness's own output.

    `costs` is one figure per invocation, cycled, so a broad-mode inspection
    over two scopes reports two different figures and a total that is neither.
    A figure of None is an invocation that reported nothing at all.
    """

    def __init__(self, target: Path, config: dict, *, findings=(),
                 costs=(FIRST_COST,), act=None, raises: str = ""):
        self.target = Path(target)
        self.artifact = inspection.findings_paths(self.target, config)[0]
        self.findings = list(findings)
        self.costs = list(costs)
        self.act = act
        self.raises = raises
        self.invocations: list[dict] = []

    def cost_of(self, index: int):
        return self.costs[index % len(self.costs)]

    @property
    def reported(self) -> list:
        """What this fake actually reported, invocation by invocation."""
        return [one["cost_usd"] for one in self.invocations]

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode,
                 model, allowed_tools=None, max_budget_usd=None,
                 suite_command=None):
        index = len(self.invocations)
        cost = self.cost_of(index)
        self.invocations.append({
            "prompt": prompt, "stage": stage, "cwd": Path(cwd),
            "log_path": Path(log_path), "cost_usd": cost,
            "max_budget_usd": max_budget_usd,
        })
        log = Path(log_path)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            json.dumps({"type": "result", "total_cost_usd": DECOY_COST}) + "\n",
            encoding="utf-8")
        if self.act is not None:
            self.act(self.target)
        if self.raises:
            raise RuntimeError(self.raises)
        self.artifact.parent.mkdir(parents=True, exist_ok=True)
        self.artifact.write_text(json.dumps({"findings": self.findings}),
                                 encoding="utf-8")
        return AgentResult(ok=True, result_text="inspected", cost_usd=cost)


class NoInvocationExpected:
    """The default in place of the agent runner: being called at all is the
    failure. Every test that wants an invocation installs its own fake."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **keywords):
        self.calls += 1
        raise AssertionError(
            "this module's inspection reached agent_runner.run_agent, which it "
            "replaces so that nothing here can invoke a model")


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    guard = NoInvocationExpected()
    monkeypatch.setattr(agent_runner, "run_agent", guard)
    return guard


# ==========================================================================
# Driving a run, and driving a broad-mode inspection
# ==========================================================================


class Runner:
    """The fake agent runner the coordinator is handed for a run's stages.

    `costs` maps a stage name to what its invocation reports, so a run can be
    driven to the edge of a declared ceiling by what its stages spend. A stage
    absent from the mapping reports nothing, which is what every run in this
    module that is not about the ceiling does — leaving the inspection as the
    only thing that reports a cost at all.
    """

    def __init__(self, target_root: Path, *, costs: dict | None = None):
        self.target_root = Path(target_root)
        self.run_dir = self.target_root / ".harness" / "runs" / STORY_ID
        self.costs = dict(costs or {})
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 **declared):
        self.calls.append(stage)
        if stage == WRITING:
            _write(self.run_dir / conftest.CHANGED_FILES,
                   {"modified": [CHANGED_SOURCE], "created": [], "deleted": []})
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Did the work.\n", encoding="utf-8")
            (self.target_root / CHANGED_SOURCE).write_text(
                "def a():\n    return 11\n", encoding="utf-8")
        elif stage == VERIFYING:
            _write(self.run_dir / conftest.VERIFICATION_RESULT, PASSED)
        return AgentResult(ok=True, result_text=f"{stage} done",
                           cost_usd=self.costs.get(stage))


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run(target: Path, harness: Path, runner: Runner) -> int:
    return story_coordinator.run_story(
        STORY_ID, harness, target, runner, sleep=lambda _seconds: None)


def run_dir_of(target: Path) -> Path:
    return target / ".harness" / "runs" / STORY_ID


def state_of(target: Path) -> dict:
    return json.loads(
        (run_dir_of(target) / "state.json").read_text(encoding="utf-8"))


def messages(target: Path) -> list[str]:
    path = run_dir_of(target) / "events.log"
    if not path.is_file():
        return []
    return [line.split("] ", 1)[-1]
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def cost_record(target: Path) -> dict:
    return story_coordinator.load_cost_record(run_dir_of(target))


def inspection_entries(target: Path) -> list[dict]:
    """The cost.json entries a post-story inspection appended, by their stage."""
    return [one for one in cost_record(target)["invocations"]
            if one["stage"] == story_inspection.COST_STAGE]


def head_subject(root: Path) -> str:
    return _git(root, "log", "-1", "--format=%s").stdout.strip()


def committed_paths(root: Path, revision: str = "HEAD") -> list[str]:
    listed = _git(root, "show", "--name-only", "--format=", revision)
    return [line for line in listed.stdout.splitlines() if line.strip()]


def narrow_run(target: Path, harness: Path, monkeypatch, *,
               findings=(), costs=(FIRST_COST,), act=None, raises: str = "",
               stage_costs: dict | None = None):
    """One completing run of the fixture, with the inspector installed.

    Returns `(code, inspector, runner)`.
    """
    config = harness_config.load_config(target)
    inspector = Inspector(target, config, findings=findings, costs=costs,
                          act=act, raises=raises)
    monkeypatch.setattr(agent_runner, "run_agent", inspector)
    runner = Runner(target, costs=stage_costs)
    return run(target, harness, runner), inspector, runner


def broad_inspection(target: Path, harness: Path, *, findings=(),
                     costs=(FIRST_COST, SECOND_COST), act=None):
    """One whole l5-inspect invocation, against a fake runner.

    The runner is passed explicitly rather than defaulted, so nothing here can
    fall through to the real one.
    """
    config = harness_config.load_config(target)
    inspector = Inspector(target, config, findings=findings, costs=costs,
                          act=act)
    report = inspection.inspect(target, config, harness, runner=inspector)
    return report, inspector


# ==========================================================================
# Reading the record back, through the declaration that routes it
# ==========================================================================


#: The projection as this module imported it, bound once so that reading a
#: record back stays independent of any fault a test injects into the write
#: path. The tests below make that projection raise for the inspection's own
#: entry, and a reader that reached for the patched attribute would raise in
#: sympathy instead of reporting the absence it exists to observe.
PROJECTION = story_coordinator.history_record


def routed_logs() -> dict:
    """The declared logs an inspection's record reaches, and their shapes.

    Asked of the cross-run history declaration through the coordinator's own
    projection, so this module writes no log filename of its own and a
    declaration that stopped routing this kind would stop this reading too.
    """
    return {
        log: declaration
        for log, declaration in
        story_coordinator.history_log_declarations().items()
        if PROJECTION(
            {"event": inspection.INSPECTION_EVENT, "timestamp": ""},
            [], "", declaration) is not None
    }


def records(target: Path) -> list[dict]:
    """Every inspection record the target holds, oldest first."""
    directory = harness_config.history_dir(target, {})
    found: list[dict] = []
    for log in sorted(routed_logs()):
        path = directory / log
        if path.is_file():
            found += [json.loads(line)
                      for line in path.read_text(encoding="utf-8").splitlines()
                      if line.strip()]
    return found


def one_record(target: Path) -> dict:
    written = records(target)
    assert len(written) == 1, written
    return written[0]


def problems_with(record: dict) -> list[str]:
    """What the declaration says is wrong with a record, for every log it goes
    to — the shipped shape, which is the legitimate subject here: the claim is
    about what this harness declares a record to be."""
    problems: list[str] = []
    for declaration in routed_logs().values():
        problems += schema_validator.validate(record, declaration)
    return problems


# ==========================================================================
# What a broad-mode inspection records
# ==========================================================================


def test_a_broad_mode_inspection_records_its_cost_its_mode_its_scope_and_its_counts(
        target, harness):
    """One line in the declared log, carrying everything the criterion names.

    The cost is the sum of what the two invocations reported and is neither of
    them, so it is the composition being asserted rather than a figure that
    happened to be copied through; and the timestamp is parsed with the format
    the other logs are written in rather than merely being non-empty.

    The counts are the report's own, which is what the criterion asks of them:
    each of the two scopes is one invocation and each writes the finding this
    fixture supplied, so both are found and both are filed under one key.
    """
    report, inspector = broad_inspection(target, harness,
                                         findings=[finding()])

    assert len(inspector.invocations) == 2, inspector.invocations
    record = one_record(target)
    assert record["mode"] == inspection.MODE_BROAD
    assert record["cost_usd"] == FIRST_COST + SECOND_COST
    assert record["cost_usd"] not in (FIRST_COST, SECOND_COST)
    assert record["invocations"] == len(inspector.invocations)
    assert record["scope_files"] == report.scope_files
    assert record["scope_files"] == len(TRACKED)
    assert (record["filed"], record["dropped"]) == (len(report.filed),
                                                    len(report.dropped))
    assert record["findings"] == len(report.filed) + len(report.dropped)
    assert (record["findings"], record["filed"], record["dropped"]) == \
        (len(inspector.invocations), len(inspector.invocations), 0)
    assert datetime.strptime(record["timestamp"],
                             story_coordinator.HISTORY_TIMESTAMP_FORMAT)
    assert problems_with(record) == [], record


def test_a_broad_mode_record_names_no_story_and_a_narrow_mode_record_names_its_own(
        target, harness, monkeypatch):
    """The absence and its control, in one file read one way.

    A broad inspection is not made by a run and has no story to name; the
    narrow record read out of the same log by the same reader carries one, so
    the absence is the mode deciding rather than a reader that has stopped
    seeing the field.
    """
    code, _inspector, _runner = narrow_run(target, harness, monkeypatch,
                                           findings=[finding()])
    assert code == 0
    broad_inspection(target, harness, findings=[])

    written = records(target)
    assert len(written) == 2, written
    by_mode = {one["mode"]: one for one in written}
    assert set(by_mode) == {inspection.MODE_NARROW, inspection.MODE_BROAD}
    assert by_mode[inspection.MODE_NARROW]["story_id"] == STORY_ID
    assert "story_id" not in by_mode[inspection.MODE_BROAD]
    for record in written:
        assert problems_with(record) == [], record


def test_one_read_of_one_file_answers_what_inspection_cost_across_both_modes(
        target, harness, monkeypatch):
    """The criterion as a reader would exercise it: one file, both modes, and
    no run directory walked to get either figure."""
    code, narrow, _runner = narrow_run(target, harness, monkeypatch,
                                       findings=[finding()])
    assert code == 0
    _report, broad = broad_inspection(target, harness, findings=[])

    written = records(target)
    assert [one["cost_usd"] for one in written] == [
        sum(narrow.reported), sum(broad.reported)]
    assert all("cost_usd" in one and "scope_files" in one and
               "invocations" in one and "mode" in one for one in written)


# ==========================================================================
# What a narrow-mode inspection records
# ==========================================================================


def test_a_narrow_mode_record_carries_the_same_fields_and_its_story(
        target, harness, monkeypatch):
    """The one invocation's figure, its mode, its scope size and the three
    counts, whose meaning and values are what they were."""
    code, inspector, _runner = narrow_run(target, harness, monkeypatch,
                                          findings=[finding()])
    assert code == 0

    record = one_record(target)
    assert record["story_id"] == STORY_ID
    assert record["mode"] == inspection.MODE_NARROW
    assert record["cost_usd"] == FIRST_COST
    assert record["invocations"] == len(inspector.invocations) == 1
    assert record["scope_files"] == len(
        rendered_paths(inspector.invocations[0]["prompt"]))
    assert (record["findings"], record["filed"], record["dropped"]) == (1, 1, 0)
    assert problems_with(record) == [], record


def rendered_paths(prompt: str) -> list[str]:
    """The paths the fixture template rendered under its scope-paths label."""
    lines = prompt.splitlines()
    where = lines.index("scope_paths:") + 1
    found = []
    while where < len(lines) and lines[where].strip():
        found.append(lines[where].strip())
        where += 1
    return found


def test_an_inspection_that_was_not_made_still_leaves_its_line_with_zeroes(
        tmp_path, harness, no_model):
    """The bound is unusable, so no invocation is made and nothing is spent.

    The line is still there, with its counts and its scope at zero and no cost
    field at all — the same shape it had before this story, plus the mode.
    """
    unusable = build_target(tmp_path / "unusable-cap",
                            **{story_inspection.MAX_FILES_KEY: "lots"})
    assert run(unusable, harness, Runner(unusable)) == 0
    assert no_model.calls == 0

    record = one_record(unusable)
    assert (record["findings"], record["filed"], record["dropped"]) == (0, 0, 0)
    assert (record["scope_files"], record["invocations"]) == (0, 0)
    assert "cost_usd" not in record
    assert record["mode"] == inspection.MODE_NARROW
    assert problems_with(record) == [], record


# ==========================================================================
# The figure is carried off the result, never re-derived
# ==========================================================================


def test_the_recorded_cost_is_what_the_invocation_reported_and_not_what_its_log_says(
        target, harness, monkeypatch):
    """Constructed rather than argued.

    The fake writes an agent log carrying `DECOY_COST` at exactly the path the
    invocation is handed, and reports a different figure through the result. A
    record carrying the reported figure is therefore evidence that the cost
    came off the result; a record carrying the decoy would be a log having been
    read back.
    """
    code, inspector, _runner = narrow_run(target, harness, monkeypatch,
                                          findings=[finding()])
    assert code == 0
    log = inspector.invocations[0]["log_path"]
    assert str(DECOY_COST) in log.read_text(encoding="utf-8"), log

    record = one_record(target)
    assert record["cost_usd"] == FIRST_COST
    assert record["cost_usd"] != DECOY_COST
    assert inspection_entries(target)[0]["cost_usd"] == FIRST_COST
    assert cost_record(target)["total_usd"] != DECOY_COST


#: The ways a source reads a file. `open` is checked by name and the rest as
#: attribute calls, which between them cover every read either producing module
#: makes today and every one it could grow.
FILE_READS = ("read_text", "read_bytes", "readlines", "read")


def log_reads(source: str) -> list[int]:
    """Line numbers where `source` reads a file whose expression names a log.

    A read of *something called a log* rather than a read of one particular
    path, because what the criterion forbids is a second parser of the
    harness's own output wherever it is spelled — the runner's `log_path`, a
    module constant, or an attribute reached off either.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in FILE_READS:
            named = ast.unparse(node.func.value)
        elif isinstance(node.func, ast.Name) and node.func.id == "open":
            named = ast.unparse(node.args[0]) if node.args else ""
        else:
            continue
        if "log" in named.lower():
            found.append(node.lineno)
    return found


#: The modules that obtain an inspection's cost. They are read as they ship,
#: which is legitimate here: the claim *is* about what this harness's source
#: does.
PRODUCING_MODULES = ("orchestration/inspection.py",
                     "orchestration/story_inspection.py")


@pytest.mark.parametrize("relative", PRODUCING_MODULES)
def test_no_producing_module_reads_an_agent_log_back(relative):
    source = (REPO_ROOT / relative).read_text(encoding="utf-8")
    assert log_reads(source) == [], relative


@pytest.mark.parametrize("planted", [
    "def a(log_path):\n    return log_path.read_text(encoding='utf-8')\n",
    "def a(self):\n    return json.load(open(self.inspection_log))\n",
    "def a(directory):\n    return (directory / INSPECTION_LOG).read_text()\n",
])
def test_the_scan_reports_a_source_that_does_read_one(planted):
    """The control for the emptiness above: the same scan over sources that
    really do read a log reports each of them."""
    assert log_reads(planted) == [2], planted


# ==========================================================================
# The run's own cost record, and the allowance it does not touch
# ==========================================================================


def test_the_inspections_cost_is_one_entry_in_that_runs_cost_json(
        target, harness, monkeypatch):
    """Beside the stage invocations, at the entry index the run records for the
    entry now running, and at attempt zero because it is not an attempt at a
    stage."""
    stage_cost = 0.05
    code, _inspector, runner = narrow_run(
        target, harness, monkeypatch, findings=[finding()],
        stage_costs={name: stage_cost for name in runner_stages()})
    assert code == 0

    record = cost_record(target)
    appended = inspection_entries(target)
    assert len(appended) == 1, record
    entry = appended[0]
    assert entry["cost_usd"] == FIRST_COST
    assert entry["attempt"] == story_inspection.COST_ATTEMPT == 0
    assert entry["entry"] == state_of(target)["resume_count"]
    # Beside the stage invocations rather than instead of them, and the total
    # accounts for both.
    assert len(record["invocations"]) == len(runner.calls) + 1
    assert record["total_usd"] == pytest.approx(
        stage_cost * len(runner.calls) + FIRST_COST)


def runner_stages() -> list[str]:
    """The stages the built workflow declares, so no stage name is spelled at a
    call site."""
    return [stage["name"] for stage in WORKFLOW["stages"]]


def test_the_inspections_cost_does_not_move_the_runs_live_allowance(
        target, harness, monkeypatch):
    """The recording half and the enforcing half are different jobs.

    `entry_cost_usd` on state.json is what the run ceiling is compared against,
    and after the inspection it accounts for the stages and for nothing else —
    while cost.json, which enforces nothing, accounts for both.
    """
    stage_cost = 0.05
    stages = runner_stages()
    code, _inspector, runner = narrow_run(
        target, harness, monkeypatch, findings=[finding()],
        stage_costs={name: stage_cost for name in stages})
    assert code == 0

    spent = state_of(target)["entry_cost_usd"]
    assert spent == pytest.approx(stage_cost * len(runner.calls))
    assert cost_record(target)["total_usd"] == pytest.approx(spent + FIRST_COST)


def with_run_ceiling(ceiling: float) -> dict:
    """The built definition with a run ceiling declared on it.

    A copy, so the module-level definition every other run here executes is
    left exactly as it was.
    """
    return {**WORKFLOW, "max_run_cost_usd": ceiling}


@pytest.fixture
def ceilinged(tmp_path):
    """A harness root whose workflow declares a run ceiling, and the ceiling."""
    ceiling = 1.00
    root = conftest.materialize_workflow(
        with_run_ceiling(ceiling), tmp_path / "ceilinged-harness",
        rules=FIXTURE_RULES)
    (root / "prompts" / inspection.INSPECTOR_PROMPT).write_text(
        "# a template this module wrote\n\nscope_paths:\n{{scope_paths}}\n\n"
        "findings_path:\n{{findings_path}}\n", encoding="utf-8")
    return root, ceiling


#: Stage spends that leave the run at the edge of the ceiling above without
#: crossing it: the ceiling is compared immediately before each invocation, so
#: what matters is that the sum before the last stage is under it and the sum
#: after is at the edge. The inspection's own figure would carry it over.
EDGE_COSTS = {WRITING: 0.90, VERIFYING: 0.05}

#: Stage spends that do cross it, for the control.
CROSSING_COSTS = {WRITING: 1.20}


def test_a_run_at_the_edge_of_its_ceiling_completes_is_inspected_and_stays_under(
        target, ceilinged, monkeypatch):
    """The precise point of the story.

    The inspection runs after the completion commit, when the run's work is
    done and committed, so charging its spend to a cap the run has already
    honoured could push a completed run over it. Here the stages leave the
    allowance just under the ceiling and the inspection's own figure would
    carry it past — and the run completes, is inspected, and its allowance is
    where the stages left it.
    """
    harness, ceiling = ceilinged
    code, inspector, _runner = narrow_run(
        target, harness, monkeypatch, findings=[finding()],
        stage_costs=EDGE_COSTS)

    assert code == 0
    assert state_of(target)["status"] == "completed"
    assert inspector.invocations, "the completed run was not inspected"

    spent = state_of(target)["entry_cost_usd"]
    assert spent == pytest.approx(sum(EDGE_COSTS.values()))
    assert spent < ceiling
    # Charged, it would have been over — so the ceiling really is at the edge
    # rather than roomy enough for the assertion to hold either way.
    assert spent + FIRST_COST > ceiling
    assert cost_record(target)["total_usd"] > ceiling
    assert not state_of(target).get("stopped_on_cost")
    assert not [line for line in messages(target)
                if story_coordinator.format_usd(ceiling) in line]


def test_the_same_ceiling_stops_a_run_whose_stages_cross_it(
        tmp_path, ceilinged, no_model):
    """The control for the run above: the ceiling is live.

    Without it, "the edge run was not reported as over its ceiling" would pass
    just as happily against a ceiling nothing compares against.
    """
    harness, ceiling = ceilinged
    crossing = build_target(tmp_path / "crossing",
                            **{story_inspection.MAX_FILES_KEY: ROOMY_CAP})

    code = run(crossing, harness, Runner(crossing, costs=CROSSING_COSTS))

    assert code != 0
    assert state_of(crossing)["status"] == "escalated"
    assert state_of(crossing)["stopped_on_cost"] is True
    assert [line for line in messages(crossing)
            if story_coordinator.format_usd(ceiling) in line], messages(crossing)
    # And the run that was stopped on the ceiling inspected nothing, so the
    # figures above belong to the run that completed.
    assert no_model.calls == 0


def assignments_to(source: str, name: str) -> list[int]:
    """Line numbers where `source` assigns to an attribute called `name`.

    Plain and augmented assignment both, because the allowance is moved by
    `+=` wherever it is legitimately moved and a copy of that line here is
    exactly what the story forbids.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AugAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr == name:
                found.append(node.lineno)
    return found


#: The field the run ceiling is compared against, named off the coordinator's
#: own state so this module spells no field of its own.
ALLOWANCE_FIELD = "entry_cost_usd"


def test_nothing_in_the_post_story_inspection_moves_the_live_allowance():
    """The source half of the behavioural assertion above."""
    assert ALLOWANCE_FIELD in story_coordinator.RunState.__dataclass_fields__
    source = (REPO_ROOT / "orchestration" / "story_inspection.py").read_text(
        encoding="utf-8")
    assert assignments_to(source, ALLOWANCE_FIELD) == []


def test_the_scan_reports_a_module_that_does_move_it():
    """The control, and the shape of the line it is looking for: the
    coordinator's own — where moving the allowance is right — is reported by
    the same scan."""
    planted = (f"def a(state, cost):\n    state.{ALLOWANCE_FIELD} += cost\n")
    assert assignments_to(planted, ALLOWANCE_FIELD) == [2]
    coordinator = (REPO_ROOT / "orchestration" / "story_coordinator.py"
                   ).read_text(encoding="utf-8")
    assert assignments_to(coordinator, ALLOWANCE_FIELD) != []


# ==========================================================================
# An invocation that reported nothing records nothing, rather than zero
# ==========================================================================


def test_a_narrow_inspection_reporting_no_cost_records_none_and_adds_no_entry(
        target, harness, monkeypatch):
    """Absent, never zero: a zero would be indistinguishable from an inspection
    that genuinely cost nothing and would corrupt any average taken later."""
    code, inspector, _runner = narrow_run(target, harness, monkeypatch,
                                          findings=[finding()], costs=(None,))
    assert code == 0
    assert inspector.reported == [None]

    record = one_record(target)
    assert "cost_usd" not in record, record
    assert inspection_entries(target) == []
    assert cost_record(target)["total_usd"] == 0.0
    # The rest of the record is exactly what a reporting inspection leaves, so
    # this is the cost being absent rather than the record being absent.
    assert (record["findings"], record["filed"], record["dropped"]) == (1, 1, 0)
    assert record["invocations"] == 1
    assert problems_with(record) == [], record


def test_the_same_run_under_a_reporting_runner_carries_both(
        tmp_path, harness, monkeypatch):
    """The control for both absences above."""
    reporting = build_target(tmp_path / "reporting",
                             **{story_inspection.MAX_FILES_KEY: ROOMY_CAP})
    code, _inspector, _runner = narrow_run(reporting, harness, monkeypatch,
                                           findings=[finding()])
    assert code == 0
    assert one_record(reporting)["cost_usd"] == FIRST_COST
    assert [one["cost_usd"] for one in inspection_entries(reporting)] == \
        [FIRST_COST]


def test_a_broad_inspection_whose_invocations_report_nothing_records_no_cost(
        target, harness):
    """And the same distinction one level up: a total of None where none of the
    invocations reported anything, rather than a sum of zeroes."""
    report, inspector = broad_inspection(target, harness, findings=[],
                                         costs=(None,))

    assert inspector.reported == [None, None]
    assert report.cost_usd is None
    assert "cost_usd" not in one_record(target)


def test_a_broad_inspection_where_only_one_invocation_reported_records_that_one(
        target, harness):
    """The mixed case, which is the one a sum could get wrong in either
    direction: the total is what was reported and nothing is invented for the
    invocation that reported nothing."""
    report, inspector = broad_inspection(target, harness, findings=[],
                                         costs=(FIRST_COST, None))

    assert inspector.reported == [FIRST_COST, None]
    assert report.cost_usd == FIRST_COST
    assert one_record(target)["cost_usd"] == FIRST_COST


# ==========================================================================
# The broad-mode commit
# ==========================================================================


def edits_a_file_elsewhere(root: Path) -> None:
    """What an inspection agent that ignored its instructions would leave: an
    edit somewhere in the repository that is nothing to do with the record."""
    (root / SIBLING_SOURCE).write_text("def b():\n    return 22\n",
                                       encoding="utf-8")


def test_the_broad_mode_record_is_committed_staging_the_declared_paths_by_name(
        target, harness):
    """Constructed rather than assumed: the fake inspector really does change a
    file elsewhere, and that file must be left in the working tree rather than
    folded into a commit this mechanism made.

    The paths are the ones `record_paths` derives from the same projection the
    append took, so nothing here spells a log filename either.
    """
    broad_inspection(target, harness, findings=[finding()],
                     act=edits_a_file_elsewhere)

    declared = list(inspection.record_paths(
        target, harness_config.load_config(target)))
    assert declared, "the declaration routes this kind to no log"
    assert head_subject(target) == inspection.COMMIT_SUBJECT
    assert committed_paths(target) == declared
    assert SIBLING_SOURCE in story_coordinator.dirty_paths(target)


def test_the_broad_mode_commit_carries_no_completion_escalation_or_pause_shape(
        target, harness):
    """So nothing that reads a branch for one of those reads this commit."""
    broad_inspection(target, harness, findings=[])

    subject = head_subject(target)
    assert story_coordinator.escalated_story(subject) is None
    assert story_coordinator.paused_story(subject) is None
    assert story_coordinator.COMPLETION_COMMIT_MARKER not in \
        _git(target, "log", "-1", "--format=%B").stdout
    # The control for the three absences: the same readers do recognise the
    # commits they are for, driven against messages the coordinator composed.
    escalation = story_coordinator.escalation_commit_message(
        story_coordinator.RunState(story_id=STORY_ID,
                                   branch=f"story/{STORY_ID}",
                                   current_stage=VERIFYING),
        "the fixture run stopped here")
    assert story_coordinator.escalated_story(
        escalation.splitlines()[0]) == STORY_ID


# ==========================================================================
# A record that cannot be written costs the record and nothing else
# ==========================================================================


def refuse_to_record(monkeypatch) -> None:
    """Make the record write raise, for the inspection's own entry and for
    nothing else.

    Targeted at the projection every declared record goes through, and
    conditioned on the kind, so a run's own events are appended exactly as they
    would have been and the only write that fails is the one this story added.
    A blanket failure would break the run for a reason no criterion is about.
    """
    real = story_coordinator.history_record

    def refusing(entry, history, story_id, declaration):
        if entry.get("event") == inspection.INSPECTION_EVENT:
            raise RuntimeError("the history directory cannot be written")
        return real(entry, history, story_id, declaration)

    monkeypatch.setattr(story_coordinator, "history_record", refusing)


def test_a_broad_inspection_whose_record_write_raises_reports_what_it_filed(
        tmp_path, target, harness, monkeypatch):
    """The report and the exit are what they would have been.

    Compared against the same inspection of an identical target whose record
    write is intact, rather than against values written here — what the story
    promises is that the record makes no difference to the inspection.
    """
    control = build_target(tmp_path / "record-intact")
    expected, _inspector = broad_inspection(control, harness,
                                            findings=[finding()])

    refuse_to_record(monkeypatch)
    report, inspector = broad_inspection(target, harness, findings=[finding()])

    assert inspector.invocations, "the inspection was never attempted"
    assert [one.slug for one in report.filed] == \
        [one.slug for one in expected.filed]
    assert len(report.dropped) == len(expected.dropped)
    assert report.cost_usd == expected.cost_usd
    # The record really did fail to be written, so this is the guard holding
    # rather than a write that quietly succeeded.
    assert records(target) == []
    assert records(control) != []
    assert head_subject(target) == SETUP_SUBJECT


def test_a_run_whose_inspection_record_write_raises_completes_as_it_would(
        tmp_path, target, harness, monkeypatch):
    """And the narrow half: the run's exit is the one the same fixture has with
    the record write intact."""
    control = build_target(tmp_path / "run-record-intact",
                           **{story_inspection.MAX_FILES_KEY: ROOMY_CAP})
    expected, _inspector, _runner = narrow_run(control, harness, monkeypatch,
                                               findings=[finding()])

    refuse_to_record(monkeypatch)
    code, inspector, _runner = narrow_run(target, harness, monkeypatch,
                                          findings=[finding()])

    assert code == expected == 0
    assert state_of(target)["status"] == state_of(control)["status"]
    assert inspector.invocations, "the inspection was never attempted"
    assert records(target) == []
    assert records(control) != []


def test_a_cost_record_that_cannot_be_appended_costs_the_run_nothing(
        tmp_path, target, harness, monkeypatch):
    """The other half of the narrow mode's write, broken at its own seam.

    The stages report nothing, so the appender the coordinator shares with this
    mechanism is reached by the inspection alone and breaking it breaks nothing
    else. The cross-run record is still written — so the two halves are guarded
    separately — and the run completes with the status it would have had.
    """
    control = build_target(tmp_path / "cost-record-intact",
                           **{story_inspection.MAX_FILES_KEY: ROOMY_CAP})
    expected, _inspector, _runner = narrow_run(control, harness, monkeypatch,
                                               findings=[finding()])

    def refusing(*args, **keywords):
        raise RuntimeError("cost.json cannot be written")

    monkeypatch.setattr(story_coordinator, "append_cost_record", refusing)
    code, inspector, _runner = narrow_run(target, harness, monkeypatch,
                                          findings=[finding()])

    assert code == expected == 0
    assert state_of(target)["status"] == state_of(control)["status"]
    assert inspector.invocations
    assert inspection_entries(target) == []
    assert inspection_entries(control) != []
    assert one_record(target)["cost_usd"] == FIRST_COST


# ==========================================================================
# What this story did not change
# ==========================================================================


#: Where every ceiling this harness declares is written: the workflow
#: definitions carry the run and per-execution ceilings, and the configuration
#: files below them carry the inspection allowance.
CEILING_DECLARATIONS = ["workflows/", ".harness/config.yaml",
                        "templates/config.yaml"]


def test_this_story_changed_no_ceiling_declaration():
    """No ceiling is retuned here: there is no corpus yet, and this story is
    what starts one.

    Bounded at this module's own story range through the shared resolution, so
    the answer survives the commit this story is about to make rather than
    going vacuously green on it.
    """
    for declaration in CEILING_DECLARATIONS:
        assert conftest.story_diff([declaration],
                                   validation_file=Path(__file__)) == "", \
            declaration


def test_the_same_predicate_reports_a_story_that_did_change_one(tmp_path):
    """The control for that emptiness, constructed rather than recalled: a
    story that really did edit a ceiling declaration is reported by the same
    reading, so the empty diffs above are the story rather than a resolution
    that has stopped comparing."""
    root = conftest.constructed_story(
        tmp_path, respected=CEILING_DECLARATIONS[1:],
        violated=[CEILING_DECLARATIONS[0]])

    for untouched in CEILING_DECLARATIONS[1:]:
        assert conftest.constructed_story_diff(root, [untouched]) == "", untouched
    assert conftest.constructed_story_diff(root, [CEILING_DECLARATIONS[0]]) != ""
