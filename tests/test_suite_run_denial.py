"""Independent validation for story-073: a stage that runs no suite cannot
run one.

The story adds a second denial family to `hooks/bash_guard.py`. What decides
that denial is two things and neither is written into harness source: the
command comes from the target's configured `test_command`, handed to the guard
as its own argument, and the restriction comes from a per-stage
`may_not_run_suite` declaration in the workflow definition. So this module is
laid out along the two of them:

  * the guard is driven **as a program**, in a subprocess, with real PreToolUse
    payloads on stdin and the configured command in its argv. Nothing here
    imports it to call `offence` directly, because what ships is a command line
    — the same reading `tests/test_stage_tool_grants.py` established for the
    mutation family;
  * the coordinator is driven through a **built** workflow and a **constructed**
    target, so what reaches the guard is shown to come out of configuration
    rather than out of what this repository happens to deploy. The stage names,
    the restriction and the configured command are the fixture's own.

Two sets of literals are deliberately this module's own rather than read off
anything:

  * `RECORDED_TEST_COMMAND` and `RECORDED_INVOCATIONS` are the *record*: the
    configured command story-070's tester was given and the two invocations of
    it that turned up in that run's log. They are a historical fact about one
    turn, so reading them out of `.harness/config.yaml` today would make a
    change to what this deployment configures redden an assertion about what
    happened in a run that has already ended;
  * `SUITE_ALPHA` and `SUITE_BETA` are two configured commands this module
    invents, used to show that changing the configured value moves what the
    guard denies. They name no framework and no runner, which is the point:
    the mechanism cannot be reading one.

What *is* read off this repository is what this repository declares — the two
shipped workflow definitions and `prompts/story-tester.md` — because those are
the subject of the criteria about them rather than an input to some other
assertion.

Every absence asserted here carries a demonstration that the same check can
report the violation it exists to catch:

  * "a single named test file draws no decision" sits beside the same command
    with the configured directory back in its place, which is denied — so the
    silence is about the target rather than about the guard having stopped
    looking;
  * "the guard given no command says nothing about the pipe form" sits beside
    the identical run of the identical guard *with* the command, which denies
    it;
  * "an argument the guard cannot read yields no decision" sits, case by case,
    beside the single readable argument that denies;
  * "a stage under no declaration is invoked with no suite command" sits beside
    the same runner under a definition that declares it, which receives one, and
    beside a runner whose signature has no such parameter at all, which drives
    the undeclared run to completion and is refused by the declared one;
  * "no framework name entered `hooks/bash_guard.py`" sits beside a throwaway
    copy of that file with a stack token planted in it, which the same scan
    reports;
  * "the guard has no allow path" sits beside a mutant guard carrying one,
    which the same scan reports and which really does emit an allow decision;
  * "the shipped implementer declares no restriction" sits beside the stages of
    the same definition that do declare it;
  * "the tester prompt's paragraph is shorter" sits beside the same measurement
    over the prompt with its own baseline paragraph restored, which reports it.

Nothing here invokes a model, and nothing here runs the target's suite: the
guard is asked what it would decide, which is a question about a decision
rather than about a run.
"""
import ast
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
from conftest import HARNESS_ROOT, load_mutant

import agent_runner
import harness_source
import story_coordinator
from agent_runner import AgentResult

GUARD_REL = "hooks/bash_guard.py"
GUARD_PATH = HARNESS_ROOT / GUARD_REL
TESTER_PROMPT_REL = "prompts/story-tester.md"

#: The paragraph both the baseline prompt and today's open with. The span this
#: story shortened is found by this opening rather than by a line number, so an
#: edit above it does not move what is measured.
PARAGRAPH_OPENING = "This stage executes no test command"

#: The baseline of `prompts/story-tester.md`, carried as a committed fixture
#: rather than resolved out of the commit graph: an earlier version of a file is
#: an input, and an input resolved out of history moves when something is
#: committed, renamed, squashed or rebased.
TESTER_PROMPT_BASELINE = "prompts-story-tester.at-story-073-baseline.md.txt"


# ---------------------------------------------------------------------------
# The record: one configured command and the two invocations of it that ran
# ---------------------------------------------------------------------------

#: What story-070's target configured, and what both invocations below are that
#: command with things added to it.
RECORDED_TEST_COMMAND = ".venv/bin/python -m pytest tests/ -q"

#: The two commands the request records out of story-070's tester turn,
#: verbatim. One redirects into a file and then chains with `;`; the other pipes
#: into `tail`.
RECORDED_INVOCATIONS = {
    "redirect-then-semicolon": (
        ".venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider "
        "> /tmp/suite.txt 2>&1; tail -40 /tmp/suite.txt"
    ),
    "pipe-into-tail": (
        ".venv/bin/python -m pytest tests/ -q -p no:cacheprovider 2>&1 | tail -40"
    ),
}

#: The same configured command reached through every surrounding the criterion
#: names, each of which must be denied *by the suite family*. None of them
#: writes a file, so none can be denied by the redirect rule instead and every
#: denial below is the new family's.
SURROUNDED = {
    "the configured command alone": RECORDED_TEST_COMMAND,
    "an extra short flag": f"{RECORDED_TEST_COMMAND} -x",
    "an extra two-word option": f"{RECORDED_TEST_COMMAND} -p no:cacheprovider",
    "a descriptor duplication": f"{RECORDED_TEST_COMMAND} 2>&1",
    "a redirect to /dev/null": f"{RECORDED_TEST_COMMAND} > /dev/null",
    "a pipe into tail": f"{RECORDED_TEST_COMMAND} 2>&1 | tail -40",
    "a read-only neighbour before a semicolon": (
        f"ls tests; {RECORDED_TEST_COMMAND}"),
    "a read-only neighbour after a semicolon": (
        f"{RECORDED_TEST_COMMAND}; ls tests"),
    "wrapped in timeout": f"timeout 600 {RECORDED_TEST_COMMAND}",
    "prefixed with an environment assignment": f"FOO=1 {RECORDED_TEST_COMMAND}",
}

#: What the configured directory is replaced by to make each command above name
#: a target the configured command does not name. One positional argument
#: different, and nothing else.
CONFIGURED_TARGET = "tests/"
A_SINGLE_TEST_FILE = "tests/test_one_named_file.py"


def naming_one_file(command: str) -> str:
    """`command` with the configured directory replaced by a single file."""
    assert CONFIGURED_TARGET in command, command
    return command.replace(f"{CONFIGURED_TARGET} ", f"{A_SINGLE_TEST_FILE} ", 1) \
        if f"{CONFIGURED_TARGET} " in command \
        else command.replace(CONFIGURED_TARGET, A_SINGLE_TEST_FILE, 1)


# ---------------------------------------------------------------------------
# Driving the guard as a program
# ---------------------------------------------------------------------------


def payload_for(command: str) -> str:
    """A real PreToolUse hook payload for a Bash call."""
    return json.dumps({
        "session_id": "story-073-validation",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    })


def run_guard(stdin: str, *arguments: str,
              guard: Path = GUARD_PATH) -> subprocess.CompletedProcess:
    """The guard, run as a program with `stdin` on stdin and `arguments` in argv.

    No argument at all is how a stage that may legitimately run the suite is
    registered, so it is spelled here as the absence of one rather than as an
    empty string, which is a different input the guard treats differently.
    """
    return subprocess.run(
        [sys.executable, str(guard), *arguments],
        input=stdin, capture_output=True, text=True,
    )


def emitted(command: str, *arguments: str, guard: Path = GUARD_PATH) -> dict | None:
    """What the guard emitted for `command`, or None when it said nothing.

    Silence is the guard's fail-open answer and it is a different outcome from a
    decision, so it is never conflated here with "not denied".
    """
    result = run_guard(payload_for(command), *arguments, guard=guard)
    assert result.returncode == 0, (command, result.returncode, result.stderr)
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)["hookSpecificOutput"]


def decision(command: str, *arguments: str,
             guard: Path = GUARD_PATH) -> str | None:
    output = emitted(command, *arguments, guard=guard)
    return None if output is None else output["permissionDecision"]


def reason(command: str, *arguments: str) -> str:
    output = emitted(command, *arguments)
    assert output is not None, command
    return output["permissionDecisionReason"]


#: What the suite family's reason says, and the mutation family's does not.
#: Read here as the three things the criterion asks the reason to name.
REDIRECTS_TO = ("coordinator", "after your turn ends", "suite-run record")


def is_the_suite_reason(text: str) -> bool:
    return all(phrase in text for phrase in REDIRECTS_TO)


# ---------------------------------------------------------------------------
# AC1: both recorded commands, denied verbatim
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("form", sorted(RECORDED_INVOCATIONS),
                         ids=sorted(RECORDED_INVOCATIONS))
def test_each_command_the_record_carries_is_denied_verbatim(form):
    """AC1: the guard driven as a program, with the configured command supplied,
    denies both invocations story-070's tester actually ran — character for
    character as the log recorded them."""
    assert decision(RECORDED_INVOCATIONS[form],
                    RECORDED_TEST_COMMAND) == "deny", form


def test_which_family_decides_each_recorded_command():
    """The two are not denied for the same reason, and saying so is worth more
    than a pair of denials that look alike.

    The redirect form writes a file, so the redirect rule reaches it first and
    it is denied on those terms — with or without a configured command. The pipe
    form writes nothing, so it is the new family's, and it is the one that goes
    silent when no command is supplied. Stated here so the AC1 pair above cannot
    be satisfied by the older rule alone.
    """
    redirecting = RECORDED_INVOCATIONS["redirect-then-semicolon"]
    piping = RECORDED_INVOCATIONS["pipe-into-tail"]

    assert decision(redirecting) == "deny"
    assert not is_the_suite_reason(reason(redirecting, RECORDED_TEST_COMMAND))

    assert decision(piping) is None
    assert decision(piping, RECORDED_TEST_COMMAND) == "deny"
    assert is_the_suite_reason(reason(piping, RECORDED_TEST_COMMAND))


# ---------------------------------------------------------------------------
# AC2, AC3: what the reduction sees, and what it does not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", sorted(SURROUNDED), ids=sorted(SURROUNDED))
def test_a_component_reducing_to_the_configured_command_is_denied(case):
    """AC2: flags, redirections that write nothing, pipes, `;`-chained
    neighbours, a wrapper and an environment assignment all leave the same
    invocation, and each is denied by the suite family rather than by the
    redirect rule that happens to sit beside it."""
    command = SURROUNDED[case]
    assert decision(command, RECORDED_TEST_COMMAND) == "deny", case
    assert is_the_suite_reason(reason(command, RECORDED_TEST_COMMAND)), case


@pytest.mark.parametrize("case", sorted(SURROUNDED), ids=sorted(SURROUNDED))
def test_the_same_command_naming_one_test_file_draws_no_decision(case):
    """AC3: the control for every denial above, differing in one feature only.

    The configured directory is replaced by a single named test file and nothing
    else changes — same flags, same redirection, same pipe, same neighbour — so
    the silence is a statement about the target the invocation names rather than
    about a guard that has stopped seeing the shape. The tester must stay able
    to run the validation it authors, and this is that ability.
    """
    command = SURROUNDED[case]
    assert decision(command, RECORDED_TEST_COMMAND) == "deny", case
    assert decision(naming_one_file(command), RECORDED_TEST_COMMAND) is None, case


def test_an_extra_positional_argument_is_a_different_invocation():
    """The other side of AC3's rule, so "a subset stays permitted" is not read
    as "anything containing the configured command is denied": an invocation
    naming the configured directory *and* something else reduces to a longer
    sequence and draws no decision either. Its control is the same command with
    the extra argument removed, which is denied."""
    extra = RECORDED_TEST_COMMAND.replace(
        CONFIGURED_TARGET, f"{CONFIGURED_TARGET} {A_SINGLE_TEST_FILE}", 1)
    assert decision(extra, RECORDED_TEST_COMMAND) is None
    assert decision(RECORDED_TEST_COMMAND, RECORDED_TEST_COMMAND) == "deny"


def test_a_word_following_a_flag_is_read_as_that_flags_value():
    """The stated limit of the reduction, exercised rather than described.

    An option's value is treated as part of the option, which is what lets an
    invocation carrying extra two-word options reduce to the configured
    command. The price is here: a positional argument written immediately after
    a value-less flag is consumed as that flag's value, so the same extra
    argument that makes an invocation different above leaves it identical when
    it is written in that position — and it is denied.

    Asserted rather than left to be discovered, because the two behaviours are
    one rule and a reader meeting only the permissive half would conclude the
    guard discriminates on the argument's presence.
    """
    after_a_flag = f"{RECORDED_TEST_COMMAND} {A_SINGLE_TEST_FILE}"
    assert RECORDED_TEST_COMMAND.split()[-1].startswith("-"), \
        "this case needs the configured command to end in a value-less flag"
    assert decision(after_a_flag, RECORDED_TEST_COMMAND) == "deny"

    # The control: the same word, written ahead of that flag rather than after
    # it, is a positional argument again and draws no decision — so the denial
    # above is about where the word sits rather than about the guard ignoring
    # extra arguments altogether.
    before_the_flag = RECORDED_TEST_COMMAND.replace(
        CONFIGURED_TARGET, f"{CONFIGURED_TARGET} {A_SINGLE_TEST_FILE}", 1)
    assert decision(before_the_flag, RECORDED_TEST_COMMAND) is None


# ---------------------------------------------------------------------------
# AC8: what the denial says
# ---------------------------------------------------------------------------


def test_the_denial_names_where_the_answer_comes_from():
    """AC8: the reason redirects rather than only refusing. It names the
    coordinator's post-turn run of the same command as where the answer is
    computed and the injected suite-run record as where the stage reads it, and
    it quotes the component it is refusing so the stage knows which one.

    The control is the mutation family's reason for a command denied by the
    older rule, which names none of those and offers Edit instead — so the
    phrases above are this family's own rather than something every denial the
    guard writes happens to say.
    """
    text = reason(RECORDED_INVOCATIONS["pipe-into-tail"], RECORDED_TEST_COMMAND)
    for phrase in REDIRECTS_TO:
        assert phrase in text, phrase
    assert RECORDED_TEST_COMMAND in text

    mutating = reason("rm -rf build", RECORDED_TEST_COMMAND)
    assert not is_the_suite_reason(mutating)
    assert "Edit" in mutating


# ---------------------------------------------------------------------------
# AC7: no allow path, and every unreadable input yields no decision
# ---------------------------------------------------------------------------


#: Arguments the guard cannot establish a command from. Each is paired below
#: with the one readable argument that does deny.
UNREADABLE_ARGUMENTS = {
    "no argument at all": (),
    "an empty argument": ("",),
    "an argument of whitespace": ("   ",),
    "an argument of flags alone": ("-q -x",),
    "two arguments": (RECORDED_TEST_COMMAND, RECORDED_TEST_COMMAND),
}

MALFORMED_PAYLOADS = {
    "empty": "",
    "not json": "this is not json",
    "a json array": "[]",
    "a non-dict tool_input": json.dumps({"tool_input": RECORDED_TEST_COMMAND}),
    "no command key": json.dumps({"tool_input": {"description": "run it"}}),
}


@pytest.mark.parametrize("case", sorted(UNREADABLE_ARGUMENTS),
                         ids=sorted(UNREADABLE_ARGUMENTS))
def test_an_argument_the_guard_cannot_read_yields_no_decision(case):
    """AC6 and AC7: with no command established the family says nothing, for the
    very command it denies when given one.

    The control is beneath each case: the identical payload through the
    identical driver with one readable argument is denied, so the silence is
    about the argument rather than about the run.
    """
    piping = RECORDED_INVOCATIONS["pipe-into-tail"]
    assert decision(piping, *UNREADABLE_ARGUMENTS[case]) is None, case
    assert decision(piping, RECORDED_TEST_COMMAND) == "deny"


@pytest.mark.parametrize("case", sorted(MALFORMED_PAYLOADS),
                         ids=sorted(MALFORMED_PAYLOADS))
def test_a_malformed_payload_yields_no_decision_even_with_a_command(case):
    """AC7: the fail-open bias is unchanged by the new argument — a payload the
    guard cannot read produces nothing whether or not it was given a command.

    Its control is the same driver with a well-formed payload, below.
    """
    result = run_guard(MALFORMED_PAYLOADS[case], RECORDED_TEST_COMMAND)
    assert result.returncode == 0, case
    assert result.stdout.strip() == "", case


def test_the_same_driver_carries_a_suite_denial_out():
    """The control for every silence above: the same subprocess, the same argv
    and the same payload shape do produce a decision."""
    result = run_guard(payload_for(RECORDED_INVOCATIONS["pipe-into-tail"]),
                       RECORDED_TEST_COMMAND)
    assert result.returncode == 0
    assert json.loads(result.stdout)["hookSpecificOutput"][
        "permissionDecision"] == "deny"


def _non_docstring_string_constants(source: str) -> list[str]:
    """Every string literal in `source` that is not a docstring.

    The guard's own prose is *about* there being no allow path, so a scan that
    could not tell a docstring from code would report the documentation as the
    defect. The same reading `tests/test_stage_tool_grants.py` uses, written
    here because what is being asked is whether the family this story added
    brought one with it.
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
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


def test_the_guard_still_has_no_allow_path(tmp_path):
    """AC7's first half, after a second denial family joined the file.

    Read off the source rather than swept behaviourally, because a sweep shows
    only that some corpus did not reach an allow. The control is a mutant guard
    that carries one: the same scan reports it, and driven through the same
    subprocess on a command the shipped guard is silent about it really does
    emit an allow decision.
    """
    literals = _non_docstring_string_constants(
        GUARD_PATH.read_text(encoding="utf-8"))
    assert not [literal for literal in literals if "allow" in literal.lower()]

    allower = load_mutant(
        GUARD_PATH,
        [("    if reason is None:\n        return 0",
          '    if reason is None:\n'
          '        json.dump({"hookSpecificOutput": {\n'
          '            "hookEventName": HOOK_EVENT,\n'
          '            "permissionDecision": "allow",\n'
          '            "permissionDecisionReason": "control"}}, sys.stdout)\n'
          '        return 0')],
        name="bash_guard_that_allows_after_story_073", tmp_path=tmp_path)
    control = Path(allower.__file__)
    permitted = naming_one_file(RECORDED_TEST_COMMAND)
    assert decision(permitted, RECORDED_TEST_COMMAND, guard=control) == "allow"
    assert decision(permitted, RECORDED_TEST_COMMAND) is None


EVERY_COMMAND = (
    list(RECORDED_INVOCATIONS.values()) + list(SURROUNDED.values())
    + [naming_one_file(command) for command in SURROUNDED.values()]
    + [RECORDED_TEST_COMMAND, f"{RECORDED_TEST_COMMAND} {A_SINGLE_TEST_FILE}",
       "rm -rf build", "ls tests"]
)


@pytest.mark.parametrize("command", EVERY_COMMAND)
def test_no_command_in_this_module_draws_an_allow(command):
    """AC7, behaviourally, over every command this module shows the guard —
    with the configured command supplied, which is the input the new family
    needs to decide anything at all."""
    assert decision(command, RECORDED_TEST_COMMAND) in (None, "deny"), command


# ---------------------------------------------------------------------------
# AC5: no framework, runner or stack literal entered harness source
# ---------------------------------------------------------------------------


#: The guard as it stood before this story, carried as a committed fixture for
#: the reason the tester prompt's baseline is: an earlier version of a file is
#: an input, and an input resolved out of the commit graph moves when something
#: is committed, renamed, squashed or rebased.
GUARD_BASELINE = "bash_guard.at-story-073-baseline.py.txt"


def stack_findings(source: str, tmp_path: Path, name: str) -> list[tuple]:
    """What the scan reports in a guard whose text is `source`.

    Run against a throwaway root holding that one file, so both ends of the
    comparison below go through the identical code path and neither reads or
    writes this repository. Keyed on the rule, the token and the matched line
    rather than on the line number, so a line added above a match is not
    reported as a change to the match.
    """
    root = tmp_path / name
    (root / "hooks").mkdir(parents=True)
    (root / GUARD_REL).write_text(source, encoding="utf-8")
    return sorted((finding.rule, finding.token, finding.line.strip())
                  for finding in harness_source.scan(root)
                  if finding.path == GUARD_REL)


def test_this_story_added_no_target_stack_literal_to_the_guard(tmp_path):
    """AC5: the existing scan over `hooks/bash_guard.py` stays clean.

    Clean means *unchanged*, not empty: the file's shebang names an interpreter
    and is reported today exactly as it was reported before this story, and
    `tests/test_no_target_stack_in_harness_source.py` is where that one standing
    mention is accounted for. What this story is answerable for is that it added
    nothing beside it, which is what the equality below says — a stronger
    reading than "the live sweep is still green", because it would go red on a
    literal added here even if the standing list were widened to excuse it.

    The control is the same comparison against today's source with a runner
    named outright in a comment, which reports the extra mention. Both sides are
    scanned in a throwaway root, so nothing here reads or writes this
    repository's own tree.
    """
    today = GUARD_PATH.read_text(encoding="utf-8")
    before = conftest.history_fixture(GUARD_BASELINE)
    assert before != today, "the carried baseline is not a past text"

    unchanged = stack_findings(today, tmp_path, "today")
    assert unchanged == stack_findings(before, tmp_path, "baseline")

    planted = stack_findings(
        today + "\n# a runner named outright, which the rule forbids: pytest\n",
        tmp_path, "planted")
    assert len(planted) == len(unchanged) + 1
    assert "pytest" in [token for _, token, _ in planted]
    assert "pytest" not in [token for _, token, _ in unchanged]


# ---------------------------------------------------------------------------
# AC4, AC6: the declaration and the configured command, through a run
#
# A built workflow and a constructed target, so nothing below is a statement
# about what this repository deploys. The restriction is declared on one of the
# two stages, and the target configures a command this module invented.
# ---------------------------------------------------------------------------


#: Two configured commands, neither naming a language, a framework or a runner.
#: What matters about each is that it reduces to a different sequence from the
#: other, so what the guard denies under one it is silent about under the other.
SUITE_ALPHA = "echo alpha-suite tests/"
SUITE_BETA = "echo beta-suite spec/"

#: One invocation of each, in the shape the record carries: the configured
#: command with a flag added and piped into another command.
INVOCATIONS = {SUITE_ALPHA: f"{SUITE_ALPHA} -q 2>&1 | tail -40",
               SUITE_BETA: f"{SUITE_BETA} -q 2>&1 | tail -40"}

STORY_ID = "story-001"
DEFAULT_BRANCH = "main"

RULES = {
    "max_retries": 2,
    "require_verifier_pass": True,
    "blocked_paths": [".git/", ".harness/runs/", "rules/"],
}

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}

STORY = f"""\
story:
  id: {STORY_ID}
  title: Sample story for suite-denial tests
  description: |
    A stand-in story used to drive the coordinator against a fake runner that
    records the keywords each stage was invoked with.

tasks:
  - do the sample work

acceptance_criteria:
  - the sample behavior exists

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
tests_dir: tests/
"""

APP_AT_HEAD = "print('hello')\n"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _init(root: Path, message: str) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root,
                   check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)


def build_workflow(name: str, *, restricted: bool) -> dict:
    """A two-stage workflow declaring the restriction on its verifying stage.

    `restricted` false declares the key nowhere at all, which is the shape every
    definition had before this story and the one a stage that legitimately runs
    the suite is under.
    """
    declaration = {"may_not_run_suite": True} if restricted else {}
    return conftest.build_workflow(
        conftest.workflow_stage(
            outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
            changed_files=conftest.CHANGED_FILES,
            schemas={conftest.CHANGED_FILES: "changed-files"},
            max_self_routes=1),
        conftest.workflow_stage(
            name=conftest.VERIFYING_STAGE,
            outputs=(conftest.VERIFICATION_RESULT,),
            schemas={conftest.VERIFICATION_RESULT: "verification-result",
                     conftest.RETRY_GUIDANCE: "retry-guidance"},
            max_self_routes=1,
            retry_routing={"implementation-defect": {
                "stage": conftest.StageRef(0),
                "when": "the behaviour the story asked for is missing"}},
            **declaration),
        escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
        name=name,
    )


RESTRICTED = build_workflow("restricted-workflow", restricted=True)
UNRESTRICTED = build_workflow("unrestricted-workflow", restricted=False)
WRITING, VERIFYING = [stage["name"] for stage in RESTRICTED["stages"]]

#: What "the coordinator passed no suite command" looks like from inside the
#: runner, as distinct from a suite command of None. A stage under no
#: declaration must not be invoked with the keyword at all, so the two have to
#: be distinguishable here or the assertion cannot say which happened.
NO_SUITE = object()


class Runner:
    """A fake agent runner recording the keywords each invocation was handed.

    Every stage writes the artifacts its declaration names. What it records
    beyond the calls is the *keywords it was given*: a stage under no
    declaration has to be invoked with exactly the arguments the coordinator
    passed before this story, which is a statement about the call rather than
    about its result.
    """

    def __init__(self, target_root: Path):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.calls: list[str] = []
        #: (stage, the suite command it was handed, or NO_SUITE)
        self.suites: list[tuple[str, object]] = []
        #: (stage, every keyword beyond the ones the coordinator always passes)
        self.extras: list[tuple[str, dict]] = []

    def suite_at(self, stage: str):
        return next(suite for name, suite in self.suites if name == stage)

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None, **extra):
        ordinal = len(self.calls)
        self.calls.append(stage)
        self.extras.append((stage, dict(extra)))
        self.suites.append((stage, extra.get("suite_command", NO_SUITE)))

        if stage == WRITING:
            write(self.target_root / "src" / "app.py",
                  APP_AT_HEAD + f"print('invocation {ordinal + 1}')\n")
            write(self.run_dir / conftest.CHANGED_FILES,
                  json.dumps({"modified": ["src/app.py"], "created": [],
                              "deleted": []}, indent=2) + "\n")
            write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY,
                  f"Implemented on invocation {ordinal + 1}.\n")
        elif stage == VERIFYING:
            write(self.run_dir / conftest.VERIFICATION_RESULT,
                  json.dumps(PASS, indent=2) + "\n")
        return AgentResult(ok=True, result_text=f"{stage} done")


class RunnerWithoutTheSuiteParameter(Runner):
    """The same runner with the signature it had before this story.

    No `**extra` and no suite keyword, so a coordinator passing one would raise
    `TypeError` here rather than being quietly accepted. This is how "a stage
    under no declaration is invoked with exactly today's arguments" is checked as
    a property of the call rather than of what a permissive fake chose to ignore.
    """

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None):
        return Runner.__call__(self, prompt, stage=stage, cwd=cwd,
                               log_path=log_path,
                               permission_mode=permission_mode, model=model,
                               allowed_tools=allowed_tools)


@pytest.fixture
def environment(tmp_path):
    """A builder for (target, harness) pairs running a given definition, with
    the target configuring a given test command."""
    built = []

    def make(workflow: dict, test_command: str) -> tuple[Path, Path]:
        tag = f"{workflow['name']}-{len(built)}"
        built.append(tag)
        harness = conftest.materialize_workflow(
            workflow, tmp_path / f"harness-{tag}", rules=RULES)
        _init(harness, "harness")

        target = tmp_path / f"target-{tag}"
        for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                    ".harness/logs", ".harness/docs"):
            (target / sub).mkdir(parents=True)
        write(target / ".harness" / "config.yaml",
              CONFIG.format(workflow=workflow["name"], test_command=test_command))
        write(target / ".harness" / "stories" / f"{STORY_ID}.yaml", STORY)
        write(target / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
        write(target / ".harness" / "standards" / "testing.md",
              "# Testing\n- test it\n")
        write(target / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
        write(target / "src" / "app.py", APP_AT_HEAD)
        _init(target, "initial")
        subprocess.run(["git", "branch", "-M", DEFAULT_BRANCH], cwd=target,
                       check=True)
        return target, harness
    return make


def run(target: Path, harness: Path, runner) -> int:
    return story_coordinator.run_story(STORY_ID, harness, target, runner)


def test_a_declaring_stage_is_handed_the_targets_configured_command(environment):
    """AC4's first half: what reaches the guard comes out of the target's
    configuration, stage by stage.

    The declaring stage receives the configured command verbatim; the stage
    beside it, under no declaration, is invoked with no such keyword at all —
    which is the discrimination the criterion asks for, read off one run.
    """
    target, harness = environment(RESTRICTED, SUITE_ALPHA)
    runner = Runner(target)

    assert run(target, harness, runner) == 0

    assert runner.calls == [WRITING, VERIFYING]
    assert runner.suite_at(VERIFYING) == SUITE_ALPHA
    assert runner.suite_at(WRITING) is NO_SUITE


def test_the_command_handed_over_is_the_one_the_target_configures(environment):
    """AC4, the moving half: a target configuring a different command hands a
    different one over, and the guard driven with each denies that one's
    invocation and is silent about the other's.

    The two halves are asserted together deliberately — what is denied and what
    is permitted move as one, which is what "decided from configuration" means.
    A guard reading a framework name would deny both, or neither.
    """
    handed = {}
    for command in (SUITE_ALPHA, SUITE_BETA):
        target, harness = environment(RESTRICTED, command)
        runner = Runner(target)
        assert run(target, harness, runner) == 0
        handed[command] = runner.suite_at(VERIFYING)

    assert handed == {SUITE_ALPHA: SUITE_ALPHA, SUITE_BETA: SUITE_BETA}

    for configured, other in ((SUITE_ALPHA, SUITE_BETA),
                              (SUITE_BETA, SUITE_ALPHA)):
        assert decision(INVOCATIONS[configured],
                        handed[configured]) == "deny", configured
        assert decision(INVOCATIONS[other], handed[configured]) is None, configured


def test_a_stage_under_no_declaration_is_invoked_with_no_suite_command(
    environment,
):
    """AC6: a definition declaring the restriction nowhere calls the runner with
    exactly the arguments it passed before this story — asserted as the absence
    of the keyword rather than as a suite command of None, which is a different
    call.

    The control is the same runner under the declaring definition, which does
    receive it, so the absence is the coordinator withholding the keyword rather
    than the recording looking in the wrong place.
    """
    target, harness = environment(UNRESTRICTED, SUITE_ALPHA)
    runner = Runner(target)

    assert run(target, harness, runner) == 0

    assert runner.calls == [WRITING, VERIFYING]
    for stage, extra in runner.extras:
        assert extra == {}, (stage, extra)
    assert all(suite is NO_SUITE for _, suite in runner.suites)

    declaring_target, declaring_harness = environment(RESTRICTED, SUITE_ALPHA)
    declaring = Runner(declaring_target)
    assert run(declaring_target, declaring_harness, declaring) == 0
    assert declaring.suite_at(VERIFYING) is not NO_SUITE


def test_a_runner_with_the_older_signature_still_drives_an_undeclared_run(
    environment,
):
    """The same property from the runner's side, where it actually bites: a fake
    whose signature has no suite parameter at all. A coordinator that passed the
    keyword unconditionally would raise `TypeError` here.

    The control is the same runner under the declaring definition, where the
    coordinator does pass it and the call is refused — so this is a statement
    about what the undeclared path passes rather than about a signature that
    accepts anything.
    """
    target, harness = environment(UNRESTRICTED, SUITE_ALPHA)
    runner = RunnerWithoutTheSuiteParameter(target)

    assert run(target, harness, runner) == 0
    assert runner.calls == [WRITING, VERIFYING]

    declaring_target, declaring_harness = environment(RESTRICTED, SUITE_ALPHA)
    refused = RunnerWithoutTheSuiteParameter(declaring_target)
    with pytest.raises(TypeError, match="suite_command"):
        run(declaring_target, declaring_harness, refused)


def test_the_guard_registered_for_a_declaring_stage_carries_the_command(
    tmp_path,
):
    """The last link of the chain, at the other end of it: what `run_agent`
    hands the guard is the command it was given, rendered into the shipped
    declaration and quoted as one word.

    Read off `guard_settings` rather than off a running agent, and paired with
    the same call naming no command — whose rendered hook command is exactly the
    guard path, which is what an unaffected stage is registered with.
    """
    awkward = "run the suite 'now' \"please\""
    with_command = json.loads(agent_runner.guard_settings(suite_command=awkward))
    without = json.loads(agent_runner.guard_settings())

    def hook_command(settings: dict) -> str:
        entries = settings["hooks"]["PreToolUse"]
        commands = [hook["command"] for entry in entries for hook in entry["hooks"]]
        assert len(commands) == 1, commands
        return commands[0]

    registered = hook_command(with_command)
    plain = hook_command(without)
    assert plain == str(agent_runner.hooks_dir() / agent_runner.GUARD_NAME)
    assert registered.startswith(plain)

    # Quoted as one word, which is what makes a command carrying spaces and
    # quotes survive the word splitting the hook runner performs. Read back
    # through the same splitting rather than compared to a spelling of the
    # quoting, so what is asserted is what the guard would receive.
    assert shlex.split(registered) == [plain, awkward]


# ---------------------------------------------------------------------------
# AC9, AC10: what the two shipped definitions declare
#
# The shipped definitions are the *subject* here rather than an input, which is
# what makes reading them right: the criterion is about which stages this
# repository restricts.
# ---------------------------------------------------------------------------


RESTRICTION = "may_not_run_suite"

#: What each shipped definition declares the restriction on, and what it
#: deliberately leaves without it. The implementer is left out by name in both:
#: story-064 established its targeted runs and this story does not touch them.
SHIPPED_EXPECTATIONS = {
    "story-workflow": {"tester", "documenter", "verifier"},
    "refactor-workflow": {"documenter", "verifier"},
}


def shipped(name: str) -> dict:
    return conftest.shipped_workflow(HARNESS_ROOT, name)


@pytest.mark.parametrize("name", sorted(SHIPPED_EXPECTATIONS))
def test_each_shipped_definition_restricts_exactly_the_stages_it_should(name):
    """AC9, read as a partition rather than as a list of presences: the stages
    that declare it are exactly the expected set, and every other stage the
    definition defines — the implementer among them — declares nothing.

    Stated in one assertion so a restriction added to a stage nobody intended is
    reported here rather than passing for being outside a list of names.
    """
    definition = shipped(name)
    stages = definition["stages"]
    declaring = {stage["name"] for stage in stages if stage.get(RESTRICTION)}
    defined = {stage["name"] for stage in stages}

    assert declaring == SHIPPED_EXPECTATIONS[name], name
    assert "implementer" in defined - declaring, name
    for stage in stages:
        if stage["name"] not in declaring:
            assert RESTRICTION not in stage, (name, stage["name"])


@pytest.mark.parametrize("name", sorted(SHIPPED_EXPECTATIONS))
def test_each_shipped_definition_declares_it_as_a_true_flag(name):
    """The declaration is a flag rather than a value with a shape of its own:
    the coordinator reads it for truth, so a definition carrying anything else
    there would be read as a restriction it does not state."""
    for stage in shipped(name)["stages"]:
        if RESTRICTION in stage:
            assert stage[RESTRICTION] is True, (name, stage["name"])


@pytest.mark.parametrize("name", sorted(SHIPPED_EXPECTATIONS))
def test_each_shipped_definition_still_passes_the_pre_flight_validators(name):
    """AC10: the new key is not a key any well-formedness check refuses.

    The validators are the ones the pre-flight runs, and their control is the
    same definition with a value the pre-flight is known to refuse planted in
    it — so a clean sweep is a statement about these definitions rather than
    about validators that stopped looking.
    """
    definition = shipped(name)
    stages = definition["stages"]
    assert story_coordinator.self_route_problems(stages) == [], name
    assert story_coordinator.retry_routing_problems(stages) == [], name
    assert story_coordinator.cost_ceiling_problems(definition) == [], name
    assert story_coordinator.applies_when_problems(definition) == [], name

    broken = {**definition, "max_run_cost_usd": "not a number"}
    assert story_coordinator.cost_ceiling_problems(broken), name


# ---------------------------------------------------------------------------
# AC11: the tester prompt's paragraph
# ---------------------------------------------------------------------------


def the_paragraph(text: str) -> str:
    """The paragraph opening with `PARAGRAPH_OPENING`, up to the blank line.

    Found by its opening rather than by a line number, so an edit above it does
    not move what is measured. Raises rather than returning empty when the
    opening is absent, so a paragraph that has been rewritten out of recognition
    fails as itself rather than as a comparison of two empty strings.
    """
    start = text.find(PARAGRAPH_OPENING)
    assert start != -1, "the paragraph this story shortened is not here"
    end = text.find("\n\n", start)
    return text[start:end if end != -1 else len(text)]


def test_the_paragraph_is_shorter_than_the_one_it_replaced_and_names_the_guard():
    """AC11: the prompt did not grow, the paragraph shrank, and it points at
    what enforces the rule instead of arguing for it.

    The whole file is measured beside the paragraph, because a paragraph that
    shortened while the file grew would satisfy the narrow claim and break the
    constraint the story states.
    """
    baseline = conftest.history_fixture(TESTER_PROMPT_BASELINE)
    today = (HARNESS_ROOT / TESTER_PROMPT_REL).read_text(encoding="utf-8")
    assert baseline != today, "the carried baseline is not a past text"

    before = the_paragraph(baseline)
    after = the_paragraph(today)
    assert len(after) < len(before)
    assert len(after.splitlines()) < len(before.splitlines())
    assert len(today) < len(baseline)

    assert "guard" in after
    assert "guard" not in before


def test_the_measurement_reports_a_paragraph_that_did_not_shorten():
    """The control for the comparison above: the same measurement over today's
    prompt with its own baseline paragraph restored reports no shortening, so a
    green result is about the edit that was made rather than about a comparison
    that could not fail."""
    baseline = conftest.history_fixture(TESTER_PROMPT_BASELINE)
    today = (HARNESS_ROOT / TESTER_PROMPT_REL).read_text(encoding="utf-8")
    restored = today.replace(the_paragraph(today), the_paragraph(baseline), 1)

    assert the_paragraph(restored) == the_paragraph(baseline)
    assert not len(the_paragraph(restored)) < len(the_paragraph(baseline))
    assert "guard" not in the_paragraph(restored)
