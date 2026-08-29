"""Independent validation for story-072's other half: a run says which
workflow it is executing.

The announcement a run has always made named the story and not the definition
running it. With two definitions differing in their stage lists, a reader
watching a run go implementer then documenter cannot tell a correct refactor
from a story that skipped its tester — so the first line on the console and
the first line of `events.log` now name the workflow as well, and a resumed
run says which definition it loaded in the first thing it says. The name is
already resolved and already recorded in `state.json`; this is a matter of
saying what the run already knows, which is why the constraint that matters is
that nothing new is written: one `append_event` call per event, under the kind
that event already carried.

Written from the story's acceptance criteria rather than from the
implementation, at two altitudes:

  * **the run.** Targets built under `tmp_path` are driven through the real
    `story_coordinator.run_story` with a fake agent runner, and the
    announcement is read off that run's own console output, its own
    `events.log` and its own `execution-history.json`.
  * **the run somebody started from the planning offer.** The real
    `scripts/l5-plan` and the real `scripts/l5-run` are driven on a pty
    against a throwaway repository with a stub `claude` on PATH, and what that
    run announced is read from the terminal it was given. The story asks for
    this path to be observed rather than inferred from the code path it
    shares, because it is the path that would silently diverge.

**The workflows here are built, not shipped.** Every definition a run below
executes is assembled by the builder in `tests/conftest.py` and written into a
harness root this module owns: the subject is what a run *says about* the
definition it loaded, and a definition is its input. Reading
`workflows/story-workflow.json` here would make what this repository deploys
into something the suite enforces, and would leave the central case — the same
story run under a different definition, announcing a different name —
unwritable, since one definition can only ever answer the question one way.

Every absence asserted here carries a demonstration that the same check
reports the violation it exists to catch:

  * "the announcement names this definition and not the other" sits beside the
    same story run under the other definition, where the names swap;
  * "the run wrote exactly one `workflow-started` entry" sits beside a run
    directory with a second one appended, which the same counter reports as
    two;
  * "the resumed run names what it loaded rather than what the artifact now
    says" sits beside the artifact naming the other definition, which is what
    makes the two answers distinguishable at all;
  * "a run started from the offer announces its workflow on its own terminal"
    sits beside the same offer declined, where no run starts and nothing is
    announced after the planning script's own last line.

No model is invoked anywhere here: every coordinator run goes through the fake
runner below, and every `claude` the end-to-end path reaches is the stub.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
from test_plan_commit import Planning, bare_remote, drain, writes

import story_coordinator
from agent_runner import AgentResult

STORY_ID = "story-001"
PLANNED_ID = "story-900"
DEFAULT_BRANCH = "main"
TESTS_DIR = "tests/"

#: How `append_event` renders one event on the console and in `events.log`:
#: a timestamp and the message. Written once here so both readings below
#: recover the message the same way.
EVENT_LINE = re.compile(r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] (?P<message>.+)$")

#: The same rendering, found anywhere in a line rather than at its start. A
#: terminal shared by two processes carries one of them mid-line: `l5-plan`'s
#: offer prompt ends without a newline, so the first thing the run it launches
#: writes lands on the end of that prompt. Nothing `l5-plan` itself writes is
#: rendered this way, so what this finds is the launched run's own output.
EVENT_ANYWHERE = re.compile(
    r"\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] (?P<message>.+)$")

#: The event kinds the two announcements carry. They are the kinds those
#: events already carried before this story, which is the constraint: no new
#: kind, no second line, and `l5-status` keeps reading what it read.
STARTED = "workflow-started"
RESUMED = "resumed"

RULES = {
    "max_retries": 2,
    "require_verifier_pass": True,
    "blocked_paths": [".git/", ".harness/runs/", "rules/"],
}


# --------------------------------------------------------------------------
# The two definitions this module builds
# --------------------------------------------------------------------------


def runnable_workflow(name: str, writing: str, validating: str) -> dict:
    """A definition a run can complete, whose stages are the ones named."""
    return conftest.build_workflow(
        conftest.workflow_stage(
            name=writing,
            outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
            changed_files=conftest.CHANGED_FILES,
            schemas={conftest.CHANGED_FILES: "changed-files"}),
        conftest.workflow_stage(
            name=validating,
            outputs=(conftest.TEST_RESULTS, conftest.TESTER_CHANGED_FILES),
            changed_files=conftest.TESTER_CHANGED_FILES,
            schemas={conftest.TEST_RESULTS: "test-results",
                     conftest.TESTER_CHANGED_FILES: "changed-files"}),
        conftest.workflow_stage(
            name=conftest.VERIFYING_STAGE,
            outputs=(conftest.VERIFICATION_RESULT,),
            schemas={conftest.VERIFICATION_RESULT: "verification-result"}),
        escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
        name=name,
    )


EXECUTED = runnable_workflow("executed-workflow", "drafting", "checking")
#: The other definition, named differently and staged differently, so "the
#: announcement named the one that ran" is an observation rather than a
#: coincidence. Its name is not a substring of the other's and does not
#: contain it: every assertion below that one name is *absent* from a message
#: would otherwise be answering a question about spelling.
OTHER = runnable_workflow("bystander-workflow", "composing", "auditing")


def stages_of(workflow: dict) -> list[str]:
    return [stage["name"] for stage in workflow["stages"]]


def test_the_two_definitions_this_module_builds_can_be_told_apart():
    """Load-bearing for every "it named this one and not that one" below."""
    assert EXECUTED["name"] != OTHER["name"]
    assert EXECUTED["name"] not in OTHER["name"]
    assert OTHER["name"] not in EXECUTED["name"]
    assert set(stages_of(EXECUTED)) != set(stages_of(OTHER))


# --------------------------------------------------------------------------
# A target, a harness root and a fake runner
# --------------------------------------------------------------------------


STORY = """\
story:
  id: {story_id}
  title: Sample story for the run announcement tests
  description: |
    A stand-in story used to drive the coordinator deterministically against
    a fake runner.
{workflow_line}
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
test_command: echo tests-ok
tests_dir: {tests_dir}
"""

APP_AT_HEAD = "print('hello')\n"


def story_text(declared: str | None = None, story_id: str = STORY_ID,
               mandate: bool = True) -> str:
    """The artifact, with or without the block a run resolves before it starts.

    A story a test hands straight to the coordinator carries one, because since
    story-087 a run whose mandate does not resolve to a human is refused before
    anything is created. A story a *planning session* writes carries none: the
    block is written by l5-plan when the session ends, and an artifact arriving
    from a session already carrying one is refused rather than trusted.
    """
    line = f"  workflow: {declared}\n" if declared else ""
    text = STORY.format(story_id=story_id, workflow_line=line)
    return text + conftest.MANDATE_BLOCK if mandate else text


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


def init_repo(root: Path, message: str = "initial") -> None:
    for command in (
        ["git", "init", "-q", "-b", DEFAULT_BRANCH],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", message],
    ):
        subprocess.run(command, cwd=root, check=True)


def build_target(root: Path, configured: str, declared: str | None,
                 story_id: str = STORY_ID) -> Path:
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml",
          CONFIG.format(workflow=configured, tests_dir=TESTS_DIR))
    if declared is not None or story_id == STORY_ID:
        write(root / ".harness" / "stories" / f"{story_id}.yaml",
              story_text(declared, story_id))
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "src" / "app.py", APP_AT_HEAD)
    write(root / (TESTS_DIR + "test_existing.py"),
          "def test_nothing():\n    assert True\n")
    init_repo(root)
    return root


def build_harness(root: Path, workflows, *, copy=()) -> Path:
    for workflow in workflows:
        conftest.materialize_workflow(workflow, root, rules=RULES, copy=copy)
    return root


PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}

#: A verdict that fails and asks for no retry, which is how the run below is
#: driven into an escalation it can then be resumed from.
FAIL = {
    "status": "failed",
    "blocking_issues": [{
        "severity": "high",
        "issue": "the sample behavior is missing",
        "location": "src/app.py",
        "required_behavior": "the sample behavior exists",
    }],
    "unverified": [],
    "retry_recommended": False,
}


class Runner:
    """A fake agent runner that writes whatever the running stage declares."""

    def __init__(self, target_root: Path, *workflows: dict, verdicts=None):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.outputs = {stage["name"]: list(stage.get("outputs", []))
                        for workflow in workflows
                        for stage in workflow["stages"]}
        self.verdicts = list(verdicts or [PASS])
        self.calls: list[str] = []

    def _write(self, artifact: str) -> None:
        if artifact == conftest.CHANGED_FILES:
            write(self.target_root / "src" / "app.py",
                  APP_AT_HEAD + f"print('call {len(self.calls)}')\n")
            write_json(self.run_dir / artifact,
                       {"modified": ["src/app.py"], "created": [],
                        "deleted": []})
        elif artifact == conftest.TESTER_CHANGED_FILES:
            write_json(self.run_dir / artifact,
                       {"modified": [], "created": [], "deleted": []})
        elif artifact == conftest.TEST_RESULTS:
            write_json(self.run_dir / artifact, {"tests_written": 1})
        elif artifact == conftest.VERIFICATION_RESULT:
            seen = self.calls.count(conftest.VERIFYING_STAGE) - 1
            write_json(self.run_dir / artifact,
                       self.verdicts[min(seen, len(self.verdicts) - 1)])
        else:
            write(self.run_dir / artifact, f"Written for {artifact}.\n")

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None):
        self.calls.append(stage)
        if log_path is not None:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(f"===== stage: {stage} =====\n")
        for artifact in self.outputs.get(stage, []):
            self._write(artifact)
        return AgentResult(ok=True, result_text=f"{stage} done")


@pytest.fixture
def environment(tmp_path):
    """A builder for (target, harness) pairs, one per case.

    A factory rather than a fixture, because the controls below hold two
    configurations side by side — the same story under one definition and
    under the other — and each needs a target of its own.
    """
    made = set()

    def make(workflow: dict, *, name: str, declared: str | None = None
             ) -> tuple[Path, Path]:
        assert name not in made, f"two environments named {name}"
        made.add(name)
        harness = build_harness(tmp_path / f"harness-{name}",
                                (EXECUTED, OTHER))
        target = build_target(tmp_path / f"target-{name}", workflow["name"],
                              declared)
        return target, harness

    return make


def run_dir_of(target: Path, story_id: str = STORY_ID) -> Path:
    return target / ".harness" / "runs" / story_id


def state_of(target: Path) -> dict:
    return json.loads(
        (run_dir_of(target) / "state.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Reading one announcement, three ways
# --------------------------------------------------------------------------


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.rstrip()
    raise AssertionError("nothing was written at all")


def message_of(line: str) -> str:
    """The message half of a rendered event line."""
    matched = EVENT_LINE.match(line)
    assert matched, f"not an event line: {line!r}"
    return matched.group("message")


def events_of(target: Path, story_id: str = STORY_ID) -> list[str]:
    return [line for line in (run_dir_of(target, story_id) / "events.log")
            .read_text(encoding="utf-8").splitlines() if line.strip()]


def history_of(target: Path, story_id: str = STORY_ID) -> list[dict]:
    return json.loads((run_dir_of(target, story_id) / "execution-history.json")
                      .read_text(encoding="utf-8"))


def entries_of_kind(history: list[dict], kind: str) -> list[dict]:
    return [entry for entry in history if entry["event"] == kind]


# ==========================================================================
# 1. A fresh run
# ==========================================================================


def test_a_fresh_runs_first_console_line_names_the_workflow_and_the_story(
    environment, capsys,
):
    """The criterion, read off the run's own output. Its control is the next
    test, where the same story under the other definition announces the other
    name and not this one — so "the name is in the line" cannot be satisfied
    by a line that names every workflow or none."""
    target, harness = environment(EXECUTED, name="fresh",
                                  declared=EXECUTED["name"])

    code = story_coordinator.run_story(STORY_ID, harness, target,
                                       Runner(target, EXECUTED))

    assert code == 0
    announced = message_of(first_line(capsys.readouterr().out))
    assert EXECUTED["name"] in announced
    assert STORY_ID in announced
    assert OTHER["name"] not in announced


def test_the_same_story_under_the_other_definition_announces_that_one(
    environment, capsys,
):
    """The control the assertion above needs."""
    target, harness = environment(OTHER, name="fresh-other",
                                  declared=OTHER["name"])

    code = story_coordinator.run_story(STORY_ID, harness, target,
                                       Runner(target, OTHER))

    assert code == 0
    announced = message_of(first_line(capsys.readouterr().out))
    assert OTHER["name"] in announced
    assert EXECUTED["name"] not in announced


def test_the_first_line_of_events_log_is_the_same_announcement(
    environment, capsys,
):
    """One write, two renderings: the console line and the log line carry the
    same message, so a reader of either is reading the same event."""
    target, harness = environment(EXECUTED, name="events",
                                  declared=EXECUTED["name"])

    story_coordinator.run_story(STORY_ID, harness, target,
                                Runner(target, EXECUTED))

    console = message_of(first_line(capsys.readouterr().out))
    logged = message_of(events_of(target)[0])
    assert logged == console
    assert EXECUTED["name"] in logged
    assert STORY_ID in logged


def test_the_announcement_is_one_workflow_started_entry_in_the_history(
    environment, capsys,
):
    """The third rendering of the same write. The constraint the story sets is
    that nothing new is written: the event keeps its kind, and there is one of
    it rather than a second line beside it."""
    target, harness = environment(EXECUTED, name="history",
                                  declared=EXECUTED["name"])

    story_coordinator.run_story(STORY_ID, harness, target,
                                Runner(target, EXECUTED))

    started = entries_of_kind(history_of(target), STARTED)
    assert len(started) == 1
    assert started[0]["message"] == message_of(
        first_line(capsys.readouterr().out))
    assert started[0]["sequence"] == 1


def test_a_second_announcement_would_be_reported_by_the_same_counter(
    environment,
):
    """The control for the count above: the same reading over the same run
    directory with a second entry of that kind appended reports two, so
    "exactly one" is a fact about the run rather than about a counter that has
    stopped seeing entries."""
    target, harness = environment(EXECUTED, name="history-control",
                                  declared=EXECUTED["name"])
    story_coordinator.run_story(STORY_ID, harness, target,
                                Runner(target, EXECUTED))
    assert len(entries_of_kind(history_of(target), STARTED)) == 1

    story_coordinator.append_event(run_dir_of(target), "and again",
                                   kind=STARTED)

    assert len(entries_of_kind(history_of(target), STARTED)) == 2


# ==========================================================================
# 2. A resumed run
# ==========================================================================


def test_a_resumed_run_says_which_workflow_it_loaded_first(environment,
                                                           capsys):
    """The first thing a resumed run says names the definition the resume
    loaded, and it comes from what the resume recorded in `state.json` rather
    than from the artifact.

    The two are made to disagree deliberately: between the escalation and the
    resume the artifact is amended to name the *other* definition, which is
    also what clears the resume guard. So the recorded name and the artifact's
    name are different strings, and the announcement can only have come from
    one of them.
    """
    target, harness = environment(EXECUTED, name="resume",
                                  declared=EXECUTED["name"])
    escalating = Runner(target, EXECUTED, verdicts=[FAIL])
    code = story_coordinator.run_story(STORY_ID, harness, target, escalating)
    assert code == 2, escalating.calls
    assert state_of(target)["workflow"] == EXECUTED["name"]
    before = len(events_of(target))
    capsys.readouterr()

    write(target / ".harness" / "stories" / f"{STORY_ID}.yaml",
          story_text(OTHER["name"]))
    git(target, "add", "-A")
    git(target, "commit", "-q", "-m", "name the other workflow")

    story_coordinator.run_story(STORY_ID, harness, target,
                                Runner(target, EXECUTED, OTHER))

    announced = message_of(first_line(capsys.readouterr().out))
    assert EXECUTED["name"] in announced
    assert OTHER["name"] not in announced
    assert message_of(events_of(target)[before]) == announced
    assert state_of(target)["workflow"] == EXECUTED["name"]


def test_the_resumed_announcement_keeps_its_kind_and_names_the_stage(
    environment,
):
    """Still one `append_event` under the kind that event already carried, and
    still saying where the run is re-entering — the workflow is added to what
    the message said, not substituted for it."""
    target, harness = environment(EXECUTED, name="resume-kind",
                                  declared=EXECUTED["name"])
    story_coordinator.run_story(STORY_ID, harness, target,
                                Runner(target, EXECUTED, verdicts=[FAIL]))
    write(target / ".harness" / "stories" / f"{STORY_ID}.yaml",
          story_text(EXECUTED["name"]) + "\n# amended to clear the guard\n")
    git(target, "add", "-A")
    git(target, "commit", "-q", "-m", "amend the story")
    stage = state_of(target)["current_stage"]

    story_coordinator.run_story(STORY_ID, harness, target,
                                Runner(target, EXECUTED))

    resumed = entries_of_kind(history_of(target), RESUMED)
    assert len(resumed) == 1
    assert EXECUTED["name"] in resumed[0]["message"]
    assert stage in resumed[0]["message"]
    assert resumed[0]["stage"] == stage


# ==========================================================================
# 3. A run started by pressing Enter at the planning offer
#
# Observed rather than inferred, as the story asks: this is the path that
# would silently diverge, because nothing else here drives `l5-plan` into
# `l5-run`. The whole chain is real — the shipped planning script, the shipped
# run script, the real coordinator — and the only stub is `claude`, which
# writes the story artifact for the planning session and answers every stage
# invocation with nothing. A stage that receives nothing fails, which is
# immaterial: the announcement is made before the first stage is invoked, and
# the announcement is the subject.
# ==========================================================================


#: A stub `claude`. It writes whatever files it was told to write, relative to
#: the directory it inherited, and exits cleanly. It reads none of the prompt
#: it is handed and runs no git command, so a commit that exists afterwards
#: was made by `l5-plan`, and a run directory that exists afterwards was made
#: by `l5-run`.
STUB = '''\
#!/usr/bin/env python3
import json
import os
import pathlib
import sys

for relative, body in json.loads(os.environ.get("L5_STUB_WRITE", "[]")):
    path = pathlib.Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(body, encoding="utf-8")
sys.stdout.write("stub session\\n")
sys.stdout.flush()
sys.exit(0)
'''

#: The planner template `l5-plan` renders, written here rather than copied
#: from what this repository ships. It is an *input* to this module: the
#: subject is what the run says when it starts, and a planning session only
#: has to reach its end for a run to be offered at all. The shipped template
#: is the subject of `tests/test_workflow_selection.py`, which is where it is
#: read.
PLANNER_TEMPLATE = """\
You are the planner for a test that is not about planning.

Write the story artifact you were asked for and end the session.
"""


@pytest.fixture
def offer_harness(tmp_path) -> Path:
    """A harness root whose `scripts/` are the shipped ones, copied.

    Copied rather than linked because each entry point resolves its own
    harness root from its own location, and a symlink would resolve straight
    back to this repository — which ships neither definition built here.
    """
    root = build_harness(tmp_path / "offer-harness", (EXECUTED, OTHER),
                         copy=("orchestration", "scripts", "hooks"))
    (root / "prompts" / "planner.md").write_text(PLANNER_TEMPLATE,
                                                 encoding="utf-8")
    return root


@pytest.fixture
def offer_target(tmp_path) -> Planning:
    """A throwaway target the planning script can commit into and the run
    script can then execute a story in."""
    root = build_target(tmp_path / "offer-target", EXECUTED["name"], None,
                        story_id=PLANNED_ID)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)
    planning = Planning(root, bin_dir, root / ".harness" / "stories",
                        tmp_path / "session.json")
    planning.remote = bare_remote(tmp_path, planning, upstream=True)
    return planning


def plan_and_answer(target: Planning, harness: Path, reply: bytes,
                    **stub) -> tuple[int, str]:
    """Run the real `l5-plan` on a pty and answer its run offer.

    The reply is written to the terminal as soon as the process starts:
    nothing between here and the offer reads stdin — the stub session does
    not — so the bytes wait in the terminal's buffer until the offer reads
    them.
    """
    import pty

    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, str(harness / "scripts" / "l5-plan"),
         "--workflow", EXECUTED["name"], "a story request"],
        cwd=target.root, env=target.env(**stub),
        stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
    )
    os.close(slave)
    os.write(master, reply)
    return drain(process, master)


def announced_on_the_terminal(output: str) -> list[str]:
    """Every event the launched run rendered onto the terminal it was given.

    `l5-plan` writes nothing in this shape — its own lines are prefixed prose
    — so every match is the run's, in the order the run wrote them. A declined
    offer produces none at all, which is the control that makes this reading
    say something rather than merely find text.
    """
    return [matched.group("message").rstrip()
            for line in output.splitlines()
            if (matched := EVENT_ANYWHERE.search(line))]


def planned_artifact() -> str:
    """What the stub session writes: no mandate, because l5-plan confers it."""
    return story_text(EXECUTED["name"], story_id=PLANNED_ID, mandate=False)


def test_a_run_started_from_the_planning_offer_announces_its_workflow(
    offer_target, offer_harness,
):
    """Read from that run's own terminal and its own `events.log`, not from
    the code path it shares with a run started by hand.

    Its control is the next test: the same fixture with the offer declined,
    where no run directory appears and nothing is announced after the planning
    script's last line.
    """
    # The run's own exit status is not read: the stub answers no stage, so the
    # run fails after announcing itself. That is immaterial and deliberate —
    # the announcement is made before the first stage is invoked, which is
    # what makes it observable without a run that could complete.
    _, output = plan_and_answer(
        offer_target, offer_harness, reply=b"\n",
        L5_STUB_WRITE=writes((f".harness/stories/{PLANNED_ID}.yaml",
                              planned_artifact())))

    logged = message_of(events_of(offer_target.root, PLANNED_ID)[0])
    assert EXECUTED["name"] in logged
    assert PLANNED_ID in logged
    assert OTHER["name"] not in logged
    assert announced_on_the_terminal(output)[0] == logged


def test_a_declined_offer_starts_no_run_and_announces_nothing(
    offer_target, offer_harness,
):
    """The control the observation above needs."""
    status, output = plan_and_answer(
        offer_target, offer_harness, reply=b"n\n",
        L5_STUB_WRITE=writes((f".harness/stories/{PLANNED_ID}.yaml",
                              planned_artifact())))

    assert status == 0, output
    assert not run_dir_of(offer_target.root, PLANNED_ID).exists()
    assert announced_on_the_terminal(output) == []
