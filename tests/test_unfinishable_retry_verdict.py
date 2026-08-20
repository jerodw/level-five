"""Independent validation for story-049: a verifier verdict can say that
retrying cannot finish the work, and the run ends there.

The subject is a *decision a run makes at runtime*, so every assertion about
routing below is made against what a real run wrote — the state file, the
execution history, the escalation summary, the run directory itself — rather
than by calling the branch that made it.

**Which workflow the runs are driven against.** An assertion about how the
coordinator routes needs *a* workflow, not the one this repository ships, so
the routing half of this module runs against a mirrored harness root carrying
a probe workflow derived from the shipped definition under a name this
repository does not ship. Every stage name, retry category, destination and
artifact name used below is read off *that* definition, so a workflow that
grows a stage, gains a category or renames an artifact changes what these
tests derive rather than reddening them.

The two live artifacts this story does change — `prompts/verifier.md` and
`schemas/verification-result.schema.json` — are the **subject** of the last
two sections, not an input to them, so those runs are driven against the
shipped harness root and the rendered prompt is read back out of the run
directory. Reading a live artifact is right exactly there and nowhere else
here, and it is reached through the helpers that resolve it rather than
through a path this module joins, so this module stays off the live-artifact
scan's list.

Every absence this story asserts carries a demonstration that the same
assertion fails when the behaviour is violated:

  * "no stage ran after the verification", "no attempts/attempt-N/ was
    written" and "no retry was consumed" all sit beside the same run under a
    coordinator with the new branch disabled, where the identical verdict
    falls through to the routing path: it archives the attempt, increments
    the count and calls further stages, so all three assertions report;
  * "the budget was unspent" sits beside a run whose verdict differs only in
    dropping the field, which does spend a retry against the same ceiling —
    so the escalation is a judgement rather than a ceiling reached early;
  * "the verifier's own words are the recorded reason" sits beside an
    ordinary declined retry, whose recorded reason does not carry them;
  * "a passing verdict carrying the field is indistinguishable from one
    without it" sits beside the failed run, where the same search does find
    the planted text;
  * "the prompt states no shape of its own" sits beside the same prose with a
    shape sentence planted in it, which the same check reports, and "no
    placeholder is left unresolved" sits beside the template it was rendered
    from, which does carry them.

Nothing here invokes a model: every run goes through the fake agent runner,
under the guard that turns the one call that would reach one into a failure.
"""
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

import context_assembler
import conftest
import harness_config
import schema_validator
import story_coordinator
from conftest import load_mutant
from test_retry_routing import (COORDINATOR_PATH, OMITTED, PASS, PLACEHOLDER,
                                STORY_ID, Runner, attempt_dirs, build_target,
                                escalation_of, events, failing, history_of,
                                no_model,  # noqa: F401 - autouse guard
                                probe_harness, prompt_of, retry_records_of,
                                routed_retries, run_dir_of, state_of,
                                summary_of)

#: The name the probe workflow is built under. This repository ships no
#: workflow by this name, so a run driven against it is a run against a
#: definition this test owns — and every name below is derived from that
#: definition rather than written out a second time here.
PROBE_WORKFLOW = "unfinishable-verdict"

#: The field, named because it is what the story adds. Its shape is asserted
#: against the schema rather than restated.
FIELD = "unfinishable_by_retry"

#: The verifier's judgement, written the way the prompt asks for it and
#: carrying a mark nothing else in a run directory could produce. The mark is
#: what makes "this text was carried verbatim" a statement about *this* text
#: rather than about any reason at all.
MARK = "mark-cf41-unfinishable"
JUDGEMENT = (
    f"14 of the 22 modules remain and this attempt converted 2 of them, so "
    f"roughly seven further attempts would be needed; a first story should "
    f"carry the fixture builder and the three modules that use it, and a "
    f"follow-on the remaining eleven [{MARK}]"
)


def unfinishable(judgement: str = JUDGEMENT, *, target=OMITTED,
                 retry: bool = False, status: str = "failed") -> dict:
    """A verdict carrying the judgement, built on the shared failing verdict."""
    verdict = failing(target, retry=retry)
    verdict["status"] = status
    verdict[FIELD] = judgement
    return verdict


# --------------------------------------------------------------------------
# The fixture workflow the routing runs are driven against
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Fixture:
    """A mirrored harness root, and every name read off its own workflow."""

    harness: Path
    workflow: dict
    ceiling: int
    build: object

    @property
    def stage_names(self) -> list[str]:
        return [stage["name"] for stage in self.workflow["stages"]]

    @property
    def verifier(self) -> dict:
        """The stage that declares the routing table, found by the
        declaration rather than by name."""
        return next(s for s in self.workflow["stages"] if "on_failure" in s)

    @property
    def routes(self) -> dict:
        return self.verifier["on_failure"]["retry_routing"]

    @property
    def categories(self) -> list[str]:
        return sorted(self.routes)

    @property
    def outputs(self) -> list[str]:
        """Every artifact the definition says a stage writes to the run root."""
        return [artifact for stage in self.workflow["stages"]
                for artifact in stage.get("outputs", [])]

    @property
    def clean_clone_result(self) -> str:
        return self.verifier["clean_clone"]["result"]

    def unknown_category(self) -> str:
        return "not-a-" + "-or-".join(self.categories)


@pytest.fixture
def fixture(tmp_path: Path) -> Fixture:
    """A harness root carrying a workflow this repository does not ship, and
    a factory for target repositories pointed at it.

    Derived from the shipped definition by renaming it and nothing else, so
    the runs below exercise the coordinator's real routing while the names
    the assertions use come from a definition this module owns.
    """
    harness = probe_harness(tmp_path, PROBE_WORKFLOW, lambda workflow: None)

    def build(label: str) -> Path:
        return build_target(tmp_path / f"target-{label}", workflow=PROBE_WORKFLOW)

    return Fixture(
        harness=harness,
        workflow=conftest.shipped_workflow(harness, PROBE_WORKFLOW),
        ceiling=harness_config.load_rules(harness)["max_retries"],
        build=build,
    )


def calls_after_the_verification(fixture: Fixture, runner: Runner) -> list[str]:
    """Which stages ran after the first verification, in order."""
    first = runner.calls.index(fixture.verifier["name"])
    return runner.calls[first + 1:]


def run(fixture: Fixture, label: str, verdicts: list[dict],
        coordinator=story_coordinator) -> tuple[Path, Runner, int]:
    target = fixture.build(label)
    runner = Runner(target, verdicts)
    code = coordinator.run_story(STORY_ID, fixture.harness, target, runner)
    return target, runner, code


# --------------------------------------------------------------------------
# 1. The verdict ends the run at that verification
# --------------------------------------------------------------------------


def test_a_verdict_that_says_retrying_cannot_finish_ends_the_run_there(fixture):
    """Escalation at the verification that produced it, and nothing after."""
    target, runner, code = run(fixture, "ends-there", [unfinishable()])

    assert code == 2
    assert state_of(target)["status"] == "escalated"
    assert runner.calls.count(fixture.verifier["name"]) == 1
    assert calls_after_the_verification(fixture, runner) == []


def test_the_run_that_ends_this_way_left_its_retry_budget_unspent(fixture):
    """First sighting, read out of the run's own state against the ceiling.

    The count is what it was before the verification and it is *below* the
    ceiling, so the run stopped because the verifier said so rather than
    because there was nothing left to spend.
    """
    target, _, code = run(fixture, "unspent", [unfinishable()])

    assert code == 2
    state = state_of(target)
    assert state["retry_count"] == 0
    assert state["retry_count"] < fixture.ceiling
    assert attempt_dirs(target) == []
    assert routed_retries(target) == []
    assert not (run_dir_of(target) / "retry-history.json").exists()


def test_the_same_run_without_the_field_does_spend_a_retry(fixture):
    """The control for the budget assertion above.

    Same harness, same target-building code, same fake runner, same ceiling:
    the only difference is a verdict that carries no judgement and names a
    category. It reroutes, spends a retry and runs further stages — so the
    budget the run above left alone was genuinely there to spend.
    """
    category = fixture.categories[0]
    target, runner, code = run(fixture, "spends", [failing(category), PASS])

    assert code == 0
    assert state_of(target)["retry_count"] == 1
    assert state_of(target)["retry_count"] <= fixture.ceiling
    assert attempt_dirs(target) == ["attempt-1"]
    assert calls_after_the_verification(fixture, runner) != []


def test_no_attempt_archive_is_written_and_the_root_holds_the_ended_attempt(
    fixture,
):
    """Read by listing the run directory, not by trusting the path taken.

    The artifacts at the root are the ones describing the attempt that just
    ended: every declared output of every stage that ran is there, and the
    verdict at the root is the one this verification wrote.
    """
    target, _, code = run(fixture, "no-archive", [unfinishable()])

    assert code == 2
    assert attempt_dirs(target) == []
    assert not (run_dir_of(target) / "attempts").exists()

    for artifact in fixture.outputs:
        assert (run_dir_of(target) / artifact).is_file(), artifact
    verdict = json.loads(
        (run_dir_of(target) / fixture.verifier["outputs"][0]).read_text(
            encoding="utf-8"))
    assert verdict[FIELD] == JUDGEMENT


def test_disabling_the_branch_makes_all_three_absences_report(fixture, tmp_path):
    """The control for "no further stage", "no archive" and "no retry spent".

    One coordinator, one mutation: the branch that reads the judgement is
    disabled. The identical verdict — carrying the judgement, recommending a
    retry and naming a category, so the path below it can take it — then
    falls through to the routing path, and every absence the three tests
    above assert is present.
    """
    module = load_mutant(
        COORDINATOR_PATH,
        [(f'elif verdict.get("{FIELD}"):', f'elif False and verdict.get("{FIELD}"):')],
        name="mutant_coordinator_without_the_unfinishable_branch",
        tmp_path=tmp_path)

    category = fixture.categories[0]
    verdict = unfinishable(target=category, retry=True)
    target, runner, code = run(fixture, "branch-disabled", [verdict, PASS],
                               coordinator=module)

    assert code == 0
    assert calls_after_the_verification(fixture, runner) != []
    assert attempt_dirs(target) == ["attempt-1"]
    assert state_of(target)["retry_count"] == 1


# --------------------------------------------------------------------------
# 2. The recorded reason is the verifier's own text
# --------------------------------------------------------------------------


def test_the_escalation_reason_is_the_verifiers_text_and_not_a_paraphrase(
    fixture,
):
    """Equality, not containment: the coordinator composed nothing here.

    The summary is read through the harness's own reader of it, so what is
    asserted is the text a developer — and the resume guard — actually gets.
    """
    target, _, code = run(fixture, "verbatim", [unfinishable()])

    assert code == 2
    assert story_coordinator.escalation_reason(run_dir_of(target)) == JUDGEMENT
    assert MARK in summary_of(target)


def test_the_history_entry_records_the_decision_and_the_same_reason(fixture):
    target, _, code = run(fixture, "history", [unfinishable()])

    assert code == 2
    entry = escalation_of(target)
    assert entry["retry_decision"] == "escalate"
    assert entry["retry_reason"] == JUDGEMENT
    assert MARK in entry["message"]
    assert entry["verifier_outcome"] == "failed"

    schema = schema_validator.load_schema("execution-history")
    assert schema_validator.validate(history_of(target), schema) == []


def test_an_ordinary_declined_retry_records_none_of_that_text(fixture):
    """The control for the two assertions above.

    A failed verdict that recommends no retry and carries no judgement
    escalates too, and its recorded reason is the coordinator's — so finding
    the mark above is a statement about the field being carried rather than
    about every escalation reading the same way.
    """
    target, _, code = run(fixture, "declined", [failing(retry=False)])

    assert code == 2
    reason = story_coordinator.escalation_reason(run_dir_of(target))
    assert reason != JUDGEMENT
    assert MARK not in reason
    assert MARK not in summary_of(target)
    assert MARK not in json.dumps(history_of(target))


# --------------------------------------------------------------------------
# 3. A passing verdict carrying the field is not read at all
# --------------------------------------------------------------------------


def artifact_names(target: Path) -> list[str]:
    return sorted(path.name for path in run_dir_of(target).rglob("*")
                  if path.is_file())


def test_a_passing_verdict_carrying_the_field_completes_exactly_as_one_without(
    fixture,
):
    """Compared as artifacts, not as exit statuses.

    The two runs write the same set of files, take the same stages in the
    same order and record the same events — including the clean-clone check,
    which is where a passing verification does its remaining work.
    """
    passing_with = dict(PASS, **{FIELD: JUDGEMENT})
    plain, plain_runner, plain_code = run(fixture, "passing-plain", [PASS])
    carrying, carrying_runner, carrying_code = run(fixture, "passing-carrying",
                                                   [passing_with])

    assert (plain_code, carrying_code) == (0, 0)
    assert state_of(carrying)["status"] == state_of(plain)["status"] == "completed"
    assert state_of(carrying)["retry_count"] == state_of(plain)["retry_count"] == 0
    assert carrying_runner.calls == plain_runner.calls
    assert [entry["event"] for entry in history_of(carrying)] == \
        [entry["event"] for entry in history_of(plain)]
    assert artifact_names(carrying) == artifact_names(plain)

    # The clean-clone check ran in both, named off the fixture's own workflow.
    for root in (plain, carrying):
        assert (run_dir_of(root) / fixture.clean_clone_result).is_file()
        assert any(entry["event"].startswith("clean-clone")
                   for entry in history_of(root))
    assert not (run_dir_of(carrying) / "escalation-summary.md").exists()


def test_the_search_that_found_nothing_in_the_passing_run_does_find_it(fixture):
    """The control for the comparison above.

    The same search over the same two files, run against a failed verdict
    carrying the same text: it reports. So "the judgement appears nowhere in
    the passing run's record" is a statement about the passing run rather
    than about a search that stopped looking.
    """
    passing, _, passing_code = run(fixture, "passing-search",
                                   [dict(PASS, **{FIELD: JUDGEMENT})])
    failed, _, failed_code = run(fixture, "failed-search", [unfinishable()])

    assert (passing_code, failed_code) == (0, 2)
    assert MARK not in json.dumps(history_of(passing))
    assert MARK in json.dumps(history_of(failed))
    assert MARK in summary_of(failed)


# --------------------------------------------------------------------------
# 4. The contradiction is recorded as one
# --------------------------------------------------------------------------


def test_a_verdict_that_says_both_things_escalates_naming_the_contradiction(
    fixture,
):
    """Distinguishable in the record from both neighbours it sits between.

    Read from the recorded reason rather than from the decision: an ordinary
    declined retry and an ordinary routed one both record a decision this
    escalation could be mistaken for.
    """
    category = fixture.categories[0]
    target, runner, code = run(
        fixture, "contradiction",
        [unfinishable(target=category, retry=True), PASS])

    assert code == 2
    reason = story_coordinator.escalation_reason(run_dir_of(target))
    assert MARK in reason
    assert "contradict" in reason.lower()
    assert reason != JUDGEMENT

    # Neither of the two things it could be mistaken for: nothing was routed,
    # nothing was spent, and no stage ran after the verification.
    assert routed_retries(target) == []
    assert state_of(target)["retry_count"] == 0
    assert attempt_dirs(target) == []
    assert calls_after_the_verification(fixture, runner) == []


def test_the_contradiction_reason_differs_from_both_ordinary_reasons(fixture):
    """The control for the assertion above: the two reasons it must not read
    as, recorded by the two runs that produce them."""
    category = fixture.categories[0]
    contradictory, _, _ = run(fixture, "reason-contradiction",
                              [unfinishable(target=category, retry=True), PASS])
    declined, _, _ = run(fixture, "reason-declined", [failing(retry=False)])
    exhausted, _, _ = run(fixture, "reason-exhausted", [failing(category)])

    reasons = {root: story_coordinator.escalation_reason(run_dir_of(root))
               for root in (contradictory, declined, exhausted)}
    assert len(set(reasons.values())) == 3
    for root in (declined, exhausted):
        assert "contradict" not in reasons[root].lower()
        assert MARK not in reasons[root]


# --------------------------------------------------------------------------
# 5. A verdict without the field routes exactly as it did
# --------------------------------------------------------------------------


def test_a_recommended_retry_below_the_ceiling_still_reroutes(fixture):
    for category in fixture.categories:
        destination = fixture.routes[category]["stage"]
        target, runner, code = run(fixture, f"reroute-{category}",
                                   [failing(category), PASS])

        assert code == 0, category
        assert calls_after_the_verification(fixture, runner)[0] == destination
        assert [entry["retry_stage"] for entry in routed_retries(target)] == \
            [destination]
        assert [entry["retry_category"] for entry in routed_retries(target)] == \
            [category]
        assert len(retry_records_of(target)) == 1


def test_an_unroutable_target_still_escalates_naming_the_categories(fixture):
    unknown = fixture.unknown_category()
    target, _, code = run(fixture, "unroutable", [failing(unknown)])

    assert code == 2
    entry = escalation_of(target)
    for category in fixture.categories:
        assert category in entry["message"], category
    assert unknown in entry["message"]
    assert state_of(target)["retry_count"] == 0
    assert MARK not in entry["message"]


def test_an_exhausted_budget_still_escalates_naming_the_ceiling(fixture):
    target, _, code = run(fixture, "exhausted", [failing(fixture.categories[0])])

    assert code == 2
    assert state_of(target)["retry_count"] == fixture.ceiling
    entry = escalation_of(target)
    assert str(fixture.ceiling) in entry["retry_reason"]
    assert attempt_dirs(target) == [f"attempt-{n}"
                                    for n in range(1, fixture.ceiling + 1)]
    assert len(events(target, "escalated")) == 1


# --------------------------------------------------------------------------
# 6. The rendered verifier prompt — the shipped template is the subject here
# --------------------------------------------------------------------------


#: The criterion, the moment it applies, and the three parts of the split the
#: prompt must ask for. Phrases rather than sentences, so rewording the prose
#: around them costs nothing and dropping one of them reddens.
THE_CRITERION = "cannot plausibly close in the attempts that are left"
THE_FIRST_SIGHTING = "first verification"
THE_SPLIT = ("what remains", "this attempt delivered", "follow-on")

#: Ways of stating the field's *shape*. The prompt must state none of them:
#: the shape belongs to the schema injected into the same prompt.
SHAPE_STATEMENTS = ("optional", "string", '"type"', "required")


@pytest.fixture
def shipped_run(tmp_path: Path, harness_root: Path):
    """A run of this repository's own harness, for the prompt it renders.

    The shipped prompt is this section's subject, so it is reached the way a
    stage receives it: rendered by a real run and read back out of the run
    directory, which is stronger than reading the template.
    """
    workflow = conftest.shipped_workflow(harness_root)
    # Named explicitly, because story-048 converted `build_target`'s home
    # module to a workflow it builds for itself and its default follows that
    # definition. This section's subject is the *shipped* verifier prompt, so
    # this one target configures the shipped definition on purpose.
    target = build_target(tmp_path / "target-shipped",
                          workflow=workflow["name"])
    shipped_stages = [stage["name"] for stage in workflow["stages"]]
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target,
        Runner(target, [PASS], stage_names=shipped_stages)) == 0
    verifier = next(s for s in workflow["stages"] if "on_failure" in s)
    return prompt_of(target, verifier["name"], 1), verifier


def prose_of(rendered: str, harness_root: Path) -> str:
    """The rendered prompt with every injected schema removed.

    What is left is what the template itself says, which is where a restated
    shape would have to appear.
    """
    for injected in context_assembler.schema_context(harness_root).values():
        rendered = rendered.replace(injected, "")
    return rendered


def shape_statements(prose: str) -> list[str]:
    """Every statement of the field's shape the prose makes for itself."""
    return [
        statement
        for paragraph in prose.split("\n\n") if FIELD in paragraph
        for statement in SHAPE_STATEMENTS if statement in paragraph.lower()
    ]


def test_the_rendered_verifier_prompt_states_the_criterion_and_the_split(
    shipped_run, harness_root,
):
    rendered, _ = shipped_run
    prose = prose_of(rendered, harness_root)

    assert FIELD in prose
    assert THE_CRITERION in prose
    assert THE_FIRST_SIGHTING in prose
    for part in THE_SPLIT:
        assert part in prose, part


def test_the_prompt_leaves_the_fields_shape_to_the_schema_it_injects(
    shipped_run, harness_root,
):
    """Stated against the injection rather than inline.

    The schema really is in the prompt, it really does declare the field, and
    the prose around it states no shape of its own.
    """
    rendered, _ = shipped_run
    injected = context_assembler.schema_context(
        harness_root)["verification_result_schema"]

    assert injected in rendered
    assert FIELD in injected
    assert shape_statements(prose_of(rendered, harness_root)) == []


def test_the_shape_check_reports_a_shape_written_into_the_prose(
    shipped_run, harness_root,
):
    """The control for the absence above.

    The same check over the same prose with one sentence added to the
    paragraph that names the field reports it — so the emptiness above is a
    statement about the prompt rather than about a check with nothing to
    read.
    """
    rendered, _ = shipped_run
    prose = prose_of(rendered, harness_root)
    planted = prose.replace(FIELD, f"{FIELD} (an optional string)", 1)

    assert shape_statements(planted) != []
    assert set(shape_statements(planted)) <= set(SHAPE_STATEMENTS)


def test_the_rendered_verifier_prompt_resolves_every_placeholder(shipped_run):
    rendered, _ = shipped_run
    assert PLACEHOLDER.search(rendered) is None


def test_the_placeholder_check_sees_the_ones_the_template_carries(
    shipped_run, harness_root,
):
    """The control for the absence above, against the same regex and the
    template the prompt was rendered from, which does carry them."""
    _, verifier = shipped_run
    template = context_assembler.load_template(harness_root, verifier["prompt"])

    assert PLACEHOLDER.search(template) is not None
    assert "{{verification_result_schema}}" in template


# --------------------------------------------------------------------------
# 7. The schema — the shipped declaration is the subject here too
# --------------------------------------------------------------------------


def test_the_schema_declares_the_field_optional_and_says_what_it_means():
    schema = schema_validator.load_schema("verification-result")
    declaration = schema["properties"][FIELD]

    assert declaration["type"] == "string"
    assert FIELD not in schema["required"]
    assert declaration["description"].strip()


def test_the_validator_this_repository_ships_accepts_it_and_rejects_a_wrong_one():
    """The control for the declaration above: the schema is enforced rather
    than merely worded, and it still rejects what it rejected before."""
    schema = schema_validator.load_schema("verification-result")

    assert schema_validator.validate(unfinishable(), schema) == []
    assert schema_validator.validate(dict(PASS, **{FIELD: JUDGEMENT}), schema) == []
    assert schema_validator.validate(dict(PASS, **{FIELD: ["not a string"]}),
                                     schema) != []
    incomplete = {key: value for key, value in PASS.items() if key != "status"}
    assert schema_validator.validate(incomplete, schema) != []
