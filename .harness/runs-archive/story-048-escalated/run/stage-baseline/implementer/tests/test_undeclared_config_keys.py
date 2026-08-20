"""Independent validation for story-043's undeclared-key refusal.

A key a target's `.harness/config.yaml` carries that
`schemas/harness-config.schema.json` does not declare stops the run at
pre-flight. The declared set is the set of keys the harness reads, so a key
outside it is a key nothing will ever act on — a retired name left behind
after a rename, or a mistyping of a declared one. Both used to run: the
first because only `clean_clone_python` was refused by name, the second
because nothing looked at unknown keys at all, so `branch_prefixx: story/`
ran, quietly took the default, and the developer found out from the branch
name.

Written from the story's acceptance criteria rather than from the
implementation, at four altitudes:

  * **the function.** `harness_config.undeclared_config_problems` is a pure
    function over a loaded config, so it is driven directly: which keys it
    reports, in which order, and what each problem says.
  * **the refusal.** Throwaway targets carrying the retired key and a
    mistyped key are run through the real `story_coordinator.run_story` with
    a fake agent runner, and what the refusal *left behind* is read off the
    tree rather than inferred from the exit status.
  * **the ordering.** The refusal is claimed to sit above every other
    pre-flight. That is shown by breaking a later one and observing the
    undeclared key win — including a workflow name that cannot be loaded at
    all, which raises without the undeclared key and refuses cleanly with it.
  * **the configurations this repository ships.** Its own config, the
    template, and what `scripts/l5-init` writes must all load without
    refusal, which is the regression a strict rule is most likely to cause.

Every absence asserted here carries a demonstration that it can fail:

  * "the refused run created no run directory, no state file, no log, no
    branch and invoked no agent" sits beside the same fixture without the
    offending key, where the same five observations report all five;
  * "this configuration carries no undeclared key" sits beside the same
    check over the same configuration with an undeclared key put back;
  * "a comment naming a retired key is not refused" sits beside the same
    line with its `#` removed, which is refused;
  * "the retired mechanism's three names appear nowhere in the repository"
    sits beside the same scan asked for a name that does exist;
  * "the scan reports nothing under orchestration/ beyond the two
    legitimate mentions" sits beside a throwaway root with a tie planted
    under orchestration/, which the same scan reports.

Nothing here invokes a model: every run goes through the fake runner below.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import harness_config
import harness_source
import story_coordinator
from agent_runner import AgentResult
from conftest import commit_setup

import test_no_target_stack_in_harness_source as stack_module

REPO_ROOT = Path(harness_config.__file__).resolve().parents[1]

#: The key story-041 retired and story-043 stops naming. It is written here
#: from the story's words rather than imported from anything under test: the
#: point of this story is that no source file spells it any more, so a module
#: that read it out of the harness would have nowhere to read it from.
RETIRED = "clean_clone_python"

#: A mistyping of a declared key — the new capability. `branch_prefix` is
#: declared and defaults to `story/`, so before this story a run carrying
#: this spelling completed on the default branch name and said nothing.
MISTYPED = "branch_prefixx"

STORY_ID = "story-001"

#: A runner that exists on every platform this suite runs on, so a control
#: run's clean-clone check resolves it and the suite it runs exits zero.
WORKING_RUNNER = "/bin/echo"

#: The stem of the three names story-043 deletes. None of the three may appear
#: in the repository outside `.harness/runs/`, and this module is scanned along
#: with the rest of it — so the names are composed here rather than written,
#: or the scan below would report the module making the claim.
_STEM = "retired" + "_config_"

#: The mapping, the function that read it, and the coordinator's pre-flight.
DELETED_NAMES = (
    (_STEM + "keys").upper(),
    _STEM + "problems",
    "_refuse_" + _STEM + "keys",
)

#: The control for that absence: a name the change introduced, so the same
#: scan over the same files is known to be able to see a name at all.
SURVIVING_NAME = "undeclared_config_problems"

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
            _write_json(self.run_dir / "documenter-changed-files.json",
                        {"modified": [], "created": [], "deleted": []})
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


def deconfigure(target_root: Path, *keys: str) -> None:
    """Remove `key: value` lines from the target's config, and commit."""
    path = target_root / ".harness" / "config.yaml"
    kept = [line for line in path.read_text(encoding="utf-8").splitlines()
            if not any(line.startswith(f"{key}:") for key in keys)]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    commit_setup(target_root, "remove config keys for this test")


def git(root: Path, *args: str) -> str:
    """One git command against a repository built under tmp_path."""
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
def sound_target(target_root: Path) -> Path:
    """The shared fixture with a resolvable verification runner and nothing
    undeclared, so a run through it completes.

    Every refused fixture below is this one plus a key, so each difference a
    test reports is a difference that key made.
    """
    configure(target_root, verification_runner=WORKING_RUNNER)
    return target_root


@pytest.fixture(params=[RETIRED, MISTYPED], ids=["retired", "mistyped"])
def offending_key(request) -> str:
    """The two kinds of undeclared key the story names, so every guarantee
    below is asserted of both rather than of the retired one alone."""
    return request.param


# --------------------------------------------------------------------------
# 1. The function over a loaded config
# --------------------------------------------------------------------------


def test_the_retired_key_is_now_reported_as_a_key_the_harness_does_not_read():
    problems = harness_config.undeclared_config_problems(
        {"test_command": "echo ok", RETIRED: "/somewhere/bin/python"})
    assert len(problems) == 1, problems
    assert RETIRED in problems[0]


def test_a_mistyping_of_a_declared_key_is_reported_too():
    """The capability this story adds. Its own control sits in the same
    assertion: the correctly spelled key beside it is not reported."""
    problems = harness_config.undeclared_config_problems(
        {"branch_prefix": "story/", MISTYPED: "story/"})
    assert len(problems) == 1, problems
    assert MISTYPED in problems[0]
    assert "'branch_prefix'" not in problems[0], problems[0]


def test_every_problem_names_the_offending_key_and_lists_the_declared_set():
    declared = harness_config.declared_config_keys()
    problems = harness_config.undeclared_config_problems(
        {RETIRED: "x", MISTYPED: "y"})

    assert len(problems) == 2, problems
    for key, problem in zip((RETIRED, MISTYPED), problems):
        assert key in problem
        for name in declared:
            assert name in problem, (name, problem)


def test_the_declared_set_is_what_the_schema_carries_and_no_more():
    """This story's constraint was that it added no key and removed none; a
    later story adding one is a change to the schema, not to this refusal.

    Read out of the schema file itself rather than out of the function that
    reads it, so the two are compared rather than one restating the other.
    The count is not written here: `tests/test_config_keys_are_obeyed.py`
    owns the declared set and holds it to a proof per key, and a second copy
    of the number here would only ever go red for that story's reason.
    """
    schema = json.loads(
        (REPO_ROOT / "schemas" / "harness-config.schema.json").read_text(
            encoding="utf-8"))
    assert tuple(schema["properties"]) == harness_config.declared_config_keys()
    assert RETIRED not in schema["properties"]
    assert "project" not in schema["properties"]


def test_problems_come_back_in_the_order_the_config_carries_the_keys():
    forwards = harness_config.undeclared_config_problems(
        {"aaa": "1", "test_command": "echo ok", "zzz": "2"})
    backwards = harness_config.undeclared_config_problems(
        {"zzz": "2", "test_command": "echo ok", "aaa": "1"})

    assert [p.split("'")[1] for p in forwards] == ["aaa", "zzz"]
    assert [p.split("'")[1] for p in backwards] == ["zzz", "aaa"]


def test_a_config_carrying_only_declared_keys_yields_nothing():
    """Beside its control: the same config with one key added is reported, so
    the empty list is a fact about the config rather than a function that
    reports nothing whatever it is handed."""
    clean = {key: "value" for key in harness_config.declared_config_keys()}
    assert harness_config.undeclared_config_problems(clean) == []
    assert harness_config.undeclared_config_problems({**clean, MISTYPED: "x"})


def test_the_empty_config_yields_nothing():
    """No key is required — the schema's `required` is empty — so a config
    carrying nothing is a config carrying nothing undeclared."""
    assert harness_config.undeclared_config_problems({}) == []


# --------------------------------------------------------------------------
# 2. The refusal, and what it leaves behind
# --------------------------------------------------------------------------


def test_a_run_whose_config_carries_an_undeclared_key_is_refused(
    sound_target, harness_root, capsys, offending_key,
):
    configure(sound_target, **{offending_key: "whatever"})

    code, _, _ = run(sound_target, harness_root)

    assert code == 1
    refusal = capsys.readouterr().err
    assert offending_key in refusal
    # It says where to make the edit, not only that something is wrong.
    assert str(sound_target / ".harness" / "config.yaml") in refusal


def test_the_refusal_message_lists_the_keys_the_harness_does_declare(
    sound_target, harness_root, capsys, offending_key,
):
    """Its control is in the same assertion: a name that is not declared and
    is not the offending key must not appear, so "every declared name is in
    the text" is not satisfied by a message that names everything."""
    configure(sound_target, **{offending_key: "whatever"})

    run(sound_target, harness_root)

    refusal = capsys.readouterr().err
    for name in harness_config.declared_config_keys():
        assert name in refusal, name
    assert "clean_clone_interpreter" not in refusal, refusal


def test_the_refusal_leaves_no_run_directory_no_state_no_log_no_branch_and_no_agent(
    sound_target, harness_root, offending_key,
):
    """Read off the refused target's tree, as the story asks, rather than off
    the exit status alone. Its control is the next test, which makes the same
    five observations of the same fixture without the key and finds all five
    present."""
    configure(sound_target, **{offending_key: "whatever"})
    before = branches(sound_target)

    code, runner, run_dir = run(sound_target, harness_root)

    assert code == 1
    assert not run_dir.exists()
    assert not (run_dir / "state.json").exists()
    assert not (sound_target / ".harness" / "logs" / f"{STORY_ID}.log").exists()
    assert branches(sound_target) == before
    assert runner.calls == []


def test_the_same_fixture_without_the_key_creates_all_five(
    sound_target, harness_root,
):
    """The control the absences above need, and the story's own criterion
    that a config carrying nothing undeclared reaches its stages."""
    before = branches(sound_target)

    code, runner, run_dir = run(sound_target, harness_root)

    assert code == 0, runner.calls
    assert run_dir.is_dir()
    assert json.loads((run_dir / "state.json").read_text(
        encoding="utf-8"))["status"] == "completed"
    assert (sound_target / ".harness" / "logs" / f"{STORY_ID}.log").is_file()
    assert branches(sound_target) - before == {f"story/{STORY_ID}"}
    assert runner.calls == ["implementer", "tester", "documenter", "verifier"]


def test_removing_the_offending_key_from_a_refused_target_lets_it_run(
    sound_target, harness_root, offending_key,
):
    """The refusal's guidance is "remove or correct each key", so the same
    target with the key removed and nothing else changed must run. This is
    the control paired most tightly with the refusal: one line of the same
    file is the whole difference."""
    configure(sound_target, **{offending_key: "whatever"})
    assert run(sound_target, harness_root)[0] == 1

    deconfigure(sound_target, offending_key)

    code, runner, _ = run(sound_target, harness_root)
    assert code == 0, runner.calls
    assert runner.calls == ["implementer", "tester", "documenter", "verifier"]


def test_several_undeclared_keys_are_all_named_in_one_refusal(
    sound_target, harness_root, capsys,
):
    """A developer who mistyped two keys is told about both, rather than
    fixing one and meeting the next refusal."""
    configure(sound_target, **{RETIRED: "x", MISTYPED: "y",
                               "runs_dirr": ".harness/runs"})

    code, runner, _ = run(sound_target, harness_root)

    refusal = capsys.readouterr().err
    assert code == 1
    for key in (RETIRED, MISTYPED, "runs_dirr"):
        assert key in refusal, key
    assert runner.calls == []


def test_a_comment_naming_a_retired_or_unknown_key_is_not_refused(
    sound_target, harness_root,
):
    """load_config strips comments before any key is recorded, so a config
    documenting the key it used to carry still runs.

    Its control is the next test: the same line with its `#` removed is
    refused, so the comment really did carry the name and the pass is the
    stripping rather than the scan looking at the wrong file.
    """
    path = sound_target / ".harness" / "config.yaml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + f"# {RETIRED}: {WORKING_RUNNER}\n"
        + f"# {MISTYPED}: story/\n",
        encoding="utf-8")
    commit_setup(sound_target, "document the retired keys in a comment")

    code, runner, _ = run(sound_target, harness_root)

    assert code == 0, runner.calls
    assert runner.calls == ["implementer", "tester", "documenter", "verifier"]


def test_the_same_line_without_its_comment_marker_is_refused(
    sound_target, harness_root, capsys,
):
    """The control for the test above."""
    path = sound_target / ".harness" / "config.yaml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + f"{RETIRED}: {WORKING_RUNNER}\n",
        encoding="utf-8")
    commit_setup(sound_target, "the same line, uncommented")

    code, runner, _ = run(sound_target, harness_root)

    assert code == 1
    assert RETIRED in capsys.readouterr().err
    assert runner.calls == []


def test_a_trailing_comment_on_a_declared_key_is_not_read_as_a_key(
    sound_target, harness_root,
):
    """The other half of the stripping: a comment on the end of a good line
    does not become part of the key or a key of its own."""
    path = sound_target / ".harness" / "config.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "branch_prefix: story/",
            f"branch_prefix: story/  # not {MISTYPED}"),
        encoding="utf-8")
    commit_setup(sound_target, "a trailing comment naming an unknown key")

    code, runner, _ = run(sound_target, harness_root)

    assert code == 0, runner.calls


# --------------------------------------------------------------------------
# 3. The ordering: above every other pre-flight
# --------------------------------------------------------------------------


def test_the_refusal_precedes_the_workflow_being_loaded_at_all(
    sound_target, harness_root, capsys, offending_key,
):
    """The strongest ordering evidence available, because the two outcomes
    are different in kind rather than in wording.

    A workflow name nothing ships cannot be loaded: without the undeclared
    key the run raises reading it. With the undeclared key it refuses
    cleanly, which can only happen if the undeclared-key check ran first —
    and the routing and self-route pre-flights read that workflow, so they
    are below it too.
    """
    configure(sound_target, workflow="xyzzy-no-such-workflow")
    with pytest.raises(OSError):
        run(sound_target, harness_root)

    configure(sound_target, **{offending_key: "whatever"})
    code, runner, _ = run(sound_target, harness_root)

    assert code == 1
    assert offending_key in capsys.readouterr().err
    assert runner.calls == []


def test_the_undeclared_key_is_what_speaks_when_the_story_artifact_is_missing(
    sound_target, harness_root, capsys, offending_key,
):
    """Its own control, in the same test: the identical fixture without the
    undeclared key produces the later refusal, so the fixture really is
    broken in the second way and the undeclared key really displaced it."""
    missing = "story-404"
    later_code, later_runner, _ = run(sound_target, harness_root, missing)
    later = capsys.readouterr().err
    assert later_code == 1
    assert f"{missing}.yaml" in later, later
    assert later_runner.calls == []

    configure(sound_target, **{offending_key: "whatever"})
    code, runner, _ = run(sound_target, harness_root, missing)

    refusal = capsys.readouterr().err
    assert code == 1
    assert offending_key in refusal
    assert f"{missing}.yaml" not in refusal, refusal
    assert runner.calls == []


def test_a_dirty_tree_and_an_undeclared_key_together_report_the_key(
    sound_target, harness_root, capsys, offending_key,
):
    """The clean-tree pre-flight is the last one a developer meets before a
    run directory exists, and it is below this one too."""
    configure(sound_target, **{offending_key: "whatever"})
    (sound_target / "dirty.txt").write_text("the developer's own\n",
                                            encoding="utf-8")

    code, runner, _ = run(sound_target, harness_root)

    refusal = capsys.readouterr().err
    assert code == 1
    assert offending_key in refusal
    assert "dirty.txt" not in refusal, refusal
    assert runner.calls == []


def test_the_same_dirty_tree_alone_is_what_the_clean_tree_pre_flight_reports(
    sound_target, harness_root, capsys,
):
    """The control for the test above: the dirty file really is a refusal of
    its own, so the undeclared key displaced something rather than being the
    only thing wrong."""
    (sound_target / "dirty.txt").write_text("the developer's own\n",
                                            encoding="utf-8")

    code, runner, _ = run(sound_target, harness_root)

    assert code == 1
    assert "dirty.txt" in capsys.readouterr().err
    assert runner.calls == []


# --------------------------------------------------------------------------
# 4. The configurations this repository ships
#
# A strict rule refuses every config carrying a key nothing reads, and this
# repository shipped three such configs before the story: its own, the
# template, and whatever l5-init wrote from that template. Each is loaded
# through the real reader and put through the real pre-flight predicate.
# --------------------------------------------------------------------------


def test_this_repositorys_own_configuration_carries_no_undeclared_key():
    config = harness_config.load_config(REPO_ROOT)
    assert harness_config.undeclared_config_problems(config, REPO_ROOT) == []
    # The control for that absence: the same check over the same config with
    # a key put back reports it, so the empty list is about this file rather
    # than about a predicate that reports nothing.
    assert harness_config.undeclared_config_problems(
        {**config, "project": "level-five"}, REPO_ROOT)


def test_the_template_carries_no_undeclared_key(tmp_path):
    """Read through the real loader against a throwaway target, because the
    template becomes a target's config verbatim but for one substitution."""
    target = tmp_path / "from-template"
    (target / ".harness").mkdir(parents=True)
    text = (REPO_ROOT / "templates" / "config.yaml").read_text(encoding="utf-8")
    (target / ".harness" / "config.yaml").write_text(
        text.replace("{test_command}", "echo tests-ok"), encoding="utf-8")

    config = harness_config.load_config(target)
    assert harness_config.undeclared_config_problems(config, REPO_ROOT) == []
    assert harness_config.undeclared_config_problems(
        {**config, "project": "sample"}, REPO_ROOT)


def test_the_template_carries_no_substitution_placeholder_but_the_command(
    tmp_path,
):
    """`{project}` left the template, so l5-init has nothing to substitute
    for it. Its control is `{test_command}`, the placeholder that remains:
    the same search over the same text finds that one."""
    text = (REPO_ROOT / "templates" / "config.yaml").read_text(encoding="utf-8")
    assert "{project}" not in text
    assert "{test_command}" in text


def test_l5_init_writes_a_config_a_run_would_not_refuse(tmp_path):
    """The freshly initialised target, built by running the real script.

    Its control is the same check over the same produced file with a key
    appended, which is reported — so "no undeclared key" is a fact about
    what l5-init wrote.
    """
    target = tmp_path / "fresh"
    target.mkdir()
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "l5-init"),
         "--test-command", "echo tests-ok"],
        cwd=target, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    written = (target / ".harness" / "config.yaml").read_text(encoding="utf-8")
    assert "{project}" not in written, written
    assert "echo tests-ok" in written

    config = harness_config.load_config(target)
    assert harness_config.undeclared_config_problems(config, REPO_ROOT) == []
    assert harness_config.undeclared_config_problems(
        {**config, "project": "fresh"}, REPO_ROOT)


def test_a_freshly_initialised_target_can_run_a_story(
    tmp_path, target_root, harness_root,
):
    """Not merely that l5-init's config loads: a story run against it reaches
    its stages, which is the guarantee the story states.

    The config l5-init writes is copied over the shared fixture's, so the
    story artifact, standards and git repository the fixture builds are
    reused and the only thing under test is the configuration.
    """
    fresh = tmp_path / "fresh-init"
    fresh.mkdir()
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "l5-init"),
         "--test-command", "echo tests-ok"],
        cwd=fresh, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    shutil.copyfile(fresh / ".harness" / "config.yaml",
                    target_root / ".harness" / "config.yaml")
    configure(target_root, verification_runner=WORKING_RUNNER)

    code, runner, _ = run(target_root, harness_root)
    assert code == 0, (runner.calls, result.stdout)
    assert runner.calls == ["implementer", "tester", "documenter", "verifier"]


#: The declared set, read once, for deciding which runs of lines in a test
#: module's source are a configuration rather than some other `key: value`
#: text — a story artifact, a dict literal, a rendered YAML block.
DECLARED = harness_config.declared_config_keys(REPO_ROOT)

_CONFIG_KEY = re.compile(r"^([a-z_][a-z_0-9]*):(\s.*)?$")


def _config_subset_line(raw: str) -> str | None:
    """One source line reduced to the configuration line it carries, or None.

    Fixture configurations reach a target's `config.yaml` two ways: as a
    triple-quoted block written verbatim, and as quoted list items joined
    before being written. Both are the same subset once the surrounding
    quoting, the trailing comma and a trailing escaped newline come off.
    """
    line = raw.strip().rstrip(",")
    if len(line) >= 2 and line[0] == line[-1] and line[0] in "\"'":
        line = line[1:-1]
    if line.endswith("\\n"):
        line = line[:-2]
    if line.lstrip().startswith("- ") or _CONFIG_KEY.match(line):
        return line
    return None


def _fixture_configurations(text: str) -> list[list[str]]:
    """Every run of lines in a module's source that is a configuration.

    A run qualifies when at least two of its keys are declared ones and they
    are the majority, which is what separates a fixture config from the
    story artifacts and rendered YAML that share its shape. The majority
    rule is deliberately not "all declared": a run carrying an undeclared
    key is exactly what this must still return, or the sweep would skip the
    thing it is looking for.
    """
    found, block = [], []
    for raw in [*text.splitlines(), ""]:
        line = _config_subset_line(raw)
        if line is not None:
            block.append(line)
            continue
        keys = [match.group(1) for match in
                (_CONFIG_KEY.match(item) for item in block) if match]
        declared = [key for key in keys if key in DECLARED]
        if len(declared) >= 2 and len(declared) * 2 >= len(keys):
            found.append(block)
        block = []
    return found


def test_no_fixture_configuration_under_tests_carries_an_undeclared_key(
    tmp_path,
):
    """The sweep the story asked for, asserted rather than trusted, and for
    any undeclared key rather than for `project` alone.

    Every `.harness/config.yaml` a module under tests/ writes verbatim is a
    run of lines in that module's source, so the runs are found, each is
    written to a throwaway target, read through the real loader and put
    through the real predicate. A key nothing declares is reported whatever
    it is called — the `project` the sweep removed, or a mistyping nobody
    has made yet.
    """
    def problems_for(lines: list[str]) -> list[str]:
        target = tmp_path / "probe"
        (target / ".harness").mkdir(parents=True, exist_ok=True)
        (target / ".harness" / "config.yaml").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        return harness_config.undeclared_config_problems(
            harness_config.load_config(target), REPO_ROOT)

    # Three controls. The predicate reports a planted `project`, reports a
    # planted key that is not `project` — the widening this test exists for —
    # and the reader finds a configuration planted the way the fixtures carry
    # theirs, so a sweep that reports nothing is a sweep that looked.
    good = ["workflow: story-workflow", "branch_prefix: story/",
            "test_command: echo tests-ok"]
    assert problems_for(good) == []
    assert problems_for([*good, "project: sample"])
    assert problems_for([*good, f"{MISTYPED}: story/"])

    planted = _fixture_configurations(
        'CONFIG = """\\\n' + "\n".join([*good, "project: sample"]) + '\n"""\n')
    assert len(planted) == 1, planted
    assert problems_for(planted[0])

    scanned, offenders = [], []
    for path in sorted((REPO_ROOT / "tests").glob("*.py")):
        for block in _fixture_configurations(path.read_text(encoding="utf-8")):
            scanned.append(path.name)
            offenders += [f"{path.name}: {problem}"
                          for problem in problems_for(block)]

    assert offenders == [], offenders
    # And the sweep really reached the fixtures. Completeness is checked
    # against a signal the reader had no part in: every module carrying a
    # `workflow:` line, which every fixture configuration opens with, must be
    # a module the reader returned a configuration for. A reader that found
    # none of them would report no offender just as happily.
    carriers = {path.name for path in (REPO_ROOT / "tests").glob("*.py")
                if "\nworkflow: " in path.read_text(encoding="utf-8")}
    assert carriers, "the completeness signal itself found nothing"
    assert carriers <= set(scanned), sorted(carriers - set(scanned))
    assert "conftest.py" in scanned, scanned


def test_the_dict_built_fixture_configuration_carries_no_undeclared_key():
    """The one fixture configuration the sweep above cannot see.

    story-039's proofs build their config from a dict and render it, so no
    run of `key: value` lines exists in that module's source to be found.
    The dict is asked directly instead. Its control is the same predicate
    over the same fixture with a key added, which `fixture_config` accepts
    because it takes arbitrary overrides.
    """
    import test_config_keys_are_obeyed as obeyed

    assert harness_config.undeclared_config_problems(
        obeyed.fixture_config(), REPO_ROOT) == []
    assert harness_config.undeclared_config_problems(
        obeyed.fixture_config(project="level-five"), REPO_ROOT)


# --------------------------------------------------------------------------
# 5. The mechanism is gone, not joined by a sibling
# --------------------------------------------------------------------------


def _scanned_sources() -> list[Path]:
    """Every file that is harness source rather than harness prose.

    `.harness/` is excluded whole. The story excludes `.harness/runs/`
    explicitly, and the same reason covers the rest of that directory: it is
    the harness's record of its own work, not the harness. `.harness/stories/`
    holds the story artifact that *asked* for the deletion, which necessarily
    names what it deletes, and `.harness/docs/ARCHITECTURE.md` is a history
    that may keep describing a mechanism in the past tense. Neither is a place
    the mechanism could survive: what "deleted rather than joined by a sibling"
    means is that no code, schema, prompt, workflow, rule or test spells it,
    which is what the directories below hold.

    The tracked set is the working tree's `tests/*.py` united with what `git
    ls-files` reports, and not `git ls-files` alone. A test file written by
    this stage is untracked until the coordinator's `git add -A` at commit, so
    a scan of the tracked set alone cannot see the module the claim is made
    in: it would pass while this file spelled all three names, and start
    failing the moment the story committed. The union puts the claimant inside
    its own claim, which is what `test_the_scan_reaches_this_module` checks.
    """
    listed = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files"],
                            capture_output=True, text=True,
                            check=True).stdout.split()
    paths = {REPO_ROOT / name for name in listed
             if not name.startswith(".harness/")}
    paths.update((REPO_ROOT / "tests").glob("*.py"))
    return sorted(path for path in paths if path.is_file())


def _files_mentioning(name: str) -> list[str]:
    found = []
    for path in _scanned_sources():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if name in text:
            found.append(str(path.relative_to(REPO_ROOT)))
    return found


@pytest.mark.parametrize("name", DELETED_NAMES)
def test_the_retired_mechanisms_names_appear_nowhere_in_the_repository(name):
    """Its control is the next test: the same scan over the same files finds
    a name that does exist, so an empty result here means the name is gone
    rather than that the scan is reading nothing."""
    assert _files_mentioning(name) == []


def test_the_scan_that_found_nothing_can_find_something():
    """The negative control for the three assertions above."""
    found = _files_mentioning(SURVIVING_NAME)
    assert "orchestration/harness_config.py" in found, found
    assert "orchestration/story_coordinator.py" in found, found


def test_the_scan_reaches_this_module():
    """The other half of that control, and the one the absence needs most.

    The three names are gone from the harness; the place they are likeliest to
    survive is a test module *about* their deletion. So this asserts that the
    scanned set contains this file — had it not, the absence above would be
    green while this module spelled all three, and would go red on its own
    the moment the run committed and `git ls-files` began reporting it.

    A name written into this module is therefore found by the same scan, which
    is what the second assertion shows: `SURVIVING_NAME` is spelled here, and
    the scan reports this file for it.
    """
    scanned = {str(path.relative_to(REPO_ROOT)) for path in _scanned_sources()}
    here = str(Path(__file__).resolve().relative_to(REPO_ROOT))
    assert here in scanned, sorted(name for name in scanned
                                   if name.startswith("tests/"))
    assert here in _files_mentioning(SURVIVING_NAME)


def test_no_retirement_mapping_survives_on_harness_config():
    """The story's constraint that the refusal replaces the retired-key
    refusal rather than joining it: no attribute of the module maps a name to
    a replacement any more."""
    mappings = {name: value
                for name, value in vars(harness_config).items()
                if isinstance(value, dict) and not name.startswith("__")}
    assert mappings == {}, mappings
    # Named through DELETED_NAMES rather than written out, for the reason
    # given there. Their control is the assertion beneath, which shows the
    # same hasattr over the same two modules seeing what does exist.
    _mapping, retired_problems, retired_refusal = DELETED_NAMES
    assert not hasattr(harness_config, retired_problems)
    assert not hasattr(story_coordinator, retired_refusal)
    assert hasattr(harness_config, SURVIVING_NAME)
    assert hasattr(story_coordinator, "_refuse_undeclared_config_keys")


# --------------------------------------------------------------------------
# 6. The last language name has left orchestration/
# --------------------------------------------------------------------------


def test_the_scan_reports_only_the_two_legitimate_mentions_under_orchestration():
    """The point of deleting the literal. Its control is the next test."""
    under_orchestration = {
        (finding.path, finding.line.strip())
        for finding in harness_source.scan(REPO_ROOT)
        if finding.path.startswith("orchestration/")
    }
    expected = {(path, line) for (path, line) in stack_module.PERMANENT_MENTIONS
                if path.startswith("orchestration/")}

    assert {path for path, _ in under_orchestration} == {
        "orchestration/story_parser.py",
        "orchestration/story_coordinator.py",
    }
    assert under_orchestration == {(path, line.strip())
                                   for path, line in expected}


def test_the_same_scan_reports_a_tie_planted_under_orchestration(tmp_path):
    """The negative control for the absence above, built against a throwaway
    root rather than by editing this one."""
    root = tmp_path / "throwaway"
    (root / "orchestration").mkdir(parents=True)
    (root / "orchestration" / "planted.py").write_text(
        "COMMAND = 'pytest tests/'\n", encoding="utf-8")

    findings = harness_source.scan(root)

    assert [f.path for f in findings] == ["orchestration/planted.py"]


def test_permanent_mentions_holds_eight_and_none_is_harness_config():
    """Read off the list the other module owns, because this story's edit to
    it is one of its acceptance criteria."""
    mentions = stack_module.PERMANENT_MENTIONS
    assert len(mentions) == 8, sorted(mentions)
    assert not [path for path, _ in mentions
                if path == "orchestration/harness_config.py"]
    # The control for that absence: the same comprehension over the same list
    # finds the orchestration path that is still there.
    assert [path for path, _ in mentions
            if path == "orchestration/story_parser.py"]


def test_harness_config_no_longer_spells_any_stack_token():
    """The story's stated outcome, asserted of the file rather than of the
    scan's allowlist. Its control is the same matcher over the same file's
    text with the retired key put back."""
    source = (REPO_ROOT / "orchestration" / "harness_config.py").read_text(
        encoding="utf-8")
    assert not harness_source.STACK_PATTERN.search(source), source
    assert harness_source.STACK_PATTERN.search(
        source + f"\nRETIRED = {{'{RETIRED}': 'verification_runner'}}\n")
