"""The suite run in a clean clone, and the clone it is run in.

Two stories share that subject and this module validates both, which is why
it declares two origins in `conftest.STORY_ORIGINS` and every story-range
call below names the one it means:

- story-014: the suite run in a clean clone before the story commits.
- story-033: that clone is built over git's normal transport.

story-014. The verifier runs the suite in the working tree, the one
environment where the story's own commit does not exist yet — `_complete`
commits the tree after the documenter and after every check the workflow
performs. These tests hold the second run the coordinator now does in that
gap: a fresh clone of the repository with the story committed into it, run
after the verifier passes and before the documenter starts.

The central fixture is the bug that story exists for, reconstructed rather
than described. A test that resolves its baseline out of `HEAD` is correct
while the story is uncommitted and wrong the moment the story *is* `HEAD`;
it reports green to the verifier and red to CI. `BUGGY_TEST_COMMAND` below
is exactly that shape, and the pair of routing tests asserts the check
tells it apart from `CORRECT_TEST_COMMAND`, which reads the working tree
and is right in both environments. The check must catch the first and let
the second through — a check that failed everything would be no check.

story-033 is about how the clone underneath all of that is obtained, and
its half of this module begins where the section banner says so.

Nothing here invokes a model: every run goes through a fake agent runner,
and every clone source is a local filesystem path.
"""
import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import context_assembler
import harness_config
import schema_validator
import story_coordinator
from agent_runner import AgentResult
from conftest import first_retry_route, load_mutant
import conftest

#: The two stories this module validates, as `conftest.STORY_ORIGINS`
#: declares them. They are the module's recorded lineage; since story-053 the
#: scope assertions below are restated over a story each test builds, so
#: nothing here resolves a range out of this repository's own commit graph.
STORY_014 = "tests/test_story_014_validation.py"
STORY_033 = "tests/test_story_033_validation.py"

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
COORDINATOR_PATH = Path(story_coordinator.__file__)
COORDINATOR_SOURCE = COORDINATOR_PATH.read_text(encoding="utf-8")
#: The workflow these runs execute, assembled by the builder in
#: `tests/conftest.py` rather than resolved out of what this repository
#: deploys. story-048 made the change: the subject here is *the clean-clone
#: check* — that the coordinator runs the suite in a fresh clone of the
#: committed tree, writes what it found, and routes a failure back to the stage
#: the declaration names — and the stage list is an input to that question. The
#: declaration is what the check turns on, and a built workflow states it as
#: exactly as a deployed one does.
#:
#: Three assertions further down keep the shipped harness root, because their
#: subject really is what this repository ships: that its implementer template
#: carries the placeholder, and that every template it ships renders without a
#: leftover. They take `shipped_harness_root` rather than `harness_root`.
WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        outputs=(conftest.TEST_RESULTS, conftest.TESTER_CHANGED_FILES),
        changed_files=conftest.TESTER_CHANGED_FILES,
        schemas={conftest.TEST_RESULTS: "test-results",
                 conftest.TESTER_CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        outputs=(conftest.DOCUMENTATION_REPORT,
                 conftest.DOCUMENTER_CHANGED_FILES),
        changed_files=conftest.DOCUMENTER_CHANGED_FILES,
        schemas={conftest.DOCUMENTER_CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        clean_clone={"result": conftest.CLEAN_CLONE_RESULT,
                     "retry_stage": conftest.StageRef(0)},
        retry_routing={"implementation-defect": {
            "stage": conftest.StageRef(0),
            "when": "the behaviour the story asked for is missing"}}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="clean-clone-workflow",
)
STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, DOCUMENTING, VERIFYING = STAGE_NAMES
VERIFIER_STAGE = next(s for s in WORKFLOW["stages"] if "clean_clone" in s)


@pytest.fixture
def configured_workflow() -> str:
    """Point the shared target fixture at the definition built above."""
    return WORKFLOW["name"]


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A harness root carrying that definition, so a converted case drives a
    real coordinator loading a real file."""
    return conftest.materialize_workflow(WORKFLOW,
                                         tmp_path / "clean-clone-harness")


@pytest.fixture
def shipped_harness_root() -> Path:
    """This repository, for the three assertions whose subject is what it
    ships rather than what a run happens to be driven by."""
    return REPO_ROOT
#: Since story-028 the clean-clone declaration names both artifacts of the
#: check — the result it writes and the stage a failure routes to — so the
#: result name is read off `result` rather than off the bare declaration.
ARTIFACT = VERIFIER_STAGE["clean_clone"]["result"]
#: The retry category a failing verdict names, read off the loaded workflow's
#: routing table, which replaced the constant route in story-028.
RETRY_CATEGORY, _RETRY_STAGE = first_retry_route(WORKFLOW)
MAX_RETRIES = json.loads(
    (REPO_ROOT / "rules" / "execution-rules.json").read_text(encoding="utf-8")
)["max_retries"]

SCHEMA = schema_validator.load_schema("clean-clone-result")
HISTORY_SCHEMA = schema_validator.load_schema("execution-history")

LINE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] .*$")

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}
FAIL = {"status": "failed",
        "blocking_issues": [{"severity": "high", "issue": "sample behavior missing",
                             "location": "src/app.py",
                             "required_behavior": "sample behavior exists"}],
        "unverified": [], "retry_recommended": True,
        "retry_target": RETRY_CATEGORY}

#: The marker the fake implementer writes into the target's working tree.
#: It stands in for a story's change: uncommitted in the target, committed
#: in the clone, which is the whole difference the check exists to see.
MARKER = "STORY_MARKER"

#: A test that resolves its baseline as the HEAD blob. Green in the working
#: tree, where HEAD predates the story; red once the story is HEAD. This is
#: story-011's differential-baseline defect, reduced to one command.
BUGGY_TEST_COMMAND = (
    f"sh -c 'if git show HEAD:src/app.py | grep -q {MARKER}; then exit 1; fi'"
)
#: The same question asked of the working tree instead of the HEAD blob:
#: right in both environments.
CORRECT_TEST_COMMAND = f"sh -c 'grep -q {MARKER} src/app.py'"

#: A stand-in executable the configured command can be pointed at. It accepts
#: whatever arguments it is handed and exits zero, so a test that only needs
#: "the configured runner is what ran" has one answer and no second variable.
FAKE_INTERPRETER = """\
#!/bin/sh
exit 0
"""


# --------------------------------------------------------------------------
# Fixture plumbing
# --------------------------------------------------------------------------


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def commit_setup(target_root: Path, message: str) -> None:
    """Commit setup a test made after the fixture built the repository.

    story-021's clean-tree pre-flight refuses a run whose target tree already
    holds work no stage produced, and a test's configuration is exactly that:
    part of the repository the run starts *from*, not something the run is
    meant to commit. Committing it is what makes the setup a fact about the
    target rather than uncommitted work sitting in it, so every assertion
    below keeps its subject and its strictness.
    """
    subprocess.run(["git", "add", "-A"], cwd=target_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=target_root,
                   check=True)


def configure(target_root: Path, **overrides) -> None:
    """Rewrite the target's config keys, adding those it does not carry."""
    lines = (target_root / ".harness" / "config.yaml").read_text(
        encoding="utf-8").splitlines()
    for key, value in overrides.items():
        rendered = f"{key}: {value}"
        for index, line in enumerate(lines):
            if line.startswith(f"{key}:"):
                lines[index] = rendered
                break
        else:
            lines.append(rendered)
    (target_root / ".harness" / "config.yaml").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    commit_setup(target_root, "configure the target for this test")


def install_interpreter(target_root: Path, rel: str) -> str:
    """Drop the stand-in interpreter at `rel` inside the target repository."""
    path = target_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FAKE_INTERPRETER, encoding="utf-8")
    path.chmod(0o755)
    return rel


def install_runner(target_root: Path, rel: str, *, status: int,
                   argv_log: Path) -> str:
    """Drop a verification runner that is an interpreter of nothing at `rel`.

    story-041 removed the assumption that the executable running the suite is
    a Python interpreter, and this is the executable that holds it removed: it
    evaluates no source, it records the arguments it was handed into
    `argv_log`, and it exits with the status baked into it. The status is a
    parameter rather than a constant so "the record carries the suite's own
    exit code" is a fact a test chose, not the zero every command returns when
    nothing went wrong.
    """
    path = target_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {argv_log}\nexit {status}\n",
        encoding="utf-8")
    path.chmod(0o755)
    return rel


def run_dir_of(target_root: Path, story_id: str = "story-001") -> Path:
    return target_root / ".harness" / "runs" / story_id


def log_lines(run_dir: Path) -> list[str]:
    return (run_dir / "events.log").read_text(encoding="utf-8").splitlines()


def history_of(run_dir: Path) -> list[dict]:
    return json.loads(
        (run_dir / "execution-history.json").read_text(encoding="utf-8"))


def read_state(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text(encoding="utf-8"))


def record_of(run_dir: Path, artifact: str = ARTIFACT) -> dict:
    return json.loads((run_dir / artifact).read_text(encoding="utf-8"))


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=True,
    ).stdout


class Runner:
    """A fake agent runner that writes each stage's declared artifacts.

    The implementer also edits the target's working tree, which is what
    gives the clean-clone check something to commit: `src/app.py` gains the
    marker and an untracked `probe.txt` and an ignored `ignored/secret.txt`
    appear beside it.
    """

    def __init__(self, target_root: Path, verdicts: list[dict],
                 story_id: str = "story-001"):
        self.target_root = target_root
        self.run_dir = run_dir_of(target_root, story_id)
        self.verdicts = list(verdicts)
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None, max_budget_usd=None):
        self.calls.append(stage)
        self.prompts = getattr(self, "prompts", {})
        self.prompts.setdefault(stage, []).append(prompt)
        if stage == WRITING:
            (self.target_root / "src" / "app.py").write_text(
                f"print('hello')\n# {MARKER}\n", encoding="utf-8")
            (self.target_root / "probe.txt").write_text("probe\n", encoding="utf-8")
            (self.target_root / "ignored").mkdir(exist_ok=True)
            (self.target_root / "ignored" / "secret.txt").write_text(
                "secret\n", encoding="utf-8")
            write_json(self.run_dir / conftest.CHANGED_FILES, {
                "modified": ["src/app.py"], "created": ["probe.txt"], "deleted": [],
            })
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Did the work.\n", encoding="utf-8")
        elif stage == VALIDATING:
            write_json(self.run_dir / conftest.TEST_RESULTS, {
                "status": "passed", "tests_written": 2, "tests_run": 5,
                "tests_passed": 5, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES, {
                "modified": [], "created": ["tests/test_app.py"], "deleted": [],
            })
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
        elif stage == DOCUMENTING:
            (self.run_dir / conftest.DOCUMENTATION_REPORT).write_text(
                "No changes needed.\n", encoding="utf-8")
            write_json(self.run_dir / conftest.DOCUMENTER_CHANGED_FILES,
                       {"modified": [], "created": [], "deleted": []})
        return AgentResult(ok=True, result_text=f"{stage} done")


@pytest.fixture
def story_target(target_root: Path) -> Path:
    """The shared target with an ignore rule the clone must honor."""
    (target_root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=target_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "ignore rules"],
                   cwd=target_root, check=True)
    return target_root


def dirty_target(target_root: Path) -> Path:
    """A target carrying an uncommitted story: edits, untracked and ignored."""
    (target_root / "src" / "app.py").write_text(
        f"print('hello')\n# {MARKER}\n", encoding="utf-8")
    (target_root / "probe.txt").write_text("probe\n", encoding="utf-8")
    (target_root / "ignored").mkdir(exist_ok=True)
    (target_root / "ignored" / "secret.txt").write_text("secret\n", encoding="utf-8")
    return target_root


def clean_clone(target_root: Path, destination: Path, command: str,
                runner: str | None = None):
    return story_coordinator.run_clean_clone(
        target_root, command, runner, destination)


# --------------------------------------------------------------------------
# The fixture really is the gap: green in the tree, red once committed
# --------------------------------------------------------------------------


def test_the_reconstructed_bug_passes_in_the_working_tree(story_target):
    """The premise every routing test below rests on. If this command failed
    in the working tree the verifier would already have caught it and the
    check would be proving nothing."""
    dirty_target(story_target)
    result = subprocess.run(
        ["sh", "-c", f"if git show HEAD:src/app.py | grep -q {MARKER}; then exit 1; fi"],
        cwd=story_target, capture_output=True, text=True,
    )
    assert result.returncode == 0


def test_the_reconstructed_bug_fails_once_the_story_is_committed(
    story_target, tmp_path,
):
    dirty_target(story_target)
    result = clean_clone(story_target, tmp_path / "scratch", BUGGY_TEST_COMMAND)
    assert result.ran is True
    assert result.exit_code != 0


def test_the_corrected_baseline_passes_in_both_environments(story_target, tmp_path):
    """The check distinguishes the two rather than failing everything."""
    dirty_target(story_target)
    assert subprocess.run(
        ["sh", "-c", f"grep -q {MARKER} src/app.py"], cwd=story_target,
    ).returncode == 0
    result = clean_clone(story_target, tmp_path / "scratch", CORRECT_TEST_COMMAND)
    assert result.ran is True
    assert result.exit_code == 0


# --------------------------------------------------------------------------
# How the environment is built
# --------------------------------------------------------------------------


def test_the_story_is_present_in_the_clone_as_a_commit(story_target, tmp_path):
    """Not as pending edits: a test reading git history must see it."""
    dirty_target(story_target)
    clean_clone(story_target, tmp_path / "scratch", "sh -c 'true'")
    clone = tmp_path / "scratch" / "clone"

    assert MARKER in git(clone, "show", "HEAD:src/app.py")
    assert git(clone, "show", "HEAD:probe.txt").strip() == "probe"
    assert git(clone, "status", "--porcelain").strip() == ""


def test_files_gitignore_excludes_do_not_reach_the_clone(story_target, tmp_path):
    """The clone holds the set `_complete`'s `git add -A` would commit."""
    dirty_target(story_target)
    clean_clone(story_target, tmp_path / "scratch", "sh -c 'true'")
    clone = tmp_path / "scratch" / "clone"

    assert not (clone / "ignored").exists()
    assert not (clone / ".harness" / "runs").exists()
    assert (story_target / "ignored" / "secret.txt").is_file()


def test_the_clone_source_is_a_local_path_and_never_a_url():
    """By construction rather than by observation: the argument handed to
    `git clone` is the target root, and no coordinator literal spells a
    remote."""
    body = ast.unparse(
        next(node for node in ast.walk(ast.parse(COORDINATOR_SOURCE))
             if isinstance(node, ast.FunctionDef) and node.name == "_build_clone"))
    assert "'git', 'clone'" in body
    assert "str(target_root)" in body
    for node in ast.walk(ast.parse(COORDINATOR_SOURCE)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "://" not in node.value, node.value
            assert "git@" not in node.value, node.value


def test_the_target_repository_is_not_mutated_by_the_check(story_target, tmp_path):
    dirty_target(story_target)
    before = (
        git(story_target, "rev-parse", "HEAD"),
        git(story_target, "status", "--porcelain"),
        git(story_target, "branch", "--format=%(refname)"),
        git(story_target, "stash", "list"),
        git(story_target, "diff", "--cached"),
    )
    clean_clone(story_target, tmp_path / "scratch", BUGGY_TEST_COMMAND)
    after = (
        git(story_target, "rev-parse", "HEAD"),
        git(story_target, "status", "--porcelain"),
        git(story_target, "branch", "--format=%(refname)"),
        git(story_target, "stash", "list"),
        git(story_target, "diff", "--cached"),
    )
    assert before == after


def test_a_gitignored_interpreter_directory_is_available_in_the_clone(
    story_target, tmp_path,
):
    """A virtualenv is gitignored and therefore absent from a fresh clone, so
    the configured interpreter path would not resolve there."""
    (story_target / ".gitignore").write_text("ignored/\n.venv/\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "ignore the venv"],
                   cwd=story_target, check=True)
    install_interpreter(story_target, ".venv/bin/fakepy")
    dirty_target(story_target)

    result = clean_clone(story_target, tmp_path / "scratch", ".venv/bin/fakepy -m pytest")
    clone = tmp_path / "scratch" / "clone"

    assert result.exit_code == 0, result.output_tail
    assert (clone / ".venv" / "bin" / "fakepy").is_file()
    # Linked after the commit and excluded inside the clone, so the clone's
    # working tree reports nothing the target's does not.
    assert git(clone, "status", "--porcelain").strip() == ""


# --------------------------------------------------------------------------
# The command and the interpreter
# --------------------------------------------------------------------------


def test_no_test_command_string_appears_in_orchestration_code():
    """story-040 adds the one module whose deliverable *is* to name a
    toolchain: harness_source.py declares the stack tokens its scan looks
    for, `pytest` among them, and cannot declare them without spelling
    them. It is exempt by name, and the exemption is held shut from both
    sides — that module must actually name `pytest`, or the exemption is
    stale, and it is still held to the other two fragments — so no other
    module gains any latitude."""
    declares_the_tokens = "harness_source.py"
    declaring = REPO_ROOT / "orchestration" / declares_the_tokens
    assert declaring.is_file(), declares_the_tokens
    declared = declaring.read_text(encoding="utf-8")
    assert "pytest" in declared
    for fragment in ("-m pytest", "unittest"):
        assert fragment not in declared, f"{declares_the_tokens} names {fragment}"

    for source in (REPO_ROOT / "orchestration").glob("*.py"):
        if source.name == declares_the_tokens:
            continue
        text = source.read_text(encoding="utf-8")
        for fragment in ("pytest", "-m pytest", "unittest"):
            assert fragment not in text, f"{source.name} names {fragment}"


def test_the_configured_command_is_what_runs(story_target, tmp_path):
    dirty_target(story_target)
    marker = tmp_path / "it-ran"
    result = clean_clone(
        story_target, tmp_path / "scratch", f"sh -c 'echo ran > {marker}'")
    assert result.exit_code == 0
    assert marker.read_text(encoding="utf-8").strip() == "ran"
    assert result.command == f"sh -c 'echo ran > {marker}'"


def test_the_configured_verification_runner_replaces_the_commands_first_word(
    story_target, tmp_path,
):
    """Pointed at an executable that is not the one the harness runs under,
    the record names that one rather than the command's own first word."""
    install_interpreter(story_target, "fakepy")
    dirty_target(story_target)

    result = clean_clone(
        story_target, tmp_path / "scratch", ".venv/bin/python -m pytest -q",
        "./fakepy")

    assert result.ran is True
    assert result.exit_code == 0
    assert result.runner == "./fakepy"
    assert result.command == "./fakepy -m pytest -q"


def test_the_command_keeps_its_arguments_when_the_interpreter_is_replaced(
    story_target, tmp_path,
):
    install_interpreter(story_target, "fakepy")
    dirty_target(story_target)
    result = clean_clone(
        story_target, tmp_path / "scratch", "somepython -m pytest tests/ -q", "./fakepy")
    assert result.command == "./fakepy -m pytest tests/ -q"


def test_an_absent_verification_runner_falls_back_to_the_commands_own(
    story_target, tmp_path,
):
    install_interpreter(story_target, "fakepy")
    dirty_target(story_target)
    result = clean_clone(story_target, tmp_path / "scratch", "./fakepy -m pytest", None)
    assert result.ran is True
    assert result.runner == "./fakepy"
    assert result.command == "./fakepy -m pytest"


def test_a_verification_runner_that_does_not_exist_refuses_rather_than_falls_back(
    story_target, tmp_path,
):
    """A check that quietly runs the wrong executable is worse than one that
    refuses."""
    dirty_target(story_target)
    result = clean_clone(
        story_target, tmp_path / "scratch", "sh -c 'true'", ".venv999/bin/python")

    assert result.ran is False
    assert ".venv999/bin/python" in result.reason
    assert result.exit_code is None
    assert result.runner == ".venv999/bin/python"
    assert not (tmp_path / "scratch" / "clone").exists()


def test_a_command_whose_first_word_is_not_an_interpreter_runs_as_written(
    story_target, tmp_path,
):
    """The configured test command is the target's own, whatever it is, and
    an unset runner leaves it running exactly as written."""
    dirty_target(story_target)
    result = clean_clone(story_target, tmp_path / "scratch", "sh -c 'true'", None)
    assert result.ran is True
    assert result.runner == "sh"
    assert result.command == "sh -c true"


# --------------------------------------------------------------------------
# story-041: the runner is not assumed to be an interpreter of any language
#
# The three tests above point the check at a stand-in that happens to be a
# shell script, but they assert about the *command* the check builds. What
# story-041 claims is stronger and about the *record*: a target whose
# configured runner is not a Python interpreter gets a correct one — the
# executable the configuration named recorded as the runner, the check
# recorded as having run, and the exit code tracking the suite's own.
#
# "The run did not raise" is not that claim, and neither is "the record is
# non-empty": both pass today against a check that recorded whatever it liked.
# So the runner below reports which arguments reached it, its exit status is a
# parameter rather than a constant, and the record's key set is asserted whole
# — a record that carried a version field, or that spelled the executable
# under any other key, fails here rather than passing unnoticed.
# --------------------------------------------------------------------------

#: The configured command in these tests names a first word that exists
#: nowhere, so nothing can run except by the substitution putting the
#: configured runner in its place.
FOREIGN_TEST_COMMAND = "xyzzy-suite-driver --profile ci tests/"
FOREIGN_ARGUMENTS = ["--profile", "ci", "tests/"]
RUNNER_PATH = "toolchain/run-suite"


@pytest.mark.parametrize("status", [0, 7])
def test_a_runner_that_is_no_interpreter_is_what_runs_and_what_is_recorded(
    story_target, tmp_path, status,
):
    argv_log = tmp_path / "argv.txt"
    install_runner(story_target, RUNNER_PATH, status=status, argv_log=argv_log)
    dirty_target(story_target)

    result = clean_clone(story_target, tmp_path / "scratch",
                         FOREIGN_TEST_COMMAND, f"./{RUNNER_PATH}")

    assert result.ran is True
    assert result.runner == f"./{RUNNER_PATH}"
    assert result.command == f"./{RUNNER_PATH} --profile ci tests/"
    # The suite's own status, not a status the check decided for it.
    assert result.exit_code == status
    # And it really was that executable, with the configured command's own
    # remaining words: the record describes a run that happened rather than
    # one the check narrated.
    assert argv_log.read_text(encoding="utf-8").split() == FOREIGN_ARGUMENTS


def test_the_record_such_a_run_writes_is_exactly_the_fields_the_schema_declares(
    story_target, tmp_path,
):
    """The key set whole rather than one absence at a time.

    An assertion that the record carries no version field would pass against a
    record read from the wrong place, or against a field renamed rather than
    removed. Equality against the declared set cannot: it fails on a field
    that survived, on one that was added, and on the executable spelled under
    any key but `runner`.
    """
    argv_log = tmp_path / "argv.txt"
    install_runner(story_target, RUNNER_PATH, status=4, argv_log=argv_log)
    dirty_target(story_target)

    record = clean_clone(story_target, tmp_path / "scratch",
                         FOREIGN_TEST_COMMAND, f"./{RUNNER_PATH}").as_record()

    assert set(record) == {"ran", "command", "runner", "clone_path",
                           "exit_code", "output_tail"}
    assert set(record) <= set(SCHEMA["properties"])
    assert schema_validator.validate(record, SCHEMA) == []


def test_the_check_writes_that_record_for_a_configuration_that_names_the_runner(
    story_target, tmp_path,
):
    """The same claim through the key the configuration carries.

    The test above hands `run_clean_clone` its runner directly. This one hands
    the coordinator a configuration and reads the artifact off disk, which is
    where a reader of a finished run meets it.
    """
    argv_log = tmp_path / "argv.txt"
    install_runner(story_target, RUNNER_PATH, status=3, argv_log=argv_log)
    dirty_target(story_target)
    run_dir = run_dir_of(story_target)
    run_dir.mkdir(parents=True, exist_ok=True)

    story_coordinator.clean_clone_check(
        run_dir, story_target,
        {"test_command": FOREIGN_TEST_COMMAND,
         "verification_runner": f"./{RUNNER_PATH}"},
        ARTIFACT, stage_name=VERIFIER_STAGE["name"])

    record = record_of(run_dir)
    assert record["runner"] == f"./{RUNNER_PATH}"
    assert record["ran"] is True
    assert record["exit_code"] == 3
    assert record["command"] == f"./{RUNNER_PATH} --profile ci tests/"
    assert argv_log.read_text(encoding="utf-8").split() == FOREIGN_ARGUMENTS
    assert schema_validator.validate(record, SCHEMA) == []


def test_a_run_recording_a_runner_the_configuration_did_not_name_fails_these(
    story_target, tmp_path,
):
    """The control the three assertions above need, which the story asks for
    by name: a run that succeeds while recording a runner the configuration
    did not name must fail them.

    The mutant is the one the module already carries — the configured runner
    ignored in favour of the command's own first word — and the constructed
    violation is run through the same helper the proofs use. It completes, it
    raises nothing, and every assertion above about *which* executable ran is
    false of it.
    """
    module = variant("an interpreter the configuration did not name", tmp_path)
    argv_log = tmp_path / "argv.txt"
    install_runner(story_target, RUNNER_PATH, status=7, argv_log=argv_log)
    dirty_target(story_target)

    result = module.run_clean_clone(
        story_target, f"./{RUNNER_PATH} --profile ci tests/", "./fakepy",
        tmp_path / "scratch")

    assert result.ran is True
    assert result.runner != "./fakepy"
    assert result.exit_code == 7


# --------------------------------------------------------------------------
# The scratch directory
# --------------------------------------------------------------------------


def test_the_clone_is_built_outside_the_target_repository(story_target, tmp_path):
    dirty_target(story_target)
    result = clean_clone(story_target, tmp_path / "scratch", "sh -c 'true'")
    clone = Path(result.clone_path).resolve()
    assert story_target.resolve() not in clone.parents
    assert clone != story_target.resolve()


@pytest.mark.parametrize("command", ["sh -c 'true'", "sh -c 'exit 3'"])
def test_the_scratch_directory_is_removed_whatever_the_result(
    story_target, tmp_path, command,
):
    dirty_target(story_target)
    run_dir = run_dir_of(story_target)
    run_dir.mkdir(parents=True, exist_ok=True)
    config = {"test_command": command}

    result = story_coordinator.clean_clone_check(
        run_dir, story_target, config, ARTIFACT,
        stage_name=VERIFIER_STAGE["name"])

    assert result.ran is True
    assert not Path(result.clone_path).exists()
    assert not Path(result.clone_path).parent.exists()
    # The record outlives the directory it describes.
    assert (run_dir / ARTIFACT).is_file()


def test_the_check_leaves_no_scratch_directory_behind_in_the_temp_root(
    story_target, tmp_path,
):
    import tempfile
    root = Path(tempfile.gettempdir())
    before = {p.name for p in root.glob("l5-clean-clone-*")}
    dirty_target(story_target)
    run_dir = run_dir_of(story_target)
    run_dir.mkdir(parents=True, exist_ok=True)
    story_coordinator.clean_clone_check(
        run_dir, story_target, {"test_command": "sh -c 'exit 1'"}, ARTIFACT,
        stage_name=VERIFIER_STAGE["name"])
    assert {p.name for p in root.glob("l5-clean-clone-*")} == before


# --------------------------------------------------------------------------
# The artifact
# --------------------------------------------------------------------------


def test_the_schema_uses_only_the_keywords_the_validator_supports():
    assert schema_validator.unsupported_keywords(SCHEMA) == []
    assert schema_validator.validate(
        {"ran": True, "command": "x", "runner": "y"}, SCHEMA) == []


def test_no_union_keyword_appears_anywhere_in_the_schema():
    unions = {"oneOf", "anyOf", "allOf", "not", "nullable"}
    found: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            found.extend(key for key in node if key in unions)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(SCHEMA)
    assert found == []


def test_optional_fields_are_expressed_by_absence_from_required():
    assert set(SCHEMA["required"]) == {"ran", "command", "runner"}
    optional = set(SCHEMA["properties"]) - set(SCHEMA["required"])
    assert optional == {"clone_path", "exit_code", "output_tail", "output_path",
                        "reason"}


def test_the_schema_catches_a_record_missing_a_required_field():
    """The schema constrains something: it is not vacuously satisfied."""
    errors = schema_validator.validate({"ran": True, "command": "x"}, SCHEMA)
    assert errors == ["$.runner: expected a required property, found it missing"]


def test_the_schema_catches_a_wrongly_typed_exit_code():
    errors = schema_validator.validate(
        {"ran": True, "command": "x", "runner": "y", "exit_code": "1"}, SCHEMA)
    assert len(errors) == 1
    assert "$.exit_code" in errors[0]


def test_a_written_record_never_carries_a_null(story_target, tmp_path):
    dirty_target(story_target)
    refused = clean_clone(
        story_target, tmp_path / "a", "sh -c 'true'", ".venv999/bin/python")
    ran = clean_clone(story_target, tmp_path / "b", "sh -c 'exit 2'")
    for result in (refused, ran):
        assert None not in result.as_record().values()
        assert schema_validator.validate(result.as_record(), SCHEMA) == []
    assert "exit_code" not in refused.as_record()
    assert "reason" not in ran.as_record()
    assert ran.as_record()["exit_code"] == 2


def test_the_record_identifies_what_failed(story_target, tmp_path):
    dirty_target(story_target)
    result = clean_clone(
        story_target, tmp_path / "scratch",
        "sh -c 'echo FAILED tests/test_thing.py::test_it; exit 1'")
    assert result.exit_code == 1
    assert "FAILED tests/test_thing.py::test_it" in result.output_tail


def test_the_output_tail_is_bounded(story_target, tmp_path):
    dirty_target(story_target)
    result = clean_clone(
        story_target, tmp_path / "scratch",
        "sh -c 'i=0; while [ $i -lt 3000 ]; do echo aaaaaaaaaaaaaaaaaaaa; "
        "i=$((i+1)); done; exit 1'")
    assert len(result.output_tail) == story_coordinator.CLEAN_CLONE_OUTPUT_TAIL


# --------------------------------------------------------------------------
# The check runs where the story says it runs
# --------------------------------------------------------------------------


@pytest.fixture
def green_run(story_target, harness_root):
    configure(story_target, test_command=CORRECT_TEST_COMMAND)
    runner = Runner(story_target, [PASS])
    code = story_coordinator.run_story(
        "story-001", harness_root, story_target, runner)
    return code, runner, run_dir_of(story_target)


@pytest.fixture
def committed_failure_run(story_target, harness_root):
    """The bug's own run: green for the verifier, red once committed."""
    configure(story_target, test_command=BUGGY_TEST_COMMAND)
    runner = Runner(story_target, [PASS, PASS, PASS])
    code = story_coordinator.run_story(
        "story-001", harness_root, story_target, runner)
    return code, runner, run_dir_of(story_target)


def test_a_story_that_fails_only_once_committed_never_completes(
    committed_failure_run,
):
    """What the check buys, restated where story-045 moved it.

    It used to be that such a run never reached the documenter, the last
    stage. The documenter now runs before the verifier, so the guarantee is
    the one that was always the point: a story whose suite fails where the
    code ships does not finish - no completion report, and the run ends
    escalated with its retries spent.
    """
    code, runner, run_dir = committed_failure_run
    assert code == 2
    assert not (run_dir / "completion-report.md").exists()
    assert read_state(run_dir)["status"] == "escalated"


def test_the_same_story_with_its_baseline_corrected_advances(green_run):
    code, runner, run_dir = green_run
    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert read_state(run_dir)["status"] == "completed"


def test_a_run_that_reached_a_passing_verifier_records_the_check(green_run):
    """A reader can tell the check ran rather than inferring it from a pass."""
    _, _, run_dir = green_run
    record = record_of(run_dir)
    assert record["ran"] is True
    assert record["exit_code"] == 0
    assert record["command"] == CORRECT_TEST_COMMAND
    assert record["clone_path"]


def test_the_recorded_result_validates_against_its_schema(green_run):
    _, _, run_dir = green_run
    assert schema_validator.validate(record_of(run_dir), SCHEMA) == []


def test_a_failing_check_records_its_evidence_too(committed_failure_run):
    _, _, run_dir = committed_failure_run
    record = record_of(run_dir)
    assert record["ran"] is True
    assert record["exit_code"] != 0
    assert schema_validator.validate(record, SCHEMA) == []


def test_the_check_runs_after_the_documenter_stage_completes(green_run):
    """Ordering asserted on the event stream, not on the call list alone.

    story-045 moved the documenter ahead of the verifier, so the check - which
    runs on the verifier's passing verdict - now clones a tree that already
    holds the documenter's edits. The ordering is what says so.
    """
    _, _, run_dir = green_run
    events = [e["event"] for e in history_of(run_dir)]
    stages = [e.get("stage") for e in history_of(run_dir)]
    passed = events.index("clean-clone-passed")
    documenter = next(
        index for index, entry in enumerate(history_of(run_dir))
        if entry["event"] == "stage-completed" and entry["stage"] == DOCUMENTING)
    assert documenter < events.index("verification-passed") < passed
    assert passed < events.index("story-completed")
    assert stages[passed] == VERIFYING


def test_a_green_run_advances_with_its_retry_count_and_artifacts_unchanged(
    green_run,
):
    _, _, run_dir = green_run
    state = read_state(run_dir)
    assert state["retry_count"] == 0
    assert state["verification_iterations"] == 1
    assert not (run_dir / "attempts").exists()
    assert not (run_dir / "retry-guidance.json").exists()
    assert (run_dir / "completion-report.md").is_file()


# --------------------------------------------------------------------------
# Routing on a failure
# --------------------------------------------------------------------------


def test_a_clean_clone_failure_reroutes_to_the_workflows_declared_retry_stage(
    committed_failure_run,
):
    _, runner, _ = committed_failure_run
    retry_stage = VERIFIER_STAGE["clean_clone"]["retry_stage"]
    assert runner.calls == [*STAGE_NAMES, *STAGE_NAMES, *STAGE_NAMES]
    assert runner.calls[4] == retry_stage


def test_each_clean_clone_failure_increments_the_retry_count_exactly_once(
    committed_failure_run,
):
    _, _, run_dir = committed_failure_run
    state = read_state(run_dir)
    failures = [e for e in history_of(run_dir) if e["event"] == "clean-clone-failed"]
    assert len(failures) == 2
    assert state["retry_count"] == 2 == MAX_RETRIES


def test_the_superseded_attempt_is_archived_under_the_number_its_prompts_use(
    committed_failure_run,
):
    _, _, run_dir = committed_failure_run
    for attempt in (1, 2):
        archive = run_dir / "attempts" / f"attempt-{attempt}"
        assert (archive / "changed-files.json").is_file()
        assert (archive / "verification-result.json").is_file()
        assert (run_dir / f"prompt-{WRITING}-attempt-{attempt}.md").is_file()
    assert (run_dir / f"prompt-{WRITING}-attempt-3.md").is_file()


def test_the_ceiling_escalates_naming_the_check_and_the_failing_tests(
    story_target, harness_root,
):
    configure(
        story_target,
        test_command=(
            "sh -c 'echo FAILED tests/test_committed.py::test_baseline; exit 1'"),
    )
    runner = Runner(story_target, [PASS, PASS, PASS])
    assert story_coordinator.run_story(
        "story-001", harness_root, story_target, runner) == 2

    run_dir = run_dir_of(story_target)
    entry = history_of(run_dir)[-1]
    assert entry["event"] == "escalated"
    assert "clean-clone" in entry["message"]
    assert "tests/test_committed.py::test_baseline" in entry["message"]
    assert entry["retry_decision"] == "escalate"
    assert read_state(run_dir)["status"] == "escalated"
    assert "clean-clone" in (run_dir / "escalation-summary.md").read_text(
        encoding="utf-8")


def test_a_refused_check_escalates_naming_the_missing_interpreter(
    story_target, harness_root,
):
    configure(story_target, test_command=CORRECT_TEST_COMMAND,
              verification_runner=".venv999/bin/python")
    runner = Runner(story_target, [PASS])
    assert story_coordinator.run_story(
        "story-001", harness_root, story_target, runner) == 2

    run_dir = run_dir_of(story_target)
    entry = history_of(run_dir)[-1]
    assert entry["event"] == "escalated"
    assert ".venv999/bin/python" in entry["message"]
    assert not (run_dir / "completion-report.md").exists()
    assert record_of(run_dir)["ran"] is False


def test_the_failure_summary_reads_the_failing_tests_out_of_the_output():
    summary = story_coordinator._clean_clone_failures(
        "collected 4 items\n"
        "FAILED tests/test_a.py::test_one - AssertionError\n"
        "FAILED tests/test_b.py::test_two\n"
        "2 failed, 2 passed\n"
    )
    assert "tests/test_a.py::test_one" in summary
    assert "tests/test_b.py::test_two" in summary
    assert "\n" not in summary


def test_the_failure_summary_is_never_empty_and_never_unbounded():
    assert story_coordinator._clean_clone_failures("")
    assert story_coordinator._clean_clone_failures("something broke") == "something broke"
    many = "\n".join(f"FAILED tests/test_{i}.py::test_it" for i in range(9))
    summary = story_coordinator._clean_clone_failures(many)
    assert "and 4 more" in summary
    assert "\n" not in summary


# --------------------------------------------------------------------------
# The routing that was already there
# --------------------------------------------------------------------------


def test_a_failed_verification_still_retries_exactly_as_before(
    story_target, harness_root,
):
    configure(story_target, test_command=CORRECT_TEST_COMMAND)
    runner = Runner(story_target, [FAIL, PASS])
    assert story_coordinator.run_story(
        "story-001", harness_root, story_target, runner) == 0

    run_dir = run_dir_of(story_target)
    assert runner.calls == [*STAGE_NAMES, *STAGE_NAMES]
    assert read_state(run_dir)["retry_count"] == 1
    entry = next(e for e in history_of(run_dir) if e["event"] == "verification-failed")
    assert entry["retry_decision"] == "retry"
    assert "1 of 2" in entry["message"]


def test_a_verifier_that_declines_a_retry_still_escalates_as_before(
    story_target, harness_root,
):
    configure(story_target, test_command=CORRECT_TEST_COMMAND)
    runner = Runner(story_target, [{**FAIL, "retry_recommended": False}])
    assert story_coordinator.run_story(
        "story-001", harness_root, story_target, runner) == 2
    entry = history_of(run_dir_of(story_target))[-1]
    assert entry["event"] == "escalated"
    assert "did not recommend" in entry["retry_reason"]
    # The verifier never passed, so the check never ran and wrote nothing.
    assert not (run_dir_of(story_target) / ARTIFACT).exists()


def test_the_check_does_not_run_when_the_verifier_never_passes(
    story_target, harness_root,
):
    configure(story_target, test_command="sh -c 'exit 1'")
    runner = Runner(story_target, [{**FAIL, "retry_recommended": False}])
    assert story_coordinator.run_story(
        "story-001", harness_root, story_target, runner) == 2
    assert not any(
        e["event"].startswith("clean-clone")
        for e in history_of(run_dir_of(story_target))
    )


# --------------------------------------------------------------------------
# The declaration is the switch
# --------------------------------------------------------------------------


def probe_harness(tmp_path: Path, name: str, mutate) -> Path:
    """A harness root carrying the built definition with one key mutated.

    Since story-048 the definition it starts from is the one this module
    builds rather than the one this repository deploys, and the templates it
    carries come from the materializer rather than from a copy of `prompts/`,
    which the built stage names would not have matched anyway.
    """
    workflow = json.loads(json.dumps(WORKFLOW))
    for stage in workflow["stages"]:
        if stage["name"] == VERIFYING:
            mutate(stage)
    workflow["name"] = name
    return conftest.materialize_workflow(workflow, tmp_path / name)


def test_a_workflow_that_omits_the_declaration_does_not_run_the_check(
    story_target, tmp_path,
):
    """Removing the one key disables the check with no change to
    orchestration code: the target's test command would fail if it ran."""
    harness = probe_harness(tmp_path, "no-clean-clone", lambda s: s.pop("clean_clone"))
    configure(story_target, workflow="no-clean-clone", test_command="sh -c 'exit 1'")

    runner = Runner(story_target, [PASS])
    assert story_coordinator.run_story(
        "story-001", harness, story_target, runner) == 0

    run_dir = run_dir_of(story_target)
    assert DOCUMENTING in runner.calls
    assert not (run_dir / ARTIFACT).exists()
    assert not any(
        e["event"].startswith("clean-clone") for e in history_of(run_dir))


def test_the_artifact_name_comes_off_the_workflow_definition(
    story_target, tmp_path,
):
    """A name no orchestration code knows about reaches the run directory."""
    renamed = "clean-clone-probe.json"
    assert renamed not in COORDINATOR_SOURCE
    harness = probe_harness(
        tmp_path, "renamed-clean-clone",
        lambda s: s.__setitem__(
            "clean_clone", {**s["clean_clone"], "result": renamed}))
    configure(story_target, workflow="renamed-clean-clone",
              test_command=CORRECT_TEST_COMMAND)

    runner = Runner(story_target, [PASS])
    assert story_coordinator.run_story(
        "story-001", harness, story_target, runner) == 0

    run_dir = run_dir_of(story_target)
    assert (run_dir / renamed).is_file()
    assert not (run_dir / ARTIFACT).exists()
    entry = next(e for e in history_of(run_dir) if e["event"] == "clean-clone-passed")
    assert entry["artifacts"] == [renamed]


def test_no_artifact_name_for_this_check_is_written_into_orchestration_code():
    assert ARTIFACT not in COORDINATOR_SOURCE
    assert ARTIFACT in json.dumps(WORKFLOW)


# --------------------------------------------------------------------------
# One write path for both renderings
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["green_run", "committed_failure_run"])
def test_every_log_line_has_one_history_entry_in_the_same_order(shape, request):
    _, _, run_dir = request.getfixturevalue(shape)
    lines = log_lines(run_dir)
    history = history_of(run_dir)
    assert lines and len(history) == len(lines)
    for index, (line, entry) in enumerate(zip(lines, history), start=1):
        assert line == f"[{entry['timestamp']}] {entry['message']}"
        assert entry["sequence"] == index


@pytest.mark.parametrize("shape", ["green_run", "committed_failure_run"])
def test_the_frozen_log_line_format_is_unchanged(shape, request):
    _, _, run_dir = request.getfixturevalue(shape)
    for line in log_lines(run_dir):
        assert LINE.match(line), line


@pytest.mark.parametrize("shape", ["green_run", "committed_failure_run"])
def test_the_history_a_run_with_the_check_produced_validates(shape, request):
    _, _, run_dir = request.getfixturevalue(shape)
    assert schema_validator.validate(history_of(run_dir), HISTORY_SCHEMA) == []


@pytest.mark.parametrize("shape", ["green_run", "committed_failure_run"])
def test_both_new_event_kinds_are_in_the_schemas_enum(shape, request):
    _, _, run_dir = request.getfixturevalue(shape)
    allowed = HISTORY_SCHEMA["items"]["properties"]["event"]["enum"]
    kinds = {e["event"] for e in history_of(run_dir)}
    assert kinds & {"clean-clone-passed", "clean-clone-failed"}
    for entry in history_of(run_dir):
        assert entry["event"] in allowed, entry


def test_each_new_event_appears_in_both_renderings(committed_failure_run):
    _, _, run_dir = committed_failure_run
    lines = log_lines(run_dir)
    for entry in history_of(run_dir):
        if entry["event"].startswith("clean-clone"):
            assert f"[{entry['timestamp']}] {entry['message']}" in lines
            assert entry["stage"] == VERIFYING
            assert entry["artifacts"] == [ARTIFACT]


def test_the_reroute_event_carries_its_decision_and_its_reason(
    committed_failure_run,
):
    _, _, run_dir = committed_failure_run
    entry = next(e for e in history_of(run_dir) if e["event"] == "clean-clone-failed")
    assert entry["retry_decision"] == "retry"
    assert entry["retry_reason"]
    assert "1 of 2" in entry["message"]
    assert VERIFIER_STAGE["clean_clone"]["retry_stage"] in entry["message"]
    assert entry["retry_stage"] == VERIFIER_STAGE["clean_clone"]["retry_stage"]


def test_the_new_events_go_through_append_event_and_nothing_else():
    """A second write path for either rendering is the drift the design
    exists to prevent."""
    module = ast.parse(COORDINATOR_SOURCE)
    writers = []
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef):
            body = ast.unparse(node)
            if "_history_path" in body and "write_text" in body:
                writers.append(node.name)
            # Opening the log, not merely naming it: _escalate's summary
            # prose points a reader at events.log without writing to it.
            if "events.log" in body and "open(" in body:
                writers.append(node.name)
    assert set(writers) == {"append_event"}
    for name in ("_clean_clone_passed", "_clean_clone_failed"):
        function = next(
            node for node in ast.walk(module)
            if isinstance(node, ast.FunctionDef) and node.name == name)
        assert "append_event" in ast.unparse(function)


# --------------------------------------------------------------------------
# The check says it is about to re-run the suite (story-058)
#
# The check re-runs the whole suite, so the console sat silent for the length
# of a suite run with `verification passed` as its last line, which reads as a
# hang. The announcement is an event rather than a print, so the wait is
# visible in l5-status and in the structured history and not only on the
# console of whoever started the run.
# --------------------------------------------------------------------------

#: The kind the coordinator appends before a check re-runs the configured test
#: command. An event kind is the coordinator's own vocabulary rather than a
#: name the workflow declares, so it is spelled here exactly as the clean-clone
#: result kinds above are.
ANNOUNCEMENT = "suite-rerun-started"

L5_STATUS = REPO_ROOT / "scripts" / "l5-status"


def announcements(run_dir: Path) -> list[dict]:
    return [e for e in history_of(run_dir) if e["event"] == ANNOUNCEMENT]


def test_the_announcement_stands_immediately_before_the_checks_result(green_run):
    """After the verifier's pass and before the result the check writes: the
    gap the reader was watching in silence is the one it now covers."""
    _, _, run_dir = green_run
    kinds = [e["event"] for e in history_of(run_dir)]
    assert kinds.count(ANNOUNCEMENT) == 1
    announced = kinds.index(ANNOUNCEMENT)
    assert kinds.index("verification-passed") < announced
    assert kinds[announced + 1] == "clean-clone-passed"


def test_the_announcement_names_the_stage_and_the_declarations_artifact(green_run):
    _, _, run_dir = green_run
    entry = announcements(run_dir)[0]
    assert entry["stage"] == VERIFYING
    assert entry["artifacts"] == [ARTIFACT]


def test_the_announcement_says_the_suite_reruns_in_a_committed_fresh_clone(
    green_run,
):
    """What a reader who has just seen `verification passed` learns from it:
    that the suite is running again, where, and why that is not the same run
    twice."""
    _, _, run_dir = green_run
    message = announcements(run_dir)[0]["message"].lower()
    assert "suite" in message
    assert "fresh clone" in message
    assert "committed" in message


def test_the_announcement_appears_in_both_renderings(green_run):
    _, _, run_dir = green_run
    entry = announcements(run_dir)[0]
    assert f"[{entry['timestamp']}] {entry['message']}" in log_lines(run_dir)


def test_the_announcement_is_on_disk_before_the_clone_is_built(
    story_target, harness_root, monkeypatch,
):
    """The ordering that makes the announcement worth anything: it is written
    before the work it announces starts, not recorded alongside the result
    once the wait is over."""
    configure(story_target, test_command=CORRECT_TEST_COMMAND)
    run_dir = run_dir_of(story_target)
    seen: list[list[str]] = []
    original = story_coordinator.run_clean_clone

    def spy(*args, **kwargs):
        seen.append([e["event"] for e in history_of(run_dir)])
        return original(*args, **kwargs)

    monkeypatch.setattr(story_coordinator, "run_clean_clone", spy)
    assert story_coordinator.run_story(
        "story-001", harness_root, story_target, Runner(story_target, [PASS])) == 0

    assert len(seen) == 1
    assert seen[0][-1] == ANNOUNCEMENT
    assert "clean-clone-passed" not in seen[0]


def test_the_announcement_names_an_artifact_no_orchestration_code_knows(
    story_target, tmp_path,
):
    """The artifact comes off the declaration that turned the check on, the
    same way the result event's does: rename it in the workflow and the
    announcement follows, with the coordinator unchanged."""
    renamed = "clean-clone-announced-probe.json"
    assert renamed not in COORDINATOR_SOURCE
    harness = probe_harness(
        tmp_path, "renamed-announcement",
        lambda s: s.__setitem__(
            "clean_clone", {**s["clean_clone"], "result": renamed}))
    configure(story_target, workflow="renamed-announcement",
              test_command=CORRECT_TEST_COMMAND)

    assert story_coordinator.run_story(
        "story-001", harness, story_target, Runner(story_target, [PASS])) == 0

    entry = announcements(run_dir_of(story_target))[0]
    assert entry["artifacts"] == [renamed]


def test_a_workflow_that_omits_the_declaration_announces_nothing_either(
    story_target, tmp_path,
):
    """Removing the one key silences the announcement and the result together,
    which is what announcing from inside the check buys: neither can happen
    without the other, and no orchestration code changed.

    The control is the run above, whose declaration is present and whose
    announcement stands before its result — same coordinator, same runner, one
    key different. The target's test command would fail if the check ran.
    """
    harness = probe_harness(tmp_path, "no-announcement",
                            lambda s: s.pop("clean_clone"))
    configure(story_target, workflow="no-announcement",
              test_command="sh -c 'exit 1'")

    assert story_coordinator.run_story(
        "story-001", harness, story_target, Runner(story_target, [PASS])) == 0

    run_dir = run_dir_of(story_target)
    assert announcements(run_dir) == []
    assert not any(
        e["event"].startswith("clean-clone") for e in history_of(run_dir))


def test_l5_status_renders_a_run_whose_history_carries_the_announcement(green_run):
    """The reason it is an event rather than a print: the wait shows up where
    a reader who is not watching the console goes to look."""
    _, _, run_dir = green_run
    target_root = run_dir.parents[2]
    entry = announcements(run_dir)[0]
    # The detail view renders the tail of the log, which is where the
    # announcement of a check running this late in the run falls.
    assert any(entry["message"] in line for line in log_lines(run_dir)[-10:])

    result = subprocess.run(
        [sys.executable, str(L5_STATUS), run_dir.name],
        cwd=target_root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert entry["message"] in result.stdout


# --------------------------------------------------------------------------
# The retried implementer's evidence
# --------------------------------------------------------------------------


def test_the_coordinator_writes_no_retry_guidance_of_its_own(
    committed_failure_run,
):
    """That artifact is the verifier's. The verifier passed here, so nobody
    wrote one, and deterministic code must not fabricate an agent's
    judgement."""
    _, _, run_dir = committed_failure_run
    assert not (run_dir / "retry-guidance.json").exists()
    module = ast.parse(COORDINATOR_SOURCE)
    for node in ast.walk(module):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "retry-guidance.json" not in node.value or "absent" in node.value


def test_the_retried_implementer_receives_the_clean_clone_evidence(
    committed_failure_run,
):
    _, runner, run_dir = committed_failure_run
    prompt = (run_dir / f"prompt-{WRITING}-attempt-2.md").read_text(encoding="utf-8")
    record = record_of(run_dir)
    assert "{{" not in prompt
    assert record["command"] in prompt
    assert str(record["exit_code"]) in prompt


def test_the_placeholder_exists_in_the_prompt_and_in_the_context(
    story_target, shipped_harness_root,
):
    """A subject read: the claim is about the template *this repository ships*,
    so it keeps reading what this repository ships."""
    harness_root = shipped_harness_root
    template = context_assembler.load_template(harness_root, "story-implementer.md")
    assert "{{clean_clone_result}}" in template

    run_dir = run_dir_of(story_target)
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / ARTIFACT, {"ran": True, "command": "x", "runner": "y"})
    context = build_context_for(story_target, harness_root, run_dir)
    assert "clean_clone_result" in context
    assert "x" in context["clean_clone_result"]


def test_the_placeholder_renders_when_the_check_has_not_run(
    story_target, shipped_harness_root,
):
    """Same subject: the shipped template, rendered."""
    harness_root = shipped_harness_root
    run_dir = run_dir_of(story_target)
    run_dir.mkdir(parents=True, exist_ok=True)
    context = build_context_for(story_target, harness_root, run_dir)
    template = context_assembler.load_template(harness_root, "story-implementer.md")
    assert "{{" not in context_assembler.render(template, context)


def test_every_prompt_still_renders_with_no_leftover_placeholder(
    story_target, shipped_harness_root,
):
    """Every template *this repository ships*, which only the shipped root
    holds — a built harness carries the fixture's own templates instead."""
    harness_root = shipped_harness_root
    run_dir = run_dir_of(story_target)
    run_dir.mkdir(parents=True, exist_ok=True)
    context = build_context_for(story_target, harness_root, run_dir)
    for prompt_file in sorted(p.name for p in (harness_root / "prompts").glob("*.md")):
        template = context_assembler.load_template(harness_root, prompt_file)
        assert "{{" not in context_assembler.render(template, context), prompt_file


def build_context_for(target_root: Path, harness_root: Path, run_dir: Path) -> dict:
    config = harness_config.load_config(target_root)
    rules = harness_config.load_rules(harness_root)
    story_text = (
        target_root / ".harness" / "stories" / "story-001.yaml"
    ).read_text(encoding="utf-8")
    return context_assembler.build_context(
        story_text=story_text,
        story=story_coordinator.read_story(story_text).parsed,
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=config,
        rules=rules,
        workflow=WORKFLOW,
        retry_count=0,
    )


# --------------------------------------------------------------------------
# The checks above are not vacuous
# --------------------------------------------------------------------------


MUTANTS = {
    # The check itself removed.
    "a coordinator that never runs the check": (
        '                clean_clone = stage.get("clean_clone") or {}',
        "                clean_clone = {}",
    ),
    # A tree copy rather than a commit: the story is present as pending
    # edits, so a test reading git history never sees it.
    "a clone the story was never committed into": (
        '    _git(clone, "add", "-A")\n',
        "",
    ),
    # The configured runner ignored in favor of the command's own first word.
    "an interpreter the configuration did not name": (
        "    runner = verification_runner or argv[0]",
        "    runner = argv[0]",
    ),
    # The scratch directory left behind.
    "a scratch directory that is never removed": (
        "        shutil.rmtree(scratch, ignore_errors=True)",
        "        pass",
    ),
}


def variant(name: str, tmp_path: Path):
    """The named mutation applied to the working-tree coordinator.

    Built through `conftest.load_mutant` since story-029, which folded the
    loader this file shared byte for byte with
    `tests/test_story_012_validation.py`. The mutations, their anchors and
    every assertion below are unchanged.
    """
    return load_mutant(
        COORDINATOR_PATH, [MUTANTS[name]],
        name="mutant_" + re.sub(r"\W+", "_", name), tmp_path=tmp_path)


def test_a_coordinator_that_skips_the_check_is_caught(
    story_target, harness_root, tmp_path,
):
    module = variant("a coordinator that never runs the check", tmp_path)
    configure(story_target, test_command=BUGGY_TEST_COMMAND)
    runner = Runner(story_target, [PASS])
    assert module.run_story("story-001", harness_root, story_target, runner) == 0
    assert DOCUMENTING in runner.calls
    assert not (run_dir_of(story_target) / ARTIFACT).exists()


def test_a_clone_without_the_story_committed_is_caught(
    story_target, harness_root, tmp_path,
):
    """The mutant builds the clone but never stages the working tree, so the
    story is absent from its HEAD and the HEAD-baseline bug goes green."""
    module = variant("a clone the story was never committed into", tmp_path)
    configure(story_target, test_command=BUGGY_TEST_COMMAND)
    runner = Runner(story_target, [PASS])
    assert module.run_story("story-001", harness_root, story_target, runner) == 0
    assert DOCUMENTING in runner.calls


def test_an_ignored_verification_runner_is_caught(story_target, tmp_path):
    module = variant("an interpreter the configuration did not name", tmp_path)
    install_interpreter(story_target, "fakepy")
    dirty_target(story_target)
    result = module.run_clean_clone(
        story_target, "sh -c 'true'", "./fakepy", tmp_path / "scratch")
    assert result.runner != "./fakepy"
    assert result.command != "./fakepy -c true"


def test_a_scratch_directory_left_behind_is_caught(story_target, tmp_path):
    module = variant("a scratch directory that is never removed", tmp_path)
    dirty_target(story_target)
    run_dir = run_dir_of(story_target)
    run_dir.mkdir(parents=True, exist_ok=True)
    result = module.clean_clone_check(
        run_dir, story_target, {"test_command": "sh -c 'true'"}, ARTIFACT,
        stage_name=VERIFIER_STAGE["name"])
    left = Path(result.clone_path)
    try:
        assert left.exists()
    finally:
        shutil.rmtree(left.parent, ignore_errors=True)


# --------------------------------------------------------------------------
# The schema inventories
# --------------------------------------------------------------------------


def test_the_new_schema_ships_and_both_inventories_still_assert_equality():
    """Adding a schema file necessarily fails both set-equality assertions.
    They were updated rather than relaxed to a subset."""
    assert (REPO_ROOT / "schemas" / "clean-clone-result.schema.json").is_file()
    # story-013 moved the declaration out of the two test files and into
    # schemas/manifest.json. The forcing property is unchanged — the new
    # schema still had to be declared — only the one place it is declared
    # moved. The two files remain where the equality is asserted.
    assert "clean-clone-result" in schema_validator.shipped_schemas(REPO_ROOT)
    for rel in ("tests/test_schema_validator.py", "tests/test_artifact_schemas.py"):
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "shipped_schemas()" in source, rel
        assert "issubset" not in source, rel
        assert ">=" not in source.split("SHIPPED")[1][:400], rel


# ==========================================================================
# story-033: the clone underneath both checks is built over the normal
# transport
#
# `_build_clone` is the one place the harness clones the target repository,
# and both checks that exist — the clean-clone check above and the revert
# check — reach it. It used to clone with `--no-hardlinks`, which is still a
# *local* clone: git enumerates the source's `.git/objects` as a directory
# tree and copies what it enumerated, and anything it cannot read, or that
# disappears between the enumeration and the copy, is a hard failure. Three
# CI runs failed that way; one of the files git could not copy was
# `.git/objects/pack/multi-pack-index.lock`.
#
# `--no-local` makes git negotiate a pack over a pipe instead, so no file in
# the source's object store is ever opened by the clone.
#
# Two things about how this is asserted, both deliberate:
#
#   * The transport is read off **the argument list the harness actually
#     hands to git**, captured as it runs, not off the text of
#     `orchestration/story_coordinator.py`. A module can say `--no-local` in
#     a docstring, in a comment, or in a branch nothing takes.
#   * "carries no directory-copy flag" is an absence, so it is paired with a
#     control: the same capture, over the same fixture, with the module's one
#     flag mutated back — and the capture reports `--no-hardlinks` there.
# ==========================================================================


# The end-to-end fixtures come from the story that built the revert check:
# story-033 changes how a clone is obtained and nothing about what either
# check decides, so the assertions below are driven through the harness's own
# path rather than through a reimplementation of it. Two of those names
# collide with this module's own story-014 helpers and are aliased rather
# than shadowing them.
from test_revert_baseline import (  # noqa: E402, F401
    TEST_COMMAND,
    WRITING as REVERT_WRITING,
    added_coverage,
    capture,
    fixture_and_a_test_that_needs_it,
    forced_repair,
    run,
    target,
    write,
)
#: Aliased rather than imported under its own name. Since story-048 that module
#: drives its runs against a workflow it builds for itself, and so does this
#: one; importing its `harness_root` unaliased would rebind this module's own
#: fixture and point every run above at the other module's definition, whose
#: name this module's target does not configure.
from test_revert_baseline import (  # noqa: E402
    harness_root as revert_harness_root,
)
from test_revert_baseline import (  # noqa: E402
    TESTS_CONFTEST_AT_HEAD,
    TEST_APP_AT_HEAD,
    record_of as revert_record_of,
    run_dir_of as revert_run_dir_of,
)

#: The flag that makes git negotiate a pack over a pipe, and the flags that
#: put it back on the directory-copy path. `-l` is git's short form of
#: `--local`; none of the three may appear.
TRANSPORT = "--no-local"
DIRECTORY_COPY_FLAGS = ("--no-hardlinks", "--local", "-l")

#: The mutation every control below uses: today's module with its one
#: transport flag put back to what it was. It is applied to the working-tree
#: source, never to source recovered at a revision.
OLD_COMMAND = (f'"{TRANSPORT}"', '"--no-hardlinks"')

#: The name of the entry planted under the source's object store. The file CI
#: could not copy, by name, so the fixture says what it stands in for.
LOCK = "multi-pack-index.lock"

#: story-033's change lands in one file; every absence assertion about what
#: that story did *not* touch is controlled against the diff to this one.
CHANGED_BY_STORY_033 = "orchestration/story_coordinator.py"

TRANSPORT_MARKER = "STORY_033_MARKER"


def old_command_coordinator(tmp_path: Path):
    """Today's coordinator with the previous clone command, as a module.

    The control's subject. Built through the shared mutation loader, which
    takes a working-tree path: what is being shown is that today's fixture
    fails under the old command, and a module recovered out of history would
    be a different module in more ways than the one under test.
    """
    return load_mutant(COORDINATOR_PATH, [OLD_COMMAND],
                       name="story_033_old_command", tmp_path=tmp_path)


# --------------------------------------------------------------------------
# The argument list the harness runs
# --------------------------------------------------------------------------


@pytest.fixture
def clone_argv(monkeypatch):
    """Every argument list handed to `git clone` while a test runs.

    The spawn itself is intercepted rather than the coordinator's helpers, so
    what is recorded is what the operating system was asked to do — including
    a clone built by the mutant module, which is what makes the control below
    a control on the same instrument.
    """
    seen: list[list[str]] = []
    original = subprocess.run

    def spy(args, *rest, **kwargs):
        if isinstance(args, (list, tuple)) and list(args[:2]) == ["git", "clone"]:
            seen.append([str(argument) for argument in args])
        return original(args, *rest, **kwargs)

    monkeypatch.setattr(story_coordinator.subprocess, "run", spy)
    return seen


def test_the_harness_builds_its_clone_over_the_normal_transport(
    target, tmp_path, clone_argv,
):
    """Asserted against the argument list git was given, not the module text."""
    story_coordinator._build_clone(target, tmp_path / "clone")

    assert len(clone_argv) == 1, clone_argv
    argv = clone_argv[0]
    assert TRANSPORT in argv, argv
    for flag in DIRECTORY_COPY_FLAGS:
        assert flag not in argv, argv


def test_the_same_capture_reports_the_flag_the_previous_command_carried(
    target, tmp_path, clone_argv,
):
    """The control for the absence above.

    The capture is the same fixture, the same call and the same instrument;
    only the module's one flag differs. It reports `--no-hardlinks` here, so
    the absence asserted above is a fact about the command rather than about
    a capture that sees nothing.
    """
    old = old_command_coordinator(tmp_path)

    old._build_clone(target, tmp_path / "clone")

    assert len(clone_argv) == 1, clone_argv
    argv = clone_argv[0]
    assert "--no-hardlinks" in argv, argv
    assert TRANSPORT not in argv, argv


def test_the_source_git_is_given_is_a_filesystem_path_and_not_a_url(
    target, tmp_path, clone_argv,
):
    """story-014's criterion, re-asked of the new command's argument list."""
    story_coordinator._build_clone(target, tmp_path / "clone")

    argv = clone_argv[0]
    assert str(target) in argv, argv
    for argument in argv:
        assert "://" not in argument, argv
        assert "git@" not in argument, argv


# --------------------------------------------------------------------------
# A source repository a directory copy cannot handle
# --------------------------------------------------------------------------


@pytest.fixture(params=["an unreadable file", "a dangling symlink"])
def unclonable_source(request, target: Path):
    """A target whose object store holds an entry `--no-hardlinks` cannot copy.

    Both forms stand in for the same thing CI met: a transient file under
    `.git/objects/pack` that the directory-copy path enumerates and then
    cannot read. The lock file itself cannot be reproduced deterministically —
    it exists only while something is writing a multi-pack index — so the
    fixture reproduces its *effect* on the copy, which is what fails.

    The mode is restored on the way out so the temporary directory can be
    removed whatever the test did.
    """
    entry = target / ".git" / "objects" / "pack" / LOCK
    entry.parent.mkdir(parents=True, exist_ok=True)
    if request.param == "an unreadable file":
        entry.write_text("a multi-pack index is being written\n", encoding="utf-8")
        os.chmod(entry, 0o000)
    else:
        entry.symlink_to(target / ".git" / "objects" / "pack" / "gone")
    yield target
    if entry.is_symlink():
        entry.unlink()
    elif entry.exists():
        os.chmod(entry, 0o600)


def test_a_source_a_directory_copy_cannot_read_still_clones(
    unclonable_source, tmp_path,
):
    """The new command never opens the source's object files, so the entry
    that stops a copy is not something it can trip over."""
    clone = tmp_path / "clone"

    story_coordinator._build_clone(unclonable_source, clone)

    assert (clone / "src" / "app.py").is_file()
    assert git(clone, "log", "--oneline").strip()
    assert git(clone, "status", "--porcelain").strip() == ""


def test_the_same_source_fails_under_the_previous_command(
    unclonable_source, tmp_path,
):
    """The fixture is hostile: the command story-033 removed fails on it.

    Without this the test above would pass against a fixture that was never
    difficult, and "the clone succeeds" would be a statement about nothing.
    """
    old = old_command_coordinator(tmp_path)

    with pytest.raises(RuntimeError) as failure:
        old._build_clone(unclonable_source, tmp_path / "clone")

    assert "Could not clone" in str(failure.value)
    assert not (tmp_path / "clone" / "src" / "app.py").exists()


def test_the_previous_command_clones_the_same_repository_without_that_entry(
    unclonable_source, tmp_path,
):
    """The second control: the mutant module is not simply broken.

    The same source, the same mutant, the planted entry removed — and the old
    command builds the clone. So what fails above is the entry under
    `.git/objects`, which is the thing story-033 is about.
    """
    entry = unclonable_source / ".git" / "objects" / "pack" / LOCK
    if not entry.is_symlink():
        os.chmod(entry, 0o600)
    entry.unlink()
    old = old_command_coordinator(tmp_path)

    old._build_clone(unclonable_source, tmp_path / "clone")

    assert (tmp_path / "clone" / "src" / "app.py").is_file()


# --------------------------------------------------------------------------
# One clone site, and no remote literal in the module
# --------------------------------------------------------------------------


def clone_sites(source: str) -> list[str]:
    """The top-level functions in one module that invoke `git clone`.

    Read off the argument lists rather than off the text, so a docstring
    naming the command is not a second cloner and a list literal is.
    """
    named = []
    for function in ast.parse(source).body:
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            if not isinstance(node, ast.List) or len(node.elts) < 2:
                continue
            words = [element.value for element in node.elts[:2]
                     if isinstance(element, ast.Constant)]
            if words == ["git", "clone"]:
                named.append(function.name)
                break
    return named


#: A second cloner, planted in the module's text for the control below. Not
#: written to disk and not executed: the question is what the scan reports.
A_SECOND_CLONER = '''

def _build_another_clone(target_root, clone):
    subprocess.run(["git", "clone", str(target_root), str(clone)])
'''


def test_exactly_one_function_in_the_coordinator_invokes_git_clone():
    assert clone_sites(COORDINATOR_SOURCE) == ["_build_clone"]


def test_the_same_scan_reports_a_second_cloner_when_there_is_one():
    """The control: the scan is looking where it claims to look."""
    assert clone_sites(COORDINATOR_SOURCE + A_SECOND_CLONER) == [
        "_build_clone", "_build_another_clone"]


def url_constants(source: str) -> list[str]:
    """Every string constant in a module that spells a remote."""
    return [node.value for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and ("://" in node.value or "git@" in node.value)]


def test_no_string_constant_in_the_coordinator_carries_a_remote():
    """story-014's assertion, restated here because story-033 rewrote the
    docstring the clone lives under: prose about transports is exactly where
    a URL would arrive by accident."""
    assert url_constants(COORDINATOR_SOURCE) == []


@pytest.mark.parametrize("planted", ['\nREMOTE = "https://example.invalid/x"\n',
                                     '\nREMOTE = "git@example.invalid:x"\n'])
def test_the_same_scan_reports_a_remote_when_there_is_one(planted):
    """The control for the absence above."""
    assert url_constants(COORDINATOR_SOURCE + planted) == [planted.split('"')[1]]


# --------------------------------------------------------------------------
# What the clone carries is unchanged
# --------------------------------------------------------------------------


@pytest.fixture
def dirty_transport_target(target: Path) -> Path:
    """A target carrying an uncommitted story, with the ignore rules the real
    repository has: a gitignored directory and the run directory."""
    (target / ".gitignore").write_text(
        ".pytest_cache/\n__pycache__/\nignored/\n.harness/runs/\n", encoding="utf-8")
    commit_setup(target, "ignore rules for this test")

    write(target / "src" / "app.py",
          f'def greet(name):\n    return "{TRANSPORT_MARKER}"\n')
    write(target / "probe.txt", "probe\n")
    write(target / "ignored" / "secret.txt", "secret\n")
    write(target / ".harness" / "runs" / "story-001" / "state.json", "{}\n")
    return target


def test_the_clone_carries_the_working_tree_as_a_commit_and_is_clean(
    dirty_transport_target, tmp_path,
):
    clone = tmp_path / "clone"

    story_coordinator._build_clone(dirty_transport_target, clone)

    assert TRANSPORT_MARKER in git(clone, "show", "HEAD:src/app.py")
    assert git(clone, "show", "HEAD:probe.txt").strip() == "probe"
    assert git(clone, "status", "--porcelain").strip() == ""
    assert git(clone, "log", "--oneline").count("\n") >= 2


def test_no_gitignored_file_and_no_run_directory_reaches_the_clone(
    dirty_transport_target, tmp_path,
):
    """An absence, with the control beside it.

    The control is `probe.txt`: an untracked file the ignore rules do *not*
    exclude, which reaches the clone by the same copy the two absent paths
    would have used. So the clone is receiving untracked files — the
    assertion is about what the ignore rules exclude and not about a clone
    that carries nothing, or a source that never held them.
    """
    clone = tmp_path / "clone"

    story_coordinator._build_clone(dirty_transport_target, clone)

    assert (dirty_transport_target / "ignored" / "secret.txt").is_file()
    assert (dirty_transport_target / ".harness" / "runs" / "story-001"
            / "state.json").is_file()
    assert not (clone / "ignored").exists()
    assert not (clone / ".harness" / "runs").exists()
    assert (clone / "probe.txt").is_file()


def test_the_target_repository_is_only_read(dirty_transport_target, tmp_path):
    before = (git(dirty_transport_target, "rev-parse", "HEAD"),
              git(dirty_transport_target, "status", "--porcelain"),
              git(dirty_transport_target, "branch", "--format=%(refname)"),
              git(dirty_transport_target, "stash", "list"),
              git(dirty_transport_target, "diff", "--cached"))

    story_coordinator._build_clone(dirty_transport_target, tmp_path / "clone")

    assert (git(dirty_transport_target, "rev-parse", "HEAD"),
            git(dirty_transport_target, "status", "--porcelain"),
            git(dirty_transport_target, "branch", "--format=%(refname)"),
            git(dirty_transport_target, "stash", "list"),
            git(dirty_transport_target, "diff", "--cached")) == before


# --------------------------------------------------------------------------
# Both checks still reach their verdicts through the new command
# --------------------------------------------------------------------------


def test_a_forced_edit_still_reaches_the_permitted_verdict(
    target, revert_harness_root,
):
    """End to end: a rename the pre-existing test cannot survive."""
    code, _ = run(target, revert_harness_root, {REVERT_WRITING: [forced_repair]})

    assert code == 0
    record = revert_record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is True
    assert record["paths"] == ["tests/test_app.py"]


def test_an_unforced_edit_still_reaches_the_refused_verdict(
    target, revert_harness_root,
):
    """The control for the verdict above: the same run, an edit nothing forced,
    and the opposite decision. A check that permitted everything would be no
    check."""
    code, _ = run(target, revert_harness_root, {REVERT_WRITING: [added_coverage]})

    assert code == 2
    record = revert_record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is False
    assert record["paths"] == ["tests/test_app.py"]


def test_a_governed_path_is_restored_from_the_baseline_inside_the_clone(
    target, tmp_path,
):
    """The restore, in a clone built the new way.

    The control is the second clone: the same builder with nothing reverted
    carries the edit, so this says the revert happened rather than that the
    clone never saw the change.
    """
    baseline = capture(target, tmp_path / "before")
    forced_repair(target, revert_run_dir_of(target))

    story_coordinator._build_clone(target, tmp_path / "reverted",
                                   revert=["tests/test_app.py"], baseline=baseline)
    story_coordinator._build_clone(target, tmp_path / "intact")

    assert git(tmp_path / "reverted", "show",
               "HEAD:tests/test_app.py") == TEST_APP_AT_HEAD
    assert git(tmp_path / "intact", "show",
               "HEAD:tests/test_app.py") != TEST_APP_AT_HEAD
    # Everything outside the reverted path is the working tree in both.
    for clone in ("reverted", "intact"):
        assert "salute" in git(tmp_path / clone, "show", "HEAD:src/app.py")


def test_a_governed_path_the_baseline_lacks_is_deleted_inside_the_clone(
    target, tmp_path,
):
    """The delete half, in a clone built the new way.

    The control sits in the same clone: the path the baseline *does* hold is
    present and restored, so the deletion is about absence from the baseline
    and not about the revert emptying the prefix.
    """
    baseline = capture(target, tmp_path / "before")
    fixture_and_a_test_that_needs_it(target, revert_run_dir_of(target))

    story_coordinator._build_clone(
        target, tmp_path / "reverted",
        revert=["tests/conftest.py", "tests/test_uses_fixture.py"],
        baseline=baseline)
    committed = git(tmp_path / "reverted", "ls-files").split()

    assert "tests/test_uses_fixture.py" not in committed
    assert not (tmp_path / "reverted" / "tests" / "test_uses_fixture.py").exists()
    assert "tests/conftest.py" in committed
    assert git(tmp_path / "reverted", "show",
               "HEAD:tests/conftest.py") == TESTS_CONFTEST_AT_HEAD


def test_the_clone_keeps_the_source_history_rather_than_shallowing_it(
    target, tmp_path,
):
    """The transport can shallow a clone; this one does not.

    The checks exist so a test resolving a baseline out of git history sees
    what it would see where the code ships, and a shallow clone would not.
    """
    write(target / "src" / "app.py",
          f'def greet(name):\n    return "{TRANSPORT_MARKER}"\n')
    commit_setup(target, "a second commit in the source")
    clone = tmp_path / "clone"

    story_coordinator._build_clone(target, clone)

    assert not (clone / ".git" / "shallow").exists()
    source_commits = git(target, "rev-list", "--count", "HEAD").strip()
    # The clone holds one more: the working tree, committed by the builder.
    assert git(clone, "rev-list", "--count", "HEAD").strip() \
        == str(int(source_commits) + 1)


# --------------------------------------------------------------------------
# What story-033 did not touch
# --------------------------------------------------------------------------


def story_033_diff(root: Path, *paths: str) -> str:
    """A story's own diff to `paths`, bounded at both ends of its range.

    Asked of a repository the caller built. It used to be asked of this
    repository's own commit graph, where it re-stated a frozen past fact and
    took the answer from a history that moves: a rename gives a path a new
    add-commit and empties every assertion bounded by that path's range, and a
    squash makes the range unresolvable in a clone. The predicate is unchanged
    — empty means the story left those paths alone — and each caller below
    constructs a story in which it is shown both holding and reporting.
    """
    return conftest.constructed_story_diff(root, list(paths))


def test_no_file_under_github_is_changed_by_story_033(tmp_path):
    """The CI retry and the fail-fast setting are a backstop for the next
    unknown, not story-033's fix, and a story that leaves them alone shows an
    empty diff over its own range."""
    respecting = conftest.constructed_story(tmp_path, respected=[".github/"],
                                            name="github-left-alone")
    assert story_033_diff(respecting, ".github/") == ""


def test_the_same_comparison_reports_the_file_story_033_did_change(tmp_path):
    """The control: the resolution is bounded at the story's own range and is
    looking at that story. Without it the emptiness above would hold just as
    well for a comparison of a commit with itself."""
    violating = conftest.constructed_story(tmp_path,
                                           violated=[CHANGED_BY_STORY_033],
                                           name="coordinator-changed")
    assert CHANGED_BY_STORY_033 in story_033_diff(violating,
                                                  CHANGED_BY_STORY_033)
    assert "rewritten inside the story's own run commit" in story_033_diff(
        violating, CHANGED_BY_STORY_033)


def test_the_three_named_ci_tests_are_not_modified_by_story_033(tmp_path):
    """They pass unmodified because they are unmodified: the module holding
    all three is untouched by the story's range, and the suite this stage
    ran includes it.

    Spelled at the name the module had inside that range. story-038 renamed
    it to the one this file now carries, and a path is asked for at a
    revision under the name it has there — which is exactly the shape that
    made reading this repository's own graph for it unsafe, and is here a
    property of a repository the test builds.
    """
    validation = conftest.constructed_story(
        tmp_path, respected=["tests/test_story_014_validation.py"],
        name="ci-tests-left-alone")
    assert story_033_diff(validation, "tests/test_story_014_validation.py") == ""
    edited = conftest.constructed_story(
        tmp_path, violated=["tests/test_story_014_validation.py"],
        name="ci-tests-edited")
    assert story_033_diff(edited, "tests/test_story_014_validation.py") != ""


def commits_saying(phrase: str) -> list[str]:
    """The subjects of commits whose message carries `phrase`.

    A search rather than a pinned revision: the point is that the record
    still says what it said, which survives a rebase, and a pinned SHA would
    not.
    """
    found = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "log", "--format=%s", f"--grep={phrase}"],
        capture_output=True, text=True, check=True).stdout
    return [line for line in found.splitlines() if line.strip()]


def test_the_commits_describing_the_cause_as_unestablished_still_say_so():
    """story-033 supersedes them in the record instead of rewriting them."""
    subjects = commits_saying("cause is not established")

    assert len(subjects) >= 2, subjects
    assert "CI: retry only the failed tests once, and only in CI" in subjects
    assert "CI: let every Python version finish when one fails" in subjects


def test_the_same_search_finds_nothing_when_the_phrase_is_not_there():
    """The control: the search reads the history rather than reporting
    whatever it is asked for."""
    assert commits_saying("this phrase is in no commit message") == []
