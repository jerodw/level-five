"""Independent validation for story-070's third proxy: the suite census.

The census stands in for the ownership check and the revert check under a
workflow whose stages legitimately edit the validation. Its claim is narrow and
mechanical: the target prints an object of counter names to integers, the
coordinator takes that object in a clone at the stage's baseline and again in
the tree the stage left, and the run stops when a counter the baseline carried
is gone or smaller afterwards.

The subjects below are kept apart deliberately.

  * **The coordinator's comparison** is a mechanism, so the census it compares
    is an *input*. The runs below configure a census command this repository
    does not ship — one that counts marker files a stage can add, drop or
    lower at will — because "a counter disappeared", "a counter appeared" and
    "a counter fell" are properties of the comparison rather than of any
    particular way of counting a suite. The workflow those runs execute is
    built by `conftest.build_workflow` for the same reason.

  * **This repository's own census**, `.harness/census.py`, is a shipped
    artifact and *is* the subject of the cases that name pytest: a deleted
    test, a test newly skipped, a test newly xfailed, a removed assertion, a
    renamed test whose call sites were updated, and an assertion replaced by a
    weaker one. Each of those is done — to a repository built under tmp_path —
    and the shipped check is run over it, rather than being argued about.

Every absence asserted here carries a control that constructs the violation. "A
workflow declaring no census announces nothing" is a count against the same run
with the declaration restored; "no census command means no announcement" is
paired with the configured one; "the blind spot is stated" is paired with a text
that does not state it and the same predicate reporting so; "the schema is in no
stage's schemas map" is paired with the schema that is.

`.harness/docs/ARCHITECTURE.md` and `README.md` are not asserted on: this
story's plan assigns both to the documenter, which has not run when this module
is written.

Nothing here invokes a model, and nothing here resolves a baseline out of this
repository's commit graph.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
import harness_config
import schema_validator
import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The census this repository ships. A live harness artifact, and the subject
#: of every pytest-shaped case below.
SHIPPED_CENSUS = REPO_ROOT / ".harness" / "census.py"

SCHEMA_STEM = "census-result"
SCHEMA_PATH = REPO_ROOT / "schemas" / f"{SCHEMA_STEM}.schema.json"

STORY_ID = "story-001"

#: The directory the fixtures below keep their suite in, and the path the
#: declarations govern. One value, stated once: the target is built with its
#: tests here and the workflow is built to declare this same place, so
#: everything downstream derives the name from the fixture.
GOVERNED = "tests/"

#: The kind the coordinator appends before a check re-runs the configured test
#: command. An event kind is the coordinator's own vocabulary rather than a name
#: a workflow declares, so it is spelled here rather than read off a definition.
ANNOUNCEMENT = "suite-rerun-started"

PASS_VERDICT = {"status": "passed", "blocking_issues": [], "unverified": [],
                "retry_recommended": False}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


def init_repository(root: Path) -> None:
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")


# --------------------------------------------------------------------------
# Part one: this repository's own census, put to the changes it claims to see
#
# A suite small enough to count by hand, and one change per case made to it.
# The census parses rather than runs, so nothing here executes pytest: what is
# being decided is what the counters say, and the counters come from the source.
# --------------------------------------------------------------------------

HELPER_AT_HEAD = '''\
def double(value):
    return value * 2
'''

TEST_ALPHA_AT_HEAD = '''\
def test_addition():
    assert 1 + 1 == 2
    assert 2 + 2 == 4


def test_membership():
    assert "a" in "abc"
'''

TEST_BETA_AT_HEAD = '''\
from helper import double


def test_double():
    assert double(2) == 4
'''

#: What the shipped census reads off the suite above, counted by hand: three
#: test functions, none suppressed, and four assert statements.
CENSUS_AT_HEAD = {"unskipped_tests": 3, "assertions": 4}


@pytest.fixture
def counted_target(tmp_path: Path) -> Path:
    """A repository whose suite the shipped census can be run over."""
    root = tmp_path / "counted-target"
    write(root / "helper.py", HELPER_AT_HEAD)
    write(root / "tests" / "test_alpha.py", TEST_ALPHA_AT_HEAD)
    write(root / "tests" / "test_beta.py", TEST_BETA_AT_HEAD)
    write(root / ".gitignore", "__pycache__/\n")
    init_repository(root)
    return root


def shipped_census_command(directory: str = GOVERNED) -> str:
    """This repository's census, pointed at a directory inside the tree it runs in."""
    return shlex.join([sys.executable, str(SHIPPED_CENSUS), directory])


def delete_a_test(root: Path) -> None:
    """The whole second function removed."""
    write(root / "tests" / "test_alpha.py", '''\
def test_addition():
    assert 1 + 1 == 2
    assert 2 + 2 == 4
''')


def mark_a_test_skipped(root: Path) -> None:
    write(root / "tests" / "test_alpha.py", '''\
import pytest


def test_addition():
    assert 1 + 1 == 2
    assert 2 + 2 == 4


@pytest.mark.skip(reason="not today")
def test_membership():
    assert "a" in "abc"
''')


def mark_a_test_xfailed(root: Path) -> None:
    write(root / "tests" / "test_alpha.py", '''\
import pytest


def test_addition():
    assert 1 + 1 == 2
    assert 2 + 2 == 4


@pytest.mark.xfail(reason="known to fail")
def test_membership():
    assert "a" in "abc"
''')


def remove_an_assertion(root: Path) -> None:
    write(root / "tests" / "test_alpha.py", '''\
def test_addition():
    assert 1 + 1 == 2


def test_membership():
    assert "a" in "abc"
''')


def rename_a_test_and_its_call_sites(root: Path) -> None:
    """The case that motivated the whole thread: a rename, carried through."""
    write(root / "helper.py", '''\
def doubled(value):
    return value * 2
''')
    write(root / "tests" / "test_beta.py", '''\
from helper import doubled


def test_doubled():
    assert doubled(2) == 4
''')


def weaken_an_assertion_in_place(root: Path) -> None:
    """The stated blind spot: still one assert, asserting much less."""
    write(root / "tests" / "test_beta.py", '''\
from helper import double


def test_double():
    assert double(2)
''')


#: What the shipped census makes of each change, so a reader meets the claim
#: and the demonstration together. `False` means the coordinator refuses the
#: stage; `True` means it advances.
CENSUS_CASES = {
    "a deleted test": (delete_a_test, False),
    "a test marked skipped": (mark_a_test_skipped, False),
    "a test marked xfailed": (mark_a_test_xfailed, False),
    "a removed assertion": (remove_an_assertion, False),
    "a renamed test whose call sites were updated": (
        rename_a_test_and_its_call_sites, True),
    "an assertion replaced by a weaker one": (weaken_an_assertion_in_place, True),
}

#: The two cases the census permits, split out by why: the first is what the
#: proxy is *for*, and the second is the hole it is documented to have.
PERMITTED_BY_DESIGN = "a renamed test whose call sites were updated"
PERMITTED_BLIND_SPOT = "an assertion replaced by a weaker one"


def census_over(root: Path, change, *, command: str | None = None,
                paths: tuple[str, ...] = (GOVERNED,)) -> dict:
    """Take the baseline census, make `change`, take the census again.

    The two calls the coordinator makes around a stage, made here directly:
    `capture_stage_baseline` before, the change in the working tree, and
    `suite_census_check` after. Everything the check needs comes from the two
    arguments; nothing is routed through a run, because what is being decided
    is the check's verdict rather than the routing on it.
    """
    run_dir = root / ".harness" / "runs" / STORY_ID
    run_dir.mkdir(parents=True, exist_ok=True)
    baseline = story_coordinator.capture_stage_baseline(
        run_dir, root, "stage-baseline", "stage", list(paths), accounted_for=set())
    if change is not None:
        change(root)
    artifact = f"{SCHEMA_STEM}.json"
    story_coordinator.suite_census_check(
        run_dir, root, {"census_command": command or shipped_census_command()},
        artifact, list(paths), baseline, stage_name="stage")
    return json.loads((run_dir / artifact).read_text(encoding="utf-8"))


def test_the_shipped_census_counts_the_fixture_suite_as_it_stands(counted_target):
    """The premise under every case below: the counters mean what they say.

    Without this a census that counted nothing at all would make every
    "permitted" result below vacuous and every "refused" one impossible.
    """
    record = census_over(counted_target, None)
    assert record["ran"] is True
    assert record["baseline"] == CENSUS_AT_HEAD
    assert record["after"] == CENSUS_AT_HEAD
    assert record["permitted"] is True
    assert record["regressions"] == []


@pytest.mark.parametrize("case", sorted(CENSUS_CASES))
def test_what_this_repositorys_census_makes_of_each_change(case, counted_target):
    """Each change made to a real tree, and the shipped check run over it."""
    change, permitted = CENSUS_CASES[case]
    record = census_over(counted_target, change)
    assert record["ran"] is True
    assert record["permitted"] is permitted, (case, record)
    assert bool(record["regressions"]) is (not permitted)


def test_each_refused_change_names_the_counter_it_lowered(counted_target):
    """Not merely that the census refused, but that it says what fell.

    A refusal naming no counter would leave the developer to re-derive the
    comparison by hand, which is the whole of what the check exists to save.
    """
    for case, (change, permitted) in sorted(CENSUS_CASES.items()):
        if permitted:
            continue
        record = census_over(counted_target, change)
        for regression in record["regressions"]:
            assert regression["counter"] in CENSUS_AT_HEAD, case
            assert regression["baseline"] == CENSUS_AT_HEAD[regression["counter"]]
            assert regression["after"] < regression["baseline"], case


def test_the_rename_the_proxy_exists_for_moves_no_counter(counted_target):
    """Permitted because nothing moved, not because the check stopped looking."""
    change, _ = CENSUS_CASES[PERMITTED_BY_DESIGN]
    record = census_over(counted_target, change)
    assert record["after"] == record["baseline"] == CENSUS_AT_HEAD


def test_the_weaker_assertion_passes_and_is_recorded_as_the_blind_spot(
        counted_target):
    """The proxy's hole, demonstrated rather than described.

    The assertion genuinely got weaker — `double(2) == 4` became `double(2)`,
    which holds for any non-zero return — and the census is unmoved. That is
    the case the check does not cover, so the demonstration sits beside the
    three places the limit is written down.
    """
    change, _ = CENSUS_CASES[PERMITTED_BLIND_SPOT]
    record = census_over(counted_target, change)
    assert record["permitted"] is True
    assert record["after"] == record["baseline"] == CENSUS_AT_HEAD


# --------------------------------------------------------------------------
# What the shipped census prints, and what it says it cannot see
# --------------------------------------------------------------------------

COUNTED_FIXTURE = '''\
import pytest


def test_counted():
    assert True
    assert 1 == 1


@pytest.mark.skip(reason="declared")
def test_skipped():
    assert True


@pytest.mark.xfail
def test_xfailed():
    assert True


@pytest.mark.skipif(True, reason="declared")
def test_skipped_conditionally():
    assert True


def helper_that_is_not_a_test():
    assert True
'''

#: The same file with every marker taken off, so the suppression is shown to
#: be doing something rather than merely coinciding with the answer.
UNMARKED_FIXTURE = "\n".join(
    line for line in COUNTED_FIXTURE.splitlines()
    if not line.startswith("@pytest.mark."))


def run_shipped_census(directory: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SHIPPED_CENSUS), str(directory)],
        capture_output=True, text=True)


def test_the_census_prints_a_json_object_of_counter_names_to_integers(tmp_path):
    """The contract the coordinator parses, asserted at the shipped interface."""
    directory = tmp_path / "suite"
    write(directory / "test_counted.py", COUNTED_FIXTURE)
    result = run_shipped_census(directory)

    assert result.returncode == 0, result.stderr
    printed = json.loads(result.stdout)
    assert isinstance(printed, dict)
    assert printed
    for counter, value in printed.items():
        assert isinstance(counter, str)
        assert isinstance(value, int) and not isinstance(value, bool)


def test_the_census_counts_unsuppressed_tests_and_assert_statements(tmp_path):
    """Counted by hand against the fixture above: one test survives the three
    markers, and every assert in the file is counted wherever it sits."""
    directory = tmp_path / "suite"
    write(directory / "test_counted.py", COUNTED_FIXTURE)
    counted = json.loads(run_shipped_census(directory).stdout)
    assert counted == {"unskipped_tests": 1, "assertions": 6}


def test_the_same_file_without_its_markers_counts_every_test(tmp_path):
    """The control for the assertion above.

    Three of the four test functions are excluded there. If the marker lookup
    had stopped matching anything, both files would count the same and the
    exclusion would be invisible."""
    directory = tmp_path / "unmarked"
    write(directory / "test_counted.py", UNMARKED_FIXTURE)
    counted = json.loads(run_shipped_census(directory).stdout)
    assert counted["unskipped_tests"] == 4
    assert counted["assertions"] == 6


# --------------------------------------------------------------------------
# The limit, stated where a reader forms a view of what the check is worth
# --------------------------------------------------------------------------


def states_the_blind_spot(text: str) -> bool:
    """Whether a text says the proxy assumes weakening shrinks a count.

    Several words together rather than one phrase: the assumption, the shape
    of the change that escapes it, what the change is done to, and the fact
    that it goes through. A single phrase would match a text that mentioned
    weakening while claiming the census caught it.
    """
    lowered = text.lower()
    return ("assume" in lowered
            and ("loosen" in lowered or "weaker" in lowered)
            and "assertion" in lowered
            and "pass" in lowered)


def census_section_of_the_coordinator() -> str:
    """The coordinator's own account of the check, from its source.

    Bounded at the block the check is defined in — from the section's own
    heading to the heading of the section after it — rather than taken as the
    whole module, so a phrase written somewhere else in a five-thousand-line
    file cannot satisfy an assertion about what the census says about itself,
    and so a name the census section may not carry cannot hide in it either.
    """
    source = (REPO_ROOT / "orchestration" / "story_coordinator.py").read_text(
        encoding="utf-8")
    start = source.index("The suite census")
    end = source.index("# Whether a documented claim", start)
    return source[start:end]


def test_the_limit_is_stated_in_each_place_a_reader_meets_the_verdict():
    """The target's census, the artifact's schema, and the coordinator's own
    definition of the check. A reader arriving at any one of the three learns
    what a permitted census does not claim."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    for name, text in (("the shipped census", SHIPPED_CENSUS.read_text(encoding="utf-8")),
                       ("the result schema", schema["description"]),
                       ("the coordinator", census_section_of_the_coordinator())):
        assert states_the_blind_spot(text), name


def test_this_repositorys_census_says_in_its_own_docstring_what_it_cannot_see():
    """Where a reader of the counters meets them: the file that produces them.

    Not that some text somewhere says it — the census script's own module
    docstring, which is what a developer adding a counter reads first."""
    docstring = subprocess.run(
        [sys.executable, "-c",
         "import ast,sys;print(ast.get_docstring(ast.parse(open(sys.argv[1])"
         ".read())))", str(SHIPPED_CENSUS)],
        capture_output=True, text=True, check=True).stdout.lower()
    assert "invisible" in docstring
    assert "moves no counter" in docstring
    assert states_the_blind_spot(docstring)


def test_the_predicate_reports_a_text_that_does_not_state_it():
    """The control for the assertion above: a predicate loose enough to match
    anything would pass it whatever those three said."""
    assert not states_the_blind_spot(
        (REPO_ROOT / "workflows" / "story-workflow.json").read_text(encoding="utf-8"))


def test_the_census_says_it_counts_a_run_time_skip_as_unskipped(tmp_path):
    """The narrower limit the shipped census states, demonstrated.

    Parsing sees declared markers and nothing else, so a body that calls
    `pytest.skip` is counted. The census says so; this shows it is so."""
    directory = tmp_path / "runtime-skip"
    write(directory / "test_runtime.py", '''\
import pytest


def test_skipped_at_run_time():
    pytest.skip("decided while running")
    assert False
''')
    assert json.loads(run_shipped_census(directory).stdout)["unskipped_tests"] == 1
    assert "run time" in SHIPPED_CENSUS.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Part two: the coordinator's comparison, over a census it did not write
#
# The census here counts marker files a stage can add, drop or lower. It is an
# input: "a counter disappeared" and "a counter appeared" are properties of the
# comparison, and deriving them from the way this repository happens to count
# pytest would make a deployment fact into something this module enforces.
# --------------------------------------------------------------------------

#: The fixture census, written into the target under a name of its own. Each
#: marker file beneath the governed directory is one counter; the constant is
#: there so a census is never empty, which would make "no counter fell" true
#: for a reason that has nothing to do with the stage.
FIXTURE_CENSUS = '''\
import json
import pathlib
import sys

counters = {"constant": 1}
for path in sorted(pathlib.Path(sys.argv[1]).glob("counter_*.txt")):
    counters[path.stem] = int(path.read_text().strip())
print(json.dumps(counters))
'''

FIXTURE_CENSUS_REL = "fixture-census.py"

#: The counter the fixture target starts with, and its starting value.
COUNTER = "counter_alpha"
COUNTER_AT_BASELINE = 3

CENSUS_ARTIFACT = f"{SCHEMA_STEM}.json"

WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        suite_census={"result": CENSUS_ARTIFACT,
                      "baseline": "stage-baseline",
                      "paths": [GOVERNED]},
        schemas={conftest.CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="census-workflow",
)

STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VERIFYING = STAGE_NAMES

#: The stage that declares the check, found by the declaration rather than by
#: name, and every name the check needs read off that declaration.
CENSUS_STAGE = next(s for s in WORKFLOW["stages"] if "suite_census" in s)
DECLARATION = CENSUS_STAGE["suite_census"]
ARTIFACT = DECLARATION["result"]
DECLARED_PATHS = DECLARATION["paths"]

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


@pytest.fixture
def marker_target(tmp_path: Path) -> Path:
    """A target whose census counts marker files under the governed path."""
    root = tmp_path / "marker-target"
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml",
          CONFIG.format(workflow=WORKFLOW["name"], tests_dir=GOVERNED))
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", conftest.STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / FIXTURE_CENSUS_REL, FIXTURE_CENSUS)
    write(root / GOVERNED / f"{COUNTER}.txt", f"{COUNTER_AT_BASELINE}\n")
    write(root / ".gitignore", "__pycache__/\n")
    init_repository(root)
    return root


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    return conftest.materialize_workflow(WORKFLOW, tmp_path / "census-harness")


def fixture_census_command(root: Path) -> str:
    return shlex.join([sys.executable, str(root / FIXTURE_CENSUS_REL), GOVERNED])


def configure(root: Path, **overrides: object) -> None:
    """Rewrite configuration keys and commit, as a run's pre-flight requires."""
    path = root / ".harness" / "config.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    for key, value in overrides.items():
        if value is None:
            lines = [line for line in lines if not line.startswith(f"{key}:")]
            continue
        for index, line in enumerate(lines):
            if line.startswith(f"{key}:"):
                lines[index] = f"{key}: {value}"
                break
        else:
            lines.append(f"{key}: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    conftest.commit_setup(root, "configure the target for this test")


def lower_the_counter(root: Path) -> dict:
    write(root / GOVERNED / f"{COUNTER}.txt", "1\n")
    return {"modified": [f"{GOVERNED}{COUNTER}.txt"], "created": [], "deleted": []}


def raise_the_counter(root: Path) -> dict:
    write(root / GOVERNED / f"{COUNTER}.txt", "9\n")
    return {"modified": [f"{GOVERNED}{COUNTER}.txt"], "created": [], "deleted": []}


def drop_the_counter(root: Path) -> dict:
    (root / GOVERNED / f"{COUNTER}.txt").unlink()
    return {"modified": [], "created": [], "deleted": [f"{GOVERNED}{COUNTER}.txt"]}


def add_a_counter(root: Path) -> dict:
    write(root / GOVERNED / "counter_beta.txt", "1\n")
    return {"modified": [], "created": [f"{GOVERNED}counter_beta.txt"],
            "deleted": []}


def leave_it_alone(root: Path) -> dict:
    return {"modified": [], "created": [], "deleted": []}


class Runner:
    """A fake agent runner: each stage writes its artifacts, and the writing
    stage makes the working-tree change the case is about."""

    def __init__(self, root: Path, change):
        self.run_dir = root / ".harness" / "runs" / STORY_ID
        self.root = root
        self.change = change
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None):
        self.calls.append(stage)
        if stage == WRITING:
            write_json(self.run_dir / conftest.CHANGED_FILES, self.change(self.root))
            write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY, "Did it.\n")
        elif stage == VERIFYING:
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, PASS_VERDICT)
        return AgentResult(ok=True, result_text=f"{stage} done")


def run(root: Path, harness: Path, change=leave_it_alone) -> tuple[int, Runner]:
    runner = Runner(root, change)
    code = story_coordinator.run_story(STORY_ID, harness, root, runner)
    return code, runner


def run_dir_of(root: Path) -> Path:
    return root / ".harness" / "runs" / STORY_ID


def record_of(root: Path) -> dict:
    return json.loads((run_dir_of(root) / ARTIFACT).read_text(encoding="utf-8"))


def evidence(root: Path) -> tuple[str, str]:
    """The two places an escalation reason must appear."""
    run_dir = run_dir_of(root)
    return ((run_dir / "events.log").read_text(encoding="utf-8"),
            (run_dir / "escalation-summary.md").read_text(encoding="utf-8"))


def history_of(root: Path) -> list[dict]:
    return json.loads((run_dir_of(root) / "execution-history.json").read_text(
        encoding="utf-8"))


def announcements(root: Path) -> list[dict]:
    return [entry for entry in history_of(root)
            if entry["event"] == ANNOUNCEMENT]


def state_of(root: Path) -> dict:
    return json.loads((run_dir_of(root) / "state.json").read_text(encoding="utf-8"))


@pytest.fixture
def censused(marker_target: Path) -> Path:
    """The marker target with its own census command configured."""
    configure(marker_target, census_command=fixture_census_command(marker_target))
    return marker_target


# --------------------------------------------------------------------------
# Both censuses, the command, and the verdict, recorded
# --------------------------------------------------------------------------


def test_the_result_records_both_censuses_the_command_and_the_verdict(
        censused, harness_root):
    code, runner = run(censused, harness_root)
    assert code == 0
    assert runner.calls == STAGE_NAMES

    record = record_of(censused)
    assert record["ran"] is True
    assert record["command"] == fixture_census_command(censused)
    assert record["baseline"] == {"constant": 1, COUNTER: COUNTER_AT_BASELINE}
    assert record["after"] == record["baseline"]
    assert record["permitted"] is True
    assert Path(record["baseline_path"]).is_dir()


def test_the_baseline_census_is_taken_in_a_clone_at_the_stages_baseline(
        censused, harness_root):
    """Not in the tree the stage left. The stage lowers the counter, and the
    baseline half of the record still reports what the tree held before it."""
    assert run(censused, harness_root, lower_the_counter)[0] == 2
    record = record_of(censused)
    assert record["baseline"][COUNTER] == COUNTER_AT_BASELINE
    assert record["after"][COUNTER] == 1
    assert (censused / GOVERNED / f"{COUNTER}.txt").read_text().strip() == "1"


# --------------------------------------------------------------------------
# What advances and what does not
# --------------------------------------------------------------------------


def test_a_census_leaving_every_counter_at_or_above_its_baseline_advances(
        censused, harness_root):
    code, runner = run(censused, harness_root, raise_the_counter)
    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert state_of(censused)["status"] == "completed"
    record = record_of(censused)
    assert record["permitted"] is True
    assert record["after"][COUNTER] > record["baseline"][COUNTER]


def test_a_decreased_counter_stops_the_run_naming_it_and_both_values(
        censused, harness_root):
    code, runner = run(censused, harness_root, lower_the_counter)
    assert code == 2
    assert runner.calls == [WRITING]
    assert state_of(censused)["status"] == "escalated"

    record = record_of(censused)
    assert record["permitted"] is False
    assert record["regressions"] == [
        {"counter": COUNTER, "baseline": COUNTER_AT_BASELINE, "after": 1}]
    for text in evidence(censused):
        assert COUNTER in text
        assert str(COUNTER_AT_BASELINE) in text
        assert WRITING in text


def test_a_counter_the_baseline_carried_and_the_later_census_lost_stops_the_run(
        censused, harness_root):
    code, _ = run(censused, harness_root, drop_the_counter)
    assert code == 2

    record = record_of(censused)
    assert record["permitted"] is False
    assert record["regressions"] == [
        {"counter": COUNTER, "baseline": COUNTER_AT_BASELINE}]
    assert COUNTER not in record["after"]
    for text in evidence(censused):
        assert COUNTER in text


def test_a_counter_the_baseline_did_not_carry_is_no_violation(
        censused, harness_root):
    """A target may start counting something new without the addition reading
    as a removal of something old: the comparison runs over the counters the
    *baseline* carried."""
    code, _ = run(censused, harness_root, add_a_counter)
    assert code == 0

    record = record_of(censused)
    assert record["permitted"] is True
    assert record["regressions"] == []
    assert set(record["after"]) - set(record["baseline"]) == {"counter_beta"}


# --------------------------------------------------------------------------
# A census that could not be taken refuses rather than permits
# --------------------------------------------------------------------------

#: One command per way of failing, each printing or exiting rather than being
#: described, and the phrase the refusal must use to tell it from the others.
BROKEN_CENSUSES = {
    "a non-zero exit": ("import sys; sys.exit(3)", "exited 3"),
    "output that is not JSON": ("print('not json at all')", "not JSON"),
    "a JSON value that is not an object": ("print('[]')", "not an object"),
    "a value that is not an integer": ('print(\'{"a": "b"}\')', "not an integer"),
    "a value that is a boolean": ('print(\'{"a": true}\')', "not an integer"),
}


@pytest.mark.parametrize("case", sorted(BROKEN_CENSUSES))
def test_a_census_command_that_cannot_be_read_stops_the_run_saying_which(
        case, marker_target, harness_root):
    program, phrase = BROKEN_CENSUSES[case]
    configure(marker_target,
              census_command=shlex.join([sys.executable, "-c", program]))

    code, runner = run(marker_target, harness_root)
    assert code == 2, case
    assert runner.calls == [WRITING]

    record = record_of(marker_target)
    assert record["ran"] is False
    assert "permitted" not in record
    assert phrase in record["reason"], (case, record["reason"])
    _, summary = evidence(marker_target)
    assert "census" in summary
    assert phrase in summary


def test_the_same_run_with_a_readable_census_completes(censused, harness_root):
    """The control for the five refusals above: identical machinery, a command
    the coordinator can read, and the run goes through."""
    assert run(censused, harness_root)[0] == 0
    assert record_of(censused)["ran"] is True


def test_a_stage_with_no_captured_baseline_refuses_rather_than_permits(
        censused, tmp_path):
    """Called directly, because a run always captures one. A census with only
    one side has decided nothing, and a check that decided nothing may not
    report a pass."""
    run_dir = tmp_path / "no-baseline-run"
    run_dir.mkdir()
    decided = story_coordinator.suite_census_check(
        run_dir, censused, {"census_command": fixture_census_command(censused)},
        ARTIFACT, DECLARED_PATHS, None, stage_name=WRITING)

    assert decided.ran is False
    assert decided.permitted is None
    assert "baseline" in decided.reason
    assert not (run_dir / "execution-history.json").exists()


# --------------------------------------------------------------------------
# Two ways of not asking for a census, and what each costs
# --------------------------------------------------------------------------


def test_a_target_configuring_no_census_command_reports_a_check_that_did_not_run(
        marker_target, harness_root):
    """Declining the check is not failing it, and it is not passing it either:
    the record says the census did not run, and the run goes on."""
    code, runner = run(marker_target, harness_root)
    assert code == 0
    assert runner.calls == STAGE_NAMES

    record = record_of(marker_target)
    assert record["ran"] is False
    assert record["command"] == ""
    assert "permitted" not in record
    assert "census_command" in record["reason"]


def test_that_target_announces_nothing_because_nothing_is_about_to_take_time(
        marker_target, harness_root):
    assert run(marker_target, harness_root)[0] == 0
    assert announcements(marker_target) == []


def test_the_same_target_with_a_census_command_does_announce(censused, harness_root):
    """The control for the two assertions above. One configuration key apart:
    the census runs, the record carries a verdict, and the wait is announced
    before it starts."""
    assert run(censused, harness_root)[0] == 0
    assert record_of(censused)["ran"] is True

    announced = announcements(censused)
    assert len(announced) == 1
    assert announced[0]["stage"] == WRITING
    assert announced[0]["artifacts"] == [ARTIFACT]
    assert "census" in announced[0]["message"]


def test_the_announcement_states_the_wait_the_census_actually_costs(
        censused, harness_root):
    """A census is a census twice over, not a suite run twice over. A reader
    told to expect a suite run would be waiting for work that never starts."""
    assert run(censused, harness_root)[0] == 0
    message = announcements(censused)[0]["message"].lower()
    assert "census" in message
    assert "as long as a suite run" not in message


def test_the_announcement_appears_in_both_renderings(censused, harness_root):
    assert run(censused, harness_root)[0] == 0
    entry = announcements(censused)[0]
    log = (run_dir_of(censused) / "events.log").read_text(encoding="utf-8")
    assert f"[{entry['timestamp']}] {entry['message']}" in log.splitlines()


def test_the_announcement_is_written_before_the_census_clone_is_built(
        censused, harness_root, monkeypatch):
    """The ordering that makes announcing worth anything: on disk before the
    wait begins rather than beside the result once it is over."""
    seen: list[list[str]] = []
    original = story_coordinator._build_clone

    def spy(*args, **kwargs):
        seen.append([entry["event"] for entry in history_of(censused)])
        return original(*args, **kwargs)

    monkeypatch.setattr(story_coordinator, "_build_clone", spy)
    assert run(censused, harness_root)[0] == 0
    assert len(seen) == 1
    assert seen[0][-1] == ANNOUNCEMENT


def test_a_workflow_declaring_no_census_takes_none_and_announces_nothing(
        censused, tmp_path, monkeypatch):
    """The declaration is one switch, not two. With the key gone the check
    neither runs nor announces, and no orchestration code changed."""
    stripped = json.loads(json.dumps(WORKFLOW))
    for stage in stripped["stages"]:
        stage.pop("suite_census", None)
    harness = conftest.materialize_workflow(stripped, tmp_path / "no-declaration")

    built: list[tuple] = []
    original = story_coordinator._build_clone
    monkeypatch.setattr(story_coordinator, "_build_clone",
                        lambda *a, **k: built.append(k.get("revert", ()))
                        or original(*a, **k))

    code, _ = run(censused, harness, lower_the_counter)
    assert code == 0
    assert not (run_dir_of(censused) / ARTIFACT).exists()
    assert announcements(censused) == []
    assert built == []


def test_the_same_change_under_the_declaring_workflow_is_refused(
        censused, harness_root):
    """The control for the assertion above: the workflow is the only
    difference, and with the declaration in place the run stops."""
    assert run(censused, harness_root, lower_the_counter)[0] == 2


# --------------------------------------------------------------------------
# The record as evidence: the schema, the inventory, and who reads it
# --------------------------------------------------------------------------


def test_the_schema_exists_and_is_named_in_the_inventory():
    manifest = json.loads(
        (REPO_ROOT / "schemas" / "manifest.json").read_text(encoding="utf-8"))
    assert SCHEMA_PATH.is_file()
    assert SCHEMA_STEM in manifest["schemas"]
    assert schema_validator.load_schema(SCHEMA_STEM)["title"] == SCHEMA_STEM


def test_this_repository_configures_a_census_and_is_not_refused_for_it():
    """The key is declared, this repository carries it, and carrying it is no
    offence. The control is a copy of the same configuration with the key
    mistyped, which the same check reports — so "no problem" here is a
    statement about the declaration rather than about a check that stopped
    looking."""
    config = conftest.repository_config()
    assert config["census_command"]
    assert "census_command" in harness_config.declared_config_keys()
    assert harness_config.undeclared_config_problems(config) == []

    mistyped = {key: value for key, value in config.items()
                if key != "census_command"}
    mistyped["cencus_command"] = config["census_command"]
    assert harness_config.undeclared_config_problems(mistyped)


def test_this_repositorys_configured_census_is_the_census_it_ships():
    """The command names the script this module has been running all along,
    so the demonstrations above are demonstrations about what a run would
    take."""
    configured = shlex.split(conftest.repository_config()["census_command"])
    assert any(Path(word).name == SHIPPED_CENSUS.name for word in configured)


def test_the_written_record_validates_against_that_schema(censused, harness_root):
    assert run(censused, harness_root)[0] == 0
    record = record_of(censused)
    schema = schema_validator.load_schema(SCHEMA_STEM)
    assert schema_validator.validate(record, schema) == []
    # The control: the same validator against the same schema rejects a record
    # missing what a census result must always carry.
    incomplete = {key: value for key, value in record.items() if key != "ran"}
    assert schema_validator.validate(incomplete, schema) != []


def test_a_refused_census_also_validates(censused, harness_root):
    """The refusing shape is a different shape — no verdict, regressions
    present — and it is written to the same schema."""
    assert run(censused, harness_root, lower_the_counter)[0] == 2
    schema = schema_validator.load_schema(SCHEMA_STEM)
    assert schema_validator.validate(record_of(censused), schema) == []


def test_the_schema_appears_in_no_stages_schemas_map():
    """No agent is asked to satisfy it: the coordinator writes it. The control
    is the record that *is* in a stage's map, so a lookup that had stopped
    seeing anything would fail here."""
    mapped = {name for stage in WORKFLOW["stages"]
              for name in stage.get("schemas", {}).values()}
    assert SCHEMA_STEM not in mapped
    assert "changed-files" in mapped


# --------------------------------------------------------------------------
# The coordinator learns nothing about the target's test framework
# --------------------------------------------------------------------------

#: Names a coordinator that had learned the target's stack would carry. The
#: harness-wide scan lives in `tests/test_no_target_stack_in_harness_source.py`;
#: what is asserted here is narrower and belongs to this story: the census
#: section itself interprets no counter and knows no framework.
FRAMEWORK_WORDS = ("pytest", "unittest", "xfail", "skipif", "assert statement",
                   "unskipped_tests", "assertions")


def test_the_census_section_names_no_framework_and_no_counter():
    section = census_section_of_the_coordinator().lower()
    for word in FRAMEWORK_WORDS:
        assert word not in section, word
    # The control: this repository's own census, which is where that knowledge
    # is allowed to live, carries the words the section may not.
    target_side = SHIPPED_CENSUS.read_text(encoding="utf-8").lower()
    assert [word for word in FRAMEWORK_WORDS if word in target_side]


def test_the_comparison_reads_no_counter_name(censused, harness_root):
    """Behaviourally rather than by reading the source: the fixture census
    above names its counters nothing the coordinator could recognise, and the
    comparison decides on them all the same."""
    assert run(censused, harness_root, lower_the_counter)[0] == 2
    assert record_of(censused)["regressions"][0]["counter"] == COUNTER
    assert COUNTER not in (REPO_ROOT / "orchestration" /
                           "story_coordinator.py").read_text(encoding="utf-8")
