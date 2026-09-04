"""Independent validation for story-102: a run stands on its branch before it
writes anything.

The invariant: nothing a run writes — tracked or gitignored — happens until the
run stands on its story branch, and every refusal that can be made without
standing there stays above the checkout. The checkout is the run's first act,
and a checkout git will not make refuses the way its neighbouring pre-flights
refuse rather than raising out of a run that has already begun.

The configuration the defect hid in is built here rather than described: a
target repository whose run directory is gitignored and whose cross-run history
is tracked, escalated on its story branch and then resumed while standing on
another branch. The history file is committed on the story branch and absent
from the branch stood on, so the writes the run used to make above the checkout
put a file in the working tree that git then refused to overwrite — and the run
died after archiving an entry, zeroing its counters and appending records.

Every absence asserted here carries a demonstration that the same check reports
the violation it exists to catch:

  * "the checkout is written above every act" is a position check over each act
    the story names, and its control is the same check over a body with the
    checkout line moved to the end, where it must name *every* act — so no
    single anchor is carrying the assertion;
  * "the checkout is written below every refusal that needs no branch" is the
    same shape, controlled by the body with the checkout line moved to the top,
    where it must name every refusal;
  * "a refused checkout writes nothing" is paired, in both refusal fixtures,
    with the identical run whose checkout is not refused, which does write all
    of it;
  * "`_checkout_story_branch` raises nothing on a refused checkout" is an AST
    scan whose control is the same scan over the function with a raise put
    back;
  * "`run_story` is the helper's only caller" is a scan whose control is a
    source with a second caller written into it, which the same scan reports;
  * "the guard resolves the story branch rather than HEAD" is asserted from a
    resume standing off the branch, and its control is a mutant of today's
    coordinator with the comparison pointed back at HEAD, which establishes
    nothing in the identical situation.

The workflow these runs execute is built by the fixture in `tests/conftest.py`
rather than resolved out of what this repository deploys: the subject is where
the checkout sits among the things a run does, and the stage list is an input
to that. Nothing here invokes a model, and no baseline is resolved out of this
repository's own commit graph.
"""
import ast
import json
import subprocess
from pathlib import Path

import pytest

import conftest
import harness_config
import story_coordinator
from agent_runner import AgentResult

COORDINATOR_PATH = Path(story_coordinator.__file__)

#: The workflow these runs execute. Built rather than read: any definition that
#: escalates on a failed verdict drives the ordering identically, and deriving
#: the stage names from what this repository deploys would make a deployment
#: fact into something these assertions move on.
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
        retry_routing={"implementation-defect": {
            "stage": conftest.StageRef(0),
            "when": "the behaviour the story asked for is missing"}}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="stands-on-its-branch-workflow",
)
STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, DOCUMENTING, VERIFYING = STAGE_NAMES

STORY_ID = "story-001"
DEFAULT_BRANCH = "main"
BRANCH_PREFIX = "story/"
STORY_BRANCH = f"{BRANCH_PREFIX}{STORY_ID}"

#: A prefix git will not make a branch out of: `..` is not allowed in a
#: reference name. It drives the one refusal a *fresh* run can be given, where
#: nothing exists yet and so "nothing was written" is the whole assertion.
UNUSABLE_PREFIX = "story/..bad/"

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}

FAIL = {
    "status": "failed",
    "blocking_issues": [{
        "severity": "high",
        "issue": "the sample behavior was not implemented",
        "location": "src/app.py",
        "required_behavior": "the sample behavior exists",
    }],
    "unverified": [],
    "retry_recommended": False,
}

STORY = f"""\
story:
  id: {STORY_ID}
  title: Sample story for coordinator tests
  description: |
    A stand-in story used to exercise the workflow deterministically.

tasks:
  - do the sample work

acceptance_criteria:
  - the sample behavior exists
  - existing behavior is preserved

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
STORY += conftest.MANDATE_BLOCK

CONFIG = """\
workflow: {workflow}
branch_prefix: "{prefix}"
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: echo tests-ok
tests_dir: tests/
"""

#: The shape this story is about: the run directory is execution state and is
#: not versioned, and the cross-run history *is* — which is what makes the
#: history file differ between the story branch and the branch a developer
#: types `l5-run` from, and what git refused the checkout over.
GITIGNORE = ".harness/runs/\n"

APP_AT_HEAD = "print('hello')\n"


# --------------------------------------------------------------------------
# The target, the harness root and the fake runner
# --------------------------------------------------------------------------


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=check)


def build_target(root: Path, *, prefix: str = BRANCH_PREFIX) -> Path:
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml",
          CONFIG.format(workflow=WORKFLOW["name"], prefix=prefix))
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "src" / "app.py", APP_AT_HEAD)
    write(root / ".gitignore", GITIGNORE)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root,
                   check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    subprocess.run(["git", "branch", "-M", DEFAULT_BRANCH], cwd=root, check=True)
    return root


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return build_target(tmp_path / "branch-target")


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A harness root carrying the built definition, as a git repository.

    The guard reads a recorded harness revision back, so a root git cannot
    resolve would clear the guard for a reason none of these cases is about.
    """
    root = conftest.materialize_workflow(WORKFLOW, tmp_path / "branch-harness")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root,
                   check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "the harness"], cwd=root,
                   check=True)
    return root


def edits_the_module(root: Path, attempt: int) -> dict:
    write(root / "src" / "app.py", APP_AT_HEAD + f"print('attempt {attempt}')\n")
    return {"modified": ["src/app.py"], "created": [], "deleted": []}


class Runner:
    """A fake agent runner: each stage writes the artifacts it declares."""

    def __init__(self, target_root: Path, edit=None,
                 verdicts: list | None = None):
        self.target_root = target_root
        self.run_dir = run_dir_of(target_root)
        self.edit = edit
        self.verdicts = verdicts or [PASS]
        self.calls: list[str] = []

    def _nth(self, sequence: list, index: int):
        return sequence[min(index, len(sequence) - 1)]

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None):
        self.calls.append(stage)
        attempt = max(1, self.calls.count(WRITING))
        if stage == WRITING:
            changed = (self.edit(self.target_root, attempt) if self.edit
                       else {"modified": [], "created": [], "deleted": []})
            write_json(self.run_dir / conftest.CHANGED_FILES, changed)
            write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY,
                  f"Implemented on attempt {attempt}.\n")
        elif stage == VALIDATING:
            write_json(self.run_dir / conftest.TEST_RESULTS, {"tests_written": 1})
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES,
                       {"modified": [], "created": [], "deleted": []})
        elif stage == DOCUMENTING:
            write(self.run_dir / conftest.DOCUMENTATION_REPORT, "Nothing.\n")
            write_json(self.run_dir / conftest.DOCUMENTER_CHANGED_FILES,
                       {"modified": [], "created": [], "deleted": []})
        elif stage == VERIFYING:
            verdict = self._nth(self.verdicts, self.calls.count(stage) - 1)
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, verdict)
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path) -> Path:
    return target_root / ".harness" / "runs" / STORY_ID


def state_of(target_root: Path) -> dict:
    return json.loads((run_dir_of(target_root) / "state.json").read_text())


def write_state(target_root: Path, **changes) -> None:
    path = run_dir_of(target_root) / "state.json"
    state = json.loads(path.read_text())
    state.update(changes)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def run(target_root: Path, harness: Path, edit=None,
        verdicts: list | None = None,
        runner: Runner | None = None) -> tuple[int, Runner]:
    runner = runner or Runner(target_root, edit, verdicts)
    code = story_coordinator.run_story(
        STORY_ID, harness, target_root, runner)
    return code, runner


def escalate(target_root: Path, harness: Path) -> Runner:
    """Drive one run to an escalation, on the story branch it cuts."""
    code, runner = run(target_root, harness, edits_the_module, [FAIL])
    assert code == 2, "the shape was meant to escalate"
    assert state_of(target_root)["status"] == "escalated"
    assert current_branch(target_root) == STORY_BRANCH
    return runner


def current_branch(root: Path) -> str:
    return git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def messages(target_root: Path) -> list[str]:
    """What the run said, with the repository it said it in written out.

    Two targets built from one recipe sit under different temporary
    directories, so a message naming an absolute path would differ between them
    for a reason no assertion here is about. Every other difference survives.
    """
    log = (run_dir_of(target_root) / "events.log").read_text()
    return [line.split("] ", 1)[1].replace(str(target_root), "<target>")
            for line in log.splitlines() if "] " in line]


def history_directory(target_root: Path) -> Path:
    """Where this target's cross-run records go, resolved as a run resolves it."""
    return harness_config.history_dir(
        target_root, harness_config.load_config(target_root))


def history_files(target_root: Path) -> dict[str, str]:
    """Every cross-run history log in the working tree, by run-relative path."""
    directory = history_directory(target_root)
    if not directory.is_dir():
        return {}
    return {
        path.relative_to(target_root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(directory.rglob("*")) if path.is_file()
    }


def committed_history(target_root: Path,
                      revision: str = STORY_BRANCH) -> list[str]:
    """The cross-run history files a revision carries, run-relative.

    The directory is the one a run resolves, and the filenames are whatever the
    run wrote there, so this module names neither.
    """
    directory = history_directory(target_root).relative_to(target_root).as_posix()
    return [relative for relative in
            git(target_root, "ls-tree", "-r", "--name-only", revision
                ).stdout.split()
            if relative.startswith(directory)]


def conflicting_history(target_root: Path) -> list[str]:
    """The branch's own history files, written differently on the branch stood on.

    The observed instance, reconstructed: `.harness/history` is tracked, the
    story branch carries records the base does not, and a run standing on the
    base that writes there before checking out leaves git a file it will not
    overwrite.
    """
    conflicting = committed_history(target_root)
    assert conflicting, "the escalation committed no cross-run history"
    for relative in conflicting:
        write(target_root / relative,
              "a record written while standing on the base\n")
    return conflicting


def tracked_at(target_root: Path, revision: str, relative: str) -> str | None:
    """A file's text at a revision, or None where that revision carries none."""
    shown = git(target_root, "show", f"{revision}:{relative}", check=False)
    return shown.stdout if shown.returncode == 0 else None


def commit_a_fix_on_the_branch(target_root: Path) -> None:
    """What a developer does between an escalation and a resume.

    It is also what clears the resume guard: a commit on the story branch moves
    the branch past the escalation commit, which is the comparison this story
    repoints at the branch. Performed *on the branch* and returned from, so the
    caller decides where the resume is invoked from.
    """
    standing = current_branch(target_root)
    git(target_root, "checkout", "-q", STORY_BRANCH)
    write(target_root / "src" / "app.py", APP_AT_HEAD + "print('by hand')\n")
    git(target_root, "add", "-A")
    git(target_root, "commit", "-q", "-m", "the fix the developer made")
    git(target_root, "checkout", "-q", standing)


def escalated_and_fixed(target_root: Path, harness: Path) -> str:
    """An escalated run with a fix committed on its branch, standing on the base.

    The configuration the defect hid in, returned with the repository standing
    on `DEFAULT_BRANCH`: the cross-run history is committed on the story branch
    and absent from here, which is the divergence git refuses a checkout over.
    """
    escalate(target_root, harness)
    commit_a_fix_on_the_branch(target_root)
    git(target_root, "checkout", "-q", DEFAULT_BRANCH)
    return current_branch(target_root)


# --------------------------------------------------------------------------
# The resume that reproduced the failure, and the one that always worked
# --------------------------------------------------------------------------


def test_the_history_the_branches_disagree_about_is_what_git_refuses_over(
    target, harness_root,
):
    """The fixture's own premise, demonstrated with git alone.

    Not an assertion about the coordinator: it establishes that this repository
    shape really is one where writing the history file and *then* checking out
    the story branch is refused, which is what the old ordering did and what
    every case below is built on. Without it, a resume that reaches its stage
    would be evidence of nothing.
    """
    standing = escalated_and_fixed(target, harness_root)
    assert standing == DEFAULT_BRANCH
    committed = committed_history(target)

    assert committed, "the escalation committed no cross-run history"
    for relative in committed:
        # Tracked on the story branch, carried by no commit on the base: the
        # divergence a run writing above the checkout walks into.
        assert tracked_at(target, STORY_BRANCH, relative) is not None
        assert tracked_at(target, DEFAULT_BRANCH, relative) is None
        write(target / relative, "a record written while standing on the base\n")

    refused = git(target, "checkout", STORY_BRANCH, check=False)

    assert refused.returncode != 0
    for relative in committed:
        assert relative in refused.stderr
    assert current_branch(target) == DEFAULT_BRANCH


def test_a_resume_from_another_branch_reaches_its_stage(target, harness_root):
    """The acceptance criterion the story exists for.

    Invoked from the base while the story branch's tracked cross-run history
    differs from it — the configuration the test above shows git refuses a
    write-then-checkout in — and it runs to completion rather than raising.
    """
    escalated_and_fixed(target, harness_root)

    code, runner = run(target, harness_root, verdicts=[PASS])

    assert code == 0
    assert runner.calls == [VERIFYING]
    assert state_of(target)["status"] == "completed"
    assert current_branch(target) == STORY_BRANCH


#: The fields of a run's recorded state that describe what the resume did,
#: rather than when it happened or what it cost. Compared between the two
#: resumes below, which must be indistinguishable in every one of them.
DECIDED_STATE = ("story_id", "branch", "status", "current_stage",
                 "retry_count", "verification_iterations", "resume_count",
                 "workflow", "self_route_count")


def decided(target_root: Path) -> dict:
    state = state_of(target_root)
    return {field: state.get(field) for field in DECIDED_STATE}


def test_a_resume_from_the_story_branch_is_unchanged(target, harness_root,
                                                     tmp_path):
    """The configuration the defect hid in: standing on the branch already.

    Asserted as sameness with the off-branch resume rather than as a list of
    facts — two targets built from one recipe, escalated identically, resumed
    from the two places a developer can be standing. What the run decided, what
    it said and what it archived are compared whole, so a resume that behaved
    differently for having been invoked from the branch fails here whatever the
    difference was.
    """
    on_branch = target
    escalate(on_branch, harness_root)
    commit_a_fix_on_the_branch(on_branch)
    assert current_branch(on_branch) == STORY_BRANCH
    code, on_branch_runner = run(on_branch, harness_root, verdicts=[PASS])
    assert code == 0

    off_branch = build_target(tmp_path / "off-branch-target")
    escalated_and_fixed(off_branch, harness_root)
    other, off_branch_runner = run(off_branch, harness_root, verdicts=[PASS])

    assert other == code
    assert off_branch_runner.calls == on_branch_runner.calls
    assert decided(off_branch) == decided(on_branch)
    assert messages(off_branch) == messages(on_branch)
    entry = story_coordinator.entry_dir(run_dir_of(on_branch), 1)
    assert entry.is_dir()
    assert sorted(p.name for p in entry.iterdir()) == sorted(
        p.name for p in
        story_coordinator.entry_dir(run_dir_of(off_branch), 1).iterdir())


def test_the_record_this_run_appends_lands_on_the_story_branch(
    target, harness_root,
):
    """Where the cross-run history lives, which this story does not change.

    A resume invoked from the base still appends on the story branch and still
    commits it there: the completion commit carries the history, and the base
    carries none of it. The control is the base's own copy of the same path,
    which the same reads report as absent.
    """
    escalated_and_fixed(target, harness_root)
    code, _ = run(target, harness_root, verdicts=[PASS])
    assert code == 0

    committed = committed_history(target)

    assert committed
    for relative in committed:
        on_branch = tracked_at(target, STORY_BRANCH, relative)
        assert on_branch and on_branch.strip()
        assert tracked_at(target, DEFAULT_BRANCH, relative) is None   # control


# --------------------------------------------------------------------------
# Where the checkout is written, held from the source
# --------------------------------------------------------------------------

#: The call whose position the whole story is about, matched by name because
#: its arguments are wrapped across lines.
CHECKOUT = "_checkout_story_branch("

#: Everything `run_story` does that writes — to the run directory, to the
#: cross-run history, to the queue or to the recorded state. Each must be
#: written below the checkout, and each is asked about its *first* occurrence,
#: so a second call written above the checkout is caught as readily as a moved
#: one.
ACTS = (
    "run_dir.mkdir(",
    "set_history_dir(",
    "prune_history(",
    "archive_attempt(",
    "move_entry_artifacts(",
    "resume_from_capacity(",
    "save_state(",
    "append_event(",
    "outbox_sweep.sweep(",
)

#: Every refusal `run_story` makes that needs no branch to stand on. Each must
#: be written above the checkout, and each is asked about its *last*
#: occurrence, so a refusal moved below to make the reordering easier is caught.
REFUSALS = (
    "read_story(",
    "stage_exception_problems(",
    "completion_commits(",
    "dirty_paths(",
    "already ended with status",
    "unchanged_since_escalation(",
    "already holds an archived attempt",
    "already holds an archived entry",
)


def run_story_body(source: str | None = None) -> str:
    """`run_story`'s own text, which is where every ordering below is read."""
    return conftest.function_source(
        source if source is not None
        else COORDINATOR_PATH.read_text(encoding="utf-8"),
        "run_story")


def _position(body: str, anchor: str, *, last: bool) -> int:
    where = body.rfind(anchor) if last else body.find(anchor)
    assert where >= 0, f"run_story no longer writes {anchor!r} at all"
    return where


def ordering_problems(body: str) -> list[str]:
    """Every place `body` puts an act above the checkout or a refusal below it.

    One checker, run against the shipped body and against each mutant below, so
    the control demonstrates the same code reporting the violation rather than
    a second reading of the same rule.
    """
    checkout = _position(body, CHECKOUT, last=False)
    problems = []
    for anchor in ACTS:
        if _position(body, anchor, last=False) < checkout:
            problems.append(f"{anchor} is written above the checkout")
    for anchor in REFUSALS:
        if _position(body, anchor, last=True) > checkout:
            problems.append(f"{anchor} is written below the checkout")
    return problems


def checkout_moved(body: str, *, to_the_end: bool) -> str:
    """`body` with the checkout call's line moved to one end of the function.

    The violation each ordering assertion exists to catch, constructed rather
    than described: with the call at the end every act precedes it, and with it
    at the start every refusal follows it.
    """
    lines = body.splitlines(keepends=True)
    carrying = [index for index, line in enumerate(lines) if CHECKOUT in line]
    assert len(carrying) == 1, carrying
    line = lines.pop(carrying[0])
    return "".join(lines + [line]) if to_the_end else "".join([line] + lines)


def test_the_checkout_is_written_above_every_act_the_run_takes():
    """The checkout is the run's first act, read off the source.

    The control is the same checker over the body with the checkout moved to
    the end, which must report *every* act — so no one anchor is carrying the
    assertion and each of them can report its own violation.
    """
    body = run_story_body()

    assert [problem for problem in ordering_problems(body)
            if "above the checkout" in problem] == []

    reported = ordering_problems(checkout_moved(body, to_the_end=True))
    for anchor in ACTS:
        assert f"{anchor} is written above the checkout" in reported


def test_the_checkout_is_written_below_every_refusal_that_needs_no_branch():
    """The other half: every refusal that can be made without standing on the
    branch stays above the checkout, and none moved below it.

    The control is the body with the checkout moved to the top, which must
    report every refusal.
    """
    body = run_story_body()

    assert [problem for problem in ordering_problems(body)
            if "below the checkout" in problem] == []

    reported = ordering_problems(checkout_moved(body, to_the_end=False))
    for anchor in REFUSALS:
        assert f"{anchor} is written below the checkout" in reported


def test_the_shipped_ordering_holds_whole():
    """Both families at once, so a body satisfying neither cannot slip between
    the two assertions above."""
    assert ordering_problems(run_story_body()) == []


# --------------------------------------------------------------------------
# The helper reports what git refused rather than raising
# --------------------------------------------------------------------------


CHECKOUT_HELPER = "_checkout_story_branch"


def raises_in(source: str, name: str) -> list[str]:
    """Every `raise` written in the named top-level function of `source`."""
    return [ast.dump(node)
            for node in ast.walk(ast.parse(conftest.function_source(source, name)))
            if isinstance(node, ast.Raise)]


def callers_of(source: str, called: str) -> set[str]:
    """Every top-level function of `source` whose body calls `called`."""
    tree = ast.parse(source)
    names = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) \
                    and inner.func.id == called:
                names.add(node.name)
    return names


def test_the_checkout_helper_writes_no_raise_on_a_refused_checkout():
    """The absence, with its control: the same scan over the helper with a
    raise put back reports it, so a scan that has stopped seeing the function
    cannot pass as an absence."""
    source = COORDINATOR_PATH.read_text(encoding="utf-8")

    assert raises_in(source, CHECKOUT_HELPER) == []

    put_back = source.replace(
        "    result = _git(target_root, *args)",
        "    result = _git(target_root, *args)\n"
        "    if result.returncode != 0:\n"
        "        raise RuntimeError(branch)",
        1,
    )
    assert put_back != source
    assert raises_in(put_back, CHECKOUT_HELPER) != []                 # control


def test_run_story_is_the_only_caller_of_the_checkout_helper():
    """One call site, so the refusal below it is the only way a refused
    checkout is handled. The control is a source carrying a second caller,
    which the same scan reports."""
    source = COORDINATOR_PATH.read_text(encoding="utf-8")

    assert callers_of(source, CHECKOUT_HELPER) == {"run_story"}

    second = source + (
        "\n\ndef _a_second_caller(target_root, branch):\n"
        f"    return {CHECKOUT_HELPER}(target_root, branch)\n"
    )
    assert callers_of(second, CHECKOUT_HELPER) == {"run_story",
                                                  "_a_second_caller"}  # control


def test_the_helper_returns_the_problems_git_named(target, harness_root):
    """The helper, called directly on a checkout git refuses.

    It returns git's own words rather than raising, leaves the repository
    standing where it was, and the control beside it is the same call on a
    checkout git makes, which returns nothing and does move the branch.
    """
    escalated_and_fixed(target, harness_root)
    conflicting = conflicting_history(target)

    problems = story_coordinator._checkout_story_branch(target, STORY_BRANCH)

    assert problems
    assert all(problem.strip() for problem in problems)
    assert any(relative in problem
               for relative in conflicting for problem in problems)
    assert current_branch(target) == DEFAULT_BRANCH

    # The control: the same call once the conflict is cleared, which reports
    # nothing and stands the repository on the branch.
    for relative in conflicting:
        (target / relative).unlink()
    assert story_coordinator._checkout_story_branch(target, STORY_BRANCH) == []
    assert current_branch(target) == STORY_BRANCH


# --------------------------------------------------------------------------
# A refused checkout refuses like its neighbours, and writes nothing
# --------------------------------------------------------------------------


def test_a_fresh_run_whose_checkout_is_refused_creates_nothing(
    tmp_path, harness_root, capsys,
):
    """The refusal in the state where "nothing was written" is the whole of it:
    a fresh run, whose branch git will not make.

    Exit 1 through the shared refusal — the branch named, one line per problem
    git reported, and guidance saying what clears them. The control is the same
    fixture with a usable prefix, which does create the run directory, the
    state, the log and the history, and does invoke agents.
    """
    refused_target = build_target(tmp_path / "unusable-branch",
                                  prefix=UNUSABLE_PREFIX)
    branch = f"{UNUSABLE_PREFIX}{STORY_ID}"
    capsys.readouterr()

    blocked = Runner(refused_target)
    code = story_coordinator.run_story(
        STORY_ID, harness_root, refused_target, blocked)
    message = capsys.readouterr().err

    assert code == 1
    assert blocked.calls == []
    assert not run_dir_of(refused_target).exists()
    assert not (refused_target / ".harness" / "logs" / f"{STORY_ID}.log").exists()
    assert history_files(refused_target) == {}
    assert current_branch(refused_target) == DEFAULT_BRANCH
    assert git(refused_target, "status", "--porcelain").stdout.strip() == ""

    assert branch in message
    for problem in story_coordinator._checkout_story_branch(
            refused_target, branch):
        assert f"  - {problem}" in message
    assert "run the story again" in message

    # The control: the same run under a prefix git will make a branch out of.
    proceeding = build_target(tmp_path / "usable-branch")
    code, runner = run(proceeding, harness_root, edits_the_module, [PASS])
    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert run_dir_of(proceeding).is_dir()
    assert history_files(proceeding) != {}


def test_a_refused_resume_archives_nothing_and_resets_no_counter(
    target, harness_root, capsys,
):
    """The refusal in the state the traceback was found in: a run to re-enter,
    a run directory already holding evidence, and a checkout git refuses.

    The state is left `running`, which is the one status the clean-tree
    pre-flight excludes — a crashed run's tree is its own unfinished work — so
    the run reaches the checkout with the conflict in place, which is what
    makes this a test of the checkout's refusal rather than of the dirty-tree
    one. The control is the identical resume with the conflict cleared, which
    does archive, does open the entry and does reset the counters.
    """
    escalated_and_fixed(target, harness_root)
    write_state(target, status="running")
    run_dir = run_dir_of(target)
    conflicting = conflicting_history(target)

    state_before = (run_dir / "state.json").read_text()
    events_before = (run_dir / "events.log").read_text()
    history_before = history_files(target)
    capsys.readouterr()

    blocked = Runner(target)
    code = story_coordinator.run_story(STORY_ID, harness_root, target, blocked)
    message = capsys.readouterr().err

    assert code == 1
    assert blocked.calls == []
    assert STORY_BRANCH in message
    for relative in conflicting:
        assert any(f"  - {problem}" in message and relative in problem
                   for problem in story_coordinator._checkout_story_branch(
                       target, STORY_BRANCH))
    assert (run_dir / "state.json").read_text() == state_before
    assert (run_dir / "events.log").read_text() == events_before
    assert history_files(target) == history_before
    assert not story_coordinator.entry_dir(run_dir, 1).exists()
    assert not story_coordinator.attempt_dir(run_dir, 1).exists()
    assert current_branch(target) == DEFAULT_BRANCH

    # The control: the same resume with the conflict cleared, which takes every
    # act the refusal above took none of.
    for relative in conflicting:
        (target / relative).unlink()
    code, runner = run(target, harness_root, verdicts=[PASS])
    assert code == 0
    assert runner.calls != []
    assert story_coordinator.entry_dir(run_dir, 1).is_dir()
    assert state_of(target)["resume_count"] == 1
    assert (run_dir / "state.json").read_text() != state_before


# --------------------------------------------------------------------------
# The guard asks about the story branch, wherever the developer stands
# --------------------------------------------------------------------------

#: Today's escalation-commit comparison, and the comparison it replaced. The
#: mutant below undoes exactly this one expression, which is what makes it a
#: control for the repointing rather than for the guard as a whole.
BRANCH_COMPARISON = 'f"{state.branch}~1"'
HEAD_COMPARISON = '"HEAD~1"'


def guard_pointed_at_head(tmp_path: Path):
    """Today's coordinator with the escalation comparison pointed back at HEAD.

    The one expression this story changed, undone. Everything else — the three
    comparisons, the exemptions, the porcelain leg — is today's, so a
    difference between this module and the real one is the repointing and
    nothing else.
    """
    return conftest.load_mutant(
        COORDINATOR_PATH,
        [(BRANCH_COMPARISON, HEAD_COMPARISON)],
        name="coordinator_guard_at_head",
        tmp_path=tmp_path,
    )


def test_the_guard_compares_the_story_branch_rather_than_where_it_was_invoked(
    target, harness_root, tmp_path,
):
    """The repointing, demonstrated from the situation it exists for.

    Standing on the base with nothing changed since the escalation, today's
    guard establishes sameness and names its evidence. The control is the
    mutant with the comparison pointed back at HEAD, which in the identical
    situation establishes nothing — so the refusal above is the branch being
    resolved rather than the guard being easy to satisfy.
    """
    escalate(target, harness_root)
    git(target, "checkout", "-q", DEFAULT_BRANCH)
    state = story_coordinator.load_state(run_dir_of(target))
    story_text = (target / ".harness" / "stories" / f"{STORY_ID}.yaml").read_text()

    evidence = story_coordinator.unchanged_since_escalation(
        state, story_text, target, harness_root)

    assert evidence
    assert current_branch(target) == DEFAULT_BRANCH

    mutant = guard_pointed_at_head(tmp_path)
    assert mutant.unchanged_since_escalation(
        mutant.load_state(run_dir_of(target)), story_text, target,
        harness_root) == []                                          # control


def test_the_comparison_names_the_branch_and_no_longer_names_head():
    """The same fact in the source, since a guard that established sameness for
    some other reason would satisfy the behavioural test above.

    The control is the mutant expression, which the same reads report: HEAD
    found and the branch absent.
    """
    guard = conftest.function_source(
        COORDINATOR_PATH.read_text(encoding="utf-8"),
        "unchanged_since_escalation")

    assert BRANCH_COMPARISON in guard
    assert HEAD_COMPARISON not in guard

    pointed_at_head = guard.replace(BRANCH_COMPARISON, HEAD_COMPARISON)
    assert HEAD_COMPARISON in pointed_at_head                          # control
    assert BRANCH_COMPARISON not in pointed_at_head


def test_an_off_branch_resume_with_nothing_changed_is_refused(
    target, harness_root, capsys,
):
    """The guard's refusal, reached through a whole run invoked off the branch.

    Before this story such a resume could never be refused: the comparison
    resolved HEAD, which off the branch is not the story branch's tip's parent,
    so sameness could never be established. The control is the same resume with
    one of the guard's three inputs changed — a commit on the story branch —
    which proceeds.
    """
    escalate(target, harness_root)
    git(target, "checkout", "-q", DEFAULT_BRANCH)
    capsys.readouterr()

    blocked = Runner(target)
    code = story_coordinator.run_story(STORY_ID, harness_root, target, blocked)
    message = capsys.readouterr().err

    assert code == 1
    assert blocked.calls == []
    assert f"{STORY_ID} escalated at stage" in message
    assert state_of(target)["status"] == "escalated"
    assert current_branch(target) == DEFAULT_BRANCH

    # The control: one of the three inputs changed, and the same resume from
    # the same place proceeds.
    commit_a_fix_on_the_branch(target)
    assert current_branch(target) == DEFAULT_BRANCH
    code, runner = run(target, harness_root, verdicts=[PASS])
    assert code == 0
    assert runner.calls != []


def test_the_porcelain_leg_still_asks_about_the_one_working_tree(
    target, harness_root,
):
    """The leg this story deliberately left alone.

    A working tree is one tree whatever branch is checked out, so the status
    comparison means the same thing from either place. Demonstrated as a
    difference the guard reports from the base exactly as it reports it from
    the branch: the guard clears in both when the tree is dirty, and refuses in
    both when it is not.
    """
    escalate(target, harness_root)
    state = story_coordinator.load_state(run_dir_of(target))
    story_text = (target / ".harness" / "stories" / f"{STORY_ID}.yaml").read_text()

    def evidence() -> list[str]:
        return story_coordinator.unchanged_since_escalation(
            state, story_text, target, harness_root)

    assert evidence()                                   # on the branch, clean
    git(target, "checkout", "-q", DEFAULT_BRANCH)
    assert evidence()                                   # off the branch, clean

    write(target / "src" / "app.py", APP_AT_HEAD + "print('uncommitted')\n")
    assert evidence() == []                             # off the branch, dirty
    git(target, "checkout", "-q", "--", "src/app.py")
    git(target, "checkout", "-q", STORY_BRANCH)
    write(target / "src" / "app.py", APP_AT_HEAD + "print('uncommitted')\n")
    assert evidence() == []                             # on the branch, dirty
