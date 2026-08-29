"""Independent validation for story-072: the planner proposes the workflow it
plans for.

Since story-069 `l5-plan --workflow` states the workflow the planner's stage
facts are rendered against, and without the flag the script fell back to the
configured key -- so forgetting the flag and deliberately choosing the
configured workflow produced identical output. This story splits planning in
two. Invoked without the flag, the planner is first asked, against a prompt
carrying the request and what each defined workflow says it is for and nothing
else, which workflow this request belongs under; `l5-plan` reads that answer
mechanically, shows the developer the reasoning, and asks them to accept it,
name another, or abort. The confirmed name is what the real session is
rendered against, and an invocation with no terminal and no flag is refused
rather than falling back to a name nothing chose.

Written from the story's acceptance criteria rather than from the
implementation, at four altitudes:

  * **the declaration.** The definitions this repository ships and the prompt
    it ships are read here as the subjects they are -- artifacts this harness
    deploys -- and asked whether each definition says when it applies and
    whether the prompt enumerates any workflow of its own.
  * **the validator.** `story_coordinator.applies_when_problems` is pure over
    a definition, so it is driven directly over definitions built here.
  * **the pure decisions.** `workflow_selection`'s candidates, its reading of
    what phase one wrote, and its reading of the developer's reply are pure
    over inputs a test can construct, so each is driven over constructed ones.
  * **the two-phase session.** The real `scripts/l5-plan` is run against a
    throwaway repository with a stub `claude` on PATH -- on a pty where the
    confirmation has to be answered, and with no terminal where the refusal is
    the subject -- so how many invocations were made, what each carried, and
    what was committed are observations of the script rather than of its
    source.

**The workflows driven here are built, not shipped.** Every definition a run
loads or a session is rendered against is assembled by the builder in
`tests/conftest.py` and written into a harness root this module owns, because
the subject is the *mechanism* -- what a definition must declare, which
definitions become candidates, which one a session ends up rendered against --
and a workflow is its input. The exception is the section that asks what this
repository deploys: that the definitions under `workflows/` each say when they
apply, and that `prompts/workflow-selector.md` names none of them. Those are
assertions about the shipped artifacts, so they read the shipped artifacts.

Every absence asserted here carries a demonstration that the same check
reports the violation it exists to catch:

  * "a shipped definition's statement does not restate its own name" sits
    beside a probe definition whose statement is its name, which the same
    check reports;
  * "the selector prompt names no defined workflow" sits beside the same
    prompt rendered with the candidates, where every name appears;
  * "a definition carrying no statement is refused before a stage runs, with
    no run directory, log, branch or invocation left behind" sits beside the
    same definition carrying one, where all four appear;
  * "the flag makes no phase-one invocation, prints no proposal and asks no
    confirmation" sits beside the same session without the flag, which makes
    two invocations and prints both;
  * "an unusable answer starts no session and writes no artifact" sits beside
    the same fixture whose answer is usable, which plans and commits;
  * "a headless invocation without the flag invokes nothing, writes nothing
    and commits nothing" sits beside the same invocation stating a workflow,
    which does all three;
  * "`scripts/l5-plan` reads the configured workflow key on no path" sits
    beside the same scan over a copy of that script with the read put back,
    which reports it -- and beside a session whose configured key names a
    workflow no definition has, which plans anyway;
  * "the flagged session asks for no answer and keeps no transcript" sits
    beside the same target planned without the flag, where both appear;
  * "the transcript is not named on the path that reaches a proposal" sits
    beside the session that proposed nothing, where it is.

Since story-076 the stub is not a substitute for delivery. It writes the
answer only where the real classifying turn could have written it -- inside
the directory it was invoked in -- and otherwise writes nothing and prints
what the real turn printed instead. `write_text` has no permission model, so
a stub without that guard delivers where the real agent never can, which is
how phase one came to be green here and broken everywhere else. Section 7
asks what the invocation carried: the path, the working directory, the grant
and the streams, none of which needs a model to settle.

Nothing here invokes a model: every run goes through the fake runner below and
every session through the stub `claude`.
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

import context_assembler
import schema_validator
import story_coordinator
import workflow_selection
from agent_runner import AgentResult

HARNESS_ROOT = Path(__file__).resolve().parents[1]
SHIPPED_WORKFLOWS = HARNESS_ROOT / "workflows"
SELECTOR_PROMPT_PATH = (HARNESS_ROOT / "prompts"
                        / workflow_selection.SELECTOR_PROMPT)
L5_PLAN_SOURCE = HARNESS_ROOT / "scripts" / "l5-plan"

STORY_ID = "story-001"
PLANNED_ID = "story-900"
DEFAULT_BRANCH = "main"
TESTS_DIR = "tests/"

#: The placeholder pattern the assembler reads, so a template scanned here is
#: scanned the way the renderer reads it.
PLACEHOLDER = re.compile(r"\{\{[a-z_]+\}\}")

RULES = {
    "max_retries": 2,
    "require_verifier_pass": True,
    "blocked_paths": [".git/", ".harness/runs/", "rules/"],
}


# --------------------------------------------------------------------------
# The workflows this module builds
#
# Two runnable definitions with different stage lists, different create
# restrictions and different statements of when they apply, so "the session
# was rendered against *that* definition" and "the proposal was *that* one"
# are observations rather than coincidences. A third carries a statement and
# nothing else, which is the shape the story says must become selectable by
# shipping a file.
#
# Every name below is the fixture's own, declared once, and every assertion
# derives from it exactly as it would derive from a shipped definition.
# `conftest.VERIFYING_STAGE` is the one name the harness itself imposes.
# --------------------------------------------------------------------------

ADDING_PREFIX = "adding-only/"
PRESERVING_PREFIX = "preserving-only/"

#: What each built definition says it is for. Written here rather than taken
#: from a shipped definition, because a candidate list is the *input* to every
#: assertion below about which candidate was proposed.
ADDING_APPLIES = ("The correctness claim is that something the target could "
                  "not do before now happens, and nothing yet shows it does.")
PRESERVING_APPLIES = ("The correctness claim is that nothing observable "
                      "changed, and the evidence for that already exists.")
THIRD_APPLIES = ("The correctness claim is about how the work is written "
                 "down rather than about what the target does.")


def runnable_workflow(name: str, writing: str, validating: str, prefix: str,
                      applies_when: str) -> dict:
    """A definition a run can complete, whose stages are the ones named."""
    return conftest.build_workflow(
        conftest.workflow_stage(
            name=writing,
            outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
            changed_files=conftest.CHANGED_FILES,
            schemas={conftest.CHANGED_FILES: "changed-files"},
            may_not_create=(prefix,)),
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
        applies_when=applies_when,
    )


#: The definition a request that adds behaviour belongs under, in this
#: module's own vocabulary.
ADDING = runnable_workflow("adding-workflow", "drafting", "checking",
                           ADDING_PREFIX, ADDING_APPLIES)
#: The other one, distinguishable from it by every declaration that matters.
PRESERVING = runnable_workflow("preserving-workflow", "composing", "auditing",
                               PRESERVING_PREFIX, PRESERVING_APPLIES)
#: The third the story says must become selectable by shipping a definition:
#: a statement of when it applies and the stages a definition must have, and
#: nothing else that any assertion here reads.
THIRD = runnable_workflow("recording-workflow", "narrating", "reviewing",
                          "recording-only/", THIRD_APPLIES)

#: A name no harness root this module builds carries.
UNDEFINED = "cartographer-workflow"


def stages_of(workflow: dict) -> list[str]:
    return [stage["name"] for stage in workflow["stages"]]


def distinctive(workflow: dict, other: dict) -> set[str]:
    """The stages one definition declares and the other does not."""
    return set(stages_of(workflow)) - set(stages_of(other))


def prefix_of(workflow: dict) -> str:
    return workflow["stages"][0]["may_not_create"][0]


def test_the_definitions_this_module_builds_can_be_told_apart():
    """The derivations above are load-bearing: an accidental overlap would
    make every "it was rendered against the one that was confirmed" assertion
    vacuous."""
    built = (ADDING, PRESERVING, THIRD)
    assert len({workflow["name"] for workflow in built}) == len(built)
    assert len({workflow["applies_when"] for workflow in built}) == len(built)
    assert len({prefix_of(workflow) for workflow in built}) == len(built)
    for workflow in built:
        for other in built:
            if workflow is not other:
                assert distinctive(workflow, other), (workflow["name"],
                                                      other["name"])
    assert UNDEFINED not in {workflow["name"] for workflow in built}


# ==========================================================================
# 1. A definition says when it is the right one to plan under
# ==========================================================================


def test_a_definition_that_says_when_it_applies_is_not_reported():
    """The control every case below needs: the same validator over the same
    builder reports nothing when the statement is there."""
    assert story_coordinator.applies_when_problems(ADDING) == []


#: Each way a definition can fail to say what it is for. `None` is the absent
#: key, which is what every definition written before this story carried.
UNUSABLE = {
    "absent": None,
    "empty": "",
    "whitespace": "   \n\t ",
    "a number": 7,
    "a list of statements": ["it applies sometimes"],
}


@pytest.mark.parametrize("case", sorted(UNUSABLE))
def test_a_definition_with_no_usable_statement_is_reported(case):
    probe = dict(ADDING)
    if UNUSABLE[case] is None:
        probe.pop("applies_when")
    else:
        probe["applies_when"] = UNUSABLE[case]

    problems = story_coordinator.applies_when_problems(probe)

    assert problems
    assert probe["name"] in " ".join(problems)


def test_the_builder_defaults_the_statement_and_takes_none_to_omit_it():
    """The fixture half of the same fact: a built definition satisfies the
    validator without its caller saying anything, and a caller that wants a
    definition carrying none asks for that explicitly."""
    assert story_coordinator.applies_when_problems(
        conftest.build_workflow(conftest.workflow_stage(name="drafting"))) == []
    assert story_coordinator.applies_when_problems(
        conftest.build_workflow(conftest.workflow_stage(name="drafting"),
                                applies_when=None))


# --------------------------------------------------------------------------
# The validator runs at pre-flight, before any stage is invoked
# --------------------------------------------------------------------------


STORY = """\
story:
  id: {story_id}
  title: Sample story for workflow proposal tests
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
logs_dir: {logs_dir}
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: echo tests-ok
tests_dir: {tests_dir}
"""

#: Where a target built here keeps the raw output of the agents it runs, and so
#: where phase one's answer and phase one's transcript are asked for. Declared
#: by the fixture rather than read from `workflow_selection`, because "the path
#: phase one was asked to write to is under the *configured* directory" is only
#: an observation if the configuration is this module's own statement.
TARGET_LOGS_DIR = ".harness/logs"

#: A second target's answer to the same question, sharing no path component
#: with the first, so a script deriving the location from a literal rather than
#: from the configuration cannot satisfy both.
ANOTHER_LOGS_DIR = "var/planning-logs"

APP_AT_HEAD = "print('hello')\n"


def story_text(declared: str | None = None, story_id: str = STORY_ID,
               mandate: bool = True) -> str:
    """The artifact, with or without the block a run resolves before it starts.

    A story a test installs for the coordinator carries one, because since
    story-087 a run whose mandate does not resolve to a human is refused
    before anything is created. A story a *planning session* writes carries
    none: l5-plan confers the block when the session ends, and an artifact
    that arrives from a session already carrying one is refused.
    """
    line = f"  workflow: {declared}\n" if declared else ""
    text = STORY.format(story_id=story_id, workflow_line=line)
    return text + conftest.MANDATE_BLOCK if mandate else text


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def init_repo(root: Path, message: str = "initial") -> None:
    for command in (
        ["git", "init", "-q", "-b", DEFAULT_BRANCH],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", message],
    ):
        subprocess.run(command, cwd=root, check=True)


def build_target(root: Path, configured: str, declared: str | None) -> Path:
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml",
          CONFIG.format(workflow=configured, tests_dir=TESTS_DIR,
                        logs_dir=TARGET_LOGS_DIR))
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml",
          story_text(declared))
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "src" / "app.py", APP_AT_HEAD)
    write(root / (TESTS_DIR + "test_existing.py"),
          "def test_nothing():\n    assert True\n")
    init_repo(root)
    return root


def build_harness(root: Path, workflows, *, copy=()) -> Path:
    """A harness root carrying every definition it was given.

    Several definitions in one root, because the whole subject is a *choice*
    between them: a root holding one definition can only ever answer the
    question one way.
    """
    for workflow in workflows:
        conftest.materialize_workflow(workflow, root, rules=RULES, copy=copy)
    return root


class Runner:
    """A fake agent runner that writes whatever the running stage declares."""

    def __init__(self, target_root: Path, *workflows: dict):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.outputs = {stage["name"]: list(stage.get("outputs", []))
                        for workflow in workflows
                        for stage in workflow["stages"]}
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
            write_json(self.run_dir / artifact,
                       {"status": "passed", "blocking_issues": [],
                        "unverified": [], "retry_recommended": False})
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


def branches(target: Path) -> set[str]:
    listing = subprocess.run(
        ["git", "-C", str(target), "branch", "--format=%(refname:short)"],
        capture_output=True, text=True, check=True).stdout
    return {line.strip() for line in listing.splitlines() if line.strip()}


@pytest.fixture
def preflight(tmp_path):
    """A builder for (target, harness) pairs running one built definition."""
    made = set()

    def make(workflow: dict, *, name: str,
             declared: str | None = None) -> tuple[Path, Path]:
        """A target configured to run `workflow`, whose artifact names
        `declared` — or names nothing, which is the compatibility shape every
        artifact written before story-069 carries."""
        assert name not in made, f"two environments named {name}"
        made.add(name)
        harness = build_harness(tmp_path / f"harness-{name}", (workflow,))
        target = build_target(tmp_path / f"target-{name}", workflow["name"],
                              declared)
        return target, harness

    return make


def without_a_statement(workflow: dict) -> dict:
    """The same definition with exactly one declaration removed."""
    probe = json.loads(json.dumps(workflow))
    probe.pop("applies_when")
    return probe


def test_a_definition_with_no_statement_is_refused_before_a_stage_is_invoked(
    preflight, capsys,
):
    """Read off the refused target's tree rather than off the exit status: a
    refusal that had already spent a stage is the defect this pre-flight
    exists against. Its control is the next test, which makes the same four
    observations of the same definition carrying a statement."""
    probe = without_a_statement(ADDING)
    target, harness = preflight(probe, name="no-statement",
                                declared=probe["name"])
    before = branches(target)
    runner = Runner(target, ADDING)

    code = story_coordinator.run_story(STORY_ID, harness, target, runner)

    assert code == 1
    assert ADDING["name"] in capsys.readouterr().err
    assert runner.calls == []
    assert not (target / ".harness" / "runs" / STORY_ID).exists()
    assert not (target / ".harness" / "logs" / f"{STORY_ID}.log").exists()
    assert branches(target) == before


def test_the_same_definition_carrying_a_statement_runs(preflight):
    """The control the absences above need."""
    target, harness = preflight(ADDING, name="with-statement",
                                declared=ADDING["name"])
    before = branches(target)
    runner = Runner(target, ADDING)

    code = story_coordinator.run_story(STORY_ID, harness, target, runner)

    assert code == 0, runner.calls
    assert runner.calls == stages_of(ADDING)
    assert (target / ".harness" / "runs" / STORY_ID).is_dir()
    assert (target / ".harness" / "logs" / f"{STORY_ID}.log").is_file()
    assert branches(target) - before == {f"story/{STORY_ID}"}


# ==========================================================================
# 2. What this repository ships
#
# The definitions under `workflows/` and `prompts/workflow-selector.md` are
# the subjects here rather than inputs: the criterion is about what this
# harness deploys, and an assertion about that has to read what it deploys.
# ==========================================================================


def shipped_definitions() -> dict[str, dict]:
    """Every definition under `workflows/`, read as the files they are.

    Read directly rather than through `load_workflow`, because what a
    definition says it is for is answerable without resolving any
    configuration reference it carries — which is the same reason
    `workflow_selection` reads them this way.
    """
    found = {path.stem: json.loads(path.read_text(encoding="utf-8"))
             for path in sorted(SHIPPED_WORKFLOWS.glob("*.json"))}
    assert found, "this repository ships no workflow definition"
    return found


def test_every_shipped_definition_says_when_it_applies():
    for name, definition in shipped_definitions().items():
        assert story_coordinator.applies_when_problems(definition) == [], name


def test_the_shipped_statements_differ_from_one_another():
    """Two definitions saying the same thing cannot be chosen between, which
    is the whole job the field was added for."""
    statements = [definition["applies_when"]
                  for definition in shipped_definitions().values()]
    assert len(set(statements)) == len(statements)


def restates_its_name(name: str, statement: str) -> bool:
    """Whether a statement leans on the definition's own name.

    A reader choosing between definitions already has the names; a statement
    that repeats one tells them nothing they did not have. Both halves of a
    hyphenated name are asked about, so `refactor-workflow` is reported by a
    statement saying only "for refactor work".
    """
    words = [part for part in re.split(r"[^a-z]+", name.lower()) if part]
    return any(word in statement.lower() for word in words if word != "workflow")


def test_no_shipped_statement_restates_the_name_of_the_definition_it_is_in():
    """Its control is beside it: the same check over a probe whose statement
    *is* its name reports it, so a green here cannot mean the check has
    stopped seeing names."""
    for name, definition in shipped_definitions().items():
        assert not restates_its_name(name, definition["applies_when"]), name

    for name in shipped_definitions():
        assert restates_its_name(name, f"Choose this for {name} work.")


def selector_prompt() -> str:
    return SELECTOR_PROMPT_PATH.read_text(encoding="utf-8")


#: The placeholders the selector prompt has to carry for the script to be able
#: to fill it: the request, the candidates and where to write the answer. Named
#: from the module that fills them rather than spelled twice.
FILLED_BY_THE_SCRIPT = ("request", "workflow_candidates", "selection_path")


@pytest.mark.parametrize("placeholder", FILLED_BY_THE_SCRIPT)
def test_the_selector_prompt_carries_the_placeholder(placeholder):
    assert "{{%s}}" % placeholder in selector_prompt()


def test_the_selector_prompt_names_no_workflow_of_its_own():
    """The property that makes `applies_when` worth adding rather than a
    prompt that lists what it knows: with the placeholders stripped, neither
    definition this repository ships nor any this module builds appears.

    Its control is the render beside it, where the candidates the script would
    hand it do appear — so "no name is in the template" cannot be satisfied by
    a check that has stopped seeing names at all.
    """
    stripped = PLACEHOLDER.sub("", selector_prompt())
    for name in (*shipped_definitions(), ADDING["name"], PRESERVING["name"],
                 THIRD["name"]):
        assert name not in stripped, name

    rendered = context_assembler.render(
        selector_prompt(),
        {"workflow_candidates": workflow_selection.candidate_block(
            (workflow_selection.Candidate(ADDING["name"], ADDING_APPLIES),
             workflow_selection.Candidate(THIRD["name"], THIRD_APPLIES)))})
    for name in (ADDING["name"], THIRD["name"]):
        assert name in rendered, name


def test_the_selector_prompt_carries_no_workflow_stage_facts():
    """One classifying turn that cannot be rendered against a workflow. Its
    control is the same reading over the planner template, which does carry
    the placeholders that inject stage facts."""
    stage_facts = {"workflow_stages", "stage_rules", "stage_name"}
    carried = set(PLACEHOLDER.findall(selector_prompt()))
    carried = {name.strip("{}") for name in carried}
    assert not carried & stage_facts

    planner = (HARNESS_ROOT / "prompts" / "planner.md").read_text(
        encoding="utf-8")
    planner_carries = {name.strip("{}") for name in PLACEHOLDER.findall(planner)}
    assert planner_carries & stage_facts


def test_the_selection_schema_is_declared_in_the_manifest_and_loads():
    manifest = json.loads(
        (HARNESS_ROOT / "schemas" / "manifest.json").read_text(encoding="utf-8"))
    assert workflow_selection.SELECTION_SCHEMA in manifest["schemas"]
    schema = schema_validator.load_schema(workflow_selection.SELECTION_SCHEMA)
    assert schema["required"] == ["reasoning"]
    assert "workflow" in schema["properties"]


# ==========================================================================
# 3. The candidates phase one is shown
# ==========================================================================


@pytest.fixture
def three_definitions(tmp_path) -> Path:
    return build_harness(tmp_path / "three", (ADDING, PRESERVING, THIRD))


def test_every_defined_workflow_is_a_candidate_with_what_it_says_it_is_for(
    three_definitions,
):
    found = workflow_selection.candidates(three_definitions)

    assert {candidate.name for candidate in found} == {
        ADDING["name"], PRESERVING["name"], THIRD["name"]}
    assert {candidate.applies_when for candidate in found} == {
        ADDING_APPLIES, PRESERVING_APPLIES, THIRD_APPLIES}


def test_a_third_definition_becomes_a_candidate_by_being_written(tmp_path):
    """The property the story is really buying: a definition shipped with a
    statement is offered, and the prompt that offers it is untouched. Its
    control is the same root before the third definition is written, where the
    third name is not offered."""
    root = build_harness(tmp_path / "growing", (ADDING, PRESERVING))
    before = {candidate.name
              for candidate in workflow_selection.candidates(root)}
    assert THIRD["name"] not in before

    conftest.materialize_workflow(THIRD, root, rules=RULES)

    after = {candidate.name for candidate in workflow_selection.candidates(root)}
    assert after - before == {THIRD["name"]}


def test_a_definition_with_no_statement_is_not_offered_as_a_candidate(tmp_path):
    """There is nothing to choose it by, and a classifying turn asked to
    choose between a description and a blank is being asked to guess. Its
    control is the same root carrying the same definition with its statement,
    where it is offered."""
    silent = build_harness(tmp_path / "silent",
                           (ADDING, without_a_statement(PRESERVING)))
    assert {candidate.name for candidate in workflow_selection.candidates(
        silent)} == {ADDING["name"]}

    speaking = build_harness(tmp_path / "speaking", (ADDING, PRESERVING))
    assert {candidate.name for candidate in workflow_selection.candidates(
        speaking)} == {ADDING["name"], PRESERVING["name"]}


def test_the_candidate_block_carries_each_name_beside_its_own_words():
    block = workflow_selection.candidate_block(
        (workflow_selection.Candidate(ADDING["name"], ADDING_APPLIES),
         workflow_selection.Candidate(PRESERVING["name"], PRESERVING_APPLIES)))

    for name, statement in ((ADDING["name"], ADDING_APPLIES),
                            (PRESERVING["name"], PRESERVING_APPLIES)):
        assert name in block, name
        assert statement in block, name


# ==========================================================================
# 4. Reading what phase one wrote
# ==========================================================================


OFFERED = (workflow_selection.Candidate(ADDING["name"], ADDING_APPLIES),
           workflow_selection.Candidate(PRESERVING["name"], PRESERVING_APPLIES))

REASONING = "the request asks for behaviour that does not exist yet"


def selection_at(path: Path, payload) -> workflow_selection.Selection:
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return workflow_selection.read_selection(path, OFFERED, HARNESS_ROOT)


def test_an_answer_naming_an_offered_workflow_is_a_proposal(tmp_path):
    """The control every case below needs: the same reader over a usable
    answer does propose, and carries the reasoning through to the developer."""
    selection = selection_at(tmp_path / "answer.json",
                             {"workflow": ADDING["name"],
                              "reasoning": REASONING})

    assert selection.proposed
    assert selection.workflow == ADDING["name"]
    assert selection.reasoning == REASONING
    assert selection.fault is None


def test_an_answer_that_was_never_written_proposes_nothing(tmp_path):
    selection = workflow_selection.read_selection(
        tmp_path / "never-written.json", OFFERED, HARNESS_ROOT)

    assert not selection.proposed
    assert selection.workflow is None
    assert selection.fault


def test_an_answer_that_cannot_be_read_proposes_nothing(tmp_path):
    """A path that exists and is not a file: the read fails for a reason that
    is not absence, and the developer is told which."""
    unreadable = tmp_path / "unreadable.json"
    unreadable.mkdir()

    selection = workflow_selection.read_selection(unreadable, OFFERED,
                                                  HARNESS_ROOT)

    assert not selection.proposed
    assert selection.fault


def test_an_answer_that_is_not_json_proposes_nothing(tmp_path):
    selection = selection_at(tmp_path / "prose.json",
                             "I think this is a refactor, honestly.")

    assert not selection.proposed
    assert selection.fault


def test_an_answer_that_does_not_satisfy_the_schema_proposes_nothing(tmp_path):
    """The reasoning is what the developer is asked to review, so an answer
    carrying a name and no reasoning is not a proposal either."""
    selection = selection_at(tmp_path / "no-reasoning.json",
                             {"workflow": ADDING["name"]})

    assert not selection.proposed
    assert selection.fault


def test_an_answer_that_is_unsure_proposes_nothing_and_keeps_its_reasoning(
    tmp_path,
):
    """Absence of a name is how an unsure classification is stated, and what
    it left unsettled is still shown: the developer is being asked to decide
    the thing phase one could not."""
    selection = selection_at(tmp_path / "unsure.json",
                             {"reasoning": "it could be either of these"})

    assert not selection.proposed
    assert selection.reasoning == "it could be either of these"
    assert selection.fault


def test_an_answer_naming_a_workflow_it_was_not_offered_proposes_nothing(
    tmp_path,
):
    """Nothing is rendered against a name nothing chose, and the fault says
    which name was named so the developer can see what happened."""
    selection = selection_at(tmp_path / "invented.json",
                             {"workflow": UNDEFINED, "reasoning": REASONING})

    assert not selection.proposed
    assert UNDEFINED in selection.fault
    for candidate in OFFERED:
        assert candidate.name in selection.fault, candidate.name


def test_each_unusable_shape_is_reported_as_the_shape_it_was(tmp_path):
    """One fault per shape rather than one sentence for all of them: a
    developer told only "no proposal" cannot tell a model that declined from a
    model that wrote nothing at all."""
    faults = {
        "absent": workflow_selection.read_selection(
            tmp_path / "gone.json", OFFERED, HARNESS_ROOT).fault,
        "not json": selection_at(tmp_path / "a.json", "not json").fault,
        "schema": selection_at(tmp_path / "b.json", {}).fault,
        "unsure": selection_at(tmp_path / "c.json",
                               {"reasoning": REASONING}).fault,
        "unoffered": selection_at(tmp_path / "d.json",
                                  {"workflow": UNDEFINED,
                                   "reasoning": REASONING}).fault,
    }
    assert all(faults.values())
    assert len(set(faults.values())) == len(faults)


# ==========================================================================
# 5. Reading the developer's reply
# ==========================================================================


def test_an_empty_reply_accepts_the_proposal():
    for reply in ("\n", "", "   \n"):
        decision = workflow_selection.read_reply(reply, OFFERED,
                                                 ADDING["name"])
        assert decision.action == workflow_selection.ACCEPT, repr(reply)
        assert decision.workflow == ADDING["name"], repr(reply)


def test_naming_another_defined_workflow_overrides_the_proposal():
    decision = workflow_selection.read_reply(f"{PRESERVING['name']}\n",
                                             OFFERED, ADDING["name"])

    assert decision.action == workflow_selection.OVERRIDE
    assert decision.workflow == PRESERVING["name"]


def test_an_empty_reply_with_no_proposal_aborts():
    """There is nothing to accept, and starting a session under a name nobody
    chose is the one thing this must not do. Its control is the same reply
    with a proposal on the table, which accepts."""
    decision = workflow_selection.read_reply("\n", OFFERED, None)

    assert decision.action == workflow_selection.ABORT
    assert decision.workflow is None
    assert workflow_selection.read_reply(
        "\n", OFFERED, ADDING["name"]).action == workflow_selection.ACCEPT


def test_naming_a_workflow_with_no_definition_aborts_carrying_the_name():
    decision = workflow_selection.read_reply(f"{UNDEFINED}\n", OFFERED, None)

    assert decision.action == workflow_selection.ABORT
    assert decision.workflow is None
    assert decision.unknown == UNDEFINED


def test_naming_a_workflow_with_no_definition_aborts_over_a_proposal_too():
    """An unrecognised name is not a typo the script quietly ignores in favour
    of what phase one said: the developer asked for something else, and what
    they asked for does not exist."""
    decision = workflow_selection.read_reply(f"{UNDEFINED}\n", OFFERED,
                                             ADDING["name"])

    assert decision.action == workflow_selection.ABORT
    assert decision.workflow is None
    assert decision.unknown == UNDEFINED


# ==========================================================================
# 6. The two-phase session, driven through the real script
# ==========================================================================


#: What a stub invocation prints, one line on each stream, so a transcript can
#: be asked whether phase one's stdout and its stderr both reached it.
STUB_STDOUT = "stub session"
STUB_STDERR = "stub diagnostics"

#: What the stub prints instead of an answer when it is asked for one outside
#: the directory it was invoked in: the shape of what the real classifying turn
#: printed, an approval it cannot get and no file. `write_text` has no
#: permission model, so a stub without this guard can always write where the
#: real agent never can — which is exactly how a feature broken in production
#: kept a green suite.
OUTSIDE_THE_WORKSPACE = ("I need your approval to create a file outside this "
                         "workspace, so I have not written the selection.")

#: A stub `claude` that serves both invocations. It appends one JSON line per
#: invocation — the argument list, the working directory, whether its streams
#: are a terminal, and the system prompt it was handed — so how many
#: invocations a session made and what each carried are read off the log rather
#: than inferred.
#:
#: Phase one is recognised by the answer path in the prompt it was given rather
#: than by a flag this test sets, so the recognition is the script's own doing:
#: an invocation carrying a path to write a selection to is the selecting one.
#: It writes there only when that path is inside the directory it was invoked
#: in, which is the only place the real turn's permission mode accepts edits;
#: asked for anywhere else it writes nothing and says so, exactly as the real
#: turn did. Every other invocation writes the artifacts the test told it to
#: write, relative to the directory it inherited.
STUB_BODY = '''\
import json
import os
import pathlib
import re
import sys

argv = sys.argv
prompt = ""
if "--append-system-prompt" in argv:
    prompt = argv[argv.index("--append-system-prompt") + 1]
with open(os.environ["L5_STUB_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({
        "argv": argv,
        "cwd": os.getcwd(),
        "tty": [os.isatty(0), os.isatty(1), os.isatty(2)],
        "prompt": prompt,
    }) + "\\n")
answer_path = re.search(r"(\\S+" + re.escape(ANSWER_NAME) + ")", prompt)
if answer_path:
    wanted = pathlib.Path(answer_path.group(1)).resolve()
    here = pathlib.Path.cwd().resolve()
    if here != wanted and here not in wanted.parents:
        sys.stdout.write(OUTSIDE_THE_WORKSPACE + "\\n")
    else:
        answer = os.environ.get("L5_STUB_SELECTION")
        if answer is not None:
            wanted.write_text(answer, encoding="utf-8")
else:
    for relative, body in json.loads(os.environ.get("L5_STUB_WRITE", "[]")):
        path = pathlib.Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
sys.stdout.write(STUB_STDOUT + "\\n")
sys.stdout.flush()
sys.stderr.write(STUB_STDERR + "\\n")
sys.stderr.flush()
sys.exit(int(os.environ.get("L5_STUB_EXIT", "0")))
'''


def stub_source() -> str:
    """The stub, carrying the names it has to agree with the harness on.

    The answer file's name is `workflow_selection`'s declaration rather than a
    second spelling here: the stub recognises phase one by the path it was
    asked to write to, and what that file is called is the module's to decide.
    """
    declared = "".join(
        f"{name} = {value!r}\n"
        for name, value in (("ANSWER_NAME", workflow_selection.SELECTION_ANSWER),
                            ("OUTSIDE_THE_WORKSPACE", OUTSIDE_THE_WORKSPACE),
                            ("STUB_STDOUT", STUB_STDOUT),
                            ("STUB_STDERR", STUB_STDERR)))
    return "#!/usr/bin/env python3\n" + declared + STUB_BODY

#: The prompts `l5-plan` needs from a harness root beyond the stage templates
#: `materialize_workflow` writes. All three are this repository's own artifacts
#: rather than inputs a test should invent — the selector prompt especially,
#: since "a third definition is selectable with no edit to it" is a claim about
#: the shipped file — so they are copied rather than rebuilt.
PLANNING_PROMPTS = ("planner.md", "prose-layer.md",
                    workflow_selection.SELECTOR_PROMPT)


@pytest.fixture
def planning_harness(tmp_path) -> Path:
    """A harness root a real `l5-plan` can run out of.

    `scripts/` and `orchestration/` are copied rather than linked because the
    entry point resolves its own harness root from its own location, and a
    symlink would resolve straight back to this repository — which ships
    neither definition built here.
    """
    root = build_harness(tmp_path / "planning-harness",
                         (ADDING, PRESERVING),
                         copy=("orchestration", "scripts"))
    for name in PLANNING_PROMPTS:
        (root / "prompts" / name).write_text(
            (HARNESS_ROOT / "prompts" / name).read_text(encoding="utf-8"),
            encoding="utf-8")
    return root


#: The workflow the throwaway target's configuration names. It is a name no
#: harness root here defines, which is what makes "the script reads the
#: configured key on no path" observable: a session that read it would refuse
#: on a workflow with no definition rather than plan.
CONFIGURED = UNDEFINED


def build_planning(tmp_path: Path, *, name: str = "plan-target",
                   logs_dir: str = TARGET_LOGS_DIR,
                   remote: bool = True) -> Planning:
    """A throwaway target with the stub above on PATH, and its logs directory.

    `logs_dir` is a parameter rather than a constant because where phase one is
    asked for its answer is derived from it: a second target configuring
    somewhere else is what makes "under the configured directory" an
    observation rather than a coincidence with one literal.
    """
    root = tmp_path / name
    (root / ".harness" / "stories").mkdir(parents=True)
    write(root / ".harness" / "config.yaml",
          CONFIG.format(workflow=CONFIGURED, tests_dir=TESTS_DIR,
                        logs_dir=logs_dir))
    write(root / "README.md", "target\n")
    init_repo(root)
    bin_dir = tmp_path / f"bin-{name}"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(stub_source(), encoding="utf-8")
    stub.chmod(0o755)
    planning = Planning(root, bin_dir, root / ".harness" / "stories",
                        tmp_path / f"session-{name}.jsonl")
    if remote:
        planning.remote = bare_remote(tmp_path, planning, name=f"origin-{name}",
                                      upstream=True)
    return planning


@pytest.fixture
def planning(tmp_path) -> Planning:
    """A throwaway target with the stub above on PATH and a bare origin."""
    return build_planning(tmp_path)


def invocations(planning: Planning) -> list[dict]:
    """Every `claude` invocation the session made, in order."""
    if not planning.log.exists():
        return []
    return [json.loads(line)
            for line in planning.log.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def answer(workflow: str | None, reasoning: str = REASONING) -> str:
    """What phase one writes, as phase one would write it."""
    payload = {"reasoning": reasoning}
    if workflow is not None:
        payload["workflow"] = workflow
    return json.dumps(payload)


def planned(declared: str | None) -> str:
    """What the stub session writes: no mandate, because l5-plan confers it."""
    return story_text(declared, story_id=PLANNED_ID, mandate=False)


def relative_artifact() -> str:
    return f".harness/stories/{PLANNED_ID}.yaml"


def artifact_path(planning: Planning) -> Path:
    return planning.stories_dir / f"{PLANNED_ID}.yaml"


#: The reply that accepts phase one's proposal: Enter alone.
CONFIRMS = b"\n"

#: The reply that approves the plan when the session ends, and the one that
#: declines the offer to run what was committed. Both are spelled where they
#: are decided rather than as second literals here, and a test that gets as far
#: as either question writes them in the order the script asks.
APPROVES = conftest.APPROVES.encode()
DECLINES = conftest.DECLINES.encode()


def plan_on_a_terminal(planning: Planning, harness: Path, *args: str,
                       reply: bytes = CONFIRMS, cwd: Path | None = None,
                       **stub) -> tuple[int, str]:
    """Run the real `scripts/l5-plan` with a pty for its three streams.

    The reply is written to the terminal as soon as the process starts, the
    way `test_plan_run_offer` writes its own: nothing between here and the
    confirmation reads stdin — the stub session does not — so the bytes wait
    in the terminal's buffer until the confirmation reads them.

    A test whose subject stops at the confirmation writes that one reply. One
    that runs past it writes the answers to the questions that follow too, in
    the order the script asks them: the confirmation, then the approval
    story-088 asks before it stamps anything, then the offer to run what was
    committed.

    `cwd` is where the developer invoked `l5-plan` from, and defaults to the
    target root because that is where they usually are. Somewhere else inside
    the target is what separates "the working directory phase one ran in" from
    "the directory this process happened to start in".
    """
    import pty

    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, str(harness / "scripts" / "l5-plan"), *args],
        cwd=cwd or planning.root, env=planning.env(**stub),
        stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
    )
    os.close(slave)
    os.write(master, reply)
    return drain(process, master)


def plan_without_a_terminal(planning: Planning, harness: Path, *args: str,
                            stdin: str | None = None,
                            **stub) -> subprocess.CompletedProcess:
    """Run the same script with no terminal, and never wait for it forever.

    The timeout is the point rather than a precaution: a refusal that read
    stdin before deciding would block here, and this reports that as a failure
    rather than as a hung suite.
    """
    return subprocess.run(
        [sys.executable, str(harness / "scripts" / "l5-plan"), *args],
        cwd=planning.root, env=planning.env(**stub),
        input=stdin if stdin is not None else "",
        capture_output=True, text=True, timeout=120,
    )


def rendered_against(invocation: dict, workflow: dict) -> bool:
    """Whether a planner invocation carries that definition's stage facts.

    Read from what was injected rather than from what was asked for: the
    stages the definition declares and the prefix only it restricts.
    """
    prompt = invocation["prompt"]
    return (all(name in prompt for name in stages_of(workflow))
            and prefix_of(workflow) in prompt)


# --------------------------------------------------------------------------
# The flag skips phase one entirely
# --------------------------------------------------------------------------


def test_the_flag_invokes_the_planner_once_and_proposes_nothing(
    planning, planning_harness,
):
    """A session given `--workflow` makes no phase-one invocation at all, and
    the developer is asked nothing. Its control is the next test, which runs
    the same session without the flag and finds two invocations and a
    confirmation."""
    status, output = plan_on_a_terminal(
        planning, planning_harness, "--workflow", ADDING["name"],
        "a story request", reply=b"")

    assert status == 0, output
    made = invocations(planning)
    assert len(made) == 1
    assert rendered_against(made[0], ADDING)
    assert workflow_selection.SELECTOR_PROMPT not in output
    assert "proposes" not in output
    assert PRESERVING["name"] not in output


def test_without_the_flag_the_planner_is_invoked_twice(planning,
                                                       planning_harness):
    """The central criterion: once to choose the workflow, once to plan under
    the one that was confirmed. The second invocation's facts are read from
    what was injected into it, not from what was asked for."""
    status, output = plan_on_a_terminal(
        planning, planning_harness, "a story request", reply=b"\n",
        L5_STUB_SELECTION=answer(PRESERVING["name"]))

    assert status == 0, output
    made = invocations(planning)
    assert len(made) == 2
    selecting, planning_turn = made
    assert workflow_selection.SELECTOR_PROMPT not in planning_turn["prompt"]
    assert rendered_against(planning_turn, PRESERVING)
    assert not rendered_against(planning_turn, ADDING)


def test_phase_one_carries_the_candidates_and_no_workflow_stage_facts(
    planning, planning_harness,
):
    """What the selecting turn is handed is every definition's name and its
    own words. Its control is the planning turn beside it, which does carry
    the stage facts phase one must not have."""
    plan_on_a_terminal(planning, planning_harness, "a story request",
                       reply=b"\n",
                       L5_STUB_SELECTION=answer(ADDING["name"]))

    selecting, planning_turn = invocations(planning)
    for workflow, statement in ((ADDING, ADDING_APPLIES),
                                (PRESERVING, PRESERVING_APPLIES)):
        assert workflow["name"] in selecting["prompt"], workflow["name"]
        assert statement in selecting["prompt"], workflow["name"]
    for workflow in (ADDING, PRESERVING):
        for stage in stages_of(workflow):
            assert stage not in selecting["prompt"], stage
        assert prefix_of(workflow) not in selecting["prompt"]
    assert rendered_against(planning_turn, ADDING)


def test_the_request_reaches_phase_one_as_well_as_the_planning_session(
    planning, planning_harness,
):
    """Phase one classifies *this* request, so the request has to be in front
    of it. Its control is the same reading over a different request, which
    carries that one instead."""
    plan_on_a_terminal(planning, planning_harness, "make the loader faster",
                       reply=b"\n",
                       L5_STUB_SELECTION=answer(ADDING["name"]))

    selecting = invocations(planning)[0]
    assert "make the loader faster" in selecting["prompt"]
    assert "rename the loader" not in selecting["prompt"]


# --------------------------------------------------------------------------
# The confirmation
# --------------------------------------------------------------------------


def test_the_confirmation_shows_the_reasoning_and_lists_the_alternatives(
    planning, planning_harness,
):
    """A name alone gives the developer nothing to disagree with. The
    reasoning phase one wrote is shown, and so are the workflows they may name
    instead — including the one that was not proposed."""
    reasoning = "nothing in the target does this yet, so the claim is new work"
    _, output = plan_on_a_terminal(
        planning, planning_harness, "a story request", reply=b"\n",
        L5_STUB_SELECTION=answer(ADDING["name"], reasoning))

    assert reasoning in output
    assert ADDING["name"] in output
    assert PRESERVING["name"] in output


def test_pressing_enter_plans_under_the_proposal(planning, planning_harness):
    status, output = plan_on_a_terminal(
        planning, planning_harness, "a story request", reply=b"\n",
        L5_STUB_SELECTION=answer(PRESERVING["name"]))

    assert status == 0, output
    assert rendered_against(invocations(planning)[1], PRESERVING)


def test_naming_another_workflow_plans_under_the_one_the_developer_typed(
    planning, planning_harness,
):
    """The override, observed the same way acceptance is: from the facts
    injected into the planning session. Its control is the acceptance above,
    where the same proposal with an empty reply plans under the proposal."""
    status, output = plan_on_a_terminal(
        planning, planning_harness, "a story request",
        reply=f"{PRESERVING['name']}\n".encode(),
        L5_STUB_SELECTION=answer(ADDING["name"]))

    assert status == 0, output
    planning_turn = invocations(planning)[1]
    assert rendered_against(planning_turn, PRESERVING)
    assert not rendered_against(planning_turn, ADDING)


def test_the_confirmed_workflow_is_what_the_artifact_is_held_to(
    planning, planning_harness,
):
    """The end-of-session refusal is story-069's and keeps working: what it
    holds the artifact to is the confirmed name. Its control is the session
    below, whose artifact names the workflow that was confirmed and commits."""
    head = planning.head()
    status, output = plan_on_a_terminal(
        planning, planning_harness, "a story request", reply=CONFIRMS + APPROVES,
        L5_STUB_SELECTION=answer(ADDING["name"]),
        L5_STUB_WRITE=writes((relative_artifact(), planned(PRESERVING["name"]))))

    assert status != 0
    assert ADDING["name"] in output
    assert PRESERVING["name"] in output
    assert planning.head() == head
    assert artifact_path(planning).is_file()


def test_a_confirmed_session_commits_the_artifact_naming_that_workflow(
    planning, planning_harness,
):
    """The control for the refusal above, and the whole path end to end: the
    proposal is accepted, the session is rendered against it, the artifact
    names it, and the commit happens. The second reply declines the run offer,
    which is what follows a successful commit."""
    head = planning.head()
    status, output = plan_on_a_terminal(
        planning, planning_harness, "a story request", reply=CONFIRMS + APPROVES + DECLINES,
        L5_STUB_SELECTION=answer(ADDING["name"]),
        L5_STUB_WRITE=writes((relative_artifact(), planned(ADDING["name"]))))

    assert status == 0, output
    assert planning.head() != head
    assert rendered_against(invocations(planning)[1], ADDING)


# --------------------------------------------------------------------------
# An answer that proposes nothing
# --------------------------------------------------------------------------


#: One case per shape an unusable phase-one answer can take, each stated as
#: what the stub writes: `None` writes no answer at all. Checked one by one
#: rather than one standing for the rest, because each is a different thing to
#: tell the developer.
UNUSABLE_ANSWERS = {
    "no answer written": None,
    "not json": "I think this is a refactor, honestly.",
    "not a selection": json.dumps({"workflow": ADDING["name"]}),
    "unsure": json.dumps({"reasoning": "it could be either of these"}),
    "a workflow with no definition": json.dumps(
        {"workflow": UNDEFINED, "reasoning": REASONING}),
}


@pytest.mark.parametrize("case", sorted(UNUSABLE_ANSWERS))
def test_an_unusable_answer_proposes_nothing_and_an_empty_reply_aborts(
    case, planning, planning_harness,
):
    """No session is started and no artifact is written, so nothing is
    rendered against a name nothing chose. The developer is told what happened
    and asked to name a workflow; they decline. Its control is the next test,
    where the same fixture is given a name and does plan."""
    head = planning.head()
    stub = {} if UNUSABLE_ANSWERS[case] is None else {
        "L5_STUB_SELECTION": UNUSABLE_ANSWERS[case]}

    status, output = plan_on_a_terminal(
        planning, planning_harness, "a story request", reply=b"\n",
        L5_STUB_WRITE=writes((relative_artifact(), planned(ADDING["name"]))),
        **stub)

    assert status != 0, output
    assert len(invocations(planning)) == 1
    assert "no workflow" in output
    for name in (ADDING["name"], PRESERVING["name"]):
        assert name in output, name
    assert not artifact_path(planning).exists()
    assert planning.head() == head


@pytest.mark.parametrize("case", sorted(UNUSABLE_ANSWERS))
def test_naming_a_workflow_after_an_unusable_answer_plans_under_it(
    case, planning, planning_harness,
):
    """The control for the family above: the same unusable answer, and a
    developer who names a workflow rather than declining, plans under the name
    they typed."""
    stub = {} if UNUSABLE_ANSWERS[case] is None else {
        "L5_STUB_SELECTION": UNUSABLE_ANSWERS[case]}

    status, output = plan_on_a_terminal(
        planning, planning_harness, "a story request",
        reply=f"{PRESERVING['name']}\n".encode(), **stub)

    assert status == 0, output
    made = invocations(planning)
    assert len(made) == 2
    assert rendered_against(made[1], PRESERVING)


def test_naming_a_workflow_with_no_definition_at_the_confirmation_aborts(
    planning, planning_harness,
):
    """The reply is read once and never re-asked, and the name it carried is
    reported so the developer knows why nothing started."""
    head = planning.head()
    status, output = plan_on_a_terminal(
        planning, planning_harness, "a story request",
        reply=f"{UNDEFINED}\n".encode(),
        L5_STUB_SELECTION=answer(ADDING["name"]),
        L5_STUB_WRITE=writes((relative_artifact(), planned(ADDING["name"]))))

    assert status != 0
    assert UNDEFINED in output
    assert len(invocations(planning)) == 1
    assert not artifact_path(planning).exists()
    assert planning.head() == head


# --------------------------------------------------------------------------
# A third definition, shipped and nothing else
# --------------------------------------------------------------------------


def test_a_third_definition_can_be_proposed_and_confirmed_with_no_prompt_edit(
    planning, planning_harness,
):
    """The property that makes `applies_when` worth adding. The definition is
    written into the harness root and nothing else changes: the selector
    prompt is the file this repository ships, byte for byte, and the third
    workflow is offered, proposed, confirmed and rendered against.

    Its control is the same session before the definition is written, where
    the same answer names a workflow the harness does not define and proposes
    nothing.
    """
    shipped = SELECTOR_PROMPT_PATH.read_text(encoding="utf-8")
    prompt_in_root = planning_harness / "prompts" / workflow_selection.SELECTOR_PROMPT
    assert prompt_in_root.read_text(encoding="utf-8") == shipped

    status, _ = plan_on_a_terminal(
        planning, planning_harness, "a story request", reply=b"\n",
        L5_STUB_SELECTION=answer(THIRD["name"]))
    assert status != 0, "a workflow with no definition was accepted"

    conftest.materialize_workflow(THIRD, planning_harness, rules=RULES)
    assert prompt_in_root.read_text(encoding="utf-8") == shipped

    planning.log.unlink()
    status, output = plan_on_a_terminal(
        planning, planning_harness, "a story request", reply=b"\n",
        L5_STUB_SELECTION=answer(THIRD["name"]))

    assert status == 0, output
    made = invocations(planning)
    assert len(made) == 2
    assert THIRD["name"] in made[0]["prompt"]
    assert THIRD_APPLIES in made[0]["prompt"]
    assert rendered_against(made[1], THIRD)


# --------------------------------------------------------------------------
# No terminal and no flag
# --------------------------------------------------------------------------


def test_an_invocation_with_no_terminal_and_no_flag_is_refused(
    planning, planning_harness,
):
    """Nothing is invoked, written or committed, and the message names the
    flag and the workflows the harness defines, so the repair is in the
    message. Its control is the next test, which is the same invocation
    stating a workflow."""
    head = planning.head()

    result = plan_without_a_terminal(
        planning, planning_harness, "a story request",
        L5_STUB_SELECTION=answer(ADDING["name"]),
        L5_STUB_WRITE=writes((relative_artifact(), planned(ADDING["name"]))))

    assert result.returncode != 0
    assert "--workflow" in result.stderr
    for workflow in (ADDING, PRESERVING):
        assert workflow["name"] in result.stderr, workflow["name"]
    assert invocations(planning) == []
    assert not artifact_path(planning).exists()
    assert planning.head() == head


def test_the_same_invocation_stating_a_workflow_plans_and_is_then_refused(
    planning, planning_harness,
):
    """The control the absences above need: with the flag, a session *does*
    run and is rendered against the definition the flag names, so the empty
    invocation list above is the refusal rather than a fixture that never
    invokes anything.

    Where it ends changed with story-087 and is asserted as it now is: a
    headless invocation has no terminal, so no human was present to confer a
    mandate, nothing is stamped and nothing is committed or pushed. The
    session still ran, which is the half this control exists for.
    """
    head = planning.head()
    before = subprocess.run(
        ["git", "-C", str(planning.remote), "rev-parse", DEFAULT_BRANCH],
        capture_output=True, text=True).stdout.strip()

    result = plan_without_a_terminal(
        planning, planning_harness, "--workflow", ADDING["name"],
        "a story request",
        L5_STUB_WRITE=writes((relative_artifact(), planned(ADDING["name"]))))

    made = invocations(planning)
    assert len(made) == 1
    assert rendered_against(made[0], ADDING)
    assert result.returncode != 0, result.stdout
    assert re.search(r"(?i)no human present", result.stdout), result.stdout
    assert planning.head() == head
    assert subprocess.run(
        ["git", "-C", str(planning.remote), "rev-parse", DEFAULT_BRANCH],
        capture_output=True, text=True).stdout.strip() == before


@pytest.mark.parametrize("stdin", ["", f"{ADDING['name']}\n", "\n"])
def test_the_headless_refusal_returns_whatever_is_on_stdin(
    stdin, planning, planning_harness,
):
    """It reads no input, so what is waiting on stdin cannot change what it
    does. A version that read before deciding would block on the empty case
    and would accept the named one; both are visible here, and the runner's
    timeout is what reports the block."""
    result = plan_without_a_terminal(planning, planning_harness,
                                     "a story request", stdin=stdin)

    assert result.returncode != 0
    assert "--workflow" in result.stderr
    assert invocations(planning) == []


# --------------------------------------------------------------------------
# The configured workflow key is read on no path
# --------------------------------------------------------------------------


def test_a_session_plans_although_the_configured_key_names_no_definition(
    planning, planning_harness,
):
    """The behavioural half of "the script reads the configured key on no
    path": this target configures a workflow no harness root here defines, and
    both a flagged session and a confirmed one plan anyway. A script that
    still read the key would refuse on a name with no definition."""
    assert CONFIGURED not in {candidate.name for candidate
                              in workflow_selection.candidates(planning_harness)}

    status, output = plan_on_a_terminal(
        planning, planning_harness, "--workflow", ADDING["name"],
        "a story request", reply=b"")
    assert status == 0, output

    planning.log.unlink()
    status, output = plan_on_a_terminal(
        planning, planning_harness, "a story request", reply=b"\n",
        L5_STUB_SELECTION=answer(PRESERVING["name"]))
    assert status == 0, output
    assert rendered_against(invocations(planning)[1], PRESERVING)


#: How the configured workflow key is read, spelled as the coordinator spells
#: it — `tests/test_workflow_selection.py` holds the coordinator to this exact
#: expression, so a scan for it here and the coordinator's own read cannot
#: drift apart.
CONFIGURED_KEY_READ = 'config.get("workflow"'


def test_the_planning_script_reads_the_configured_workflow_key_nowhere():
    """An absence over the shipped script, which is the subject: after this
    story no path renders the planner against a name that was neither given on
    the command line nor confirmed by a developer.

    Its control is beside it: the same scan over a copy of that script with
    the read put back reports it, so a green here cannot mean the scan has
    stopped seeing reads.
    """
    source = L5_PLAN_SOURCE.read_text(encoding="utf-8")
    assert CONFIGURED_KEY_READ not in source

    with_the_fallback = source.replace(
        "    workflow_name = selected\n",
        '    workflow_name = selected or config.get("workflow")\n')
    assert with_the_fallback != source, (
        "the line the control is planted at has moved; plant it elsewhere")
    assert CONFIGURED_KEY_READ in with_the_fallback


def test_the_coordinator_still_resolves_an_artifact_naming_no_workflow(
    preflight,
):
    """The other half of the same story: the key stays declared and stays read
    where the coordinator reads it. Driven through a real run whose artifact
    names no workflow, which executes the configured definition's stages —
    so the key a session no longer consults is still the one a run resolves
    an unnamed artifact through.

    Its control is `test_the_same_definition_carrying_a_statement_runs` above,
    where the artifact names the definition and the same stages run: the two
    together say the stages came from the configuration here rather than from
    an artifact that happened to agree.
    """
    target, harness = preflight(PRESERVING, name="configured-fallback",
                                declared=None)
    runner = Runner(target, PRESERVING)

    code = story_coordinator.run_story(STORY_ID, harness, target, runner)

    assert code == 0, runner.calls
    assert runner.calls == stages_of(PRESERVING)
    state = json.loads(
        (target / ".harness" / "runs" / STORY_ID / "state.json").read_text(
            encoding="utf-8"))
    assert state["workflow"] == PRESERVING["name"]


# ==========================================================================
# 7. Phase one can deliver its answer
#
# Everything above this section was green while no unnamed `l5-plan` had ever
# reached a proposal outside this suite: the answer was asked for under
# `tempfile.mkdtemp`, which no permission mode accepts edits within, and the
# stub wrote it there anyway because `write_text` has no permission model.
#
# So the stub above now writes only where the real turn could have written,
# and what follows asks what the invocation actually carried — the path, the
# working directory, the grant and the streams — none of which needs a model
# to settle. Nothing here judges whether a proposal was the right one.
# ==========================================================================


#: How the answer path is found in the prompt phase one was handed. The file's
#: name is `workflow_selection`'s declaration, as it is for the stub.
ANSWER_IN_PROMPT = re.compile(
    r"(\S+" + re.escape(workflow_selection.SELECTION_ANSWER) + ")")


def answer_asked_for(invocation: dict) -> Path:
    """The path an invocation was asked to write its answer to.

    Read out of the prompt the script rendered rather than out of the script's
    source, so this is what phase one was actually told.
    """
    found = ANSWER_IN_PROMPT.search(invocation["prompt"])
    assert found, "the invocation carried no answer path"
    return Path(found.group(1))


def transcript_in(logs: Path) -> Path:
    return logs / workflow_selection.SELECTION_TRANSCRIPT


def answer_in(logs: Path) -> Path:
    return logs / workflow_selection.SELECTION_ANSWER


def squashed(text: str) -> str:
    """Text with all whitespace removed, for asking whether a path is in it.

    A terminal's line endings are not part of what was printed, and a path
    printed at the end of a sentence is the same path whether or not one fell
    between two of its components.
    """
    return "".join(text.split())


def test_the_stub_writes_only_where_the_real_turn_could_have_written(tmp_path):
    """The guard the whole section rests on, driven directly.

    Run twice against the same stub: once asked for a path inside the directory
    it was invoked in, where it delivers, and once for a path outside it, where
    it writes nothing and says what the real turn said. Without this pair, every
    delivery below could be the stub's permission-free `write_text` standing in
    for a turn that could never have delivered at all.
    """
    stub = tmp_path / "claude"
    stub.write_text(stub_source(), encoding="utf-8")
    stub.chmod(0o755)
    inside_root = tmp_path / "workspace"
    inside_root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    payload = answer(ADDING["name"])

    def run(asked_for: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(stub), "--append-system-prompt",
             f"Write your answer to {asked_for}", "a request"],
            cwd=inside_root, capture_output=True, text=True,
            env={**os.environ, "L5_STUB_LOG": str(tmp_path / "log.jsonl"),
                 "L5_STUB_SELECTION": payload})

    within = answer_in(inside_root / "logs")
    within.parent.mkdir()
    run(within)
    assert within.read_text(encoding="utf-8") == payload

    beyond = answer_in(outside)
    result = run(beyond)
    assert not beyond.exists()
    assert OUTSIDE_THE_WORKSPACE in result.stdout


def test_an_unnamed_plan_reaches_a_proposal_by_the_route_production_uses(
    planning, planning_harness,
):
    """The story's first criterion, end to end: no `--workflow`, two
    invocations, and the answer arriving because the invoked process wrote it
    to the path the invocation named — the stub now writes nowhere else.

    Its control is the pair above: the same stub asked for a path it could not
    have written writes nothing, so a proposal here is a delivery rather than a
    test filling in for one.
    """
    head = planning.head()
    status, output = plan_on_a_terminal(
        planning, planning_harness, "a story request", reply=CONFIRMS + APPROVES + DECLINES,
        L5_STUB_SELECTION=answer(ADDING["name"]),
        L5_STUB_WRITE=writes((relative_artifact(), planned(ADDING["name"]))))

    assert status == 0, output
    made = invocations(planning)
    assert len(made) == 2
    assert answer_asked_for(made[0]).is_absolute()
    assert ADDING["name"] in output
    assert rendered_against(made[1], ADDING)
    assert planning.head() != head


def test_phase_one_is_asked_for_its_answer_beneath_the_target_root(
    planning, planning_harness,
):
    """Inside the workspace the permission mode accepts edits within, which is
    the whole of what was wrong: a path outside it is a path the turn reasons
    its way to and then cannot write.

    The directory is the one the target configures, and its control is the test
    beside it, where a target configuring somewhere else is asked for its
    answer there.
    """
    plan_on_a_terminal(planning, planning_harness, "a story request",
                       reply=b"\n", L5_STUB_SELECTION=answer(ADDING["name"]))

    asked_for = answer_asked_for(invocations(planning)[0])

    assert planning.root.resolve() in asked_for.resolve().parents
    assert asked_for.parent.resolve() == (planning.root / TARGET_LOGS_DIR).resolve()


def test_the_answer_path_follows_the_logs_dir_the_target_configures(
    tmp_path, planning_harness,
):
    """The control for the assertion above, and the constraint that the path is
    derived from the configuration rather than from a literal at the call site:
    a target naming another directory is asked for its answer in that one, and
    the default directory is not created at all."""
    elsewhere = build_planning(tmp_path, name="other-target",
                               logs_dir=ANOTHER_LOGS_DIR, remote=False)

    plan_on_a_terminal(elsewhere, planning_harness, "a story request",
                       reply=b"\n", L5_STUB_SELECTION=answer(ADDING["name"]))

    asked_for = answer_asked_for(invocations(elsewhere)[0])

    assert asked_for.parent.resolve() == (elsewhere.root
                                          / ANOTHER_LOGS_DIR).resolve()
    assert transcript_in(elsewhere.root / ANOTHER_LOGS_DIR).is_file()
    assert not (elsewhere.root / TARGET_LOGS_DIR).exists()


def test_phase_one_runs_with_its_working_directory_at_the_target_root(
    planning, planning_harness,
):
    """Whatever directory the developer invoked `l5-plan` from. Run from a
    subdirectory of the target, which is the case that tells the two apart: a
    session that inherited this process's directory would leave the answer path
    outside phase one's workspace, and the stub — like the real turn — would
    write nothing there.

    So the proposal reaching the confirmation is itself the check, and the
    logged directory says which directory it was.
    """
    from_here = planning.root / "src" / "deep"
    from_here.mkdir(parents=True)

    status, output = plan_on_a_terminal(
        planning, planning_harness, "a story request", reply=b"\n",
        cwd=from_here, L5_STUB_SELECTION=answer(PRESERVING["name"]))

    assert status == 0, output
    made = invocations(planning)
    selecting = made[0]
    assert Path(selecting["cwd"]).resolve() == planning.root.resolve()
    assert Path(selecting["cwd"]).resolve() != from_here.resolve()
    assert len(made) == 2, output
    assert rendered_against(made[1], PRESERVING)


def granted_tools(invocation: dict) -> list[str]:
    """The tools an invocation's argument list grants, in order.

    Read off the command line rather than out of the script, because what a
    turn may do being readable from the invocation is the point of stating it
    there.
    """
    argv = invocation["argv"]
    return [argv[index + 1] for index, item in enumerate(argv)
            if item == "--allowedTools"]


def test_phase_ones_argument_list_grants_the_tool_its_answer_needs(
    planning, planning_harness,
):
    """One tool for a turn whose entire output is one JSON file. Its control is
    the flagged session beside it, whose single invocation grants none — so a
    green here cannot mean the reader has stopped seeing grants."""
    plan_on_a_terminal(planning, planning_harness, "a story request",
                       reply=b"\n", L5_STUB_SELECTION=answer(ADDING["name"]))
    assert granted_tools(invocations(planning)[0]) == ["Write"]

    planning.log.unlink()
    plan_on_a_terminal(planning, planning_harness, "--workflow", ADDING["name"],
                       "a story request", reply=b"")
    assert granted_tools(invocations(planning)[0]) == []


def test_phase_ones_stdin_is_not_the_sessions_stdin(planning, planning_harness):
    """A turn given its whole request on the command line has nothing to read,
    and inheriting the terminal buys a wait for input that never comes.

    Its control is in the same session: the planning turn beside it does
    inherit the developer's terminal on all three streams, so a green here
    cannot mean the pty failed to reach either invocation.
    """
    plan_on_a_terminal(planning, planning_harness, "a story request",
                       reply=b"\n", L5_STUB_SELECTION=answer(ADDING["name"]))

    selecting, planning_turn = invocations(planning)

    assert selecting["tty"][0] is False
    assert planning_turn["tty"] == [True, True, True]


# --------------------------------------------------------------------------
# What the turn said on the way there is kept
# --------------------------------------------------------------------------


def test_phase_ones_output_is_kept_after_an_invocation_that_proposed(
    planning, planning_harness,
):
    """Both streams, under the configured logs directory, headed by the request
    that was being answered."""
    plan_on_a_terminal(planning, planning_harness, "make the loader faster",
                       reply=b"\n", L5_STUB_SELECTION=answer(ADDING["name"]))

    kept = transcript_in(planning.root / TARGET_LOGS_DIR)

    assert kept.is_file()
    held = kept.read_text(encoding="utf-8")
    assert STUB_STDOUT in held
    assert STUB_STDERR in held
    assert "make the loader faster" in held


def test_phase_ones_output_is_kept_after_an_invocation_that_proposed_nothing(
    planning, planning_harness,
):
    """The invocation whose output there was never any other way to see: it
    delivered nothing, so what it said on the way there is all there is. Its
    control is the test above, where the same reading finds the same output
    after an invocation that did deliver."""
    status, _ = plan_on_a_terminal(planning, planning_harness,
                                   "a story request", reply=b"\n")

    assert status != 0
    held = transcript_in(planning.root / TARGET_LOGS_DIR).read_text(
        encoding="utf-8")
    assert STUB_STDOUT in held
    assert STUB_STDERR in held
    assert "a story request" in held


def test_a_later_invocation_appends_to_the_transcript_rather_than_replacing_it(
    planning, planning_harness,
):
    """An earlier invocation's evidence survives a later one: the file is a
    record of what phase one has said in this repository, not of what it said
    most recently."""
    plan_on_a_terminal(planning, planning_harness, "the earlier request",
                       reply=b"\n")
    kept = transcript_in(planning.root / TARGET_LOGS_DIR)
    after_one = kept.read_text(encoding="utf-8")

    plan_on_a_terminal(planning, planning_harness, "the later request",
                       reply=b"\n")
    after_two = kept.read_text(encoding="utf-8")

    assert "the earlier request" in after_two
    assert "the later request" in after_two
    assert after_two.startswith(after_one)
    assert after_two.count(STUB_STDOUT) == after_one.count(STUB_STDOUT) + 1


def test_the_message_naming_no_proposal_names_the_transcript(
    planning, planning_harness,
):
    """"It wrote no answer" tells a developer what happened and not where to
    look. Its control is the session beside it, which reaches a proposal: the
    path says nothing new there and is not printed, so a green here cannot mean
    the reading finds the path in any output at all."""
    kept = transcript_in(planning.root / TARGET_LOGS_DIR)

    _, silent = plan_on_a_terminal(planning, planning_harness,
                                   "a story request", reply=b"\n")
    assert squashed(str(kept)) in squashed(silent)

    planning.log.unlink()
    _, proposing = plan_on_a_terminal(
        planning, planning_harness, "a story request", reply=b"\n",
        L5_STUB_SELECTION=answer(ADDING["name"]))
    assert squashed(str(kept)) not in squashed(proposing)


# --------------------------------------------------------------------------
# An answer is this invocation's or it is nothing
# --------------------------------------------------------------------------


def test_an_answer_left_by_an_earlier_invocation_is_not_read_as_this_ones(
    planning, planning_harness,
):
    """A stale answer naming a workflow would report a proposal this invocation
    never made, and the session would be rendered against it.

    Its control is the second half: the same content, written by the invocation
    itself, is a proposal — so "no proposal" here is the answer being this
    invocation's or nothing, rather than the reader having stopped seeing
    answers.
    """
    stale = answer_in(planning.root / TARGET_LOGS_DIR)
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(answer(ADDING["name"]), encoding="utf-8")

    status, output = plan_on_a_terminal(planning, planning_harness,
                                        "a story request", reply=b"\n")

    assert status != 0, output
    assert "no workflow" in output
    assert len(invocations(planning)) == 1

    planning.log.unlink()
    status, output = plan_on_a_terminal(
        planning, planning_harness, "a story request", reply=b"\n",
        L5_STUB_SELECTION=answer(ADDING["name"]))
    assert status == 0, output
    assert rendered_against(invocations(planning)[1], ADDING)


def test_the_answer_is_not_left_behind_for_the_next_invocation(
    planning, planning_harness,
):
    """Whatever this invocation did with it. The file is observed in place
    first, which is the control the absence needs: the reading does report an
    answer file when one is there."""
    left = answer_in(planning.root / TARGET_LOGS_DIR)
    left.parent.mkdir(parents=True, exist_ok=True)
    left.write_text(answer(ADDING["name"]), encoding="utf-8")
    assert left.exists()

    plan_on_a_terminal(planning, planning_harness, "a story request",
                       reply=b"\n", L5_STUB_SELECTION=answer(ADDING["name"]))

    assert not left.exists()


def test_the_flag_asks_for_no_answer_and_keeps_no_transcript(
    planning, planning_harness,
):
    """A session given `--workflow` invokes phase one not at all, so neither
    artifact appears. Its control is the same target without the flag, where
    both the transcript and an invocation carrying an answer path do."""
    logs = planning.root / TARGET_LOGS_DIR

    status, _ = plan_on_a_terminal(planning, planning_harness, "--workflow",
                                   ADDING["name"], "a story request", reply=b"")

    assert status == 0
    assert not transcript_in(logs).exists()
    assert not answer_in(logs).exists()
    assert ANSWER_IN_PROMPT.search(invocations(planning)[0]["prompt"]) is None

    planning.log.unlink()
    plan_on_a_terminal(planning, planning_harness, "a story request",
                       reply=b"\n", L5_STUB_SELECTION=answer(ADDING["name"]))

    assert transcript_in(logs).is_file()
    assert ANSWER_IN_PROMPT.search(invocations(planning)[0]["prompt"])
