"""Independent validation for story-209: a stage says how hard its agent
thinks, and the value reaches the invocation.

Before this story every agent the harness ran thought as hard as the
developer's own editor happened to be set to think. The setting came from a
preference file outside the repository, the most consequential judgement in a
run and the shortest prose edit ran at the same one, and changing it for some
unrelated piece of work silently changed how every story was built. The story
lets a stage declare a level, hands it to the agent, and records a considered
value for every stage of both definitions this repository ships.

The subjects are kept apart, because they fail for different reasons:

  * **the command.** `run_agent` is driven against a fake process, and the
    command it built is read back. Both halves are asked: a level reaches the
    CLI beside the model, and an invocation given none builds the command it
    built before the parameter existed.
  * **the pre-flight.** `effort_problems` is a pure function over a
    definition, so it is driven as one, over definitions built here — each
    level the CLI accepts, values it does not accept, a level on a stage and a
    level on a stage's inspection declaration, and a definition declaring
    nothing at all.
  * **the refusal.** The same malformed definition run end to end, which must
    refuse before a run directory, a state file or an invocation exists.
  * **the pass-through.** Runs driven through a fake stage runner that records
    the keywords each invocation was handed, under a definition declaring a
    different level on each stage and under one declaring none.
  * **the inspection's own level.** The pre-stage inspection is a separate
    invocation the judging stage does not make, so its level is declared on
    the `inspection` block rather than on the stage. The run below declares a
    different level in each place and reads back which one the invocation
    carried.
  * **the reasons.** A reading over a definition's declarations, held against
    definitions built here and then against every definition this repository
    ships — the one place in this module where the shipped artifact is the
    subject rather than an input.

Every absence asserted here carries a demonstration that the same reading
reports the violation it exists to catch:

  * "no effort argument appears anywhere in the command" sits beside the same
    scan over the command built *with* a level, which finds it, and the
    argument-for-argument equality sits beside the same comparison against
    that command, which differs;
  * "a definition declaring nothing is not checked" sits beside the same
    definition with a level declared as `null`, which is reported;
  * "the malformed definition left no run directory, no state and no
    invocation" sits beside the identical run with the level repaired, which
    creates all three and invokes every stage;
  * "a stage declaring nothing is invoked without the keyword" is asserted
    through a fake runner whose signature has no such parameter — so the
    coordinator passing one raises rather than being quietly swallowed — and
    sits beside the run under declared levels, where the same recorder sees
    one;
  * "a broad-mode invocation carries no level" sits beside the same recorder
    handed to the same function with a level supplied, which carries it;
  * "every reason states its own level" sits beside reasons that are absent,
    blank, and present but silent, each reported, and beside a reason naming
    the level whose name contains the declared one — which a substring reading
    would have accepted and this one reports.

`high` is a substring of `xhigh`, so the reading below matches a level as a
word rather than as a substring. That trap is not taken on trust: the pair is
found in the CLI's own vocabulary and the control is built from it.

Nothing here invokes a model. The stages run through a fake runner handed to
`run_story`, and `agent_runner.run_agent` is replaced for every test in this
module by a recording stand-in, so the inspection an unconverted path might
reach cannot talk to a provider. Nothing here resolves a baseline out of git;
the only repositories read are the ones each test built.

The workflows these runs execute are built by `tests/conftest.py`'s builder.
The subject is the mechanism — what a declared level does to an invocation —
so the stage list, the stage names, the artifact name and the levels chosen
are inputs to it, derived from the definition this module built. What this
repository *declares* is the subject of the last section alone.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

import agent_runner
import conftest
import harness_config
import inspection
import story_coordinator
import story_inspection
from agent_runner import AgentResult
from conftest import StageRef, build_workflow, workflow_stage

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]

STORY_ID = conftest.SAMPLE_STORY_ID

# --------------------------------------------------------------------------
# The vocabulary, and the names the declarations live under
# --------------------------------------------------------------------------

#: The levels the CLI accepts, read off the one module that talks to it rather
#: than restated here. Every level this module declares is drawn from this
#: tuple, so a vocabulary the CLI widens does not need a second edit here.
LEVELS = agent_runner.EFFORT_LEVELS

#: The argument the command carries the level as. The CLI's own spelling, which
#: is what an assertion about a built command has to look for; it is written
#: once, here.
EFFORT_ARGUMENT = "--effort"
#: The argument it must sit beside, on the same terms.
MODEL_ARGUMENT = "--model"

#: The workflow keys this story added: the level a stage declares, the sibling
#: recording the judgement behind it, and the block a stage declares the
#: pre-stage inspection under. Spelled once each, and every reading below
#: derives from these rather than writing them again.
EFFORT_KEY = "effort"
REASON_SUFFIX = "_reason"
REASON_KEY = EFFORT_KEY + REASON_SUFFIX
INSPECTION_KEY = "inspection"

#: Distinct levels for the three stages and for the inspection, so a value
#: observed at an invocation identifies which declaration it came from. Taken
#: from the CLI's vocabulary by position rather than written out, and the
#: premise that there are enough distinct ones is asserted below rather than
#: assumed.
WRITING_LEVEL, VALIDATING_LEVEL = LEVELS[0], LEVELS[1]
JUDGING_LEVEL, INSPECTION_LEVEL = LEVELS[-1], LEVELS[2]

#: The two levels whose names contain one another, found in the vocabulary
#: rather than written down: the trap the reason reading exists to survive.
SUBSTRING_PAIR = next(
    ((shorter, longer) for shorter in LEVELS for longer in LEVELS
     if shorter != longer and shorter in longer), None)


# --------------------------------------------------------------------------
# The workflows these runs execute
# --------------------------------------------------------------------------

#: The run-directory artifact the fixture's inspection declaration names.
#: Deliberately not the name this repository deploys: the record arriving under
#: this name is what says the coordinator reads the declaration rather than
#: carrying a name of its own.
FINDINGS_ARTIFACT = "findings-about-this-change.json"


def reason_for(level: str) -> str:
    """A recorded reason of the shape the convention asks for: it states its own
    level, as a word, before saying anything else."""
    return f"{level}. What a fixture says when the judgement is not its subject."


def stages(*, levels: bool, inspection_level: str | None) -> tuple[dict, ...]:
    """The three stages these runs execute, declaring levels or not.

    One builder for both definitions, so the definition declaring nothing
    differs from the one declaring levels in exactly the declarations under
    test and in nothing else.
    """
    def declared(level: str) -> dict:
        return ({EFFORT_KEY: level, REASON_KEY: reason_for(level)}
                if levels else {})

    judging_inspection = {"result": FINDINGS_ARTIFACT}
    if inspection_level is not None:
        judging_inspection[EFFORT_KEY] = inspection_level
        judging_inspection[REASON_KEY] = reason_for(inspection_level)

    return (
        workflow_stage(
            outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
            changed_files=conftest.CHANGED_FILES,
            schemas={conftest.CHANGED_FILES: "changed-files"},
            **declared(WRITING_LEVEL)),
        workflow_stage(
            outputs=(conftest.TEST_RESULTS, conftest.TESTER_CHANGED_FILES),
            changed_files=conftest.TESTER_CHANGED_FILES,
            schemas={conftest.TEST_RESULTS: "test-results",
                     conftest.TESTER_CHANGED_FILES: "changed-files"},
            **declared(VALIDATING_LEVEL)),
        workflow_stage(
            name=conftest.VERIFYING_STAGE,
            outputs=(conftest.VERIFICATION_RESULT,),
            schemas={conftest.VERIFICATION_RESULT: "verification-result",
                     conftest.RETRY_GUIDANCE: "retry-guidance"},
            inspection=judging_inspection,
            retry_routing={"the-behaviour": {
                "stage": StageRef(0),
                "when": "the behaviour the story asked for is missing"}},
            **declared(JUDGING_LEVEL)),
    )


#: A different level on each stage, and a fourth on the inspection the judging
#: stage declares — so an invocation's level says which declaration it came
#: from and the inspection's cannot be mistaken for its stage's.
DECLARED = build_workflow(
    *stages(levels=True, inspection_level=INSPECTION_LEVEL),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="declared-effort-workflow")

#: The same three stages declaring no level anywhere, including on the
#: inspection: the compatibility case, and what every definition looked like
#: before this story.
UNDECLARED = build_workflow(
    *stages(levels=False, inspection_level=None),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="undeclared-effort-workflow")

STAGE_NAMES = [stage["name"] for stage in DECLARED["stages"]]
WRITING, VALIDATING, JUDGING = STAGE_NAMES

#: What each stage of the declaring definition says it thinks at, read off the
#: definition rather than written a second time.
DECLARED_LEVELS = {stage["name"]: stage[EFFORT_KEY]
                   for stage in DECLARED["stages"]}


def with_a_stage_level(workflow: dict, level, *, index: int = 0) -> dict:
    """`workflow` with one stage's declared level replaced by `level`.

    A copy rather than a mutation, so a test can hold the malformed definition
    and its repair side by side under one name.
    """
    built = json.loads(json.dumps(workflow))
    built["stages"][index][EFFORT_KEY] = level
    return built


# --------------------------------------------------------------------------
# A target repository, a harness root, and the stand-in agents
# --------------------------------------------------------------------------

SOURCE_DIR = "src/"
CHANGED_FILE = f"{SOURCE_DIR}app.py"
CHANGED_TEST = "tests/t_existing.py"

TRACKED = {
    CHANGED_FILE: "def work():\n    return 1\n",
    CHANGED_TEST: "def check():\n    assert True\n",
}

#: What the fixture allows one inspection to take into scope. Larger than the
#: expansion these runs produce, so nothing here is silently trimmed; what the
#: cap does is another module's question.
ROOMY_CAP = 60


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def build_target(root: Path, workflow: str) -> Path:
    """A target repository a run of one of these definitions can execute in.

    The shape `conftest.target_root` builds — its config and its story, read
    off conftest so neither is spelled twice — with a source declaration added,
    so the change these runs make is inside an inspected scope and the
    pre-stage inspection has something to be made about.
    """
    for sub in (".harness/standards", ".harness/stories", ".harness/docs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    config = conftest.CONFIG.format(workflow=workflow)
    config += f"source_dirs:\n  - {SOURCE_DIR}\n"
    config += f"{story_inspection.MAX_FILES_KEY}: {ROOMY_CAP}\n"
    write(root / ".harness" / "config.yaml", config)
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", conftest.STORY)
    write(root / ".harness" / "standards" / "coding.md",
          "# Coding Standards\n- keep it simple\n")
    write(root / ".harness" / "standards" / "testing.md",
          "# Testing Standards\n- test everything\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Sample Architecture\n")
    for relative, text in TRACKED.items():
        write(root / relative, text)
    # What a deployed target ignores, so the run directory an inspection writes
    # into is not tracked work the run has to account for.
    write(root / ".gitignore", ".harness/runs/\n.harness/logs/\n")
    conftest.init_repository(root, "the tree this run starts from")
    return root


def materialize(workflow: dict, root: Path) -> Path:
    """The built definition and an Inspector template, written where a run
    finds them. `materialize_workflow` writes a template per stage; the
    Inspector's is named for the inspection rather than for a stage, so it is
    written here."""
    built = conftest.materialize_workflow(workflow, root)
    write(built / "prompts" / inspection.INSPECTOR_PROMPT,
          "# an Inspector template this module wrote\n\n"
          "{{framing}}\n\n{{scope_paths}}\n\n{{findings_path}}\n")
    return built


@pytest.fixture
def environment(tmp_path):
    """A builder for (target, harness) pairs running a definition given here.

    A factory rather than a fixture per definition, because the cases below
    hold two definitions side by side — a malformed declaration and its repair,
    a run under declared levels and the same run under none — and each needs a
    target configured for its own workflow.

    `label` distinguishes two environments running the *same* definition, which
    is what a case driving one workflow through two different runners needs: a
    second run of one story in one target is refused as a re-run, so each run
    gets a repository of its own.
    """
    def make(workflow: dict, *, label: str = "") -> tuple[Path, Path]:
        suffix = f"{workflow['name']}{label}"
        harness = materialize(workflow, tmp_path / f"harness-{suffix}")
        target = build_target(tmp_path / f"target-{suffix}", workflow["name"])
        return target, harness
    return make


class InspectionRecorder:
    """Stands in for `agent_runner.run_agent` at an inspection's invocation.

    It reaches no model. It records the keywords it was handed — which is the
    whole of what the cases below read — and writes an empty findings envelope
    where an invocation writes one, so a scope is inspected once rather than
    invoked again for having produced nothing.
    """

    def __init__(self):
        #: One entry per invocation: the working tree it was handed and every
        #: keyword beyond the ones the inspection has always passed. Recorded
        #: as a mapping rather than as a named parameter, so an invocation
        #: carrying no level is distinguishable from one carrying None.
        self.invocations: list[dict] = []

    @property
    def declared(self) -> list[dict]:
        return [one["declared"] for one in self.invocations]

    def __call__(self, prompt, *, stage, cwd, log_path=None,
                 permission_mode=None, model=None, **declared):
        tree = Path(cwd)
        self.invocations.append(
            {"prompt": prompt, "stage": stage, "cwd": tree,
             "declared": dict(declared)})
        config = harness_config.load_config(tree)
        artifact, _log = inspection.findings_paths(tree, config)
        write_json(artifact, {"findings": []})
        return AgentResult(ok=True, result_text="inspected")


@pytest.fixture(autouse=True)
def inspector(monkeypatch) -> InspectionRecorder:
    """Installed for every test in this module, so an inspection any run
    reaches records what it was handed instead of talking to a provider."""
    recorder = InspectionRecorder()
    monkeypatch.setattr(agent_runner, "run_agent", recorder)
    return recorder


PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}

#: What "the coordinator passed no level at all" looks like from inside the
#: runner, as distinct from a level of None. A stage under no declaration must
#: not be invoked with the keyword, so the two have to be distinguishable here
#: or the assertion cannot say which happened.
NO_LEVEL = object()


class Runner:
    """The fake agent runner the coordinator is handed for the run's stages.

    Each stage writes the artifacts its declaration names and edits the file it
    records, in the tree the coordinator handed it. What it records beyond the
    calls is the *keywords it was handed*: a stage under no declaration has to
    be invoked with exactly the arguments the coordinator passed before this
    story, which is a statement about the call rather than about its result.
    """

    def __init__(self, target_root: Path):
        self.target_root = Path(target_root)
        self.run_dir = conftest.run_dir_for(self.target_root, STORY_ID)
        self.calls: list[str] = []
        #: (stage, the level it was handed, or NO_LEVEL)
        self.levels: list[tuple[str, object]] = []
        #: (stage, every keyword beyond the ones the coordinator has always
        #: passed) — so an unexpected one is reported rather than swallowed.
        self.extras: list[tuple[str, dict]] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 run_dir=None, **extra):
        self.calls.append(stage)
        self.extras.append((stage, dict(extra)))
        self.levels.append((stage, extra.get(EFFORT_KEY, NO_LEVEL)))
        tree = Path(cwd) if cwd else self.target_root
        if stage == WRITING:
            write_json(self.run_dir / conftest.CHANGED_FILES,
                       {"modified": [CHANGED_FILE], "created": [],
                        "deleted": []})
            write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY,
                  "Did the work.\n")
            write(tree / CHANGED_FILE, "def work():\n    return 11\n")
        elif stage == VALIDATING:
            write_json(self.run_dir / conftest.TEST_RESULTS,
                       {"tests_written": 1})
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES,
                       {"modified": [CHANGED_TEST], "created": [],
                        "deleted": []})
            write(tree / CHANGED_TEST,
                  "def check():\n    assert True  # and again\n")
        elif stage == JUDGING:
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, PASS)
        return AgentResult(ok=True, result_text=f"{stage} done")

    def level_at(self, stage: str, occurrence: int = 0):
        return [level for name, level in self.levels if name == stage][occurrence]


class RunnerWithoutTheEffortParameter(Runner):
    """The same runner with the signature it had before this story.

    No `**extra` and no level keyword, so the coordinator passing one would
    raise `TypeError` here rather than being quietly accepted. This is how "a
    stage under no declaration is invoked with exactly today's arguments" is
    checked as a property of the call rather than of what a permissive fake
    chose to ignore.
    """

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 run_dir=None):
        return Runner.__call__(self, prompt, stage=stage, cwd=cwd,
                               log_path=log_path,
                               permission_mode=permission_mode, model=model,
                               allowed_tools=allowed_tools, run_dir=run_dir)


def run(target: Path, harness: Path, runner: Runner) -> int:
    return story_coordinator.run_story(STORY_ID, harness, target, runner,
                                       sleep=lambda _seconds: None)


def run_dir_of(target: Path) -> Path:
    return conftest.run_dir_for(target, STORY_ID)


def branches(target: Path) -> list[str]:
    listing = _git(target, "branch", "--format=%(refname:short)")
    return sorted(line.strip() for line in listing.stdout.splitlines()
                  if line.strip())


# ==========================================================================
# The fixture is what it claims to be
# ==========================================================================


def test_the_vocabulary_has_enough_distinct_levels_to_tell_the_stages_apart():
    """The premise every pass-through case rests on.

    Each stage and the inspection are declared at a different level, so a level
    read off an invocation says which declaration it came from. Two stages
    sharing one would make that reading ambiguous while still passing, so the
    distinctness is asserted rather than assumed.
    """
    chosen = [WRITING_LEVEL, VALIDATING_LEVEL, JUDGING_LEVEL, INSPECTION_LEVEL]
    assert len(set(chosen)) == len(chosen), chosen
    for level in chosen:
        assert level in LEVELS


def test_the_vocabulary_carries_the_substring_pair_the_reason_reading_survives():
    """`high` is a substring of `xhigh`, which is why the reading below matches
    a level as a word. The pair is found in the CLI's own vocabulary rather than
    written here, so if that vocabulary ever loses it this premise says so
    instead of the control quietly becoming vacuous."""
    assert SUBSTRING_PAIR is not None, LEVELS
    shorter, longer = SUBSTRING_PAIR
    assert shorter in longer
    assert shorter != longer


def test_the_fixture_declares_a_level_on_every_stage_and_on_the_inspection():
    """What the pass-through cases read, asserted of the definitions rather
    than assumed of them: one declares everywhere, its companion nowhere, and
    the inspection's level differs from the level of the stage carrying it."""
    declaration = story_coordinator.inspection_declaration(DECLARED["stages"])
    assert declaration[EFFORT_KEY] == INSPECTION_LEVEL
    assert declaration[EFFORT_KEY] != DECLARED_LEVELS[JUDGING]
    assert sorted(DECLARED_LEVELS) == sorted(STAGE_NAMES)

    bare = story_coordinator.inspection_declaration(UNDECLARED["stages"])
    assert EFFORT_KEY not in bare
    assert [stage for stage in UNDECLARED["stages"]
            if EFFORT_KEY in stage] == []
    # The two definitions differ in the declarations under test and in nothing
    # else, so a difference the runs below observe is this one.
    assert with_every_level_removed(DECLARED) == UNDECLARED


def with_every_level_removed(definition: dict) -> dict:
    """`definition` with every level and recorded reason stripped, and its name
    replaced by its companion's — which is what a definition declaring nothing
    differs from it by."""
    bare = json.loads(json.dumps(definition))
    bare["name"] = UNDECLARED["name"]
    for stage in bare["stages"]:
        for key in (EFFORT_KEY, REASON_KEY):
            stage.pop(key, None)
        block = stage.get(INSPECTION_KEY)
        if isinstance(block, dict):
            for key in (EFFORT_KEY, REASON_KEY):
                block.pop(key, None)
    return bare


# ==========================================================================
# The command the runner builds
# ==========================================================================


class FakePopen:
    """Enough of `Popen` for `run_agent`, recording the command it was built
    with and emitting one result event. The real CLI is never invoked."""

    calls: list[list[str]] = []

    def __init__(self, cmd, **kwargs):
        FakePopen.calls.append(list(cmd))
        self.stdin = open("/dev/null", "w")
        self.stdout = iter([json.dumps({"type": "result",
                                        "result": "done"}) + "\n"])

    def wait(self):
        self.stdin.close()
        return 0


#: The model the invocations below name, so "beside the model" is a position in
#: a command that has one rather than a claim about an absent argument.
MODEL = "a-model-this-module-named"

#: The real function, bound before the autouse recorder replaces the module
#: attribute. The two cases below are the only ones in this module whose subject
#: is the command `run_agent` builds rather than what some caller hands it, so
#: they have to reach the function itself; every other case in this module wants
#: the recorder and gets it. Bound at import, which is before any fixture runs.
RUN_AGENT = agent_runner.run_agent


def invoke_run_agent(monkeypatch, tmp_path, **keywords) -> list[str]:
    """Drive the real `run_agent` against a fake process, returning the command
    it built."""
    FakePopen.calls = []
    monkeypatch.setattr(agent_runner.subprocess, "Popen", FakePopen)
    RUN_AGENT(
        "prompt",
        stage=WRITING,
        cwd=tmp_path,
        log_path=tmp_path / "agent.log",
        permission_mode="acceptEdits",
        model=MODEL,
        allowed_tools=["Bash(grep:*)"],
        **keywords,
    )
    assert len(FakePopen.calls) == 1
    return FakePopen.calls[0]


def test_the_command_cases_drive_the_runner_itself_and_not_the_stand_in():
    """The premise the two cases below rest on, and one that fails silently.

    Every test in this module runs under the autouse recorder, which replaces
    `agent_runner.run_agent` so no path here can reach a provider. A case that
    read the module attribute would therefore be asking the recorder what
    command it built, and the recorder builds none — so the assertions would be
    about a stand-in rather than about the harness. The bound function is
    asserted to be the real one and the module attribute to be something else,
    so the indirection is doing what it is there for rather than being an alias
    for what the fixture installed.
    """
    assert RUN_AGENT is not agent_runner.run_agent
    assert isinstance(agent_runner.run_agent, InspectionRecorder)
    assert RUN_AGENT.__module__ == agent_runner.__name__
    assert RUN_AGENT.__name__ == "run_agent"


@pytest.mark.parametrize("level", LEVELS)
def test_a_declared_level_reaches_the_cli_beside_the_model(level, monkeypatch,
                                                           tmp_path):
    """How hard an invocation thinks is a property of the invocation, like which
    model runs it — so it is handed to the CLI in the same place and on the same
    terms, which is what the position below says."""
    cmd = invoke_run_agent(monkeypatch, tmp_path, **{EFFORT_KEY: level})

    assert EFFORT_ARGUMENT in cmd
    assert cmd[cmd.index(EFFORT_ARGUMENT) + 1] == level
    # Adjacent to the model rather than merely present: the model's value sits
    # immediately before this argument.
    assert cmd[cmd.index(EFFORT_ARGUMENT) - 1] == MODEL
    assert cmd[cmd.index(MODEL) - 1] == MODEL_ARGUMENT


def test_without_a_level_the_command_is_the_one_it_built_before(monkeypatch,
                                                                tmp_path):
    """The compatibility half, and the one most easily lost.

    Stated as an absence and as an equality: no argument anywhere in the command
    mentions an effort, and the whole command is word for word the command built
    when the parameter is not passed at all. The control is the same command
    built *with* a level, which the same scan reports and which differs from the
    baseline by exactly the two arguments the level adds.
    """
    omitted = invoke_run_agent(monkeypatch, tmp_path)
    passed_none = invoke_run_agent(monkeypatch, tmp_path, **{EFFORT_KEY: None})

    assert [word for word in omitted if EFFORT_KEY in word.lower()] == []
    assert passed_none == omitted

    with_level = invoke_run_agent(monkeypatch, tmp_path,
                                  **{EFFORT_KEY: LEVELS[0]})
    assert [word for word in with_level if EFFORT_KEY in word.lower()] == [
        EFFORT_ARGUMENT]
    assert len(with_level) == len(omitted) + 2
    assert with_level != omitted


# ==========================================================================
# The pre-flight that refuses a level the CLI would not take
# ==========================================================================


#: Values a level may not take. The empty string and None are here because a
#: truthiness test would read either as "declared nothing" and let it through to
#: every invocation in the run; `True` because a JSON `true` is not a level
#: however permissively it is read; and the wrong case because the CLI's
#: vocabulary is the CLI's rather than approximately its.
NOT_LEVELS = ["", "extreme", "Medium", "highest", None, True, 0, [], {}]


@pytest.mark.parametrize("level", LEVELS)
def test_every_level_the_cli_accepts_is_accepted_by_the_check(level):
    """Each level in turn, on a stage and on an inspection block, so the check
    admits the whole vocabulary rather than the part a fixture happened to
    choose."""
    built = build_workflow(
        workflow_stage(**{EFFORT_KEY: level}),
        workflow_stage(name=conftest.VERIFYING_STAGE,
                       inspection={"result": FINDINGS_ARTIFACT,
                                   EFFORT_KEY: level}),
        name=f"accepted-{level}-workflow")
    assert story_coordinator.effort_problems(built) == []


@pytest.mark.parametrize("level", NOT_LEVELS, ids=repr)
def test_a_level_the_cli_does_not_accept_is_reported(level):
    """Named by the stage and by the value, so the message says which
    declaration to change and what in it is wrong."""
    built = build_workflow(workflow_stage(**{EFFORT_KEY: level}),
                           name="a-bad-level-workflow")
    problems = story_coordinator.effort_problems(built)

    assert len(problems) == 1, problems
    assert built["stages"][0]["name"] in problems[0]
    assert repr(level) in problems[0]


@pytest.mark.parametrize("level", NOT_LEVELS, ids=repr)
def test_a_level_declared_on_an_inspection_block_is_reported_too(level):
    """The other place a level is declared. The pre-stage inspection is a
    separate invocation, so a typo there is a typo no reading of the stage's own
    declaration would find — and it is caught on the same terms, named by the
    stage carrying the block."""
    built = build_workflow(
        workflow_stage(name=conftest.VERIFYING_STAGE,
                       inspection={"result": FINDINGS_ARTIFACT,
                                   EFFORT_KEY: level}),
        name="a-bad-inspection-level-workflow")
    problems = story_coordinator.effort_problems(built)

    assert len(problems) == 1, problems
    assert built["stages"][0]["name"] in problems[0]
    assert repr(level) in problems[0]
    assert INSPECTION_KEY in problems[0]


def test_a_definition_declaring_no_level_anywhere_is_not_checked_at_all():
    """Absent means the CLI's own default, which is what every invocation did
    before this key existed — so a definition declaring none has nothing to
    report. The control is the same definition with a level declared as `null`,
    which is a declaration and is not a level: the check reports it in both
    places, so the silence above is the absence deciding rather than a check
    that has stopped looking.
    """
    assert story_coordinator.effort_problems(UNDECLARED) == []

    declared_null = json.loads(json.dumps(UNDECLARED))
    declared_null["stages"][0][EFFORT_KEY] = None
    block = story_coordinator.inspection_declaration(declared_null["stages"])
    block[EFFORT_KEY] = None

    problems = story_coordinator.effort_problems(declared_null)
    assert len(problems) == 2, problems
    assert story_coordinator.effort_problems(UNDECLARED) == []


def test_the_check_reads_its_vocabulary_from_the_module_that_talks_to_the_cli():
    """One home for the CLI's own words. Asserted as a difference in the
    answer rather than as a reading of the source: a level added to
    `agent_runner`'s set is accepted by the coordinator's check without the
    coordinator being edited, which a second copy of the vocabulary there
    could not do.
    """
    invented = "a-level-no-cli-accepts-yet"
    built = build_workflow(workflow_stage(**{EFFORT_KEY: invented}),
                           name="an-invented-level-workflow")
    assert story_coordinator.effort_problems(built) != []

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(agent_runner, "EFFORT_LEVELS", (*LEVELS, invented))
        assert story_coordinator.effort_problems(built) == []


def test_an_unusable_level_refuses_the_run_before_anything_is_created(
    environment, capsys,
):
    """The refusal in full: exit 1, no stage invoked, no inspection invoked, no
    run directory and so no state file inside it, and no branch cut. On the
    terms an unusable cost ceiling is already refused on — the definition is
    wrong rather than the story or the tree, so nothing is spent discovering it.

    The control is the identical run under the same definition with the level
    repaired, which does create each of those and does invoke every stage — so
    the absences above are a refusal having happened rather than a run that
    failed to start for some other reason.
    """
    bad = next(level for level in NOT_LEVELS
               if isinstance(level, str) and level and level not in LEVELS)
    refused = with_a_stage_level(UNDECLARED, bad)
    target, harness = environment(refused)
    before = branches(target)
    runner = Runner(target)

    assert run(target, harness, runner) == 1

    assert runner.calls == []
    assert not run_dir_of(target).exists()
    assert branches(target) == before
    message = capsys.readouterr().err
    assert refused["stages"][0]["name"] in message
    assert repr(bad) in message

    # The control: the same definition, under the same name, with the level
    # repaired.
    repaired = with_a_stage_level(UNDECLARED, LEVELS[0])
    materialize(repaired, harness)
    proceeding = Runner(target)
    assert run(target, harness, proceeding) == 0
    assert proceeding.calls == STAGE_NAMES
    assert (run_dir_of(target) / "state.json").is_file()
    assert branches(target) != before


# ==========================================================================
# What a run hands each invocation
# ==========================================================================


def test_each_stage_is_invoked_with_the_level_its_own_stage_declares(
    environment,
):
    """The property the whole story is for: the levels differ from each other,
    and each invocation is handed the one its own stage declares.

    Read off the calls the run actually made and compared against the
    definition, so an invocation handed some other stage's level is reported
    rather than averaged away.
    """
    target, harness = environment(DECLARED)
    runner = Runner(target)

    assert run(target, harness, runner) == 0

    assert runner.calls == STAGE_NAMES
    assert runner.levels == [(name, DECLARED_LEVELS[name])
                             for name in runner.calls]
    # And no other level reached any invocation: the set observed is the set
    # declared, which a coordinator handing one stage's value to every stage
    # would fail.
    assert {level for _stage, level in runner.levels} == set(
        DECLARED_LEVELS.values())


def test_a_stage_declaring_no_level_is_invoked_without_the_keyword_at_all(
    environment, inspector,
):
    """The compatibility half, in two readings of one definition.

    The first run uses the permissive recorder, which records every keyword it
    was handed: it saw no level, and the control for that reading is the run
    above, where the same recorder under a declaring definition saw one.

    The second run uses a runner whose signature has no such parameter, which is
    what makes the first reading a statement about the call rather than about
    what a permissive fake chose to ignore: the coordinator passing a level
    would raise `TypeError` here. That it would is demonstrated rather than
    assumed — the same runner is handed one directly and refuses it — so the run
    completing is the coordinator having passed none.

    The inspection this definition declares carries no level either, on the same
    terms and for the same reason.
    """
    target, harness = environment(UNDECLARED, label="-recorded")
    recording = Runner(target)

    assert run(target, harness, recording) == 0
    assert recording.calls == STAGE_NAMES
    assert recording.levels == [(name, NO_LEVEL) for name in recording.calls]
    assert [extra for _stage, extra in recording.extras] == [
        {} for _ in recording.calls]

    assert inspector.invocations, "the inspection made no invocation"
    for declared in inspector.declared:
        assert EFFORT_KEY not in declared, declared

    # The other reading: a runner with the signature it had before this story
    # drives the same definition to completion.
    older_target, older_harness = environment(UNDECLARED, label="-older")
    older = RunnerWithoutTheEffortParameter(older_target)

    assert run(older_target, older_harness, older) == 0
    assert older.calls == STAGE_NAMES

    # And that signature really would have refused a level, so the completion
    # above is the keyword's absence rather than a fake that would have taken it.
    with pytest.raises(TypeError):
        RunnerWithoutTheEffortParameter(older_target)(
            "prompt", stage=WRITING, cwd=older_target,
            **{EFFORT_KEY: LEVELS[0]})


def test_the_pre_stage_inspection_carries_the_level_its_own_declaration_names(
    environment, inspector,
):
    """The inspection is a separate invocation the judging stage does not make,
    so the level it thinks at is declared on the `inspection` block rather than
    on the stage — and the two differ here, so which one arrived is observable
    rather than a coincidence.

    The stage runner's own record is read beside it: the judging stage was
    handed its own level in the same run, so "the inspection carried the
    block's" is not the stage's value having been read twice.
    """
    target, harness = environment(DECLARED)
    runner = Runner(target)

    assert run(target, harness, runner) == 0

    assert inspector.invocations, "the pre-stage inspection made no invocation"
    for declared in inspector.declared:
        assert declared.get(EFFORT_KEY) == INSPECTION_LEVEL, declared
        assert declared[EFFORT_KEY] != DECLARED_LEVELS[JUDGING]
    assert runner.level_at(JUDGING) == DECLARED_LEVELS[JUDGING]


def test_a_broad_mode_invocation_carries_no_level_at_all(environment,
                                                          inspector):
    """The standalone sweep is not a workflow stage and has no declaration to
    read one off, so it keeps invoking exactly as it did before this story.

    The control is the same recorder handed to the same function with a level
    supplied, which carries it — so the absence above is this caller supplying
    none rather than a seam that drops whatever it is given.
    """
    target, harness = environment(DECLARED)
    config = harness_config.load_config(target)

    inspection.inspect(target, config, harness, runner=inspector)

    assert inspector.invocations, "broad mode made no invocation"
    for declared in inspector.declared:
        assert EFFORT_KEY not in declared, declared

    # The control, at the seam broad mode reaches: one scope inspected through
    # the same function with a level supplied, which the same recorder sees.
    bound, problem = inspection.bounds(config)
    assert bound is not None, problem
    supplied = InspectionRecorder()
    inspection.inspect_scope(
        inspection.scopes((), config)[0], target, config, harness, bound,
        inspection.blocked_prefixes(harness), supplied,
        **{EFFORT_KEY: INSPECTION_LEVEL})

    assert supplied.invocations, "the control made no invocation"
    for declared in supplied.declared:
        assert declared.get(EFFORT_KEY) == INSPECTION_LEVEL, declared


# ==========================================================================
# The reason recorded beside a declared level
#
# The one section whose subject is what this repository ships. The property is
# held first against definitions built here, with levels and reasons this module
# chose, because what it is about is what a *declaration* has to carry; then the
# same reading is made of every definition under `workflows/`.
# ==========================================================================


def states_the_level(reason: str, level: str) -> bool:
    """Whether a recorded reason states the level it explains, as a word.

    As a word and not as a substring: `high` is a substring of `xhigh`, so a
    containment test would let a reason explaining one level stand in for a
    declaration of the other.
    """
    return re.search(rf"\b{re.escape(str(level))}\b",
                     str(reason).lower()) is not None


def level_declarations(definition: dict) -> list[tuple[str, dict]]:
    """Every level a definition declares, as where-it-is and the mapping that
    declares it.

    A function rather than a loop inside a test, so the properties below can be
    asked of a definition a test builds as readily as of a shipped one — which
    is what lets them be held on values a test chose.
    """
    found = []
    for stage in definition["stages"]:
        if EFFORT_KEY in stage:
            found.append((stage["name"], stage))
        block = stage.get(INSPECTION_KEY)
        if isinstance(block, dict) and EFFORT_KEY in block:
            found.append((f"{stage['name']} {INSPECTION_KEY}", block))
    return found


def levels_with_no_reason_stating_them(definition: dict) -> list[str]:
    """Every declared level whose recorded reason is missing, blank, or does not
    state the level it explains.

    A list rather than an assertion, so the same reading can be made of a
    definition that violates it — which is the control each absence needs.
    """
    return [where for where, declaring in level_declarations(definition)
            if not str(declaring.get(REASON_KEY, "")).strip()
            or not states_the_level(declaring[REASON_KEY],
                                    declaring[EFFORT_KEY])]


def levels_left_undeclared(definition: dict) -> list[str]:
    """Every stage of a definition, and every inspection block on one, that
    declares no level at all."""
    missing = []
    for stage in definition["stages"]:
        if EFFORT_KEY not in stage:
            missing.append(stage["name"])
        block = stage.get(INSPECTION_KEY)
        if isinstance(block, dict) and EFFORT_KEY not in block:
            missing.append(f"{stage['name']} {INSPECTION_KEY}")
    return missing


def test_a_declared_level_is_recorded_where_a_reader_meets_it():
    """How hard a stage thinks is a judgement about where being wrong is
    expensive, and nothing in the harness reads the reason — so the sentence
    beside the value is the only place that judgement survives. A reason that
    never mentions its own level would satisfy a bare "a reason is present"
    check while explaining nothing.

    Held against a definition this test builds, with levels this test chose,
    because that is what the property is about: what a declaration has to
    carry. Both places a level is declared are covered, so neither is left to
    the shipped reading alone.
    """
    assert len(level_declarations(DECLARED)) == len(STAGE_NAMES) + 1
    assert levels_with_no_reason_stating_them(DECLARED) == []
    assert levels_left_undeclared(DECLARED) == []


@pytest.mark.parametrize("reason, reported", [
    (None, True), ("   ", True), ("a level nobody wrote down", True),
], ids=["absent", "blank", "silent about its own level"])
def test_that_reading_reports_a_level_whose_reason_does_not_state_it(reason,
                                                                     reported):
    """The control for the absence above, built rather than mutated from what
    this repository ships: a reason that is absent, blank, or present and silent
    about the level it explains is reported in every case."""
    extra = {} if reason is None else {REASON_KEY: reason}
    built = build_workflow(
        workflow_stage(**{EFFORT_KEY: LEVELS[0]}, **extra),
        name="an-unexplained-level-workflow")

    found = levels_with_no_reason_stating_them(built)
    assert bool(found) is reported
    assert found == [built["stages"][0]["name"]]


def test_the_reading_matches_the_level_as_a_word_and_not_as_a_substring():
    """The trap the story names. One level's name contains another's, so a
    reason explaining the longer one would satisfy a containment test for the
    shorter — leaving a declaration explained by a sentence about a different
    level, and saying so nowhere.

    Being reported below is itself the demonstration: a substring reading could
    not report this definition, because the shorter level's name is right there
    in the reason. The sound case beside it — the same declaration whose reason
    names its own level — is not reported, so the reading is discriminating
    rather than refusing everything.
    """
    shorter, longer = SUBSTRING_PAIR
    wrong = build_workflow(
        workflow_stage(**{EFFORT_KEY: shorter, REASON_KEY: reason_for(longer)}),
        name="a-reason-about-another-level-workflow")
    assert levels_with_no_reason_stating_them(wrong) == [
        wrong["stages"][0]["name"]]

    sound = build_workflow(
        workflow_stage(**{EFFORT_KEY: shorter, REASON_KEY: reason_for(shorter)}),
        name="a-reason-about-its-own-level-workflow")
    assert levels_with_no_reason_stating_them(sound) == []


def shipped_definitions() -> dict[str, dict]:
    """Every workflow under `workflows/`, loaded the way a run loads it."""
    return {path.stem: conftest.shipped_workflow(REPO_ROOT, path.stem)
            for path in sorted((REPO_ROOT / "workflows").glob("*.json"))}


def test_the_definition_this_repository_configures_is_among_those_swept():
    """What the glob has to find for the sweeps below to mean anything. Asked of
    the configured definition rather than of a count: another definition added
    costs nothing to cover, and one removed is a deployment decision these
    sweeps have nothing to say about."""
    configured = conftest.repository_config()["workflow"]
    assert configured in shipped_definitions()


@pytest.mark.parametrize("name", sorted(shipped_definitions()))
def test_every_level_a_shipped_definition_declares_states_itself_in_its_reason(
    name,
):
    """The same reading over what this repository ships, which is why the
    convention exists at all: a developer meeting one of these levels in a
    definition meets the judgement beside it. The built cases above are what say
    this reading can report a violation."""
    assert levels_with_no_reason_stating_them(shipped_definitions()[name]) == []


@pytest.mark.parametrize("name", sorted(shipped_definitions()))
def test_every_shipped_stage_and_inspection_declares_a_level(name):
    """Nothing left at whatever a personal setting happens to say: every stage
    each definition defines, and the inspection each declares, says what it
    thinks at. The control is a definition declaring none, which the same
    reading reports — so the silence here is the declarations being present
    rather than a reading that finds nothing to look at."""
    definition = shipped_definitions()[name]
    assert level_declarations(definition), name
    assert levels_left_undeclared(definition) == []

    assert levels_left_undeclared(UNDECLARED) == (
        STAGE_NAMES + [f"{JUDGING} {INSPECTION_KEY}"])
