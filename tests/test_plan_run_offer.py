"""Independent validation for story-059: l5-plan offers to run the story it
just planned.

The subject is a *script that has just committed and pushed something and then
asks a question*, so almost nothing here is asserted from source. The throwaway
target repository, the stub `claude` on PATH and the bare remote are
`tests/test_plan_commit.py`'s, imported rather than rebuilt; what this module
adds is a mirrored harness root whose `scripts/l5-plan` is a copy of the shipped
script and whose `scripts/l5-run` is a stub that records the argument list it
was given and whether its three standard streams are a terminal. The real
`scripts/l5-run` is never executed and no model is invoked anywhere in this
file: every planning session is the stub, and every run is the stub.

The mirrored root is the same idiom `test_plan_time_validation.pre_story_harness`
uses — a scripts directory of its own, every other harness directory a symlink
to the real one — for the same reason: the script under test then loads the
same config, workflow, rules and prompt template the shipped one does, and the
only thing that differs is the sibling it launches.

Every assertion here that claims an absence carries a control showing the same
observation reporting the violation it exists to catch:

  * "a declined offer starts nothing" sits beside the same fixture answered
    with Enter, where the run stub's log does appear;
  * "a commit that fails offers nothing" and "a push that fails offers nothing"
    each sit beside the same repository with the failure removed, where the
    offer is made and the command is printed;
  * "a session with more than one artifact offers nothing" sits beside a
    single-artifact run on the same kind of terminal, which does offer;
  * "stdin that is not a terminal exits rather than blocking" sits beside a
    harness whose offer asks regardless of the terminal and reads from a fifo
    nothing writes to, where the same bounded wait reports the block;
  * "the launch passes no capture or redirect argument" sits beside the same
    reading of that call with `capture_output=True` planted in it;
  * "the planner prompt no longer tells the planner to print the run command"
    sits beside the sentence that file carried before this story, which the
    same scanner reports.

That last control is a sentence constructed here rather than a text resolved
out of this repository's commit graph: it is an *input* to the scanner, one
line long, and reproducing it costs nothing while resolving it would move
whenever something is committed, renamed, squashed or rebased.
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

import context_assembler
import harness_config
import plan_run_offer

from test_plan_commit import (  # noqa: F401 - fixtures used by name
    Planning,
    artifact,
    bare_remote,
    committed_paths,
    drain,
    make_planning,
    planning,
    remote_refs,
    writes,
)

HARNESS_ROOT = Path(__file__).resolve().parents[1]
L5_PLAN = HARNESS_ROOT / "scripts" / "l5-plan"
PLAN_RUN_OFFER = HARNESS_ROOT / "orchestration" / "plan_run_offer.py"

#: A stub `l5-run`. It records the argument list it was given, the directory it
#: was started in and whether its three standard streams are a terminal, and
#: exits with the status it was told to. It starts no story and reads no
#: artifact, so a log that exists afterwards means l5-plan launched the run.
RUN_STUB = '''\
#!/usr/bin/env python3
import json
import os
import pathlib
import sys

pathlib.Path(os.environ["L5_RUN_STUB_LOG"]).write_text(
    json.dumps({
        "argv": sys.argv,
        "cwd": os.getcwd(),
        "tty": [os.isatty(0), os.isatty(1), os.isatty(2)],
    }),
    encoding="utf-8",
)
sys.stdout.write("stub run\\n")
sys.stdout.flush()
sys.exit(int(os.environ.get("L5_RUN_STUB_EXIT", "0")))
'''

#: The offer, redefined to ask whatever stdin is and to read from a fifo
#: nothing writes to. Appended to the working tree's own source, never to a
#: pinned revision, and used once: as the control for the non-terminal case,
#: where it is the version that blocks.
BLOCKING_OFFER = '''

def can_prompt(stream):
    return True


def should_run(stream):
    with open(os.environ["L5_BLOCK_FIFO"], encoding="utf-8") as blocking:
        return blocking.readline().strip() == ""
'''


# --------------------------------------------------------------------------
# A harness root whose l5-run is a stub, and the runners that drive it.
# --------------------------------------------------------------------------


@dataclass
class Harness:
    """A harness root running the shipped l5-plan beside a stub l5-run."""

    root: Path
    log: Path

    @property
    def plan(self) -> Path:
        return self.root / "scripts" / "l5-plan"

    @property
    def run_script(self) -> Path:
        return self.root / "scripts" / "l5-run"

    def launched(self) -> dict:
        return json.loads(self.log.read_text(encoding="utf-8"))

    def env(self, planning: Planning, **stub) -> dict:
        return planning.env(L5_RUN_STUB_LOG=str(self.log), **stub)


def stubbed_harness(tmp_path: Path, offer_source: str | None = None) -> Harness:
    """A harness root whose l5-run is the stub above.

    `scripts/l5-plan` is a copy of the shipped script rather than a symlink,
    because the script resolves its harness root from its own resolved path and
    a symlink would resolve back to the real one — where `scripts/l5-run` is the
    real one. Every other directory is a symlink to the real thing, so this runs
    the shipped orchestration, prompts, schemas, workflows and rules.

    `offer_source`, when given, replaces `orchestration/plan_run_offer.py`; it
    is used by the one control that needs an offer that behaves differently.
    """
    root = tmp_path / "stubbed-harness"
    (root / "scripts").mkdir(parents=True)
    for name in ("prompts", "schemas", "workflows", "rules"):
        os.symlink(HARNESS_ROOT / name, root / name)
    if offer_source is None:
        os.symlink(HARNESS_ROOT / "orchestration", root / "orchestration")
    else:
        shutil.copytree(HARNESS_ROOT / "orchestration", root / "orchestration")
        (root / "orchestration" / PLAN_RUN_OFFER.name).write_text(
            offer_source, encoding="utf-8")
    plan = root / "scripts" / "l5-plan"
    plan.write_text(L5_PLAN.read_text(encoding="utf-8"), encoding="utf-8")
    plan.chmod(0o755)
    run = root / "scripts" / "l5-run"
    run.write_text(RUN_STUB, encoding="utf-8")
    run.chmod(0o755)
    return Harness(root, tmp_path / "launched.json")


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return stubbed_harness(tmp_path / "harness")


def plan_without_a_terminal(harness: Harness, planning: Planning, *args: str,
                            timeout: int = 120,
                            **stub) -> subprocess.CompletedProcess:
    """Run l5-plan with stdin redirected from /dev/null.

    The timeout is the whole point of this runner: a version of the offer that
    asked anyway and waited for an answer would never return, and this reports
    that as a failure rather than as a hung suite.
    """
    with open(os.devnull, "rb") as devnull:
        return subprocess.run(
            [sys.executable, str(harness.plan), *args],
            cwd=planning.root, env=harness.env(planning, **stub),
            stdin=devnull, capture_output=True, text=True, timeout=timeout,
        )


def plan_on_a_terminal(harness: Harness, planning: Planning, *args: str,
                       reply: bytes | None = None, **stub) -> tuple[int, str]:
    """Run l5-plan with a pty for stdin, stdout and stderr, and answer it.

    The reply is written to the pty as soon as the process starts, the way
    `test_plan_commit`'s interrupt test writes its own: nothing between here and
    the offer reads stdin — the stub session does not — so the bytes wait in the
    terminal's buffer until the offer reads them.
    """
    import pty

    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, str(harness.plan), *args],
        cwd=planning.root, env=harness.env(planning, **stub),
        stdin=slave, stdout=slave, stderr=slave,
        start_new_session=True,
    )
    os.close(slave)
    if reply is not None:
        os.write(master, reply)
    return drain(process, master)


def wrote(story_id: str = "story-900") -> str:
    return writes((f".harness/stories/{story_id}.yaml", artifact(story_id)))


def command_for(harness: Harness, planning: Planning, story_id: str,
                base: str | None = None) -> str:
    """The command the offer renders, from the directory l5-plan was run in."""
    cwd = Path.cwd()
    os.chdir(planning.root)
    try:
        return plan_run_offer.run_command(harness.root, story_id, base)
    finally:
        os.chdir(cwd)


# --------------------------------------------------------------------------
# Enter runs it, and the run is the terminal's.
# --------------------------------------------------------------------------


def test_enter_starts_the_run_for_the_story_that_was_just_committed(
        harness: Harness, planning: Planning):
    """The artifact is committed and pushed, and then the run is launched."""
    remote = planning.remote
    refs_before = remote_refs(remote)

    status, _ = plan_on_a_terminal(harness, planning, "add a thing",
                                   reply=b"\n", L5_STUB_WRITE=wrote(),
                                   L5_RUN_STUB_EXIT=6)

    assert committed_paths(planning.root) == [".harness/stories/story-900.yaml"]
    assert remote_refs(remote) != refs_before
    launched = harness.launched()
    assert launched["argv"] == [str(harness.run_script), "story-900"]
    assert launched["cwd"] == str(planning.root.resolve())
    # l5-plan exits with the run's own status.
    assert status == 6


def test_the_run_is_given_the_terminal_l5_plan_was_given(harness: Harness,
                                                         planning: Planning):
    """Nothing captures, pipes or buffers the run's output."""
    plan_on_a_terminal(harness, planning, "add a thing", reply=b"\n",
                       L5_STUB_WRITE=wrote())

    assert harness.launched()["tty"] == [True, True, True]


def subprocess_run_keywords(source: str, function: str) -> list[str]:
    """The keyword arguments the named function passes to `subprocess.run`.

    A redirect or a capture can only reach the child as one of these, so this
    is what "the child inherits the terminal" is read off. The control below
    plants one and shows the reading reports it.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            return sorted(
                keyword.arg or "**"
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "run"
                for keyword in call.keywords
            )
    raise AssertionError(f"{function} is not defined in this source")


def test_the_launch_passes_no_capture_or_redirect_argument():
    source = PLAN_RUN_OFFER.read_text(encoding="utf-8")
    assert subprocess_run_keywords(source, "launch_run") == []

    # Control: the same reading of the same call with a capture planted in it.
    planted = source.replace(
        "subprocess.run(_arguments(harness_root, story_id, base))",
        "subprocess.run(_arguments(harness_root, story_id, base), "
        "capture_output=True)",
    )
    assert planted != source, "the launch's call site has moved"
    assert subprocess_run_keywords(planted, "launch_run") == ["capture_output"]


# --------------------------------------------------------------------------
# Anything else skips, and the skip path prints a command instead.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("reply", [b"n\n", b"no\n", b"maybe not\n"])
def test_a_non_empty_reply_skips_and_starts_nothing(harness: Harness,
                                                    planning: Planning,
                                                    reply: bytes):
    status, output = plan_on_a_terminal(harness, planning, "add a thing",
                                        reply=reply, L5_STUB_WRITE=wrote())

    assert status == 0
    assert not harness.log.exists()
    assert command_for(harness, planning, "story-900") in output

    # Control: the same harness and the same repository, answered with Enter,
    # where the run is launched — so "nothing was started" is the reply's doing
    # rather than a stub that never records anything.
    plan_on_a_terminal(harness, planning, "add another thing", reply=b"\n",
                       L5_STUB_WRITE=wrote("story-901"))
    assert harness.launched()["argv"] == [str(harness.run_script), "story-901"]


def test_the_printed_command_taken_verbatim_starts_the_story_unmodified(
        harness: Harness, planning: Planning):
    """What is printed is pasted back into a shell and run, from the same cwd."""
    result = plan_without_a_terminal(harness, planning, "add a thing",
                                     L5_STUB_WRITE=wrote())
    command = command_for(harness, planning, "story-900")
    assert command in result.stdout

    subprocess.run(command, shell=True, cwd=planning.root,
                   env=harness.env(planning), check=True, capture_output=True)

    assert harness.launched()["argv"] == [str(harness.run_script), "story-900"]


@pytest.mark.parametrize("where, expected", [
    ("root", os.path.join("scripts", "l5-run")),
    ("scripts", f".{os.sep}l5-run"),
    ("elsewhere", None),
])
def test_the_command_names_the_script_as_it_must_be_typed(
        harness: Harness, tmp_path: Path, monkeypatch, where: str,
        expected: str | None):
    """Relative when the script lies beneath the current directory, else absolute.

    A relative name carrying no separator is written `./l5-run`, because a bare
    name is looked up on PATH rather than in the current directory. Each
    rendering is then run from the directory it was rendered for, so what is
    asserted is that the command works rather than that it reads a certain way.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    cwd = {"root": harness.root, "scripts": harness.root / "scripts",
           "elsewhere": outside}[where]
    monkeypatch.chdir(cwd)

    command = plan_run_offer.run_command(harness.root, "story-900")

    if expected is None:
        assert command == f"{harness.run_script} story-900"
    else:
        assert command == f"{expected} story-900"
    environment = dict(os.environ, L5_RUN_STUB_LOG=str(harness.log))
    subprocess.run(command, shell=True, cwd=cwd, env=environment, check=True,
                   capture_output=True)
    assert harness.launched()["argv"][1:] == ["story-900"]


def test_the_reply_is_read_once_and_never_re_asked():
    """Driven directly, so what is asserted is the number of reads.

    A version that looped until it recognised an answer would read again; this
    one reads a line, decides, and leaves everything after it unread.
    """
    class CountingTerminal:
        def __init__(self, text: str):
            self.remaining = text
            self.reads = 0

        def isatty(self) -> bool:
            return True

        def readline(self) -> str:
            self.reads += 1
            line, _, self.remaining = self.remaining.partition("\n")
            return f"{line}\n"

    stream = CountingTerminal("maybe\nSTILL-UNREAD\n")
    assert plan_run_offer.should_run(stream) is False
    assert stream.reads == 1
    assert "STILL-UNREAD" in stream.remaining

    # Control: the same reader over an empty line does consume it and does say
    # run, so one read above is a decision rather than a reader that never looked.
    accepting = CountingTerminal("\nSTILL-UNREAD\n")
    assert plan_run_offer.should_run(accepting) is True
    assert accepting.reads == 1


# --------------------------------------------------------------------------
# No terminal: nothing is asked, nothing is read, nothing blocks.
# --------------------------------------------------------------------------


def test_stdin_that_is_not_a_terminal_is_never_prompted_and_exits(
        harness: Harness, planning: Planning):
    """The report is exactly what it would be with the offer's command appended.

    Asserted as the whole of stdout rather than as a substring, because "no
    prompt was written" is a claim about everything that was written, and a
    prompt ends without a newline where every line here has one.
    """
    result = plan_without_a_terminal(harness, planning, "add a thing",
                                     L5_STUB_WRITE=wrote())

    assert result.returncode == 0, result.stderr
    assert not harness.log.exists()
    assert result.stdout.splitlines() == [
        "stub session",
        "l5-plan: committed .harness/stories/story-900.yaml as "
        "Plan story-900: Stub planned story",
        "l5-plan: pushed main to origin",
        f"l5-plan: run story-900 with: "
        f"{command_for(harness, planning, 'story-900')}",
    ]
    assert result.stdout.endswith("\n")


def test_a_bounded_wait_reports_an_offer_that_asks_anyway(tmp_path: Path,
                                                          planning: Planning):
    """The control for the test above.

    The harness here carries an offer that asks whatever stdin is and reads
    from a fifo nothing ever writes to — the version the terminal check exists
    to prevent. The same runner, given the same /dev/null stdin, does not
    return, and the bounded wait says so. Without this, "it exited" would be a
    claim about a wait that has never had anything to wait for.
    """
    blocking = stubbed_harness(
        tmp_path / "blocking",
        PLAN_RUN_OFFER.read_text(encoding="utf-8") + BLOCKING_OFFER,
    )
    fifo = tmp_path / "never-written"
    os.mkfifo(fifo)
    # Held open read-write so opening it does not itself block; nothing is ever
    # written to it, so the offer's read is what blocks.
    keep_open = os.open(fifo, os.O_RDWR)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            plan_without_a_terminal(blocking, planning, "add a thing",
                                    timeout=20, L5_STUB_WRITE=wrote(),
                                    L5_BLOCK_FIFO=str(fifo))
    finally:
        os.close(keep_open)
    # The commit and the push still happened: the block is at the offer, after
    # everything this story leaves alone.
    assert committed_paths(planning.root) == [".harness/stories/story-900.yaml"]


# --------------------------------------------------------------------------
# --base reaches both the launched run and the printed command.
# --------------------------------------------------------------------------


def test_base_reaches_the_launched_run(harness: Harness, planning: Planning):
    plan_on_a_terminal(harness, planning, "--base", "main", "add a thing",
                       reply=b"\n", L5_STUB_WRITE=wrote())

    assert harness.launched()["argv"] == [
        str(harness.run_script), "story-900", "--base", "main"]


def test_base_reaches_the_printed_command(harness: Harness, planning: Planning):
    result = plan_without_a_terminal(harness, planning, "--base", "main",
                                     "add a thing", L5_STUB_WRITE=wrote())

    with_base = command_for(harness, planning, "story-900", "main")
    assert with_base.endswith("story-900 --base main")
    printed = [line for line in result.stdout.splitlines()
               if line.endswith(with_base)]
    assert len(printed) == 1, result.stdout
    # Compared as a whole line rather than as a substring: the rendering
    # without a base is a prefix of this one, so a substring search would pass
    # on either and say nothing about which was printed.
    without_base = command_for(harness, planning, "story-900")
    assert [line for line in result.stdout.splitlines()
            if line.endswith(without_base)] == []


# --------------------------------------------------------------------------
# The offer is reached only after the commit and the push have succeeded.
# --------------------------------------------------------------------------


def refuse_to_commit(planning: Planning) -> None:
    """A pre-commit hook that fails, so `git commit` does."""
    hooks = planning.root.joinpath(".git", "hooks")
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)


def test_a_commit_that_fails_returns_1_and_offers_nothing(harness: Harness,
                                                          planning: Planning):
    refuse_to_commit(planning)
    before = planning.head()

    status, output = plan_on_a_terminal(harness, planning, "add a thing",
                                        reply=b"n\n", L5_STUB_WRITE=wrote())

    assert status == 1
    assert planning.head() == before
    assert "could not commit" in output
    assert not harness.log.exists()
    assert command_for(harness, planning, "story-900") not in output

    # Control: the same repository with the hook removed. The commit succeeds,
    # the offer is made, and the command this test asserts the absence of is
    # printed — so the absence above is the failure's doing.
    planning.root.joinpath(".git", "hooks", "pre-commit").unlink()
    _, control = plan_on_a_terminal(harness, planning, "add another thing",
                                    reply=b"n\n", L5_STUB_WRITE=wrote("story-901"))
    assert command_for(harness, planning, "story-901") in control


def test_a_push_that_fails_returns_1_and_offers_nothing(tmp_path: Path,
                                                        harness: Harness):
    """A repository with no remote: the commit is made and the push cannot be."""
    remoteless = make_planning(tmp_path / "remoteless")
    assert remoteless.git("remote").stdout.strip() == ""

    status, output = plan_on_a_terminal(harness, remoteless, "add a thing",
                                        reply=b"n\n", L5_STUB_WRITE=wrote())

    assert status == 1
    assert committed_paths(remoteless.root) == [
        ".harness/stories/story-900.yaml"]
    assert "push it yourself." in output
    assert not harness.log.exists()
    assert command_for(harness, remoteless, "story-900") not in output

    # Control: the same repository given somewhere to push to. The push
    # succeeds, the offer is made, and the command appears.
    bare_remote(tmp_path / "remoteless", remoteless, upstream=True)
    _, control = plan_on_a_terminal(harness, remoteless, "add another thing",
                                    reply=b"n\n", L5_STUB_WRITE=wrote("story-901"))
    assert command_for(harness, remoteless, "story-901") in control


def test_more_than_one_artifact_prints_a_command_for_each_and_offers_nothing(
        harness: Harness, planning: Planning):
    """A terminal is there to ask on; the offer is still not made.

    Nothing is written to the pty, so a version that asked would wait for an
    answer that never comes and the wait below would report it.
    """
    status, output = plan_on_a_terminal(
        harness, planning, "add two things",
        L5_STUB_WRITE=writes(
            (".harness/stories/story-903.yaml", artifact("story-903")),
            (".harness/stories/story-904.yaml", artifact("story-904")),
        ),
    )

    assert status == 0
    assert not harness.log.exists()
    for story_id in ("story-903", "story-904"):
        assert command_for(harness, planning, story_id) in output

    # Control: one artifact, the same terminal, nothing written to it either —
    # and this time the offer is made, which is why the run starts when the
    # answer arrives. Answered here so the process ends.
    _, control = plan_on_a_terminal(harness, planning, "add one thing",
                                    reply=b"\n", L5_STUB_WRITE=wrote("story-905"))
    assert harness.launched()["argv"] == [str(harness.run_script), "story-905"]


# --------------------------------------------------------------------------
# The planner prompt stops printing the command the script now prints.
# --------------------------------------------------------------------------


def rendered_planner_prompt() -> str:
    """The planner prompt as l5-plan renders it, not the template as it reads.

    Rendered against this repository's own configuration and workflow, the way
    the script does, because what the planner is told is the rendering.
    """
    config = harness_config.load_config(HARNESS_ROOT)
    workflow = harness_config.load_workflow(
        HARNESS_ROOT, config.get("workflow", "story-workflow"), config)
    rules = harness_config.load_rules(HARNESS_ROOT)
    template = context_assembler.load_template(HARNESS_ROOT, "planner.md")
    context = context_assembler.schema_context(HARNESS_ROOT)
    context.update(context_assembler.workflow_context(workflow, rules))
    prose = context_assembler.resolved_partial(
        HARNESS_ROOT, context_assembler.PROSE_LAYER, context)
    if prose is not None:
        context["prose_layer"] = prose
    return context_assembler.render(template, context)


#: An instruction to print the command that starts a run. "l5-run parses and
#: validates the artifact" is a statement about the harness rather than a
#: command to type, and the control below shows the difference is one this
#: scanner can see.
RUN_COMMAND_INSTRUCTIONS = (
    re.compile(r"(?i)\bscripts/l5-run\b"),
    re.compile(r"(?i)\bhow to (?:execute|run) it\b"),
)


def run_command_instructions(text: str) -> list[str]:
    return [match.group(0) for pattern in RUN_COMMAND_INSTRUCTIONS
            for match in pattern.finditer(text)]


#: The closing sentence `prompts/planner.md` carried before this story,
#: constructed here rather than resolved out of the commit graph: it is an
#: input to the scanner above, and one line of it.
THE_SENTENCE_BEFORE = ("After writing the artifact, tell the developer the "
                       "story id and how to execute it: scripts/l5-run "
                       "<story-id>.")


def test_the_rendered_planner_prompt_still_names_the_story_id():
    assert re.search(r"(?i)tell the developer the story id",
                     rendered_planner_prompt())


def test_the_rendered_planner_prompt_no_longer_prints_the_run_command():
    assert run_command_instructions(rendered_planner_prompt()) == []

    # Control: the sentence this file's closing line replaced. The same scanner
    # reports it, so the emptiness above is a change to the prompt rather than
    # a scanner that sees nothing.
    assert run_command_instructions(THE_SENTENCE_BEFORE) != []
    assert run_command_instructions(
        rendered_planner_prompt() + THE_SENTENCE_BEFORE) != []


# --------------------------------------------------------------------------
# What this file itself may not do.
# --------------------------------------------------------------------------


def shipped_run_script_calls(source: str) -> list[str]:
    """Every line of a source that names the harness's own `scripts/l5-run`.

    This story's tests cover the run path without executing a real run, and
    this is how that is held: the only l5-run any test here starts is the stub
    written into a harness root under tmp_path.
    """
    return [line.strip() for line in source.splitlines()
            if re.search(r"""HARNESS_ROOT\s*/\s*["']scripts["']\s*/\s*["']l5-run""",
                         line)
            or re.search(r"""HARNESS_ROOT\s*\.\s*joinpath\(\s*["']scripts""", line)]


def test_this_module_never_starts_the_shipped_l5_run():
    source = Path(__file__).read_text(encoding="utf-8")
    assert shipped_run_script_calls(source) == []

    # Control: the same reader over this source with the call planted in it.
    # The planted text is spelled across two literals so that no physical line
    # of this file carries the shape the reader looks for — otherwise the
    # control would be reported as a violation of the very rule it controls.
    planted = (f'{source}\n'
               'subprocess.run([str(HARNESS_ROOT / "scripts"'
               ' / "l5-run")])\n')
    assert shipped_run_script_calls(planted) != []
