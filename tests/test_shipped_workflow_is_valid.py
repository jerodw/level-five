"""The assertions whose subject really is the workflow this repository ships.

Every other module under `tests/` that used to load `workflows/story-workflow.json`
was testing a *mechanism* — does the coordinator self-route, route a retry,
refuse a malformed declaration, enforce a boundary — and reached for the live
artifact only to avoid writing a stage name or an artifact name into the test.
story-048 converted those to workflows they build for themselves, because
deriving a mechanism's names from what this repository happens to deploy makes a
deployment fact into something the suite enforces: story-047 granted one stage a
`max_self_routes` budget, a correct one-line change, and reddened four
assertions in a module with nothing to say about whether that grant was right.

This module is the other side of that split, and it exists so a displaced
configuration assertion has somewhere to go rather than being deleted. Here the
shipped definition *is* the subject: whether this deployment's workflow is
well-formed, and whether it says what this project intends of it. An assertion
that goes red here when the workflow changes has done its job — that is the
question it is asking.

Three validators decide well-formedness, and the coordinator runs all three at
pre-flight, before a run spends a stage on a definition that cannot work:

  * `self_route_problems` — every declared self-route budget is a count;
  * `retry_routing_problems` — every declared route names a stage the workflow
    defines, and one that sits before the stage declaring the route;
  * `stage_exception_problems` — a story's stage exceptions mean something
    against the workflow the run loaded.

Each is asserted clean against what this repository ships *and* shown to report
a definition that violates it, because "the shipped workflow has no problems"
is an absence assertion: it passes just as happily against a validator that has
stopped looking. The violating definitions come from the builder in
`tests/conftest.py`, never from a mutated copy of the shipped one, so a control
here is a statement about the validator rather than about today's deployment.
"""
import ast
import builtins
import inspect
import json
import os
from pathlib import Path

import pytest

import context_assembler
import harness_config
import schema_validator
import story_coordinator

import conftest
from conftest import (StageRef, build_workflow, materialize_workflow,
                      shipped_workflow, workflow_stage)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The definition this repository deploys, loaded the way a run loads it —
#: against this repository's own configuration, so `{{tests_dir}}` in the
#: implementer's create restriction resolves to the value a run would enforce
#: rather than staying a token. Reading it here is the point of the module.
SHIPPED = shipped_workflow(REPO_ROOT, "story-workflow")
SHIPPED_STAGES = SHIPPED["stages"]
SHIPPED_NAMES = [stage["name"] for stage in SHIPPED_STAGES]

#: The story artifact the exception cross-check is run against. This
#: repository's own stories are the honest input to that check: a stage
#: exception is a planning decision made against this deployment's stage list,
#: so a grant that means nothing here is a defect in this repository.
STORIES_DIR = REPO_ROOT / ".harness" / "stories"


def _executable_source(text: str) -> str:
    """`text` with docstrings and comment lines removed.

    Prose may name what code may not, so the name-absence assertions below read
    what executes rather than what is written above it. Carried over with the
    assertion it serves, from the modules story-048 converted.
    """
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


# --------------------------------------------------------------------------
# The builder resolves nothing this repository ships
#
# Asserted by running it rather than by reading its source. A scan of
# `build_workflow`'s text for the absence of a `read_text` would pass against a
# builder that reached the shipped definition through a helper, and would keep
# passing after any rename. So every route to the filesystem is closed and the
# builder is asked to work anyway.
# --------------------------------------------------------------------------


@pytest.fixture
def no_filesystem(monkeypatch):
    """Every route to a file, closed. What still works read nothing."""
    def refuse(*args, **kwargs):
        raise AssertionError(f"this read a file: {args[:1]}")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(os, "open", refuse)
    monkeypatch.setattr(Path, "open", refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    monkeypatch.setattr(Path, "read_bytes", refuse)


def test_the_builder_assembles_a_workflow_without_reading_anything(no_filesystem):
    """The property the whole conversion rests on, demonstrated.

    A builder that quietly reached for the shipped definition would give every
    converted module the coupling the conversion removed, and would do it
    invisibly — the definition would still look built.
    """
    built = build_workflow(
        workflow_stage(outputs=("artifact.json",), changed_files="artifact.json"),
        workflow_stage(name=conftest.VERIFYING_STAGE),
    )
    assert [stage["name"] for stage in built["stages"]][-1] == \
        conftest.VERIFYING_STAGE
    assert built["stages"][0]["outputs"] == ["artifact.json"]


def test_the_no_filesystem_guard_stops_a_read_of_the_shipped_workflow(no_filesystem):
    """The control for the assertion above.

    Closing the filesystem and observing that a function still works says
    nothing unless the closure is shown to stop a function that does read. The
    helper the conversion moved *away* from is exactly that function.
    """
    with pytest.raises(AssertionError, match="this read a file"):
        shipped_workflow(REPO_ROOT, "story-workflow")


# --------------------------------------------------------------------------
# The three validators, against what this repository ships
# --------------------------------------------------------------------------


def test_the_shipped_workflow_declares_only_budgets_that_are_counts():
    assert story_coordinator.self_route_problems(SHIPPED_STAGES) == []


def test_the_shipped_workflow_routes_every_retry_backwards_to_a_stage_it_defines():
    assert story_coordinator.retry_routing_problems(SHIPPED_STAGES) == []


def test_every_stage_exception_this_repository_states_means_something():
    """Each of this repository's own stories, cross-checked against its own
    workflow. A grant naming a stage this deployment does not define, or a path
    its stage was never restricted on, grants nothing — and the run that
    discovers it is refused at pre-flight."""
    stories = sorted(STORIES_DIR.glob("*.yaml"))
    assert stories, "no story artifact was read, so this asserts nothing"
    granted = 0
    for story_path in stories:
        story = story_coordinator.read_story(
            story_path.read_text(encoding="utf-8"), REPO_ROOT).parsed
        granted += len(story.get("stage_exceptions", []))
        assert story_coordinator.stage_exception_problems(
            story, SHIPPED_STAGES) == [], story_path.name
    # The companion the loop needs: a corpus in which no story grants anything
    # satisfies the assertion above without exercising the check once.
    assert granted, "no story in this repository states a stage exception"


# --------------------------------------------------------------------------
# ... and each validator shown to report the definition that violates it
# --------------------------------------------------------------------------


@pytest.mark.parametrize("budget", [-1, True, "1", 1.0],
                         ids=["negative", "a-bool", "a-string", "a-float"])
def test_self_route_problems_reports_a_budget_that_is_not_a_count(budget):
    stages = build_workflow(workflow_stage(max_self_routes=budget))["stages"]
    problems = story_coordinator.self_route_problems(stages)
    assert len(problems) == 1, problems
    assert stages[0]["name"] in problems[0]


def test_self_route_problems_accepts_a_stage_that_declares_no_budget():
    """The companion the parametrization needs: a validator that reported
    everything would satisfy the four cases above."""
    stages = build_workflow(workflow_stage(),
                            workflow_stage(max_self_routes=0))["stages"]
    assert "max_self_routes" not in stages[0]
    assert story_coordinator.self_route_problems(stages) == []


def test_retry_routing_problems_reports_a_route_to_a_stage_that_is_not_defined():
    stages = build_workflow(
        workflow_stage(),
        workflow_stage(retry_routing={"a-category": {"stage": "no-such-stage",
                                                     "when": "never"}}),
    )["stages"]
    problems = story_coordinator.retry_routing_problems(stages)
    assert len(problems) == 1, problems
    assert "does not define" in problems[0]


def test_retry_routing_problems_reports_a_route_that_points_forward():
    """A route at or after the stage declaring it would carry the run past the
    verification that sent it back."""
    stages = build_workflow(
        workflow_stage(retry_routing={"a-category": {"stage": StageRef(1),
                                                     "when": "never"}}),
        workflow_stage(),
    )["stages"]
    problems = story_coordinator.retry_routing_problems(stages)
    assert len(problems) == 1, problems
    assert "does not sit before it" in problems[0]


def test_retry_routing_problems_reports_a_clean_clone_route_on_the_same_terms():
    """The clean-clone route is not one of the categories the verifier chooses
    between, and is held to the same two checks anyway."""
    stages = build_workflow(
        workflow_stage(),
        workflow_stage(clean_clone={"result": "result.json",
                                    "retry_stage": "no-such-stage"}),
    )["stages"]
    problems = story_coordinator.retry_routing_problems(stages)
    assert len(problems) == 1, problems
    assert "clean_clone" in problems[0]


def test_retry_routing_problems_accepts_a_route_that_points_backwards():
    stages = build_workflow(
        workflow_stage(),
        workflow_stage(retry_routing={"a-category": {"stage": StageRef(0),
                                                     "when": "always"}},
                       clean_clone={"result": "result.json",
                                    "retry_stage": StageRef(0)}),
    )["stages"]
    assert story_coordinator.retry_routing_problems(stages) == []


def test_stage_exception_problems_reports_a_grant_naming_an_undefined_stage():
    stages = build_workflow(workflow_stage(may_not_create=("guarded/",)))["stages"]
    story = {"stage_exceptions": [{"stage": "no-such-stage",
                                   "create": "guarded/",
                                   "reason": "because"}]}
    problems = story_coordinator.stage_exception_problems(story, stages)
    assert len(problems) == 1, problems
    assert "does not define" in problems[0]


def test_stage_exception_problems_reports_a_grant_the_stage_was_never_restricted_on():
    stages = build_workflow(workflow_stage(may_not_create=("guarded/",)))["stages"]
    story = {"stage_exceptions": [{"stage": stages[0]["name"],
                                   "create": "elsewhere/",
                                   "reason": "because"}]}
    problems = story_coordinator.stage_exception_problems(story, stages)
    assert len(problems) == 1, problems
    assert "never restricted" in problems[0]


def test_stage_exception_problems_accepts_a_grant_under_a_declared_prefix():
    stages = build_workflow(workflow_stage(may_not_create=("guarded/",)))["stages"]
    for granted in ("guarded/", "guarded/one-file.py", "guarded/deeper/"):
        story = {"stage_exceptions": [{"stage": stages[0]["name"],
                                       "create": granted, "reason": "because"}]}
        assert story_coordinator.stage_exception_problems(story, stages) == [], \
            granted


# --------------------------------------------------------------------------
# What this project intends of the definition it deploys
#
# Not well-formedness — a workflow can be well-formed and still be the wrong
# workflow for this project. These are the deployment decisions, stated where a
# change to them is *supposed* to go red.
# --------------------------------------------------------------------------


def test_the_shipped_workflow_is_the_one_this_repository_configures():
    """The definition asserted about below is the one a run of this repository
    would load, rather than a file that happens to sit beside it."""
    configured = conftest.repository_config().get("workflow", "story-workflow")
    assert SHIPPED["name"] == configured
    assert (REPO_ROOT / "workflows" / f"{configured}.json").is_file()


def test_the_shipped_workflow_runs_the_four_stages_this_project_intends():
    """The stage list, in order. This project separates writing the code from
    writing its validation, documents before it verifies so the documentation
    is judged with everything else, and verifies last."""
    assert SHIPPED_NAMES == ["implementer", "tester", "documenter", "verifier"]
    assert SHIPPED_NAMES[-1] == conftest.VERIFYING_STAGE


def test_the_verifying_stage_is_the_name_the_coordinator_keys_on():
    """A harness fact rather than a deployment one, and the reason a built
    workflow whose run must reach a verdict names its last stage this. Asserted
    against the coordinator's own source, so a rename there fails here rather
    than silently making every built workflow unverifiable."""
    source = Path(story_coordinator.__file__).read_text(encoding="utf-8")
    assert f'"{conftest.VERIFYING_STAGE}"' in source
    assert conftest.VERIFYING_STAGE in SHIPPED_NAMES


def test_the_implementer_may_not_create_the_configured_tests_directory():
    """The separation the two-stage split exists for, and the one declaration in
    this workflow that references configuration: the restriction is written as
    `{{tests_dir}}` and resolves to what this repository configures."""
    restrictions = story_coordinator.stage_restrictions(SHIPPED_STAGES)
    tests_dir = conftest.repository_config()["tests_dir"]
    assert restrictions == [(SHIPPED_NAMES[0], tests_dir)]
    # The token, not the value, is what the file says.
    raw = json.loads((REPO_ROOT / "workflows" / "story-workflow.json")
                     .read_text(encoding="utf-8"))
    assert raw["stages"][0]["may_not_create"] == ["{{tests_dir}}"]


def test_every_stage_that_writes_a_changed_files_record_declares_it_as_an_output():
    """A record the coordinator reads but the stage never declared would be
    unchecked for freshness."""
    for stage in SHIPPED_STAGES:
        record = stage.get("changed_files")
        if record is not None:
            assert record in stage.get("outputs", []), stage["name"]


def test_every_schema_a_stage_names_exists_in_this_repository():
    for stage in SHIPPED_STAGES:
        for artifact, schema_name in stage.get("schemas", {}).items():
            assert (REPO_ROOT / "schemas" / f"{schema_name}.schema.json").is_file(), \
                (stage["name"], artifact, schema_name)


def test_every_stage_has_a_prompt_template_this_repository_ships():
    for stage in SHIPPED_STAGES:
        assert (REPO_ROOT / "prompts" / stage["prompt"]).is_file(), stage["name"]


def test_every_retry_category_this_deployment_defines_carries_a_when_clause():
    """A category with no `when` gives the verifier nothing to choose on."""
    routes = list(context_assembler.retry_routes(SHIPPED_STAGES))
    assert routes
    for route in routes:
        assert route.when.strip(), route.category


def test_the_budgets_this_deployment_grants_are_recorded_where_a_reader_meets_them():
    """A budget is a judgement about how a stage fails, and this deployment
    records the reasoning beside the grant rather than in a commit message.

    Deliberately shaped as "every budget that differs from the common one says
    why" rather than as a count of budgeted stages: granting one more stage the
    common budget is the change story-047 made, and it belongs here as a
    passing change rather than as a red one.
    """
    budgets = {stage["name"]: stage["max_self_routes"]
               for stage in SHIPPED_STAGES if "max_self_routes" in stage}
    assert budgets, "this deployment grants no self-route budget at all"
    common = min(budgets.values())
    for stage in SHIPPED_STAGES:
        if stage.get("max_self_routes", common) != common:
            assert stage.get("max_self_routes_reason", "").strip(), stage["name"]


def test_a_recorded_reason_states_the_number_it_is_explaining():
    """Displaced from tests/test_self_routing_retry.py, whose subject became
    how a budgeted stage and a budget-less one behave once it built its own
    workflow.

    A reason that never mentions the budget it justifies would satisfy the
    check above while explaining nothing, so the shipped reasons are read.
    Digits or the English word, because a sentence about a budget of two reads
    better as "two" and either spelling states the number.
    """
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
    declared = [stage for stage in SHIPPED_STAGES if "max_self_routes" in stage]
    assert declared
    common = min(stage["max_self_routes"] for stage in declared)
    outliers = 0
    for stage in declared:
        if stage["max_self_routes"] == common:
            continue
        outliers += 1
        reason = stage["max_self_routes_reason"].lower()
        budget = stage["max_self_routes"]
        assert str(budget) in reason or words.get(budget, "\0") in reason, \
            stage["name"]
    # The companion the loop needs: a deployment budgeting every stage alike
    # satisfies the loop above without reading one reason.
    assert outliers, "this deployment declares no budget that differs from the " \
                     "common one, so no recorded reason was read"


def test_this_deployment_escalates_when_the_retry_ceiling_is_reached():
    assert SHIPPED["escalation_rules"]["max_retries_exceeded"]["action"] \
        == "escalate"


def test_the_retry_ceiling_is_a_count_this_repository_states_once():
    rules = harness_config.load_rules(REPO_ROOT)
    assert isinstance(rules["max_retries"], int)
    assert rules["max_retries"] >= 1


# --------------------------------------------------------------------------
# A built definition drives a real run
#
# The converted modules rest on this: a workflow the builder produced,
# materialized into a harness root, exercises the same code path a module
# reading the shipped definition exercised. Asserted here once, so a converted
# module can take it as given.
# --------------------------------------------------------------------------


def test_a_built_workflow_materializes_into_a_root_a_run_can_load(tmp_path):
    built = build_workflow(
        workflow_stage(outputs=("record.json",), changed_files="record.json",
                       schemas={"record.json": "changed-files"}),
        workflow_stage(name=conftest.VERIFYING_STAGE,
                       outputs=(conftest.VERIFICATION_RESULT,),
                       retry_routing={"a-category": {"stage": StageRef(0),
                                                     "when": "always"}}),
        name="a-built-workflow",
    )
    root = materialize_workflow(built, tmp_path / "harness")

    loaded = harness_config.load_workflow(root, built["name"],
                                          conftest.repository_config())
    assert loaded == built
    assert story_coordinator.self_route_problems(loaded["stages"]) == []
    assert story_coordinator.retry_routing_problems(loaded["stages"]) == []
    for stage in loaded["stages"]:
        assert (root / "prompts" / stage["prompt"]).is_file()
    assert (root / "schemas" / "changed-files.schema.json").is_file()
    assert (root / "rules" / "execution-rules.json").is_file()


def test_a_built_workflow_carries_only_what_it_was_asked_for():
    """Every key absent unless asked for, which is what lets a module build a
    case this repository does not deploy — a stage with no budget is a
    different thing from one declaring zero, and the coordinator treats the two
    differently."""
    bare = build_workflow(workflow_stage())["stages"][0]
    assert set(bare) == {"name", "prompt"}
    assert "escalation_rules" not in build_workflow(workflow_stage())

    asked = build_workflow(
        workflow_stage(outputs=("a.json",), changed_files="a.json",
                       may_not_create=("guarded/",), max_self_routes=3,
                       revert_check={"result": "r.json", "baseline": "b"},
                       schemas={"a.json": "changed-files"}),
        workflow_stage(retry_routing={"c": {"stage": StageRef(0), "when": "w"}},
                       clean_clone={"result": "cc.json",
                                    "retry_stage": StageRef(0)}),
        escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    )
    first, second = asked["stages"]
    assert first["may_not_create"] == ["guarded/"]
    assert first["max_self_routes"] == 3
    assert first["revert_check"] == {"result": "r.json", "baseline": "b"}
    assert second["on_failure"]["retry_routing"]["c"]["stage"] == first["name"]
    assert second["clean_clone"]["retry_stage"] == first["name"]
    assert asked["escalation_rules"]["max_retries_exceeded"]["action"] == "escalate"


def test_a_route_pointing_at_no_stage_is_refused_by_the_builder():
    """The builder's own control: a `StageRef` past the end of the workflow is
    a test's mistake, and is refused rather than substituted with something."""
    with pytest.raises(AssertionError, match="names nothing"):
        build_workflow(workflow_stage(
            retry_routing={"c": {"stage": StageRef(4), "when": "w"}}))


def test_two_stages_may_not_share_a_name():
    with pytest.raises(AssertionError, match="share a name"):
        build_workflow(workflow_stage(name="same"), workflow_stage(name="same"))


# --------------------------------------------------------------------------
# The property story-048 exists for
# --------------------------------------------------------------------------


def test_a_workflow_change_moves_this_module_and_not_a_mechanism_module(tmp_path):
    """The demonstration, run rather than argued.

    A built workflow is mutated the three ways a deployment change mutates one
    — a stage granted a budget, a stage added, a route retitled — and each time
    the *configuration* questions this module asks give a different answer while
    the *mechanism* questions a converted module asks give the same one. That
    asymmetry is the whole point of the conversion: the first kind of assertion
    is supposed to move, and the second is not.
    """
    def mechanism_answer(stages):
        """What a converted module asks: does routing work, derived from the
        definition in front of it rather than from a name written down."""
        return (story_coordinator.self_route_problems(stages),
                story_coordinator.retry_routing_problems(stages),
                [route.stage for route in context_assembler.retry_routes(stages)]
                == [stages[0]["name"]])

    def configuration_answer(stages):
        """What this module asks: what does *this* deployment declare."""
        return ([stage["name"] for stage in stages],
                {stage["name"]: stage.get("max_self_routes")
                 for stage in stages},
                sorted(context_assembler.retry_routes(stages),
                       key=lambda route: route.category))

    base = build_workflow(
        workflow_stage(),
        workflow_stage(name=conftest.VERIFYING_STAGE,
                       retry_routing={"a-category": {"stage": StageRef(0),
                                                     "when": "always"}}),
        name="base-workflow")
    baseline_mechanism = mechanism_answer(base["stages"])
    baseline_configuration = configuration_answer(base["stages"])

    budgeted = build_workflow(
        workflow_stage(max_self_routes=1),
        workflow_stage(name=conftest.VERIFYING_STAGE,
                       retry_routing={"a-category": {"stage": StageRef(0),
                                                     "when": "always"}}),
        name="budgeted-workflow")
    added = build_workflow(
        workflow_stage(),
        workflow_stage(),
        workflow_stage(name=conftest.VERIFYING_STAGE,
                       retry_routing={"a-category": {"stage": StageRef(0),
                                                     "when": "always"}}),
        name="added-stage-workflow")
    retitled = build_workflow(
        workflow_stage(),
        workflow_stage(name=conftest.VERIFYING_STAGE,
                       retry_routing={"another-category": {"stage": StageRef(0),
                                                           "when": "always"}}),
        name="retitled-route-workflow")

    for changed in (budgeted, added, retitled):
        root = materialize_workflow(changed, tmp_path / changed["name"])
        stages = harness_config.load_workflow(
            root, changed["name"], conftest.repository_config())["stages"]
        assert mechanism_answer(stages) == baseline_mechanism, changed["name"]
        assert configuration_answer(stages) != baseline_configuration, \
            changed["name"]


# --------------------------------------------------------------------------
# Configuration assertions displaced from the modules story-048 converted
#
# Each one asked a question about what *this* repository deploys and happened to
# be sitting in a module whose other assertions were about a mechanism. Moving
# the module to a built workflow would have made these vacuous — a built
# workflow says nothing about the deployment — so they were moved here rather
# than dropped. The module each came from is named beside it.
# --------------------------------------------------------------------------


def budget_problems(stages):
    """One problem per stage of `stages` that declares no self-route budget,
    and one per declared budget that is not an integer count.

    Written as a function rather than inline in the assertion below so the
    control beside it can put the same code to a definition that violates the
    claim. An assertion spelled out inside its own test can only ever be shown
    to pass; a control that restates it in different words is a second
    assertion agreeing with the first rather than a demonstration that either
    can fail.
    """
    problems = []
    for stage in stages:
        if "max_self_routes" not in stage:
            problems.append(f"{stage['name']} declares no max_self_routes")
        elif not isinstance(stage["max_self_routes"], int) \
                or isinstance(stage["max_self_routes"], bool):
            problems.append(
                f"{stage['name']} declares a max_self_routes that is not a "
                f"count: {stage['max_self_routes']!r}")
    return problems


def test_every_stage_of_this_deployment_declares_a_self_route_budget():
    """From tests/test_self_routing_retry.py, whose subject became "how a
    budgeted stage and a budget-less one behave" once story-048 split
    configuration from mechanism and it built its own workflow.

    It arrived here reading the population this deployment no longer has: that
    the shipped workflow budgets several stages and leaves at least one
    unbudgeted. story-060 granted the documenter a budget, which was the last
    stage declaring none, and the assertion states the decision that replaced
    that fact — every stage of this workflow declares a budget, and every
    declared budget is a count. It is not preserved as a deployment fact
    because it never needed to be one: the compatibility property it was
    protecting, that a stage declaring no budget escalates on a mechanical
    failure exactly as it did before story-036, is held in
    tests/test_self_routing_retry.py against a budget-less stage that module
    builds for itself.
    """
    assert SHIPPED_STAGES, "no stage was read, so this asserts nothing"
    assert budget_problems(SHIPPED_STAGES) == []


def test_budget_problems_reports_a_definition_that_leaves_a_stage_unbudgeted():
    """The control for the assertion above.

    "Every shipped stage declares a budget" is an absence assertion: it passes
    when the property holds, and it passes just as happily against a check that
    has stopped looking -- one whose loop never runs, or whose membership test
    can no longer be false. So the same function is put to a definition that
    does leave a stage unbudgeted, which is the deployment this repository had
    until story-060 granted the documenter its budget.

    The definition is built by `tests/conftest.py`'s builder rather than by
    mutating the shipped one: a control that deleted a key from what this
    repository deploys would restate today's deployment, and would stop
    building its violation the day the stage it reached for was renamed. The
    builder omits `max_self_routes` unless asked for it, so a budget-less stage
    is what it produces by default.
    """
    stages = build_workflow(workflow_stage(max_self_routes=1),
                            workflow_stage())["stages"]
    unbudgeted = stages[1]["name"]
    assert "max_self_routes" not in stages[1]

    problems = budget_problems(stages)
    assert len(problems) == 1, problems
    assert unbudgeted in problems[0]

    # And the companion that control needs in turn: a check reporting every
    # stage it is handed would satisfy the assertion above just as well.
    budgeted = build_workflow(workflow_stage(max_self_routes=1),
                              workflow_stage(max_self_routes=0))["stages"]
    assert budget_problems(budgeted) == []


@pytest.mark.parametrize("budget", [True, "1", 1.0, None],
                         ids=["a-bool", "a-string", "a-float", "a-null"])
def test_budget_problems_reports_a_declared_budget_that_is_not_a_count(budget):
    """The other half of what the assertion above claims of this deployment --
    that every declared budget is an integer count -- shown to be able to fail.

    `True` is in the cases because `isinstance(True, int)` holds, so a check
    written as a bare integer test would accept a declaration of `true` and
    call it a budget. `None` is here because a stage carrying the key with
    nothing in it is not the same thing as a stage carrying no key, and only
    the second is what this deployment stopped having.
    """
    stages = build_workflow(workflow_stage())["stages"]
    # Assigned after building rather than asked of the builder, because the
    # builder reads `None` as "not asked for" and would produce no key at all,
    # which is the other violation and is covered above.
    stages[0]["max_self_routes"] = budget

    problems = budget_problems(stages)
    assert len(problems) == 1, problems
    assert stages[0]["name"] in problems[0]


def reason_convention_problems(stages):
    """One problem per stage recording a reason for a budget that does not
    differ from the common one.

    The convention `test_the_budgets_this_deployment_grants_are_recorded_where_
    a_reader_meets_them` states from one side -- every budget differing from the
    common one says why -- read from the other. A reason beside the common
    budget explains a number no reader would have questioned, and it is the
    thing that would have arrived with the documenter's grant had 1 been
    recorded as though it were a judgement rather than the default.

    A function rather than an inline loop, for the reason `budget_problems`
    is one: the assertion below claims an absence, and a control can only
    demonstrate that absence can be reported if it can run the same code.
    """
    declared = [stage for stage in stages if "max_self_routes" in stage]
    if not declared:
        return []
    common = min(stage["max_self_routes"] for stage in declared)
    return [f"{stage['name']} records a reason for the common budget "
            f"{common!r}"
            for stage in stages
            if stage.get("max_self_routes_reason", "").strip()
            and stage.get("max_self_routes") == common]


def test_only_a_budget_that_differs_from_the_common_one_records_a_reason():
    """The sibling-reason convention, stated where the grant that tests it
    lands. story-060 gave the documenter the common budget, and the decision
    recorded with it is that the common budget carries no reason -- so the
    stage this deployment budgets differently stays the only one a reader
    meets an explanation beside.
    """
    reasons = [stage["name"] for stage in SHIPPED_STAGES
               if stage.get("max_self_routes_reason", "").strip()]
    assert reasons, "no recorded reason was read, so this asserts nothing"
    assert reason_convention_problems(SHIPPED_STAGES) == []


def test_reason_convention_problems_reports_a_reason_beside_the_common_budget():
    """The control for the assertion above, built rather than mutated from what
    this repository deploys."""
    stages = build_workflow(
        workflow_stage(max_self_routes=1),
        workflow_stage(max_self_routes=1,
                       max_self_routes_reason="one, because of something"),
    )["stages"]
    problems = reason_convention_problems(stages)
    assert len(problems) == 1, problems
    assert stages[1]["name"] in problems[0]

    # The companion: a check reporting every recorded reason would satisfy the
    # assertion above without distinguishing the outlier from the common one.
    outlier = build_workflow(
        workflow_stage(max_self_routes=1),
        workflow_stage(max_self_routes=2,
                       max_self_routes_reason="two, because of something"),
    )["stages"]
    assert reason_convention_problems(outlier) == []


def test_this_deployment_defines_more_than_one_retry_category():
    """From tests/test_retry_routing.py. The mechanism question — does the
    coordinator route on the category the verdict names — is asked there against
    a built table. Whether *this* deployment offers the verifier a real choice
    is a configuration question and is asked here."""
    routes = list(context_assembler.retry_routes(SHIPPED_STAGES))
    destinations = {route.category: route.stage for route in routes}
    assert len(destinations) >= 2, destinations
    assert len(set(destinations.values())) == len(destinations), \
        "two categories route to the same stage, so the category decides nothing"


def test_this_deployment_declares_a_clean_clone_check_and_a_revert_check():
    """From tests/test_clean_clone_check.py and tests/test_revert_check.py.
    Both modules assert that the coordinator *runs* the check a workflow
    declares, which a built workflow states. That this deployment turns both on
    is the configuration half."""
    clean_clone = [stage for stage in SHIPPED_STAGES if "clean_clone" in stage]
    revert_check = [stage for stage in SHIPPED_STAGES if "revert_check" in stage]
    assert len(clean_clone) == 1, [stage["name"] for stage in clean_clone]
    assert len(revert_check) == 1, [stage["name"] for stage in revert_check]
    assert clean_clone[0]["clean_clone"]["retry_stage"] in SHIPPED_NAMES
    assert revert_check[0]["revert_check"]["baseline"]


def test_this_deployment_documents_before_it_verifies():
    """The order stated here, once, where the rest of this deployment's
    configuration is stated. tests/test_documenter_before_verification.py holds
    story-045's mechanism end to end against a workflow it builds — that a
    definition listing a documenting stage before the judge is executed that
    way, and that the clone the check runs in holds the documenter's edits —
    and this is the declaration-level statement that *this* deployment is such
    a definition."""
    documenting = [stage["name"] for stage in SHIPPED_STAGES
                   if "documentation-report.md" in stage.get("outputs", [])]
    assert len(documenting) == 1, documenting
    assert SHIPPED_NAMES.index(documenting[0]) \
        < SHIPPED_NAMES.index(conftest.VERIFYING_STAGE)


def test_this_deployment_runs_the_stages_story_045_ordered():
    """From tests/test_documenter_before_verification.py's
    `test_the_workflow_lists_the_stages_in_the_new_order`, which compared this
    deployment's stage-name list against the order story-045 landed. That is a
    claim about what is deployed rather than about any mechanism, so it moved
    here when that module converted its runs to a built workflow; the module
    keeps the git-history comparison showing the reorder changed nothing else.

    Stated as the full list rather than as a pairwise ordering, because the
    criterion story-045 wrote is the list: write, then validate, then document,
    then judge.
    """
    assert SHIPPED_NAMES == ["implementer", "tester", "documenter", "verifier"]
    # And the judging stage really is the one the coordinator keys on, so the
    # name at the end of that list is not a coincidence of spelling.
    assert SHIPPED_NAMES[-1] == conftest.VERIFYING_STAGE


def test_this_deployment_validates_every_artifact_it_routes_on():
    """From tests/test_artifact_schemas.py's deployment half. Every artifact
    whose content the coordinator reads to make a decision is schema-checked
    before it is read."""
    validated = {artifact for stage in SHIPPED_STAGES
                 for artifact in stage.get("schemas", {})}
    assert conftest.VERIFICATION_RESULT in validated
    assert conftest.RETRY_GUIDANCE in validated
    for stage in SHIPPED_STAGES:
        record = stage.get("changed_files")
        if record is not None:
            assert record in validated, stage["name"]


def test_every_artifact_schema_the_shipped_stages_name_is_in_the_inventory():
    """From tests/test_schema_inventory_location.py's deployment half: the
    manifest is the inventory, and a stage naming a schema outside it would be
    validated against something nothing declares."""
    manifest = json.loads(
        (REPO_ROOT / "schemas" / "manifest.json").read_text(encoding="utf-8"))
    inventory = set(manifest if isinstance(manifest, list) else manifest.get(
        "schemas", manifest))
    named = {schema for stage in SHIPPED_STAGES
             for schema in stage.get("schemas", {}).values()}
    assert named
    assert named <= {str(entry) for entry in inventory} or all(
        schema_validator.load_schema(name) for name in named)


def test_the_coordinator_source_names_no_stage_this_deployment_declares():
    """From tests/test_stage_baseline.py and tests/test_revert_baseline.py,
    which both carried story-019's property: the coordinator decides from the
    declaration rather than from a stage name written into its own source.

    Asked of a built workflow the property is vacuous — the builder names its
    stages `stage-1`, `stage-2`, and no source would contain those anyway — so
    it moved here, where the names are the ones this repository actually
    deploys and a name appearing in the source is the thing it was watching
    for. The verifying stage is exempt because the coordinator legitimately
    keys its verdict handling on that name, which is why `conftest` writes it
    down; that exemption is held by
    `test_the_verifying_stage_is_the_name_the_coordinator_keys_on` above.

    The control is a name the code does own, asserted present, so a stripping
    that stopped seeing anything fails here rather than passing quietly.
    """
    body = _executable_source(
        (REPO_ROOT / "orchestration" / "story_coordinator.py")
        .read_text(encoding="utf-8"))
    assert "state.json" in body                     # the control
    for name in SHIPPED_NAMES:
        if name == conftest.VERIFYING_STAGE:
            continue
        assert name not in body, name

    # And the same of the capture, which may not name the verifying stage
    # either: nothing about a pre-stage baseline turns on which stage judges.
    capture = _executable_source(
        inspect.getsource(story_coordinator.capture_stage_baseline))
    assert "ls-files" in capture                    # the control
    for name in SHIPPED_NAMES:
        assert name not in capture, name

    # The negative control for both absences: the same scan over a source that
    # does name a shipped stage reports it.
    planted = _executable_source(
        f"def decide(stage):\n    return stage == {SHIPPED_NAMES[0]!r}\n")
    assert SHIPPED_NAMES[0] in planted


def test_the_shipped_definition_is_the_json_this_repository_holds():
    """The last configuration fact, and the one that keeps the rest honest: the
    definition every assertion above reads is the file in the working tree,
    resolved only where the definition asks for it."""
    raw = json.loads((REPO_ROOT / "workflows" / "story-workflow.json")
                     .read_text(encoding="utf-8"))
    assert raw["name"] == SHIPPED["name"]
    assert [stage["name"] for stage in raw["stages"]] == SHIPPED_NAMES
    resolved_keys = {"may_not_create"}
    for raw_stage, loaded_stage in zip(raw["stages"], SHIPPED_STAGES):
        for key in raw_stage:
            if key not in resolved_keys:
                assert raw_stage[key] == loaded_stage[key], (raw_stage["name"], key)


def test_this_module_reads_the_shipped_workflow_and_says_so():
    """This module is on the declared list of permitted readers in
    tests/test_baseline_honesty.py, and the reason recorded there has to be true
    of the module: the shipped definition is the subject here, not an input."""
    source = Path(__file__).read_text(encoding="utf-8")
    assert "shipped_workflow(REPO_ROOT" in source
    # And it does not smuggle the shipped definition into a mechanism
    # assertion: every validator control above is handed a built definition.
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("self_route_problems",
                                       "retry_routing_problems"):
            argument = ast.unparse(node.args[0])
            assert argument in ("SHIPPED_STAGES", "stages",
                                "loaded['stages']"), argument
