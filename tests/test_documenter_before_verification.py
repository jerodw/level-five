"""Independent validation for story-045: the documenter runs before the
verifier, and documentation is a retry category.

One reorder, three consequences, and this module holds all three:

  * **the order.** A workflow that lists a documenting stage before the stage
    that judges it is executed in that order, and the coordinator follows the
    definition rather than a sequence written into itself.
  * **the third route.** A verifier's routing table may declare a category
    routing back to the documenting stage; the category reaches the verifier's
    prompt through the injection story-028 landed rather than through any prose
    in a template; and a failing verdict naming it re-enters at that stage.
  * **what the reorder buys.** The verifier is handed the documenter's output,
    and the clean-clone check — which runs on the verifier's passing verdict —
    clones a tree that already holds the documenter's edits. That second one is
    story-043 reduced to a fixture: a documenter wrote a sentence naming a
    `tests/` module the same story deleted, the suite rejected it, and the run
    completed anyway because the check had already passed minutes before.

Almost nothing here is asserted from source. A target repository is built
under tmp_path, fake stage agents drive it into each shape, and what the
coordinator actually wrote — the execution history, the rendered prompts,
the clean clone's own committed tree, the run directory — is read back.

**Which workflow those runs are driven by changed in story-048.** Every
assertion above is about a *mechanism*: that the coordinator invokes stages in
the order a definition lists them, that it routes a verdict to the stage a
declared category names, that it clones after the documenting stage has run.
A stage list is an input to each of those questions rather than its subject, so
the runs below are driven by a workflow this module builds with
`conftest.build_workflow` and materializes into a harness root of its own.
Before that conversion they were driven by `workflows/story-workflow.json`,
which made every one of them go red the moment this deployment's stage list
changed for reasons none of them had an opinion about.

What is *not* an input, and so still reads what this repository ships, is
listed in `tests/test_baseline_honesty.py` beside this module's name and
restated at each assertion below:

  * that this deployment's routing table offers a documentation category, what
    that category's `when` clause distinguishes, and that the two categories
    story-045 inherited are still declared — the subject is this deployment's
    configuration, and a built table would assert the builder's arguments back
    to itself;
  * that the reorder changed no other stage declaration, read at the two ends
    of this story's own commit range out of git history;
  * that `prompts/verifier.md` — the template this repository ships — declares
    both documenter placeholders, says in its role layer that the documenter's
    output is part of the verifier's subject, and restates no category,
    destination or `when` of its own.

The declaration-level statement of the *order* this deployment ships moved to
`tests/test_shipped_workflow_is_valid.py`, where the rest of this deployment's
configuration is stated; `test_this_deployment_documents_before_it_verifies`
and `test_this_deployment_runs_the_stages_story_045_ordered` there are the
successors of the `test_the_workflow_lists_the_stages_in_the_new_order` this
module used to hold.

Every absence asserted here carries a demonstration that it can fail, and
for this story the demonstration has one natural shape: *the previous
behaviour*. A second definition is built by reordering the first one's stages
and dropping the documentation route, materialized into a harness root beside
it, and the same fixture is run against both, so each ordering claim is shown
red under the order this story replaced:

  * "the clean clone holds the documenter's edits" sits beside the same run
    under the old order, where the same clone does not hold them;
  * "a documented claim the suite rejects ends the run" sits beside the same
    documenter under the old order, where the run completes with a red
    suite — which is what story-043 shipped;
  * "the reordered workflow has no routing problems" sits beside the same
    check over the old order with the route left in place, which reports the
    documentation route by name;
  * "`prompts/verifier.md` restates no category, destination or `when`" sits
    beside the prompt rendered from that same template, which carries all
    three;
  * "the verifier's prompt carries the documenter's artifacts" sits beside
    the same template rendered against a run directory holding neither,
    where both placeholders resolve to the optional-placeholder None.

Nothing here invokes a model: every run goes through a fake agent runner,
and `no_model` below turns the single subprocess call that would reach one
into a failure.
"""
import json
import subprocess
from pathlib import Path

import pytest

import agent_runner
import context_assembler
import harness_config
import story_coordinator
from agent_runner import AgentResult
import conftest

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]

#: The workflow these runs execute, assembled by the builder in
#: `tests/conftest.py` rather than resolved out of what this repository
#: deploys. It declares the shape story-045 landed — a documenting stage
#: between the validating stage and the stage that judges, and a routing table
#: whose third category comes back to it — because that shape is the *input*
#: every mechanism assertion below needs, not a report of what is deployed.
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
        clean_clone={"result": conftest.CLEAN_CLONE_RESULT,
                     "retry_stage": conftest.StageRef(0)},
        retry_routing={
            "code-defect": {
                "stage": conftest.StageRef(0),
                "when": "the behaviour the story asked for is missing"},
            "validation-defect": {
                "stage": conftest.StageRef(1),
                "when": "the validation does not exercise what it claims"},
            "prose-defect": {
                "stage": conftest.StageRef(2),
                "when": "the defect is in the written description itself"},
        }),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="documents-before-verifying",
)
STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, DOCUMENTING, VERIFYING = STAGE_NAMES

#: The built table, and the category that comes back to the documenting stage,
#: both derived from the definition above rather than spelled here.
BUILT_ROUTES = next(stage for stage in WORKFLOW["stages"]
                    if "on_failure" in stage)["on_failure"]["retry_routing"]
BUILT_CATEGORIES = sorted(BUILT_ROUTES)
DOC_CATEGORY = next(category for category, route in BUILT_ROUTES.items()
                    if route["stage"] == DOCUMENTING)

#: The order this story replaced, derived from the order above by moving the
#: documenting stage behind the stage that judges — so it stays the *previous*
#: order rather than a second list to maintain, and so both definitions carry
#: the same stage names and the same declarations.
PREVIOUS_ORDER = [WRITING, VALIDATING, VERIFYING, DOCUMENTING]


def reordered(workflow: dict, order: list[str], *, name: str) -> dict:
    """`workflow` with its stages in `order`, changing no declaration."""
    built = json.loads(json.dumps(workflow))
    by_name = {stage["name"]: stage for stage in built["stages"]}
    built["stages"] = [by_name[stage_name] for stage_name in order]
    built["name"] = name
    return built


def without_documentation_route(workflow: dict) -> dict:
    verifier = next(s for s in workflow["stages"] if "on_failure" in s)
    verifier["on_failure"]["retry_routing"].pop(DOC_CATEGORY)
    return workflow


#: The workflow as it stood before this story: old order, two routes.
#:
#: Both halves, because they are inseparable — `retry_routing_problems` refuses
#: the documentation route the moment the documenting stage sits after the
#: stage that judges, so a workflow in the previous order that kept the route
#: is not a workflow any run reaches.
PREVIOUS_WORKFLOW = without_documentation_route(
    reordered(WORKFLOW, PREVIOUS_ORDER, name="previous-order"))
#: The illegal pairing above, kept whole so the pre-flight refusal has
#: something to refuse.
PREVIOUS_WITH_ROUTE = reordered(WORKFLOW, PREVIOUS_ORDER,
                                name="old-order-with-route")

# --------------------------------------------------------------------------
# What this repository ships, where what it ships is the subject
#
# Each of the three readings below is declared in
# `tests/test_baseline_honesty.py`. They are not inputs to a mechanism: they
# are the configuration story-045 landed and the template it left alone.
# --------------------------------------------------------------------------

SHIPPED = conftest.shipped_workflow(REPO_ROOT, "story-workflow")
SHIPPED_VERIFIER = next(s for s in SHIPPED["stages"] if "on_failure" in s)
SHIPPED_VERIFIER_NAME = SHIPPED_VERIFIER["name"]
ROUTES = SHIPPED_VERIFIER["on_failure"]["retry_routing"]

#: This deployment's own three categories and the stage its documentation
#: category routes to, named outright because they are what is being asserted.
#: `tests/test_retry_routing.py` deliberately writes none of them, and the
#: difference is the subject: that module validates *routing whatever a
#: workflow declares*, where a name written into it would let a coordinator
#: that routes to a constant pass. These three assertions validate story-045's
#: own acceptance criteria, which name the categories, so a deployment that
#: quietly declared something else is exactly what they must report.
EXPECTED_CATEGORIES = ["documentation", "implementation", "validation"]
DOCUMENTATION = "documentation"
DOCUMENTER = "documenter"
#: The order this deployment shipped before and after story-045, read here only
#: by the git-history comparison below — the *declaration-level* statement of
#: today's order lives in `tests/test_shipped_workflow_is_valid.py`.
SHIPPED_ORDER = ["implementer", "tester", "documenter", "verifier"]
SHIPPED_PREVIOUS_ORDER = ["implementer", "tester", "verifier", "documenter"]

VERIFIER_TEMPLATE_PATH = REPO_ROOT / "prompts" / f"{SHIPPED_VERIFIER_NAME}.md"
VERIFIER_TEMPLATE = VERIFIER_TEMPLATE_PATH.read_text(encoding="utf-8")

RULES = harness_config.load_rules(REPO_ROOT)
MAX_RETRIES = RULES["max_retries"]

STORY_ID = "story-001"
DEFAULT_BRANCH = "main"
ARCHITECTURE_DOC = ".harness/docs/ARCHITECTURE.md"

#: The documenter's marker in the repository tree, and its marker in the
#: report it writes into the run directory. Two markers because the two
#: reach the verifier by different routes — one through the architecture
#: document the tree carries, one through the documentation report — and a
#: single marker could not tell them apart.
DOC_MARKER = "DOCUMENTER_WROTE_THIS"
#: Deliberately not a phrase any template itself uses, so its presence in a
#: rendered prompt is content that was injected rather than the label the
#: template prints above the placeholder.
REPORT_MARKER = "REPORTED_BY_THE_DOCUMENTER"

#: story-043's case, reduced to one sentence: a documenter naming a tests/
#: module the same story deleted. The name is one no module in this
#: repository has, so a suite that rejects it is rejecting this sentence.
DELETED_MODULE = "tests/test_a_module_this_story_deleted.py"

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


def failing(category: str) -> dict:
    return {
        "status": "failed",
        "blocking_issues": [{
            "severity": "high",
            "issue": "the sample behavior is not implemented",
            "location": "src/app.py:1",
            "required_behavior": "the sample behavior exists",
        }],
        "unverified": [],
        "retry_recommended": True,
        "retry_target": category,
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
workflow: {workflow}
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - {doc}
test_command: {test_command}
tests_dir: tests/
"""


# --------------------------------------------------------------------------
# No model, for every test in this file
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    """Turn the one call that would reach a model into a failure.

    Every run below passes a fake runner explicitly, so this should never
    fire; it exists so "no model was invoked" is enforced rather than
    assumed, and the test beneath it shows the guard can fire.
    """
    real = agent_runner.subprocess.Popen

    def guarded(command, *args, **kwargs):
        first = command[0] if isinstance(command, (list, tuple)) else command
        if str(first).endswith("claude"):
            raise AssertionError("a model was invoked")
        return real(command, *args, **kwargs)

    monkeypatch.setattr(agent_runner.subprocess, "Popen", guarded)


def test_the_no_model_guard_fires_when_a_model_is_invoked(tmp_path):
    """The control for the guard every other test in this file runs under."""
    with pytest.raises(AssertionError, match="a model was invoked"):
        agent_runner.run_agent("prompt", stage=WRITING, cwd=tmp_path,
                               log_path=tmp_path / "agent.log")


# --------------------------------------------------------------------------
# A target repository, harness roots, and a fake runner
# --------------------------------------------------------------------------


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=check)


def build_target(root: Path, *, workflow: str | None = None,
                 test_command: str = "echo tests-ok") -> Path:
    """A target repository with a story, standards and an architecture doc.

    The run directory and the log directory are ignored, as they are in a
    real target: the clean-clone check copies untracked-but-not-ignored
    files into the clone, and a run's own artifacts are not part of the tree
    the suite is meant to be run against.
    """
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".gitignore", ".harness/runs/\n.harness/logs/\n")
    write(root / ".harness" / "config.yaml",
          CONFIG.format(workflow=workflow or WORKFLOW["name"],
                        doc=ARCHITECTURE_DOC, test_command=test_command))
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ARCHITECTURE_DOC, "# Architecture\n\nThe harness runs stages.\n")
    write(root / "src" / "app.py", "print('hello')\n")
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    git(root, "branch", "-M", DEFAULT_BRANCH)
    return root


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return build_target(tmp_path / "target")


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A harness root carrying the definition built above, so a converted case
    drives a real coordinator loading a real file."""
    return conftest.materialize_workflow(WORKFLOW, tmp_path / "new-order-harness")


@pytest.fixture
def old_order_harness(tmp_path: Path) -> Path:
    """The same, for the definition in the order this story replaced.

    It is not a workflow anything ships any more, and only a run driven from it
    can show what the reorder changed.
    """
    return conftest.materialize_workflow(PREVIOUS_WORKFLOW,
                                         tmp_path / "old-order-harness")


class Runner:
    """A fake agent runner: each stage writes the artifacts it declares.

    The writing stage edits the repository tree and the documenting stage edits
    the architecture document, which is what gives the clean-clone check two
    distinguishable things to carry into its clone. `documented` is the
    sentence the documenting stage writes, so a test chooses whether the
    document makes a claim the suite accepts.

    The verdicts are consumed one per verifier call, the last repeating, so
    a run is driven into a shape by listing what the verifier says.
    """

    def __init__(self, target_root: Path, verdicts: list | None = None, *,
                 documented: str = DOC_MARKER):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.verdicts = list(verdicts or [PASS])
        self.documented = documented
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None, max_budget_usd=None):
        self.calls.append(stage)
        if stage == WRITING:
            write(self.target_root / "src" / "app.py",
                  "print('hello')\n# the story's change\n")
            write_json(self.run_dir / conftest.CHANGED_FILES,
                       {"modified": ["src/app.py"], "created": [], "deleted": []})
            write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY, "Did the work.\n")
        elif stage == VALIDATING:
            write_json(self.run_dir / conftest.TEST_RESULTS, {
                "status": "passed", "tests_written": 1, "tests_run": 1,
                "tests_passed": 1, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES, {
                "modified": [], "created": ["tests/test_app.py"], "deleted": [],
            })
        elif stage == DOCUMENTING:
            write(self.target_root / ARCHITECTURE_DOC,
                  f"# Architecture\n\nThe harness runs stages.\n"
                  f"{self.documented}\n")
            write(self.run_dir / conftest.DOCUMENTATION_REPORT,
                  f"# Documentation report\n\n{REPORT_MARKER}: "
                  f"{self.documented}\n")
            write_json(self.run_dir / conftest.DOCUMENTER_CHANGED_FILES, {
                "modified": [ARCHITECTURE_DOC], "created": [], "deleted": [],
            })
        elif stage == VERIFYING:
            verdict = self.verdicts.pop(0) if len(self.verdicts) > 1 \
                else self.verdicts[0]
            # A failed verdict accounts for the guidance in force for the
            # attempt it judges, reporting every entry unmet — the ordinary
            # under-delivery case, which routes as it always has.
            verdict = conftest.answering_guidance(verdict, self.run_dir)
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, verdict)
            if verdict["status"] == "failed":
                write_json(self.run_dir / conftest.RETRY_GUIDANCE, {
                    "current_focus": [{
                        "focus": "fix what the verdict named",
                        "satisfied_when": "what the verdict named is fixed",
                    }],
                    "preserve_behavior": ["existing behavior"],
                    "retry_scope": ["src/app.py"],
                })
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path) -> Path:
    return target_root / ".harness" / "runs" / STORY_ID


def history_of(target_root: Path) -> list[dict]:
    return json.loads((run_dir_of(target_root) / "execution-history.json")
                      .read_text(encoding="utf-8"))


def prompt_of(target_root: Path, stage: str, attempt: int) -> str:
    return (run_dir_of(target_root) /
            story_coordinator.prompt_file(stage, attempt)).read_text(
                encoding="utf-8")


def state_of(target_root: Path) -> dict:
    return json.loads((run_dir_of(target_root) / "state.json").read_text(
        encoding="utf-8"))


# --------------------------------------------------------------------------
# The order
# --------------------------------------------------------------------------


def test_a_run_invokes_the_stages_in_that_order(target, harness_root):
    runner = Runner(target)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0
    assert runner.calls == STAGE_NAMES


def test_a_run_under_the_previous_order_invokes_them_in_the_previous_order(
    tmp_path, old_order_harness,
):
    """The control for the assertion above.

    The order is something the definition decides and the loop follows, so
    the same coordinator and the same fake runner produce the previous order
    when handed the previous definition. Without this, "the calls came in
    this order" would hold equally against a loop that ignored the
    definition and ran a sequence written into it.
    """
    root = build_target(tmp_path / "old-order-target",
                        workflow=PREVIOUS_WORKFLOW["name"])
    runner = Runner(root)
    assert story_coordinator.run_story(
        STORY_ID, old_order_harness, root, runner) == 0
    assert runner.calls == PREVIOUS_ORDER
    assert runner.calls != STAGE_NAMES


def test_the_move_changed_no_stage_declaration(harness_root):
    """A reorder and nothing else: every stage's own declaration is what it
    was, apart from the one route the story adds.

    A shipped-artifact reading, deliberately. The subject is what *this
    repository's* story-045 commit did to the definition it deploys — not the
    working tree against whatever HEAD happens to be, and not a definition this
    module built, which has no reorder to compare.

    Both ends are frozen past texts, and since story-053 both are carried as
    committed fixtures rather than resolved out of this repository's commit
    graph. What the story did to the definition does not change when the
    repository is committed to, renamed, squashed or rebased; the resolution
    did. The two texts are the same two texts, lifted from exactly those
    bounds, and the assertion that they carry different stage orders is the
    control that they are two files rather than one read twice.
    """
    before = json.loads(conftest.history_fixture(
        "story-workflow.at-story-045-baseline.json"))
    after = json.loads(conftest.history_fixture(
        "story-workflow.at-story-045-endpoint.json"))

    old = {stage["name"]: stage for stage in before["stages"]}
    new = {stage["name"]: stage for stage in after["stages"]}
    assert sorted(old) == sorted(new)

    # The comparison is live: the two readings really are of different
    # orders, so the equality below is a statement about declarations rather
    # than about one file read twice.
    assert [s["name"] for s in before["stages"]] == SHIPPED_PREVIOUS_ORDER
    assert [s["name"] for s in after["stages"]] == SHIPPED_ORDER

    for name in old:
        if name == SHIPPED_VERIFIER_NAME:
            continue
        assert new[name] == old[name], name

    old_routes = old[SHIPPED_VERIFIER_NAME]["on_failure"]["retry_routing"]
    new_routes = new[SHIPPED_VERIFIER_NAME]["on_failure"]["retry_routing"]
    assert set(new_routes) - set(old_routes) == {DOCUMENTATION}
    for category, route in old_routes.items():
        assert new_routes[category] == route, category
    # Everything else the verifier declares — its prompt, its outputs, its
    # schemas, its clean-clone declaration, its self-route budget — is
    # untouched by the move.
    assert {key: value for key, value in new[SHIPPED_VERIFIER_NAME].items()
            if key != "on_failure"} == \
        {key: value for key, value in old[SHIPPED_VERIFIER_NAME].items()
         if key != "on_failure"}


# --------------------------------------------------------------------------
# The third route
# --------------------------------------------------------------------------


def test_the_verifier_declares_exactly_the_three_categories():
    """A shipped-artifact reading: story-045's acceptance criterion is that
    *this deployment* offers the verifier a documentation category, which only
    the deployed table can answer."""
    assert sorted(ROUTES) == EXPECTED_CATEGORIES
    assert ROUTES[DOCUMENTATION]["stage"] == DOCUMENTER
    assert ROUTES[DOCUMENTATION]["when"].strip()


def test_the_documentation_when_tells_the_document_from_the_code_it_describes():
    """The `when` is what the verifier chooses by, so what it distinguishes
    is a property of this story rather than of any code path — and of this
    deployment's own wording, which is why the shipped table is read."""
    when = ROUTES[DOCUMENTATION]["when"].lower()
    # A defect in the document itself is the subject.
    assert "in the documentation itself" in when
    # And the case that belongs to the other category is stated outright,
    # naming that category.
    assert "accurately describes wrong behaviour" in when
    assert "implementation defect" in when
    assert "not a documentation one" in when


def test_the_two_existing_routes_are_preserved():
    """Shipped again, and for the same reason: what story-045 must not have
    disturbed is what this deployment declared before it."""
    assert ROUTES["implementation"]["stage"] == "implementer"
    assert ROUTES["validation"]["stage"] == "tester"


def test_the_reordered_workflow_declares_no_routing_problem():
    """The positive half of the pair below, against the built definition.

    That the *shipped* definition has no routing problems is asserted by
    `test_the_shipped_workflow_routes_every_retry_backwards_to_a_stage_it_defines`
    in tests/test_shipped_workflow_is_valid.py, where the deployment is the
    subject. Here the question is whether a table declaring a route back to a
    documenting stage that sits before the judge is legal at all.
    """
    assert story_coordinator.retry_routing_problems(WORKFLOW["stages"]) == []


def test_restoring_the_previous_order_makes_the_documentation_route_a_problem():
    """The control for the absence above, and the reason the reorder and the
    route are one story: the route is legal only after the move."""
    problems = story_coordinator.retry_routing_problems(
        PREVIOUS_WITH_ROUTE["stages"])

    assert len(problems) == 1, problems
    assert DOC_CATEGORY in problems[0]
    assert DOCUMENTING in problems[0]
    assert VERIFYING in problems[0]


def test_the_previous_order_with_the_route_is_refused_at_pre_flight(tmp_path):
    """End to end: no run directory, no branch, no agent invoked."""
    harness = conftest.materialize_workflow(
        PREVIOUS_WITH_ROUTE, tmp_path / "refused-harness")
    root = build_target(tmp_path / "refused-target",
                        workflow=PREVIOUS_WITH_ROUTE["name"])
    runner = Runner(root)

    assert story_coordinator.run_story(STORY_ID, harness, root, runner) == 1

    assert runner.calls == []
    assert not run_dir_of(root).exists()
    assert git(root, "branch", "--list", f"story/{STORY_ID}").stdout.strip() == ""


def test_the_same_workflow_without_the_route_is_not_refused(
    tmp_path, old_order_harness,
):
    """The control for the refusal above: what is refused is the route, not
    the built harness or the old order."""
    root = build_target(tmp_path / "accepted-target",
                        workflow=PREVIOUS_WORKFLOW["name"])
    runner = Runner(root)

    assert story_coordinator.run_story(
        STORY_ID, old_order_harness, root, runner) == 0
    assert runner.calls == PREVIOUS_ORDER


# --------------------------------------------------------------------------
# A documentation verdict re-enters at the documenter
# --------------------------------------------------------------------------


def test_a_documentation_verdict_re_runs_the_documenter_and_not_the_implementer(
    target, harness_root,
):
    runner = Runner(target, [failing(DOC_CATEGORY), PASS])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0

    assert runner.calls == STAGE_NAMES + [DOCUMENTING, VERIFYING]
    # The stages before the destination are not re-invoked on the way back.
    assert runner.calls.count(WRITING) == 1
    assert runner.calls.count(VALIDATING) == 1
    assert runner.calls.count(DOCUMENTING) == 2
    # The retried stage is told it is on a retry, and by which category.
    retried = prompt_of(target, DOCUMENTING, 2)
    assert DOC_CATEGORY in retried
    assert context_assembler.PLACEHOLDER.search(retried) is None


@pytest.mark.parametrize("category", BUILT_CATEGORIES)
def test_the_history_records_every_category_the_same_way(
    target, harness_root, category,
):
    """The documentation category is recorded exactly as the other two are:
    same event, same fields, same shape — only the values differ."""
    destination = BUILT_ROUTES[category]["stage"]
    runner = Runner(target, [failing(category), PASS])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0

    entry = next(e for e in history_of(target)
                 if e["event"] == "verification-failed")
    assert entry["retry_category"] == category
    assert entry["retry_stage"] == destination
    assert entry["retry_decision"] == "retry"
    assert entry["stage"] == VERIFYING
    assert entry["verifier_outcome"] == "failed"
    assert destination in entry["message"] and category in entry["message"]


def test_a_run_that_passes_first_time_invokes_the_documenter_once(
    target, harness_root,
):
    """The cost the story accepted is paid only on a retry. A reorder that
    quietly ran the documenter twice on the common case would be a
    different, more expensive trade."""
    runner = Runner(target)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0

    assert runner.calls.count(DOCUMENTING) == 1
    started = [e for e in history_of(target)
               if e["event"] == "stage-started" and e["stage"] == DOCUMENTING]
    assert len(started) == 1


# --------------------------------------------------------------------------
# The verifier's new subject
# --------------------------------------------------------------------------


def test_the_verifiers_prompt_carries_the_documenters_report_and_record(
    target, harness_root,
):
    runner = Runner(target)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0

    run_dir = run_dir_of(target)
    prompt = prompt_of(target, VERIFYING, 1)

    report = (run_dir / conftest.DOCUMENTATION_REPORT).read_text(encoding="utf-8")
    record = (run_dir / conftest.DOCUMENTER_CHANGED_FILES).read_text(
        encoding="utf-8")
    assert report.strip() in prompt
    assert record.strip() in prompt
    assert REPORT_MARKER in prompt
    assert ARCHITECTURE_DOC in prompt
    assert context_assembler.PLACEHOLDER.search(prompt) is None


def test_the_prompt_says_none_when_the_documenter_wrote_nothing(
    target, harness_root, tmp_path,
):
    """The control for the assertion above.

    The same template rendered against a run directory holding neither
    artifact resolves both placeholders to the optional-placeholder None, so
    what the run's prompt carried is content that was injected rather than
    prose the template always had.
    """
    empty = tmp_path / "empty-run-dir"
    (empty / "verification").mkdir(parents=True)
    template = (harness_root / "prompts" /
                next(s["prompt"] for s in WORKFLOW["stages"]
                     if s["name"] == VERIFYING)).read_text(encoding="utf-8")

    context = context_assembler.build_context(
        story_text=STORY,
        story=story_coordinator.read_story(STORY).parsed,
        run_dir=empty,
        target_root=target,
        harness_root=harness_root,
        config=harness_config.load_config(target),
        rules=RULES,
        workflow=WORKFLOW,
        retry_count=0,
    )
    rendered = context_assembler.render(template, context)

    assert context["documentation_report"] is None
    assert context["documenter_changed_files"] is None
    assert REPORT_MARKER not in rendered
    assert context_assembler.PLACEHOLDER.search(rendered) is None


def test_the_template_declares_both_placeholders():
    """A shipped-artifact reading: whether *this repository's* verifier
    template asks for the documenter's output is a fact about the template it
    ships, and no built template can stand in for it."""
    assert "{{documentation_report}}" in VERIFIER_TEMPLATE
    assert "{{documenter_changed_files}}" in VERIFIER_TEMPLATE


def test_the_role_layer_says_the_documenters_output_is_part_of_the_subject():
    """Positive, and shipped for the same reason as the assertion above: the
    template must say it, so it fails on its own if the sentence is dropped."""
    role = VERIFIER_TEMPLATE.split("[Role Layer]", 1)[1].split("[Workflow Layer]", 1)[0]
    # Whitespace-collapsed, so a sentence the template hard-wraps reads as
    # one sentence rather than as whatever the wrap happened to split.
    responsibilities = " ".join(role.split("Do not:", 1)[0].lower().split())
    assert "documentation report" in responsibilities
    assert "changed-files record" in responsibilities
    assert "documenter" in responsibilities


def test_the_prompt_names_all_three_routes_with_destination_and_when(
    target, harness_root,
):
    """Injection, against the built table: every category a workflow declares
    reaches the judging stage's prompt with its destination and its `when`."""
    runner = Runner(target)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0
    prompt = prompt_of(target, VERIFYING, 1)

    for category, route in BUILT_ROUTES.items():
        line = next((line for line in prompt.splitlines()
                     if category in line and route["stage"] in line), None)
        assert line is not None, category
        assert route["when"] in line


def test_the_template_restates_no_category_destination_or_when(target):
    """The routes reach the verifier by injection, not by prose — asserted of
    the template *this repository ships*, which is what that claim is about.

    The absence is asserted against the same template *rendered* as its
    control: every pairing and every `when` this test says the template lacks
    is shown present once the same template has been rendered against this
    deployment's own routing table, so a check looking at the wrong text would
    report the pairing missing from both.
    """
    context = context_assembler.build_context(
        story_text=STORY,
        story=story_coordinator.read_story(STORY).parsed,
        run_dir=run_dir_of(target),
        target_root=target,
        harness_root=REPO_ROOT,
        config=harness_config.load_config(target),
        rules=RULES,
        workflow=SHIPPED,
        retry_count=0,
    )
    rendered = context_assembler.render(VERIFIER_TEMPLATE, context)

    for category, route in ROUTES.items():
        pairing = f"{category} -> {route['stage']}"
        assert pairing not in VERIFIER_TEMPLATE, category
        assert pairing in rendered, category
        assert route["when"] not in VERIFIER_TEMPLATE, category
        assert route["when"] in rendered, category


def test_a_fourth_category_reaches_the_prompt_with_no_edit_to_the_template(
    tmp_path, target, harness_root,
):
    """The property story-028 landed, of which this story's third category is
    the first real test — asserted here against a fourth, so the injection is
    shown to be general rather than to have been widened by hand to three."""
    added = "packaging-defect"
    assert added not in BUILT_ROUTES, "pick a category the workflow lacks"
    when = "the defect is in how this story's work is packaged"

    with_fourth = json.loads(json.dumps(WORKFLOW))
    with_fourth["name"] = "four-categories"
    next(s for s in with_fourth["stages"]
         if "on_failure" in s)["on_failure"]["retry_routing"][added] = {
             "stage": WRITING, "when": when}

    four_harness = conftest.materialize_workflow(with_fourth,
                                                 tmp_path / "four-harness")
    root = build_target(tmp_path / "target-four", workflow=with_fourth["name"])

    assert story_coordinator.run_story(
        STORY_ID, four_harness, root, Runner(root)) == 0
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, Runner(target)) == 0

    # The same template on both sides: the fourth category was declared, not
    # written into a prompt.
    template_name = next(s["prompt"] for s in WORKFLOW["stages"]
                         if s["name"] == VERIFYING)
    assert (four_harness / "prompts" / template_name).read_bytes() == \
        (harness_root / "prompts" / template_name).read_bytes()

    with_four = prompt_of(root, VERIFYING, 1)
    with_three = prompt_of(target, VERIFYING, 1)
    assert any(added in line and WRITING in line and when in line
               for line in with_four.splitlines())
    assert added not in with_three


# --------------------------------------------------------------------------
# The clean-clone check now runs over a tree the documenter has edited
# --------------------------------------------------------------------------


def clone_evidence(tmp_path: Path, name: str) -> tuple[str, Path, Path]:
    """A test command that reports what the clean clone's own commit holds.

    Asserting on the check's exit code alone would say only that *a* suite
    passed somewhere. This command runs inside the clone, reads the
    architecture document out of the clone's `HEAD` commit — the state a
    test resolving a baseline out of git history actually sees — and records
    the clone's root beside it, so the evidence can be tied to the scratch
    clone the run's own record names.
    """
    doc = tmp_path / f"{name}-doc.txt"
    where = tmp_path / f"{name}-where.txt"
    command = (
        f"sh -c 'git show HEAD:{ARCHITECTURE_DOC} > {doc}; "
        f"git rev-parse --show-toplevel > {where}'"
    )
    return command, doc, where


def test_the_clean_clone_holds_the_documenters_edits(tmp_path, harness_root):
    command, doc, where = clone_evidence(tmp_path, "new-order")
    root = build_target(tmp_path / "clone-target", test_command=command)

    assert story_coordinator.run_story(
        STORY_ID, harness_root, root, Runner(root)) == 0

    record = json.loads((run_dir_of(root) / conftest.CLEAN_CLONE_RESULT)
                        .read_text(encoding="utf-8"))
    assert record["ran"] is True and record["exit_code"] == 0
    # The evidence came from the scratch clone this run's own record names,
    # not from some other checkout that happened to be lying around.
    assert Path(where.read_text(encoding="utf-8").strip()).resolve() == \
        Path(record["clone_path"]).resolve()
    # And that clone's commit holds what the documenter wrote.
    assert DOC_MARKER in doc.read_text(encoding="utf-8")


def test_under_the_previous_order_the_clean_clone_lacked_them(
    tmp_path, old_order_harness,
):
    """The control, and the gap this story closed: the same check over the
    same fixture under the previous order clones a tree the documenter has
    not touched yet."""
    command, doc, where = clone_evidence(tmp_path, "old-order")
    root = build_target(tmp_path / "old-clone-target",
                        workflow=PREVIOUS_WORKFLOW["name"],
                        test_command=command)

    assert story_coordinator.run_story(
        STORY_ID, old_order_harness, root, Runner(root)) == 0

    assert where.is_file(), "the check did not run at all"
    assert DOC_MARKER not in doc.read_text(encoding="utf-8")
    # The documenter did run — after the check, which is the whole point.
    assert DOC_MARKER in (root / ARCHITECTURE_DOC).read_text(encoding="utf-8")


def test_the_check_runs_after_the_documenter_and_before_the_run_completes(
    target, harness_root,
):
    runner = Runner(target)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0

    events = [(e["event"], e.get("stage")) for e in history_of(target)]
    documented = events.index(("stage-completed", DOCUMENTING))
    passed = events.index(("verification-passed", VERIFYING))
    clean = events.index(("clean-clone-passed", VERIFYING))
    completed = events.index(("story-completed", None))
    assert documented < passed < clean < completed


def test_under_the_previous_order_the_check_ran_before_the_documenter(
    tmp_path, old_order_harness,
):
    """The control for the ordering above, on the same event stream."""
    root = build_target(tmp_path / "old-events-target",
                        workflow=PREVIOUS_WORKFLOW["name"])
    assert story_coordinator.run_story(
        STORY_ID, old_order_harness, root, Runner(root)) == 0

    events = [(e["event"], e.get("stage")) for e in history_of(root)]
    assert events.index(("clean-clone-passed", VERIFYING)) < \
        events.index(("stage-completed", DOCUMENTING))


# --------------------------------------------------------------------------
# story-043, reconstructed
# --------------------------------------------------------------------------


def rejecting_command() -> str:
    """A suite that rejects one claim: a document naming a deleted module."""
    return (
        f"sh -c 'if grep -q {DELETED_MODULE} {ARCHITECTURE_DOC}; "
        f"then exit 1; fi'"
    )


def test_a_documented_claim_the_suite_rejects_now_ends_the_run(
    tmp_path, harness_root,
):
    """story-043's case: the documenter writes a sentence naming a tests/
    module the same story deleted. The clean-clone check now sees it, and
    the run escalates rather than completing."""
    root = build_target(tmp_path / "story-043-target",
                        test_command=rejecting_command())
    sentence = f"Validation for this lives in {DELETED_MODULE}."
    runner = Runner(root, documented=sentence)

    assert story_coordinator.run_story(
        STORY_ID, harness_root, root, runner) == 2

    run_dir = run_dir_of(root)
    assert not (run_dir / "completion-report.md").exists()
    assert state_of(root)["status"] == "escalated"
    record = json.loads((run_dir / conftest.CLEAN_CLONE_RESULT).read_text(
        encoding="utf-8"))
    assert record["ran"] is True and record["exit_code"] != 0
    # It ended on the check rather than on some other refusal: the run spent
    # its retries on the clean-clone route before escalating.
    assert state_of(root)["retry_count"] == MAX_RETRIES
    assert runner.calls.count(DOCUMENTING) == MAX_RETRIES + 1


def test_a_documented_claim_the_suite_accepts_completes_the_run(
    tmp_path, harness_root,
):
    """The control for the run above: the same fixture and the same suite,
    with the one sentence the suite rejects replaced by one it does not. A
    check that failed every run would be no check."""
    root = build_target(tmp_path / "accepted-doc-target",
                        test_command=rejecting_command())
    runner = Runner(root, documented="Validation for this lives in the suite.")

    assert story_coordinator.run_story(
        STORY_ID, harness_root, root, runner) == 0
    assert (run_dir_of(root) / "completion-report.md").is_file()


def test_under_the_previous_order_the_same_claim_completed_the_run(
    tmp_path, old_order_harness,
):
    """What story-043 shipped, reconstructed: the check passes minutes before
    the stage that breaks the suite, and the run completes red."""
    root = build_target(tmp_path / "story-043-old-target",
                        workflow=PREVIOUS_WORKFLOW["name"],
                        test_command=rejecting_command())
    sentence = f"Validation for this lives in {DELETED_MODULE}."
    runner = Runner(root, documented=sentence)

    assert story_coordinator.run_story(
        STORY_ID, old_order_harness, root, runner) == 0

    run_dir = run_dir_of(root)
    assert (run_dir / "completion-report.md").is_file()
    record = json.loads((run_dir / conftest.CLEAN_CLONE_RESULT).read_text(
        encoding="utf-8"))
    assert record["exit_code"] == 0
    # And the tree the completed run left behind is one that suite rejects.
    assert subprocess.run(
        ["sh", "-c", f"grep -q {DELETED_MODULE} {ARCHITECTURE_DOC}"],
        cwd=root).returncode == 0
