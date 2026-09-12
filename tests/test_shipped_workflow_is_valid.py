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

These validators decide well-formedness, and the coordinator runs them at
pre-flight, before a run spends a stage on a definition that cannot work:

  * `self_route_problems` — every declared self-route budget is a count;
  * `retry_routing_problems` — every declared route names a stage the workflow
    defines, and one that sits before the stage declaring the route;
  * `cost_ceiling_problems` — every declared cost ceiling, at the workflow
    level and on each stage, is a non-negative number that is not a bool;
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


def test_the_shipped_workflow_declares_only_ceilings_that_are_numbers():
    """The whole definition rather than its stages, because one of the two
    ceilings is declared at the workflow level."""
    assert story_coordinator.cost_ceiling_problems(SHIPPED) == []


def test_every_stage_exception_this_repository_states_means_something():
    """Each of this repository's own stories, cross-checked against its own
    workflow. A grant naming a stage this deployment does not define, or a path
    its stage was never restricted on, grants nothing — and the run that
    discovers it is refused at pre-flight.

    What is held is conditional on what the corpus holds, and deliberately says
    nothing about how many stories exist or whether any of them states a grant.
    A companion requiring one did sit here, and it made the module depend on
    this repository's story corpus being shaped a particular way in order to
    have something to read. That the check reports a bad grant and accepts a
    good one is driven on stories the tests below construct, which is where
    that property belongs.
    """
    for story_path in sorted(STORIES_DIR.glob("*.yaml")):
        story = story_coordinator.read_story(
            story_path.read_text(encoding="utf-8"), REPO_ROOT).parsed
        assert story_coordinator.stage_exception_problems(
            story, SHIPPED_STAGES) == [], story_path.name


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


@pytest.mark.parametrize("ceiling", [-1, True, "30", [], None],
                         ids=["negative", "a-bool", "a-string", "a-list",
                              "declared-null"])
def test_cost_ceiling_problems_reports_a_ceiling_that_is_not_a_number(ceiling):
    """Both halves at once, so a validator checking only one of the two places
    a ceiling can be declared is reported here."""
    workflow = build_workflow(
        workflow_stage(max_execution_cost_usd=ceiling),
        name="a-badly-ceilinged-workflow")
    workflow["max_run_cost_usd"] = ceiling
    problems = story_coordinator.cost_ceiling_problems(workflow)
    assert len(problems) == 2, problems
    assert workflow["name"] in problems[0]
    assert workflow["stages"][0]["name"] in problems[1]


def test_cost_ceiling_problems_accepts_zero_and_a_definition_declaring_none():
    """The companion the parametrization needs. Zero is a deliberate refusal to
    run and is well-formed; declaring nothing is unbounded and is not checked
    at all. A validator that reported everything would satisfy the cases above
    while refusing both of these."""
    zero = build_workflow(workflow_stage(max_execution_cost_usd=0),
                          name="a-zero-ceilinged-workflow")
    zero["max_run_cost_usd"] = 0
    assert story_coordinator.cost_ceiling_problems(zero) == []

    silent = build_workflow(workflow_stage(), name="an-unceilinged-workflow")
    assert "max_execution_cost_usd" not in silent["stages"][0]
    assert story_coordinator.cost_ceiling_problems(silent) == []


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


# --------------------------------------------------------------------------
# Every definition this repository ships, not only the one it configures
#
# story-070 shipped a second workflow, reachable per work item rather than by
# configuration, and a definition nothing configures is a definition no
# pre-flight has ever run over. The well-formedness validators above are asked
# of all of them, discovered by glob so a third costs nothing to cover, while
# the deployment assertions below stay pointed at the configured one — what
# this project intends of its default is not a question about a workflow that
# has to be asked for by name.
# --------------------------------------------------------------------------


def shipped_definitions() -> dict[str, dict]:
    """Every workflow under `workflows/`, loaded the way a run loads it."""
    return {path.stem: shipped_workflow(REPO_ROOT, path.stem)
            for path in sorted((REPO_ROOT / "workflows").glob("*.json"))}


def test_the_configured_definition_is_among_the_definitions_swept():
    """What the glob has to find for the sweep below to mean anything: the
    definition this repository configures.

    It used to require two definitions to ship, which made removing one — a
    deployment decision the sweep has nothing to say about — redden the module.
    The configured one is a different matter: another test above already
    requires the file it names to exist, so a sweep that missed it would be a
    sweep that had stopped reading `workflows/` rather than a deployment
    shipping fewer definitions.
    """
    assert SHIPPED["name"] in shipped_definitions()


@pytest.mark.parametrize("name", sorted(shipped_definitions()))
def test_every_shipped_definition_is_well_formed(name):
    """The same validators the pre-flight runs, over every definition that
    ships. Their controls are the built definitions above: each of these three
    is shown reporting a violation there, so a clean sweep here is a statement
    about these definitions rather than about a validator that stopped
    looking."""
    definition = shipped_definitions()[name]
    stages = definition["stages"]
    assert stages, name
    assert story_coordinator.self_route_problems(stages) == [], name
    assert story_coordinator.retry_routing_problems(stages) == [], name
    assert story_coordinator.cost_ceiling_problems(definition) == [], name
    assert definition["name"] == name


@pytest.mark.parametrize("name", sorted(shipped_definitions()))
def test_every_shipped_definition_names_prompts_and_schemas_that_exist(name):
    for stage in shipped_definitions()[name]["stages"]:
        assert (REPO_ROOT / "prompts" / stage["prompt"]).is_file(), stage["name"]
        for schema in stage.get("schemas", {}).values():
            assert schema in schema_validator.shipped_schemas(), schema


def test_the_shipped_workflow_is_the_one_this_repository_configures():
    """The definition asserted about below is the one a run of this repository
    would load, rather than a file that happens to sit beside it."""
    configured = conftest.repository_config().get("workflow", "story-workflow")
    assert SHIPPED["name"] == configured
    assert (REPO_ROOT / "workflows" / f"{configured}.json").is_file()


def test_a_definitions_stage_order_is_the_order_its_run_executes():
    """What the stage list is *for*, held against a definition this test builds
    with stages it named: the order declared is the order the loaded definition
    carries, and the stage that judges is last.

    This asserted the shipped list outright — write, then validate, then
    document, then judge — which made adding or renaming a stage a red module
    rather than a deployment change. What the assertion was protecting is the
    ordering property, and that is a fact about definitions rather than about
    which four stages this repository happens to deploy; the deployment half
    that survives is beneath it, and is a coupling to orchestration rather than
    a configuration choice.
    """
    named = ["writes-it", "validates-it", "documents-it",
             conftest.VERIFYING_STAGE]
    built = build_workflow(*[workflow_stage(name=name) for name in named],
                           name="an-ordered-workflow")

    assert [stage["name"] for stage in built["stages"]] == named
    assert built["stages"][-1]["name"] == conftest.VERIFYING_STAGE


def test_the_stage_that_judges_this_deployment_is_the_last_one():
    """The one ordering fact about the shipped definition that is not a
    configuration choice: a run reaches a verdict only if the stage the
    coordinator keys its verdict handling on is reached, and a definition
    listing it anywhere but last leaves stages after the verdict.

    Which stage that is comes off the coordinator rather than off this
    deployment's taste, and the test below holds that coupling.
    """
    assert SHIPPED_NAMES[-1] == conftest.VERIFYING_STAGE


def test_the_verifying_stage_is_the_name_the_coordinator_keys_on():
    """A harness fact rather than a deployment one, and the reason a built
    workflow whose run must reach a verdict names its last stage this. Asserted
    against the coordinator's own source, so a rename there fails here rather
    than silently making every built workflow unverifiable."""
    source = Path(story_coordinator.__file__).read_text(encoding="utf-8")
    assert f'"{conftest.VERIFYING_STAGE}"' in source
    assert conftest.VERIFYING_STAGE in SHIPPED_NAMES


def raw_definition() -> dict:
    """The shipped definition as it ships, references unresolved."""
    return json.loads((REPO_ROOT / "workflows" / "story-workflow.json")
                      .read_text(encoding="utf-8"))


#: The token every declaration naming the test location is written as. One
#: substitution, so a target configuring a different location is governed
#: there under all of them with no workflow edit.
TESTS_DIR_TOKEN = "{{tests_dir}}"


def test_the_implementer_may_not_create_the_configured_tests_directory():
    """The separation the two-stage split exists for, and the one configuration
    reference in this workflow: the restriction is written as `{{tests_dir}}`
    and resolves to what this repository configures."""
    restrictions = story_coordinator.stage_restrictions(SHIPPED_STAGES)
    tests_dir = conftest.repository_config()["tests_dir"]
    creation = [restriction for restriction in restrictions
                if restriction.sense == story_coordinator.CREATE_RESTRICTION]
    assert [(restriction.stage, restriction.prefix)
            for restriction in creation] == [(SHIPPED_NAMES[0], tests_dir)]
    # The token, not the value, is what the file says.
    assert raw_definition()["stages"][0][
        story_coordinator.CREATE_RESTRICTION] == [TESTS_DIR_TOKEN]


def confined_stage_declaration(definition: dict) -> dict:
    """The stage that writes the validation, out of a definition.

    Found by the restriction that confines it rather than by its name, so this
    names no stage of its own; the assertions below then read that stage's
    other declarations.
    """
    confinements = [restriction for restriction
                    in story_coordinator.stage_restrictions(SHIPPED_STAGES)
                    if restriction.sense == story_coordinator.CONFINEMENT]
    (confinement,) = confinements
    return next(stage for stage in definition["stages"]
                if stage["name"] == confinement.stage)


def test_the_stage_that_writes_the_validation_is_confined_to_that_location():
    """The mirror image of the restriction above, on the other stage.

    Written as the same token, so the confinement moves with the configuration
    exactly as the create restriction does — a target keeping its tests
    elsewhere is governed there under both with no workflow edit.
    """
    confinement = next(
        restriction for restriction
        in story_coordinator.stage_restrictions(SHIPPED_STAGES)
        if restriction.sense == story_coordinator.CONFINEMENT)

    assert confinement.prefix == conftest.repository_config()["tests_dir"]
    assert confinement.stage != SHIPPED_NAMES[0]
    assert confined_stage_declaration(raw_definition())[
        story_coordinator.CONFINEMENT] == [TESTS_DIR_TOKEN]


def test_that_stage_declares_the_revert_check_the_confinement_needs():
    """The confinement brings its baseline with it, and the baseline is the
    revert check's.

    Without the check there is no stage baseline over the tree outside the
    confinement, and an edit made there could be neither judged nor undone —
    which is the evidence gap the confinement exists to close. So the two are
    asserted together, and the artifact and the directories the check names are
    read off the declaration rather than spelled here.
    """
    declaration = confined_stage_declaration(raw_definition())["revert_check"]

    assert declaration["result"]
    assert declaration["baseline"]
    assert declaration["discarded"]
    # The result artifact is the stage's own, not shared with another stage's.
    others = [stage["revert_check"]["result"]
              for stage in raw_definition()["stages"]
              if "revert_check" in stage
              and stage["revert_check"]["result"] != declaration["result"]]
    assert declaration["result"] not in others
    # And so is the directory the discarded work is kept under, or one stage's
    # refusal would overwrite another's evidence.
    kept = [stage["revert_check"].get("discarded")
            for stage in raw_definition()["stages"]
            if "revert_check" in stage]
    assert len(kept) == len(set(kept))


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
    """A category with no `when` gives the verifier nothing to choose on.

    Conditional on what is defined: the companion requiring a category to exist
    is gone, because whether this deployment defines any is a configuration
    question rather than something this loop needs in order to be honest.
    """
    for route in context_assembler.retry_routes(SHIPPED_STAGES):
        assert route.when.strip(), route.category


def test_the_budgets_this_deployment_grants_are_recorded_where_a_reader_meets_them():
    """A budget is a judgement about how a stage fails, and this deployment
    records the reasoning beside the grant rather than in a commit message.

    Deliberately shaped as "every budget that differs from the common one says
    why" rather than as a count of budgeted stages: granting one more stage the
    common budget is the change story-047 made, and it belongs here as a
    passing change rather than as a red one.

    Conditional on what is declared, and the collection step is written so a
    deployment declaring no budget at all leaves this vacuously true rather
    than raising on an empty minimum. The companion that used to require a
    budget to exist is gone: it made the module depend on this deployment being
    configured a particular way in order for the loop beside it to have
    something to read.
    """
    budgets = [stage["max_self_routes"] for stage in SHIPPED_STAGES
               if "max_self_routes" in stage]
    if not budgets:
        return
    common = min(budgets)
    for stage in SHIPPED_STAGES:
        if stage.get("max_self_routes", common) != common:
            assert stage.get("max_self_routes_reason", "").strip(), stage["name"]


#: Every budget a recorded reason may be written out in words for. Digits or
#: the English word, because a sentence about a budget of two reads better as
#: "two" and either spelling states the number.
BUDGET_WORDS = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four",
                5: "five"}


def states_the_number(reason: str, number: int) -> bool:
    """Whether a recorded reason states the number it explains, either way."""
    lowered = reason.lower()
    return str(number) in lowered or BUDGET_WORDS.get(number, "\0") in lowered


def test_a_recorded_reason_states_the_number_it_is_explaining():
    """Displaced from tests/test_self_routing_retry.py, whose subject became
    how a budgeted stage and a budget-less one behave once it built its own
    workflow.

    A reason that never mentions the budget it justifies would satisfy the
    check above while explaining nothing, so the shipped reasons are read —
    every one of them, rather than the outliers alone.

    Reading only the outliers came with a companion requiring an outlier to
    exist, which made the suite fail unless this deployment kept one stage
    budgeted differently from the rest. A test may hold what a recorded reason
    must say; it may not require the deployment to be configured a particular
    way so that it has something to read. So the loop is over every declared
    reason and the companion is gone. It is not vacuous today: the stage that
    writes the validation declares one, and its reason says so.
    """
    for stage in SHIPPED_STAGES:
        reason = stage.get("max_self_routes_reason")
        if reason is None or "max_self_routes" not in stage:
            continue
        assert states_the_number(reason, stage["max_self_routes"]), stage["name"]


#: The key a declared value's recorded derivation sits under, formed from the
#: declaration's own name. Written as the suffix rather than as two more
#: literals, so a reason key and the number it explains cannot drift apart
#: here without drifting apart in the definition too.
REASON_SUFFIX = "_reason"
RUN_CEILING_KEY = "max_run_cost_usd"
EXECUTION_CEILING_KEY = "max_execution_cost_usd"


def ceiling_declarations(definition: dict) -> list[tuple[str, dict, str]]:
    """Every cost ceiling a definition declares, as where-it-is, the mapping
    that declares it, and the key.

    A function rather than a loop inside a test so the property below can be
    asked of a definition a test builds as readily as of the shipped one, which
    is what lets the property be held on values a test chose.
    """
    declarations = []
    if RUN_CEILING_KEY in definition:
        declarations.append((definition["name"], definition, RUN_CEILING_KEY))
    declarations += [(stage["name"], stage, EXECUTION_CEILING_KEY)
                     for stage in definition["stages"]
                     if EXECUTION_CEILING_KEY in stage]
    return declarations


def ceilings_with_no_reason_stating_them(definition: dict) -> list[str]:
    """Every declared ceiling whose recorded reason is missing or does not
    state the number it explains.

    A list rather than an assertion so the same reading can be made of a
    definition that violates it, which is the control the absence needs.
    """
    return [where for where, declaring, key in ceiling_declarations(definition)
            if not str(declaring.get(key + REASON_SUFFIX, "")).strip()
            or str(declaring[key]) not in declaring[key + REASON_SUFFIX]]


def test_a_declared_ceiling_is_recorded_where_a_reader_meets_the_number():
    """A ceiling is a judgement about what is pathological, and the logs the
    figures came from are gitignored and reach no clone — so the reason beside
    the number is the only place the derivation survives. A reason that never
    mentions its own ceiling would satisfy a bare "a reason is present" check
    while explaining nothing.

    Held against a definition this test builds, with ceilings this test chose,
    because that is what the property is about: what a *declaration* has to
    carry. It used to be asked of the shipped definition alone and paired with
    an equality requiring every stage and the workflow to declare one — which
    is a configuration this deployment happens to have chosen, which the
    pre-flight explicitly accepts a definition for not having, and which a
    stage added or a ceiling removed would have reddened here.
    """
    built = build_workflow(
        workflow_stage(max_execution_cost_usd=17,
                       max_execution_cost_usd_reason="17, because 17"),
        workflow_stage(name=conftest.VERIFYING_STAGE),
        name="a-ceilinged-workflow")
    built[RUN_CEILING_KEY] = 41
    built[RUN_CEILING_KEY + REASON_SUFFIX] = "41 is what this one is willing"

    assert len(ceiling_declarations(built)) == 2
    assert ceilings_with_no_reason_stating_them(built) == []


@pytest.mark.parametrize("reason, missing", [
    (None, True), ("   ", True), ("a number nobody wrote down", True),
], ids=["absent", "blank", "silent about its own number"])
def test_that_reading_reports_a_ceiling_whose_reason_does_not_state_it(
    reason, missing,
):
    """The control for the absence above, built rather than mutated from what
    this repository deploys: a reason that is absent, blank, or present and
    silent about the number it explains is reported in every case."""
    extra = {} if reason is None else {
        EXECUTION_CEILING_KEY + REASON_SUFFIX: reason}
    built = build_workflow(
        workflow_stage(max_execution_cost_usd=17, **extra),
        name="an-unexplained-ceiling-workflow")

    reported = ceilings_with_no_reason_stating_them(built)
    assert bool(reported) is missing
    assert reported == [built["stages"][0]["name"]]


def test_every_ceiling_this_deployment_declares_is_recorded_that_way_too():
    """The same reading over what this repository ships, which is the reason
    the convention exists at all: a developer meeting one of these numbers in
    the definition meets the derivation beside it.

    It asks nothing about which ceilings are declared or what their values are
    — a definition declaring none satisfies it, and the built cases above are
    what say the reading can report one.
    """
    assert ceilings_with_no_reason_stating_them(SHIPPED) == []


def test_nothing_in_the_harness_reads_a_recorded_reason():
    """The reason keys are for a reader of the definition, not for the
    coordinator: a ceiling whose behaviour depended on its own justification
    would be a value nobody could change without rewriting prose.

    The control is the same scan over the same source with a read of one of
    those keys planted in it, which reports it — so the empty result is a scan
    that can see one. Prose may name what code may not, so docstrings and
    comment lines are stripped before the reading.
    """
    reason_keys = sorted({key for declaring in (SHIPPED, *SHIPPED_STAGES)
                          for key in declaring if key.endswith(REASON_SUFFIX)})
    assert reason_keys, "this deployment records no reason at all"

    source = "\n".join(
        _executable_source(path.read_text(encoding="utf-8"))
        for path in sorted((REPO_ROOT / "orchestration").glob("*.py")))
    assert [key for key in reason_keys if key in source] == []

    planted = source + (f'\ndef planted(stage):\n'
                        f'    return stage["{reason_keys[0]}"]\n')
    assert [key for key in reason_keys if key in planted] == [reason_keys[0]]


# --------------------------------------------------------------------------
# The split budget this deployment opted into
#
# story-108 split the self-route budget by cause, and made the split
# opt-in: a stage declaring no bookkeeping budget spends max_self_routes on
# everything, exactly as every stage did before. What this deployment declares
# is therefore a fact about this deployment and not about the mechanism, and it
# is asserted here — where the shipped definition is the subject — rather than
# in the module that drives the mechanism against a workflow it builds.
# --------------------------------------------------------------------------


def test_a_stage_that_splits_its_budget_by_cause_declares_both_halves():
    """A bookkeeping budget on its own splits nothing: the coordinator compares
    a bookkeeping cause against it and every other cause against the failure
    budget beside it, so a stage declaring only the first has opted into a
    split with one half undeclared.

    This pinned both of this deployment's numbers at two, which is a
    configuration rather than a property — and story-137 dropped the failure
    half to one, which is exactly the kind of correct change a pinned value
    reddens for no reason. What is held now is the pairing, over whichever
    stages declare the split, and nothing about how many do or what they say.
    """
    key = story_coordinator.BOOKKEEPING_SELF_ROUTE_BUDGET_KEY
    for stage in SHIPPED_STAGES:
        if key in stage:
            assert story_coordinator.SELF_ROUTE_BUDGET_KEY in stage, \
                stage["name"]


def test_the_split_budget_is_recorded_where_a_reader_meets_the_number():
    """The rule every other declared number here is held to, applied to the
    budget story-108 added: a reason is present and states its own number.

    Conditional on the declaration, as its sibling above is: the companion
    requiring this deployment to declare a bookkeeping budget at all is gone,
    it being a configuration this loop should not have depended on. It is not
    vacuous today, because the stage that writes the validation declares one.
    """
    key = story_coordinator.BOOKKEEPING_SELF_ROUTE_BUDGET_KEY
    for stage in SHIPPED_STAGES:
        if key not in stage:
            continue
        reason = stage.get(key + REASON_SUFFIX, "")
        assert reason.strip(), stage["name"]
        assert states_the_number(reason, stage[key]), stage["name"]


#: The vocabulary of a budget that stands in for a fix rather than being a
#: judgement about the work. Present tense throughout, deliberately: a reason
#: may record that a *former* budget was a hedge — that is history, and
#: story-108's reason says exactly that about the third budget it removed —
#: and may not describe the budget it currently explains as one. What the
#: budget between story-101 and story-108 was is settled; what this deployment
#: declares now has to be defensible on its own.
STANDING_IN_FOR_A_FIX = ("is a hedge", "not the fix", "is not the fix",
                         "pending a fix", "until that is fixed",
                         "awaiting a fix")


def reasons_standing_in_for_a_fix(stages: list[dict]) -> list[str]:
    """Every recorded budget reason describing its own budget as a stand-in.

    A function rather than a loop inside the test, so the same reading can be
    made of a reason this file writes — which is the control the absence needs.
    """
    keys = (story_coordinator.SELF_ROUTE_BUDGET_KEY + REASON_SUFFIX,
            story_coordinator.BOOKKEEPING_SELF_ROUTE_BUDGET_KEY + REASON_SUFFIX)
    found = []
    for stage in stages:
        for key in keys:
            reason = str(stage.get(key, "")).lower()
            for phrase in STANDING_IN_FOR_A_FIX:
                if phrase in reason:
                    found.append(f"{stage['name']} {key}: {phrase}")
    return found


def test_no_self_route_reason_this_deployment_ships_stands_in_for_a_fix():
    """The defect story-108 closed was charged to a budget, and the budget's
    own reason said so: it read "It is a hedge and not the fix" and named the
    brief the fix was filed under. With the fix landed, no reason here may
    still describe the budget it explains that way.

    The control beside it is a reason this test writes carrying exactly the
    sentence the shipped one used to carry, which the same reading reports — so
    the empty result is a scan that can see one.
    """
    assert reasons_standing_in_for_a_fix(SHIPPED_STAGES) == []

    planted = [{"name": "planted",
                story_coordinator.SELF_ROUTE_BUDGET_KEY + REASON_SUFFIX: (
                    "Three, where every other budget in this workflow is one. "
                    "It is a hedge and not the fix: the budget spends the same "
                    "whatever the cause.")}]
    found = reasons_standing_in_for_a_fix(planted)
    assert found
    key = story_coordinator.SELF_ROUTE_BUDGET_KEY + REASON_SUFFIX
    assert {entry.split(": ")[-1] for entry in found} == \
        {"is a hedge", "not the fix"}
    assert {entry.split(": ")[0] for entry in found} == {f"planted {key}"}


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


def orphan_reason_problems(stages):
    """One problem per stage recording a self-route reason beside no budget.

    A reason with nothing to explain is a reason a reader cannot check and a
    number nobody declared -- the shape a budget removed without its
    justification leaves behind. This is what survives of the convention
    story-060 recorded here and story-137 removed: that only a budget differing
    from the common one may carry a reason.

    That convention read the reasons this deployment happened to record and
    required the outlier to be the only one recording one, so it made a correct
    change red twice over -- once when story-137 dropped the tester's failure
    budget to the common number while keeping the reason that explains why, and
    again for any deployment budgeting every stage alike. What a recorded reason
    must *say* is not weakened by its going: `test_a_recorded_reason_states_the_
    number_it_is_explaining` now reads every declared reason rather than the
    outliers alone, which is strictly more than the pair here ever read.

    A function rather than an inline loop, for the reason `budget_problems` is
    one: the assertion below claims an absence, and a control can only
    demonstrate that absence can be reported if it can run the same code.
    """
    return [f"{stage['name']} records a max_self_routes_reason and declares no "
            f"max_self_routes"
            for stage in stages
            if str(stage.get("max_self_routes_reason", "")).strip()
            and "max_self_routes" not in stage]


def test_every_recorded_reason_this_deployment_ships_explains_a_declared_budget():
    """No reason here explains a number that is not there."""
    assert orphan_reason_problems(SHIPPED_STAGES) == []


def test_orphan_reason_problems_reports_a_reason_beside_no_budget():
    """The control for the assertion above, built rather than mutated from what
    this repository deploys: the builder omits `max_self_routes` unless asked
    for it, so a reason beside no budget is one argument away."""
    stages = build_workflow(
        workflow_stage(max_self_routes=1,
                       max_self_routes_reason="one, because of something"),
        workflow_stage(max_self_routes_reason="two, because of something"),
    )["stages"]
    problems = orphan_reason_problems(stages)
    assert len(problems) == 1, problems
    assert stages[1]["name"] in problems[0]

    # The companion: a check reporting every recorded reason would satisfy the
    # assertion above without distinguishing the orphan from the explained one.
    explained = build_workflow(
        workflow_stage(max_self_routes=1,
                       max_self_routes_reason="one, because of something"),
        workflow_stage(max_self_routes=2,
                       max_self_routes_reason="two, because of something"),
    )["stages"]
    assert orphan_reason_problems(explained) == []


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
    is the configuration half.

    The revert check is declared on every stage this deployment governs where
    it writes, which is what makes an edit outside a stage's lane recoverable:
    the check is what brings the stage baseline with it. So the two lists are
    asserted to agree rather than the check being counted, and each declaring
    stage is asserted to name a baseline of its own to restore from.
    """
    clean_clone = [stage for stage in SHIPPED_STAGES if "clean_clone" in stage]
    assert len(clean_clone) == 1, [stage["name"] for stage in clean_clone]
    assert clean_clone[0]["clean_clone"]["retry_stage"] in SHIPPED_NAMES

    declaring = [stage["name"] for stage in SHIPPED_STAGES
                 if "revert_check" in stage]
    governed = sorted({restriction.stage for restriction
                       in story_coordinator.stage_restrictions(SHIPPED_STAGES)})
    assert declaring, declaring
    assert sorted(declaring) == governed
    for stage in SHIPPED_STAGES:
        declaration = stage.get("revert_check")
        if declaration is not None:
            assert declaration["baseline"], stage["name"]


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


def test_this_deployment_writes_its_validation_where_it_cannot_be_written_first():
    """From tests/test_documenter_before_verification.py's
    `test_the_workflow_lists_the_stages_in_the_new_order`, which compared this
    deployment's stage-name list against the order story-045 landed. That is a
    claim about what is deployed rather than about any mechanism, so it moved
    here when that module converted its runs to a built workflow; the module
    keeps the git-history comparison showing the reorder changed nothing else.

    It arrived as the full list — implementer, tester, documenter, verifier —
    and story-137 took the list out: pinning it made a stage added or renamed a
    red module rather than a deployment change, and the ordering property it
    stood for is held above against a definition the test builds. What survives
    is the separation this deployment's confinement actually depends on and
    that no built definition can state: the stage confined to the test location
    runs after the stage restricted from creating there, so the validation is
    written against an implementation that already exists.
    """
    creating = [restriction.stage for restriction
                in story_coordinator.stage_restrictions(SHIPPED_STAGES)
                if restriction.sense == story_coordinator.CREATE_RESTRICTION]
    confined = [restriction.stage for restriction
                in story_coordinator.stage_restrictions(SHIPPED_STAGES)
                if restriction.sense == story_coordinator.CONFINEMENT]

    for restricted in creating:
        for writer in confined:
            assert SHIPPED_NAMES.index(restricted) < SHIPPED_NAMES.index(writer)


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
    # Every key whose value the loader resolves against the target's
    # configuration, read off the coordinator's own vocabulary rather than
    # listed here — a sense added there is a sense this comparison keeps
    # skipping rather than one it starts reporting as a mismatch between the
    # token and the value.
    resolved_keys = {story_coordinator.CREATE_RESTRICTION,
                     story_coordinator.CONFINEMENT}
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
