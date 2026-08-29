"""story-086 validation: a stage cannot edit the story that governs it.

`blocked_paths` already refused a stage the run directory and the cross-run
history, on one argument: a stage must not rewrite the record of its own
execution. The story artifact is the other half of that argument. An
implementer that cannot satisfy an acceptance criterion can edit the
criterion; a tester that cannot make an assertion pass can narrow what the
story asked for. This story adds the prefix and these are the proofs that the
existing mechanism honours it.

Nothing about how prefixes are matched or what a violation escalates to is the
subject here — that mechanism is unchanged, and this module holds it to a
*stories* prefix the way `tests/test_cross_run_history.py` holds it to a
history one.

The division between what runs against a fixture and what reads what this
repository ships is the one story-081 established:

  * **the behaviour** — a stage record naming a path beneath a blocked stories
    directory escalates the run and the summary names the path; the same
    record without the prefix completes; the predicate discriminates in both
    directions — runs against the workflow this module builds and the rule set
    it declares. What is asserted is that a blocked stories prefix works, not
    that this repository happens to declare one, so relocating or renaming
    anything this repository deploys leaves these assertions pointed where
    they were.
  * **the deployment** — that this repository's own
    `rules/execution-rules.json` carries the entry, and that the path the
    coordinator resolves a running story from lies beneath a prefix that rule
    set blocks — reads the shipped files and says so. No stage may write the
    rule set (`rules/` is blocked for every stage of every story), so the
    entry is made by hand and asserted here.

The escalation is held for every stage of the built workflow that declares a
changed-files record rather than for one of them, because the two failure
modes the rule exists to refuse belong to different stages.

Every absence asserted here is paired with a demonstration that the same check
reports the violation it exists to catch: each escalation has the same record
without the prefix beside it, and the deployment assertion has the same
prefix-match run over the blocked list with the stories entry withheld, where
it must find nothing. Nothing invokes a model; nothing resolves a baseline out
of git.
"""
import json
import re
from pathlib import Path

import pytest

import conftest
import harness_config
import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# The fixture workflow and the fixture rules
# --------------------------------------------------------------------------

#: Built rather than resolved out of what this repository deploys: the subject
#: is what a run does with a record naming a blocked path, and the stage list
#: is an input to that. Three stages declare a changed-files record and one
#: does not, so the coverage claim below — the escalation holds for every stage
#: that declares one — has both kinds to tell apart.
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
        schemas={conftest.VERIFICATION_RESULT: "verification-result"}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="story-artifact-blocked-workflow",
)

STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, DOCUMENTING, VERIFYING = STAGE_NAMES

#: Every stage of the built workflow that declares a changed-files record,
#: derived from the definition rather than listed. The escalation cases below
#: are parametrized over this, so a stage gaining a record is covered without
#: this module being edited.
RECORDING_STAGES = [stage["name"] for stage in WORKFLOW["stages"]
                    if "changed_files" in stage]

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}

#: A path no blocked prefix matches, used as the control everywhere an
#: escalation is asserted. It is the file the target fixture ships, so a
#: control run is a run doing ordinary work.
UNBLOCKED_PATH = "src/app.py"


def configured_stories_dir(root: Path) -> str:
    """The directory a target keeps its story artifacts in.

    Read off that target's own configuration, which is where the coordinator
    reads it from, rather than spelled here: the fixture target and this
    repository are two different deployments and each answers for itself.
    """
    return harness_config.load_config(root)["stories_dir"]


def prefix_of(directory: str) -> str:
    """A directory as `blocked_paths` spells one — trailing separator, so the
    prefix match is a match on the directory rather than on a name that
    happens to start with it."""
    return directory.rstrip("/") + "/"


@pytest.fixture
def configured_workflow() -> str:
    return WORKFLOW["name"]


@pytest.fixture
def stories_prefix(target_root: Path) -> str:
    return prefix_of(configured_stories_dir(target_root))


@pytest.fixture
def fixture_rules(stories_prefix: str) -> dict:
    """The rule set these runs execute under: this module's own.

    The entries beside the stories prefix are here because a rule set with one
    prefix would let a run pass for the wrong reason — the check has a list to
    walk, and the control path below has to be one no entry in it matches.
    """
    return {
        "max_retries": 2,
        "require_verifier_pass": True,
        "blocked_paths": [".git/", ".harness/runs/", "rules/", stories_prefix],
    }


@pytest.fixture
def harness_root(tmp_path: Path, fixture_rules: dict) -> Path:
    return conftest.materialize_workflow(
        WORKFLOW, tmp_path / "story-artifact-harness", rules=fixture_rules)


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class Runner:
    """A fake agent runner that writes each stage's declared artifacts.

    `records` is the seam every case here uses: it replaces the changed-files
    record one named stage writes, so the escalation and its control differ in
    a path and in nothing else — same stage, same record shape, same rules.
    """

    def __init__(self, target_root: Path, records: dict | None = None,
                 story_id: str = "story-001"):
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.records = dict(records or {})
        self.calls: list[str] = []

    def record_for(self, stage: str) -> dict:
        return self.records.get(
            stage, {"modified": [UNBLOCKED_PATH], "created": [], "deleted": []})

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None, max_budget_usd=None, suite_command=None):
        self.calls.append(stage)
        if stage == WRITING:
            write_json(self.run_dir / conftest.CHANGED_FILES,
                       self.record_for(stage))
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Implemented.\n", encoding="utf-8")
            (Path(cwd) / "src" / "app.py").write_text(
                "print('hello again')\n", encoding="utf-8")
        elif stage == VALIDATING:
            write_json(self.run_dir / conftest.TEST_RESULTS, {"tests_written": 1})
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES,
                       self.record_for(stage))
        elif stage == DOCUMENTING:
            (self.run_dir / conftest.DOCUMENTATION_REPORT).write_text(
                "Documented.\n", encoding="utf-8")
            write_json(self.run_dir / conftest.DOCUMENTER_CHANGED_FILES,
                       self.record_for(stage))
        elif stage == VERIFYING:
            write_json(self.run_dir / conftest.VERIFICATION_RESULT,
                       conftest.answering_guidance(dict(PASS), self.run_dir))
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path, story_id: str = "story-001") -> Path:
    return target_root / ".harness" / "runs" / story_id


def state_of(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# The behaviour, against the fixture rule set
# --------------------------------------------------------------------------


@pytest.mark.parametrize("stage", RECORDING_STAGES)
def test_a_stage_naming_a_path_beneath_the_stories_directory_escalates(
    stage, target_root, harness_root, stories_prefix,
):
    """The enforcement, held for every stage that declares a record.

    The prefix comes off the rule set this run executes under, so what is
    asserted is that a stage record naming a path beneath a blocked stories
    directory escalates and that the summary names the path — not that this
    repository happens to block one. It is held for each recording stage
    rather than for one because the failure modes belong to different stages:
    an acceptance criterion edited by the stage that cannot satisfy it, an
    assertion narrowed by the stage that cannot make it pass.

    The run reaching the stage is asserted too. Without it an escalation
    raised earlier, for some reason having nothing to do with the prefix,
    would read as this one.
    """
    beneath = stories_prefix + "story-001.yaml"
    runner = Runner(target_root, records={
        stage: {"modified": [beneath], "created": [], "deleted": []}})
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 2

    run_dir = run_dir_of(target_root)
    summary = (run_dir / "escalation-summary.md").read_text(encoding="utf-8")
    assert f"blocked path: {beneath}" in summary
    assert state_of(run_dir)["status"] == "escalated"
    assert runner.calls[-1] == stage


@pytest.mark.parametrize("stage", RECORDING_STAGES)
def test_the_same_record_without_the_stories_prefix_completes_the_run(
    stage, target_root, harness_root,
):
    """The control for each escalation above.

    The same stage, the same record shape and the same rules — only the path
    differs — and the run completes through every stage. So the escalation is
    the prefix deciding rather than the record being rejected for some other
    reason, and the stage is one a run can get past at all.
    """
    runner = Runner(target_root, records={
        stage: {"modified": [UNBLOCKED_PATH], "created": [], "deleted": []}})
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 0
    assert state_of(run_dir_of(target_root))["status"] == "completed"
    assert runner.calls == STAGE_NAMES


def matches_any(path: str, blocked: list[str]) -> bool:
    """Whether a blocked list matches a path, spelled the way the check
    spells it: a prefix match over the declared entries."""
    return any(path.startswith(prefix) for prefix in blocked)


def test_the_blocked_prefix_is_the_one_the_fixture_rules_declare(fixture_rules,
                                                                 stories_prefix):
    """The predicate the escalations above rest on, exercised in both
    directions against the declaration this module itself carries.

    Beneath the prefix matches; outside it does not. Without the second half
    an escalation could be a list that matches everything, and without the
    first a control could be a list that matches nothing.
    """
    blocked = fixture_rules["blocked_paths"]
    assert stories_prefix in blocked
    assert matches_any(stories_prefix + "story-001.yaml", blocked)
    assert not matches_any(UNBLOCKED_PATH, blocked)


def test_the_covered_stages_are_the_workflow_stages_that_declare_a_record():
    """What the parametrization above covers, and what it therefore leaves
    uncovered.

    Two claims the parametrized cases cannot make about themselves: that they
    run for more than one stage — a single-stage sweep would satisfy the
    escalation assertion while saying nothing about "every stage" — and that
    the workflow holds a stage they skip, which is what makes the selection a
    selection rather than the whole stage list under another name.
    """
    assert len(RECORDING_STAGES) > 1
    skipped = [name for name in STAGE_NAMES if name not in RECORDING_STAGES]
    assert skipped == [VERIFYING]
    assert "changed_files" not in WORKFLOW["stages"][STAGE_NAMES.index(VERIFYING)]


# --------------------------------------------------------------------------
# The deployment, read out of what this repository ships
# --------------------------------------------------------------------------


def shipped_blocked_paths() -> list[str]:
    return json.loads(
        (REPO_ROOT / "rules" / "execution-rules.json").read_text(
            encoding="utf-8"))["blocked_paths"]


def test_this_repository_blocks_the_story_directory_to_every_stage():
    """The deployment fact, as a positive assertion about a shipped file.

    The tests above hold the behaviour against this module's own rules. This
    one holds the half they cannot: that this repository's own
    `rules/execution-rules.json` carries the entry, so a stage of a real run
    cannot edit the story that governs it. Drop the entry and it fails here —
    and here only, which is the point of the division.

    The entry is spelled as the directory this repository configures its
    stories at, so the assertion is that *this deployment's* story directory
    is blocked rather than that some string appears in a list.

    No stage may write that file — `rules/` is blocked for every stage of
    every story — so the entry is made by hand and asserted here.
    """
    assert prefix_of(configured_stories_dir(REPO_ROOT)) in shipped_blocked_paths()


def resolved_story_path(target_root: Path, harness_root: Path, story_id: str,
                        capsys) -> str:
    """The path the coordinator resolves a story artifact from, read out of
    the coordinator rather than rebuilt beside it.

    Asked for a story that is not there, `run_story` refuses and names the
    path it looked at. That refusal is the resolution — the same expression
    that reads the artifact of a run that does exist — so the assertion below
    is about where the harness goes for a story rather than about where this
    module thinks it goes.
    """
    assert story_coordinator.run_story(
        story_id, harness_root, target_root, None) == 1
    message = capsys.readouterr().err
    found = re.search(r"No story artifact at (\S+)\.", message)
    assert found, f"the refusal did not name a path: {message!r}"
    return str(Path(found.group(1)).relative_to(target_root))


def test_the_story_path_the_coordinator_resolves_is_beneath_a_blocked_prefix(
    tmp_path, target_root, harness_root, capsys,
):
    """The rule covers the artifact that actually governs a run.

    A prefix in a list is only worth what it matches. This drives the
    coordinator's own resolution, under this repository's configured stories
    directory, and holds the path it comes back with against this
    repository's shipped blocked list — so what is asserted is that the
    artifact a run is governed by is the artifact the rule covers, not that a
    directory sharing its name is.

    The control is beside it: the same match over the same blocked list with
    the stories entry withheld finds nothing, so the match above is that entry
    deciding rather than some other prefix — or a match that would report any
    path at all.
    """
    config_path = target_root / ".harness" / "config.yaml"
    config_path.write_text(
        re.sub(r"(?m)^stories_dir: .*$",
               f"stories_dir: {configured_stories_dir(REPO_ROOT)}",
               config_path.read_text(encoding="utf-8")),
        encoding="utf-8")
    conftest.commit_setup(target_root, "point the target at this deployment's "
                                       "stories directory")

    resolved = resolved_story_path(target_root, harness_root,
                                   "story-does-not-exist", capsys)
    blocked = shipped_blocked_paths()
    assert matches_any(resolved, blocked)

    stories_entry = prefix_of(configured_stories_dir(REPO_ROOT))
    without = [prefix for prefix in blocked if prefix != stories_entry]
    assert not matches_any(resolved, without)
