"""Independent validation for story-045: the documenter runs before the
verifier, and documentation is a retry category.

One reorder, three consequences, and this module holds all three:

  * **the order.** The workflow reads implementer -> tester -> documenter ->
    verifier, and a run invokes the stages in that order.
  * **the third route.** The verifier's routing table declares
    documentation -> documenter beside the two it already declared; the
    category reaches the verifier's prompt through the injection story-028
    landed rather than through any prose in `prompts/verifier.md`; and a
    failing verdict naming it re-enters at the documenter.
  * **what the reorder buys.** The verifier is handed the documenter's
    output, and the clean-clone check — which runs on the verifier's passing
    verdict — clones a tree that already holds the documenter's edits. That
    second one is story-043 reduced to a fixture: a documenter wrote a
    sentence naming a `tests/` module the same story deleted, the suite
    rejected it, and the run completed anyway because the check had already
    passed minutes before.

Almost nothing here is asserted from source. A target repository is built
under tmp_path, fake stage agents drive it into each shape, and what the
coordinator actually wrote — the execution history, the rendered prompts,
the clean clone's own committed tree, the run directory — is read back.

Every absence asserted here carries a demonstration that it can fail, and
for this story the demonstration has one natural shape: *the previous
behaviour*. A harness root carrying the old stage order is built beside the
shipped one and the same fixture is run against both, so each ordering
claim is shown red under the order this story replaced:

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

This story cannot verify itself: the coordinator loads the workflow at the
start of a run, so the run that lands the reorder executes under the old
order. Everything below is about the *definition on disk* and about runs
driven from it explicitly, neither of which depends on how this run itself
was sequenced.

Nothing here invokes a model: every run goes through a fake agent runner,
and `no_model` below turns the single subprocess call that would reach one
into a failure.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import agent_runner
import context_assembler
import harness_config
import story_coordinator
from agent_runner import AgentResult
from conftest import BASELINE, ENDPOINT, repository_file_at
import conftest

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]

WORKFLOW = conftest.shipped_workflow(REPO_ROOT, "story-workflow")
STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]

#: The stage that declares the routing table, found by the declaration
#: rather than by name.
VERIFIER_STAGE = next(s for s in WORKFLOW["stages"] if "on_failure" in s)
VERIFIER_NAME = VERIFIER_STAGE["name"]
ROUTES = VERIFIER_STAGE["on_failure"]["retry_routing"]

#: This module names the four stages and the three categories outright,
#: where `tests/test_retry_routing.py` deliberately does not. The difference
#: is the subject: that module validates *routing whatever the workflow
#: declares*, and a name written into it would let a coordinator that routes
#: to a constant pass. This module validates the story's own acceptance
#: criteria, which name the order and the categories, so a workflow that
#: quietly declares something else is exactly what it must report.
EXPECTED_ORDER = ["implementer", "tester", "documenter", "verifier"]
EXPECTED_CATEGORIES = ["documentation", "implementation", "validation"]
DOCUMENTATION = "documentation"
DOCUMENTER = "documenter"

#: The order this story replaced, derived from the new one so it stays the
#: previous order rather than a second list to maintain.
PREVIOUS_ORDER = ["implementer", "tester", "verifier", "documenter"]

VERIFIER_TEMPLATE_PATH = REPO_ROOT / "prompts" / f"{VERIFIER_NAME}.md"
VERIFIER_TEMPLATE = VERIFIER_TEMPLATE_PATH.read_text(encoding="utf-8")

RULES = harness_config.load_rules(REPO_ROOT)
MAX_RETRIES = RULES["max_retries"]

STORY_ID = "story-001"
DEFAULT_BRANCH = "main"
ARCHITECTURE_DOC = ".harness/docs/ARCHITECTURE.md"

#: The documenter's marker in the repository tree, and its marker in the
#: report it writes into the run directory. Two markers because the two
#: reach the verifier by different routes — one through the architecture
#: document the tree carries, one through {{documentation_report}} — and a
#: single marker could not tell them apart.
DOC_MARKER = "DOCUMENTER_WROTE_THIS"
#: Deliberately not a phrase the verifier's template itself uses, so its
#: presence in a rendered prompt is content that was injected rather than
#: the label the template prints above the placeholder.
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
        agent_runner.run_agent("prompt", stage=EXPECTED_ORDER[0], cwd=tmp_path,
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


def build_target(root: Path, *, workflow: str = "story-workflow",
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
          CONFIG.format(workflow=workflow, doc=ARCHITECTURE_DOC,
                        test_command=test_command))
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
def harness_root() -> Path:
    return REPO_ROOT


def probe_harness(tmp_path: Path, name: str, mutate) -> Path:
    """A harness root carrying a workflow this repository does not ship.

    Everything but the workflow is the shipped harness — the same prompts,
    the same schemas, the same rules — so a run against it differs from a
    run against this repository in exactly the definition under test. This
    is how the *previous* stage order is exercised: it is not a workflow
    anything ships any more, and only a run driven from it can show what the
    reorder changed.
    """
    root = tmp_path / name
    root.mkdir()
    for directory in ("prompts", "rules", "schemas"):
        shutil.copytree(REPO_ROOT / directory, root / directory)
    workflow = json.loads(json.dumps(WORKFLOW))
    workflow["name"] = name
    mutate(workflow)
    (root / "workflows").mkdir()
    write_json(root / "workflows" / f"{name}.json", workflow)
    return root


def reorder(workflow: dict, order: list[str]) -> None:
    """Put the workflow's stages in `order`, changing no declaration."""
    by_name = {stage["name"]: stage for stage in workflow["stages"]}
    workflow["stages"] = [by_name[name] for name in order]


def drop_documentation_route(workflow: dict) -> None:
    verifier = next(s for s in workflow["stages"] if s["name"] == VERIFIER_NAME)
    verifier["on_failure"]["retry_routing"].pop(DOCUMENTATION)


def previous_order(workflow: dict) -> None:
    """The workflow as it stood before this story: old order, two routes.

    Both halves, because they are inseparable — `retry_routing_problems`
    refuses the documentation route the moment the documenter sits after the
    verifier, so a workflow in the previous order that kept the route is not
    a workflow any run reaches.
    """
    reorder(workflow, PREVIOUS_ORDER)
    drop_documentation_route(workflow)


@pytest.fixture
def old_order_harness(tmp_path: Path) -> Path:
    return probe_harness(tmp_path, "previous-order", previous_order)


class Runner:
    """A fake agent runner: each stage writes the artifacts it declares.

    The implementer edits the repository tree and the documenter edits the
    architecture document, which is what gives the clean-clone check two
    distinguishable things to carry into its clone. `documented` is the
    sentence the documenter writes, so a test chooses whether the document
    makes a claim the suite accepts.

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
                 permission_mode=None, model=None, allowed_tools=None):
        self.calls.append(stage)
        if stage == "implementer":
            write(self.target_root / "src" / "app.py",
                  "print('hello')\n# the story's change\n")
            write_json(self.run_dir / "changed-files.json",
                       {"modified": ["src/app.py"], "created": [], "deleted": []})
            write(self.run_dir / "implementation-summary.md", "Did the work.\n")
        elif stage == "tester":
            write_json(self.run_dir / "test-results.json", {
                "status": "passed", "tests_written": 1, "tests_run": 1,
                "tests_passed": 1, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / "tester-changed-files.json", {
                "modified": [], "created": ["tests/test_app.py"], "deleted": [],
            })
        elif stage == "documenter":
            write(self.target_root / ARCHITECTURE_DOC,
                  f"# Architecture\n\nThe harness runs stages.\n"
                  f"{self.documented}\n")
            write(self.run_dir / "documentation-report.md",
                  f"# Documentation report\n\n{REPORT_MARKER}: "
                  f"{self.documented}\n")
            write_json(self.run_dir / "documenter-changed-files.json", {
                "modified": [ARCHITECTURE_DOC], "created": [], "deleted": [],
            })
        elif stage == VERIFIER_NAME:
            verdict = self.verdicts.pop(0) if len(self.verdicts) > 1 \
                else self.verdicts[0]
            write_json(self.run_dir / "verification-result.json", verdict)
            if verdict["status"] == "failed":
                write_json(self.run_dir / "retry-guidance.json", {
                    "current_focus": ["fix what the verdict named"],
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


def test_the_workflow_lists_the_stages_in_the_new_order():
    assert STAGE_NAMES == EXPECTED_ORDER


def test_a_run_invokes_the_stages_in_that_order(target, harness_root):
    runner = Runner(target)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0
    assert runner.calls == EXPECTED_ORDER


def test_a_run_under_the_previous_order_invokes_them_in_the_previous_order(
    tmp_path, old_order_harness,
):
    """The control for the two assertions above.

    The order is something the definition decides and the loop follows, so
    the same coordinator and the same fake runner produce the previous order
    when handed the previous definition. Without this, "the calls came in
    this order" would hold equally against a loop that ignored the
    definition and ran a sequence written into it.
    """
    root = build_target(tmp_path / "old-order-target", workflow="previous-order")
    runner = Runner(root)
    assert story_coordinator.run_story(
        STORY_ID, old_order_harness, root, runner) == 0
    assert runner.calls == PREVIOUS_ORDER
    assert runner.calls != EXPECTED_ORDER


def test_the_move_changed_no_stage_declaration(harness_root):
    """A reorder and nothing else: every stage's own declaration is what it
    was, apart from the one route the story adds.

    Read at both ends of this story's own commit range through the shared
    resolution, so it is a comparison of the definition before this story
    against the definition after it — not of the working tree against
    whatever HEAD happens to be.
    """
    before = json.loads(repository_file_at(
        "workflows/story-workflow.json", validation_file=Path(__file__),
        bound=BASELINE))
    after = json.loads(repository_file_at(
        "workflows/story-workflow.json", validation_file=Path(__file__),
        bound=ENDPOINT))

    old = {stage["name"]: stage for stage in before["stages"]}
    new = {stage["name"]: stage for stage in after["stages"]}
    assert sorted(old) == sorted(new)

    # The comparison is live: the two readings really are of different
    # orders, so the equality below is a statement about declarations rather
    # than about one file read twice.
    assert [s["name"] for s in before["stages"]] == PREVIOUS_ORDER
    assert [s["name"] for s in after["stages"]] == EXPECTED_ORDER

    for name in old:
        if name == VERIFIER_NAME:
            continue
        assert new[name] == old[name], name

    old_routes = old[VERIFIER_NAME]["on_failure"]["retry_routing"]
    new_routes = new[VERIFIER_NAME]["on_failure"]["retry_routing"]
    assert set(new_routes) - set(old_routes) == {DOCUMENTATION}
    for category, route in old_routes.items():
        assert new_routes[category] == route, category
    # Everything else the verifier declares — its prompt, its outputs, its
    # schemas, its clean-clone declaration, its self-route budget — is
    # untouched by the move.
    assert {key: value for key, value in new[VERIFIER_NAME].items()
            if key != "on_failure"} == \
        {key: value for key, value in old[VERIFIER_NAME].items()
         if key != "on_failure"}


# --------------------------------------------------------------------------
# The third route
# --------------------------------------------------------------------------


def test_the_verifier_declares_exactly_the_three_categories():
    assert sorted(ROUTES) == EXPECTED_CATEGORIES
    assert ROUTES[DOCUMENTATION]["stage"] == DOCUMENTER
    assert ROUTES[DOCUMENTATION]["when"].strip()


def test_the_documentation_when_tells_the_document_from_the_code_it_describes():
    """The `when` is what the verifier chooses by, so what it distinguishes
    is a property of this story rather than of any code path."""
    when = ROUTES[DOCUMENTATION]["when"].lower()
    # A defect in the document itself is the subject.
    assert "in the documentation itself" in when
    # And the case that belongs to the other category is stated outright,
    # naming that category.
    assert "accurately describes wrong behaviour" in when
    assert "implementation defect" in when
    assert "not a documentation one" in when


def test_the_two_existing_routes_are_preserved():
    assert ROUTES["implementation"]["stage"] == "implementer"
    assert ROUTES["validation"]["stage"] == "tester"


def test_the_reordered_workflow_declares_no_routing_problem():
    assert story_coordinator.retry_routing_problems(WORKFLOW["stages"]) == []


def test_restoring_the_previous_order_makes_the_documentation_route_a_problem():
    """The control for the absence above, and the reason the reorder and the
    route are one story: the route is legal only after the move."""
    workflow = json.loads(json.dumps(WORKFLOW))
    reorder(workflow, PREVIOUS_ORDER)

    problems = story_coordinator.retry_routing_problems(workflow["stages"])

    assert len(problems) == 1, problems
    assert DOCUMENTATION in problems[0]
    assert DOCUMENTER in problems[0]
    assert VERIFIER_NAME in problems[0]


def test_the_previous_order_with_the_route_is_refused_at_pre_flight(tmp_path):
    """End to end: no run directory, no branch, no agent invoked."""
    harness = probe_harness(tmp_path, "old-order-with-route",
                            lambda workflow: reorder(workflow, PREVIOUS_ORDER))
    root = build_target(tmp_path / "refused-target",
                        workflow="old-order-with-route")
    runner = Runner(root)

    assert story_coordinator.run_story(STORY_ID, harness, root, runner) == 1

    assert runner.calls == []
    assert not run_dir_of(root).exists()
    assert git(root, "branch", "--list", f"story/{STORY_ID}").stdout.strip() == ""


def test_the_same_workflow_without_the_route_is_not_refused(
    tmp_path, old_order_harness,
):
    """The control for the refusal above: what is refused is the route, not
    the probe harness or the old order."""
    root = build_target(tmp_path / "accepted-target", workflow="previous-order")
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
    runner = Runner(target, [failing(DOCUMENTATION), PASS])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0

    assert runner.calls == EXPECTED_ORDER + [DOCUMENTER, VERIFIER_NAME]
    # The stages before the destination are not re-invoked on the way back.
    assert runner.calls.count("implementer") == 1
    assert runner.calls.count("tester") == 1
    assert runner.calls.count(DOCUMENTER) == 2
    # The retried documenter is told it is on a retry, and by which category.
    retried = prompt_of(target, DOCUMENTER, 2)
    assert DOCUMENTATION in retried
    assert context_assembler.PLACEHOLDER.search(retried) is None


@pytest.mark.parametrize("category", EXPECTED_CATEGORIES)
def test_the_history_records_every_category_the_same_way(
    target, harness_root, category,
):
    """The documentation category is recorded exactly as the other two are:
    same event, same fields, same shape — only the values differ."""
    destination = ROUTES[category]["stage"]
    runner = Runner(target, [failing(category), PASS])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0

    entry = next(e for e in history_of(target)
                 if e["event"] == "verification-failed")
    assert entry["retry_category"] == category
    assert entry["retry_stage"] == destination
    assert entry["retry_decision"] == "retry"
    assert entry["stage"] == VERIFIER_NAME
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

    assert runner.calls.count(DOCUMENTER) == 1
    started = [e for e in history_of(target)
               if e["event"] == "stage-started" and e["stage"] == DOCUMENTER]
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
    prompt = prompt_of(target, VERIFIER_NAME, 1)

    report = (run_dir / "documentation-report.md").read_text(encoding="utf-8")
    record = (run_dir / "documenter-changed-files.json").read_text(
        encoding="utf-8")
    assert report.strip() in prompt
    assert record.strip() in prompt
    assert REPORT_MARKER in prompt
    assert ARCHITECTURE_DOC in prompt
    assert context_assembler.PLACEHOLDER.search(prompt) is None


def test_the_prompt_says_none_when_the_documenter_wrote_nothing(target, tmp_path):
    """The control for the assertion above.

    The same template rendered against a run directory holding neither
    artifact resolves both placeholders to the optional-placeholder None, so
    what the run's prompt carried is content that was injected rather than
    prose the template always had.
    """
    empty = tmp_path / "empty-run-dir"
    (empty / "verification").mkdir(parents=True)

    context = context_assembler.build_context(
        story_text=STORY,
        story=story_coordinator.read_story(STORY).parsed,
        run_dir=empty,
        target_root=target,
        harness_root=REPO_ROOT,
        config=harness_config.load_config(target),
        rules=RULES,
        workflow=WORKFLOW,
        retry_count=0,
    )
    rendered = context_assembler.render(VERIFIER_TEMPLATE, context)

    assert context["documentation_report"] is None
    assert context["documenter_changed_files"] is None
    assert REPORT_MARKER not in rendered
    assert context_assembler.PLACEHOLDER.search(rendered) is None


def test_the_template_declares_both_placeholders():
    assert "{{documentation_report}}" in VERIFIER_TEMPLATE
    assert "{{documenter_changed_files}}" in VERIFIER_TEMPLATE


def test_the_role_layer_says_the_documenters_output_is_part_of_the_subject():
    """Positive: the template must say it, so it fails on its own if the
    sentence is dropped."""
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
    runner = Runner(target)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0
    prompt = prompt_of(target, VERIFIER_NAME, 1)

    for category, route in ROUTES.items():
        line = next((line for line in prompt.splitlines()
                     if category in line and route["stage"] in line), None)
        assert line is not None, category
        assert route["when"] in line


def test_the_template_restates_no_category_destination_or_when(
    target, harness_root,
):
    """The routes reach the verifier by injection, not by prose.

    The absence is asserted against the *rendered* prompt as its control:
    every pairing and every `when` this test says the template lacks is
    shown present once the same template has been rendered, so a check
    looking at the wrong text would report the pairing missing from both.
    """
    runner = Runner(target)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0
    prompt = prompt_of(target, VERIFIER_NAME, 1)

    for category, route in ROUTES.items():
        pairing = f"{category} -> {route['stage']}"
        assert pairing not in VERIFIER_TEMPLATE, category
        assert pairing in prompt, category
        assert route["when"] not in VERIFIER_TEMPLATE, category
        assert route["when"] in prompt, category


def test_a_fourth_category_reaches_the_prompt_with_no_edit_to_the_template(
    tmp_path, target, harness_root,
):
    """The property story-028 landed, of which this story's third category is
    the first real test — asserted here against a fourth, so the injection is
    shown to be general rather than to have been widened by hand to three."""
    added = "packaging"
    assert added not in ROUTES, "pick a category the shipped workflow lacks"
    when = "the defect is in how this story's work is packaged"

    def mutate(workflow: dict) -> None:
        verifier = next(s for s in workflow["stages"]
                        if s["name"] == VERIFIER_NAME)
        verifier["on_failure"]["retry_routing"][added] = {
            "stage": EXPECTED_ORDER[0], "when": when,
        }

    harness = probe_harness(tmp_path, "four-categories", mutate)
    root = build_target(tmp_path / "target-four", workflow="four-categories")

    assert story_coordinator.run_story(
        STORY_ID, harness, root, Runner(root)) == 0
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, Runner(target)) == 0

    assert (harness / "prompts" / f"{VERIFIER_NAME}.md").read_bytes() == \
        VERIFIER_TEMPLATE_PATH.read_bytes()

    with_four = prompt_of(root, VERIFIER_NAME, 1)
    with_three = prompt_of(target, VERIFIER_NAME, 1)
    assert any(added in line and EXPECTED_ORDER[0] in line and when in line
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

    record = json.loads((run_dir_of(root) / "clean-clone-result.json")
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
    root = build_target(tmp_path / "old-clone-target", workflow="previous-order",
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
    documented = events.index(("stage-completed", DOCUMENTER))
    passed = events.index(("verification-passed", VERIFIER_NAME))
    clean = events.index(("clean-clone-passed", VERIFIER_NAME))
    completed = events.index(("story-completed", None))
    assert documented < passed < clean < completed


def test_under_the_previous_order_the_check_ran_before_the_documenter(
    tmp_path, old_order_harness,
):
    """The control for the ordering above, on the same event stream."""
    root = build_target(tmp_path / "old-events-target", workflow="previous-order")
    assert story_coordinator.run_story(
        STORY_ID, old_order_harness, root, Runner(root)) == 0

    events = [(e["event"], e.get("stage")) for e in history_of(root)]
    assert events.index(("clean-clone-passed", VERIFIER_NAME)) < \
        events.index(("stage-completed", DOCUMENTER))


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
    record = json.loads((run_dir / "clean-clone-result.json").read_text(
        encoding="utf-8"))
    assert record["ran"] is True and record["exit_code"] != 0
    # It ended on the check rather than on some other refusal: the run spent
    # its retries on the clean-clone route before escalating.
    assert state_of(root)["retry_count"] == MAX_RETRIES
    assert runner.calls.count(DOCUMENTER) == MAX_RETRIES + 1


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
                        workflow="previous-order",
                        test_command=rejecting_command())
    sentence = f"Validation for this lives in {DELETED_MODULE}."
    runner = Runner(root, documented=sentence)

    assert story_coordinator.run_story(
        STORY_ID, old_order_harness, root, runner) == 0

    run_dir = run_dir_of(root)
    assert (run_dir / "completion-report.md").is_file()
    record = json.loads((run_dir / "clean-clone-result.json").read_text(
        encoding="utf-8"))
    assert record["exit_code"] == 0
    # And the tree the completed run left behind is one that suite rejects.
    assert subprocess.run(
        ["sh", "-c", f"grep -q {DELETED_MODULE} {ARCHITECTURE_DOC}"],
        cwd=root).returncode == 0
