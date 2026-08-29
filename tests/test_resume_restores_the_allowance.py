"""Independent validation for story-062: a resume restores the run's attempt
allowance and archives the entry it ends.

The subject is *what a second entry into a run does to the first one*, so
almost nothing here is asserted from source. A target repository is built under
tmp_path, fake stage agents drive it to its retry ceiling, and the coordinator
is then run again against the run directory the escalation left. What a resume
does is whatever that second run does to that directory.

The workflow these runs execute is built by the fixture in `tests/conftest.py`
rather than resolved out of what this repository deploys: the subject is the
resume branch, and the stage list is an input to it. The retry ceiling is the
fixture's own for the same reason — the story's motivating case is a run that
stopped *at* the ceiling, so the ceiling is a number this module sets rather
than one it inherits from what this repository happens to ship.

The motivating case is asserted at both ends, because a test that only states
the new behaviour says nothing about whether anything changed. The other end is
today's coordinator with the reset and the move taken out, loaded through the
shared mutation loader — a working-tree mutation, never a pinned revision.

Every absence asserted here carries a demonstration that the same check can
report the violation it exists to catch:

  * "a resumed run no longer escalates on the first failing verdict" sits
    beside the same run driven by the coordinator without the reset, which
    does escalate;
  * "an occupied entry directory refuses, moves nothing and resets nothing" is
    a byte-for-byte comparison of the whole run directory either side of the
    refusal, and sits beside the identical resume with the directory absent,
    which proceeds and does move things;
  * "the record was not rewritten" is a byte comparison against the record
    captured before the resume, and sits beside the same comparison over the
    records the resume legitimately appends to, which does differ;
  * "no name the moved set is built from is written into the coordinator" sits
    beside the same scan over that source with a name planted in it, which
    reports it;
  * "a moved artifact is no longer at the run root" sits beside the same check
    made before the move, where it is.

What is *not* asserted here is that the escalation reason strings are
unchanged. The suite already holds them verbatim — `test_escalation_summary`
names the ceiling reason outright, and two modules carry reason strings as
coordinator source excerpts — so a reason that moved reddens those rather than
going unreported, and a second reading of this repository's own history to say
the same thing would only make the answer depend on when the tree was last
committed, renamed or squashed.

Nothing here invokes a model: every run goes through a fake agent runner.
"""
import json
import subprocess
from pathlib import Path

import pytest

import conftest
from conftest import first_retry_route, function_source, load_mutant

import run_status
import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
COORDINATOR_REL = "orchestration/story_coordinator.py"
COORDINATOR_PATH = REPO_ROOT / COORDINATOR_REL

#: The ceiling these runs are held to, set here rather than read off what this
#: repository deploys. One retry is the smallest number that still produces
#: everything the move has to carry — an archived attempt, two rendered
#: attempts of the resumed stage and two verification iterations — and a
#: ceiling this module owns keeps a change to the shipped allowance from
#: reddening assertions that have nothing to say about it.
MAX_RETRIES = 1
RULES = {
    "max_retries": MAX_RETRIES,
    "require_verifier_pass": True,
    "blocked_paths": [".git/", ".harness/runs/", "rules/"],
}

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
    name="resume-allowance-workflow",
)
STAGES = WORKFLOW["stages"]
STAGE_NAMES = [stage["name"] for stage in STAGES]
WRITING, VERIFYING = STAGE_NAMES
#: The stage a run that stops at a verdict re-enters at, read off the
#: definition rather than written.
RESUMED_STAGE = VERIFYING
RETRY_CATEGORY, RETRY_STAGE = first_retry_route(WORKFLOW)

STORY_ID = "story-001"
DEFAULT_BRANCH = "main"
TESTS_DIR = "tests/"

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


def failing(marker: str, *, retry: bool) -> dict:
    """A failing verdict whose text names the attempt that produced it.

    Every verdict this module drives differs in its text, so an iteration file
    that has been written over by a later attempt is distinguishable from one
    that was not, and a comparison of two verdicts is never a comparison of two
    copies of one string.
    """
    return {
        "status": "failed",
        "blocking_issues": [{
            "severity": "high",
            "issue": f"{marker} did not implement the sample behavior",
            "location": f"src/{marker}.py",
            "required_behavior": f"the sample behavior exists after {marker}",
        }],
        "unverified": [],
        "retry_recommended": retry,
        "retry_target": RETRY_CATEGORY,
    }


STORY = f"""\
story:
  id: {STORY_ID}
  title: Sample story for resume tests
  description: |
    A stand-in story used to exercise the resume branch deterministically.

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
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: echo tests-ok
tests_dir: {TESTS_DIR}
"""

APP_AT_HEAD = "print('hello')\n"


# --------------------------------------------------------------------------
# A target repository and a fake runner
# --------------------------------------------------------------------------


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


def build_target(root: Path) -> Path:
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml", CONFIG)
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "src" / "app.py", APP_AT_HEAD)
    write(root / "tests" / "test_existing.py", "def test_nothing():\n    assert True\n")
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
    return build_target(tmp_path / "allowance-target")


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A harness root carrying the definition built above and this module's
    own execution rules, so every run below is a real coordinator loading real
    files — and the ceiling it stops at is the fixture's."""
    root = conftest.materialize_workflow(WORKFLOW, tmp_path / "allowance-harness",
                                         rules=RULES)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root,
                   check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "harness"], cwd=root, check=True)
    return root


class Runner:
    """A fake agent runner: each stage writes the artifacts it declares, and
    the writing stage also edits the target's working tree.

    It records, at the entry to every stage, the prompt it was handed and the
    state file as it stood — which is how "the counters were reset *before* the
    first stage of the resumed entry ran" is checked as a fact about the run
    rather than about the state it happens to end in.

    Every artifact it writes carries the ordinal of the invocation that wrote
    it, so two renderings of one name are never two copies of one string.
    """

    def __init__(self, target_root: Path, verdicts: list | None = None):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.verdicts = verdicts or [PASS]
        self.calls: list[str] = []
        #: (stage, state.json as it stood when the stage was entered)
        self.states: list[tuple[str, dict]] = []
        #: (stage, the prompt text the stage was handed)
        self.prompts: list[tuple[str, str]] = []

    def state_at(self, stage: str, occurrence: int = 0) -> dict:
        return [state for name, state in self.states if name == stage][occurrence]

    def prompt_at(self, stage: str, occurrence: int = 0) -> str:
        return [text for name, text in self.prompts if name == stage][occurrence]

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None, max_budget_usd=None):
        self.calls.append(stage)
        self.prompts.append((stage, prompt))
        self.states.append((stage, json.loads(
            (self.run_dir / "state.json").read_text(encoding="utf-8"))))
        ordinal = len(self.calls)

        if stage == WRITING:
            write(self.target_root / "src" / "app.py",
                  APP_AT_HEAD + f"print('invocation {ordinal}')\n")
            write_json(self.run_dir / conftest.CHANGED_FILES,
                       {"modified": ["src/app.py"], "created": [], "deleted": []})
            write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY,
                  f"Implemented on invocation {ordinal}.\n")
        elif stage == VERIFYING:
            seen = self.calls.count(stage) - 1
            write_json(self.run_dir / conftest.VERIFICATION_RESULT,
                       self.verdicts[min(seen, len(self.verdicts) - 1)])
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path) -> Path:
    return target_root / ".harness" / "runs" / STORY_ID


def state_of(target_root: Path) -> dict:
    return json.loads(
        (run_dir_of(target_root) / "state.json").read_text(encoding="utf-8"))


def write_state(target_root: Path, **changes) -> None:
    """Rewrite state.json in place, the way an inspecting developer would."""
    path = run_dir_of(target_root) / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state.update(changes)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def events(target_root: Path) -> list[str]:
    log = (run_dir_of(target_root) / "events.log").read_text(encoding="utf-8")
    return [line.split("] ", 1)[1] for line in log.splitlines() if "] " in line]


def retry_records(target_root: Path) -> list[dict]:
    path = run_dir_of(target_root) / "retry-history.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def run(target_root: Path, harness: Path, verdicts: list | None = None,
        coordinator=story_coordinator) -> tuple[int, Runner]:
    runner = Runner(target_root, verdicts)
    code = coordinator.run_story(STORY_ID, harness, target_root, runner)
    return code, runner


#: Two failing verdicts against a ceiling of one retry: the run retries once
#: and then escalates with its allowance spent. The shape the story is about.
def ceiling_verdicts(entry: str) -> list[dict]:
    return [failing(f"{entry}-attempt-1", retry=True),
            failing(f"{entry}-attempt-2", retry=True)]


def escalate_at_the_ceiling(target_root: Path, harness: Path,
                            entry: str = "entry-1") -> Runner:
    code, runner = run(target_root, harness, ceiling_verdicts(entry))
    assert code == 2, "the shape was meant to escalate"
    state = state_of(target_root)
    assert state["status"] == "escalated"
    assert state["retry_count"] == MAX_RETRIES, "the allowance was meant to be spent"
    return runner


def ready_to_resume(target_root: Path, marker: str) -> None:
    """Do the two things a resume asks of a developer, as one named act.

    The tree is changed — so the unchanged-since-escalation guard has
    something to see — and then committed, so the dirty-tree pre-flight is
    satisfied. The marker makes each change distinct from the last, which is
    what lets one run be resumed more than once.
    """
    write(target_root / "src" / "app.py", APP_AT_HEAD + f"print('{marker}')\n")
    git(target_root, "add", "-A")
    git(target_root, "commit", "-q", "--allow-empty", "-m", f"decided: {marker}")


def resume(target_root: Path, harness: Path, verdicts: list | None = None,
           marker: str = "by hand", coordinator=story_coordinator):
    ready_to_resume(target_root, marker)
    return run(target_root, harness, verdicts, coordinator)


# --------------------------------------------------------------------------
# Reading a run directory
# --------------------------------------------------------------------------


def snapshot(run_dir: Path) -> dict[str, bytes]:
    """Every file under the run directory, by run-relative path, with its bytes.

    The comparison a refusal is held to and the evidence a move is held to are
    both about *content*, so this is what both read: a check that a path exists
    passes just as happily when the wrong file was preserved.
    """
    return {path.relative_to(run_dir).as_posix(): path.read_bytes()
            for path in sorted(run_dir.rglob("*")) if path.is_file()}


def under(entry: Path, run_dir: Path, contents: dict[str, bytes]) -> dict[str, bytes]:
    """The part of a snapshot that lies under `entry`, re-keyed run-relatively.

    A move is required to preserve each name's position *relative to the run
    directory*, so what is compared against the pre-move snapshot is the path
    with the entry directory taken off the front — and a move that flattened
    `verification/iteration-1.json` beside the entry would fail here rather
    than being read as a name that simply did not move.
    """
    prefix = entry.relative_to(run_dir).as_posix() + "/"
    return {name[len(prefix):]: content for name, content in contents.items()
            if name.startswith(prefix)}


def iteration_files(run_dir: Path) -> list[str]:
    """The verification iterations at the run root, oldest name first.

    Globbed rather than spelled: the iteration filename is the harness's, and
    the assertions below are about which of them a resumed entry lands on
    rather than about how the name is shaped.
    """
    return sorted(path.name for path in run_dir.glob("verification/*.json"))


#: The half of a run directory that accounts for the whole run. Every one of
#: these is named by the story as staying at the root across a resume, and none
#: of them is keyed by a counter this story resets.
RECORDS = ("retry-history.json", "execution-history.json", "events.log",
           "state.json", "escalation-summary.md")


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
        kept.append(line)
    return "\n".join(kept)


# --------------------------------------------------------------------------
# The motivating case, at both ends
#
# A run that escalated at the retry ceiling and is then resumed takes a retry
# on its first failing verdict rather than escalating. Stating only that would
# say nothing about whether anything changed, so the other end is stated too:
# the same run, driven by today's coordinator with the reset and the move taken
# out, escalates a second time on that same verdict.
# --------------------------------------------------------------------------


#: The reset and the move, as they stand in the resume branch. Taking them out
#: leaves the branch the story found: the counters carried forward and nothing
#: archived. `load_mutant` fails if the anchor has moved, so this cannot
#: silently become a mutation that changes nothing.
THE_RESET = """\
        moved = move_entry_artifacts(run_dir, entry_artifacts(run_dir, stages), opened)
        state.resume_count = entry
        state.retry_count = 0
        state.verification_iterations = 0
"""
WITHOUT_THE_RESET = """\
        moved = []
"""


def coordinator_carrying_the_counters_forward(tmp_path: Path):
    """Today's coordinator with the allowance left spent across a resume.

    A working-tree mutation, which is what a control demonstrating that an
    assertion can fail is allowed to be — nothing here is recovered out of this
    repository's history or executed from a pinned revision.
    """
    return load_mutant(COORDINATOR_PATH, [(THE_RESET, WITHOUT_THE_RESET)],
                       name="coordinator_before_story_062", tmp_path=tmp_path)


def test_a_run_escalated_at_the_ceiling_takes_a_retry_after_a_resume(
    target, harness_root,
):
    """The story's motivating case. The resumed run meets a failing verdict
    that recommends a retry, and takes it: the retry stage runs again and the
    run goes on to finish, rather than stopping at a ceiling that was already
    reached before the developer intervened."""
    escalate_at_the_ceiling(target, harness_root)

    code, resumed = resume(
        target, harness_root,
        verdicts=[failing("entry-2-attempt-1", retry=True), PASS])

    assert code == 0
    assert resumed.calls == [RESUMED_STAGE, RETRY_STAGE, VERIFYING]
    assert state_of(target)["status"] == "completed"


def test_before_this_story_the_same_resume_escalated_a_second_time(
    target, harness_root, tmp_path,
):
    """The other end of the criterion, and the control for the test above.

    The identical run and the identical verdict, driven by the coordinator
    without the reset: the allowance is still spent when the resumed verifier
    reports, so the verdict that is retried above escalates here instead, and
    the retry stage is never reached.
    """
    escalate_at_the_ceiling(target, harness_root)
    before = coordinator_carrying_the_counters_forward(tmp_path)

    code, resumed = resume(
        target, harness_root,
        verdicts=[failing("entry-2-attempt-1", retry=True), PASS],
        coordinator=before)

    assert code == 2
    assert resumed.calls == [RESUMED_STAGE]
    assert state_of(target)["status"] == "escalated"
    assert state_of(target)["retry_count"] == MAX_RETRIES


# --------------------------------------------------------------------------
# The state the resumed entry starts from
# --------------------------------------------------------------------------


def crash(target_root: Path) -> None:
    """Leave the run recorded as `running`, the way a dead process leaves it."""
    write_state(target_root, status="running")


@pytest.mark.parametrize("crashed", [False, True],
                         ids=["escalated", "crashed"])
def test_the_counters_are_zero_in_the_state_the_first_resumed_stage_reads(
    target, harness_root, crashed,
):
    """Read at the entry to the resumed entry's first stage rather than off
    the state the run ends in: what the story requires is that the allowance is
    restored *before* the stage is invoked, and a state written afterwards
    would satisfy an end-state assertion while the stage ran with the old
    counters.

    Both recorded statuses drive it, because a resume decides from the status
    and from nothing else.
    """
    escalate_at_the_ceiling(target, harness_root)
    escalated = state_of(target)
    assert escalated["verification_iterations"] > 0
    if crashed:
        crash(target)

    code, resumed = resume(target, harness_root, verdicts=[PASS])

    assert code == 0
    at_entry = resumed.state_at(RESUMED_STAGE)
    assert at_entry["retry_count"] == 0
    assert at_entry["verification_iterations"] == 0
    assert at_entry["resume_count"] == escalated["resume_count"] + 1


def test_self_route_count_is_left_where_story_036_put_it(target, harness_root):
    """The third counter needs nothing from this story: it was already zeroed
    where a stage is entered other than by a self-route. Stated so that the
    three counters agreeing is checked rather than assumed."""
    escalate_at_the_ceiling(target, harness_root)
    code, resumed = resume(target, harness_root, verdicts=[PASS])

    assert code == 0
    assert resumed.state_at(RESUMED_STAGE)["self_route_count"] == 0


def test_a_state_file_written_before_this_story_resumes_as_the_first_entry(
    target, harness_root,
):
    """A run escalated before this story landed has no `resume_count` in its
    state file. It must load — reading as the first entry — rather than failing
    to parse, so the story does not strand the runs it was written for.

    The control is a field no `RunState` declares, dropped into the same file:
    that one does fail to load, so the tolerance above is the default doing its
    work rather than the loader ignoring what it is given.
    """
    escalate_at_the_ceiling(target, harness_root)
    run_dir = run_dir_of(target)
    path = run_dir / "state.json"
    written_before = {key: value
                      for key, value in json.loads(path.read_text()).items()
                      if key != "resume_count"}
    path.write_text(json.dumps(written_before, indent=2) + "\n", encoding="utf-8")

    assert story_coordinator.load_state(run_dir).resume_count == 0

    code, _ = resume(target, harness_root, verdicts=[PASS])
    assert code == 0
    assert state_of(target)["resume_count"] == 1

    with pytest.raises(TypeError):
        story_coordinator.RunState(**{**written_before, "a_field_nobody_declares": 1})


# --------------------------------------------------------------------------
# What moves into the entry, and what does not
# --------------------------------------------------------------------------


def plant_self_route_evidence(run_dir: Path, stage: str, attempt: int) -> list[str]:
    """The names a self-route leaves behind, written by hand into the run.

    A run that self-routed and later escalated is a run this module would
    otherwise have to drive a mechanical failure to produce, and the subject
    here is what the *move* does with those names rather than how they came to
    be written. Both are derived from the functions that write them, with the
    try number the writing side uses, so a test never spells one.
    """
    names = [story_coordinator.prompt_file(stage, attempt, 1),
             story_coordinator.self_route_result_file(stage, attempt, 1)]
    for name in names:
        write(run_dir / name, f"planted {name}\n")
    return names


def test_the_counter_keyed_artifacts_move_into_the_entry_byte_for_byte(
    target, harness_root,
):
    """The move's central guarantee, compared against content captured before
    the resume rather than against a path existing.

    Everything the escalated entry wrote under a counter-keyed name is under
    the entry directory afterwards with the bytes it had, at the same position
    relative to the run directory — the rendered prompts of both attempts, the
    try-suffixed prompt and self-route record a self-routing attempt left, both
    verification iterations, and the archived attempt directory.
    """
    escalate_at_the_ceiling(target, harness_root)
    run_dir = run_dir_of(target)
    planted = plant_self_route_evidence(run_dir, RESUMED_STAGE, MAX_RETRIES + 1)
    before = snapshot(run_dir)
    iterations = iteration_files(run_dir)
    assert len(iterations) > 1, "the shape was meant to take more than one verification"

    code, _ = resume(target, harness_root, verdicts=[PASS])
    assert code == 0

    entry = story_coordinator.entry_dir(run_dir, 1)
    archived = under(entry, run_dir, snapshot(run_dir))
    assert archived, "the resume archived nothing at all"
    # Everything the entry had before the resume, at the bytes it had. What
    # the entry gained during the resume is the interrupted attempt's own
    # archive, which story-061 wrote and which has its own test below; it is
    # not in the snapshot because it did not exist when the snapshot was taken.
    moved = {name: content for name, content in archived.items() if name in before}
    assert moved, "nothing that existed before the resume is under the entry"
    for name, content in moved.items():
        assert content == before[name], name

    # The names the criterion lists, each derived from the function that wrote
    # it rather than spelled here.
    expected = set(planted)
    expected.update(story_coordinator.prompt_file(RESUMED_STAGE, attempt)
                    for attempt in range(1, MAX_RETRIES + 2))
    expected.update(f"verification/{name}" for name in iterations)
    assert expected <= set(archived), expected - set(archived)
    attempts = story_coordinator.attempt_dir(run_dir, 1).parent.name
    assert any(name.startswith(f"{attempts}/") for name in archived), archived


def test_the_entry_is_kept_under_the_directory_the_story_named(
    target, harness_root,
):
    """The layout itself, stated once and literally.

    Everywhere else this module asks the coordinator where an entry's evidence
    went, which is right: a test that re-derives a name is a second copy of it.
    But *which* directory a re-entry opens is a decision this story made and
    recorded — `resumes/resume-K/`, K the index of the entry that ended — and a
    decision nothing states is a decision that can be changed without anyone
    noticing.
    """
    escalate_at_the_ceiling(target, harness_root)
    run_dir = run_dir_of(target)

    code, _ = resume(target, harness_root, verdicts=[PASS])

    assert code == 0
    assert (run_dir / "resumes" / "resume-1").is_dir()
    assert story_coordinator.entry_dir(run_dir, 1) == run_dir / "resumes" / "resume-1"


def test_a_moved_artifact_is_gone_from_the_run_root(target, harness_root):
    """A move rather than a copy, for the name the resumed entry does not
    re-land on: the interrupted attempt's number is above the restored
    allowance, so nothing writes it again.

    The control is the same check made before the resume, where the file is at
    the root — so the absence afterwards is the move having happened rather
    than a check looking somewhere nothing was ever written.
    """
    escalate_at_the_ceiling(target, harness_root)
    run_dir = run_dir_of(target)
    interrupted = story_coordinator.prompt_file(
        RESUMED_STAGE, state_of(target)["retry_count"] + 1)
    assert (run_dir / interrupted).is_file()

    code, _ = resume(target, harness_root, verdicts=[PASS])

    assert code == 0
    assert not (run_dir / interrupted).exists()
    assert (story_coordinator.entry_dir(run_dir, 1) / interrupted).is_file()


def test_the_records_stay_at_the_root_and_account_for_both_sides(
    target, harness_root,
):
    """The half that never moves, and the reason it must not: these account for
    the whole run, and a run's account of itself cannot be filed under one of
    its entries.

    The retry records are compared field for field against the ones captured
    before the resume, so "no record was rewritten" is a statement about
    content. The control is the same list read after the resume added to it,
    which does differ — so the equality above is not a comparison of two
    readings of an empty file.
    """
    escalate_at_the_ceiling(target, harness_root)
    run_dir = run_dir_of(target)
    entry_1_records = retry_records(target)
    assert entry_1_records, "the shape was meant to take a retry"

    code, _ = resume(target, harness_root,
                     verdicts=[failing("entry-2-attempt-1", retry=True), PASS])
    assert code == 0

    entry = story_coordinator.entry_dir(run_dir, 1)
    for name in RECORDS:
        assert (run_dir / name).is_file(), name
        assert not (entry / name).exists(), name

    after = retry_records(target)
    assert after[:len(entry_1_records)] == entry_1_records
    assert len(after) > len(entry_1_records), "the resumed entry took a retry too"

    # The other record spans the seam too: the resumed stage is recorded as
    # having started on both sides of the event that marks the re-entry.
    history = json.loads(
        (run_dir / "execution-history.json").read_text(encoding="utf-8"))
    seam = next(index for index, record in enumerate(history)
                if record["event"] == "resumed")
    started = [index for index, record in enumerate(history)
               if record.get("stage") == RESUMED_STAGE]
    assert [index for index in started if index < seam], history
    assert [index for index in started if index > seam], history


def test_the_inputs_the_resumed_stage_reads_are_unmoved_and_unmodified(
    target, harness_root,
):
    """A resume re-enters at the stage the run stopped at, and that stage
    assembles its context from artifacts an earlier stage wrote. None of them
    is keyed by a counter, so none of them moves — asserted by the bytes at the
    root and by the prompt the resumed stage was actually handed, which is
    where a context that had lost them would show it.
    """
    escalate_at_the_ceiling(target, harness_root)
    run_dir = run_dir_of(target)
    inputs = {name: (run_dir / name).read_bytes()
              for name in (conftest.CHANGED_FILES,
                           conftest.IMPLEMENTATION_SUMMARY)}
    summary_text = inputs[conftest.IMPLEMENTATION_SUMMARY].decode("utf-8").strip()

    code, resumed = resume(target, harness_root, verdicts=[PASS])
    assert code == 0

    entry = story_coordinator.entry_dir(run_dir, 1)
    for name, content in inputs.items():
        assert (run_dir / name).read_bytes() == content, name
        assert not (entry / name).exists(), name
    assert summary_text in resumed.prompt_at(RESUMED_STAGE)


# --------------------------------------------------------------------------
# The resumed entry's own writing, beside the entry it archived
# --------------------------------------------------------------------------


def test_the_resumed_stage_renders_attempt_one_beside_the_archived_rendering(
    target, harness_root,
):
    """Both copies are asserted, and they are asserted to differ.

    Asserting only that the archived prompt equals what was captured would pass
    if the resume had copied the fresh rendering back over itself, and
    asserting only that a fresh prompt exists would pass if it were the
    escalated entry's own. So: the archived copy carries the bytes it had, the
    root carries a rendering under the restored allowance's attempt number, and
    the two are not the same text.
    """
    escalate_at_the_ceiling(target, harness_root)
    run_dir = run_dir_of(target)
    first = story_coordinator.prompt_file(RESUMED_STAGE, 1)
    escalated_rendering = (run_dir / first).read_bytes()

    code, _ = resume(target, harness_root, verdicts=[PASS])
    assert code == 0

    archived = story_coordinator.entry_dir(run_dir, 1) / first
    assert archived.read_bytes() == escalated_rendering
    assert (run_dir / first).is_file()
    assert (run_dir / first).read_bytes() != escalated_rendering


def test_the_resumed_verification_lands_on_the_first_iteration_again(
    target, harness_root,
):
    """`verification_iterations` behaves as `retry_count` does: it is reset,
    and what the reset would have written over is under the entry with the
    verdict it held. The two files share a name and differ in content, which is
    the whole hazard the entry directory exists to remove."""
    escalate_at_the_ceiling(target, harness_root)
    run_dir = run_dir_of(target)
    first = iteration_files(run_dir)[0]
    escalated_verdict = (run_dir / "verification" / first).read_bytes()
    assert json.loads(escalated_verdict)["status"] == "failed"

    code, _ = resume(target, harness_root, verdicts=[PASS])
    assert code == 0

    assert iteration_files(run_dir) == [first]
    assert json.loads(
        (run_dir / "verification" / first).read_text(encoding="utf-8")) == PASS
    archived = story_coordinator.entry_dir(run_dir, 1) / "verification" / first
    assert archived.read_bytes() == escalated_verdict


# --------------------------------------------------------------------------
# The interrupted attempt story-061 archives, and where it comes to rest
# --------------------------------------------------------------------------


@pytest.mark.parametrize("crashed", [False, True],
                         ids=["escalated", "crashed"])
def test_the_interrupted_attempt_is_archived_under_the_counters_as_they_stood(
    target, harness_root, crashed,
):
    """story-061's archive is not what this story changes, so it is restated
    here on both resumed statuses: the attempt it archives is numbered by the
    counters *before* the reset, and its contents are what that attempt wrote.

    What this story does change is where the archive comes to rest — it travels
    into the entry with the rest of the attempts directory — so that is stated
    as the guarantee that now holds, with the run root asserted to hold no
    attempt of the escalated entry any more.
    """
    escalate_at_the_ceiling(target, harness_root)
    run_dir = run_dir_of(target)
    interrupted = state_of(target)["retry_count"] + 1
    verdict = (run_dir / conftest.VERIFICATION_RESULT).read_bytes()
    rendering = (run_dir / story_coordinator.prompt_file(
        RESUMED_STAGE, interrupted)).read_bytes()
    if crashed:
        crash(target)

    code, _ = resume(target, harness_root, verdicts=[PASS])
    assert code == 0

    entry = story_coordinator.entry_dir(run_dir, 1)
    archived = entry / story_coordinator.attempt_dir(
        run_dir, interrupted).relative_to(run_dir)
    assert (archived / conftest.VERIFICATION_RESULT).read_bytes() == verdict
    assert (archived / story_coordinator.prompt_file(
        RESUMED_STAGE, interrupted)).read_bytes() == rendering
    assert not story_coordinator.attempt_dir(run_dir, interrupted).exists()


# --------------------------------------------------------------------------
# The refusal
# --------------------------------------------------------------------------


def test_a_resume_onto_an_occupied_entry_refuses_and_writes_nothing(
    target, harness_root, capsys,
):
    """The refusal, compared by content over the whole run directory: a resume
    that would open an entry over an existing one exits non-zero, invokes no
    stage, moves nothing and resets nothing.

    The control is the same resume with the directory absent, which proceeds
    and does move things — so the byte-for-byte equality above is a refusal
    having happened rather than a resume that had nothing to do.
    """
    escalate_at_the_ceiling(target, harness_root)
    run_dir = run_dir_of(target)
    occupied = story_coordinator.entry_dir(run_dir, 1)
    occupied.mkdir(parents=True)
    write(occupied / "an-earlier-entry.txt", "kept\n")
    before = snapshot(run_dir)

    ready_to_resume(target, "the developer's change")
    refused = Runner(target, [PASS])
    code = story_coordinator.run_story(STORY_ID, harness_root, target, refused)

    assert code == 1
    assert refused.calls == []
    assert snapshot(run_dir) == before
    assert state_of(target)["retry_count"] == MAX_RETRIES
    assert str(occupied) in capsys.readouterr().err

    # The control: the same run with the entry directory cleared away resumes,
    # and the directory it opens is not empty. Clearing it is committed, since
    # removing a tracked file is itself a change the dirty-tree pre-flight is
    # entitled to refuse — a different refusal from the one under test.
    (occupied / "an-earlier-entry.txt").unlink()
    occupied.rmdir()
    git(target, "add", "-A")
    git(target, "commit", "-q", "-m", "cleared the entry directory")
    proceeding = Runner(target, [PASS])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, proceeding) == 0
    assert proceeding.calls == [RESUMED_STAGE]
    assert snapshot(run_dir) != before
    assert under(occupied, run_dir, snapshot(run_dir))


# --------------------------------------------------------------------------
# A second entry, and what the whole run has cost
# --------------------------------------------------------------------------


def escalate_twice(target_root: Path, harness: Path) -> int:
    """Drive the run to its ceiling, resume, and drive it there again.

    Returns the attempts actually taken, counted as the verifier invocations
    the fake runner recorded — a number this module observes rather than one it
    recomposes the way the coordinator does.
    """
    taken = escalate_at_the_ceiling(target_root, harness).calls.count(VERIFYING)
    ready_to_resume(target_root, "the first decision")
    code, second = run(target_root, harness, ceiling_verdicts("entry-2"))
    assert code == 2, "the second entry was meant to escalate too"
    return taken + second.calls.count(VERIFYING)


def test_a_second_resume_opens_a_second_entry_and_collides_with_neither(
    target, harness_root,
):
    """Two entries archived, each holding its own attempt's evidence, neither
    written over by the other and neither colliding with the entry now running.

    The first entry's contents are captured before the second resume and
    compared afterwards, so "the second resume did not disturb the first" is a
    statement about bytes.
    """
    escalate_twice(target, harness_root)
    run_dir = run_dir_of(target)
    first_entry = under(story_coordinator.entry_dir(run_dir, 1), run_dir,
                        snapshot(run_dir))
    assert first_entry

    code, _ = resume(target, harness_root, verdicts=[PASS],
                     marker="the second decision")
    assert code == 0

    after = snapshot(run_dir)
    second_entry = under(story_coordinator.entry_dir(run_dir, 2), run_dir, after)
    assert under(story_coordinator.entry_dir(run_dir, 1), run_dir, after) == first_entry
    assert second_entry
    assert state_of(target)["resume_count"] == 2
    # The entry now running is the run root, and it holds its own first
    # iteration rather than either archived entry's.
    assert json.loads((run_dir / "verification"
                       / iteration_files(run_dir)[0]).read_text()) == PASS


def test_the_accumulated_total_matches_the_attempts_the_run_actually_took(
    target, harness_root,
):
    """The number the summary and `l5-status` report, checked against attempts
    counted independently of the way the coordinator composes it: the verifier
    invocations the fake runner recorded across every entry of the run.

    Read on a run resumed more than once, because a total that agreed with the
    records only on the first entry would still be wrong on the second.
    """
    taken = escalate_twice(target, harness_root)
    ready_to_resume(target, "the second decision")
    code, third = run(target, harness_root, ceiling_verdicts("entry-3"))
    assert code == 2
    taken += third.calls.count(VERIFYING)

    run_dir = run_dir_of(target)
    state = story_coordinator.load_state(run_dir)
    assert state.resume_count == 2
    assert state.retry_count < taken, "the live counter is entry-scoped"
    assert story_coordinator.accumulated_attempts(run_dir, state) == taken

    detail = run_status.format_detail(target, STORY_ID)
    summary = (run_dir / "escalation-summary.md").read_text(encoding="utf-8")
    assert str(taken) in detail
    assert str(taken) in summary


# --------------------------------------------------------------------------
# The event that resolves a record written before the move
# --------------------------------------------------------------------------


def test_one_event_records_what_moved_and_where(target, harness_root):
    """A retry record written before the resume names its archive relative to
    the run directory, and the move puts that archive one directory further
    down. The record is evidence and is not rewritten, so the log is what
    resolves it: the event names the entry directory and the names that went
    into it, and the record's stale path resolves against that entry.

    The control is the record itself, compared byte for byte either side of the
    resume — an event that resolved a path by editing the record would fail it.
    """
    escalate_at_the_ceiling(target, harness_root)
    run_dir = run_dir_of(target)
    record = retry_records(target)[0]
    stale = record["archive_directory"]
    assert (run_dir / stale).is_dir()

    code, _ = resume(target, harness_root, verdicts=[PASS])
    assert code == 0

    entry = story_coordinator.entry_dir(run_dir, 1)
    assert retry_records(target)[0] == record
    assert not (run_dir / stale).exists()
    assert (entry / stale).is_dir()

    naming = [line for line in events(target)
              if entry.relative_to(run_dir).as_posix() in line]
    assert len(naming) == 1, naming
    assert Path(stale).parts[0] in naming[0]


# --------------------------------------------------------------------------
# A re-run is unaffected
# --------------------------------------------------------------------------


def test_deleting_the_run_directory_still_starts_from_nothing(
    target, harness_root,
):
    """A re-run agrees with this story by construction, and that is asserted
    rather than assumed: no entry directory, no carried counters, and the first
    entry's index."""
    escalate_at_the_ceiling(target, harness_root)
    run_dir = run_dir_of(target)
    subprocess.run(["rm", "-rf", str(run_dir)], check=True)
    git(target, "add", "-A")
    git(target, "commit", "-q", "--allow-empty", "-m", "cleared the run")

    code, rerun = run(target, harness_root, verdicts=[PASS])

    assert code == 0
    assert rerun.calls[0] == WRITING
    at_entry = rerun.state_at(WRITING)
    assert at_entry["resume_count"] == 0
    assert at_entry["retry_count"] == 0
    assert at_entry["verification_iterations"] == 0
    assert not (run_dir / "resumes").exists()


# --------------------------------------------------------------------------
# The moved set is derived, not named
# --------------------------------------------------------------------------


#: The functions this story added to the coordinator, which between them decide
#: what an entry takes with it and where it goes.
ADDED_FUNCTIONS = ("entry_dir", "entry_artifacts", "move_entry_artifacts",
                   "accumulated_attempts")


def names_written(source: str, names: list[str]) -> list[str]:
    """The given names that appear as literals in executable source."""
    return sorted({name for name in names if name in source})


def test_the_moved_set_is_derived_from_the_workflow_rather_than_named(
    target, harness_root,
):
    """The discovery is run against a workflow whose stage names this
    repository does not deploy, with the counter-keyed names planted through
    the functions that write them: it finds them, so nothing about the set is
    keyed to a name the coordinator knows in advance.

    The control is the artifact the same run directory holds that is *not*
    keyed by a counter — the writing stage's own output, which a resumed stage
    reads as input — which the same call must not collect.
    """
    escalate_at_the_ceiling(target, harness_root)
    run_dir = run_dir_of(target)
    planted = plant_self_route_evidence(run_dir, WRITING, 1)

    collected = story_coordinator.entry_artifacts(run_dir, STAGES)

    assert set(planted) <= set(collected), planted
    assert story_coordinator.prompt_file(RESUMED_STAGE, 1) in collected
    assert conftest.CHANGED_FILES not in collected
    assert conftest.IMPLEMENTATION_SUMMARY not in collected


def test_no_stage_or_artifact_name_is_written_into_the_added_source(
    harness_root,
):
    """The same fact read off the source, so a name that reached the
    coordinator without changing what this module's own runs do is still
    reported. Prose may name what code may not, so docstrings and comments are
    stripped before the reading.

    The control is the same scan over the same source with a stage name and an
    artifact name planted in it, which reports both — so the empty result above
    is a reading that can see one.
    """
    text = COORDINATOR_PATH.read_text(encoding="utf-8")
    added = executable_source(
        "\n".join(function_source(text, name) for name in ADDED_FUNCTIONS))
    forbidden = [f'"{name}"' for name in STAGE_NAMES] + [
        f'"{conftest.CHANGED_FILES}"', f'"{conftest.VERIFICATION_RESULT}"']

    assert names_written(added, forbidden) == []

    planted = added + (f'\ndef planted():\n    return ["{STAGE_NAMES[0]}", '
                       f'"{conftest.CHANGED_FILES}"]\n')
    assert names_written(planted, forbidden) == sorted(
        {f'"{STAGE_NAMES[0]}"', f'"{conftest.CHANGED_FILES}"'})
