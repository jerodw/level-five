"""Independent validation for story-034: the resume guard can refuse when the
harness is its own target.

The subject is one leg of `unchanged_since_escalation`. Before this story its
third comparison read `state.harness_revision`, which is recorded as the first
act of `_escalate` and therefore always differs afterwards in a repository
where the harness *is* the target — so in this repository the guard could
never refuse. After it, a shared root defers the third comparison to the
second.

Almost nothing here is asserted from prose. Two fixtures are built under
tmp_path and driven with a fake agent runner:

  * `shared_root` — one git checkout carrying both a target project and a copy
    of the harness's `workflows/`, `rules/`, `prompts/` and `schemas/`, handed
    to `run_story` as both `harness_root` and `target_root`. This is the shape
    the harness is developed under and the one the story is about.
  * `separate_root` — a target repository and the real harness checkout, the
    deployment the guard was written for, which this story must leave alone.

Every absence asserted here carries a demonstration that the same check can
report the violation it exists to catch:

  * "the shared-root resume refuses and invokes nothing" sits beside the same
    resume with the story amended and beside the same resume with a commit on
    the branch, each of which does invoke an agent — so the refusal is about
    nothing having changed rather than about resuming;
  * "the separate-root deployment behaves exactly as it did" is asserted at
    both ends of this story's own range: the pre-story guard is reconstructed
    by removing the shared-root leg from today's source, shown to be the
    pre-story text once docstrings and comments are stripped, and run beside
    today's guard on the same escalated repository. Its control is the *shared*
    root, where the two disagree — which is the whole of what this story
    changed;
  * "no fourth comparison was added" is a length assertion whose control is a
    mutant that appends a fourth evidence line, which the same assertion
    reports;
  * "no flag, environment variable or configuration key bypasses the guard" is
    a scan whose control is `run_story`, which does read configuration and
    which the same scan finds;
  * "an unestablishable shared-root question produces no false refusal" sits
    beside `same_repository` returning True for two paths in one checkout, so
    the helper is shown capable of the answer it is being asked to withhold.

Nothing here invokes a model: every run goes through a fake agent runner.
"""
import ast
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import (BASELINE as BASELINE_BOUND, ENDPOINT, function_source,
                      function_source_at, load_mutant)

import harness_config
import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
COORDINATOR_REL = "orchestration/story_coordinator.py"
COORDINATOR_PATH = REPO_ROOT / COORDINATOR_REL
WORKFLOW = harness_config.load_workflow(REPO_ROOT, "story-workflow")
VERIFIER_STAGE = next(s for s in WORKFLOW["stages"] if "on_failure" in s)

STORY_ID = "story-001"
DEFAULT_BRANCH = "main"
STORY_BRANCH = f"story/{STORY_ID}"

#: The directories a checkout has to carry to serve as a harness root.
HARNESS_DIRS = ("workflows", "rules", "prompts", "schemas")

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

CONFIG = """\
workflow: story-workflow
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: echo tests-ok
"""

APP_AT_HEAD = "print('hello')\n"


# --------------------------------------------------------------------------
# The two fixtures: one checkout serving both roles, and the separate pair
# --------------------------------------------------------------------------


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=check)


def build_target(root: Path, *, harness_inside: bool,
                 gitignore: str = "") -> Path:
    """A target repository, optionally carrying the harness inside it.

    `harness_inside` is the whole difference between the two fixtures: with it
    the same directory is a working harness root — `workflows/`, `rules/`,
    `prompts/` and `schemas/` are what `run_story` reads out of one — and can
    be passed as both roots, which is the deployment this story is about.
    """
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml", CONFIG)
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "src" / "app.py", APP_AT_HEAD)
    if gitignore:
        write(root / ".gitignore", gitignore)
    if harness_inside:
        for sub in HARNESS_DIRS:
            shutil.copytree(REPO_ROOT / sub, root / sub)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root,
                   check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    subprocess.run(["git", "branch", "-M", DEFAULT_BRANCH], cwd=root, check=True)
    return root


@pytest.fixture
def shared_root(tmp_path: Path) -> Path:
    """One checkout that is both the harness and the target."""
    return build_target(tmp_path / "shared", harness_inside=True)


@pytest.fixture
def separate_root(tmp_path: Path) -> Path:
    """A target repository whose harness is the real checkout elsewhere."""
    return build_target(tmp_path / "separate", harness_inside=False)


# --------------------------------------------------------------------------
# The fake runner and the escalation it drives
# --------------------------------------------------------------------------


class Runner:
    """A fake agent runner: each stage writes the artifacts its workflow entry
    declares, and the implementer edits the target's working tree."""

    def __init__(self, target_root: Path, verdict: dict = FAIL, *,
                 edit: bool = True):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.verdict = verdict
        self.edit = edit
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None):
        self.calls.append(stage)
        if stage == "implementer":
            record = {"modified": [], "created": [], "deleted": []}
            if self.edit:
                write(self.target_root / "src" / "app.py",
                      APP_AT_HEAD + "print('implemented')\n")
                record["modified"] = ["src/app.py"]
            write_json(self.run_dir / "changed-files.json", record)
            write(self.run_dir / "implementation-summary.md", "Implemented.\n")
        elif stage == "tester":
            write_json(self.run_dir / "test-results.json", {
                "status": "passed", "tests_written": 1, "tests_run": 1,
                "tests_passed": 1, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / "tester-changed-files.json",
                       {"modified": [], "created": [], "deleted": []})
        elif stage == "verifier":
            write_json(self.run_dir / "verification-result.json", self.verdict)
        elif stage == "documenter":
            write(self.run_dir / "documentation-report.md", "Nothing.\n")
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path) -> Path:
    return target_root / ".harness" / "runs" / STORY_ID


def state_of(target_root: Path) -> dict:
    return json.loads((run_dir_of(target_root) / "state.json").read_text())


def story_path_of(target_root: Path) -> Path:
    return target_root / ".harness" / "stories" / f"{STORY_ID}.yaml"


def escalate(target_root: Path, harness: Path, *, edit: bool = True) -> Runner:
    runner = Runner(target_root, FAIL, edit=edit)
    code = story_coordinator.run_story(STORY_ID, harness, target_root, runner)
    assert code == 2, "the shape was meant to escalate"
    assert state_of(target_root)["status"] == "escalated"
    return runner


def guard(target_root: Path, harness: Path,
          story_text: str | None = None, *,
          module=story_coordinator, changes: dict | None = None) -> list[str]:
    """`unchanged_since_escalation`, called directly against a run directory.

    `changes` overrides recorded fields *in memory* rather than by rewriting
    state.json. Both fixtures track their run directory, so writing the file
    dirties the working tree — and the guard's second comparison returns early
    on a dirty tree, which would make every "this field cleared the guard"
    assertion pass for a reason that has nothing to do with the field.
    """
    state = module.load_state(run_dir_of(target_root))
    for field, value in (changes or {}).items():
        assert hasattr(state, field), field
        setattr(state, field, value)
    if story_text is None:
        story_text = story_path_of(target_root).read_text()
    return module.unchanged_since_escalation(
        state, story_text, target_root, harness)


def amend_the_story(target_root: Path) -> None:
    write(story_path_of(target_root),
          STORY + "  - and keep the sample behavior working\n")


def commit_on_the_branch(target_root: Path,
                         message: str = "a developer's fix") -> None:
    write(target_root / "src" / "app.py", APP_AT_HEAD + "print('by hand')\n")
    git(target_root, "add", "-A")
    git(target_root, "commit", "-q", "-m", message)


def executable_source(text: str) -> str:
    """Strip docstrings and comment lines; prose may name what code may not."""
    kept, in_docstring = [], False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if not (len(stripped) > 3 and stripped.rstrip().endswith('"""')
                    and stripped.rstrip() != '"""'):
                in_docstring = not in_docstring
            continue
        if in_docstring or stripped.startswith("#"):
            continue
        if stripped:
            kept.append(line)
    return "\n".join(kept)


#: The shared-root leg exactly as it stands in today's source. Removing it is
#: how the pre-story guard is reconstructed below; the assertion that the
#: removal really does reconstruct it is
#: `test_removing_the_shared_root_leg_reproduces_the_pre_story_guard`.
SHARED_ROOT_LEG = """    if same_repository(target_root, harness_root):
        evidence.append(
            "the harness is the same checkout as the target, so it is covered "
            "by the branch comparison above"
        )
        return evidence

"""


def pre_story_coordinator(tmp_path: Path):
    """Today's coordinator with the shared-root leg removed.

    This is the guard as it stood before this story, built by deleting what
    the story added rather than by loading a module out of git history — a
    coordinator recovered from history runs against today's workflow, schemas
    and config and stops running as soon as any of them legitimately changes,
    which is why `conftest.load_mutant` takes a working-tree path.
    """
    return load_mutant(COORDINATOR_PATH, [(SHARED_ROOT_LEG, "")],
                       name="coordinator_before_story_034", tmp_path=tmp_path)


# --------------------------------------------------------------------------
# The refusal this story exists to make possible
# --------------------------------------------------------------------------


def test_a_shared_root_resume_with_nothing_changed_is_refused(
    shared_root, capsys,
):
    """The story's first acceptance criterion, driven end to end.

    One checkout is both roots. The run escalates, nothing is touched, and the
    resume is refused: exit 1, no agent invoked, the recorded status still
    `escalated`, and no attempt archived — the refusal happens above the
    archive, so a refused resume leaves the run exactly as the escalation did.

    The controls are the two tests below, which amend the story and commit on
    the branch in this same fixture and do invoke an agent.
    """
    escalate(shared_root, shared_root)
    run_dir = run_dir_of(shared_root)
    summary = (run_dir / "escalation-summary.md").read_text()
    reason = summary.split("## Reason", 1)[1].split("##", 1)[0].strip()
    state_before = (run_dir / "state.json").read_text()
    head_before = git(shared_root, "rev-parse", "HEAD").stdout.strip()
    capsys.readouterr()

    refused = Runner(shared_root)
    code = story_coordinator.run_story(
        STORY_ID, shared_root, shared_root, refused)
    message = capsys.readouterr().err

    assert code == 1
    assert refused.calls == []
    assert reason and reason in message
    assert state_of(shared_root)["status"] == "escalated"
    assert (run_dir / "state.json").read_text() == state_before
    assert not (run_dir / "attempts").exists()
    assert git(shared_root, "rev-parse", "HEAD").stdout.strip() == head_before


def test_the_shared_root_refusal_names_its_three_pieces_of_evidence(
    shared_root, capsys,
):
    """The second acceptance criterion: what the refusal says.

    All three lines are read off the printed message, and the third is the one
    this story adds — the harness being the same checkout as the target rather
    than a recorded revision. The control is the separate-root refusal below,
    whose third line names a revision instead, so the assertion is about which
    leg answered rather than about any three lines being present.
    """
    escalate(shared_root, shared_root)
    capsys.readouterr()

    assert story_coordinator.run_story(
        STORY_ID, shared_root, shared_root, Runner(shared_root)) == 1
    message = capsys.readouterr().err

    evidence = guard(shared_root, shared_root)
    assert len(evidence) == 3
    for line in evidence:
        assert line in message

    story_line, branch_line, harness_line = evidence
    assert "byte for byte the one the escalated run read" in story_line
    assert state_of(shared_root)["escalation_commit"][:12] in branch_line
    assert "nothing uncommitted" in branch_line
    assert "the same checkout as the target" in harness_line
    assert str(story_path_of(shared_root)) in message
    assert STORY_BRANCH in message


def test_the_separate_root_refusal_still_names_the_recorded_revision(
    separate_root,
):
    """The control for the assertion above, and the leg this story left alone.

    The same three-line shape in the deployment the guard was written for: the
    third line is the recorded harness revision, which is what a separate-root
    reader must still see.
    """
    escalate(separate_root, REPO_ROOT)

    evidence = guard(separate_root, REPO_ROOT)

    assert len(evidence) == 3
    assert "still at revision" in evidence[2]
    assert state_of(separate_root)["harness_revision"][:12] in evidence[2]
    assert "the same checkout" not in evidence[2]


def test_amending_the_story_resumes_a_shared_root_run(shared_root):
    """The third acceptance criterion: the guard fails open where it always
    did. The control is the untouched guard asserted first, which refuses."""
    escalate(shared_root, shared_root)
    assert guard(shared_root, shared_root) != []            # the control

    amend_the_story(shared_root)
    assert guard(shared_root, shared_root) == []

    # Committing after the guard assertion is story-021's clean-tree pre-flight
    # being satisfied, not a second way of clearing the guard: the claim is
    # carried by the direct call above.
    git(shared_root, "add", "-A")
    git(shared_root, "commit", "-q", "-m", "the amended story")

    resumed = Runner(shared_root, PASS)
    code = story_coordinator.run_story(
        STORY_ID, shared_root, shared_root, resumed)
    assert code == 0
    assert resumed.calls[0] == VERIFIER_STAGE["name"]
    assert state_of(shared_root)["status"] == "completed"


def test_a_commit_on_the_branch_resumes_a_shared_root_run(shared_root):
    """The fourth acceptance criterion, and the one the deferral rests on.

    Under a shared root the branch comparison stands for the harness one, so a
    commit made after the escalation has to clear the guard — it is both a
    change to the branch and, in this fixture, a change to the harness source.
    The control is the assertion before the commit, which refuses.
    """
    escalate(shared_root, shared_root)
    assert guard(shared_root, shared_root) != []            # the control

    commit_on_the_branch(shared_root)
    assert git(shared_root, "status", "--porcelain").stdout.strip() == ""

    assert guard(shared_root, shared_root) == []

    resumed = Runner(shared_root, PASS)
    code = story_coordinator.run_story(
        STORY_ID, shared_root, shared_root, resumed)
    assert code == 0
    assert resumed.calls[0] == VERIFIER_STAGE["name"]


def test_an_edit_to_the_harness_source_alone_resumes_a_shared_root_run(
    shared_root,
):
    """What the deferral claims, tested as itself: under one tree a change to
    the harness source *is* a change to the branch, so editing only a copied
    harness file and committing it clears the guard.

    The control is the same fixture immediately before the edit, which refuses.
    """
    escalate(shared_root, shared_root)
    assert guard(shared_root, shared_root) != []            # the control

    workflow_path = shared_root / "workflows" / "story-workflow.json"
    workflow = json.loads(workflow_path.read_text())
    workflow["description"] = "an edited harness"
    write_json(workflow_path, workflow)
    git(shared_root, "add", "-A")
    git(shared_root, "commit", "-q", "-m", "an edit to the harness itself")

    assert guard(shared_root, shared_root) == []


# --------------------------------------------------------------------------
# What the guard cannot establish, it does not refuse on
# --------------------------------------------------------------------------


def test_an_escalation_that_committed_nothing_refuses_in_neither_fixture(
    tmp_path,
):
    """The fifth acceptance criterion: the clean-tree escalation shape.

    An ignored run directory and stages that touch nothing leave the
    escalation with nothing to commit, so `escalation_commit` is empty and the
    second comparison returns early — before either form of the third. Driven
    genuinely rather than by emptying the field, in both fixtures.

    The control is the same repository shape with one edit, whose escalation
    does record a commit and whose guard does refuse.
    """
    for name, harness_inside in (("quiet-shared", True),
                                 ("quiet-separate", False)):
        root = build_target(tmp_path / name, harness_inside=harness_inside,
                            gitignore=".harness/runs/\n")
        harness = root if harness_inside else REPO_ROOT
        escalate(root, harness, edit=False)

        assert state_of(root)["escalation_commit"] == ""
        assert guard(root, harness) == [], name

        # The control: the same shape with an edit, which does commit and does
        # refuse — so the empty result above is the empty commit's doing.
        loud = build_target(tmp_path / f"{name}-edited",
                            harness_inside=harness_inside,
                            gitignore=".harness/runs/\n")
        loud_harness = loud if harness_inside else REPO_ROOT
        escalate(loud, loud_harness, edit=True)
        assert state_of(loud)["escalation_commit"] != ""
        assert guard(loud, loud_harness) != [], name


def test_each_recorded_fact_alone_clears_the_guard_in_both_fixtures(
    shared_root, separate_root,
):
    """The three comparisons, one at a time, so no single input can be the
    only one carrying the decision.

    Under a shared root the third comparison no longer reads
    `harness_revision`, so emptying that field must *not* clear the guard
    there — which is exactly the difference this story made. Its control is
    the separate-root fixture in the same loop, where emptying it does clear
    the guard.

    Every field override is made in memory, for the reason `guard` records:
    rewriting state.json dirties a tracked run directory, and the guard's
    second comparison returns early on a dirty tree, so each assertion below
    would hold whatever the field said.
    """
    escalate(shared_root, shared_root)
    escalate(separate_root, REPO_ROOT)
    assert len(guard(shared_root, shared_root)) == 3
    assert len(guard(separate_root, REPO_ROOT)) == 3

    for root, harness in ((shared_root, shared_root), (separate_root, REPO_ROOT)):
        for field in ("story_digest", "escalation_commit"):
            assert guard(root, harness, changes={field: ""}) == [], \
                (root.name, field)
        assert guard(root, harness) != []                   # the control

        story_text = story_path_of(root).read_text()
        assert guard(root, harness, story_text + "\n# amended\n") == []

    assert guard(separate_root, REPO_ROOT,
                 changes={"harness_revision": "0" * 40}) == []
    assert guard(separate_root, REPO_ROOT, changes={"harness_revision": ""}) == []

    # The shared root does not read the field at all, which is the story.
    assert guard(shared_root, shared_root,
                 changes={"harness_revision": "0" * 40}) != []
    assert guard(shared_root, shared_root,
                 changes={"harness_revision": ""}) != []


def test_same_repository_answers_only_what_git_can_establish(
    shared_root, separate_root, tmp_path,
):
    """The seventh acceptance criterion, at the helper.

    Shared is decided from `git rev-parse --show-toplevel` on both roots, so a
    subdirectory of one checkout is shared with its own root, two checkouts are
    not, and a root git cannot resolve is not — the one-directional bias every
    other decision function in the coordinator takes.

    The True cases are the control for the False ones: the helper is shown
    capable of the answer it is being asked to withhold.
    """
    not_a_repository = tmp_path / "not-a-repository"
    not_a_repository.mkdir()
    inside = shared_root / "src"

    assert story_coordinator.same_repository(shared_root, shared_root) is True
    assert story_coordinator.same_repository(shared_root, inside) is True
    assert story_coordinator.same_repository(inside, shared_root) is True

    assert story_coordinator.same_repository(separate_root, REPO_ROOT) is False
    assert story_coordinator.same_repository(shared_root, separate_root) is False
    assert story_coordinator.same_repository(shared_root, not_a_repository) is False
    assert story_coordinator.same_repository(not_a_repository, shared_root) is False
    assert story_coordinator.same_repository(
        not_a_repository, not_a_repository) is False


def test_an_unestablishable_root_falls_through_to_the_revision_comparison(
    tmp_path,
):
    """The rest of the seventh criterion, at the guard.

    A harness root that is not a git repository cannot be shown to share the
    target's checkout, so the guard takes the revision comparison it always
    took — and the escalation recorded "" for a revision it could not read, so
    the run resumes rather than being falsely refused.

    The control is the identical run against the real harness checkout, which
    does refuse.
    """
    target = build_target(tmp_path / "fallthrough", harness_inside=False,
                          gitignore=".harness/runs/\n")
    fake = tmp_path / "harness-not-a-repo"
    for sub in HARNESS_DIRS:
        shutil.copytree(REPO_ROOT / sub, fake / sub)
    assert story_coordinator._revision(fake) == ""
    assert story_coordinator.same_repository(target, fake) is False

    escalate(target, fake)
    assert state_of(target)["harness_revision"] == ""
    assert guard(target, fake) == []

    resumed = Runner(target, PASS)
    assert story_coordinator.run_story(STORY_ID, fake, target, resumed) == 0
    assert resumed.calls[0] == VERIFIER_STAGE["name"]

    control = build_target(tmp_path / "fallthrough-control",
                           harness_inside=False, gitignore=".harness/runs/\n")
    escalate(control, REPO_ROOT)
    assert guard(control, REPO_ROOT) != []                  # the control


# --------------------------------------------------------------------------
# The separate-root deployment, at both ends of this story's range
# --------------------------------------------------------------------------


def test_removing_the_shared_root_leg_reproduces_the_pre_story_guard(tmp_path):
    """What the reconstruction below is allowed to claim.

    The mutant is today's coordinator with the shared-root leg deleted. Once
    docstrings and comments are stripped — prose changed and may — its
    `unchanged_since_escalation` is the pre-story function, character for
    character, read out of this story's own baseline commit.

    The control is the same comparison against *today's* function, which
    differs: so the equality above is the deletion reconstructing the earlier
    code rather than the two texts being the same file read twice.
    """
    mutant = pre_story_coordinator(tmp_path)
    reconstructed = executable_source(
        function_source(Path(mutant.__file__).read_text(encoding="utf-8"),
                        "unchanged_since_escalation"))
    before = executable_source(function_source_at(
        COORDINATOR_REL, "unchanged_since_escalation",
        validation_file=Path(__file__), bound=BASELINE_BOUND, repo=REPO_ROOT))
    today = executable_source(function_source_at(
        COORDINATOR_REL, "unchanged_since_escalation",
        validation_file=Path(__file__), bound=ENDPOINT, repo=REPO_ROOT))

    assert reconstructed == before
    assert today != before                                  # the control


def test_the_separate_root_leg_is_unchanged_character_for_character(tmp_path):
    """The revision comparison and its evidence line, sliced out of the
    function's own text at both ends of this story's range.

    The control is the whole function at the same two ends, which differs —
    so this is the separate-root tail being untouched rather than two readings
    of one unchanged file.
    """
    def tail(bound: str) -> str:
        body = function_source_at(
            COORDINATOR_REL, "unchanged_since_escalation",
            validation_file=Path(__file__), bound=bound, repo=REPO_ROOT)
        return body[body.index("    if not state.harness_revision"):]

    whole_before = function_source_at(
        COORDINATOR_REL, "unchanged_since_escalation",
        validation_file=Path(__file__), bound=BASELINE_BOUND, repo=REPO_ROOT)
    whole_after = function_source_at(
        COORDINATOR_REL, "unchanged_since_escalation",
        validation_file=Path(__file__), bound=ENDPOINT, repo=REPO_ROOT)

    assert tail(ENDPOINT) == tail(BASELINE_BOUND)
    assert whole_after != whole_before                      # the control


def test_a_separate_root_deployment_behaves_at_both_ends_as_it_did(
    separate_root, tmp_path,
):
    """The sixth acceptance criterion, driven rather than reasoned about.

    The pre-story guard and today's guard are run against the same escalated
    separate-root repository, through every case that decides a resume: nothing
    changed, an amended story, a commit on the branch, an unrecorded harness
    revision, an empty escalation commit. Every answer agrees.

    The control is the *shared* root, where the two disagree — which is the
    whole of what this story changed, and what makes the agreement above a
    fact about separate roots rather than about two identical modules.
    """
    before = pre_story_coordinator(tmp_path)
    escalate(separate_root, REPO_ROOT)
    story_text = story_path_of(separate_root).read_text()

    def both(root: Path, harness: Path, text: str | None = None,
             changes: dict | None = None):
        return (guard(root, harness, text, module=before, changes=changes),
                guard(root, harness, text, module=story_coordinator,
                      changes=changes))

    was, now = both(separate_root, REPO_ROOT)
    assert was == now != []
    assert len(now) == 3

    was, now = both(separate_root, REPO_ROOT, story_text + "\n# amended\n")
    assert was == now == []

    for field in ("story_digest", "escalation_commit", "harness_revision"):
        was, now = both(separate_root, REPO_ROOT, changes={field: ""})
        assert was == now == [], field

    commit_on_the_branch(separate_root)
    was, now = both(separate_root, REPO_ROOT)
    assert was == now == []

    # The control: one checkout as both roots, where the pre-story guard can
    # never refuse and today's does.
    shared = build_target(tmp_path / "shared-for-control", harness_inside=True)
    escalate(shared, shared)
    was, now = both(shared, shared)
    assert was == []
    assert len(now) == 3


# --------------------------------------------------------------------------
# The shape of the guard: three comparisons, and no way past it
# --------------------------------------------------------------------------


def test_the_guard_still_makes_exactly_three_comparisons(
    shared_root, separate_root, tmp_path,
):
    """The eighth acceptance criterion. Neither form of the third comparison
    returns more or fewer than three pieces of evidence.

    The control is a mutant that appends a fourth evidence line, which the same
    assertion reports — so a length of three is a fact about the guard rather
    than about the assertion being unable to see a fourth.
    """
    escalate(shared_root, shared_root)
    escalate(separate_root, REPO_ROOT)

    assert len(guard(shared_root, shared_root)) == 3
    assert len(guard(separate_root, REPO_ROOT)) == 3

    four_legged = load_mutant(
        COORDINATOR_PATH,
        [('    evidence.append(\n        f"the harness is still at revision '
          '{state.harness_revision[:12]}"\n    )\n',
          '    evidence.append(\n        f"the harness is still at revision '
          '{state.harness_revision[:12]}"\n    )\n'
          '    evidence.append("a fourth comparison nothing makes")\n')],
        name="coordinator_with_a_fourth_leg", tmp_path=tmp_path)
    assert len(guard(separate_root, REPO_ROOT, module=four_legged)) == 4


def test_no_flag_environment_variable_or_configuration_key_bypasses_the_guard():
    """The ninth acceptance criterion, as a scan of what the guard reads.

    `unchanged_since_escalation` and `same_repository` read their arguments,
    the recorded state and git. Nothing else: no environment lookup, no
    configuration key, no command-line flag.

    The control is `run_story`, which does read configuration and which the
    same scan finds — so an empty result is the guard's doing rather than a
    scan that has stopped matching.
    """
    bypass = ("environ", "getenv", "config.get", "argparse", "add_argument",
              "--force", "--skip", "--no-guard", "skip_guard", "force")

    def reads(name: str) -> list[str]:
        source = executable_source(function_source(
            COORDINATOR_PATH.read_text(encoding="utf-8"), name))
        return [token for token in bypass if token in source]

    assert reads("unchanged_since_escalation") == []
    assert reads("same_repository") == []
    assert reads("run_story")                               # the control


def test_the_guards_only_inputs_are_its_arguments(tmp_path):
    """The same claim structurally: every name `same_repository` calls is one
    the module defines or one of its own parameters.

    The control is the assertion's own machinery — `_git` is found as a called
    name, so a scan that had stopped seeing calls would fail here first.
    """
    source = function_source(COORDINATOR_PATH.read_text(encoding="utf-8"),
                             "same_repository")
    tree = ast.parse(source).body[0]
    called = {node.func.id for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}

    assert "_git" in called                                 # the control
    assert called <= {"_git"}


def test_the_story_did_not_move_where_the_harness_revision_is_recorded():
    """The constraint the story states it does not attempt: `_escalate`'s
    assignment stays where it is, as do the two escalation commits.

    Asserted at both ends of this story's range, so "unchanged" is bounded
    rather than read off today's working tree. The control is
    `unchanged_since_escalation`, compared the same way at the same two ends,
    which did change.
    """
    def escalate_source(bound: str) -> str:
        return function_source_at(COORDINATOR_REL, "_escalate",
                                  validation_file=Path(__file__), bound=bound,
                                  repo=REPO_ROOT)

    assert escalate_source(ENDPOINT) == escalate_source(BASELINE_BOUND)
    assert "state.harness_revision = _revision(harness_root)" \
        in escalate_source(ENDPOINT)

    guard_before = function_source_at(
        COORDINATOR_REL, "unchanged_since_escalation",
        validation_file=Path(__file__), bound=BASELINE_BOUND, repo=REPO_ROOT)
    guard_after = function_source_at(
        COORDINATOR_REL, "unchanged_since_escalation",
        validation_file=Path(__file__), bound=ENDPOINT, repo=REPO_ROOT)
    assert guard_after != guard_before                      # the control


def test_the_rejected_alternative_is_recorded_at_the_comparison():
    """The story's third task: why a tree hash was not taken is written where
    the next reader meets the comparison, not only in a document.

    The control is the same scan for a phrase the source does not carry, which
    finds nothing — so the assertion is reading the text rather than passing on
    any non-empty source.
    """
    source = function_source(COORDINATOR_PATH.read_text(encoding="utf-8"),
                             "unchanged_since_escalation")
    prose = "\n".join(line for line in source.splitlines()
                      if line.lstrip().startswith("#")) + source

    assert "tree hash" in prose
    assert "which directories" in prose
    assert "a phrase this source does not carry" not in prose   # the control
