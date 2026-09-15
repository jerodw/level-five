"""story-147 validation: a finding about the code a story just wrote is
answered by that story.

The post-story inspection used to file every finding it made as a brief,
including the findings about the diff the run that invoked it had just
produced — so a review comment on a change became a backlog item filed against
whatever story came next. This story moves the inspection ahead of the stage
that judges the change and gives it the destination it was missing: a finding
whose subject is among the files the story changed is written to a
run-directory artifact the judging stage reads, and every other finding is
filed exactly as it was before.

The subjects are kept apart deliberately:

  * **the partition.** `about_the_change` is a pure function over what an
    invocation found and what the run's stages recorded they changed, so it is
    driven as a function: a finding naming a changed file, one naming only a
    file beside the change, one naming both, and one naming nothing at all.

  * **the position.** Observed rather than read off the source. The fake
    Inspector and the fake stage runner write into one journal, so the journal
    itself says the inspection ran between the stage before the judging stage
    and the judging stage — and, under a workflow that declares none, that it
    ran after every stage instead.

  * **the destinations.** A run is driven with each kind of finding in turn and
    the artifact, the outbox queue and the judging stage's rendered prompt are
    read back off what that run actually wrote.

  * **once per attempt.** A correction pass re-entering the judging stage
    within one attempt and a retry that re-enters it on the next, both driven
    as runs through the same fixture, with the invocation count and the
    artifact names read back.

  * **the guarantee.** The entry point is total in the shape its post-story
    sibling is, it makes no commit of its own, its cost reaches the run's
    cost.json and never the live allowance, and nothing in the coordinator
    turns what it wrote into a decision.

Every absence asserted here carries a demonstration that it can fail:

  * "a finding about the change reaches no queue entry" sits beside a finding
    naming only a file beside the change, which under the same run does reach
    one;
  * "a finding about the change is not in the filed set" sits beside the same
    reading over the sibling finding, which is in it;
  * "the pre-stage inspection made no commit" sits beside the same search over
    the run driven under a workflow declaring no inspection, where the
    post-story mode's commit is found;
  * "no second invocation within one attempt" sits beside the retry run, where
    the same counter reads two;
  * "the declaration's keys name no budget" sits beside a copy of the same
    declaration with a budget planted in it, which the same reading reports;
  * "no coordinator call site reads what the pre-stage inspection answered"
    sits beside planted call sites that do, which the same scan reports;
  * "the judging stage renders a statement rather than a blank" sits beside the
    same rendering with the record removed, which is blank.

Nothing here reaches a model. `agent_runner.run_agent` is replaced for every
test in this module by a fake that fails the test if it is called without
having been installed deliberately, and the stages run through a fake runner
handed to `run_story`. Nothing here resolves a baseline out of git; the one
repository it reads is the one it built.

The workflow these runs execute is built by `tests/conftest.py`'s builder and
materialized into a harness root this module owns. The subject is the
mechanism, so the stage list, the artifact name and the declaration's position
are inputs to it: every name below is derived from the definition this module
built rather than from the definition this repository deploys. What this
repository deploys is `tests/test_shipped_workflow_is_valid.py`'s subject.
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import json
import subprocess
from pathlib import Path

import pytest

import agent_runner
import conftest
import harness_config
import inspection
import outbox
import schema_validator
import story_coordinator
import story_inspection
from agent_runner import AgentResult
from conftest import StageRef, workflow_stage

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
ORCHESTRATION = REPO_ROOT / "orchestration"
COORDINATOR_SOURCE = (
    ORCHESTRATION / "story_coordinator.py").read_text(encoding="utf-8")

STORY_ID = conftest.SAMPLE_STORY_ID

#: The artifact the fixture's declaration names. Deliberately not the name this
#: repository deploys: the record reaching the run directory under this name is
#: what says the coordinator reads the name off the declaration rather than
#: carrying one of its own.
FINDINGS_ARTIFACT = "findings-about-this-change.json"

#: The artifact the fixture's correction pass names, on the same terms.
CORRECTION_ARTIFACT = "correction-probe.json"

#: The workflow these runs execute. Three stages, because the correction pass
#: has to re-enter somewhere that is not the judging stage — a pass that
#: re-entered the judge would run nothing before judging itself — and because
#: two writing stages make "the changed set is what every stage recorded" a
#: statement about two records rather than one.
WORKFLOW = conftest.build_workflow(
    workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"}),
    workflow_stage(
        outputs=(conftest.TEST_RESULTS, conftest.TESTER_CHANGED_FILES),
        changed_files=conftest.TESTER_CHANGED_FILES,
        schemas={conftest.TEST_RESULTS: "test-results",
                 conftest.TESTER_CHANGED_FILES: "changed-files"}),
    workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        inspection={"result": FINDINGS_ARTIFACT},
        correction_pass={"result": CORRECTION_ARTIFACT, "budget": 1,
                         "stage": StageRef(1)},
        retry_routing={
            "the-behaviour": {"stage": StageRef(0),
                              "when": "the behaviour the story asked for is missing"},
        }),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="pre-stage-inspection-workflow",
)

STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, JUDGING = STAGE_NAMES

#: The stage the fixture declares the inspection on, and the declaration, both
#: found by the coordinator's own reader rather than by position — so an
#: assertion about "the stage the inspection runs before" cannot disagree with
#: what the coordinator would do with the same definition.
DECLARATION = story_coordinator.inspection_declaration(WORKFLOW["stages"])
DECLARING_STAGE = next(stage["name"] for stage in WORKFLOW["stages"]
                       if stage.get("inspection"))

#: The key the declaration names its run-directory artifact under. Spelled once,
#: here, and derived from this by every assertion that goes looking for the file.
RESULT_KEY = "result"

#: The stage the correction pass re-enters at, read off the declaration.
CORRECTION = next(stage for stage in WORKFLOW["stages"]
                  if "correction_pass" in stage)["correction_pass"]
CORRECTION_ENTRY = CORRECTION["stage"]

#: The one category the fixture's table routes, so a failed verdict has
#: somewhere to go and the run reaches a second attempt.
RETRY_CATEGORY = next(iter(
    next(stage for stage in WORKFLOW["stages"]
         if "on_failure" in stage)["on_failure"]["retry_routing"]))

#: The context field the coordinator injects the inspection's findings under.
#: `conftest.BUILT_PROMPT_FIELDS` predates it, and the fixture says a module
#: needing a field it does not list passes its own template — which is what this
#: does, rather than widening the shared list and changing what every other
#: module's built prompts render.
FINDINGS_FIELD = "inspection_findings"

PROMPTS = {
    name: (conftest.built_stage_prompt(name)
           + f"{FINDINGS_FIELD}:\n{{{{{FINDINGS_FIELD}}}}}\n")
    for name in STAGE_NAMES
}


# ==========================================================================
# The target this module builds
# ==========================================================================


SOURCE_DIR = "src/"
CHANGED_FILE = f"{SOURCE_DIR}changed.py"
SIBLING_FILE = f"{SOURCE_DIR}beside.py"
CHANGED_TEST = "tests/t_changed.py"

#: A file the writing stage creates during the run, which no `TRACKED` entry
#: puts in the target and nothing stages before the inspection runs. It is the
#: condition every file a story creates is in at the moment this inspection is
#: made — on disk, under an inspected scope key, and in no index — and only the
#: runs that ask for it have it.
CREATED_FILE = f"{SOURCE_DIR}created.py"

TRACKED = {
    CHANGED_FILE: "def changed():\n    return 1\n",
    SIBLING_FILE: "def beside():\n    return 2\n",
    CHANGED_TEST: "def check():\n    assert True\n",
}

#: What the fixture allows one inspection to take into scope. Larger than the
#: whole expansion, so nothing below is silently trimmed; what the cap does is
#: `tests/test_a_completed_story_is_inspected.py`'s question.
ROOMY_CAP = 60

SEVERITY_ENUM = schema_validator.load_schema(
    inspection.BRIEF_SCHEMA)["properties"]["severity"]["enum"]

#: The subject of the commit the target is built on, so an ordering read off
#: the journal can tell the tree the run started from from the tree it made.
SETUP_SUBJECT = "the tree this run starts from"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


def build_target(root: Path, *, workflow: str = WORKFLOW["name"],
                 **config_keys) -> Path:
    """A target repository a run of this fixture can execute in.

    The shape `conftest.target_root` builds — its config and its story, read off
    conftest so neither is spelled twice — with the source layout above added,
    a source-dirs declaration so the change is inside an inspected scope, and
    whatever configuration the caller departs from.
    """
    for sub in (".harness/standards", ".harness/stories", ".harness/docs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    config = conftest.CONFIG.format(workflow=workflow)
    config += f"source_dirs:\n  - {SOURCE_DIR}\n"
    config_keys.setdefault(story_inspection.MAX_FILES_KEY, ROOMY_CAP)
    # The lowest severity the scale defines, which is no floor at all. The
    # findings this module builds carry that severity and this module's subject
    # is where a finding goes rather than which findings the floor files; with
    # the harness default in force every one of them would be dropped before
    # the partition could send it anywhere.
    config_keys.setdefault(inspection.MIN_SEVERITY_KEY, str(min(SEVERITY_ENUM)))
    config += "".join(f"{key}: {value}\n"
                      for key, value in sorted(config_keys.items()))
    (root / ".harness" / "config.yaml").write_text(config, encoding="utf-8")
    (root / ".harness" / "stories" / f"{STORY_ID}.yaml").write_text(
        conftest.STORY, encoding="utf-8")
    (root / ".harness" / "standards" / "coding.md").write_text(
        "# Coding Standards\n- keep it simple\n", encoding="utf-8")
    (root / ".harness" / "standards" / "testing.md").write_text(
        "# Testing Standards\n- test everything\n", encoding="utf-8")
    (root / ".harness" / "docs" / "ARCHITECTURE.md").write_text(
        "# Sample Architecture\n", encoding="utf-8")
    for relative, text in TRACKED.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    conftest.init_repository(root, SETUP_SUBJECT)
    return root


@pytest.fixture
def harness(tmp_path) -> Path:
    """A harness root carrying the built workflow and an Inspector template
    this module wrote."""
    return materialize(WORKFLOW, tmp_path / "pre-stage-inspection-harness")


def materialize(workflow: dict, root: Path) -> Path:
    built = conftest.materialize_workflow(workflow, root, prompts=PROMPTS)
    (built / "prompts" / inspection.INSPECTOR_PROMPT).write_text(
        "# an Inspector template this module wrote\n\n"
        "{{framing}}\n\n{{scope_paths}}\n\n{{findings_path}}\n",
        encoding="utf-8")
    return built


@pytest.fixture
def target(tmp_path) -> Path:
    return build_target(tmp_path / "pre-stage-inspection-target")


# ==========================================================================
# The findings, and the fake Inspector that produces them
# ==========================================================================


def finding(slug: str, paths: list[str], marker: str) -> dict:
    """One conforming finding naming the paths the caller chose.

    `marker` is distinctive text, so a search of a rendered prompt is looking
    for *this* finding rather than for any sentence about a defect.
    """
    schema = schema_validator.load_schema(inspection.BRIEF_SCHEMA)
    return {
        "title": f"zzz: {marker}",
        "slug": slug,
        "body": f"{marker}: {' and '.join(paths)} disagree",
        "category": schema["properties"]["category"]["enum"][0],
        "severity": min(SEVERITY_ENUM),
        "confidence": schema["properties"]["confidence"]["enum"][0],
        "effort": schema["properties"]["effort"]["enum"][0],
        "workflow": WORKFLOW["name"],
        "paths": list(paths),
    }


OWN_FINDING = finding("zzz-about-the-change", [CHANGED_FILE],
                      "MARKER-OWN the change itself is wrong")
CREATED_FINDING = finding("zzz-about-the-created-file", [CREATED_FILE],
                          "MARKER-CREATED the file the story wrote is wrong")
SIBLING_FINDING = finding("zzz-beside-the-change", [SIBLING_FILE],
                          "MARKER-SIBLING the file beside it was always wrong")
BOTH_FINDING = finding("zzz-both", [CHANGED_FILE, SIBLING_FILE],
                       "MARKER-BOTH the change disagrees with its neighbour")


class Inspector:
    """Stands in for `agent_runner.run_agent` for the inspection's invocation.

    It reaches no model: it records what it was handed, writes a line into the
    shared journal — which is the whole of how the position below is observed —
    and writes the findings the caller supplied where an invocation writes them.
    """

    def __init__(self, target: Path, journal: Path, *, findings=(),
                 raises: str = ""):
        self.target = Path(target)
        self.journal = Path(journal)
        self.findings = list(findings)
        self.raises = raises
        self.invocations: list[dict] = []
        self.tree = self.target

    def __call__(self, prompt, *, stage, cwd, log_path=None,
                 permission_mode=None, model=None, **declared):
        self.invocations.append({
            "prompt": prompt, "stage": stage, "cwd": Path(cwd),
            # What the index held at the instant this ran, recorded here
            # because it is only answerable here: the run commits its work
            # afterwards, so a listing taken once the run has returned says
            # nothing about the tree the inspection was made against.
            "tracked": _git(Path(cwd), "ls-files").stdout.splitlines(),
        })
        self.tree = Path(cwd)
        with self.journal.open("a", encoding="utf-8") as handle:
            handle.write("inspected\n")
        if self.raises:
            raise RuntimeError(self.raises)
        config = harness_config.load_config(self.tree)
        artifact = inspection.findings_paths(self.tree, config)[0]
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps({"findings": self.findings}),
                            encoding="utf-8")
        return AgentResult(ok=True, result_text="inspected")


class NoInvocationExpected:
    """The default in place of the agent runner: being called at all is the
    failure. Every test that wants an invocation installs its own fake."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **keywords):
        self.calls += 1
        raise AssertionError(
            "the inspection reached agent_runner.run_agent, which this module "
            "replaces so that nothing here can invoke a model")


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    """Substituted for every test in this module, so a path that reaches for
    the real runner fails here rather than talking to a provider."""
    guard = NoInvocationExpected()
    monkeypatch.setattr(agent_runner, "run_agent", guard)
    return guard


# ==========================================================================
# Driving one run
# ==========================================================================


PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


def passing_with(*findings: dict) -> dict:
    return {**PASS, "correctable_findings": [dict(one) for one in findings]}


#: One correctable finding, categorised for the one category the fixture's
#: table declares, so a passing verdict carrying it spends a correction pass
#: rather than escalating on a category the definition does not define.
CORRECTABLE_FINDING = {
    "location": f"{CHANGED_FILE} - the docstring",
    "finding": "MARKER-CORRECTABLE the wording is stale",
    "correction": "MARKER-CORRECTABLE-FIX reword it",
    "category": RETRY_CATEGORY,
}


def failing_into(category: str) -> dict:
    return {
        "status": "failed",
        "blocking_issues": [{
            "severity": "high", "issue": "sample behavior missing",
            "location": CHANGED_FILE,
            "required_behavior": "sample behavior exists",
        }],
        "unverified": [], "retry_recommended": True, "retry_target": category,
    }


class Runner:
    """The fake agent runner the coordinator is handed for the run's stages.

    Each stage writes the artifacts its declaration names and edits the file it
    says it changed, in the tree the coordinator handed it — which since
    story-117 is the worktree the run works in. Every invocation writes a line
    into the shared journal, so the order the stages ran in and the moment the
    inspection ran are read off one file.
    """

    def __init__(self, target_root: Path, journal: Path, verdicts: list[dict],
                 creates=()):
        self.target_root = Path(target_root)
        self.run_dir = conftest.run_dir_for(self.target_root, STORY_ID)
        self.journal = Path(journal)
        self.verdicts = list(verdicts)
        # Paths the writing stage creates and records as created. Nothing
        # stages them, so they are in the tree and in no index when the
        # inspection is made — which is the whole of what a created file is.
        self.creates = tuple(creates)
        self.calls: list[str] = []
        # Recorded per invocation, because two entries to one stage inside one
        # attempt write the same prompt file and the second overwrites the
        # first: what each entry was *given* is a question about that instant,
        # and a later read of the file answers a different one.
        self.prompts: dict[str, list[str]] = {}

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, **declared):
        self.calls.append(stage)
        self.prompts.setdefault(stage, []).append(prompt)
        with self.journal.open("a", encoding="utf-8") as handle:
            handle.write(f"stage {stage}\n")
        tree = Path(cwd) if cwd else self.target_root
        if stage == WRITING:
            _write(self.run_dir / conftest.CHANGED_FILES,
                   {"modified": [CHANGED_FILE],
                    "created": list(self.creates), "deleted": []})
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Did the work.\n", encoding="utf-8")
            (tree / CHANGED_FILE).write_text(
                "def changed():\n    return 11\n", encoding="utf-8")
            for relative in self.creates:
                path = tree / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("def created():\n    return 3\n",
                                encoding="utf-8")
        elif stage == VALIDATING:
            _write(self.run_dir / conftest.TEST_RESULTS, {"tests_written": 1})
            _write(self.run_dir / conftest.TESTER_CHANGED_FILES,
                   {"modified": [CHANGED_TEST], "created": [], "deleted": []})
            (tree / CHANGED_TEST).write_text(
                "def check():\n    assert True  # and again\n",
                encoding="utf-8")
        elif stage == JUDGING:
            verdict = conftest.answering_guidance(
                self.verdicts.pop(0), self.run_dir)
            _write(self.run_dir / conftest.VERIFICATION_RESULT, verdict)
            if verdict["status"] == "failed":
                _write(self.run_dir / conftest.RETRY_GUIDANCE, {
                    "current_focus": [{
                        "focus": "make the sample behavior exist",
                        "satisfied_when": "the sample behavior exists",
                    }],
                    "preserve_behavior": ["existing behavior"],
                    "retry_scope": [CHANGED_FILE],
                })
        return AgentResult(ok=True, result_text=f"{stage} done")


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class Run:
    """One driven run, with everything an assertion below reads off it."""

    def __init__(self, code: int, target: Path, journal: Path,
                 inspector: Inspector, runner: Runner):
        self.code = code
        self.target = target
        self.journal = journal
        self.inspector = inspector
        self.runner = runner

    @property
    def run_dir(self) -> Path:
        return conftest.run_dir_for(self.target, STORY_ID)

    @property
    def tree(self) -> Path:
        return conftest.run_root_for(self.target, STORY_ID)

    @property
    def state(self) -> dict:
        return json.loads(
            (self.run_dir / "state.json").read_text(encoding="utf-8"))

    @property
    def lines(self) -> list[str]:
        if not self.journal.is_file():
            return []
        return [line for line in
                self.journal.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    @property
    def messages(self) -> list[str]:
        path = self.run_dir / "events.log"
        if not path.is_file():
            return []
        return [line.split("] ", 1)[-1]
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def artifact(self, attempt: int = 1, *,
                 artifact: str = FINDINGS_ARTIFACT) -> Path:
        """Where this attempt's findings record is, through the same function
        that writes the name — so what is read cannot drift from what was
        written."""
        return self.run_dir / story_inspection.findings_artifact_file(
            artifact, attempt)

    def findings_record(self, attempt: int = 1) -> dict:
        return json.loads(
            self.artifact(attempt).read_text(encoding="utf-8"))

    def prompt(self, stage: str, attempt: int = 1, try_number: int = 0) -> str:
        return (self.run_dir / story_coordinator.prompt_file(
            stage, attempt, try_number)).read_text(encoding="utf-8")

    @property
    def queue(self) -> list[dict]:
        """Every entry the run's own outbox queue holds, read in the tree the
        run works in — which is where the sweep that ships them looks."""
        entries = outbox.entry_files(outbox.queue_dir(self.tree))
        return [json.loads(path.read_text(encoding="utf-8"))
                for path in entries]

    @property
    def subjects(self) -> list[str]:
        return _git(self.tree, "log", "--format=%s").stdout.splitlines()

    @property
    def cost(self) -> dict:
        path = self.run_dir / "cost.json"
        if not path.is_file():
            return {"invocations": [], "total_usd": 0}
        return json.loads(path.read_text(encoding="utf-8"))

    @property
    def history_records(self) -> list[dict]:
        """The cross-run inspection log's records, found by the declaration
        that routes this kind rather than by a filename written here."""
        found: list[dict] = []
        for relative in story_inspection.record_paths(self.tree, {}):
            path = self.tree / relative
            if path.is_file():
                found += [json.loads(line) for line
                          in path.read_text(encoding="utf-8").splitlines()
                          if line.strip()]
        return found


def drive(target: Path, harness: Path, tmp_path: Path, *,
          findings=(), verdicts=(PASS,), raises: str = "",
          creates=(), name: str = "journal") -> Run:
    """One run of the fixture with the Inspector installed, ready to read.

    The Inspector is put in place of the guard the autouse fixture installed.
    That fixture set the attribute through monkeypatch, so the *original*
    runner is what is restored when the test ends, whatever this leaves there —
    which is why nothing here has to undo it, and why nothing here can leave a
    fake behind for the next module.
    """
    journal = tmp_path / f"{name}.txt"
    inspector = Inspector(target, journal, findings=findings, raises=raises)
    agent_runner.run_agent = inspector
    stage_runner = Runner(target, journal, list(verdicts), creates=creates)
    code = story_coordinator.run_story(
        STORY_ID, harness, target, stage_runner, sleep=lambda _seconds: None)
    return Run(code, target, journal, inspector, stage_runner)


@pytest.fixture
def driven(target, harness, tmp_path):
    """A factory, so one test can hold a subject and its control side by side."""
    def make(**kwargs) -> Run:
        return drive(target, harness, tmp_path, **kwargs)
    return make


# ==========================================================================
# The fixture is what it claims to be
# ==========================================================================


def test_the_fixture_declares_the_inspection_on_the_stage_that_judges():
    """The premise every case below rests on.

    Read through the coordinator's own reader rather than by position, and
    asserted to be the judging stage rather than any stage — an inspection
    declared on the first stage would run before anything had changed, and
    every assertion about what it found would be vacuous.
    """
    assert DECLARATION[RESULT_KEY] == FINDINGS_ARTIFACT
    assert DECLARING_STAGE == JUDGING
    assert DECLARING_STAGE == STAGE_NAMES[-1]
    assert [stage["name"] for stage in WORKFLOW["stages"]
            if stage.get("inspection")] == [JUDGING]


def test_the_correction_pass_reenters_somewhere_other_than_the_judging_stage():
    """The premise the once-per-attempt case rests on: a pass that re-entered
    the judge would judge itself without running anything, and the second entry
    to the judging stage would be indistinguishable from the first."""
    assert CORRECTION_ENTRY in STAGE_NAMES
    assert CORRECTION_ENTRY != JUDGING


def test_neither_orchestration_module_carries_a_findings_artifact_name():
    """What makes "the coordinator reads the name off the declaration" an
    observation rather than a coincidence.

    The name the runs below look for in a run directory is the fixture's, and
    it reaches the run directory because the declaration carried it there. The
    other half is that orchestration writes no such name of its own: a filename
    in the source would be a name a workflow could not change. Prose may name
    what code may not, so what executes is read rather than what is written
    above it, and the control is a name the code does own.
    """
    for module in (story_coordinator, story_inspection):
        body = executable_source(
            Path(module.__file__).read_text(encoding="utf-8"))
        assert FINDINGS_ARTIFACT not in body, module.__name__
        assert not any(name.startswith("story-inspection")
                       for name in json_literals(body)), module.__name__

    # The control: the same reading over a source that does carry such a name
    # reports it, so the absences above are a scan that can see one rather than
    # one that has stopped looking.
    planted = executable_source(
        f'def where(run_dir):\n    return run_dir / "{FINDINGS_ARTIFACT}"\n')
    assert json_literals(planted) == {FINDINGS_ARTIFACT}


def executable_source(text: str) -> str:
    """`text` with docstrings and comment lines removed."""
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


def json_literals(source: str) -> set[str]:
    """Every string constant in a source that names a `.json` file."""
    return {node.value for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value.endswith(".json")}


# ==========================================================================
# The partition, driven as a function
# ==========================================================================


class _Found:
    """What `about_the_change` reads: a finding with the scope it came from.

    Built here rather than taken from an invocation, because the partition is a
    function over findings and the invocation is not its subject. It carries
    only the attribute the partition reads, so a partition that started reading
    something else would fail here rather than quietly agreeing.
    """

    def __init__(self, found: dict):
        self.finding = found


CHANGED_SET = (CHANGED_FILE, CHANGED_TEST)


def partition(*findings: dict):
    own, others = story_inspection.about_the_change(
        [_Found(one) for one in findings], CHANGED_SET)
    return ([one.finding for one in own], [one.finding for one in others])


def test_a_finding_naming_a_file_the_story_changed_is_the_storys_own():
    own, others = partition(OWN_FINDING)
    assert own == [OWN_FINDING]
    assert others == []


def test_a_finding_naming_only_a_file_beside_the_change_is_the_backlogs():
    """The control the case above needs: the same reading over a finding whose
    paths name nothing in the changed set puts it the other way."""
    own, others = partition(SIBLING_FINDING)
    assert own == []
    assert others == [SIBLING_FINDING]


def test_a_finding_naming_both_is_the_storys_own():
    """The deliberate asymmetry: a finding that touches the change at all is a
    review comment on it, so naming a neighbour beside it does not move it to
    the backlog."""
    own, others = partition(BOTH_FINDING)
    assert own == [BOTH_FINDING]
    assert others == []


def test_a_finding_citing_a_line_is_still_matched_against_the_changed_set():
    """The changed set never cites a line and a finding may, so the match is on
    bare paths — the same derivation a brief's identity is built from."""
    cited = finding("zzz-cited", [f"{CHANGED_FILE}:12"], "MARKER-CITED")
    own, others = partition(cited)
    assert own == [cited]
    assert others == []


@pytest.mark.parametrize("paths", [[], ["docs/elsewhere.md"]],
                         ids=["naming nothing", "naming only a stranger"])
def test_a_finding_naming_nothing_in_the_changed_set_is_the_backlogs(paths):
    """A finding the partition cannot attribute to the change goes where every
    finding went before this story: to the backlog."""
    stranger = finding("zzz-stranger", paths, "MARKER-STRANGER")
    own, others = partition(stranger)
    assert own == []
    assert others == [stranger]


def test_the_partition_keeps_every_finding_it_was_given():
    """Nothing is dropped between the two halves, which is what makes the
    counts on the summary line add up: a finding is answered by the story or it
    is filed, and there is no third place."""
    own, others = partition(OWN_FINDING, SIBLING_FINDING, BOTH_FINDING)
    assert own + others == [OWN_FINDING, BOTH_FINDING, SIBLING_FINDING]


# ==========================================================================
# The position, observed rather than inferred
# ==========================================================================


def test_the_inspection_runs_before_the_judging_stage_and_not_after_the_run(
        driven):
    """Observed from the order the calls were made.

    The Inspector and the stage runner write into one journal, so the journal
    says the inspection happened after the stage before the judging stage and
    before the judging stage itself — and exactly once, which is what says
    `_complete` did not inspect again.
    """
    run = driven(findings=[OWN_FINDING])

    assert run.code == 0
    assert run.runner.calls == [WRITING, VALIDATING, JUDGING]
    assert run.lines == [f"stage {WRITING}", f"stage {VALIDATING}",
                         "inspected", f"stage {JUDGING}"], run.lines
    assert len(run.inspector.invocations) == 1


def without_the_declaration(tmp_path: Path, target: Path) -> Path:
    """A harness root carrying the fixture with the inspection declaration
    removed, and `target` configured to run it.

    The probe-workflow idiom the clean-clone and correction-pass modules
    already use: a definition derived from the fixture by mutating the single
    declaration the test is about, so "the key's absence leaves the mechanism
    where it was" is driven as a run rather than argued from source.

    It keeps the fixture's *name* and changes only the harness root it is
    written into, so the target's configuration is untouched and the findings
    this module builds — which name the workflow they were found under, as a
    brief must — are the same findings under both roots. A renamed probe would
    make every one of them name a workflow the harness does not define, and the
    run would drop them all before the filing this case is about.
    """
    workflow = json.loads(json.dumps(WORKFLOW))
    for stage in workflow["stages"]:
        stage.pop("inspection", None)
    assert story_coordinator.inspection_declaration(workflow["stages"]) == {}
    return materialize(workflow, tmp_path / "no-inspection-declared-harness")


def test_a_workflow_declaring_no_inspection_inspects_after_the_run_as_before(
        target, tmp_path):
    """The other half of the story, and the control for the position above.

    The same fixture with the one declaration removed: the inspection happens
    after every stage rather than before the judge, the finding about the
    change is filed as a brief exactly as it was before this story, and no
    findings artifact is written at all.
    """
    harness = without_the_declaration(tmp_path, target)
    run = drive(target, harness, tmp_path, findings=[OWN_FINDING],
                name="undeclared-journal")

    assert run.code == 0
    assert run.lines == [f"stage {WRITING}", f"stage {VALIDATING}",
                         f"stage {JUDGING}", "inspected"], run.lines
    assert len(run.inspector.invocations) == 1
    assert [entry["key"] for entry in run.queue], "nothing was filed"
    assert not run.artifact().exists()
    # And the post-story mode's own commit is there, which is the control for
    # "the pre-stage inspection makes no commit" below.
    assert story_inspection.COMMIT_SUBJECT.format(story_id=STORY_ID) \
        in run.subjects


# ==========================================================================
# story-148: one scope, reached the same way from both positions
# ==========================================================================


def calls_to(tree: ast.AST, name: str) -> list[ast.Call]:
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == name]


def argument_shape(call: ast.Call) -> tuple:
    """What one call site passes, as something two sites can be compared by."""
    return (
        tuple(arg.id if isinstance(arg, ast.Name) else ast.dump(arg)
              for arg in call.args),
        tuple(sorted((keyword.arg or "**", ast.dump(keyword.value))
                     for keyword in call.keywords)),
    )


def enclosing_function(tree: ast.AST, call: ast.Call) -> ast.FunctionDef:
    """The function a call sits in — the innermost, since a nested definition
    is inside its parent's walk as well as its own."""
    holders = [node for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef)
               and any(one is call for one in ast.walk(node))]
    assert holders, "the call is not inside a function"
    return max(holders, key=lambda node: node.lineno)


def test_both_positions_reach_the_scope_through_one_call_with_one_shape():
    """Nothing tells the expansion which position it is answering for.

    The scope is built at exactly one call site, with bare arguments and no
    keyword a caller could switch a mode with; and every call to the function
    holding that site — which is how both positions reach it — passes the same
    arguments in the same order. A per-position listing would have to appear as
    a second call site or as a shape that differs between them, and neither can
    hide from this reading.
    """
    tree = ast.parse(
        Path(story_inspection.__file__).read_text(encoding="utf-8"))
    sites = calls_to(tree, "expansion")
    assert len(sites) == 1, [site.lineno for site in sites]
    assert argument_shape(sites[0])[1] == ()

    holder = enclosing_function(tree, sites[0])
    reaching = calls_to(tree, holder.name)
    assert len(reaching) > 1, "only one position reaches the scope"
    assert len({argument_shape(one) for one in reaching}) == 1, holder.name

    # The control for both absences: the same reading over a source where the
    # two positions do differ reports two shapes, so what it found above is
    # agreement rather than a reading that has stopped looking.
    planted = ast.parse("def before(r):\n    return prepare(r, staged=False)\n"
                        "def after(r):\n    return prepare(r, staged=True)\n")
    assert len({argument_shape(one)
                for one in calls_to(planted, "prepare")}) == 2


# ==========================================================================
# Where each kind of finding goes
# ==========================================================================


def filed_markers(run: Run) -> list[str]:
    """The marker text of every finding the run's queue holds.

    Matched against the entry as a whole rather than against its key: a queue
    entry's key is the identity the filing derives, and what says *which
    finding* was filed is the finding's own words, which is what a marker is
    for.
    """
    entries = [json.dumps(entry) for entry in run.queue]
    return sorted(
        marker for marker in (OWN_FINDING["body"], SIBLING_FINDING["body"],
                              BOTH_FINDING["body"], CREATED_FINDING["body"])
        if any(marker in entry for entry in entries))


def test_a_finding_about_the_change_reaches_the_artifact_and_no_queue_entry(
        driven):
    """The criterion this story exists for: a review comment on the change is
    given to the stage that can still answer it, and is not filed as a backlog
    item against whatever story comes next."""
    run = driven(findings=[OWN_FINDING])

    record = run.findings_record()
    assert record["ran"] is True
    assert record["findings"] == [OWN_FINDING]
    assert record["attempt"] == 1
    assert record["story_id"] == STORY_ID
    assert run.queue == []


def scope_lines(run: Run) -> list[str]:
    """The paths the inspection's own invocation was given.

    The fixture's Inspector template renders `scope_paths` as the whole of a
    block of its own, one path per line, so a bare path on a line of the prompt
    is a path the scope carried — and a path merely mentioned in the framing is
    not one.
    """
    assert run.inspector.invocations, "no inspection invocation was made"
    prompt = run.inspector.invocations[0]["prompt"]
    return [line.strip() for line in prompt.splitlines() if line.strip()]


def test_a_file_the_story_created_reaches_the_scope_the_inspector_is_given(
        driven):
    """The pre-stage inspection runs before anything has committed or staged
    the run's work, so every file the story created is in the tree and in no
    index at that moment — which the invocation's own record of the index says
    here rather than this test assuming it. The scope carries it anyway, beside
    the modified file that was tracked all along.
    """
    run = driven(findings=[CREATED_FINDING], creates=[CREATED_FILE])

    assert run.code == 0
    tracked = run.inspector.invocations[0]["tracked"]
    assert CREATED_FILE not in tracked, "the premise is gone"
    assert CHANGED_FILE in tracked

    assert CREATED_FILE in scope_lines(run)
    assert CHANGED_FILE in scope_lines(run)


def test_a_finding_about_a_file_the_story_created_is_answered_by_the_story(
        driven):
    """The outcome the scope above exists for.

    A review comment on a file this story wrote reaches the stage that can still
    answer it and is filed against no later story. The control is the sibling
    case below: under the same run, the same floor and the same queue, a finding
    naming a file the story did not touch is filed and reaches no artifact.
    """
    run = driven(findings=[CREATED_FINDING], creates=[CREATED_FILE])

    assert run.findings_record()["findings"] == [CREATED_FINDING]
    assert filed_markers(run) == []
    assert run.queue == []


def test_a_finding_beside_the_change_is_filed_and_is_not_in_the_artifact(
        driven):
    """The control the absence above needs: under the same run, with the same
    floor, the same cap and the same queue, a finding whose paths name nothing
    the story changed reaches the queue and reaches the artifact not at all."""
    run = driven(findings=[SIBLING_FINDING])

    assert run.findings_record()["findings"] == []
    assert filed_markers(run) == [SIBLING_FINDING["body"]]


def test_a_finding_naming_a_changed_file_and_one_beside_it_reaches_the_stage(
        driven):
    """Treated as the story's own, which is the file-level attribution this
    story took knowingly: it reaches the verifier rather than the tracker."""
    run = driven(findings=[BOTH_FINDING])

    assert run.findings_record()["findings"] == [BOTH_FINDING]
    assert run.queue == []


def test_the_two_kinds_are_separated_within_one_invocation(driven):
    """Both kinds from one invocation, so the split is the partition's rather
    than an artefact of which finding the run happened to carry."""
    run = driven(findings=[OWN_FINDING, SIBLING_FINDING, BOTH_FINDING])

    record = run.findings_record()
    assert record["findings"] == [OWN_FINDING, BOTH_FINDING]
    assert filed_markers(run) == [SIBLING_FINDING["body"]]


def test_the_summary_says_how_many_were_answered_by_the_story(driven):
    """A reader comparing the findings count against the filed count would read
    the difference as a silent drop, which is the one thing every count on that
    line exists to make impossible — so the line says where they went."""
    run = driven(findings=[OWN_FINDING, SIBLING_FINDING])

    summaries = [line for line in run.messages if "finding(s)" in line]
    assert len(summaries) == 1, run.messages
    assert "2 finding(s)" in summaries[0]
    assert "1 filed" in summaries[0]
    assert "answered by the story: 1" in summaries[0]


def test_a_run_that_answered_nothing_says_nothing_about_answering(driven):
    """The control for the clause above: a run whose every finding was filed
    carries the counts and not the clause, so the clause is a fact about this
    run rather than a fixed suffix."""
    run = driven(findings=[SIBLING_FINDING])

    summaries = [line for line in run.messages if "finding(s)" in line]
    assert len(summaries) == 1, run.messages
    assert "answered by the story" not in summaries[0]


def test_the_filing_from_here_goes_through_the_same_severity_floor(
        tmp_path, harness):
    """A finding that is not the story's own is filed on exactly the terms it
    was filed on before this story, and the floor is one of them.

    The same run with the floor raised above the finding's own severity: it is
    dropped beneath the floor and reaches no queue entry. The control is the
    run beside it, with the floor where every other case here puts it, where
    the same finding is filed — so the absence is the floor's doing rather than
    the partition's.
    """
    above = min(SEVERITY_ENUM) + 1
    assert above in SEVERITY_ENUM, SEVERITY_ENUM

    floored = drive(
        build_target(tmp_path / "floored",
                     **{inspection.MIN_SEVERITY_KEY: above}),
        harness, tmp_path, findings=[SIBLING_FINDING], name="floored-journal")
    assert floored.code == 0
    assert filed_markers(floored) == []
    assert any(inspection.BENEATH_THE_FLOOR in line
               for line in floored.messages), floored.messages

    unfloored = drive(build_target(tmp_path / "unfloored"), harness, tmp_path,
                      findings=[SIBLING_FINDING], name="unfloored-journal")
    assert filed_markers(unfloored) == [SIBLING_FINDING["body"]]


def test_the_filing_from_here_goes_through_the_same_brief_cap(
        tmp_path, harness):
    """The cap on briefs is `inspection.file_findings`'s, so this producer
    files under exactly the terms the broad mode does.

    Two findings for the backlog against a cap of one: the second is dropped
    past the cap and named as such. The control is the same pair with the cap
    at two, where both are filed.
    """
    second = finding("zzz-beside-again", [SIBLING_FILE],
                     "MARKER-SIBLING-TWO another long-standing disagreement")
    pair = [SIBLING_FINDING, second]

    capped = drive(
        build_target(tmp_path / "capped",
                     **{inspection.MAX_FINDINGS_KEY: 1}),
        harness, tmp_path, findings=pair, name="capped-journal")
    assert capped.code == 0
    assert len(capped.queue) == 1
    assert any(inspection.PAST_THE_CAP in line
               for line in capped.messages), capped.messages

    roomy = drive(
        build_target(tmp_path / "roomy",
                     **{inspection.MAX_FINDINGS_KEY: len(pair)}),
        harness, tmp_path, findings=pair, name="roomy-journal")
    assert len(roomy.queue) == len(pair)


# ==========================================================================
# What the judging stage is given
# ==========================================================================


def rendered_findings(prompt: str) -> str:
    """What the fixture's template rendered under the findings label.

    Everything after the label, because the fixture appends this field last and
    a findings record is many lines — a reader that took the line below the
    label would read the first line of a JSON object and call it the value.
    """
    label = f"{FINDINGS_FIELD}:"
    where = prompt.index(f"\n{label}\n")
    return prompt[where + len(label) + 2:]


#: A record the fixture's template renders that no run below ever produces,
#: used as the rendering of an absent record. Taken from
#: `conftest.BUILT_PROMPT_FIELDS` rather than written as a literal: what the
#: assembler renders for a keyword nobody passed is the assembler's convention,
#: and an assertion spelling it here would be a second copy of it.
ABSENT_RECORD_FIELD = "revert_check_result"


def rendered_line(prompt: str, name: str) -> str:
    """The one-line value the fixture template rendered under a label."""
    lines = prompt.splitlines()
    return lines[lines.index(f"{name}:") + 1]


def test_the_judging_stages_prompt_carries_the_findings_about_the_change(
        driven):
    """Not merely written to a file nobody reads: the record reaches the stage
    that decides, by its own words."""
    run = driven(findings=[OWN_FINDING])

    rendered = rendered_findings(run.prompt(JUDGING))
    assert OWN_FINDING["body"] in rendered
    assert json.loads(rendered.strip())["findings"] == [OWN_FINDING]


def test_a_filed_finding_does_not_reach_the_judging_stages_prompt(driven):
    """The control: the same reading over a run whose finding was filed finds
    the section rendered and that finding absent from it, so the case above is
    a statement about which findings reach the stage."""
    run = driven(findings=[SIBLING_FINDING])

    rendered = rendered_findings(run.prompt(JUDGING))
    assert SIBLING_FINDING["body"] not in rendered
    assert json.loads(rendered.strip())["findings"] == []


def test_an_inspection_that_found_nothing_renders_a_statement_not_a_blank(
        driven):
    """An absence a reader takes for agreement is the failure this record
    exists against, so an inspection that ran and found nothing says both."""
    run = driven(findings=[])

    record = run.findings_record()
    assert record["ran"] is True
    assert record["findings"] == []
    rendered = json.loads(rendered_findings(run.prompt(JUDGING)).strip())
    assert rendered["ran"] is True


def test_an_inspection_that_could_not_run_says_so_and_costs_the_run_nothing(
        target, harness, tmp_path):
    """The Inspector broken at the invocation, constructed rather than assumed.

    The stage is told the inspection could not be made rather than being handed
    nothing, and the run is indifferent to it: it completes with the exit status
    a run whose inspection worked has.
    """
    run = drive(target, harness, tmp_path, findings=[OWN_FINDING],
                raises="no provider answered", name="broken-journal")

    assert run.code == 0
    assert run.state["status"] == "completed"
    record = run.findings_record()
    assert record["ran"] is False
    assert record["reason"].strip(), record
    rendered = json.loads(rendered_findings(run.prompt(JUDGING)).strip())
    assert rendered["ran"] is False


def test_the_mechanism_switched_off_still_tells_the_stage_it_is_off(
        tmp_path, harness, no_model):
    """The cap unset is the mechanism off. No invocation is made — the guard
    installed for every test here is what makes that observable — and the stage
    is still handed a statement saying so rather than an absence it would have
    to interpret, which is also what keeps the once-per-attempt rule one rule.
    """
    target = build_target(tmp_path / "switched-off",
                          **{story_inspection.MAX_FILES_KEY: ""})
    journal = tmp_path / "off-journal.txt"
    code = story_coordinator.run_story(
        STORY_ID, harness, target, Runner(target, journal, [PASS]),
        sleep=lambda _seconds: None)
    run = Run(code, target, journal, Inspector(target, journal), None)

    assert code == 0
    assert no_model.calls == 0
    record = run.findings_record()
    assert record["ran"] is False
    assert record["findings"] == []


def test_a_stage_declaring_no_inspection_renders_nothing_for_it(driven):
    """The control for the renderings above, and the guarantee that every stage
    of every workflow declaring none is unchanged.

    A stage the declaration does not name is handed nothing for it, and what
    "nothing" renders as is the assembler's own convention — read off a record
    in the same prompt that this run never produces, rather than written down
    here.
    """
    run = driven(findings=[OWN_FINDING])

    for stage in (WRITING, VALIDATING):
        prompt = run.prompt(stage)
        assert rendered_findings(prompt).strip() == \
            rendered_line(prompt, ABSENT_RECORD_FIELD).strip()
    # And the declaring stage's rendering is not that, which is what makes the
    # equality above a statement about the stages that declare none.
    judged = run.prompt(JUDGING)
    assert rendered_findings(judged).strip() != \
        rendered_line(judged, ABSENT_RECORD_FIELD).strip()


# ==========================================================================
# Once per attempt
# ==========================================================================


def test_a_correction_pass_reentering_the_judge_makes_no_second_invocation(
        driven):
    """A re-entry within one attempt reuses what the attempt already wrote.

    The pass re-enters at the declared stage and runs forward to the judge
    again, so the judging stage is invoked twice inside attempt 1 — and the
    inspection is invoked once, with the second entry rendering the record the
    first one wrote.
    """
    run = driven(findings=[OWN_FINDING],
                 verdicts=[passing_with(CORRECTABLE_FINDING), PASS])

    assert run.code == 0
    assert run.runner.calls.count(JUDGING) == 2
    assert run.state["retry_count"] == 0
    assert len(run.inspector.invocations) == 1
    assert run.lines.count("inspected") == 1
    # Both entries to the judging stage rendered the one record the attempt
    # wrote, so the second was given evidence rather than a blank. Read off
    # what each entry was handed rather than off the prompt file, which the
    # second entry overwrote.
    given = [json.loads(rendered_findings(one).strip())
             for one in run.runner.prompts[JUDGING]]
    assert len(given) == 2
    assert given[0] == given[1]
    assert given[1]["findings"] == [OWN_FINDING]
    assert given[1]["attempt"] == 1
    # One file, rather than a second under a name of its own — which is the
    # control the retry case below is the other half of.
    assert [path.name for path in run.run_dir.glob("*")
            if path.name.startswith(FINDINGS_ARTIFACT.split(".")[0])] == \
        [run.artifact(1).name]


def test_a_retry_inspects_again_under_the_next_attempts_name(driven):
    """The control the case above needs, and a criterion in its own right: a
    retry means the code changed, and a stale reading is worse than none.

    The same fixture, a failed verdict routing a retry, so the run reaches
    attempt 2 — where a second invocation is made and lands under a name of its
    own rather than finding attempt 1's and skipping.
    """
    run = driven(findings=[OWN_FINDING],
                 verdicts=[failing_into(RETRY_CATEGORY), PASS])

    assert run.code == 0
    assert run.state["retry_count"] == 1
    assert len(run.inspector.invocations) == 2
    assert run.lines.count("inspected") == 2
    assert run.artifact(1).is_file()
    assert run.artifact(2).is_file()
    assert run.artifact(1) != run.artifact(2)
    assert run.findings_record(2)["attempt"] == 2
    # And attempt 2's prompt renders attempt 2's reading rather than attempt 1's.
    rendered = json.loads(
        rendered_findings(run.prompt(JUDGING, attempt=2)).strip())
    assert rendered["attempt"] == 2


def test_the_artifact_name_is_keyed_by_the_attempt_that_wrote_it():
    """The keying, driven as the function that composes it: two attempts cannot
    land on one name, which is the whole of the once-per-attempt rule."""
    names = {story_inspection.findings_artifact_file(FINDINGS_ARTIFACT, n)
             for n in (1, 2, 3)}
    assert len(names) == 3
    assert all(name.endswith(".json") for name in names)
    assert FINDINGS_ARTIFACT not in names


def test_a_resume_does_not_leave_the_previous_entrys_reading_behind():
    """A resume zeroes the retry count, so the next entry's attempt 1 would
    land on the ending entry's attempt 1 — and would find it and skip
    inspecting, handing a resumed run a reading of a tree it no longer has.

    Asked of the coordinator's own list of what an entry takes with it, with
    the control beside it: a workflow declaring no inspection contributes no
    such name, so the name being there is a fact about the declaration.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as temporary:
        run_dir = Path(temporary)
        (run_dir / story_inspection.findings_artifact_file(
            FINDINGS_ARTIFACT, 1)).write_text("{}", encoding="utf-8")

        carried = story_coordinator.inspection_artifacts(
            run_dir, WORKFLOW["stages"], "*")
        assert carried == [story_inspection.findings_artifact_file(
            FINDINGS_ARTIFACT, 1)]
        assert set(carried) <= set(
            story_coordinator.entry_artifacts(run_dir, WORKFLOW["stages"]))

        undeclared = [{"name": name} for name in STAGE_NAMES]
        assert story_coordinator.inspection_artifacts(
            run_dir, undeclared, "*") == []


# ==========================================================================
# The guarantee: it supplies and does not decide
# ==========================================================================


def test_the_entry_point_offers_no_way_to_stop_a_run(driven):
    """Its whole signature and its return, in the terms its post-story
    sibling's own equivalent assertion uses.

    A parameter added later that a call site could set to make the inspection
    refuse is reported rather than absorbed, and the value it answers with is
    None on a path that did everything it could do.
    """
    parameters = inspect_module.signature(
        story_inspection.inspect_before_stage).parameters
    sibling = inspect_module.signature(
        story_inspection.inspect_after_story).parameters
    assert list(parameters) == [
        "run_dir", "target_root", "config", "harness_root", "story_id",
        "stages", "artifact", "attempt", "runner"]
    # Everything the post-story entry point takes, and nothing beyond what this
    # mode needs to name its artifact: no parameter by which a caller could be
    # told to stop.
    assert set(sibling) <= set(parameters)
    assert set(parameters) - set(sibling) == {"artifact", "attempt"}

    run = driven(findings=[OWN_FINDING])
    config = harness_config.load_config(run.tree)
    answered = story_inspection.inspect_before_stage(
        run.run_dir, run.tree, config, run.inspector.invocations[0]["cwd"],
        STORY_ID, WORKFLOW["stages"], artifact=FINDINGS_ARTIFACT, attempt=9,
        runner=Inspector(run.target, run.journal, findings=[OWN_FINDING]))
    assert answered is None


def pre_stage_calls_in(source: str) -> list[ast.AST]:
    """Every call to the pre-stage entry point in a source, as the node
    enclosing it.

    The enclosing statement rather than the call, because what the rule is about
    is not that the inspection is called but what is done with what it answers:
    a bare expression statement discards it, and anything else is a call site
    that could turn an inspection into a decision.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.Call):
                continue
            target = child.func
            if (isinstance(target, ast.Attribute)
                    and target.attr ==
                    story_inspection.inspect_before_stage.__name__
                    and isinstance(target.value, ast.Name)
                    and target.value.id == story_inspection.__name__):
                found.append(node)
    return found


def test_the_coordinator_calls_it_once_and_reads_nothing_from_it():
    """One call site, and it cannot turn an inspection into a decision."""
    calls = pre_stage_calls_in(COORDINATOR_SOURCE)
    assert len(calls) == 1
    assert [node for node in calls if not isinstance(node, ast.Expr)] == []


@pytest.mark.parametrize("planted", [
    "    outcome = story_inspection.inspect_before_stage(run_dir)\n",
    "    if story_inspection.inspect_before_stage(run_dir).blocked:\n"
    "        return 1\n",
    "    return story_inspection.inspect_before_stage(run_dir)\n",
])
def test_the_scan_reports_a_call_site_that_reads_what_it_answered(planted):
    """Control: the empty list above is a fact about the coordinator's one call
    site rather than about a scan that has stopped seeing a use."""
    source = f"def a_function(run_dir):\n{planted}"
    found = pre_stage_calls_in(source)
    assert len(found) == 1
    assert [node for node in found if not isinstance(node, ast.Expr)] != []


def test_the_record_the_stage_reads_is_read_only_to_be_rendered():
    """Nothing branches on the findings artifact.

    The name the coordinator reads the record into is traced through the
    function that runs the stage loop: it is assigned, it is handed to the
    context builder, and it appears in no test, no comparison and no return.
    The control beside it is the same reading over a copy of that source with a
    branch on the same name planted in it.
    """
    source = conftest.function_source(COORDINATOR_SOURCE, "run_story")
    assert branches_on(source, FINDINGS_FIELD) == []
    planted = source + (
        f"\n    if {FINDINGS_FIELD}:\n        return 1\n")
    assert branches_on(planted, FINDINGS_FIELD) != []


def branches_on(source: str, name: str) -> list[ast.AST]:
    """Every conditional, comparison or return in `source` that reads `name`.

    Parsed rather than searched for as text, so a mention inside a comment or a
    string is not a branch and a branch spelled unusually is still one.

    Only the *deciding* expression of each node is read — a conditional's test,
    a return's value — and never its body. A whole stage loop is one `while`,
    so a reading that walked the body would report every name in the function
    and say nothing about any of them.
    """
    found = []
    for node in ast.walk(ast.parse(source.strip())):
        if isinstance(node, (ast.If, ast.While, ast.IfExp)):
            deciding = [node.test]
        elif isinstance(node, ast.Compare):
            deciding = [node]
        elif isinstance(node, ast.Return):
            deciding = [node.value] if node.value is not None else []
        else:
            continue
        if any(isinstance(inner, ast.Name) and inner.id == name
               for expression in deciding for inner in ast.walk(expression)):
            found.append(node)
    return found


def test_the_pre_stage_inspection_makes_no_commit_of_its_own(driven):
    """An inspection commit at HEAD would sit where `_complete` reads HEAD to
    recognise a resumed escalation, so the record rides the run's own commit
    instead.

    The control for this absence is the run under the workflow declaring no
    inspection above, where the same search over the same subjects finds the
    post-story mode's commit.
    """
    run = driven(findings=[OWN_FINDING])

    assert story_inspection.COMMIT_SUBJECT.format(story_id=STORY_ID) \
        not in run.subjects


def test_the_record_it_wrote_is_carried_by_the_runs_completion_commit(driven):
    """What the absence above costs nothing: the record is durable anyway,
    because the completion commit stages the tree the inspection wrote into."""
    run = driven(findings=[OWN_FINDING])

    assert len(run.history_records) == 1
    recorded = story_inspection.record_paths(run.tree, {})
    assert recorded, "this target routes the kind to no log at all"
    committed = _git(run.tree, "show", "--name-only", "--format=",
                     "HEAD").stdout.split()
    assert set(recorded) <= set(committed), (recorded, committed)
    assert _git(run.tree, "status", "--porcelain").stdout.strip() == ""


def test_the_cost_reaches_the_runs_record_and_not_the_live_allowance(
        target, harness, tmp_path):
    """The inspection's spend is recorded beside the stage invocations and is
    deliberately never added to the allowance the run ceiling is compared
    against.

    The figure is carried rather than computed, so a runner reporting one is
    what puts it in the record — and the control is a run whose Inspector
    reported none, where no entry appears at all rather than an entry of zero.
    """
    class Costing(Inspector):
        def __call__(self, *args, **keywords):
            super().__call__(*args, **keywords)
            return AgentResult(ok=True, result_text="inspected",
                               cost_usd=1.25)

    journal = tmp_path / "costed-journal.txt"
    agent_runner.run_agent = Costing(target, journal, findings=[OWN_FINDING])
    stage_runner = Runner(target, journal, [PASS])
    code = story_coordinator.run_story(
        STORY_ID, harness, target, stage_runner, sleep=lambda _s: None)
    run = Run(code, target, journal, agent_runner.run_agent, stage_runner)

    assert code == 0
    spends = [one for one in run.cost["invocations"]
              if one["stage"] == story_inspection.COST_STAGE]
    assert [one["cost_usd"] for one in spends] == [1.25]
    assert [one["attempt"] for one in spends] == [story_inspection.COST_ATTEMPT]
    # Recorded, and never charged to the allowance the ceiling reads.
    assert story_inspection.COST_STAGE not in str(run.state.get("entry_cost_usd"))
    assert run.state["entry_cost_usd"] == pytest.approx(
        sum(one["cost_usd"] for one in run.cost["invocations"]
            if one["stage"] != story_inspection.COST_STAGE))
    # And the cross-run log carries the same figure.
    assert [one.get("cost_usd") for one in run.history_records] == [1.25]


def test_an_inspection_reporting_no_cost_records_none_rather_than_a_zero(
        driven):
    """The control for the figure above: the fixture's Inspector reports no
    cost, and a zero nobody reported would be averaged later as though it were
    one."""
    run = driven(findings=[OWN_FINDING])

    assert [one for one in run.cost["invocations"]
            if one["stage"] == story_inspection.COST_STAGE] == []


def test_the_declaration_carries_no_budget_and_grants_no_veto():
    """No budget is added and none is widened.

    The declaration names an artifact and explains itself and does nothing
    else, so there is no number here for the mechanism to spend. The control is
    the same reading over a copy of the declaration with a budget planted in
    it, which reports it.
    """
    assert budget_keys(DECLARATION) == []
    planted = {**DECLARATION, "budget": 3}
    assert budget_keys(planted) == ["budget"]


#: The words a key that grants or widens an allowance is spelled with in this
#: repository's definitions. A declaration carrying one of them would be
#: spending something, which is exactly what this mechanism may not do.
ALLOWANCE_WORDS = ("budget", "max", "retries", "cost", "ceiling", "limit")


def budget_keys(declaration: dict) -> list[str]:
    return sorted(key for key in declaration
                  if any(word in key.lower() for word in ALLOWANCE_WORDS))
