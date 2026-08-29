"""story-082 validation: a run commits the history it writes.

Two claims, one mechanism. Every path a run can end on must commit the
cross-run records that path wrote, and the execution log must be able to
describe every point a run can reach.

What this module validates:

  * each of the four terminal paths — a completion, an escalation, a budget
    stop and a capacity pause — leaves nothing it appended to the history
    directory outside the commit that path left, and leaves HEAD standing on a
    commit that path itself made;
  * each of those four assertions catches a planted commit-then-append
    inversion, so none of them is passing because no record was written;
  * the two kinds the story adds: a pause records that it paused, a resume
    records that it resumed, and a run that paused, resumed and completed
    leaves those three records in that order;
  * routing the two new kinds is confined to the declaration: the projection
    spells no event kind, and every routed kind projects a record of the same
    shape, so this story added kinds without reshaping the records the three
    kinds story-081 declared;
  * the shipped declaration lists both new kinds in the log's two enums and
    says what the log now holds.

The story-065 amend cases — a clean-tree completion standing on its own
escalation commit, run with a history directory set — are the subject of
`tests/test_completion_survives_merge.py`, where the rest of that behaviour
already lives, rather than being restated here.

The workflow these runs execute is built by `conftest.build_workflow` and the
rule set is this module's own: what a terminal path does with the record it
writes is the subject, and the stage list is an input to it. The assertions
whose subject genuinely is what this repository ships — the schema's enums, the
log's description, the source of the projection — read the shipped files and
say so.

Every absence asserted here sits beside a demonstration that the same reader
reports the violation it exists to catch. Nothing invokes a model: every run
goes through a fake agent runner. Nothing sleeps: the one run that waits has
its wait injected. Nothing resolves a baseline out of this repository's commit
graph — the histories read below are built here, by runs this module drives.
"""
import json
import subprocess
import time
from pathlib import Path

import pytest

import conftest

import harness_config
import schema_validator
import story_coordinator
from agent_runner import AgentResult, CapacityStop

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
COORDINATOR_PATH = REPO_ROOT / "orchestration" / "story_coordinator.py"


# --------------------------------------------------------------------------
# The declaration, read from the schema, so nothing below writes a log name,
# a field name or an event kind of its own
# --------------------------------------------------------------------------

SCHEMA = schema_validator.load_schema(story_coordinator.CROSS_RUN_HISTORY_SCHEMA)

DECLARATIONS = {name: log["items"] for name, log in SCHEMA["properties"].items()}

#: The declared property that selects into a log rather than being projected.
EVENT = story_coordinator.HISTORY_EVENT_PROPERTY

#: The log that records how far an execution got, told apart from the other by
#: what its own declaration carries rather than by its filename.
OUTCOME_LOG = next(name for name, shape in DECLARATIONS.items()
                   if "status" in shape["properties"])

#: Where a run keeps its cross-run records, resolved through the harness rather
#: than written here. The target built below configures no `history_dir`, so
#: this is what its runs resolve.
HISTORY_DIR = harness_config.DEFAULT_HISTORY_DIR


def kinds_routed_to(log: str) -> list[str]:
    return DECLARATIONS[log]["properties"][EVENT]["enum"]


def projected(log: str) -> set[str]:
    return set(DECLARATIONS[log]["properties"]) - {EVENT}


def declared_status(kind: str) -> str:
    """The status the outcome log's own declaration gives an event kind.

    Derived from the declaration rather than copied from the projection: the
    status enum holds exactly one value a kind either is or ends with, and that
    it is exactly one is asserted here rather than assumed. So a kind added to
    the event enum with no status to project into fails loudly, here, instead
    of quietly making the comparisons below compare nothing.
    """
    statuses = DECLARATIONS[OUTCOME_LOG]["properties"]["status"]["enum"]
    matched = [status for status in statuses
               if kind == status or kind.endswith(f"-{status}")]
    assert len(matched) == 1, (kind, matched)
    return matched[0]


# --------------------------------------------------------------------------
# The workflow, the rules and the target these runs execute against
# --------------------------------------------------------------------------

#: What one invocation of the first stage may spend, and what the runner
#: reports when a run is being driven to its budget stop. A ceiling no harness
#: would choose, so a run stopped at it is obeying this declaration.
EXECUTION_CEILING = 7.5

#: How long a run may wait in place for capacity, and a reset time inside it.
#: The wait is injected, so nothing here sleeps; the bound only has to admit
#: the offset for the in-place resume to be reached.
PAUSE_BOUND = 611
INSIDE_THE_BOUND = PAUSE_BOUND - 11

WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"},
        max_execution_cost_usd=EXECUTION_CEILING),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        retry_routing={"implementation-defect": {
            "stage": conftest.StageRef(0),
            "when": "the behaviour the story asked for is missing"}}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="history-commit-workflow",
)

WRITING, VERIFYING = [stage["name"] for stage in WORKFLOW["stages"]]

RULES = {
    "max_retries": 2,
    "require_verifier_pass": True,
    "blocked_paths": [".git/", ".harness/runs/", "rules/"],
}

STORY_ID = "story-001"
STORY_TITLE = "Sample story for history commit tests"
DEFAULT_BRANCH = "main"

STORY = f"""\
story:
  id: {STORY_ID}
  title: {STORY_TITLE}
  description: |
    A stand-in story used to drive each terminal path deterministically
    against a fake runner.

tasks:
  - do the sample work

acceptance_criteria:
  - the sample behavior exists

verification_requirements:
  - confirm the sample behavior

scope:
  modify:
    - src/
  do_not_modify:
    - rules/

constraints:
  - preserve existing behavior

mandate:
  source:
    kind: human
  conferred_at: 2026-08-28 09:00:00
  conferred_by: A Developer <developer@example.com>
  recorded_by: l5-plan
"""

CONFIG = f"""\
workflow: {WORKFLOW['name']}
branch_prefix: story/
permission_mode: acceptEdits
max_pause_wait_seconds: {PAUSE_BOUND}
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: echo tests-ok
tests_dir: tests/
"""

#: The run directory and the log directory are ignored, as they are in every
#: real target: a repository that tracked its run directory would be dirty at
#: every terminal path for reasons that have nothing to do with the record
#: under test.
GITIGNORE = ".harness/runs/\n.harness/logs/\n"

APP_AT_HEAD = "print('hello')\n"

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}

FAIL = {
    "status": "failed",
    "blocking_issues": [{
        "severity": "high",
        "issue": "the sample behavior is missing",
        "location": "src/app.py",
        "required_behavior": "the sample behavior exists",
    }],
    "unverified": [],
    # No retry asked for, so the run escalates on the verdict rather than
    # travelling to the retry ceiling first.
    "retry_recommended": False,
}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


def build_target(root: Path) -> Path:
    """A target configured to run the built workflow, on a trunk."""
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml", CONFIG)
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / ".gitignore", GITIGNORE)
    write(root / "src" / "app.py", APP_AT_HEAD)
    write(root / "tests" / "test_existing.py",
          "def test_nothing():\n    assert True\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root,
                   check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    subprocess.run(["git", "branch", "-M", DEFAULT_BRANCH], cwd=root, check=True)
    return root


class Runner:
    """A fake agent runner, told what each invocation should do.

    `verdict` is what the verifying stage writes. `capacity` is a stop the
    invocation at that ordinal reports instead of doing its work, and `cost` is
    what an invocation reports having spent — the two seams the capacity pause
    and the budget stop are driven through, both of them fields of the result
    rather than words in it.
    """

    def __init__(self, target_root: Path, *, verdict: dict = PASS,
                 capacity: dict | None = None, cost: float | None = None):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.verdict = verdict
        self.capacity = dict(capacity or {})
        self.cost = cost
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None, **extra):
        ordinal = len(self.calls)
        self.calls.append(stage)

        if ordinal in self.capacity:
            # A stage stopped part-way through still left something in the
            # working tree, which is what the pause commit exists to protect.
            write(self.target_root / "src" / "app.py",
                  APP_AT_HEAD + "print('half-written when capacity ran out')\n")
            return AgentResult(ok=False, result_text=f"{stage} stopped",
                               capacity=self.capacity[ordinal])

        if stage == WRITING:
            write(self.target_root / "src" / "app.py",
                  APP_AT_HEAD + f"print('invocation {ordinal + 1}')\n")
            write_json(self.run_dir / conftest.CHANGED_FILES,
                       {"modified": ["src/app.py"], "created": [], "deleted": []})
            write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY,
                  f"Implemented on invocation {ordinal + 1}.\n")
        elif stage == VERIFYING:
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, self.verdict)
        return AgentResult(ok=True, result_text=f"{stage} done",
                           cost_usd=self.cost)


@pytest.fixture
def environment(tmp_path):
    """A builder for (target, harness) pairs, one per run a test drives."""
    harness = conftest.materialize_workflow(
        WORKFLOW, tmp_path / "harness", rules=RULES)

    def make(name: str = "target") -> tuple[Path, Path]:
        return build_target(tmp_path / name), harness
    return make


def run_dir_of(target: Path) -> Path:
    return target / ".harness" / "runs" / STORY_ID


def history_dir_of(target: Path) -> Path:
    return target / HISTORY_DIR


def subject_of(target: Path, revision: str = "HEAD") -> str:
    return git(target, "log", "-1", "--format=%s", revision).strip()


def parse_records(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def working_records(target: Path, log: str = OUTCOME_LOG) -> list[dict]:
    """The records the working tree's copy of a log holds."""
    path = history_dir_of(target) / log
    return parse_records(path.read_text(encoding="utf-8")) if path.is_file() else []


def committed_text(target: Path, log: str) -> str:
    """A log as the commit at HEAD carries it, or "" when HEAD carries none."""
    relative = f"{HISTORY_DIR}/{log}"
    result = subprocess.run(
        ["git", "-C", str(target), "show", f"HEAD:{relative}"],
        capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def committed_records(target: Path, log: str = OUTCOME_LOG) -> list[dict]:
    return parse_records(committed_text(target, log))


def records_outside_the_commit(target: Path) -> dict[str, list[dict]]:
    """Which records the history directory holds that HEAD's commit does not.

    The one reader every "nothing was left outside the commit" assertion below
    goes through, and the one the controls drive, so a control demonstrates
    this reader reporting a violation rather than some other comparison.

    A log HEAD does not carry at all reads as an empty committed text, so an
    untracked log reports every record it holds; a log whose committed text is
    not a prefix of the working one was rewritten rather than appended to, and
    reports whole. Both are failures of the same claim: what is in the
    directory is in the commit.

    The log names come from the declaration, so a log added there is a log this
    reader looks at without being told to.
    """
    outside: dict[str, list[dict]] = {}
    for log in DECLARATIONS:
        path = history_dir_of(target) / log
        if not path.is_file():
            continue
        working = path.read_text(encoding="utf-8")
        committed = committed_text(target, log)
        extra = parse_records(
            working[len(committed):] if working.startswith(committed) else working)
        if extra:
            outside[log] = extra
    return outside


def routed_kinds(run_dir: Path, log: str = OUTCOME_LOG) -> list[str]:
    """The kinds this run emitted that the log's declaration routes there.

    In the order the run emitted them, read off the run's own execution
    history. Every kind named below is derived through this rather than
    written: which kind a completion, an escalation, a budget stop, a pause or
    a resume emits is the coordinator's fact, and a test that spelled it would
    be asserting against its own copy of it.
    """
    kinds = kinds_routed_to(log)
    return [entry[EVENT] for entry in story_coordinator.load_history(run_dir)
            if entry.get(EVENT) in kinds]


# --------------------------------------------------------------------------
# The four terminal paths, and the inversion planted in each
#
# Each case is a driver — what makes a run take that path — and a mutant
# factory that plants the defect the story exists to remove: the path commits
# and *then* appends its record. The mutants are anchored on the shipped source
# and `conftest.load_mutant` requires each anchor to occur, so an anchor that
# has moved fails as itself rather than as a mutant that changed nothing.
# --------------------------------------------------------------------------


def drive_completion(target: Path, harness: Path, coordinator) -> int:
    runner = Runner(target)
    return coordinator.run_story(STORY_ID, harness, target, runner)


def drive_escalation(target: Path, harness: Path, coordinator) -> int:
    runner = Runner(target, verdict=FAIL)
    return coordinator.run_story(STORY_ID, harness, target, runner)


def drive_budget_stop(target: Path, harness: Path, coordinator) -> int:
    runner = Runner(target, cost=EXECUTION_CEILING)
    return coordinator.run_story(STORY_ID, harness, target, runner)


def drive_capacity_pause(target: Path, harness: Path, coordinator) -> int:
    """A capacity stop carrying no reset time, which exits rather than waits."""
    runner = Runner(target, capacity={0: CapacityStop(signal="a capacity stop")})
    return coordinator.run_story(STORY_ID, harness, target, runner,
                                 sleep=lambda seconds: None)


def invert_completion(tmp_path):
    """The completion path putting its record after its commit.

    Exactly the ordering the harness had before this story: the three-way
    choice taken, and only then the completion appended — so the run exits with
    its own completion record untracked.
    """
    return conftest.load_mutant(
        COORDINATOR_PATH,
        [
            (
                '    append_event(\n'
                '        run_dir,\n'
                '        f"story completed on branch {state.branch}",\n'
                '        kind="story-completed",\n'
                '    )\n'
                '    _git(target_root, "add", "-A")\n',
                '',
            ),
            # The anchor ends at the last commit rather than at `return 0`:
            # story-092 put the completion sweep between the two, and what
            # this inversion is about is the record landing after the commit
            # rather than what else happens on the way out of the function.
            (
                '    else:\n'
                '        _git(target_root, "commit", "--allow-empty", "-m",\n'
                '             completion_commit_message(state, title))\n',
                '    else:\n'
                '        _git(target_root, "commit", "--allow-empty", "-m",\n'
                '             completion_commit_message(state, title))\n'
                '    append_event(\n'
                '        run_dir,\n'
                '        f"story completed on branch {state.branch}",\n'
                '        kind="story-completed",\n'
                '    )\n',
            ),
        ],
        name="coordinator_completing_before_it_records", tmp_path=tmp_path)


def invert_escalation(tmp_path):
    """The escalation path putting its record after the commit it ends on.

    The escalation's own commit of the tree is made by the wrapper, after the
    escalation has finished writing, so the record is moved past *that* — which
    is what an escalation appending after its commit would look like.
    """
    return conftest.load_mutant(
        COORDINATOR_PATH,
        [
            (
                '    append_event(\n'
                '        run_dir,\n'
                '        f"escalated: {reason}",\n'
                '        kind="escalated",\n'
                '        stage=state.current_stage or None,\n'
                '        **event_fields,\n'
                '    )\n'
                '    state.escalation_commit = commit_escalated_work(\n',
                '    state.escalation_commit = commit_escalated_work(\n',
            ),
            (
                '        if state.escalation_commit:\n'
                '            commit_escalated_tree(target_root, state, reason)\n'
                '        return code\n',
                '        if state.escalation_commit:\n'
                '            commit_escalated_tree(target_root, state, reason)\n'
                '        append_event(\n'
                '            run_dir,\n'
                '            f"escalated: {reason}",\n'
                '            kind="escalated",\n'
                '            stage=state.current_stage or None,\n'
                '            **event_fields,\n'
                '        )\n'
                '        return code\n',
            ),
        ],
        name="coordinator_escalating_before_it_records", tmp_path=tmp_path)


def invert_budget_stop(tmp_path):
    """The budget stop recorded after the escalation it goes through commits.

    Anchored at the execution-allowance stop, which is the one the runs below
    reach; the reason line above the ordering is what makes the anchor that
    site rather than the run-ceiling one, whose statements are identical.
    """
    return conftest.load_mutant(
        COORDINATOR_PATH,
        [(
            '                f"stopped on its budget rather than failing at its work"\n'
            '            )\n'
            '            state.stopped_on_cost = True\n'
            '            _budget_stopped(run_dir, name, reason)\n'
            '            return _escalate(\n'
            '                run_dir,\n'
            '                state,\n'
            '                reason,\n'
            '                target_root=target_root,\n'
            '                harness_root=harness_root,\n'
            '                duration_seconds=elapsed(),\n'
            '            )\n',
            '                f"stopped on its budget rather than failing at its work"\n'
            '            )\n'
            '            state.stopped_on_cost = True\n'
            '            stopped = _escalate(\n'
            '                run_dir,\n'
            '                state,\n'
            '                reason,\n'
            '                target_root=target_root,\n'
            '                harness_root=harness_root,\n'
            '                duration_seconds=elapsed(),\n'
            '            )\n'
            '            _budget_stopped(run_dir, name, reason)\n'
            '            return stopped\n',
        )],
        name="coordinator_stopping_before_it_records", tmp_path=tmp_path)


def invert_capacity_pause(tmp_path):
    """The pause path putting its record after both of the commits it makes."""
    return conftest.load_mutant(
        COORDINATOR_PATH,
        [(
            '    _capacity_paused(run_dir, state.current_stage, capacity, reason)\n'
            '    message = pause_commit_message(state, reason)\n'
            '    committed = commit_escalated_work(\n'
            '        target_root, state, reason, run_dir=run_dir, message=message\n'
            '    )\n'
            '    if committed:\n'
            '        commit_escalated_tree(target_root, state, reason, message=message)\n',
            '    message = pause_commit_message(state, reason)\n'
            '    committed = commit_escalated_work(\n'
            '        target_root, state, reason, run_dir=run_dir, message=message\n'
            '    )\n'
            '    if committed:\n'
            '        commit_escalated_tree(target_root, state, reason, message=message)\n'
            '    _capacity_paused(run_dir, state.current_stage, capacity, reason)\n',
        )],
        name="coordinator_pausing_before_it_records", tmp_path=tmp_path)


def head_is_this_completion(target: Path) -> bool:
    return subject_of(target) == story_coordinator.completion_commit_subject(
        STORY_ID, STORY_TITLE)


def head_is_this_escalation(target: Path) -> bool:
    return story_coordinator.escalated_story(subject_of(target)) == STORY_ID


def head_is_this_pause(target: Path) -> bool:
    return story_coordinator.paused_story(subject_of(target)) == STORY_ID


#: One entry per terminal path a run can end on: what drives a run down it,
#: what its exit code is, how the commit it ends on identifies itself, and the
#: commit-then-append inversion planted in it. Every kind is derived from the
#: run afterwards rather than named here.
TERMINAL_PATHS = {
    "completion": (drive_completion, 0, head_is_this_completion,
                   invert_completion),
    "escalation": (drive_escalation, 2, head_is_this_escalation,
                   invert_escalation),
    "budget stop": (drive_budget_stop, 2, head_is_this_escalation,
                    invert_budget_stop),
    "capacity pause": (drive_capacity_pause, story_coordinator.PAUSE_EXIT_CODE,
                       head_is_this_pause, invert_capacity_pause),
}

PATH_IDS = list(TERMINAL_PATHS)


# --------------------------------------------------------------------------
# The claim, one terminal path at a time
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", PATH_IDS)
def test_every_terminal_path_commits_the_records_it_wrote(path, environment):
    """The story's whole subject, asked of each path in turn.

    The positive assertions come first and they are what make the absence at
    the end mean anything: the path really did write a record, so the
    directory has something in it, and HEAD really is a commit this path made,
    identified through the harness's own readers of a commit subject rather
    than through a message written here. The absence — nothing in the history
    directory that the commit does not carry — has the test below as its
    control.
    """
    drive, expected_code, head_is_the_paths_own, _ = TERMINAL_PATHS[path]
    target, harness = environment(name=path.replace(" ", "-"))

    assert drive(target, harness, story_coordinator) == expected_code

    emitted = routed_kinds(run_dir_of(target))
    assert emitted, "the path emitted nothing the declaration routes to a log"
    assert head_is_the_paths_own(target), \
        "HEAD is not a commit this path left, so there is no commit to be inside"

    # Every kind the path emitted is a record the commit at HEAD carries, and
    # the directory holds nothing the commit does not.
    committed = committed_records(target)
    assert [record["status"] for record in committed] == \
        [declared_status(kind) for kind in emitted]
    assert committed == working_records(target)
    assert records_outside_the_commit(target) == {}


@pytest.mark.parametrize("path", PATH_IDS)
def test_each_of_those_assertions_catches_a_planted_inversion(path, environment,
                                                              tmp_path):
    """The control: the same path, appending after it commits, is reported.

    Without this, a green result above would be satisfied just as happily by a
    run that wrote no record at all. Here the record *is* written and the
    commit is made before it, and the same reader the assertion above uses must
    name it — as the very record the path's terminal kind produces.
    """
    drive, expected_code, _, invert = TERMINAL_PATHS[path]
    target, harness = environment(name=f"inverted-{path.replace(' ', '-')}")
    inverted = invert(tmp_path)

    assert drive(target, harness, inverted) == expected_code

    written = working_records(target)
    assert written, "the inverted path wrote no record at all"
    outside = records_outside_the_commit(target)
    assert OUTCOME_LOG in outside, \
        "the inversion left every record inside the commit, so the reader " \
        "above cannot be shown to see the defect it is written against"
    # The record the path appended last — the one its terminal act wrote — is
    # exactly what the commit does not carry, and everything appended before
    # the commit still is.
    assert outside[OUTCOME_LOG] == written[-1:]
    assert committed_records(target) == written[:-1]
    assert written[-1]["status"] == \
        declared_status(routed_kinds(run_dir_of(target))[-1])


# --------------------------------------------------------------------------
# The two kinds this story adds
# --------------------------------------------------------------------------


@pytest.fixture
def paused(environment):
    """A run that stopped for capacity and exited, and the kind it recorded."""
    target, harness = environment(name="paused")
    assert drive_capacity_pause(target, harness, story_coordinator) == \
        story_coordinator.PAUSE_EXIT_CODE
    return target, harness


def test_a_capacity_pause_records_one_line_saying_it_paused(paused):
    """The point a paused run reached, in the log that outlives the run.

    Before this story a pause reached no log at all, so a run that paused for
    hours and a run that never started were indistinguishable in the record.
    The status is compared against the kind the run emitted and against the
    run's own state, so the record and the run have to agree.
    """
    target, _ = paused
    run_dir = run_dir_of(target)
    written = working_records(target)
    assert len(written) == 1

    kind = routed_kinds(run_dir)[-1]
    assert written[0]["status"] == declared_status(kind)
    assert written[0]["story_id"] == run_dir.name
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "paused"
    # And it is inside the pause's own commit, which the case above asserts of
    # every path; repeated here because "one record, and it is committed" is
    # the whole of what this criterion asks.
    assert committed_records(target) == written
    assert head_is_this_pause(target)


def test_a_resume_records_one_line_saying_it_resumed(paused):
    """A fresh process picking up the paused run, which continues it.

    The control is the log as the pause left it: one record, of another kind.
    So the second record is the resume being recorded rather than the reader
    counting something that was already there.
    """
    target, harness = paused
    before = working_records(target)
    assert len(before) == 1

    assert story_coordinator.run_story(
        STORY_ID, harness, target, Runner(target)) == 0

    after = working_records(target)
    resumed = after[len(before)]
    kinds = routed_kinds(run_dir_of(target))
    assert resumed["status"] == declared_status(kinds[len(before)])
    assert resumed["status"] != before[0]["status"]
    assert resumed["story_id"] == run_dir_of(target).name


def test_a_paused_resumed_and_completed_run_leaves_three_records_in_order(
        environment):
    """The property the two new kinds exist for, in one run.

    A capacity stop whose reset time is inside the configured bound is waited
    out in place and the same stage re-entered, so one run reaches all three
    points. The wait is injected and recorded, so nothing sleeps and the run
    demonstrably went through the pause rather than around it.

    The control is a run of the same shape that was never interrupted: it
    leaves one record, so three records here is the interruption being visible
    in the history rather than a count of what any completed run leaves.
    """
    target, harness = environment(name="paused-then-completed")
    runner = Runner(target, capacity={
        0: CapacityStop(signal="a capacity stop",
                        reset_at=time.time() + INSIDE_THE_BOUND)})
    slept: list[float] = []
    assert story_coordinator.run_story(
        STORY_ID, harness, target, runner, sleep=slept.append) == 0
    assert slept, "the run did not wait in place, so it never resumed"

    statuses = [record["status"] for record in working_records(target)]
    assert statuses == [declared_status(kind)
                        for kind in routed_kinds(run_dir_of(target))]
    assert len(statuses) == 3
    assert len(set(statuses)) == 3, "a resumed run must be told from its parts"
    assert records_outside_the_commit(target) == {}

    control, control_harness = environment(name="never-interrupted")
    assert drive_completion(control, control_harness, story_coordinator) == 0
    uninterrupted = [record["status"] for record in working_records(control)]
    assert uninterrupted == [statuses[-1]], \
        "an uninterrupted run must be distinguishable from a resumed one"


# --------------------------------------------------------------------------
# The routing of the two new kinds is confined to the declaration
# --------------------------------------------------------------------------


def test_the_schema_declares_both_new_kinds_in_both_of_the_logs_enums(
        paused, environment):
    """The deployment fact, read out of the shipped schema.

    The two kinds are not written here: they are taken from the runs above —
    the kind a pause emits and the kind a resume emits — and looked up in the
    declaration this repository ships. So this fails if the enums lose either
    entry, and it cannot pass by naming a kind the harness does not emit.
    """
    target, harness = paused
    pause_kind = routed_kinds(run_dir_of(target))[-1]
    assert story_coordinator.run_story(
        STORY_ID, harness, target, Runner(target)) == 0
    resume_kind = routed_kinds(run_dir_of(target))[1]
    assert pause_kind != resume_kind

    for kind in (pause_kind, resume_kind):
        assert kind in kinds_routed_to(OUTCOME_LOG), kind
        assert kind in DECLARATIONS[OUTCOME_LOG]["properties"]["status"]["enum"], \
            kind


def test_the_logs_description_says_what_it_now_holds():
    """The log records one line per point an execution reached.

    A shipped artifact, and the subject: the description said one record per
    execution that reached an *outcome*, which a pause and a resume are not,
    and the runs above show it now holds both.
    """
    assert "point an execution reached" in \
        SCHEMA["properties"][OUTCOME_LOG]["description"]


def test_every_routed_kind_projects_a_record_of_the_same_shape():
    """Adding kinds did not reshape the records the existing kinds carry.

    `history_record` is driven directly against one constructed entry per
    routed kind — the same entry, differing only in its kind — so what is
    compared is the projection rather than a run. Every kind produces the same
    field set and a status that is its own kind, which is the whole of what the
    two new kinds needed and the whole of what the three existing ones still
    get.
    """
    shapes = {}
    for log in DECLARATIONS:
        for kind in kinds_routed_to(log):
            entry = {"sequence": 1, "timestamp": "2026-08-28 12:00:00",
                     EVENT: kind, "message": "a routed entry"}
            record = story_coordinator.history_record(
                entry, [], STORY_ID, DECLARATIONS[log])
            assert record is not None, (log, kind)
            shapes.setdefault(log, []).append((kind, frozenset(record)))

    for log, projections in shapes.items():
        assert len({fields for _, fields in projections}) == 1, log
        assert set(projections[0][1]) <= projected(log), log

    # The status a record carries is the one the declaration gives the kind
    # that produced it, for every kind the outcome log declares — the
    # derivation the two new kinds needed no code under `orchestration/` to
    # obtain, which is the claim story-081 made and this story first exercises.
    for kind in kinds_routed_to(OUTCOME_LOG):
        entry = {"sequence": 1, "timestamp": "2026-08-28 12:00:00",
                 EVENT: kind, "message": "a routed entry"}
        record = story_coordinator.history_record(
            entry, [], STORY_ID, DECLARATIONS[OUTCOME_LOG])
        assert record["status"] == declared_status(kind)


def kinds_named_in(source: str) -> list[str]:
    """Which declared event kinds a piece of source spells.

    The projection's claim is that what reaches a log is decided by the
    declaration and by nothing else, so a projection naming a kind is a second
    place where "which kinds matter" is settled — and routing a new kind would
    then be an edit under `orchestration/` rather than an edit to the schema.
    """
    declared = {kind for log in DECLARATIONS for kind in kinds_routed_to(log)}
    return sorted(kind for kind in declared if kind in source)


PROJECTION_FUNCTIONS = ("history_record", "_append_history_records")


@pytest.mark.parametrize("name", PROJECTION_FUNCTIONS)
def test_the_projection_spells_no_event_kind(name):
    """The absence, with the same reader over planted source as its control."""
    source = conftest.function_source(
        COORDINATOR_PATH.read_text(encoding="utf-8"), name)
    assert kinds_named_in(source) == []

    # The control: a kind spliced into that same source is reported, so the
    # emptiness above is the reader looking rather than the reader being unable
    # to see. The kind is taken from the declaration, so it is one that would
    # matter.
    planted = kinds_routed_to(OUTCOME_LOG)[0]
    assert kinds_named_in(source + f'\n# routed here: "{planted}"\n') == [planted]
