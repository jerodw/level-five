"""story-165 validation: two runs in flight at once merge without contending
for the history a third one wrote.

Every run used to append its cross-run records to the same tracked files
under the history directory, so two story branches that both ran added
different lines at the same end of the same file and every merge between them
conflicted. This story moves where a record is written and nothing about what
it says: a record goes to a segment named for the writer that produced it —
the story's id for a run's records and for the mandate record l5-plan writes,
a moment for a broad-mode inspection, which has no story to name — and a log
is read as the root-level file, if present, followed by every segment's file
of that name.

What this module validates:

  * two stories completed from one base on two branches, each carrying a
    mandate record and a run's records, merge with git reporting no conflict,
    and the gathered logs of the merged tree hold every record either wrote;
  * the merge check is read off git's own exit status and index, and reports
    the contention the layout removes when two branches append to one file;
  * a run's records land under the story's own segment and nothing is written
    at the root of the history directory;
  * the mandate record goes to the story's segment and `commit_records` stages
    exactly that path;
  * a broad-mode inspection writes to a segment named for the moment rather
    than for a story, and stages exactly that path;
  * records already at the root are gathered and pruned exactly as a segment's
    are, and a run leaves the root-level file byte for byte as it found it;
  * the prune drops from whichever file holds the record and rewrites only
    those, rewrites nothing with the bound unset, and refuses on a malformed
    line in a segment naming the file and line with nothing rewritten — and
    since story-168 the read it drops by is the run's own startup read, which
    counts exactly what the prune drops and rewrites nothing;
  * two stories completed from one base under a configured retention bound,
    with records past the bound planted at the root and in an earlier story's
    segment, merge with no conflict and the merged history holds every
    planted record — the contention story-165 left open, which story-168
    closed by moving the rewrite out of the run and into `l5-prune`;
  * the reader-side resolver names the root-level file first and the segments
    in sorted order, and names only files that exist.

The workflow these runs execute is built by `conftest.build_workflow` and the
rule set is this module's own: what two runs' histories do on a merge is the
subject, and the stage list is an input to it. Nothing invokes a model; every
run and the post-story inspection go through a fake runner. Nothing resolves a
baseline out of this repository's commit graph: every history read here is
built by this module, in a repository this module built.

Every absence asserted here — no conflict, nothing at the root, nothing
rewritten — sits beside a demonstration that the same reader reports the
violation it exists to catch.
"""
import json
import subprocess
import time
from pathlib import Path

import pytest

import agent_runner
import conftest
import harness_config
import inspection
import plan_mandate
import schema_validator
import story_coordinator
import story_ids
import story_inspection
from agent_runner import AgentResult


# --------------------------------------------------------------------------
# The declaration, read from the schema, so nothing below writes a log name
# --------------------------------------------------------------------------

SCHEMA = schema_validator.load_schema(story_coordinator.CROSS_RUN_HISTORY_SCHEMA)
DECLARATIONS = {name: log["items"] for name, log in SCHEMA["properties"].items()}
EVENT = story_coordinator.HISTORY_EVENT_PROPERTY

#: The logs told apart by what their declarations carry rather than by name.
OUTCOME_LOG = next(name for name, shape in DECLARATIONS.items()
                   if "status" in shape["properties"])
ROUTING_LOG = next(name for name, shape in DECLARATIONS.items()
                   if "retry_decision" in shape["properties"])
CONFERRAL_LOG = next(name for name, shape in DECLARATIONS.items()
                     if "conferred_by" in shape["properties"])
INSPECTION_LOG = next(name for name, shape in DECLARATIONS.items()
                      if "findings" in shape["properties"])
#: Since story-219, told apart by the rejection only a decision log's record
#: carries. A run reaches it only by deciding something — a repair pass
#: carrying a decision — so a run that decides nothing reaches every log a run
#: can produce but this one, the way a run that does not complete reaches
#: every log but the inspection's.
DECISION_LOG = next(name for name, shape in DECLARATIONS.items()
                    if "rejection" in shape["properties"])

#: Which logs a run reaches, derived from the run's own event declaration.
RUN_KINDS = set(
    schema_validator.load_schema("execution-history")["items"]["properties"]
    [EVENT]["enum"]
)
RUN_PRODUCED_LOGS = {
    log for log in DECLARATIONS
    if set(DECLARATIONS[log]["properties"][EVENT]["enum"]) & RUN_KINDS
}

#: Where a target keeps its history when it configures nothing, resolved
#: through the harness rather than written here.
HISTORY_DIR = harness_config.DEFAULT_HISTORY_DIR


# --------------------------------------------------------------------------
# The workflow, the rules and the two stories
# --------------------------------------------------------------------------

WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        retry_routing={"implementation-defect": {
            "stage": conftest.StageRef(0),
            "when": "the behaviour the story asked for is missing"}}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="two-runs-merge-workflow",
)

WRITING, VERIFYING = [stage["name"] for stage in WORKFLOW["stages"]]
RETRY_CATEGORY, _ = conftest.first_retry_route(WORKFLOW)

RULES = {
    "max_retries": 2,
    "require_verifier_pass": True,
    "blocked_paths": [".git/", ".harness/runs/", "rules/"],
}

FIRST_STORY = conftest.SAMPLE_STORY_ID
SECOND_STORY = "story-002"
STORIES = (FIRST_STORY, SECOND_STORY)

#: What the fixture allows one post-story inspection to take into scope, so a
#: completing run produces the inspection log as well as the outcome log.
INSPECTION_FILE_CAP = 20

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}

FAIL_ONCE = {
    "status": "failed",
    "blocking_issues": [{
        "severity": "high",
        "issue": "the first attempt did not implement the sample behavior",
        "location": "src/app.py",
        "required_behavior": "the sample behavior exists",
    }],
    "unverified": [],
    "retry_recommended": True,
    "retry_target": RETRY_CATEGORY,
}

#: A record older than any retention bound a test here sets, and one written
#: now, both in the format the logs are written in.
OLD_STAMP = "2020-01-01 00:00:00"
RETENTION_DAYS = 1


def now_stamp() -> str:
    return time.strftime(story_coordinator.HISTORY_TIMESTAMP_FORMAT)


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True)


def git_out(root: Path, *args: str) -> str:
    finished = git(root, *args)
    assert finished.returncode == 0, finished.stderr
    return finished.stdout


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def parse_records(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.fixture
def configured_workflow() -> str:
    return WORKFLOW["name"]


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    root = conftest.materialize_workflow(
        WORKFLOW, tmp_path / "two-runs-harness", rules=RULES)
    # The template the post-story inspection renders: an inspection is not a
    # stage, so the builder writes none for it, and all a run here needs is a
    # rendering that succeeds.
    (root / "prompts" / inspection.INSPECTOR_PROMPT).write_text(
        "# the template this fixture renders\n{{scope_paths}}\n",
        encoding="utf-8")
    return root


@pytest.fixture
def target_root(target_root: Path) -> Path:
    """The shared target carrying two stories and the post-story inspection.

    Both stories and the inspection keys are committed on the base, because
    what a run starts from is the base and each branch below is cut from it.
    """
    config = target_root / ".harness" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + f"{inspection.SOURCE_DIRS_KEY}:\n  - src/\n"
        + f"{story_inspection.MAX_FILES_KEY}: {INSPECTION_FILE_CAP}\n",
        encoding="utf-8")
    stories = target_root / ".harness" / "stories"
    (stories / f"{SECOND_STORY}.yaml").write_text(
        conftest.STORY.replace(f"id: {FIRST_STORY}", f"id: {SECOND_STORY}"),
        encoding="utf-8")
    conftest.commit_setup(target_root, "two stories and the inspection")
    return target_root


class NoFindings:
    """Stands in for `agent_runner.run_agent` for the post-story inspection:
    it reaches no model and finds nothing, and the record it leaves is what
    this module reads."""

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None, max_budget_usd=None, suite_command=None,
                 run_dir=None):
        artifact, _ = inspection.findings_paths(
            Path(cwd), harness_config.load_config(Path(cwd)))
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps({"findings": []}), encoding="utf-8")
        return AgentResult(ok=True, result_text="inspected")


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    monkeypatch.setattr(agent_runner, "run_agent", NoFindings())


class Runner:
    """A fake agent runner writing each stage's declared artifacts.

    `verdicts` is what the verifying stage says on each of its invocations, so
    a run handed a failure and then a pass takes one retry and reaches every
    log a run can reach.
    """

    def __init__(self, target_root: Path, story_id: str, verdicts: list[dict]):
        self.run_dir = conftest.run_dir_for(target_root, story_id)
        self.story_id = story_id
        self.verdicts = list(verdicts)
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None, max_budget_usd=None, suite_command=None,
                 run_dir=None):
        self.calls.append(stage)
        attempt = self.calls.count(WRITING)
        if stage == WRITING:
            # A file of the story's own rather than one both stories touch:
            # what the merge below is about is the history, and two stories
            # editing one source line would conflict on that line instead.
            (Path(cwd) / "src" / f"{self.story_id}.py").write_text(
                f"print('{self.story_id} attempt {attempt}')\n",
                encoding="utf-8")
            write_json(self.run_dir / conftest.CHANGED_FILES,
                       {"modified": [], "created": [f"src/{self.story_id}.py"],
                        "deleted": []})
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                f"Implemented {self.story_id} on attempt {attempt}.\n",
                encoding="utf-8")
        elif stage == VERIFYING:
            verdict = conftest.answering_guidance(
                self.verdicts.pop(0), self.run_dir)
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, verdict)
            if verdict["status"] == "failed":
                write_json(self.run_dir / conftest.RETRY_GUIDANCE, {
                    "current_focus": [{
                        "focus": f"guidance issued after attempt {attempt}",
                        "satisfied_when": "the next attempt closes what it names",
                    }],
                    "preserve_behavior": ["existing behavior"],
                    "retry_scope": [f"src/{self.story_id}.py"],
                })
        return AgentResult(ok=True, result_text=f"{stage} done")


# --------------------------------------------------------------------------
# Where a story's history is, resolved through the harness's own resolvers
# --------------------------------------------------------------------------


def run_root_of(target: Path, story_id: str) -> Path:
    return conftest.run_root_for(target, story_id)


def history_dir_of(tree: Path) -> Path:
    return harness_config.history_dir(tree, harness_config.load_config(tree))


def segment_of(tree: Path, story_id: str) -> Path:
    return harness_config.history_segment(history_dir_of(tree), story_id)


def gathered(tree: Path, log: str) -> list[dict]:
    """One log as the reader-side resolver gathers it in `tree`."""
    return [record
            for path in story_coordinator.history_log_files(
                history_dir_of(tree), log)
            for record in parse_records(path.read_text(encoding="utf-8"))]


def gathered_logs(tree: Path) -> dict[str, list[dict]]:
    return {log: gathered(tree, log) for log in DECLARATIONS}


def root_level_files(history_directory: Path) -> set[str]:
    """The names of the files at the root of the history directory.

    The one reader every "nothing was written at the root" assertion goes
    through, so the control below can show it seeing a planted one.
    """
    if not history_directory.is_dir():
        return set()
    return {path.name for path in history_directory.iterdir() if path.is_file()}


def branch_of(target: Path, story_id: str) -> str:
    return story_coordinator.story_branch(
        harness_config.load_config(target), story_id)


def files_changed_since(root: Path, base: str, revision: str) -> set[str]:
    return set(git_out(root, "diff", "--name-only", base, revision).split())


def merge_outcome(root: Path, branch: str) -> tuple[int, list[str]]:
    """Merge `branch` into the tree at `root` and say what git reported.

    The exit status of the merge and the paths git's index holds unmerged
    afterwards: both read off git rather than off whether anything raised, so
    a conflict is git's own report and not this module's inference.
    """
    merged = git(root, "merge", "--no-edit", branch)
    unmerged = sorted({line.split("\t", 1)[1]
                       for line in git_out(root, "ls-files", "-u").splitlines()
                       if "\t" in line})
    return merged.returncode, unmerged


def confer(tree: Path, story_id: str) -> plan_mandate.Commit:
    """Write and commit the mandate record l5-plan writes for `story_id`, in
    the story's own worktree, before the run — the order l5-plan leaves.

    The stamp is the run's own clock, in the format l5-plan stamps with, so
    the record stays inside the retention bound whenever the test runs: a
    literal calendar date here is overtaken by the bound as time passes, and
    the mandate then joins the planted OLD_STAMP records past it."""
    config = harness_config.load_config(tree)
    mandate = plan_mandate.Mandate(
        path=tree / ".harness" / "stories" / f"{story_id}.yaml",
        story_id=story_id,
        source_kind=conftest.MANDATE_SOURCE_KIND,
        conferred_by=conftest.MANDATE_IDENTITY,
        conferred_at=time.strftime(plan_mandate.TIMESTAMP_FORMAT),
        stamped=True,
        detail="",
    )
    directory = plan_mandate.record(tree, config, mandate)
    return plan_mandate.commit_records(tree, directory, [mandate])


def complete(target: Path, harness: Path, story_id: str,
             verdicts: list[dict]) -> Path:
    """Confer a mandate and complete one story on its own branch, from the
    base the target stands on. Returns the tree the run worked in."""
    tree = conftest.worktree_a_run_left(target, story_id)
    committed = confer(tree, story_id)
    assert committed.committed, committed.detail
    assert story_coordinator.run_story(
        story_id, harness, target, Runner(target, story_id, verdicts)) == 0
    return tree


@pytest.fixture
def two_completed(target_root, harness_root):
    """Both stories completed from one base: the first with one retry, so its
    branch carries every log a run can reach; the second straight through."""
    base = git_out(target_root, "rev-parse", "HEAD").strip()
    first = complete(target_root, harness_root, FIRST_STORY, [FAIL_ONCE, PASS])
    second = complete(target_root, harness_root, SECOND_STORY, [PASS])
    return target_root, base, first, second


# --------------------------------------------------------------------------
# The claim: two runs from one base merge with nothing to reconcile
# --------------------------------------------------------------------------


def test_two_runs_from_one_base_touch_no_history_file_in_common(two_completed):
    """Each branch's history commits name files the other branch never
    touched, which is what leaves a merge between them nothing to reconcile.

    Both intersections are asserted non-empty first, so the empty one is a
    disjointness rather than two branches that recorded nothing.
    """
    target, base, _, _ = two_completed
    touched = {
        story: {path for path in files_changed_since(
            target, base, branch_of(target, story))
            if path.startswith(HISTORY_DIR.rstrip("/") + "/")}
        for story in STORIES
    }
    for story in STORIES:
        assert touched[story], f"{story} committed no history at all"
        assert all(path.startswith(f"{HISTORY_DIR.rstrip('/')}/{story}/")
                   for path in touched[story]), touched[story]
    assert touched[FIRST_STORY] & touched[SECOND_STORY] == set()


def test_two_runs_from_one_base_merge_without_conflict_and_keep_both_histories(
        two_completed):
    """The story's whole subject.

    The second story's branch is merged into the first's, in a real repository,
    and git's own exit status and index say the merge had nothing to
    reconcile. The gathered logs of the merged tree then hold every record
    either branch wrote — the mandate record and the run's records of each —
    and hold nothing twice. The control for the no-conflict reading is the
    test below, where the same reader is shown two branches contending.
    """
    target, _, first, second = two_completed
    before = {story: gathered_logs(tree)
              for story, tree in ((FIRST_STORY, first), (SECOND_STORY, second))}
    for story in STORIES:
        assert [r["story_id"] for r in before[story][CONFERRAL_LOG]] == [story]
        assert {r["story_id"] for r in before[story][OUTCOME_LOG]} == {story}
    assert before[FIRST_STORY][ROUTING_LOG], "the first run took no retry"
    assert before[FIRST_STORY][INSPECTION_LOG], "the first run was not inspected"

    code, unmerged = merge_outcome(first, branch_of(target, SECOND_STORY))
    assert code == 0
    assert unmerged == []
    # The merge left nothing half-applied under the history directory. The
    # check is confined to that directory: the run directory is already dirty
    # in both worktrees before the merge, because the completion commit sweeps
    # it and the post-story inspection appends to it afterwards, and that is
    # state the merge did not create.
    assert git_out(first, "status", "--porcelain", "--untracked-files=no",
                   "--", HISTORY_DIR) == ""

    after = gathered_logs(first)
    for log in DECLARATIONS:
        expected = before[FIRST_STORY][log] + before[SECOND_STORY][log]
        assert len(after[log]) == len(expected), log
        for record in expected:
            assert record in after[log], (log, record)
    assert {r["story_id"] for r in after[CONFERRAL_LOG]} == set(STORIES)
    assert {r["story_id"] for r in after[OUTCOME_LOG]} == set(STORIES)


#: A story that ran before either of the two here, whose segment carries a
#: record past the bound; under the in-run prune both runs would have
#: rewritten its file.
EARLIER_STORY = "story-000"


def plant_past_the_bound(target: Path) -> dict[str, str]:
    """Configure a bound on the base and plant, at the root and in an earlier
    story's segment, one record past it and one not, committed on the base so
    both branches below are cut carrying them. Returns each planted file's
    text keyed by its path relative to the history directory."""
    config = target / ".harness" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + f"{story_coordinator.HISTORY_RETENTION_KEY}: {RETENTION_DAYS}\n",
        encoding="utf-8")
    fresh = now_stamp()
    planted = {
        OUTCOME_LOG: outcome_line(EARLIER_STORY, OLD_STAMP)
                     + outcome_line(EARLIER_STORY, fresh),
        f"{EARLIER_STORY}/{OUTCOME_LOG}": outcome_line(EARLIER_STORY, OLD_STAMP)
                                          + outcome_line(EARLIER_STORY, fresh),
    }
    directory = target / HISTORY_DIR
    for relative, text in planted.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    conftest.commit_setup(target, "a bound, and records past it")
    return planted


@pytest.fixture
def two_completed_under_a_bound(target_root, harness_root):
    planted = plant_past_the_bound(target_root)
    base = git_out(target_root, "rev-parse", "HEAD").strip()
    first = complete(target_root, harness_root, FIRST_STORY, [FAIL_ONCE, PASS])
    second = complete(target_root, harness_root, SECOND_STORY, [PASS])
    return target_root, base, first, second, planted


def test_two_runs_under_a_bound_merge_without_conflict_and_keep_every_planted_record(
        two_completed_under_a_bound):
    """The contention story-165 left open, closed.

    Both runs are under a configured bound and both start from files holding
    records past it, at the root and in an earlier story's segment. Neither
    run rewrites those files — each branch's commits touch only the run's own
    segment — so merging the second branch into the first has nothing to
    reconcile, read off git's exit status and an empty `ls-files -u`, and the
    merged history holds every planted record byte for byte and every record
    either run wrote, nothing twice. Each run reported what it counted rather
    than dropping it. The control for the no-conflict reading is
    `test_the_merge_check_reports_the_contention_the_layout_removes`, and the
    control for "reported" is the retention module's run with the bound unset.
    """
    target, base, first, second, planted = two_completed_under_a_bound
    history_prefix = HISTORY_DIR.rstrip("/") + "/"
    for story in STORIES:
        touched = {path for path in files_changed_since(
            target, base, branch_of(target, story))
            if path.startswith(history_prefix)}
        assert touched, f"{story} committed no history at all"
        assert all(path.startswith(f"{history_prefix}{story}/")
                   for path in touched), touched
        events = (conftest.run_dir_for(target, story) / "events.log").read_text(
            encoding="utf-8")
        notes = [line for line in events.splitlines()
                 if story_coordinator.HISTORY_RETENTION_KEY in line]
        assert len(notes) == 1, events
        assert story_coordinator.PRUNE_COMMAND in notes[0]
        for relative in planted:
            assert f"{relative}: 1" in notes[0], notes[0]

    before = {story: gathered_logs(tree)
              for story, tree in ((FIRST_STORY, first), (SECOND_STORY, second))}
    code, unmerged = merge_outcome(first, branch_of(target, SECOND_STORY))
    assert code == 0
    assert unmerged == []
    assert git_out(first, "status", "--porcelain", "--untracked-files=no",
                   "--", HISTORY_DIR) == ""

    for relative, text in planted.items():
        assert (first / HISTORY_DIR / relative).read_text(encoding="utf-8") == text
        assert git_out(first, "show", f"HEAD:{history_prefix}{relative}") == text

    after = gathered_logs(first)
    for log in DECLARATIONS:
        # Every record either branch holds, once: the planted records are in
        # both branches' gathers and in the merged one exactly once.
        planted_in = [r for r in before[FIRST_STORY][log]
                      if r["story_id"] == EARLIER_STORY]
        expected = before[FIRST_STORY][log] + [
            r for r in before[SECOND_STORY][log] if r["story_id"] != EARLIER_STORY]
        assert len(after[log]) == len(expected), log
        for record in expected:
            assert record in after[log], (log, record)
        assert [r for r in after[log] if r["story_id"] == EARLIER_STORY] == \
            planted_in, log
    assert [r["story_id"] for r in after[OUTCOME_LOG]
            if r["story_id"] == EARLIER_STORY] == [EARLIER_STORY] * 4
    assert {r["story_id"] for r in after[OUTCOME_LOG]} == \
        {EARLIER_STORY, *STORIES}
    # The control for "left alone": the merged tree still holds exactly the
    # records the bound would drop, so both runs had something to rewrite and
    # neither did.
    reading = story_coordinator.read_history(
        history_dir_of(first), harness_config.load_config(first))
    assert reading.past_bound == {relative: 1 for relative in planted}


def test_the_merge_check_reports_the_contention_the_layout_removes(tmp_path):
    """The control: two branches appending to one root-level log conflict.

    The layout this story replaced, built in a throwaway repository: a base
    holding one log, and two branches each appending a different line at its
    end. The same reader the assertion above trusts reports a non-zero exit
    and names the log as unmerged, so a green result above is git having
    nothing to reconcile rather than a reader that cannot see a conflict.
    """
    root = tmp_path / "contending"
    log = root / HISTORY_DIR / OUTCOME_LOG
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({"story_id": "story-000", "status": "completed",
                               "timestamp": OLD_STAMP}) + "\n",
                   encoding="utf-8")
    conftest.init_repository(root)
    base = conftest.BASE_BRANCH_FALLBACK
    for story in STORIES:
        git_out(root, "checkout", "-q", "-b", f"story/{story}", base)
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"story_id": story, "status": "completed",
                                     "timestamp": OLD_STAMP}) + "\n")
        git_out(root, "commit", "-q", "-am", f"{story} appended its record")
    git_out(root, "checkout", "-q", f"story/{FIRST_STORY}")

    code, unmerged = merge_outcome(root, f"story/{SECOND_STORY}")
    assert code != 0
    assert unmerged == [f"{HISTORY_DIR.rstrip('/')}/{OUTCOME_LOG}"]


# --------------------------------------------------------------------------
# Where each writer puts its record
# --------------------------------------------------------------------------


def test_a_run_writes_every_log_under_its_own_segment_and_nothing_at_the_root(
        two_completed):
    """A run of story X writes its records under `<history>/X/` and writes
    no root-level file. Asked of a run that reached every run-produced log a
    run that decides nothing reaches, so each of those logs is found in the
    segment; the decision log, which only a run that decided something
    reaches, is asked to be absent rather than skipped. The absence at the
    root has the control below.
    """
    _, _, first, _ = two_completed
    segment = segment_of(first, FIRST_STORY)
    assert segment.parent == history_dir_of(first)
    assert segment.name == FIRST_STORY
    assert DECISION_LOG in RUN_PRODUCED_LOGS
    for log in RUN_PRODUCED_LOGS - {DECISION_LOG}:
        records = parse_records((segment / log).read_text(encoding="utf-8"))
        assert records, log
        assert {r["story_id"] for r in records} == {FIRST_STORY}, log
    assert not (segment / DECISION_LOG).exists()
    assert root_level_files(history_dir_of(first)) == set()
    assert {path.name for path in history_dir_of(first).iterdir()} == \
        {FIRST_STORY}


def test_the_root_reader_sees_a_file_planted_there(tmp_path):
    """The control for the absence above: the same reader names a root-level
    file when one is there, so an empty set is the root being empty."""
    directory = tmp_path / "history"
    (directory / "a-segment").mkdir(parents=True)
    (directory / "a-segment" / OUTCOME_LOG).write_text("", encoding="utf-8")
    assert root_level_files(directory) == set()
    (directory / OUTCOME_LOG).write_text("", encoding="utf-8")
    assert root_level_files(directory) == {OUTCOME_LOG}


def test_the_mandate_record_goes_to_the_storys_segment_and_is_what_is_staged(
        target_root):
    """l5-plan's record for story X is written to `<history>/X/<log>` and
    `commit_records` stages exactly that path — what was written, read back
    out of the commit it made."""
    tree = conftest.worktree_a_run_left(target_root, SECOND_STORY)
    committed = confer(tree, SECOND_STORY)
    assert committed.committed, committed.detail

    expected = segment_of(tree, SECOND_STORY) / CONFERRAL_LOG
    assert committed.paths == (str(expected.relative_to(tree)),)
    assert expected.is_file()
    assert root_level_files(history_dir_of(tree)) == set()
    staged = git_out(tree, "show", "--name-only", "--format=", "HEAD").split()
    assert staged == list(committed.paths)
    assert [r["story_id"] for r in parse_records(
        git_out(tree, "show", f"HEAD:{committed.paths[0]}"))] == [SECOND_STORY]


def test_a_broad_mode_inspection_writes_to_a_moment_segment_and_stages_it(
        target_root):
    """An inspection with no story to name gets a segment named for when it
    ran: not a story id, carrying the moment prefix, and exactly what the
    commit it makes stages."""
    config = harness_config.load_config(target_root)
    head_before = git_out(target_root, "rev-parse", "HEAD").strip()
    inspection.record(target_root, config, inspection.Report())

    directory = history_dir_of(target_root)
    segments = [path for path in directory.iterdir() if path.is_dir()]
    assert len(segments) == 1, segments
    segment = segments[0]
    assert segment.name.startswith(harness_config.MOMENT_SEGMENT_PREFIX)
    assert story_ids.STORY_NUMBER.search(segment.name) is None
    assert root_level_files(directory) == set()
    records = parse_records((segment / INSPECTION_LOG).read_text(encoding="utf-8"))
    assert [r["mode"] for r in records] == [inspection.MODE_BROAD]

    assert git_out(target_root, "rev-parse", "HEAD").strip() != head_before
    assert git_out(target_root, "log", "-1", "--format=%s").strip() == \
        inspection.COMMIT_SUBJECT
    staged = git_out(target_root, "show", "--name-only", "--format=", "HEAD").split()
    assert staged == [str((segment / INSPECTION_LOG).relative_to(target_root))]
    assert staged == list(inspection.record_paths(
        target_root, config, segment=segment))


def test_record_paths_refuses_to_guess_a_segment_when_none_is_named(target_root):
    """A path staged by a guess is a path nothing wrote."""
    config = harness_config.load_config(target_root)
    with pytest.raises(ValueError):
        inspection.record_paths(target_root, config)
    # The run-side wrapper guards that refusal into an empty tuple, so a
    # defaulted story would stage nothing and say nothing; the story is
    # therefore required, and omitting it fails at the call site.
    with pytest.raises(TypeError):
        story_inspection.record_paths(target_root, config)
    # And the story-scoped derivation is the story's segment, so the run's
    # own producer stages what the run wrote — and only what it wrote: a
    # segment nothing has written to names no file, because `git add` of a
    # path that is not there refuses the whole set beside it.
    assert inspection.record_paths(target_root, config, story_id=FIRST_STORY) == ()
    assert story_inspection.record_paths(target_root, config, FIRST_STORY) == ()
    written = story_coordinator.append_history_records(
        history_dir_of(target_root),
        {"event": inspection.INSPECTION_EVENT, "timestamp": "2026-01-01 00:00:00"},
        FIRST_STORY, [],
    )
    expected = segment_of(target_root, FIRST_STORY) / INSPECTION_LOG
    assert [path for path in written] == [expected]
    assert inspection.record_paths(target_root, config, story_id=FIRST_STORY) == \
        (str(expected.relative_to(target_root)),)
    assert story_inspection.record_paths(target_root, config, FIRST_STORY) == \
        (str(expected.relative_to(target_root)),)


# --------------------------------------------------------------------------
# The root-level files: read as a segment is, and left as they were
# --------------------------------------------------------------------------


def planted_root_record(story_id: str = "story-000") -> str:
    return json.dumps({"story_id": story_id, "status": "completed",
                       "timestamp": OLD_STAMP}) + "\n"


def test_a_run_gathers_a_root_level_record_and_leaves_its_file_untouched(
        target_root, harness_root):
    """A record written before the segments existed, at the root, is read by
    the gather beside the run's own and the file holding it is byte for byte
    what the run found — in the tree and in the commit the run left."""
    planted = planted_root_record()
    root_log = target_root / HISTORY_DIR / OUTCOME_LOG
    root_log.parent.mkdir(parents=True)
    root_log.write_text(planted, encoding="utf-8")
    conftest.commit_setup(target_root, "a record written before the segments")

    tree = complete(target_root, harness_root, FIRST_STORY, [PASS])

    relative = root_log.relative_to(target_root).as_posix()
    assert (tree / relative).read_bytes() == planted.encode("utf-8")
    assert git_out(tree, "show", f"HEAD:{relative}") == planted
    assert root_level_files(history_dir_of(tree)) == {OUTCOME_LOG}

    records = gathered(tree, OUTCOME_LOG)
    assert records[0] == json.loads(planted)
    assert {r["story_id"] for r in records[1:]} == {FIRST_STORY}
    assert len(records) > 1


def test_the_reader_names_the_root_file_first_and_the_segments_in_sorted_order(
        tmp_path):
    """The reader-side resolver's whole contract, on a constructed directory:
    the root-level file, if present, then each segment's file in sorted
    segment order, and only files that exist."""
    directory = tmp_path / "history"
    for segment in ("story-010", "story-002", "at-2026-09-18T09-00-00-000000"):
        (directory / segment).mkdir(parents=True)
        (directory / segment / OUTCOME_LOG).write_text("", encoding="utf-8")
    # A segment holding no file of this name, and a file at the root under a
    # name that is a segment's, so neither can be picked up by mistake.
    (directory / "story-003").mkdir()
    (directory / "story-003" / ROUTING_LOG).write_text("", encoding="utf-8")

    assert story_coordinator.history_log_files(directory, OUTCOME_LOG) == [
        directory / "at-2026-09-18T09-00-00-000000" / OUTCOME_LOG,
        directory / "story-002" / OUTCOME_LOG,
        directory / "story-010" / OUTCOME_LOG,
    ]
    (directory / OUTCOME_LOG).write_text("", encoding="utf-8")
    assert story_coordinator.history_log_files(directory, OUTCOME_LOG)[0] == \
        directory / OUTCOME_LOG
    assert story_coordinator.history_log_files(directory, ROUTING_LOG) == [
        directory / "story-003" / ROUTING_LOG]
    assert story_coordinator.history_log_files(
        tmp_path / "nowhere", OUTCOME_LOG) == []


# --------------------------------------------------------------------------
# The prune over the root-level file and the segments
# --------------------------------------------------------------------------


def outcome_line(story_id: str, stamp: str) -> str:
    return json.dumps({"story_id": story_id, "status": "completed",
                       "timestamp": stamp}) + "\n"


def build_history(directory: Path) -> dict[str, bytes]:
    """A root-level file and two segments, with old records in the root and
    in the first segment only. Returns every file's bytes, keyed relative to
    the directory, so a test can say which ones changed."""
    fresh = now_stamp()
    files = {
        OUTCOME_LOG: outcome_line("story-000", OLD_STAMP)
                     + outcome_line("story-000", fresh),
        f"{FIRST_STORY}/{OUTCOME_LOG}": outcome_line(FIRST_STORY, OLD_STAMP)
                                        + outcome_line(FIRST_STORY, fresh),
        f"{SECOND_STORY}/{OUTCOME_LOG}": outcome_line(SECOND_STORY, fresh),
    }
    for relative, text in files.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return snapshot(directory)


def snapshot(directory: Path) -> dict[str, bytes]:
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def test_the_prune_drops_from_whichever_file_holds_the_record_and_rewrites_only_those(
        tmp_path):
    """The rewrite, held as a property of `prune_history` alone: since
    story-168 no run calls it. The read the run does make, `read_history`, is
    shown first counting exactly what the prune then drops — keyed the same
    way — and rewriting nothing."""
    directory = tmp_path / "history"
    before = build_history(directory)
    config = {story_coordinator.HISTORY_RETENTION_KEY: RETENTION_DAYS}

    reading = story_coordinator.read_history(directory, config)
    assert reading.problems == []
    assert reading.past_bound == {OUTCOME_LOG: 1, f"{FIRST_STORY}/{OUTCOME_LOG}": 1}
    assert snapshot(directory) == before

    pruned = story_coordinator.prune_history(directory, config)

    assert pruned.problems == []
    assert pruned.dropped == reading.past_bound
    assert story_coordinator.read_history(directory, config).past_bound == {}
    after = snapshot(directory)
    assert set(after) == set(before)
    for relative in pruned.dropped:
        assert after[relative] != before[relative], relative
        assert all(r["timestamp"] != OLD_STAMP
                   for r in parse_records(after[relative].decode("utf-8")))
        assert parse_records(after[relative].decode("utf-8")), relative
    untouched = set(before) - set(pruned.dropped)
    assert untouched == {f"{SECOND_STORY}/{OUTCOME_LOG}"}
    for relative in untouched:
        assert after[relative] == before[relative]


def test_the_prune_with_the_bound_unset_reads_every_file_and_rewrites_none(
        tmp_path):
    directory = tmp_path / "history"
    before = build_history(directory)
    reading = story_coordinator.read_history(directory, {})
    assert reading.problems == [] and reading.past_bound == {}
    assert reading.cutoff is None
    # Every file was read: the parsed lines the read hands the prune name each
    # file the resolver names, so "counted nothing" is the bound being unset
    # rather than nothing having been opened.
    assert {p.relative_to(directory).as_posix() for p in reading.files} == \
        set(before)
    pruned = story_coordinator.prune_history(directory, {})
    assert pruned == story_coordinator.HistoryPrune([], {})
    assert snapshot(directory) == before


def test_a_malformed_line_in_a_segment_refuses_naming_the_file_and_the_line(
        tmp_path):
    """The refusal names the segment file and the line, and nothing — not the
    root-level file with a record to drop, not the segment itself — is
    rewritten. The control is the dropping test above: the same directory
    without the malformed line does drop, so nothing dropped here is the
    refusal and not an empty prune."""
    directory = tmp_path / "history"
    build_history(directory)
    broken = directory / SECOND_STORY / OUTCOME_LOG
    with open(broken, "a", encoding="utf-8") as handle:
        handle.write("{this is not json\n")
    before = snapshot(directory)

    pruned = story_coordinator.prune_history(
        directory, {story_coordinator.HISTORY_RETENTION_KEY: RETENTION_DAYS})

    assert len(pruned.problems) == 1, pruned.problems
    assert pruned.problems[0].startswith(f"{broken}, line 2: ")
    assert pruned.dropped == {}
    assert snapshot(directory) == before
