"""story-039 validation: every configured value is proven to govern.

The keys listed in `KEY_PROOFS` below are read out of `.harness/config.yaml`.
Until this module, the
suite could not tell a key that is obeyed from a key that was moved into
configuration and then hardcoded to the same literal, because every fixture
in the repository configured the value the harness would have picked anyway.

So nothing here asserts that a key is *mentioned*. Each key is set to a value
this harness would never choose — every one of them carries the token
``xyzzy`` — and the harness is then observed acting on that value:

* `KEY_PROOFS` maps each declared key to the node id that proves it and to
  what "proven" means for it. Most are proven **behaviourally**: the fixture
  configures the varying value and the run is observed following it. `model`,
  `permission_mode` and `allowed_tools` are handed straight to the agent
  runner and are observable nowhere else, so their proof is an
  **argument-list** assertion on the invocation the coordinator builds for a
  fake runner. `allowed_tools` covers both sites that pass it: the runner call
  and the rendered prompt.

* What the key set *is* comes from `schemas/harness-config.schema.json`,
  through `harness_config.declared_config_keys()`. Coverage is set equality
  against it in both directions — against `KEY_PROOFS`, so a key added with
  no proof fails, and against an AST scan of `orchestration/` and `scripts/`,
  so a key the harness reads and the schema does not declare fails. Neither
  comparison is against a second maintained list.

Every absence asserted here carries a control that constructs the violation:

* the AST scan is fed a synthetic module reading a fourteenth key, and
  reports it;
* the coverage comparison is fed a schema with an unproven key and a proof
  naming an undeclared key, and reports both;
* the "no varying value is a default" assertion is fed a proof value changed
  to its default, and reports it;
* and, the control that matters most, for **every** declared key a throwaway
  copy of `orchestration/` has that key's read replaced by the literal it
  falls back to, and that key's own proof is run there by a real pytest and
  required to go red. A proof that set a key and asserted nothing about its
  effect would survive the coverage checks and die here.

Nothing in this module resolves a baseline out of git; the shared resolution
in `tests/conftest.py` is used where history is read at all. Nothing invokes
a model: every run below goes through a fake runner.
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import conftest
import harness_config
import run_status
import schema_validator
import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(harness_config.__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"
MODULE_NAME = Path(__file__).name
NODE_PREFIX = f"tests/{MODULE_NAME}::"

#: The declaration. Read once, here, so every comparison below is against the
#: schema rather than against a list this module maintains.
DECLARED = harness_config.declared_config_keys()

#: This repository's own configuration, which no varying value may coincide
#: with — a proof whose value is what the repository already carries would
#: pass against a hardcoded literal and prove nothing.
THIS_REPO_CONFIG = harness_config.load_config(REPO_ROOT)


# --------------------------------------------------------------------------
# The varying values, the fallbacks they must not equal, and what "proven"
# means for each key
# --------------------------------------------------------------------------

#: One distinctive token in every value, so an accidental coincidence with
#: anything the harness would pick is impossible rather than unlikely.
TOKEN = "xyzzy"

VARYING: dict[str, object] = {
    "allowed_tools": ["Bash(xyzzy:*)"],
    "architecture_docs": ["docs/xyzzy-architecture.md"],
    "base_branch": "xyzzy-base",
    "branch_prefix": "xyzzy-branch/",
    "census_command": "xyzzy-census --count",
    "logs_dir": ".harness/xyzzy-logs",
    "model": "xyzzy-model",
    "permission_mode": "xyzzyPrompt",
    "runs_dir": ".harness/xyzzy-runs",
    "standards_dir": ".harness/xyzzy-standards",
    "stories_dir": ".harness/xyzzy-stories",
    "test_command": "xyzzy-runner --all",
    "test_selection_command": "xyzzy-selector --only {test}",
    "tests_dir": "xyzzy-checks/",
    "verification_runner": "/xyzzy/bin/interpreter",
    "workflow": "xyzzy-workflow",
}

#: What the harness uses when the key is absent, as written in the code that
#: reads it. `None` is the answer for the four keys with no fallback at all:
#: `allowed_tools`, `base_branch`, `census_command`, `model` and
#: `verification_runner` are read with a bare `config.get`, and `test_command`
#: is read with no default and no fallback, so a target that omits it cannot
#: run the clean-clone check.
FALLBACKS: dict[str, object] = {
    "allowed_tools": None,
    "architecture_docs": [],
    "base_branch": None,
    "branch_prefix": "story/",
    "census_command": None,
    "logs_dir": ".harness/logs",
    "model": None,
    "permission_mode": "acceptEdits",
    "runs_dir": ".harness/runs",
    "standards_dir": ".harness/standards",
    "stories_dir": ".harness/stories",
    "test_command": None,
    "test_selection_command": None,
    "tests_dir": None,
    "verification_runner": None,
    "workflow": "story-workflow",
}

BEHAVIOURAL = "behavioural"
ARGUMENT_LIST = "argument-list"


@dataclass(frozen=True)
class Proof:
    """Which node proves a key, and what proving it consists of.

    `kind` is recorded rather than left to a reader's inference. Three keys
    are handed to the agent runner and never touch the repository, the run
    directory or the rendered prompt in a form anything else can observe, so
    "proven" for them means the invocation the coordinator built carried the
    configured value. Saying so is the honest description of a weaker
    observation, not an excuse for it: the mutation control holds those three
    to exactly the same standard as the ten behavioural ones.
    """

    node: str
    kind: str

    @property
    def node_id(self) -> str:
        return NODE_PREFIX + self.node


KEY_PROOFS: dict[str, Proof] = {
    "allowed_tools": Proof(
        "test_allowed_tools_reaches_both_the_runner_and_the_rendered_prompt",
        ARGUMENT_LIST),
    "architecture_docs": Proof(
        "test_architecture_docs_names_the_documents_injected_into_a_stage",
        BEHAVIOURAL),
    "base_branch": Proof(
        "test_base_branch_is_the_base_the_pre_flight_resolves_and_decides_on",
        BEHAVIOURAL),
    "branch_prefix": Proof(
        "test_branch_prefix_names_the_branch_the_run_creates_and_works_on",
        BEHAVIOURAL),
    "census_command": Proof(
        "test_census_command_is_the_command_the_suite_census_runs",
        BEHAVIOURAL),
    "logs_dir": Proof(
        "test_logs_dir_is_where_the_stage_log_is_written",
        BEHAVIOURAL),
    "model": Proof(
        "test_model_is_the_model_every_stage_invocation_carries",
        ARGUMENT_LIST),
    "permission_mode": Proof(
        "test_permission_mode_is_the_mode_every_stage_invocation_carries",
        ARGUMENT_LIST),
    "runs_dir": Proof(
        "test_runs_dir_is_where_the_run_state_is_written_and_read_back",
        BEHAVIOURAL),
    "standards_dir": Proof(
        "test_standards_dir_is_where_the_injected_standards_are_read_from",
        BEHAVIOURAL),
    "stories_dir": Proof(
        "test_stories_dir_is_where_the_story_artifact_is_read_from",
        BEHAVIOURAL),
    "test_command": Proof(
        "test_test_command_is_the_command_the_clean_clone_path_builds",
        BEHAVIOURAL),
    "test_selection_command": Proof(
        "test_test_selection_command_is_what_a_nomination_is_substituted_into",
        BEHAVIOURAL),
    "tests_dir": Proof(
        "test_tests_dir_is_the_location_the_workflow_and_the_prompt_are_governed_at",
        BEHAVIOURAL),
    "verification_runner": Proof(
        "test_verification_runner_is_the_executable_the_check_resolves",
        BEHAVIOURAL),
    "workflow": Proof(
        "test_workflow_names_the_definition_the_run_actually_executes",
        BEHAVIOURAL),
}


# --------------------------------------------------------------------------
# The mutations: each key's read replaced by the literal it falls back to
#
# This is what makes every proof above non-vacuous. A proof that configured a
# key and asserted nothing about its effect passes the coverage checks and
# dies here, because the copy it runs in no longer reads the key at all.
#
# Four keys have no fallback, so the substitution is `None` — which is what
# the code would compute if the key were absent. `test_command` has neither a
# default nor a fallback, so its substitution is this repository's own
# configured value: a literal standing where a configured read used to be is
# precisely the defect this story exists to detect.
# --------------------------------------------------------------------------

HARDCODED_TEST_COMMAND = '".venv/bin/python -m pytest tests/ -q"'

MUTATIONS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "allowed_tools": (
        ("orchestration/story_coordinator.py",
         'allowed_tools=config.get("allowed_tools"),',
         "allowed_tools=None,"),
    ),
    "architecture_docs": (
        ("orchestration/context_assembler.py",
         'config.get("architecture_docs", [])',
         "[]"),
    ),
    "base_branch": (
        ("orchestration/story_coordinator.py",
         'configured = config.get("base_branch")',
         "configured = None"),
    ),
    "branch_prefix": (
        ("orchestration/story_coordinator.py",
         'config.get("branch_prefix", "story/")',
         '"story/"'),
    ),
    "census_command": (
        ("orchestration/story_coordinator.py",
         'config.get("census_command")',
         "None"),
    ),
    "logs_dir": (
        ("orchestration/story_coordinator.py",
         'config.get("logs_dir", ".harness/logs")',
         '".harness/logs"'),
    ),
    "model": (
        ("orchestration/story_coordinator.py",
         'model=config.get("model"),',
         "model=None,"),
    ),
    "permission_mode": (
        ("orchestration/story_coordinator.py",
         'config.get("permission_mode", "acceptEdits")',
         '"acceptEdits"'),
    ),
    "runs_dir": (
        ("orchestration/story_coordinator.py",
         'config.get("runs_dir", ".harness/runs")',
         '".harness/runs"'),
        ("orchestration/run_status.py",
         'config.get("runs_dir", ".harness/runs")',
         '".harness/runs"'),
    ),
    "standards_dir": (
        ("orchestration/context_assembler.py",
         'config.get("standards_dir", ".harness/standards")',
         '".harness/standards"'),
    ),
    "stories_dir": (
        ("orchestration/story_coordinator.py",
         'config.get("stories_dir", ".harness/stories")',
         '".harness/stories"'),
    ),
    "test_command": (
        ("orchestration/story_coordinator.py",
         'config["test_command"]',
         HARDCODED_TEST_COMMAND),
        ("orchestration/context_assembler.py",
         'config.get("test_command")',
         HARDCODED_TEST_COMMAND),
    ),
    "test_selection_command": (
        ("orchestration/story_coordinator.py",
         'config.get("test_selection_command")',
         "None"),
    ),
    "tests_dir": (
        ("orchestration/harness_config.py",
         'config.get("tests_dir")',
         "None"),
        ("orchestration/context_assembler.py",
         'config.get("tests_dir")',
         "None"),
    ),
    "verification_runner": (
        ("orchestration/story_coordinator.py",
         'config.get("verification_runner")',
         "None"),
    ),
    "workflow": (
        ("orchestration/story_coordinator.py",
         'config.get("workflow", "story-workflow")',
         '"story-workflow"'),
    ),
}


# --------------------------------------------------------------------------
# The fixture target and the fixture harness root
# --------------------------------------------------------------------------

STORY_ID = "story-001"
FIXTURE_WORKFLOW = "xyzzy-workflow"

#: A stage no shipped workflow defines. Its appearance in the run's stage
#: sequence is what proves the coordinator loaded the *named* definition
#: rather than the one that ships.
AUDIT_STAGE = "xyzzy-auditor"
AUDIT_ARTIFACT = "xyzzy-audit-report.md"

STANDARDS_MARKER = "xyzzy-standard: the marker that proves standards_dir"
ARCHITECTURE_MARKER = "xyzzy-architecture: the marker that proves architecture_docs"

PASS_VERDICT = {"status": "passed", "blocking_issues": [], "unverified": [],
                "retry_recommended": False}


def _yaml_lines(values: dict[str, object]) -> str:
    lines = ["# fixture configuration for story-039's proofs"]
    for key in sorted(values):
        value = values[key]
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines += [f'  - "{item}"' for item in value]
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


def fixture_config(**overrides: object) -> dict[str, object]:
    """The varying value for every key, with per-test departures applied.

    Every key is varied in every fixture. A test that names one key asserts
    about that key alone; the rest being varied too is what makes the fixture
    a repository the harness has never seen defaults for.
    """
    values = dict(VARYING)
    values.update(overrides)
    return values


def build_harness(tmp_path: Path) -> Path:
    """A runnable harness root carrying a workflow named for the varying value.

    `prompts/`, `rules/` and `schemas/` are the shipped ones, copied rather
    than symlinked so nothing here can reach back into this repository.
    `workflows/` carries the shipped definition under the configured name,
    with every suite-executing check removed — the clean-clone check, the
    revert check and the declared suite run each run the configured
    `test_command`, which in this fixture is deliberately not a command that
    exists — and with one extra stage no shipped workflow defines.
    """
    root = tmp_path / "xyzzy-harness"
    ignore = shutil.ignore_patterns("__pycache__")
    for directory in ("prompts", "rules", "schemas"):
        shutil.copytree(REPO_ROOT / directory, root / directory, ignore=ignore)
    (root / "workflows").mkdir()

    shipped = json.loads(
        (REPO_ROOT / "workflows" / "story-workflow.json").read_text(encoding="utf-8"))
    shipped["name"] = FIXTURE_WORKFLOW
    for stage in shipped["stages"]:
        stage.pop("clean_clone", None)
        stage.pop("revert_check", None)
        stage.pop("suite_run", None)
    shipped["stages"].append({
        "name": AUDIT_STAGE,
        "prompt": "documenter.md",
        "outputs": [AUDIT_ARTIFACT],
    })
    (root / "workflows" / f"{FIXTURE_WORKFLOW}.json").write_text(
        json.dumps(shipped, indent=2) + "\n", encoding="utf-8")
    return root


def build_target(tmp_path: Path, config: dict[str, object], *,
                 checkout: str | None = None,
                 extra_branches: tuple[str, ...] = ()) -> Path:
    """A target repository configured with `config` and nothing at a default.

    Every directory the configuration names is created at the configured
    path and nowhere else, so a read that ignored the configuration would
    find nothing rather than finding this repository's own defaults sitting
    where it looked.
    """
    root = tmp_path / "xyzzy-target"
    stories = root / str(config["stories_dir"])
    standards = root / str(config["standards_dir"])
    for directory in (stories, standards, root / "src"):
        directory.mkdir(parents=True)
    (root / ".harness").mkdir(exist_ok=True)
    (root / ".harness" / "config.yaml").write_text(_yaml_lines(config),
                                                   encoding="utf-8")
    (stories / f"{STORY_ID}.yaml").write_text(conftest.STORY, encoding="utf-8")
    (standards / "coding.md").write_text(
        f"# Coding Standards\n- {STANDARDS_MARKER}\n", encoding="utf-8")
    (standards / "testing.md").write_text(
        "# Testing Standards\n- test everything\n", encoding="utf-8")
    for relative in config.get("architecture_docs", []):  # type: ignore[union-attr]
        doc = root / str(relative)
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(f"# Architecture\n{ARCHITECTURE_MARKER}\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    # Named explicitly rather than inherited: `git init`'s default branch
    # varies by version and by the developer's own git configuration, and the
    # base-branch proof below decides on which branch HEAD is standing.
    _git(root, "branch", "-M", "main")
    for branch in extra_branches:
        _git(root, "branch", branch)
    if checkout:
        _git(root, "checkout", "-q", checkout)
    return root


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


def branches(root: Path) -> set[str]:
    listing = _git(root, "branch", "--format=%(refname:short)").stdout
    return set(listing.split())


class RecordingRunner:
    """Stands in for `agent_runner.run_agent`, recording every invocation.

    It writes each stage's declared artifacts so the run reaches completion,
    and it writes to whatever `log_path` it is handed — exactly as the real
    runner does — so `logs_dir` is observable as a file on disk rather than
    only as an argument.
    """

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.calls: list[dict] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None, max_budget_usd=None, suite_command=None):
        self.calls.append({
            "stage": stage, "prompt": prompt, "cwd": Path(cwd),
            "log_path": Path(log_path), "permission_mode": permission_mode,
            "model": model, "allowed_tools": allowed_tools,
        })
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"===== stage: {stage} =====\n")
        if stage == "implementer":
            _write_json(self.run_dir / "changed-files.json",
                        {"modified": ["src/app.py"], "created": [], "deleted": []})
            (self.run_dir / "implementation-summary.md").write_text(
                "Did the work.\n", encoding="utf-8")
        elif stage == "tester":
            _write_json(self.run_dir / "test-results.json", {
                "status": "passed", "tests_written": 1, "tests_run": 1,
                "tests_passed": 1, "tests_failed": 0, "failures": [],
            })
            _write_json(self.run_dir / "tester-changed-files.json",
                        {"modified": [], "created": ["tests/test_app.py"],
                         "deleted": []})
        elif stage == "verifier":
            _write_json(self.run_dir / "verification-result.json", PASS_VERDICT)
        elif stage == "documenter":
            (self.run_dir / "documentation-report.md").write_text(
                "No changes needed.\n", encoding="utf-8")
            _write_json(self.run_dir / "documenter-changed-files.json",
                        {"modified": [], "created": [], "deleted": []})
        elif stage == AUDIT_STAGE:
            (self.run_dir / AUDIT_ARTIFACT).write_text(
                "Audited.\n", encoding="utf-8")
        return AgentResult(ok=True, result_text=f"{stage} done")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@dataclass
class Run:
    """One completed fixture run, and everything a proof reads off it."""

    target: Path
    harness: Path
    config: dict
    run_dir: Path
    runner: RecordingRunner
    code: int
    values: dict = field(default_factory=dict)

    @property
    def stages(self) -> list[str]:
        return [call["stage"] for call in self.runner.calls]

    def prompt_for(self, stage: str) -> str:
        for call in self.runner.calls:
            if call["stage"] == stage:
                return call["prompt"]
        raise AssertionError(f"{stage} never ran; stages were {self.stages}")

    def argument(self, name: str) -> list:
        return [call[name] for call in self.runner.calls]

    @property
    def state(self) -> dict:
        return json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))


def start_run(tmp_path: Path, **overrides: object) -> Run:
    """Build the fixture and execute one story through the fake runner.

    Returns whatever the coordinator returned; callers that need a completed
    run use `complete_run`, which asserts on it.
    """
    checkout = overrides.pop("_checkout", None)
    extra_branches = tuple(overrides.pop("_branches", ()) or ())
    values = fixture_config(**overrides)
    harness = build_harness(tmp_path)
    target = build_target(tmp_path, values, checkout=checkout,
                          extra_branches=extra_branches)
    config = harness_config.load_config(target)
    run_dir = target / str(values["runs_dir"]) / STORY_ID
    runner = RecordingRunner(run_dir)
    code = story_coordinator.run_story(STORY_ID, harness, target, runner)
    return Run(target=target, harness=harness, config=config, run_dir=run_dir,
               runner=runner, code=code, values=values)


def complete_run(tmp_path: Path, **overrides: object) -> Run:
    run = start_run(tmp_path, **overrides)
    assert run.code == 0, (
        f"the fixture run did not complete (exit {run.code}); stages were "
        f"{run.stages}")
    assert run.state["status"] == "completed"
    return run


def clean_clone_record(run: Run) -> dict:
    """What the clean-clone path builds for the fixture's configuration.

    The command is *observed*, not executed: `verification_runner` names an
    executable that does not exist, so `run_clean_clone` reports a check that
    did not run, with the command it would have run and the runner it
    resolved, before any clone is built. That keeps the proof deterministic
    and free of any dependency on a second toolchain being installed.
    """
    artifact = "xyzzy-clean-clone-result.json"
    # The check is invoked by hand here rather than reached through a run —
    # this fixture's workflow strips the declaration, because `test_command`
    # names a command that does not exist. Its announcement therefore has no
    # declaring stage to name, and which stage it names is not what these
    # proofs are about, so the stage is taken off the fixture's own definition
    # rather than written down.
    definition = json.loads(
        (run.harness / "workflows" / f"{FIXTURE_WORKFLOW}.json").read_text(
            encoding="utf-8"))
    story_coordinator.clean_clone_check(
        run.run_dir, run.target, run.config, artifact,
        stage_name=definition["stages"][-1]["name"])
    return json.loads((run.run_dir / artifact).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# The declaration and its only reader
# --------------------------------------------------------------------------

EXPECTED_KEYS = (
    "allowed_tools", "architecture_docs", "base_branch", "branch_prefix",
    "census_command",
    "logs_dir", "model", "permission_mode", "runs_dir", "standards_dir",
    "stories_dir", "test_command", "test_selection_command", "tests_dir",
    "verification_runner", "workflow",
)


def test_declared_config_keys_returns_exactly_the_declared_names():
    assert set(DECLARED) == set(EXPECTED_KEYS)
    assert len(DECLARED) == len(EXPECTED_KEYS)


def test_the_schema_is_in_the_inventory_and_declares_no_key_the_harness_ignores():
    assert "harness-config" in schema_validator.shipped_schemas()
    schema = schema_validator.load_schema("harness-config")
    assert "additionalProperties" not in json.dumps(schema)
    assert schema_validator.unsupported_keywords(schema) == []
    # The schema's top-level claim made concrete, now that an undeclared key
    # refuses the run: this repository's own config carries nothing outside
    # the declared set, and the declared set is what the harness *reads*.
    assert [key for key in THIS_REPO_CONFIG if key not in DECLARED] == []


#: The three shapes `declared_config_keys` must raise on rather than degrade
#: to an empty or partial tuple, each constructed rather than described.
MALFORMED_SCHEMAS = {
    "missing": None,
    "unparseable": "{ not json",
    "not-an-object": "[]",
    "no-properties": '{"type": "object"}',
    "properties-not-an-object": '{"type": "object", "properties": []}',
    "properties-empty": '{"type": "object", "properties": {}}',
}


@pytest.mark.parametrize("case", sorted(MALFORMED_SCHEMAS))
def test_declared_config_keys_raises_rather_than_returning_a_partial_tuple(
        case, tmp_path):
    root = tmp_path / case
    (root / "schemas").mkdir(parents=True)
    text = MALFORMED_SCHEMAS[case]
    if text is not None:
        (root / "schemas" / "harness-config.schema.json").write_text(
            text, encoding="utf-8")
    with pytest.raises(ValueError) as raised:
        harness_config.declared_config_keys(root)
    assert "harness-config.schema.json" in str(raised.value)


def test_the_same_reader_returns_the_declared_names_from_a_copied_schema(tmp_path):
    """The positive control for the six refusals above.

    Each of them asserts a raise. That says nothing about whether the reader
    can succeed at all against a root it was handed, so one well-formed
    throwaway root is read here and must return exactly what this repository's
    does.
    """
    root = tmp_path / "well-formed"
    (root / "schemas").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "schemas" / "harness-config.schema.json",
                 root / "schemas" / "harness-config.schema.json")
    assert harness_config.declared_config_keys(root) == DECLARED


# --------------------------------------------------------------------------
# Coverage: the declared set against KEY_PROOFS, in both directions
# --------------------------------------------------------------------------


def coverage_problems(declared, proofs) -> list[str]:
    """What stops `proofs` from covering `declared` exactly.

    A function rather than a pair of inline assertions, so the comparison can
    be *shown* to report each direction against a constructed pair rather than
    only observed to be silent against the real one.
    """
    problems = []
    for key in sorted(set(declared) - set(proofs)):
        problems.append(f"{key} is declared in the schema and has no proof")
    for key in sorted(set(proofs) - set(declared)):
        problems.append(f"{key} has a proof and is not declared in the schema")
    return problems


def test_every_declared_key_has_a_proof_and_every_proof_names_a_declared_key():
    assert coverage_problems(DECLARED, KEY_PROOFS) == []


def test_the_coverage_comparison_reports_a_declared_key_with_no_proof():
    """The control for the first direction, constructed rather than argued."""
    declared = (*DECLARED, "xyzzy_fourteenth")
    assert coverage_problems(declared, KEY_PROOFS) == [
        "xyzzy_fourteenth is declared in the schema and has no proof"]


def test_the_coverage_comparison_reports_a_proof_naming_an_undeclared_key():
    """The control for the second direction."""
    proofs = dict(KEY_PROOFS)
    proofs["xyzzy_retired"] = Proof("test_nothing", BEHAVIOURAL)
    assert coverage_problems(DECLARED, proofs) == [
        "xyzzy_retired has a proof and is not declared in the schema"]


def test_every_proof_names_a_function_this_module_actually_defines():
    """Without this, a typo in a node id would make the mutation control lie.

    pytest exits non-zero when it collects nothing, and the mutation control
    below reads a non-zero exit as "the proof went red". A node id naming no
    function would therefore report a passing control for a proof that never
    ran.
    """
    defined = {node.name for node in ast.parse(
        Path(__file__).read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef)}
    assert {proof.node for proof in KEY_PROOFS.values()} <= defined


def test_the_three_runner_arguments_are_the_only_proofs_recorded_as_argument_list():
    """AC8's second half, as a fact of the mapping rather than as prose.

    `model`, `permission_mode` and `allowed_tools` are handed to the agent
    runner and are observable nowhere else — no file, no branch, no rendered
    artifact carries them — so their proof asserts on the invocation. Every
    other key changes something a run leaves behind, so nothing else is
    entitled to the weaker observation.
    """
    recorded = {key for key, proof in KEY_PROOFS.items()
                if proof.kind == ARGUMENT_LIST}
    assert recorded == {"model", "permission_mode", "allowed_tools"}
    assert {proof.kind for proof in KEY_PROOFS.values()} == {BEHAVIOURAL,
                                                             ARGUMENT_LIST}


# --------------------------------------------------------------------------
# Coverage: the declared set against what the harness actually reads
# --------------------------------------------------------------------------


def keys_read_in(path: Path) -> set[str]:
    """Every literal key read out of a `config` mapping in one source file.

    Both forms the harness uses: `config.get("key")` and `config["key"]`. A
    subscript through a *variable* is not a literal read and is not collected
    — `harness_config.load_config` builds the mapping with `config[key]`, and
    counting that would report the parser's own loop variable as a key.
    """
    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "config"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            found.add(node.args[0].value)
        if (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "config"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            found.add(node.slice.value)
    return found


def sources_to_scan(root: Path = REPO_ROOT) -> list[Path]:
    """Every file the scan reads: `orchestration/*.py` and all of `scripts/`.

    The scripts carry no `.py` suffix — they are entry points on PATH — so
    they are listed by iterating the directory rather than by globbing an
    extension, and parsed as Python text.
    """
    sources = sorted((root / "orchestration").glob("*.py"))
    sources += sorted(path for path in (root / "scripts").iterdir()
                      if path.is_file())
    return sources


def keys_read_under(paths) -> set[str]:
    found: set[str] = set()
    for path in paths:
        found |= keys_read_in(path)
    return found


def test_the_scan_reads_the_scripts_that_carry_no_py_suffix():
    scanned = sources_to_scan()
    extensionless = [path.name for path in scanned if path.suffix != ".py"]
    assert "l5-plan" in extensionless
    # l5-plan is the script that reads configuration; if the scan could not
    # parse it, the key it reads would be invisible. Since story-072 that is
    # `stories_dir` alone: no path in the script reads the configured workflow
    # key any longer, because the workflow a session renders against is the one
    # --workflow named or the one the developer confirmed. The key itself is
    # still declared and still read, by the coordinator, where a story artifact
    # naming no workflow resolves through it, and which the equality below over
    # every scanned source holds.
    assert keys_read_in(REPO_ROOT / "scripts" / "l5-plan") == {"stories_dir"}


def test_the_keys_the_harness_reads_and_the_keys_the_schema_declares_are_equal():
    read = keys_read_under(sources_to_scan())
    assert read == set(DECLARED)


def test_the_scan_reports_a_module_reading_a_key_the_schema_does_not_declare(
        tmp_path):
    """The control for the scan above, constructed rather than reasoned about.

    A scan that returns nothing new is worth nothing until it has been shown
    to return something. So a fourteenth key is planted in a synthetic module
    and the same comparison is run over it.
    """
    planted = tmp_path / "reads_a_fourteenth_key.py"
    planted.write_text(
        "def read(config):\n"
        '    return config.get("xyzzy_fourteenth"), config["xyzzy_fifteenth"]\n',
        encoding="utf-8")
    read = keys_read_under([planted])
    assert read == {"xyzzy_fourteenth", "xyzzy_fifteenth"}
    assert read - set(DECLARED) == {"xyzzy_fourteenth", "xyzzy_fifteenth"}


def test_the_scan_does_not_count_a_subscript_through_a_variable(tmp_path):
    """The other half of the scan's claim, also constructed.

    `harness_config.load_config` writes `config[key]` while building the
    mapping. Counting that would report `key` and `current_list` as configured
    keys, and the equality above would then have to be weakened to accommodate
    them — which is how a coverage rule stops meaning anything.
    """
    planted = tmp_path / "builds_a_mapping.py"
    planted.write_text(
        "def build(lines):\n"
        "    config = {}\n"
        "    for key, value in lines:\n"
        "        config[key] = value\n"
        "    return config\n",
        encoding="utf-8")
    assert keys_read_under([planted]) == set()
    # And over the real module, which both builds the mapping that way *and*
    # reads one key by name: exactly the literal read is counted, and neither
    # of the two variables the mapping is built through joins it.
    read = keys_read_in(REPO_ROOT / "orchestration" / "harness_config.py")
    assert read == {"tests_dir"}
    assert not read & {"key", "current_list"}


# --------------------------------------------------------------------------
# No varying value may be one the harness would have picked
# --------------------------------------------------------------------------


def decayed_values(varying: dict, fallbacks: dict, repo_config: dict) -> list[str]:
    """Which proof values would pass against a hardcoded literal.

    A value equal to the fallback proves nothing, because the code would
    produce it with the key deleted. A value equal to what this repository
    already configures proves nothing either, because that is the literal a
    hardcoding would most plausibly be.
    """
    problems = []
    for key in sorted(varying):
        if varying[key] == fallbacks.get(key):
            problems.append(f"{key} is set to the value the harness falls back to")
        if key in repo_config and varying[key] == repo_config[key]:
            problems.append(
                f"{key} is set to the value this repository already configures")
    return problems


def test_no_proof_value_is_a_default_or_this_repositorys_own_configured_value():
    assert decayed_values(VARYING, FALLBACKS, THIS_REPO_CONFIG) == []
    assert set(VARYING) == set(DECLARED) == set(FALLBACKS)


def test_every_varying_value_carries_the_distinctive_token():
    for key, value in sorted(VARYING.items()):
        rendered = " ".join(value) if isinstance(value, list) else str(value)
        assert TOKEN in rendered, key


@pytest.mark.parametrize("key", sorted(VARYING))
def test_the_decay_check_reports_a_proof_value_changed_to_its_fallback(key):
    """The first half of the control, for every key rather than for a sample.

    `None` is the fallback for the five keys the code reads with no default,
    and setting a proof value to it is the decay the check must report for
    those, exactly as a literal default is for the other eight.
    """
    decayed = dict(VARYING)
    decayed[key] = FALLBACKS[key]
    assert f"{key} is set to the value the harness falls back to" in \
        decayed_values(decayed, FALLBACKS, THIS_REPO_CONFIG)


@pytest.mark.parametrize(
    "key", sorted(k for k in VARYING if k in THIS_REPO_CONFIG))
def test_the_decay_check_reports_a_proof_value_set_to_this_repositorys_own(key):
    """The second half. `base_branch` and `model` are absent from this
    repository's configuration, so there is no own-value for them to decay to
    and the parametrization does not claim one."""
    decayed = dict(VARYING)
    decayed[key] = THIS_REPO_CONFIG[key]
    assert f"{key} is set to the value this repository already configures" in \
        decayed_values(decayed, FALLBACKS, THIS_REPO_CONFIG)


# --------------------------------------------------------------------------
# The ten behavioural proofs
# --------------------------------------------------------------------------


def test_branch_prefix_names_the_branch_the_run_creates_and_works_on(tmp_path):
    run = complete_run(tmp_path)
    assert run.state["branch"] == "xyzzy-branch/story-001"
    assert "xyzzy-branch/story-001" in branches(run.target)
    assert "story/story-001" not in branches(run.target)
    assert story_coordinator.story_branch(run.config, STORY_ID) == \
        "xyzzy-branch/story-001"


def test_base_branch_is_the_base_the_pre_flight_resolves_and_decides_on(
        tmp_path, capsys):
    """Both directions of the base pre-flight, against a configured base.

    The configured base is what `resolve_base` returns and what the pre-flight
    decides against. Standing anywhere else is refused by name; standing on it
    is accepted. With the key no longer read, the base resolves to `main` and
    both halves reverse — the refusal disappears and the acceptance becomes a
    refusal — so neither half can pass against a harness that ignores it.
    """
    away = start_run(tmp_path / "away", _branches=("xyzzy-base",))
    assert away.code == 1
    refusal = capsys.readouterr().err
    assert "xyzzy-base" in refusal
    assert "HEAD is on branch main" in refusal
    assert not (away.target / str(VARYING["runs_dir"])).exists()
    assert story_coordinator.resolve_base(away.target, away.config, None) == \
        "xyzzy-base"

    standing = complete_run(tmp_path / "standing", _branches=("xyzzy-base",),
                            _checkout="xyzzy-base")
    assert standing.state["branch"] == "xyzzy-branch/story-001"


def test_stories_dir_is_where_the_story_artifact_is_read_from(tmp_path):
    run = complete_run(tmp_path)
    assert (run.target / ".harness" / "xyzzy-stories" /
            f"{STORY_ID}.yaml").is_file()
    assert not (run.target / ".harness" / "stories").exists()
    # The story text the run actually read reached the stage prompts, so this
    # is the artifact at the configured path governing rather than merely
    # sitting there.
    assert "Sample story for coordinator tests" in run.prompt_for("implementer")


def test_runs_dir_is_where_the_run_state_is_written_and_read_back(tmp_path):
    run = complete_run(tmp_path)
    assert (run.target / ".harness" / "xyzzy-runs" / STORY_ID /
            "state.json").is_file()
    assert not (run.target / ".harness" / "runs").exists()
    # The status reader resolves the same directory from the same key, so a
    # run recorded under the configured path is a run `l5-status` can find.
    assert run_status._runs_dir(run.target) == \
        run.target / ".harness" / "xyzzy-runs"


def test_logs_dir_is_where_the_stage_log_is_written(tmp_path):
    run = complete_run(tmp_path)
    expected = run.target / ".harness" / "xyzzy-logs" / f"{STORY_ID}.log"
    assert expected.is_file()
    assert not (run.target / ".harness" / "logs").exists()
    assert run.argument("log_path") == [expected] * len(run.stages)


def test_standards_dir_is_where_the_injected_standards_are_read_from(tmp_path):
    run = complete_run(tmp_path)
    assert STANDARDS_MARKER in run.prompt_for("implementer")
    assert not (run.target / ".harness" / "standards").exists()


def test_architecture_docs_names_the_documents_injected_into_a_stage(tmp_path):
    run = complete_run(tmp_path)
    assert ARCHITECTURE_MARKER in run.prompt_for("implementer")
    assert "docs/xyzzy-architecture.md" in run.prompt_for("documenter")
    assert not (run.target / ".harness" / "docs").exists()


def test_workflow_names_the_definition_the_run_actually_executes(tmp_path):
    run = complete_run(tmp_path)
    # The stage no shipped definition declares. Its presence is what
    # distinguishes "the named definition was loaded" from "a definition with
    # the same stages as the shipped one was loaded".
    assert AUDIT_STAGE in run.stages
    assert run.stages == ["implementer", "tester", "documenter", "verifier",
                          AUDIT_STAGE]
    assert (run.run_dir / AUDIT_ARTIFACT).is_file()
    assert AUDIT_STAGE not in [
        stage["name"] for stage in conftest.shipped_workflow(
            REPO_ROOT, "story-workflow")["stages"]]


def test_tests_dir_is_the_location_the_workflow_and_the_prompt_are_governed_at(
        tmp_path):
    """The configured location governs both halves, observed rather than read.

    The fixture configures its tests somewhere no harness would guess, and the
    workflow definition names no directory at all — it carries the token. So
    the restriction the coordinator enforces can only have come from the
    configuration, and the same value has to reach the stage that writes the
    tests, or the stage is told to write them somewhere the coordinator does
    not govern.
    """
    run = complete_run(tmp_path)
    workflow = harness_config.load_workflow(run.harness, FIXTURE_WORKFLOW,
                                            run.config)
    assert story_coordinator.stage_restrictions(workflow["stages"]) == [
        ("implementer", "xyzzy-checks/")]
    assert "xyzzy-checks/" in run.prompt_for("tester")
    # The definition itself names no directory: what the restriction resolves
    # to is the configuration's answer and nothing else.
    definition = (run.harness / "workflows" / f"{FIXTURE_WORKFLOW}.json"
                  ).read_text(encoding="utf-8")
    assert "xyzzy-checks/" not in definition
    assert "{{tests_dir}}" in definition


def test_test_command_is_the_command_the_clean_clone_path_builds(tmp_path):
    run = complete_run(tmp_path)
    assert "xyzzy-runner --all" in run.prompt_for("implementer")
    record = clean_clone_record(run)
    # The configured command's own arguments, under the configured
    # runner: `--all` is the half that comes from `test_command`.
    assert record["command"] == "/xyzzy/bin/interpreter --all"


#: The test a stage's record nominates, for the selection proof below. It
#: names nothing that exists: what the proof observes is the command the
#: nomination was substituted into, which is built before anything is run.
NOMINATED_TEST = "xyzzy-nominated-test"


def nomination_record(run: Run) -> dict:
    """What the revert check builds when a record nominates a test.

    Observed rather than completed, for the reason `clean_clone_record` above
    is: `verification_runner` names an executable that does not exist, so the
    selector run reports a run that did not happen — carrying the command it
    would have run — before any clone is built. The check then falls through to
    the whole suite, which cannot run either, so the proof is deterministic and
    depends on no second toolchain.

    Invoked by hand rather than reached through a run, because this fixture's
    workflow strips the declaration that turns the check on; which stage
    declares it is not what this proof is about, so the stage is taken off the
    fixture's own definition rather than written down.
    """
    artifact = "xyzzy-revert-check-result.json"
    definition = json.loads(
        (run.harness / "workflows" / f"{FIXTURE_WORKFLOW}.json").read_text(
            encoding="utf-8"))
    governed = [str(run.values["tests_dir"])]
    baseline = story_coordinator.capture_stage_baseline(
        run.run_dir, run.target, "xyzzy-revert-baseline", "xyzzy-stage",
        governed, accounted_for=set())
    story_coordinator.revert_check(
        run.run_dir, run.target, run.config, artifact,
        (f"{run.values['tests_dir']}test_xyzzy.py",), baseline,
        stage_name=definition["stages"][-1]["name"],
        nomination=NOMINATED_TEST)
    return json.loads((run.run_dir / artifact).read_text(encoding="utf-8"))


def test_test_selection_command_is_what_a_nomination_is_substituted_into(tmp_path):
    """The configured command, with its substitution point replaced and its
    first word swapped for the configured runner.

    `--only` is the half that can only have come from `test_selection_command`,
    and the nominated test standing where `{test}` stood is the substitution
    itself. A harness that had stopped reading the key would record a check
    that fell through with no command built at all.
    """
    run = complete_run(tmp_path)
    record = nomination_record(run)
    assert record["nomination"]["command"] == \
        f"/xyzzy/bin/interpreter --only {NOMINATED_TEST}"
    assert record["nomination"]["short_circuited"] is False
    assert record["nomination"]["test"] == NOMINATED_TEST


def census_record(run: Run) -> dict:
    """What the census check builds for the fixture's configuration.

    Observed rather than completed, for the reason `clean_clone_record` above
    is: the configured census command names a program that does not exist, so
    the check reports a census it could not take — carrying the command it was
    asked to run and the reason it could not. That keeps the proof
    deterministic and free of any dependency on a second toolchain.

    Invoked by hand rather than reached through a run, because no shipped
    definition declares the census on the stages this fixture's workflow
    carries; which stage declares it is not what this proof is about, so the
    stage is taken off the fixture's own definition rather than written down.
    """
    artifact = "xyzzy-census-result.json"
    definition = json.loads(
        (run.harness / "workflows" / f"{FIXTURE_WORKFLOW}.json").read_text(
            encoding="utf-8"))
    governed = [str(run.values["tests_dir"])]
    baseline = story_coordinator.capture_stage_baseline(
        run.run_dir, run.target, "xyzzy-stage-baseline", "xyzzy-stage",
        governed, accounted_for=set())
    story_coordinator.suite_census_check(
        run.run_dir, run.target, run.config, artifact, governed, baseline,
        stage_name=definition["stages"][-1]["name"])
    return json.loads((run.run_dir / artifact).read_text(encoding="utf-8"))


def test_census_command_is_the_command_the_suite_census_runs(tmp_path):
    run = complete_run(tmp_path)
    record = census_record(run)
    assert record["command"] == "xyzzy-census --count"
    assert record["ran"] is False
    assert "xyzzy-census" in record["reason"]


def test_verification_runner_is_the_executable_the_check_resolves(tmp_path):
    run = complete_run(tmp_path)
    record = clean_clone_record(run)
    assert record["runner"] == "/xyzzy/bin/interpreter"
    assert record["ran"] is False
    assert "/xyzzy/bin/interpreter" in record["reason"]


# --------------------------------------------------------------------------
# The three argument-list proofs
# --------------------------------------------------------------------------


def test_model_is_the_model_every_stage_invocation_carries(tmp_path):
    run = complete_run(tmp_path)
    assert run.argument("model") == ["xyzzy-model"] * len(run.stages)


def test_permission_mode_is_the_mode_every_stage_invocation_carries(tmp_path):
    run = complete_run(tmp_path)
    assert run.argument("permission_mode") == ["xyzzyPrompt"] * len(run.stages)


def test_allowed_tools_reaches_both_the_runner_and_the_rendered_prompt(tmp_path):
    """Both sites that pass the configured grants, in one proof.

    The coordinator reads `allowed_tools` twice: once into the invocation it
    builds for the runner, and once into the context the stage's prompt is
    rendered from. A site left reading a hardcoded list would fail exactly one
    of these two assertions, so both are here.
    """
    run = complete_run(tmp_path)
    assert run.argument("allowed_tools") == [["Bash(xyzzy:*)"]] * len(run.stages)
    assert "- Bash(xyzzy:*)" in run.prompt_for("implementer")


# --------------------------------------------------------------------------
# The mutation control: every proof is run against a harness that stopped
# reading its key, and required to go red
# --------------------------------------------------------------------------

#: What the throwaway root needs to run one proof node: the code under
#: mutation, the schema the module reads its key set from, the workflow,
#: prompt and rule files a run loads, this repository's config file (the
#: module reads it to check no proof value coincides with it), and the two
#: test files. Copying only this keeps collection there to one module.
COPIED_TREES = ("orchestration", "schemas", "workflows", "prompts", "rules")
COPIED_TESTS = ("conftest.py", MODULE_NAME)


def harness_copy(tmp_path: Path) -> Path:
    """A real, runnable copy of the parts of this harness a proof needs.

    Copied rather than symlinked: every module here resolves its own root as
    `Path(__file__).resolve().parents[1]`, and `resolve()` follows a symlink
    straight back to this repository, which would make every mutation below
    invisible.
    """
    root = tmp_path / "throwaway-harness"
    ignore = shutil.ignore_patterns("__pycache__")
    for directory in COPIED_TREES:
        shutil.copytree(REPO_ROOT / directory, root / directory, ignore=ignore)
    (root / ".harness").mkdir()
    shutil.copy2(REPO_ROOT / ".harness" / "config.yaml",
                 root / ".harness" / "config.yaml")
    (root / "tests").mkdir()
    for name in COPIED_TESTS:
        shutil.copy2(TESTS_DIR / name, root / "tests" / name)
    return root


def apply_mutation(root: Path, key: str) -> None:
    """Replace `key`'s read with the literal the harness falls back to.

    Every anchor must occur, so a mutation whose target has moved fails as
    itself rather than as a mutant that silently changed nothing and then
    reported the proof green — which would be a control asserting the
    opposite of what it means to.
    """
    for relative, old, new in MUTATIONS[key]:
        path = root / relative
        source = path.read_text(encoding="utf-8")
        assert old in source, (key, relative, old)
        path.write_text(source.replace(old, new), encoding="utf-8")


def run_nodes(root: Path, *nodes: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *nodes],
        cwd=root, capture_output=True, text=True)


def test_the_pristine_copy_runs_every_proof_green(tmp_path):
    """The positive control the whole mutation control rests on.

    Each case below asserts that a mutated copy goes red. That means nothing
    unless an unmutated copy goes green for the right reason, so every proof
    node runs here in a copy with nothing changed.
    """
    root = harness_copy(tmp_path)
    result = run_nodes(root, *sorted(
        proof.node_id for proof in KEY_PROOFS.values()))
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{len(KEY_PROOFS)} passed" in result.stdout


@pytest.mark.parametrize("key", sorted(KEY_PROOFS))
def test_the_proof_for_each_key_goes_red_when_that_key_stops_being_read(
        key, tmp_path):
    """For every declared key, not for a sample of them.

    This is the assertion that makes the twelve above mean something. A proof
    that configured its key and then asserted nothing about its effect would
    satisfy the coverage checks, satisfy the no-default check, and pass in a
    copy that no longer reads the key at all — and only this reports it.
    """
    root = harness_copy(tmp_path)
    apply_mutation(root, key)
    node = KEY_PROOFS[key].node_id
    result = run_nodes(root, node)
    assert result.returncode != 0, (
        f"{node} still passed with {key}'s read replaced by the literal the "
        f"harness falls back to, so it does not prove {key} is obeyed:\n"
        f"{result.stdout}")
    # Non-zero is also what pytest returns when it collected nothing, so the
    # red is required to be one test that ran and failed rather than a node
    # that was never found.
    assert "1 failed" in result.stdout, result.stdout + result.stderr


# --------------------------------------------------------------------------
# The inventory holds the new schema, and would not hold it silently
# --------------------------------------------------------------------------

INVENTORY_NODES = (
    "tests/test_schema_validator.py::test_shipped_schemas_are_exactly_the_named_ones",
    "tests/test_artifact_schemas.py::test_schemas_directory_holds_exactly_the_named_schemas",
)

INVENTORY_TESTS = ("conftest.py", "test_schema_validator.py",
                   "test_artifact_schemas.py")


def inventory_copy(tmp_path: Path) -> Path:
    root = tmp_path / "inventory-harness"
    ignore = shutil.ignore_patterns("__pycache__")
    for directory in ("orchestration", "schemas"):
        shutil.copytree(REPO_ROOT / directory, root / directory, ignore=ignore)
    (root / "tests").mkdir()
    for name in INVENTORY_TESTS:
        shutil.copy2(TESTS_DIR / name, root / "tests" / name)
    return root


def test_the_inventory_agrees_in_both_directions_with_the_new_schema_present(
        tmp_path):
    result = run_nodes(inventory_copy(tmp_path), *INVENTORY_NODES)
    assert result.returncode == 0, result.stdout + result.stderr


def test_removing_the_new_schemas_manifest_line_turns_the_inventory_red(tmp_path):
    """The control: the inventory accepts the file because it is *named*.

    Without this, "the manifest names it" and "the inventory happens not to
    look" are the same green.
    """
    root = inventory_copy(tmp_path)
    manifest = root / "schemas" / "manifest.json"
    names = json.loads(manifest.read_text(encoding="utf-8"))["schemas"]
    assert "harness-config" in names
    manifest.write_text(
        json.dumps({"schemas": [n for n in names if n != "harness-config"]},
                   indent=2) + "\n", encoding="utf-8")
    result = run_nodes(root, *INVENTORY_NODES)
    assert result.returncode != 0, result.stdout + result.stderr


# --------------------------------------------------------------------------
# The harness itself is unchanged
# --------------------------------------------------------------------------

#: The files this story states carry no edit. Compared over the story's own
#: commit range through the shared resolution, never as the working tree
#: against HEAD: the coordinator commits the tree when a run completes, so a
#: HEAD comparison reports clean for every path the moment the story commits.
UNCHANGED = (
    ".harness/config.yaml",
    "orchestration/story_coordinator.py",
    "orchestration/context_assembler.py",
    "orchestration/run_status.py",
    "orchestration/schema_validator.py",
    "scripts/",
    "workflows/",
    "prompts/",
)


@pytest.mark.parametrize("relative", UNCHANGED)
def test_this_story_left_the_harnesss_own_behaviour_alone(relative, tmp_path):
    """Restated over a story this test builds rather than recalled out of this
    repository's own commit graph.

    The claim is unchanged: a story that touches none of these paths leaves an
    empty diff over its own range. What moved is where the evidence comes from.
    Bounded at this repository's history the assertion re-stated a frozen past
    fact whose answer moved whenever something was committed, renamed, squashed
    or rebased — a rename gives a path a new add-commit, and every assertion
    bounded by that path's range then goes silently, vacuously green. Here the
    story is constructed, the predicate is the same predicate, and the control
    beside it shows the same call reporting the violation.
    """
    respecting = conftest.constructed_story(tmp_path, respected=[relative],
                                            name="scope-respected")
    assert conftest.constructed_story_diff(respecting, [relative]) == ""
    violating = conftest.constructed_story(tmp_path, violated=[relative],
                                           name="scope-violated")
    assert conftest.constructed_story_diff(violating, [relative]) != ""


def test_the_unchanged_comparison_can_tell_a_changed_path_apart(tmp_path):
    """The control for the absence above.

    An empty diff is what a comparison bounded at the wrong commits reports
    too, so the same resolution is run against a synthetic repository in which
    the file really did change, and must report it.
    """
    repo = tmp_path / "synthetic"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "tests").mkdir()
    (repo / "subject.py").write_text("original\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "before the story")
    (repo / "subject.py").write_text("edited by the story\n", encoding="utf-8")
    (repo / "tests" / MODULE_NAME).write_text("# the story's own module\n",
                                              encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the story's run commit")

    changed = conftest.story_diff(["subject.py"],
                                  validation_file=repo / "tests" / MODULE_NAME,
                                  repo=repo)
    assert "edited by the story" in changed
    unchanged = conftest.story_diff(["tests/"],
                                    validation_file=repo / "tests" / MODULE_NAME,
                                    repo=repo, diff_filter="M")
    assert unchanged == ""


def test_a_key_the_schema_does_not_declare_refuses_the_run(tmp_path):
    """The declaration is also the run-time check, since story-043.

    Constructed rather than argued: the same fixture target that completes
    without it is given a key the schema does not declare, and the run is
    refused instead. The control is `complete_run` everywhere else in this
    module — the fixture is otherwise identical.
    """
    run = start_run(tmp_path, xyzzy_undeclared_key="something-nobody-reads")
    assert "xyzzy_undeclared_key" not in DECLARED
    assert run.code == 1
    assert run.stages == []
    assert not run.run_dir.exists()
