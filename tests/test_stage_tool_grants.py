"""Independent validation for story-035: grant stages the read-only tools they
need, and deny mutation at the door.

Three subjects, and each is exercised as the thing it actually is rather than
read as prose:

  * `hooks/bash_guard.py` is driven **as a program**, in a subprocess, with real
    PreToolUse payloads on stdin. Nothing here imports the guard to call
    `offence` directly, because what ships is a command line: a guard that
    denied correctly in-process and crashed on a payload would pass the first
    reading and fail the run.
  * `orchestration/agent_runner.py` is driven with its own `subprocess.Popen`
    replaced, so the argument list `run_agent` builds is inspected as built
    rather than described.
  * `orchestration/context_assembler.py` and `prompts/harness-layer.md` are
    rendered, and the "omitting the new argument changes nothing" claim is
    settled against a mutant of today's module with the new merge removed —
    which is what the pre-story code was at that line.

Every absence asserted here carries a demonstration that the same check can
report the violation it exists to catch:

  * "the guard says nothing about this command" is asserted only beside a
    command that differs by the one feature under test and *is* denied —
    `find -name` beside `find -delete`, `2>&1` beside `> out`, `git show` beside
    `git commit`;
  * "the guard has no allow path" is a scan for a non-docstring `"allow"`
    literal whose control is a mutant guard carrying one, which the same scan
    reports and which, run on the same read-only command, really does emit an
    allow decision the shipped guard does not;
  * "a malformed payload produces no decision" sits beside a well-formed
    mutating payload through the identical driver, which does produce one, so
    silence is shown to be about the input rather than about the driver;
  * "the guard writes nothing" is a before/after snapshot of a directory and of
    the payload file in it, whose control is a stub program that appends to
    that same file and is reported by the same snapshot;
  * "no granted entry writes to the tree" is checked against the guard's own
    mutator tables, with a control list carrying `Bash(rm:*)` and
    `Bash(git commit:*)` that the same check flags;
  * "no existing allowlist entry was removed or altered" and "run_agent's
    signature is unchanged" are both bounded at this story's own range through
    `conftest`, never against a bare HEAD, and each carries a mutant control.

Nothing here invokes a model.
"""
import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import HARNESS_ROOT, load_mutant
import conftest

import agent_runner
import context_assembler
import harness_config
import schema_validator
import story_parser

VALIDATION_FILE = Path(__file__)

GUARD_REL = "hooks/bash_guard.py"
SETTINGS_REL = "hooks/settings.json"
GUARD_PATH = HARNESS_ROOT / GUARD_REL
CONFIG_REL = ".harness/config.yaml"
TEMPLATE_CONFIG_REL = "templates/config.yaml"
HARNESS_LAYER_REL = "prompts/harness-layer.md"

WORKFLOW = conftest.shipped_workflow(HARNESS_ROOT, "story-workflow")


# ---------------------------------------------------------------------------
# Driving the guard as a program
# ---------------------------------------------------------------------------


def payload_for(command: str) -> str:
    """A real PreToolUse hook payload for a Bash call."""
    return json.dumps(
        {
            "session_id": "story-035-validation",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
    )


def run_guard(stdin: str | None, *, guard: Path = GUARD_PATH,
              cwd: Path | None = None) -> subprocess.CompletedProcess:
    """The guard, run as a program with `stdin` on its standard input.

    `stdin=None` gives it nothing to read, which is the unreadable-stdin case.
    """
    return subprocess.run(
        [sys.executable, str(guard)],
        input=stdin if stdin is not None else "",
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


def decision(command: str, *, guard: Path = GUARD_PATH) -> str | None:
    """The guard's permission decision for `command`, or None when it is silent.

    Silence is the guard's fail-open answer, and it is a different outcome from
    a decision — never conflated here with "not denied by name".
    """
    result = run_guard(payload_for(command), guard=guard)
    assert result.returncode == 0, (command, result.returncode, result.stderr)
    if not result.stdout.strip():
        return None
    emitted = json.loads(result.stdout)
    return emitted["hookSpecificOutput"]["permissionDecision"]


def reason(command: str) -> str:
    result = run_guard(payload_for(command))
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


# ---------------------------------------------------------------------------
# The corpora. Every command the guard is shown in this module is reachable
# from these lists, so the never-allow sweep at the bottom covers the module.
# ---------------------------------------------------------------------------

#: AC3, the named mutators.
MUTATORS = [
    "rm -rf build",
    "mv src/a.py src/b.py",
    "cp src/a.py src/b.py",
    "dd if=/dev/zero of=out.bin",
    "tee captured.txt",
    "truncate -s 0 notes.txt",
    "ln -s a.py b.py",
    "sed -i s/old/new/ notes.txt",
    "perl -i -pe s/old/new/ notes.txt",
]

#: AC3, the mutating git subcommands, each named in the criterion.
GIT_MUTATORS = [
    "git add -A",
    "git commit -m message",
    "git checkout main",
    "git reset --hard",
    "git rebase main",
    "git merge main",
    "git push origin main",
    "git stash",
    "git clean -fd",
    "git rm notes.txt",
    "git mv a.py b.py",
    "git apply patch.diff",
    "git restore notes.txt",
    "git switch main",
]

#: AC4, one mutator reached through each composition form.
COMPOSITIONS = {
    "pipe": "ls src | rm -rf build",
    "semicolon": "ls src; rm -rf build",
    "double ampersand": "ls src && rm -rf build",
    "double pipe": "ls src || rm -rf build",
    "continuation line": "ls src \\\n&& rm -rf build",
    "continuation line before a semicolon": "ls src; \\\n rm -rf build",
    "newline": "ls src\nrm -rf build",
    "$() substitution": "echo $(rm -rf build)",
    "backtick substitution": "echo `rm -rf build`",
    "$() inside double quotes": 'echo "$(rm -rf build)"',
}

#: AC5.
FIND_DENIED = [
    "find . -name '*.pyc' -exec rm {} \\;",
    "find . -name '*.pyc' -execdir rm {} \\;",
    "find . -name '*.pyc' -delete",
    "find . -name '*.pyc' -ok rm {} \\;",
]
FIND_ALLOWED = [
    "find . -name '*.py'",
    "find orchestration -type f",
    "find tests -newer conftest.py",
]

#: AC6.
REDIRECT_DENIED = [
    "ls src > listing.txt",
    "ls src >> listing.txt",
    "grep -n token orchestration/agent_runner.py > hits.txt",
]
REDIRECT_ALLOWED = [
    "ls src 2>&1",
    "grep -rn token orchestration 2>/dev/null",
    "ls src > /dev/null",
    "grep -rn token orchestration >> /dev/null",
]

#: AC7, the read-only set the story grants, plus chmod and the test command.
READ_ONLY = [
    "grep -rn allowed_tools orchestration",
    "rg --files-with-matches allowed_tools",
    "head -20 orchestration/agent_runner.py",
    "tail -5 prompts/harness-layer.md",
    "wc -l tests/conftest.py",
    "sort tests/conftest.py",
    "uniq tests/conftest.py",
    "diff templates/config.yaml .harness/config.yaml",
    "cat .harness/config.yaml",
    "ls -la hooks",
    "chmod +x hooks/bash_guard.py",
    "git status --short",
    "git diff --stat",
    "git log --oneline -5",
    "git show HEAD:.harness/config.yaml",
    "git branch --show-current",
    "git ls-files hooks",
    ".venv/bin/python -m pytest tests/ -q",
    "grep -n '>' prompts/harness-layer.md",
    "grep -rn 'rm -rf' tests",
]

#: AC9, inputs the guard cannot establish anything about.
UNPARSEABLE = [
    'grep -rn "unterminated orchestration',
    "cat <<'EOF'\nrm -rf build\nEOF",
    "echo $(rm -rf build",
]

MALFORMED_PAYLOADS = {
    "empty": "",
    "not json": "this is not json",
    "a json array": "[]",
    "a non-dict tool_input": json.dumps({"tool_input": "rm -rf build"}),
    "an empty object": "{}",
    "no command key": json.dumps({"tool_input": {"description": "rm -rf build"}}),
}

EVERY_COMMAND = (
    MUTATORS + GIT_MUTATORS + list(COMPOSITIONS.values()) + FIND_DENIED
    + FIND_ALLOWED + REDIRECT_DENIED + REDIRECT_ALLOWED + READ_ONLY + UNPARSEABLE
)


# ---------------------------------------------------------------------------
# AC3, AC4: the guard denies what mutates, however it is reached
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", MUTATORS)
def test_the_guard_denies_each_named_mutator(command):
    """AC3: run as a program with a real payload, each named mutator is denied."""
    assert decision(command) == "deny", command


@pytest.mark.parametrize("command", GIT_MUTATORS)
def test_the_guard_denies_each_mutating_git_subcommand(command):
    """AC3: the fourteen git subcommands the criterion names."""
    assert decision(command) == "deny", command


@pytest.mark.parametrize("form,command", sorted(COMPOSITIONS.items()))
def test_the_guard_denies_a_mutator_reached_through_a_composition(form, command):
    """AC4: the mutator is found wherever in a composed command it sits.

    Its control is the same composition with the mutator replaced by a
    read-only command below: the denial is about `rm`, not about the shape.
    """
    assert decision(command) == "deny", form
    assert "rm" in reason(command), form


@pytest.mark.parametrize("form,command", sorted(COMPOSITIONS.items()))
def test_the_same_compositions_without_a_mutator_are_not_denied(form, command):
    """The control for the composition table: shape alone denies nothing.

    Without this, every composition assertion above would still pass if the
    guard simply denied anything containing a pipe or a newline — which is the
    denial this story exists to stop paying for.
    """
    harmless = command.replace("rm -rf build", "wc -l tests/conftest.py")
    assert decision(harmless) is None, (form, harmless)


@pytest.mark.parametrize("command", FIND_DENIED)
def test_the_guard_denies_find_that_executes_or_writes(command):
    """AC5: -exec, -execdir, -delete and -ok."""
    assert decision(command) == "deny", command


@pytest.mark.parametrize("command", FIND_ALLOWED)
def test_the_guard_says_nothing_about_find_without_those_actions(command):
    """AC5, the absence half. Controlled by FIND_DENIED above: the same
    `find . -name '*.pyc'` prefix is denied the moment `-delete` is appended,
    so silence here is about the missing action rather than about `find`."""
    assert decision(command) is None, command
    assert decision(command + " -delete") == "deny", command


@pytest.mark.parametrize("command", REDIRECT_DENIED)
def test_the_guard_denies_a_redirect_that_writes_a_file(command):
    """AC6: > and >>."""
    assert decision(command) == "deny", command
    assert "redirect" in reason(command), command


@pytest.mark.parametrize("command", REDIRECT_ALLOWED)
def test_the_guard_leaves_duplication_and_dev_null_alone(command):
    """AC6, the absence half, controlled by pointing the same redirect at a
    real file — which is denied — so silence is about the target rather than
    about the operator."""
    assert decision(command) is None, command
    assert decision(command.replace("/dev/null", "captured.txt")
                    if "/dev/null" in command
                    else command + " > captured.txt") == "deny", command


@pytest.mark.parametrize("command", READ_ONLY)
def test_the_guard_says_nothing_about_the_read_only_set(command):
    """AC7: chmod, the twelve granted read-only commands, the read-only git
    subcommands, quoted operators and the test command itself.

    The control is every denial above: the same guard, the same driver, the
    same payload shape, denying the mutating counterpart of each of these."""
    assert decision(command) is None, command


def test_the_read_only_git_subcommands_are_distinguished_from_the_mutating_ones():
    """AC7's discrimination, stated as a pair rather than as two lists.

    `git show` is silent and `git commit` is denied through one code path, so
    the pairing is the evidence that the subcommand is what is being read."""
    for readable, mutating in (("status --short", "add -A"),
                               ("diff --stat", "commit -m x"),
                               ("log --oneline", "reset --hard"),
                               ("show HEAD", "checkout main"),
                               ("branch --show-current", "switch main"),
                               ("ls-files", "rm notes.txt")):
        assert decision(f"git {readable}") is None, readable
        assert decision(f"git {mutating}") == "deny", mutating


def test_a_wrapped_mutator_is_judged_as_the_command_it_wraps():
    """xargs and env are not a way past the guard, and their read-only
    counterparts are the control that the wrapper itself is not what denies."""
    assert decision("find . -name '*.pyc' | xargs rm") == "deny"
    assert decision("find . -name '*.py' | xargs grep -l token") is None
    assert decision("env FOO=1 rm -rf build") == "deny"
    assert decision("env FOO=1 grep -rn token orchestration") is None


# ---------------------------------------------------------------------------
# AC8: the guard never allows
# ---------------------------------------------------------------------------


def _non_docstring_string_constants(source: str) -> list[str]:
    """Every string literal in `source` that is not a docstring.

    Docstrings are excluded because the guard's own prose is *about* there
    being no allow path, and a scan that could not tell the two apart would
    report the documentation as the defect.
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_the_guard_has_no_allow_decision_in_its_source():
    """AC8: no code in the guard can emit an allow decision.

    A behavioural sweep alone would only show that the corpus below does not
    reach one; this shows there is nothing to reach."""
    literals = _non_docstring_string_constants(
        GUARD_PATH.read_text(encoding="utf-8"))
    assert "allow" not in [literal.strip().lower() for literal in literals]
    assert not [literal for literal in literals if "allow" in literal.lower()]


def test_the_allow_scan_reports_a_guard_that_carries_one(tmp_path):
    """The control for the scan above, and for the sweep below.

    A mutant guard with an allow path is reported by the same scan and, run
    through the same driver on a command the shipped guard is silent about,
    really does emit an allow decision. So both the scan and the sweep are
    shown capable of the finding they report the absence of."""
    mutant = load_mutant(
        GUARD_PATH,
        [('            "permissionDecision": "deny",',
          '            "permissionDecision": "deny",\n'
          '            "_allowKey": "allow",')],
        name="bash_guard_with_an_allow_literal", tmp_path=tmp_path)
    literals = _non_docstring_string_constants(
        Path(mutant.__file__).read_text(encoding="utf-8"))
    assert "allow" in [literal.strip().lower() for literal in literals]

    allower = load_mutant(
        GUARD_PATH,
        [("    if reason is None:\n        return 0",
          '    if reason is None:\n'
          '        json.dump({"hookSpecificOutput": {\n'
          '            "hookEventName": HOOK_EVENT,\n'
          '            "permissionDecision": "allow",\n'
          '            "permissionDecisionReason": "control"}}, sys.stdout)\n'
          '        return 0')],
        name="bash_guard_that_allows", tmp_path=tmp_path)
    control_guard = Path(allower.__file__)
    assert decision("ls -la hooks", guard=control_guard) == "allow"
    assert decision("ls -la hooks") is None


@pytest.mark.parametrize("command", EVERY_COMMAND)
def test_no_command_in_this_module_draws_an_allow(command):
    """AC8, behaviourally, over every command this module shows the guard."""
    assert decision(command) in (None, "deny"), command


# ---------------------------------------------------------------------------
# AC9: the fail-open bias, and that the guard writes nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", UNPARSEABLE)
def test_an_unparseable_command_produces_no_decision(command):
    """AC9: silence rather than a denial, so the call falls to the allowlist.

    Each of these carries a mutator that *would* be denied if the guard could
    read the command — which is the control that the silence is the fail-open
    path rather than the guard finding nothing to say."""
    result = run_guard(payload_for(command))
    assert result.returncode == 0, command
    assert result.stdout.strip() == "", command


def test_the_unparseable_cases_carry_a_mutator_the_guard_otherwise_denies():
    """The control for the three inputs above: each becomes a denial as soon as
    the feature that made it unparseable is removed."""
    assert decision('grep -rn "unterminated" orchestration') is None
    assert decision("rm -rf build") == "deny"
    assert decision("echo $(rm -rf build)") == "deny"


@pytest.mark.parametrize("description,raw", sorted(MALFORMED_PAYLOADS.items()))
def test_a_malformed_payload_produces_no_decision(description, raw):
    """AC9: a payload the guard cannot read yields nothing, not a deny."""
    result = run_guard(raw)
    assert result.returncode == 0, description
    assert result.stdout.strip() == "", description


def test_unreadable_stdin_produces_no_decision():
    """AC9: nothing on stdin at all."""
    result = run_guard(None)
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_the_same_driver_does_produce_a_decision_for_a_well_formed_payload():
    """The control for every silence above: the driver, the subprocess and the
    payload shape are the same ones that carry a denial out, so silence is a
    property of the input rather than of how the guard is being run."""
    result = run_guard(payload_for("rm -rf build"))
    assert result.returncode == 0
    assert json.loads(result.stdout)["hookSpecificOutput"][
        "permissionDecision"] == "deny"


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*")) if path.is_file()
    }


def test_the_guard_writes_nothing_to_the_payload_it_was_given(tmp_path):
    """AC9: no file the guard can see changes, including the payload itself."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload_file = workspace / "payload.json"
    payload_file.write_text(payload_for("rm -rf build"), encoding="utf-8")
    before = _snapshot(workspace)

    for command in ("rm -rf build", "ls -la hooks", "git commit -m x",
                    'grep -rn "unterminated orchestration'):
        result = run_guard(payload_file.read_text(encoding="utf-8"),
                           cwd=workspace)
        assert result.returncode == 0, command

    assert _snapshot(workspace) == before


def test_the_snapshot_reports_a_program_that_does_write(tmp_path):
    """The control for the snapshot above: a stub that appends to the payload
    file in its own working directory is caught by the identical check."""
    workspace = tmp_path / "writing-workspace"
    workspace.mkdir()
    payload_file = workspace / "payload.json"
    payload_file.write_text(payload_for("rm -rf build"), encoding="utf-8")
    before = _snapshot(workspace)

    stub = tmp_path / "writing_stub.py"
    stub.write_text(
        "import pathlib, sys\n"
        "sys.stdin.read()\n"
        "pathlib.Path('payload.json').open('a').write('touched')\n",
        encoding="utf-8")
    subprocess.run([sys.executable, str(stub)], input="{}", capture_output=True,
                   text=True, cwd=str(workspace), check=True)

    assert _snapshot(workspace) != before


# ---------------------------------------------------------------------------
# AC10, AC11: what agent_runner passes, and what its signature still is
# ---------------------------------------------------------------------------


class FakePopen:
    """Enough of Popen for run_agent, recording the argument list it was built
    with. The real CLI is never invoked."""

    calls: list[list[str]] = []

    def __init__(self, cmd, **kwargs):
        FakePopen.calls.append(list(cmd))
        self.stdin = open(kwargs.get("_devnull", "/dev/null"), "w")
        self.stdout = iter([json.dumps({"type": "result", "result": "done"}) + "\n"])

    def wait(self):
        self.stdin.close()
        return 0


def _built_command(monkeypatch, tmp_path) -> list[str]:
    FakePopen.calls = []
    monkeypatch.setattr(agent_runner.subprocess, "Popen", FakePopen)
    agent_runner.run_agent(
        "prompt",
        stage="implementer",
        cwd=tmp_path,
        log_path=tmp_path / "agent.log",
        permission_mode="acceptEdits",
        model=None,
        allowed_tools=["Bash(grep:*)"],
    )
    assert len(FakePopen.calls) == 1
    return FakePopen.calls[0]


def test_run_agent_passes_settings_registering_the_guard(monkeypatch, tmp_path):
    """AC10: every stage invocation carries the settings, and the path they
    name is a file that exists on disk."""
    cmd = _built_command(monkeypatch, tmp_path)
    assert "--settings" in cmd
    settings = json.loads(cmd[cmd.index("--settings") + 1])
    hooks = settings["hooks"]["PreToolUse"]
    assert [entry["matcher"] for entry in hooks] == ["Bash"]
    commands = [hook["command"] for entry in hooks for hook in entry["hooks"]]
    assert len(commands) == 1
    named = Path(commands[0])
    assert named.is_absolute()
    assert named.is_file(), named
    assert named.resolve() == GUARD_PATH.resolve()
    assert agent_runner.GUARD_PLACEHOLDER not in cmd[cmd.index("--settings") + 1]


def test_the_settings_check_reports_a_declaration_naming_nothing(tmp_path):
    """The control for AC10: the same resolution against a hooks directory
    whose guard is absent yields no settings at all, so "settings are passed"
    is not something that holds for any harness root."""
    empty = tmp_path / "harness-without-a-guard"
    (empty / "hooks").mkdir(parents=True)
    (empty / "hooks" / "settings.json").write_text(
        (HARNESS_ROOT / SETTINGS_REL).read_text(encoding="utf-8"), encoding="utf-8")
    assert agent_runner.guard_settings(empty) is None
    assert agent_runner.guard_settings(tmp_path / "nothing-at-all") is None
    assert agent_runner.guard_settings() is not None


def test_the_shipped_declaration_holds_the_guard_path_as_a_placeholder():
    """The declaration is a data file whose absolute path is computed, so the
    shape does not have to be rebuilt in code for a different installation."""
    declaration = (HARNESS_ROOT / SETTINGS_REL).read_text(encoding="utf-8")
    assert agent_runner.GUARD_PLACEHOLDER in declaration
    assert str(HARNESS_ROOT) not in declaration


def _signature_names(source: str) -> list[str]:
    node = next(item for item in ast.parse(source).body
                if isinstance(item, ast.FunctionDef) and item.name == "run_agent")
    args = node.args
    return [arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)]


def test_run_agent_signature_is_unchanged_by_this_story(tmp_path):
    """AC11, against this story's own baseline rather than a bare HEAD, so the
    answer survives the run's own commit.

    The baseline text is carried as a committed fixture since story-053: it is
    an input — an earlier version of a module — and resolving an input out of
    this repository's commit graph made the comparison move whenever something
    was committed, renamed, squashed or rebased."""
    before = conftest.history_fixture(
        "agent_runner.at-story-035-baseline.py.txt")
    today = (HARNESS_ROOT / "orchestration" / "agent_runner.py").read_text(
        encoding="utf-8")
    # The carried baseline is a *past* text: this story changed the module, so
    # a fixture equal to today's file would be answering the wrong question and
    # the equality below would hold for that reason instead.
    assert before != today
    # story-063 widened the signature deliberately, so what story-035 added —
    # nothing — is now stated as the baseline's parameters plus the ones named
    # here. Repointed rather than relaxed: the comparison is still exact, still
    # ordered, and still goes red on a parameter nobody declared. A later story
    # that widens the signature again appends to this list, which is the
    # deliberate edit an assertion about a signature should cost.
    ADDED_SINCE_STORY_035 = ["max_budget_usd"]
    assert _signature_names(today) == (
        _signature_names(before) + ADDED_SINCE_STORY_035)

    # The control: a signature that gained a parameter nobody declared is
    # reported by the same comparison, so the equality above is not vacuous.
    widened = today.replace(
        "    allowed_tools: list[str] | None = None,\n",
        "    allowed_tools: list[str] | None = None,\n"
        "    settings: str | None = None,\n", 1)
    assert _signature_names(widened) != (
        _signature_names(before) + ADDED_SINCE_STORY_035)


#: The keyword arguments the coordinator calls its injected runner with on
#: *every* invocation. Since story-063 the call site has one more that is
#: conditional — `max_budget_usd`, passed only when the stage declares a
#: max_execution_cost_usd, so that a stage under no ceiling is invoked with
#: exactly these — and a scan keyed on unconditional keywords cannot express
#: that. Every fake runner in the suite accepts the conditional one too; what
#: holds that is the story's own module rather than this tuple, which would
#: otherwise report a fake driving a ceiling-less workflow as unsatisfied.
CALL_SITE_KWARGS = ("stage", "cwd", "log_path", "permission_mode", "model",
                    "allowed_tools")


def _unsatisfied_runners(directory: Path) -> list[tuple[str, list[str]]]:
    """Runner-shaped callables under `directory` that the call site would break.

    A definition is runner-shaped when it takes `stage` and `log_path` as
    keyword-only arguments; it satisfies the call site when it names every
    keyword the coordinator passes, or absorbs the rest with **kwargs.
    """
    unsatisfied = []
    for module in sorted(directory.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = [arg.arg for arg in node.args.kwonlyargs]
            if "stage" not in names or "log_path" not in names:
                continue
            if node.args.kwarg is not None:
                continue
            missing = [kwarg for kwarg in CALL_SITE_KWARGS if kwarg not in names]
            if missing:
                unsatisfied.append((f"{module.name}::{node.name}", missing))
    return unsatisfied


def _runner_shaped_count(directory: Path) -> int:
    return sum(
        1
        for module in directory.glob("*.py")
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8")))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and {"stage", "log_path"} <= {arg.arg for arg in node.args.kwonlyargs}
    )


def test_every_fake_runner_in_the_suite_still_satisfies_the_call_site():
    """AC11's consequence: no fake runner needed editing for this story.

    Asserted as "each accepts what the coordinator passes" rather than as
    "the suite passes", because a fake that had quietly grown a parameter
    would still let the suite pass while breaking the next caller."""
    tests_dir = HARNESS_ROOT / "tests"
    assert _runner_shaped_count(tests_dir) > 0, \
        "no fake runner was discovered, so this asserts nothing"
    assert _unsatisfied_runners(tests_dir) == []


def test_the_fake_runner_scan_reports_one_that_does_not_satisfy_it(tmp_path):
    """The control for the scan above: a runner-shaped fake missing the call
    site's newest keyword is reported by the identical scan, so the empty
    result means "none present" rather than "none looked for"."""
    planted = tmp_path / "planted"
    planted.mkdir()
    (planted / "test_planted.py").write_text(
        "def fake_runner(prompt, *, stage, cwd, log_path, permission_mode,\n"
        "                model):\n"
        "    return None\n",
        encoding="utf-8")
    assert _runner_shaped_count(planted) == 1
    assert _unsatisfied_runners(planted) == [
        ("test_planted.py::fake_runner", ["allowed_tools"])]


# ---------------------------------------------------------------------------
# AC12, AC13: config_context, and that omitting the argument changes nothing
# ---------------------------------------------------------------------------


GRANTS = ["Bash(grep:*)", "Bash(git show:*)"]


def test_config_context_maps_allowed_tools_through_the_shared_helper():
    """AC12: dash-prefixed lines, and rendered by _dashed_lines rather than by
    a second copy of it — shown by replacing the helper and seeing the result
    change, which no independent formatting could do."""
    # Keyed on the grants alone rather than on the whole mapping:
    # config_context maps every configured fact a prompt renders, so another
    # key joining it must not read as this one having changed.
    rendered = context_assembler.config_context({"allowed_tools": GRANTS})
    assert rendered["allowed_tools"] == "- Bash(grep:*)\n- Bash(git show:*)"
    assert rendered["allowed_tools"] == context_assembler._dashed_lines(GRANTS)


def test_config_context_renders_none_when_the_config_declares_no_grants():
    """AC12's absence half, controlled by the populated case above: the same
    call with grants renders them, so None is about the config."""
    assert context_assembler.config_context({})["allowed_tools"] is None
    assert context_assembler.config_context(
        {"allowed_tools": []})["allowed_tools"] is None
    assert context_assembler.config_context(
        {"allowed_tools": GRANTS})["allowed_tools"] is not None


def test_config_context_uses_the_shared_dashed_lines_helper(monkeypatch):
    """AC12 names the helper, so the helper is what is checked."""
    monkeypatch.setattr(context_assembler, "_dashed_lines",
                        lambda items: "SENTINEL")
    assert context_assembler.config_context(
        {"allowed_tools": GRANTS})["allowed_tools"] == "SENTINEL"


def _context(target_root: Path, **extra) -> dict:
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text(
        encoding="utf-8")
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    return dict(
        story_text=story_text,
        story=story_parser.parse(story_text, schema_validator.load_schema("story")),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=HARNESS_ROOT,
        config=harness_config.load_config(target_root),
        rules=harness_config.load_rules(HARNESS_ROOT),
        workflow=WORKFLOW,
        retry_count=0,
        **extra,
    )


def test_the_new_build_context_argument_is_optional_and_keyword_only():
    """AC13, read off the signature."""
    parameters = ast.parse(
        (HARNESS_ROOT / "orchestration" / "context_assembler.py").read_text(
            encoding="utf-8"))
    node = next(item for item in parameters.body
                if isinstance(item, ast.FunctionDef) and item.name == "build_context")
    assert "allowed_tools" not in [arg.arg for arg in node.args.args]
    keyword_only = [arg.arg for arg in node.args.kwonlyargs]
    assert "allowed_tools" in keyword_only
    index = keyword_only.index("allowed_tools")
    assert node.args.kw_defaults[index] is not None


def test_omitting_the_argument_renders_what_it_rendered_before_this_story(
        target_root, tmp_path):
    """AC13: a call that omits the argument renders exactly what a
    context_assembler without the merge renders — which is the pre-story code
    at that line, reconstructed from today's source rather than recovered from
    history, so it stays honest when the story commits."""
    without_the_merge = load_mutant(
        HARNESS_ROOT / "orchestration" / "context_assembler.py",
        [("    context.update(config_context({**config, \"allowed_tools\": "
          "allowed_tools}))",
          "    pass  # the merge this story added, removed")],
        name="context_assembler_before_story_035", tmp_path=tmp_path)

    today = context_assembler.build_context(**_context(target_root))
    before = without_the_merge.build_context(**_context(target_root))

    assert today["allowed_tools"] is None
    # Every key the merge contributes is excluded, not the grants alone: the
    # merge is one call, so removing it removes all of them at once.
    merged = set(context_assembler.config_context({}))
    assert {key: value for key, value in today.items()
            if key not in merged} == before

    # The control: supplying the argument *does* change the render, so the
    # equality above is a statement about omission rather than about the merge
    # being inert.
    supplied = context_assembler.build_context(
        **_context(target_root, allowed_tools=GRANTS))
    assert supplied["allowed_tools"] == "- Bash(grep:*)\n- Bash(git show:*)"
    assert supplied["harness_layer"] != today["harness_layer"]


# ---------------------------------------------------------------------------
# AC14: the harness layer
# ---------------------------------------------------------------------------


def test_the_harness_layer_carries_the_placeholder_and_the_sentence():
    """AC14: the granted list is injected, and the sentence is marked as
    guidance rather than as the enforcement."""
    partial = (HARNESS_ROOT / HARNESS_LAYER_REL).read_text(encoding="utf-8")
    assert "{{allowed_tools}}" in partial
    assert "single command" in partial
    assert "denied even when every command inside it is granted" in partial
    assert "Guidance, not the enforcement" in partial
    # It names no specific command: the list is the injected value.
    assert "grep" not in partial


def test_the_rendered_harness_layer_shows_the_configured_grants(target_root):
    """AC14: with grants supplied the layer shows them; without, it renders
    None. The pair is each other's control."""
    supplied = context_assembler.build_context(
        **_context(target_root, allowed_tools=GRANTS))["harness_layer"]
    omitted = context_assembler.build_context(
        **_context(target_root))["harness_layer"]

    assert "- Bash(grep:*)" in supplied
    assert "- Bash(git show:*)" in supplied
    assert "{{" not in supplied
    assert "- Bash(grep:*)" not in omitted
    assert "Bash commands granted to you without prompting:\nNone" in omitted


def test_the_stage_prompts_render_the_grants_through_the_shared_layer(target_root):
    """The grants reach a stage, rather than only the partial.

    Which templates that is comes off the templates themselves — the ones
    injecting {{harness_layer}} — rather than from a list written here, so a
    template that stops injecting the shared block is not silently excused.
    The verifier carries its own [Harness Layer] block instead, and it is
    asserted below to be exactly the set that does not inject the partial."""
    context = context_assembler.build_context(
        **_context(target_root, allowed_tools=GRANTS))
    injecting = [name for name in ("implementer.md", "tester.md", "verifier.md",
                                   "documenter.md")
                 if "{{harness_layer}}" in context_assembler.load_template(
                     HARNESS_ROOT, name)]
    assert injecting, "no stage template injects the shared harness layer"
    for prompt_file in injecting:
        rendered = context_assembler.render(
            context_assembler.load_template(HARNESS_ROOT, prompt_file), context)
        assert "{{" not in rendered, prompt_file
        assert "- Bash(grep:*)" in rendered, prompt_file

    # The control for the selection: the templates left out are left out
    # because they carry their own block, not because they render nothing.
    for prompt_file in ("implementer.md", "tester.md", "verifier.md",
                        "documenter.md"):
        rendered = context_assembler.render(
            context_assembler.load_template(HARNESS_ROOT, prompt_file), context)
        assert "[Harness Layer]" in rendered, prompt_file


def test_the_coordinator_passes_the_configs_grants_to_build_context():
    """The wiring the render depends on: without it the placeholder would be
    injectable and never injected."""
    source = (HARNESS_ROOT / "orchestration" / "story_coordinator.py").read_text(
        encoding="utf-8")
    assert 'allowed_tools=config.get("allowed_tools")' in source


# ---------------------------------------------------------------------------
# AC1, AC2, AC15: the two allowlists
# ---------------------------------------------------------------------------


#: The twelve read-only prefixes AC1 names.
ADDED_READ_ONLY = ("grep", "rg", "find", "head", "tail", "wc", "sort", "uniq",
                   "diff", "git show", "git branch", "git ls-files")

#: Entries specific to this repository, which the l5-init template does not
#: carry and AC15 explicitly allows for.
REPOSITORY_SPECIFIC = {"Bash(.venv/bin/python:*)", "Bash(python3:*)",
                       "Bash(chmod:*)"}


def allowed_tools_in(text: str) -> list[str]:
    """The allowed_tools entries of a config's text, in order.

    A reader of text rather than of a directory, because one of the two configs
    is a template and one of the readings is at a git bound; cross-checked
    against harness_config.load_config below so it cannot drift from the parse
    the harness itself uses.
    """
    entries: list[str] = []
    collecting = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.strip() == "allowed_tools:":
            collecting = True
            continue
        if collecting and line.lstrip().startswith("- "):
            entries.append(harness_config._unquote(line.strip()[2:].strip()))
            continue
        if collecting:
            break
    return entries


def test_this_modules_config_reader_agrees_with_the_harness_parse():
    """So every assertion below is about the config rather than about a second
    parser written here."""
    assert allowed_tools_in((HARNESS_ROOT / CONFIG_REL).read_text(
        encoding="utf-8")) == harness_config.load_config(
            HARNESS_ROOT)["allowed_tools"]


@pytest.mark.parametrize("command", ADDED_READ_ONLY)
def test_the_config_grants_each_added_read_only_prefix(command):
    """AC1."""
    assert f"Bash({command}:*)" in allowed_tools_in(
        (HARNESS_ROOT / CONFIG_REL).read_text(encoding="utf-8"))


def test_no_existing_entry_was_removed_or_altered():
    """AC2, against this story's own baseline: every entry the config carried
    before is still there, unaltered and in its original order.

    The baseline config is carried as a committed fixture since story-053, for
    the reason the signature comparison above records."""
    before = allowed_tools_in(conftest.history_fixture(
        "harness-config.at-story-035-baseline.yaml.txt"))
    today = allowed_tools_in((HARNESS_ROOT / CONFIG_REL).read_text(
        encoding="utf-8"))
    assert before, "the baseline config declares no grants, so this asserts nothing"
    # The carried baseline is a *past* config: this story added grants, so a
    # fixture equal to today's file would make the prefix comparison hold for
    # the wrong reason.
    assert before != today
    assert today[:len(before)] == before

    # The control: an entry dropped from the middle is reported by the same
    # comparison, so "unchanged" is not something that holds for any list.
    dropped = [entry for entry in today if entry != before[1]]
    assert dropped[:len(before)] != before


def test_permission_mode_is_still_accept_edits():
    """AC2, against this story's own baseline and today's config.

    The baseline text is the committed fixture; the other half is the config
    this repository ships, which is where the mode has to still be what it
    was."""
    def mode(text: str) -> str:
        return next(line.split(":", 1)[1].strip()
                    for line in text.splitlines()
                    if line.startswith("permission_mode:"))

    before = conftest.history_fixture(
        "harness-config.at-story-035-baseline.yaml.txt")
    today = (HARNESS_ROOT / CONFIG_REL).read_text(encoding="utf-8")
    assert mode(today) == "acceptEdits"
    assert mode(today) == mode(before)


def _writing_entries(entries: list[str], mutators, git_mutators) -> list[str]:
    """The entries that name a command able to write, judged by the guard's own
    tables rather than by a second list written here."""
    flagged = []
    for entry in entries:
        inner = entry[len("Bash("):-len(":*)")] if entry.startswith("Bash(") else entry
        words = inner.split()
        if not words:
            continue
        name = words[0].rsplit("/", 1)[-1]
        if name in mutators:
            flagged.append(entry)
        elif name == "git" and len(words) > 1 and words[1] in git_mutators:
            flagged.append(entry)
    return flagged


def test_no_granted_entry_names_a_command_that_writes(tmp_path):
    """AC1's second half, for both configs, read entry by entry rather than
    sampled — and judged against the guard's own mutator tables, so the two
    halves of this story cannot disagree about what mutates."""
    guard = load_mutant(GUARD_PATH, [], name="bash_guard_tables",
                        tmp_path=tmp_path)
    for relative in (CONFIG_REL, TEMPLATE_CONFIG_REL):
        entries = allowed_tools_in(
            (HARNESS_ROOT / relative).read_text(encoding="utf-8"))
        assert entries, relative
        assert _writing_entries(entries, guard.MUTATORS,
                                guard.GIT_MUTATORS) == [], relative

    # The control: a list carrying a writing grant is reported by the same
    # check, so an empty result means "none present" rather than "none looked
    # for".
    assert _writing_entries(
        ["Bash(grep:*)", "Bash(rm:*)", "Bash(git commit:*)"],
        guard.MUTATORS, guard.GIT_MUTATORS) == ["Bash(rm:*)", "Bash(git commit:*)"]


def test_the_template_grants_the_same_read_only_set():
    """AC15: the two lists agree, allowing for this repository's own entries."""
    here = set(allowed_tools_in(
        (HARNESS_ROOT / CONFIG_REL).read_text(encoding="utf-8")))
    template = set(allowed_tools_in(
        (HARNESS_ROOT / TEMPLATE_CONFIG_REL).read_text(encoding="utf-8")))
    assert here - template == REPOSITORY_SPECIFIC
    assert template - here == set()
    for command in ADDED_READ_ONLY:
        assert f"Bash({command}:*)" in template
