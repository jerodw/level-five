"""Independent validation for story-041's retired-key refusal.

A configuration key the harness no longer reads is refused, not ignored. The
distinction is the whole point: `verification_runner` falls back to the
configured test command's own first word when it is unset, so a config still
carrying `clean_clone_python` after the rename would load cleanly, resolve a
runner nobody asked for, and quietly change what the clean-clone check
exercises. A silent fallback is exactly the drift a rename is supposed to
surface.

Written from the story's acceptance criteria rather than from the
implementation, at three altitudes:

  * **the function.** `harness_config.retired_config_problems` is a pure
    function over a loaded config, so it is driven directly, and the mapping
    it reads is held to naming a replacement the harness actually declares.
  * **the refusal.** A throwaway target carrying the retired key is run
    through the real `story_coordinator.run_story` with a fake agent runner,
    and what the refusal *left behind* is read off the tree rather than
    inferred from the exit status.
  * **the ordering.** The refusal is claimed to sit above every other
    pre-flight. That is shown by breaking a later one and observing the
    retired key win — including a workflow name that cannot be loaded at all,
    which raises without the retired key and refuses cleanly with it.

Every absence asserted here carries a demonstration that it can fail:

  * "the refused run created no run directory, no state file, no branch and
    invoked no agent" sits beside the same fixture with the replacement key,
    where the same four observations report all four created;
  * "this configuration carries no retired key" sits beside the same check
    over the same configuration with the retired key put back;
  * each ordering assertion sits beside the same broken fixture without the
    retired key, where the later pre-flight is the one that speaks.

Nothing here invokes a model: every run goes through the fake runner below.
"""
import json
import subprocess
from pathlib import Path

import pytest

import conftest
import harness_config
import schema_validator
import story_coordinator
from agent_runner import AgentResult
from conftest import commit_setup

REPO_ROOT = Path(harness_config.__file__).resolve().parents[1]

#: This module declares no origin in `conftest.STORY_ORIGINS`, so the shared
#: resolution bounds every comparison below at this story's own run commit and
#: its parent — and, while the story is still in flight, at the working tree
#: against HEAD, which is the one moment that pair is the correct baseline.
THIS_FILE = Path(__file__).resolve()

#: The retired key and its replacement, written from the story's words rather
#: than imported from the mapping under test. A module that read both names
#: out of `RETIRED_CONFIG_KEYS` would agree with whatever that mapping happens
#: to say; these are what the story asked for, and the mapping is compared
#: against them below.
RETIRED = "clean_clone_python"
REPLACEMENT = "verification_runner"

STORY_ID = "story-001"

#: A runner that exists on every platform this suite runs on, so the control
#: run's clean-clone check resolves it and the suite it runs exits zero. The
#: substitution puts it in place of `echo`, the fixture command's first word.
WORKING_RUNNER = "/bin/echo"

PASS_VERDICT = {"status": "passed", "blocking_issues": [], "unverified": [],
                "retry_recommended": False}


# --------------------------------------------------------------------------
# Fixture plumbing
# --------------------------------------------------------------------------


class Runner:
    """A fake agent runner that writes each stage's declared artifacts.

    It records every stage it was asked to run, which is how "no agent was
    invoked" is observed as a fact about the coordinator rather than as the
    absence of a log file nobody wrote.
    """

    def __init__(self, target_root: Path, run_dir: Path):
        self.target_root = target_root
        self.run_dir = run_dir
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None):
        self.calls.append(stage)
        # Written exactly as the real runner writes it, so the stage log is
        # observable as a file rather than only as an argument nobody used.
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"===== stage: {stage} =====\n")
        if stage == "implementer":
            (self.target_root / "src" / "app.py").write_text(
                "print('hello')\n# the story's change\n", encoding="utf-8")
            _write_json(self.run_dir / "changed-files.json",
                        {"modified": ["src/app.py"], "created": [],
                         "deleted": []})
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
        return AgentResult(ok=True, result_text=f"{stage} done")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def configure(target_root: Path, **overrides: str) -> None:
    """Rewrite the target's config keys, adding those it does not carry.

    The result is committed, because story-021's clean-tree pre-flight refuses
    a run whose target tree already holds work no stage produced, and a test's
    configuration is part of the repository the run starts *from*.
    """
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
    commit_setup(target_root, "configure the target for this test")


def git(root: Path, *args: str) -> str:
    """One git command against a repository this file built under tmp_path."""
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


def branches(root: Path) -> set[str]:
    return set(git(root, "branch", "--format=%(refname:short)").split())


def run(target_root: Path, harness_root: Path, story_id: str = STORY_ID):
    """One story executed through the real coordinator and the fake runner."""
    run_dir = target_root / ".harness" / "runs" / story_id
    runner = Runner(target_root, run_dir)
    code = story_coordinator.run_story(story_id, harness_root, target_root,
                                       runner)
    return code, runner, run_dir


@pytest.fixture
def retired_target(target_root: Path) -> Path:
    """A target whose configuration still carries the retired key."""
    configure(target_root, **{RETIRED: WORKING_RUNNER})
    return target_root


@pytest.fixture
def replacement_target(target_root: Path) -> Path:
    """The same fixture carrying the replacement key instead.

    Everything else is identical, so every difference the tests below report
    between the two is a difference the key name made.
    """
    configure(target_root, **{REPLACEMENT: WORKING_RUNNER})
    return target_root


# --------------------------------------------------------------------------
# 1. The declaration, and the function over it
# --------------------------------------------------------------------------


def test_the_mapping_retires_the_key_this_story_retired_and_names_its_successor():
    assert harness_config.RETIRED_CONFIG_KEYS == {RETIRED: REPLACEMENT}


def test_every_retired_key_names_a_replacement_the_harness_actually_reads():
    """A refusal pointing at a key nothing reads would send a developer to a
    name that does nothing, which is worse than the drift it prevents."""
    declared = harness_config.declared_config_keys()
    for retired, replacement in harness_config.RETIRED_CONFIG_KEYS.items():
        assert replacement in declared, replacement
        assert retired not in declared, retired


def test_a_config_carrying_a_retired_key_yields_one_problem_naming_both_names():
    problems = harness_config.retired_config_problems(
        {"project": "sample", RETIRED: "/somewhere/bin/python"})
    assert len(problems) == 1, problems
    assert RETIRED in problems[0]
    assert REPLACEMENT in problems[0]


def test_a_config_carrying_no_retired_key_yields_nothing():
    """Beside its control: the same config with the retired key added is
    reported, so the empty list is a fact about the config rather than a
    function that reports nothing whatever it is handed."""
    clean = {"project": "sample", REPLACEMENT: "/somewhere/bin/runner",
             "test_command": "echo tests-ok"}
    assert harness_config.retired_config_problems(clean) == []
    assert harness_config.retired_config_problems({**clean, RETIRED: "x"})


def test_this_repositorys_own_configuration_carries_the_replacement_and_not_it():
    """The shipped config was updated by this story rather than left to be
    refused by the harness it ships with."""
    config = harness_config.load_config(REPO_ROOT)
    assert config[REPLACEMENT]
    assert harness_config.retired_config_problems(config) == []
    # The control for that absence: the same check over the same configuration
    # with the retired key put back reports it.
    assert harness_config.retired_config_problems(
        {**config, RETIRED: config[REPLACEMENT]})


# --------------------------------------------------------------------------
# 2. The refusal, and what it leaves behind
# --------------------------------------------------------------------------


def test_a_run_whose_config_carries_the_retired_key_is_refused(
    retired_target, harness_root, capsys,
):
    code, _, _ = run(retired_target, harness_root)
    assert code == 1

    refusal = capsys.readouterr().err
    assert RETIRED in refusal
    assert REPLACEMENT in refusal
    # It says where to make the edit, not only that something is wrong.
    assert str(retired_target / ".harness" / "config.yaml") in refusal


def test_the_refusal_leaves_no_run_directory_no_state_no_branch_and_no_agent(
    retired_target, harness_root,
):
    """Read off the refused target's tree, as the story asks, rather than off
    the exit status alone. Its control is the next test, which makes the same
    four observations of the same fixture carrying the replacement key and
    finds all four present."""
    before = branches(retired_target)

    code, runner, run_dir = run(retired_target, harness_root)

    assert code == 1
    assert not run_dir.exists()
    assert not (run_dir / "state.json").exists()
    assert not (retired_target / ".harness" / "logs" /
                f"{STORY_ID}.log").exists()
    assert branches(retired_target) == before
    assert runner.calls == []


def test_the_same_fixture_with_the_replacement_key_creates_all_four(
    replacement_target, harness_root,
):
    """The control the absences above need, and the story's own criterion that
    a configuration carrying the new key runs to completion."""
    before = branches(replacement_target)

    code, runner, run_dir = run(replacement_target, harness_root)

    assert code == 0, runner.calls
    assert run_dir.is_dir()
    assert json.loads((run_dir / "state.json").read_text(
        encoding="utf-8"))["status"] == "completed"
    assert (replacement_target / ".harness" / "logs" /
            f"{STORY_ID}.log").is_file()
    assert branches(replacement_target) - before == {f"story/{STORY_ID}"}
    assert runner.calls == ["implementer", "tester", "verifier", "documenter"]


def test_the_replacement_key_is_the_runner_that_completed_run_recorded(
    replacement_target, harness_root,
):
    """Not merely that the run completed: the check it ran resolved the value
    the configuration named, which is what makes the control a control on the
    key rather than on the fixture."""
    _, _, run_dir = run(replacement_target, harness_root)
    record = json.loads(
        (run_dir / "clean-clone-result.json").read_text(encoding="utf-8"))
    assert record["runner"] == WORKING_RUNNER
    assert record["ran"] is True
    assert record["command"].startswith(WORKING_RUNNER)


# --------------------------------------------------------------------------
# 3. The ordering: above every other pre-flight
# --------------------------------------------------------------------------


def test_the_refusal_precedes_the_workflow_being_loaded_at_all(
    target_root, harness_root, capsys,
):
    """The strongest ordering evidence available, because the two outcomes are
    different in kind rather than in wording.

    A workflow name nothing ships cannot be loaded: without the retired key
    the run raises reading it. With the retired key it refuses cleanly, which
    can only happen if the retired-key check ran first — and the routing and
    self-route pre-flights read that workflow, so they are below it too.
    """
    configure(target_root, workflow="xyzzy-no-such-workflow")
    with pytest.raises(OSError):
        run(target_root, harness_root)

    configure(target_root, **{RETIRED: WORKING_RUNNER})
    code, runner, _ = run(target_root, harness_root)
    assert code == 1
    assert RETIRED in capsys.readouterr().err
    assert runner.calls == []


#: Later pre-flights, each broken in a way that produces its own refusal, and
#: a fragment of the message that refusal alone would print.
LATER_PREFLIGHTS = (
    ("a story artifact that does not exist", "story-404", "story-404.yaml"),
)


@pytest.mark.parametrize("case,story_id,fragment", LATER_PREFLIGHTS)
def test_the_retired_key_is_what_speaks_when_a_later_pre_flight_is_also_broken(
    target_root, harness_root, capsys, case, story_id, fragment,
):
    """Its own control, in the same test: the identical fixture without the
    retired key produces the later refusal, so the fixture really is broken in
    the second way and the retired key really is what displaced it."""
    commit_setup(target_root, "the fixture as it stands")
    later_code, later_runner, _ = run(target_root, harness_root, story_id)
    later = capsys.readouterr().err
    assert later_code == 1, case
    assert fragment in later, later
    assert later_runner.calls == []

    configure(target_root, **{RETIRED: WORKING_RUNNER})
    code, runner, _ = run(target_root, harness_root, story_id)
    refusal = capsys.readouterr().err
    assert code == 1
    assert RETIRED in refusal
    assert fragment not in refusal, refusal
    assert runner.calls == []


def test_a_dirty_tree_and_a_retired_key_together_report_the_retired_key(
    retired_target, harness_root, capsys,
):
    """The clean-tree pre-flight is the last one a developer meets before a
    run directory exists, and it is below this one too."""
    (retired_target / "dirty.txt").write_text("the developer's own\n",
                                              encoding="utf-8")

    code, runner, _ = run(retired_target, harness_root)

    refusal = capsys.readouterr().err
    assert code == 1
    assert RETIRED in refusal
    assert "dirty.txt" not in refusal, refusal
    assert runner.calls == []


def test_the_same_dirty_tree_alone_is_what_the_clean_tree_pre_flight_reports(
    replacement_target, harness_root, capsys,
):
    """The control for the test above: the dirty file really is a refusal of
    its own, so the retired key displaced something rather than being the only
    thing wrong."""
    (replacement_target / "dirty.txt").write_text("the developer's own\n",
                                                  encoding="utf-8")

    code, runner, _ = run(replacement_target, harness_root)

    assert code == 1
    assert "dirty.txt" in capsys.readouterr().err
    assert runner.calls == []


# --------------------------------------------------------------------------
# 4. The shipped configuration behaves as it did before the rename
#
# The rename is only safe if this repository's own check still exercises the
# environment it exercised yesterday. That is a differential question, so it
# is answered differentially: the configuration is read at this story's own
# baseline through the shared resolution in `tests/conftest.py`, never as
# `HEAD` and never as the working tree against the repository root — the
# coordinator commits the tree when a run completes, so those comparisons go
# vacuously green the moment this story commits.
#
# This module declares no origin, so its range is this story's own run commit
# against that commit's parent, which is exactly the pair being compared.
# --------------------------------------------------------------------------

CONFIG_FILE = ".harness/config.yaml"


def config_value(text: str, key: str) -> str | None:
    """One `key: value` line's value in a config file's text, or None.

    Written here rather than through `harness_config.load_config`, which takes
    a repository root and so cannot read a file's text at a revision. Comments
    are stripped, because the line this story cares most about carries one.
    """
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped.startswith(f"{key}:"):
            return stripped.partition(":")[2].strip()
    return None


def shipped_config_before() -> str:
    return conftest.repository_file_at(CONFIG_FILE, validation_file=THIS_FILE,
                                       bound=conftest.BASELINE)


def shipped_config_now() -> str:
    return (REPO_ROOT / CONFIG_FILE).read_text(encoding="utf-8")


def test_the_rename_carried_the_value_the_retired_key_held():
    """The retired key's value became the replacement's, unchanged, and the
    command it modifies is untouched.

    The absence — that the shipped file no longer carries the retired key — is
    controlled by the same reader finding that key in the same file at the
    baseline: it is looking in the right place with the right spelling.
    """
    before, now = shipped_config_before(), shipped_config_now()

    retired_value = config_value(before, RETIRED)
    assert retired_value, before
    assert config_value(now, RETIRED) is None
    assert config_value(now, REPLACEMENT) == retired_value
    assert config_value(now, "test_command") == config_value(before,
                                                             "test_command")


def test_the_check_builds_the_same_command_from_the_renamed_key(
    target_root, tmp_path, monkeypatch,
):
    """Through the real construction rather than through a restatement of it.

    Both pairs — the configuration as it was and as it is — are handed to
    `run_clean_clone` against the same throwaway repository, and the command
    and runner it records must be identical.

    Neither runs. The configured executable is a relative path, which
    `_resolve_interpreter` looks for under the target and then on PATH, and
    PATH's own lookup of a path with a separator in it is relative to the
    working directory — so the check is run from a throwaway directory, where
    it resolves nowhere and both calls refuse before a clone is built. That
    keeps this deterministic and free of any dependency on a second
    environment being installed wherever the suite runs.
    """
    monkeypatch.chdir(tmp_path)
    before, now = shipped_config_before(), shipped_config_now()

    was = story_coordinator.run_clean_clone(
        target_root, config_value(before, "test_command"),
        config_value(before, RETIRED), tmp_path / "was")
    is_now = story_coordinator.run_clean_clone(
        target_root, config_value(now, "test_command"),
        config_value(now, REPLACEMENT), tmp_path / "is-now")

    assert (was.ran, is_now.ran) == (False, False)
    assert is_now.runner == was.runner
    assert is_now.command == was.command
    # The pair is a comparison rather than two constants: the same call with a
    # runner the configuration does not name records a different command.
    other = story_coordinator.run_clean_clone(
        target_root, config_value(now, "test_command"),
        "/xyzzy/bin/something-else", tmp_path / "other")
    assert other.command != is_now.command


def test_the_record_that_configuration_produces_carries_no_version_field():
    """What the rename removed, asserted as a key set rather than as one
    absence: the record `run_clean_clone` builds is exactly the keys the
    schema declares, so a version field surviving under any spelling fails
    here."""
    record = story_coordinator.CleanCloneResult(
        ran=True, command="a-runner --all", runner="a-runner",
        clone_path="/somewhere", exit_code=0, output_tail="").as_record()
    schema = schema_validator.load_schema("clean-clone-result")

    assert set(record) <= set(schema["properties"])
    assert set(schema["required"]) == {"ran", "command", "runner"}
    assert "python" not in json.dumps(schema)
    # The control for that last absence: the same search over the same schema
    # with the retired spelling put back reports it.
    assert "python" in json.dumps({**schema, "properties": {
        **schema["properties"], "python": {"type": "string"}}})
