"""Independent validation for story-050: retry guidance may not sanction the
failure it corrects.

The subject is a *decision a run makes at runtime*, so every routing assertion
below is made against what a real run wrote — `state.json`, the execution
history, the run directory listing, the self-route evidence, the rendered
prompts — rather than by calling the branch that made it.

**Which workflow the runs are driven against.** An assertion about how the
coordinator routes needs *a* workflow, not the one this repository ships, so
the routing half of this module runs against a mirrored harness root carrying
a probe workflow derived from the shipped definition under a name this
repository does not ship. Every stage name, retry category, destination,
budget and artifact name used below is read off *that* definition — including
the guidance artifact, which is derived as the verifier's one conditional
output rather than spelled — so a workflow that grows a stage, gains a
category, renames an artifact or moves a budget changes what these tests
derive rather than reddening them.

The artifacts this story does change — the four schemas and `prompts/
verifier.md` — are the **subject** of the last two sections, not an input to
them, so those are reached through the shipped readers (`schema_validator.
load_schema`, `context_assembler.schema_context`) and the rendered prompt is
read back out of a real run's directory. Reading a shipped artifact is right
exactly there and nowhere else here, and it is reached through the helpers
that resolve it rather than through a path this module joins, so this module
stays off the live-artifact scan's list.

The distinction the story lives or dies on is between two failed verdicts that
differ in one field: one that reports an entry of its guidance unmet, which is
ordinary under-delivery and routes exactly as a failed verdict always has, and
one that reports every entry met and fails the work anyway, which is the
contradiction. Both are driven here, side by side, from the same fixture.

Every absence this story asserts carries a demonstration that the same
assertion fails when the behaviour is violated:

  * "no retry was consumed" and "no attempts/attempt-N/ was written for it"
    sit beside the identical verdict under a coordinator with the
    defective-guidance branch disabled, which falls through to the routing
    path, archives the attempt and increments the count — so both assertions
    report;
  * "the budget was genuinely available" is stated as the recorded count
    against the ceiling the run's own rules declare, and sits beside a run
    that does spend it;
  * "the check is not applied where no guidance was in force" sits beside the
    same verdict shape on an attempt that *was* routed with guidance, where
    the same run escalates on the mismatch — so the untouched routing is a
    statement about the guidance in force rather than about a check that
    stopped looking;
  * "the clean-clone reroute leaves no guidance in force" is read off
    `state.json` at the entry to the stage that followed it, and sits beside
    the verification-failure reroute observed the same way, which does record
    the entries;
  * "no unmet entry, so nothing under-delivered" sits beside the same run with
    one entry marked unmet, which routes as an ordinary retry;
  * "the rendered prompt leaves no placeholder unresolved" sits beside the
    template it was rendered from, which does carry them, checked by the same
    regex.

Nothing here invokes a model: every run goes through the fake agent runner,
under the guard `test_retry_routing` installs over the one call that would
reach one.
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
from agent_runner import AgentResult
from conftest import load_mutant
from test_retry_routing import (COORDINATOR_PATH, OMITTED, PASS, PLACEHOLDER,
                                STORY_ID, attempt_dirs, build_target,
                                escalation_of, events, failing, history_of,
                                no_model,  # noqa: F401 - autouse guard
                                probe_harness, retry_records_of,
                                routed_retries, run_dir_of, state_of,
                                summary_of, verifier_stage_of, write_json)
from test_self_routing_retry import FAILURE_IDS as MECHANICAL_FAILURES

#: The name the probe workflow is built under. This repository ships no
#: workflow by this name, so a run driven against it is a run against a
#: definition this module owns.
PROBE_WORKFLOW = "defective-retry-guidance"

#: The fourth self-route failure, as the schema declares it. Not a stage name,
#: a category or an artifact name — it is an identifier the schema under test
#: declares, and the schema section below asserts that it is in the enum and
#: that the three it joined are still there.
DEFECTIVE = "defective-retry-guidance"

#: The mark planted in the guidance entries. Nothing else in a run directory
#: could produce it, which is what makes "this text reached the re-running
#: verifier" a statement about *these* entries rather than about any text at
#: all.
MARK = "mark-7be2-defective-guidance"

#: The reason an entry is reported unmet. Its wording is varied deliberately in
#: one test below, to show no branch reads it.
UNMET = f"the retry converted two of the seventeen modules [{MARK}]"

#: What the coordinator's mismatch reason says it is about. A phrase rather
#: than the whole sentence, so rewording the reason around it costs nothing
#: and dropping the mismatch escalation reddens.
MISMATCH_PHRASE = "does not match the retry guidance in force"


@dataclass(frozen=True)
class Guidance:
    """One retry guidance, and the entries a verdict must account for.

    The entries are written here rather than derived through the coordinator's
    own reader of them: what is in force is exactly one focus and one
    preserved behaviour, and asserting that against the coordinator's own
    extraction would be asserting the implementation against itself.
    """

    focus: str
    satisfied_when: str
    preserved: str

    @property
    def document(self) -> dict:
        return {
            "current_focus": [{"focus": self.focus,
                               "satisfied_when": self.satisfied_when}],
            "preserve_behavior": [self.preserved],
            "retry_scope": ["src/"],
        }

    @property
    def entries(self) -> list[str]:
        return [self.focus, self.preserved]


#: The guidance every run below writes unless it says otherwise. Its focus
#: asks for the whole job and its satisfied_when says so, which is the shape
#: the story asks a verifier to write; whether it is honest is not this
#: harness's business, and no branch reads either string as language.
GUIDANCE = Guidance(
    focus=f"empty the list of modules awaiting conversion [{MARK}]",
    satisfied_when=f"the list names no module [{MARK}]",
    preserved=f"the conversions already made keep passing [{MARK}]",
)

#: The same guidance said differently, entry for entry. Used to show the
#: comparison is set equality over strings: the sets differ between the two
#: runs, each run's own two sets are equal, and the routing is identical.
REWORDED = Guidance(
    focus="finish the conversion of every module the inventory names",
    satisfied_when="the inventory is exhausted",
    preserved="nothing already converted regresses",
)


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
        """The stage that declares the routing table, found by the declaration
        rather than by name."""
        return next(s for s in self.workflow["stages"] if "on_failure" in s)

    @property
    def verifier_name(self) -> str:
        return self.verifier["name"]

    @property
    def routes(self) -> dict:
        return self.verifier["on_failure"]["retry_routing"]

    @property
    def categories(self) -> list[str]:
        return sorted(self.routes)

    @property
    def category(self) -> str:
        """The category the runs below recommend when they recommend one."""
        return self.categories[0]

    @property
    def destination(self) -> str:
        return self.routes[self.category]["stage"]

    @property
    def self_route_budget(self) -> int:
        """The budget the verifier's own stage declares. No number is written
        here: the self-route this story raises spends this and nothing else."""
        return self.verifier.get("max_self_routes", 0)

    @property
    def guidance_artifact(self) -> str:
        """The verifier's one conditional output: the retry guidance.

        Derived through the same reader the coordinator uses, so this module
        writes the artifact's name nowhere.
        """
        conditional = story_coordinator.conditional_artifacts(self.verifier)
        assert len(conditional) == 1, conditional
        return conditional[0]

    @property
    def verdict_artifact(self) -> str:
        required = story_coordinator.required_artifacts(self.verifier)
        assert len(required) == 1, required
        return required[0]

    @property
    def clean_clone_result(self) -> str:
        return self.verifier["clean_clone"]["result"]

    @property
    def clean_clone_stage(self) -> str:
        return self.verifier["clean_clone"]["retry_stage"]

    def unknown_category(self) -> str:
        return "not-a-" + "-or-".join(self.categories)


@pytest.fixture
def fixture(tmp_path: Path) -> Fixture:
    """A harness root carrying a workflow this repository does not ship, and a
    factory for target repositories pointed at it.

    Derived from the definition tests/test_retry_routing.py builds, with the one
    declaration this module's subject requires added to it: a self-route budget
    on the verifying stage, which is what a defective-guidance finding spends.
    Stated here rather than inherited, because a module whose subject is "a
    self-route the verifier takes on its own declared budget" must not depend on
    some other definition happening to grant one.
    """
    def grant_the_verifying_stage_a_budget(workflow: dict) -> None:
        verifier_stage_of(workflow)["max_self_routes"] = 1

    harness = probe_harness(tmp_path, PROBE_WORKFLOW,
                            grant_the_verifying_stage_a_budget)

    def build(label: str, **kwargs) -> Path:
        return build_target(tmp_path / f"target-{label}",
                            workflow=PROBE_WORKFLOW, **kwargs)

    return Fixture(
        harness=harness,
        workflow=conftest.shipped_workflow(harness, PROBE_WORKFLOW),
        ceiling=harness_config.load_rules(harness)["max_retries"],
        build=build,
    )


def test_the_workflow_these_runs_are_driven_against_still_has_a_subject(fixture):
    """Every derivation above, stated so a fixture change reddens here first.

    A module whose subject is "a self-route the verifier takes on its own
    declared budget" is worth nothing if the verifier declares no budget, and
    the runs below would then quietly assert an escalation rather than a
    self-route. The ceiling has to be above one too, or "the budget was
    genuinely available" cannot be shown at all.

    Asked of the definition these runs are actually driven against — the probe
    this module builds — rather than of the one this repository deploys. That
    is the whole of story-048's point: the deployment is free to grant or
    withdraw a budget without moving this module, and the declaration this
    module *depends* on is stated where it is depended on.
    """
    verifier = verifier_stage_of(fixture.workflow)

    assert verifier.get("max_self_routes", 0) >= 1, (
        "the verifier declares no self-route budget, so the defective-guidance "
        "finding has nothing to spend and this module has no subject")
    assert story_coordinator.conditional_artifacts(verifier), (
        "the verifier declares no conditional artifact, so there is no "
        "guidance for a verdict to be in force for")
    assert fixture.ceiling >= 2, (
        "the retry ceiling leaves no room to show a self-route left budget "
        "unspent after a legitimate retry")


# --------------------------------------------------------------------------
# The fake runner
#
# Every stage writes the artifacts its own declaration in the loaded workflow
# requires, never a list written here. The verifier's verdicts are scripted as
# functions of the guidance in force at the moment the verdict is written,
# which is what a real verifier answers, and the guidance in force is read off
# state.json through the shared reader rather than re-derived.
# --------------------------------------------------------------------------


def guidance_in_force_now(run_dir: Path) -> list[str]:
    """The guidance in force at this instant, empty before a run has state.

    The shared reader, guarded for the one moment it cannot answer: the first
    stage of a run is entered before any state has been saved.
    """
    if not (run_dir / "state.json").is_file():
        return []
    return conftest.guidance_in_force(run_dir)


class Runner:
    """A fake agent runner whose verifier answers the guidance in force.

    It records, at the entry to every invocation, the guidance the run's own
    `state.json` carried — which is how "a reroute left no guidance in force"
    is checked as a fact observed during the run rather than inferred from
    what the following verification did.
    """

    def __init__(self, target_root: Path, fixture: Fixture, verdicts: list,
                 guidance: Guidance = GUIDANCE):
        self.target_root = target_root
        self.fixture = fixture
        self.run_dir = run_dir_of(target_root)
        self.verdicts = list(verdicts)
        self.guidance = guidance
        self.calls: list[str] = []
        self.prompts: list[tuple[str, str]] = []
        #: (stage, the guidance in force state.json held at this entry)
        self.in_force: list[tuple[str, list[str]]] = []

    def _declaration(self, stage: str) -> dict:
        return next(s for s in self.fixture.workflow["stages"]
                    if s["name"] == stage)

    def _write(self, artifact: str, stage: str, call: int, verdict) -> None:
        path = self.run_dir / artifact
        if verdict is not None and artifact == self.fixture.verdict_artifact:
            write_json(path, verdict)
        elif artifact.endswith("changed-files.json"):
            write_json(path, {"modified": ["src/app.py"], "created": [],
                              "deleted": []})
        elif artifact.endswith(".json"):
            write_json(path, {"status": "passed", "tests_written": 1,
                              "tests_run": 1, "tests_passed": 1,
                              "tests_failed": 0, "failures": []})
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{artifact} written by {stage} call {call}.\n",
                            encoding="utf-8")

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None):
        self.calls.append(stage)
        self.prompts.append((stage, prompt))
        entries = guidance_in_force_now(self.run_dir)
        self.in_force.append((stage, entries))
        call = self.calls.count(stage)

        verdict = None
        if stage == self.fixture.verifier_name:
            scripted = self.verdicts[min(call - 1, len(self.verdicts) - 1)]
            verdict = scripted(entries) if callable(scripted) else scripted

        # The target's working tree has to move, or the run has nothing to
        # commit and nothing for the clean-clone check to see.
        if stage == self.fixture.stage_names[0]:
            source = self.target_root / "src" / "app.py"
            source.write_text(f"print('attempt {call}')\n", encoding="utf-8")

        for artifact in story_coordinator.required_artifacts(
                self._declaration(stage)):
            self._write(artifact, stage, call, verdict)

        if verdict is not None and verdict.get("status") == "failed":
            write_json(self.run_dir / self.fixture.guidance_artifact,
                       self.guidance.document)
        return AgentResult(ok=True, result_text=f"{stage} done")


def run(fixture: Fixture, label: str, verdicts: list, *,
        guidance: Guidance = GUIDANCE, coordinator=story_coordinator,
        **target_kwargs) -> tuple[Path, Runner, int]:
    target = fixture.build(label, **target_kwargs)
    runner = Runner(target, fixture, verdicts, guidance)
    code = coordinator.run_story(STORY_ID, fixture.harness, target, runner)
    return target, runner, code


# --------------------------------------------------------------------------
# The verdicts, as functions of the guidance in force
# --------------------------------------------------------------------------


def verdict(fixture: Fixture, *, retry: bool = True, target=None, **extra) -> dict:
    """A failed verdict, built on the one the routing module already shares."""
    named = fixture.category if target is None else target
    return dict(failing(named, retry=retry), **extra)


def answering(base: dict, *, unmet: str | None):
    """`base`, echoing whatever guidance is in force when it is written.

    `unmet=None` reports every entry met, which is the contradiction; a string
    reports every entry unmet, which is ordinary under-delivery. With no
    guidance in force the verdict is returned untouched, carrying no
    `guidance_outcomes` key at all.
    """
    def build(entries: list[str]) -> dict:
        if not entries:
            return base
        return dict(base,
                    guidance_outcomes=conftest.echo_guidance(entries, unmet=unmet))
    return build


def echoing(base: dict, outcomes):
    """`base` echoing exactly what `outcomes` makes of the entries in force."""
    def build(entries: list[str]) -> dict:
        return dict(base, guidance_outcomes=outcomes(entries))
    return build


def under_delivery(fixture: Fixture, **kwargs):
    return answering(verdict(fixture, **kwargs), unmet=UNMET)


def fully_met(fixture: Fixture, **kwargs):
    return answering(verdict(fixture, **kwargs), unmet=None)


def self_route_records(target: Path) -> list[dict]:
    """Every self-route evidence artifact the run left, by filename order."""
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(run_dir_of(target).glob("self-route-*.json"))]


def self_route_names(target: Path) -> list[str]:
    return sorted(p.name for p in run_dir_of(target).glob("self-route-*.json"))


def prompt_names(target: Path) -> list[str]:
    return sorted(p.name for p in run_dir_of(target).glob("prompt-*.md"))


def verification_iterations(target: Path) -> list[str]:
    archive = run_dir_of(target) / "verification"
    return sorted(p.name for p in archive.glob("*.json")) if archive.is_dir() else []


def in_force_at(runner: Runner, stage: str, nth: int) -> list[str]:
    """The guidance in force at the nth (1-based) entry to `stage`."""
    seen = [entries for name, entries in runner.in_force if name == stage]
    assert len(seen) >= nth, (stage, nth, runner.calls)
    return seen[nth - 1]


# --------------------------------------------------------------------------
# 1. The guidance in force is recorded on the state when a retry is routed
# --------------------------------------------------------------------------


def test_a_retry_routed_with_fresh_guidance_records_its_entries_on_the_state(
    fixture,
):
    """The routing input, read off `state.json` rather than out of history.

    Both what the state carries and what the stage that ran next saw, because
    the state is overwritten as the run goes on and the second verification is
    the moment the value is actually used.
    """
    target, runner, code = run(
        fixture, "in-force",
        [under_delivery(fixture), under_delivery(fixture), PASS])

    assert code == 0
    assert in_force_at(runner, fixture.verifier_name, 2) == GUIDANCE.entries
    assert in_force_at(runner, fixture.destination, 2) == GUIDANCE.entries
    # Nothing beyond the two entries, and nothing that only history knows.
    assert in_force_at(runner, fixture.verifier_name, 1) == []


def test_the_recorded_entries_are_reconstructable_from_the_state_file_alone(
    fixture,
):
    """No routing decision reads the retry history or the attempts archive.

    Stated as the state file answering on its own: the entries are there, and
    they are there while `retry-history.json` and `attempts/` also exist, so
    the assertion is about which of the three the value was taken from being
    irrecoverable — the state alone carries it in a form a resume can read.
    """
    target, _, code = run(
        fixture, "state-alone",
        [under_delivery(fixture), under_delivery(fixture), under_delivery(fixture)])

    assert code == 2
    assert (run_dir_of(target) / "retry-history.json").is_file()
    assert attempt_dirs(target) != []
    assert conftest.guidance_in_force(run_dir_of(target)) == GUIDANCE.entries


#: A verification failure, a passing verdict whose clean-clone check fails,
#: and a failed verdict answering nothing. Guidance is in force across the
#: first reroute and must not survive the second: the clean-clone reroute
#: follows a passing verdict, which writes no guidance at all. Written once
#: because two tests read two different halves of the same run.
def clean_clone_reroute(fixture) -> list:
    return [under_delivery(fixture), PASS, verdict(fixture)]


def test_a_reroute_that_wrote_no_guidance_clears_what_was_in_force(fixture):
    """Read off `state.json` on both sides of the clean-clone reroute.

    The before is what makes the after worth anything: the same reader, at the
    same kind of point in the same run, reports the entries at the stage the
    *verification* reroute sent execution to and reports nothing at the stage
    the *clean-clone* reroute sent it to. An emptiness observed with no
    populated reading beside it would pass equally if the reader had stopped
    looking.
    """
    target, runner, code = run(fixture, "clean-clone-clears",
                               clean_clone_reroute(fixture),
                               test_command="sh -c 'exit 1'")

    assert code == 2
    # The clean-clone check really did run, really did fail, and really did
    # reroute to the stage its own declaration names.
    assert (run_dir_of(target) / fixture.clean_clone_result).is_file()
    assert [entry["retry_stage"] for entry in routed_retries(target)] == \
        [fixture.destination, fixture.clean_clone_stage]

    assert in_force_at(runner, fixture.destination, 2) == GUIDANCE.entries
    assert in_force_at(runner, fixture.clean_clone_stage, 2) == []
    assert in_force_at(runner, fixture.verifier_name, 3) == []
    assert conftest.guidance_in_force(run_dir_of(target)) == []


def test_the_verification_after_that_reroute_is_not_subjected_to_the_check(
    fixture,
):
    """The check is not applied where no guidance was in force.

    The third verdict is the shape that escalates on mismatch — failed,
    recommending a routable retry, carrying no `guidance_outcomes` at all. It
    is read as an ordinary failed verdict instead and reaches the ceiling,
    which is a different escalation with a different recorded reason.
    """
    target, _, code = run(fixture, "not-checked",
                          clean_clone_reroute(fixture),
                          test_command="sh -c 'exit 1'")

    assert code == 2
    reason = escalation_of(target)["retry_reason"]
    assert MISMATCH_PHRASE not in reason
    assert str(fixture.ceiling) in reason
    assert MISMATCH_PHRASE not in json.dumps(history_of(target))
    assert state_of(target)["retry_count"] == fixture.ceiling


def test_the_same_verdict_on_a_guided_attempt_does_escalate_on_the_mismatch(
    fixture,
):
    """The control for the assertion above.

    The identical verdict — failed, recommending a routable retry, carrying no
    `guidance_outcomes` — on an attempt that *was* routed with guidance. It
    escalates naming the mismatch, so the untouched routing above is a
    statement about the guidance in force rather than about a check that has
    stopped looking.
    """
    target, _, code = run(
        fixture, "guided-mismatch",
        [under_delivery(fixture), verdict(fixture)])

    assert code == 2
    assert MISMATCH_PHRASE in escalation_of(target)["retry_reason"]


# --------------------------------------------------------------------------
# 2. Under-delivery routes exactly as a failed verdict always has
#
# The distinction the story lives or dies on: this and the section below it
# differ in one field of one verdict.
# --------------------------------------------------------------------------


def test_a_verdict_reporting_an_entry_unmet_routes_as_a_failed_verdict_does(
    fixture,
):
    """Every entry accounted for, every one unmet: ordinary under-delivery.

    Two retries against a ceiling of two, then escalation at the ceiling —
    which is what a failed verdict recommending a routable retry has always
    done, and it is asserted against the same route and count as the runs in
    section 6 that carry no guidance at all.
    """
    target, runner, code = run(
        fixture, "under-delivery", [under_delivery(fixture)])

    assert code == 2
    assert state_of(target)["retry_count"] == fixture.ceiling
    assert attempt_dirs(target) == [f"attempt-{n}"
                                    for n in range(1, fixture.ceiling + 1)]
    assert [entry["retry_stage"] for entry in routed_retries(target)] == \
        [fixture.destination] * fixture.ceiling
    assert len(retry_records_of(target)) == fixture.ceiling
    assert self_route_names(target) == []
    assert str(fixture.ceiling) in escalation_of(target)["retry_reason"]


def test_one_entry_unmet_among_several_met_is_still_under_delivery(fixture):
    """Presence on one entry is the whole signal; the rest being met is not.

    The verdict reports the preserved behaviour met and the focus unmet, which
    is exactly the retry that did some of what it was asked. It reroutes.
    """
    def outcomes(entries: list[str]) -> list[dict]:
        return [{"guidance": entry} if entry != GUIDANCE.focus
                else {"guidance": entry, "unmet": UNMET}
                for entry in entries]

    target, _, code = run(
        fixture, "one-unmet",
        [echoing(verdict(fixture), outcomes), PASS])

    assert code == 0
    assert state_of(target)["retry_count"] == 1
    assert attempt_dirs(target) == ["attempt-1"]
    assert self_route_names(target) == []
    assert [entry["retry_stage"] for entry in routed_retries(target)] == \
        [fixture.destination]


def test_dropping_the_one_unmet_marker_turns_the_same_run_into_a_self_route(
    fixture,
):
    """The control for the two assertions above, and the story's own claim.

    The same fixture, the same guidance, the same verdict — with `unmet`
    removed from the single entry that carried it. It stops routing and
    self-routes instead, so "under-delivery routes as it always has" is a
    statement about the marker rather than about a branch that never fires.
    """
    target, _, code = run(
        fixture, "unmet-dropped",
        [under_delivery(fixture), fully_met(fixture), PASS])

    assert code == 0
    assert [record["failure"] for record in self_route_records(target)] == \
        [DEFECTIVE]
    assert state_of(target)["retry_count"] == 1


# --------------------------------------------------------------------------
# 3. A fully-met guidance on a failed verdict self-routes the verifier
# --------------------------------------------------------------------------


@pytest.fixture
def defective(fixture) -> tuple[Path, Runner, int]:
    """One legitimate retry, then a verdict that reports its guidance met in
    full and fails the work anyway, then a passing verdict.

    The legitimate retry is what puts guidance in force and what leaves the
    retry budget partly spent, so "the count did not move" is measured against
    a count that had somewhere to move to.
    """
    return run(fixture, "defective",
               [under_delivery(fixture), fully_met(fixture), PASS])


def test_a_fully_met_guidance_on_a_failed_verdict_spends_no_retry(defective,
                                                                  fixture):
    """The count is unmoved and the budget was genuinely available.

    Both halves, because "unmoved" alone passes just as happily when the
    ceiling had already been reached and the run had nothing left to spend.
    """
    target, _, code = defective

    assert code == 0
    state = state_of(target)
    assert state["retry_count"] == 1
    assert state["retry_count"] < fixture.ceiling
    assert len(retry_records_of(target)) == 1


def test_no_attempt_directory_is_written_for_the_self_routed_verification(
    defective, fixture,
):
    """Read by listing the run directory, not by trusting the path taken.

    The only archive is the one the earlier legitimate retry wrote, and it
    carries the attempt number that retry ended.
    """
    target, _, _ = defective

    assert attempt_dirs(target) == ["attempt-1"]
    assert (run_dir_of(target) / "attempts" / "attempt-1").is_dir()
    assert not (run_dir_of(target) / "attempts" / "attempt-2").exists()


def test_the_self_route_record_on_disk_names_the_defective_guidance_failure(
    defective, fixture,
):
    """The evidence the coordinator wrote, read as the run left it.

    The filename is compared against the one the harness's own name-builder
    produces for that stage, attempt and try, so this module spells no
    self-route filename of its own.
    """
    target, _, _ = defective
    records = self_route_records(target)

    assert len(records) == 1
    record = records[0]
    assert record["stage"] == fixture.verifier_name
    assert record["attempt"] == 2
    assert record["try"] == 1
    assert record["failure"] == DEFECTIVE
    assert self_route_names(target) == [
        story_coordinator.self_route_result_file(fixture.verifier_name, 2, 1)]
    # The artifacts the contradiction is between, named off the stage's own
    # declarations rather than written here.
    assert set(record["artifacts"]) == {fixture.guidance_artifact,
                                        fixture.verdict_artifact}
    assert schema_validator.validate(
        record, schema_validator.load_schema("self-route-result")) == []


def test_the_run_records_a_self_route_and_no_retry_for_that_verification(
    defective,
):
    """The history says which of the two decisions was taken."""
    target, _, _ = defective

    assert len(events(target, "self-routed")) == 1
    assert len(routed_retries(target)) == 1
    assert events(target, "self-routed")[0]["stage"]
    assert events(target, "self-routed")[0]["retry_reason"]


def test_the_verifier_ran_again_and_no_other_stage_ran_between(defective,
                                                               fixture):
    """A self-route runs the stage again *in place*."""
    target, runner, _ = defective
    verifier = fixture.verifier_name

    positions = [i for i, stage in enumerate(runner.calls) if stage == verifier]
    assert len(positions) == 3
    # The second and third verifier calls are adjacent: the self-route put the
    # verifier straight back, with nothing in between.
    assert positions[2] == positions[1] + 1


def test_disabling_the_branch_makes_both_absences_report(fixture, tmp_path):
    """The control for "no retry consumed" and "no attempt archive written".

    One coordinator, one mutation: the branch that reads the fully-met
    comparison is disabled. The identical verdict — recommending a retry and
    naming a routable category, so the path below can take it — then falls
    through to the routing path, and both absences above are present.
    """
    module = load_mutant(
        COORDINATOR_PATH,
        [("elif state.guidance_in_force and not comparison.unmet:",
          "elif False and state.guidance_in_force and not comparison.unmet:")],
        name="mutant_coordinator_without_the_defective_guidance_branch",
        tmp_path=tmp_path)

    target, _, code = run(
        fixture, "branch-disabled",
        [under_delivery(fixture), fully_met(fixture), PASS],
        coordinator=module)

    assert code == 0
    assert state_of(target)["retry_count"] == 2
    assert attempt_dirs(target) == ["attempt-1", "attempt-2"]
    assert self_route_names(target) == []


# --------------------------------------------------------------------------
# 4. The statement reaches the re-running verifier
# --------------------------------------------------------------------------


def test_the_statement_names_the_met_entries_and_the_verdict_that_failed(
    defective, fixture,
):
    """The planted mark, found in the record the coordinator wrote."""
    target, _, _ = defective
    statement = self_route_records(target)[0]["statement"]

    for entry in GUIDANCE.entries:
        assert entry in statement, entry
    assert MARK in statement
    assert fixture.verdict_artifact in statement
    assert fixture.guidance_artifact in statement


def test_the_statement_reaches_the_re_running_verifier_in_its_rendered_prompt(
    defective, fixture,
):
    """Read out of the prompt the run actually rendered for the re-run.

    The filename comes from the harness's own name-builder for that stage,
    attempt and try, so nothing here spells a prompt filename.
    """
    target, _, _ = defective
    name = story_coordinator.prompt_file(fixture.verifier_name, 2, 1)
    rendered = (run_dir_of(target) / name).read_text(encoding="utf-8")

    assert MARK in rendered
    for entry in GUIDANCE.entries:
        assert entry in rendered, entry
    assert PLACEHOLDER.search(rendered) is None


def test_the_prompt_of_the_invocation_that_failed_is_not_overwritten(defective,
                                                                     fixture):
    """The attempt number in every rendered prompt filename is unmoved.

    Both prompts for that attempt exist — the one the failed invocation was
    given and the one the re-run was — and only the re-run's carries the try
    suffix. The mark appears in the second and not the first, which is what
    says they are two renderings rather than one file read twice.
    """
    target, _, _ = defective
    plain = story_coordinator.prompt_file(fixture.verifier_name, 2)
    retried = story_coordinator.prompt_file(fixture.verifier_name, 2, 1)

    assert plain in prompt_names(target)
    assert retried in prompt_names(target)
    assert MARK not in (run_dir_of(target) / plain).read_text(encoding="utf-8")
    # No prompt carries an attempt number beyond the one retry that was taken.
    suffixes = {name.rsplit("-attempt-", 1)[1].split("-")[0].split(".")[0]
                for name in prompt_names(target)}
    assert suffixes == {"1", "2"}


def test_the_ordinary_self_route_prompt_names_carry_no_mark(fixture):
    """The control for the search above.

    A run with no defective-guidance finding renders no try-suffixed verifier
    prompt at all, so finding one above is a statement about this branch.
    """
    target, _, code = run(fixture, "no-self-route",
                          [under_delivery(fixture), PASS])

    assert code == 0
    assert [name for name in prompt_names(target) if "-try-" in name] == []


def test_two_verdicts_within_one_attempt_write_two_verification_iterations(
    defective,
):
    """Two verdicts were written, so two iterations were archived — within the
    one attempt, whose number moved for neither."""
    target, _, _ = defective

    assert verification_iterations(target) == [
        "iteration-1.json", "iteration-2.json", "iteration-3.json"]
    assert state_of(target)["verification_iterations"] == 3
    assert state_of(target)["retry_count"] == 1


# --------------------------------------------------------------------------
# 5. The exhausted budget escalates, and still consumes no retry
# --------------------------------------------------------------------------


def test_a_defective_guidance_with_no_budget_left_escalates_naming_both(
    fixture,
):
    """The budget is the verifier's own declared one, spent by the first
    finding; the second has nothing left and escalates.

    The recorded reason names both halves — the guidance that sanctioned the
    outcome and the exhausted self-route budget — because a developer reading
    the escalation has to learn what failed and why the stage stopped trying.
    """
    verdicts = [under_delivery(fixture)]
    verdicts += [fully_met(fixture)] * (fixture.self_route_budget + 1)
    target, _, code = run(fixture, "budget-spent", verdicts)

    assert code == 2
    reason = escalation_of(target)["retry_reason"]
    assert "sanctioned the outcome it failed" in reason
    assert "self-route budget" in reason
    assert str(fixture.self_route_budget) in reason
    assert MARK in reason
    assert story_coordinator.escalation_reason(run_dir_of(target)) == reason
    assert MARK in summary_of(target)


def test_that_escalation_still_consumed_no_retry_and_archived_nothing_new(
    fixture,
):
    verdicts = [under_delivery(fixture)]
    verdicts += [fully_met(fixture)] * (fixture.self_route_budget + 1)
    target, _, code = run(fixture, "budget-spent-books", verdicts)

    assert code == 2
    assert state_of(target)["retry_count"] == 1
    assert state_of(target)["retry_count"] < fixture.ceiling
    assert attempt_dirs(target) == ["attempt-1"]
    assert len(retry_records_of(target)) == 1
    assert len(self_route_records(target)) == fixture.self_route_budget


# --------------------------------------------------------------------------
# 6. A mismatched guidance_outcomes escalates naming what did not match
# --------------------------------------------------------------------------


def dropped(entries: list[str]) -> list[dict]:
    """Every entry but the first, each reported unmet."""
    return conftest.echo_guidance(entries[1:], unmet=UNMET)


def added(entries: list[str]) -> list[dict]:
    """Every entry, plus one the guidance in force does not carry."""
    return conftest.echo_guidance(list(entries) + [INVENTED], unmet=UNMET)


def misquoted(entries: list[str]) -> list[dict]:
    """Every entry, with the first paraphrased rather than echoed."""
    return conftest.echo_guidance(
        [f"roughly: {entries[0]}"] + list(entries[1:]), unmet=UNMET)


def none_at_all(entries: list[str]) -> list[dict]:
    """An empty array: present, and accounting for nothing."""
    return []


#: An entry no guidance below carries, built so it cannot collide with one.
INVENTED = f"an entry the guidance never carried [{MARK}]"


MISMATCHES = [
    ("absent", None),
    ("empty", none_at_all),
    ("missing-an-entry", dropped),
    ("an-extra-entry", added),
    ("a-misquoted-entry", misquoted),
]


@pytest.mark.parametrize("label,outcomes", MISMATCHES,
                         ids=[label for label, _ in MISMATCHES])
def test_a_mismatched_guidance_outcomes_escalates_spending_nothing(
    fixture, label, outcomes,
):
    """Every shape of mismatch the story names, each read from the record.

    `absent` carries no key at all and the rest carry a wrong one; all five
    escalate, and none is read as everything met or as nothing met — no
    self-route was taken and no retry was spent for any of them.
    """
    second = (verdict(fixture) if outcomes is None
              else echoing(verdict(fixture), outcomes))
    target, _, code = run(fixture, f"mismatch-{label}",
                          [under_delivery(fixture), second])

    assert code == 2, label
    entry = escalation_of(target)
    assert MISMATCH_PHRASE in entry["retry_reason"], label
    assert entry["retry_decision"] == "escalate"
    assert state_of(target)["retry_count"] == 1
    assert attempt_dirs(target) == ["attempt-1"]
    assert self_route_names(target) == []


def test_the_absent_case_says_so_and_the_wrong_case_names_what_was_wrong(
    fixture,
):
    """The two are distinguishable in the record, not only from a met set and
    an unmet one but from each other.

    Read from the recorded reason rather than the decision: all five
    escalations above record the same decision, and a developer has to be able
    to tell a verdict that answered nothing from one that answered wrongly.
    """
    absent, _, _ = run(fixture, "reason-absent",
                       [under_delivery(fixture), verdict(fixture)])
    wrong, _, _ = run(fixture, "reason-wrong",
                      [under_delivery(fixture),
                       echoing(verdict(fixture), misquoted)])

    absent_reason = story_coordinator.escalation_reason(run_dir_of(absent))
    wrong_reason = story_coordinator.escalation_reason(run_dir_of(wrong))

    assert "carried no guidance_outcomes at all" in absent_reason
    assert "carried no guidance_outcomes at all" not in wrong_reason
    # The misquote shows up as both directions of the difference, named.
    assert "does not account for" in wrong_reason
    assert GUIDANCE.focus in wrong_reason
    assert f"roughly: {GUIDANCE.focus}" in wrong_reason
    assert absent_reason != wrong_reason


def test_the_extra_entry_case_names_the_entry_the_guidance_does_not_carry(
    fixture,
):
    target, _, code = run(fixture, "reason-extra",
                          [under_delivery(fixture),
                           echoing(verdict(fixture), added)])

    assert code == 2
    reason = story_coordinator.escalation_reason(run_dir_of(target))
    assert INVENTED in reason
    assert "does not carry" in reason


def test_a_mismatch_reason_reads_as_neither_a_met_set_nor_an_unmet_one(fixture):
    """The control for the four assertions above.

    The three recorded reasons a run with guidance in force can end with,
    produced by three runs that differ only in the second verdict, and asserted
    distinct. A mismatch reported as "everything met" would read as the
    defective-guidance reason; reported as "nothing met" it would read as the
    ceiling.
    """
    mismatch, _, _ = run(fixture, "three-mismatch",
                         [under_delivery(fixture), verdict(fixture)])
    met, _, _ = run(fixture, "three-met",
                    [under_delivery(fixture)]
                    + [fully_met(fixture)] * (fixture.self_route_budget + 1))
    unmet, _, _ = run(fixture, "three-unmet", [under_delivery(fixture)])

    reasons = [story_coordinator.escalation_reason(run_dir_of(root))
               for root in (mismatch, met, unmet)]
    assert len(set(reasons)) == 3
    assert MISMATCH_PHRASE in reasons[0]
    assert MISMATCH_PHRASE not in reasons[1]
    assert MISMATCH_PHRASE not in reasons[2]


# --------------------------------------------------------------------------
# 7. The comparison reads no string as language
# --------------------------------------------------------------------------


def routing_signature(target: Path) -> dict:
    """What a run decided, with none of the words any of it was said in."""
    return {
        "retry_count": state_of(target)["retry_count"],
        "attempts": attempt_dirs(target),
        "routes": [entry.get("retry_stage") for entry in routed_retries(target)],
        "self_routes": [record["failure"] for record in self_route_records(target)],
        "decisions": [entry["retry_decision"] for entry in history_of(target)
                      if entry.get("retry_decision")],
        "status": state_of(target)["status"],
    }


@pytest.mark.parametrize("build,label", [
    (under_delivery, "under-delivery"),
    (fully_met, "fully-met"),
])
def test_rewording_every_string_leaves_the_routing_unchanged(fixture, build,
                                                             label):
    """The whole comparison is set equality over strings the verifier echoed.

    Two runs whose guidance entries, satisfied_when conditions and unmet
    reasons share no wording at all — each run's own two sets equal, the two
    runs' sets disjoint. If any branch read what a string said, the two would
    route differently.
    """
    plain, _, plain_code = run(fixture, f"wording-plain-{label}",
                               [build(fixture), build(fixture), PASS],
                               guidance=GUIDANCE)
    other, _, other_code = run(fixture, f"wording-other-{label}",
                               [build(fixture), build(fixture), PASS],
                               guidance=REWORDED)

    assert not set(GUIDANCE.entries) & set(REWORDED.entries)
    assert other_code == plain_code
    assert routing_signature(other) == routing_signature(plain)


def test_varying_only_the_unmet_reason_leaves_the_routing_unchanged(fixture):
    """The same, for the other string the story says nothing reads.

    The guidance is held identical and only the reasons differ, so the sets
    compared are the same in both runs and only the words inside `unmet`
    change.
    """
    def with_reason(text: str):
        return answering(verdict(fixture), unmet=text)

    first, _, _ = run(fixture, "reason-a",
                      [with_reason("it ran out of time"),
                       with_reason("it ran out of time"), PASS])
    second, _, _ = run(fixture, "reason-b",
                       [with_reason("nothing was attempted at all"),
                        with_reason("nothing was attempted at all"), PASS])

    assert routing_signature(second) == routing_signature(first)


# --------------------------------------------------------------------------
# 8. Ordering: misdirection is ruled out before the confident exit
# --------------------------------------------------------------------------


#: The field story-049 added, named because the ordering is against it.
UNFINISHABLE = "unfinishable_by_retry"
UNFINISHABLE_JUDGEMENT = (
    f"fifteen modules remain and this attempt converted two, so retrying "
    f"cannot close the gap [{MARK}]"
)


def test_a_verdict_carrying_both_signals_self_routes_rather_than_escalating(
    fixture,
):
    """The ordering, observed by which of the two the run acted on.

    A fast exit paired with guidance that still sanctions partial results is
    worse than what came before it, so the misdirection is ruled out first:
    the verifier runs again in place, and the run goes on rather than ending
    on a confident judgement built on the misdirection.
    """
    both = fully_met(fixture, retry=False)
    target, _, code = run(
        fixture, "both-signals",
        [under_delivery(fixture),
         lambda entries: dict(both(entries), **{UNFINISHABLE: UNFINISHABLE_JUDGEMENT}),
         PASS])

    assert code == 0
    assert [record["failure"] for record in self_route_records(target)] == \
        [DEFECTIVE]
    assert state_of(target)["status"] == "completed"
    assert not (run_dir_of(target) / "escalation-summary.md").exists()


def test_the_same_verdict_without_the_met_guidance_does_end_the_run(fixture):
    """The control for the ordering above.

    The identical verdict, differing only in reporting its guidance entries
    unmet rather than met. The unfinishable judgement is then reached and the
    run ends on it — so the self-route above is a statement about which branch
    came first rather than about the judgement being ignored.
    """
    under = under_delivery(fixture, retry=False)
    target, _, code = run(
        fixture, "both-signals-unmet",
        [under_delivery(fixture),
         lambda entries: dict(under(entries), **{UNFINISHABLE: UNFINISHABLE_JUDGEMENT})])

    assert code == 2
    assert self_route_names(target) == []
    assert story_coordinator.escalation_reason(run_dir_of(target)) == \
        UNFINISHABLE_JUDGEMENT


def test_the_unroutable_escalations_still_come_first(fixture):
    """The two escalations above this check report exactly as they did.

    A verdict recommending a retry to a category nothing defines, carrying a
    fully-met guidance_outcomes that would otherwise self-route. It escalates
    naming the categories, so how an unroutable target is reported is
    unchanged by the branches placed below it.
    """
    unknown = fixture.unknown_category()
    target, _, code = run(
        fixture, "unroutable-first",
        [under_delivery(fixture), fully_met(fixture, target=unknown)])

    assert code == 2
    message = escalation_of(target)["message"]
    assert unknown in message
    for category in fixture.categories:
        assert category in message, category
    assert self_route_names(target) == []
    assert state_of(target)["retry_count"] == 1


# --------------------------------------------------------------------------
# 9. A first verification has no guidance in force and routes as it did
# --------------------------------------------------------------------------


@pytest.mark.parametrize("outcomes,label", [
    (None, "carrying none"),
    (lambda entries: [{"guidance": INVENTED}], "carrying an invented entry"),
    (none_at_all, "carrying an empty array"),
])
def test_a_first_verification_routes_as_it_does_today(fixture, outcomes, label):
    """Whatever `guidance_outcomes` it carries or omits.

    Nothing was in force, so there is nothing to compare against and nothing
    the coordinator could read as a mismatch — including an echo of an entry
    no guidance ever carried, which on a guided attempt escalates.
    """
    first = (verdict(fixture) if outcomes is None
             else echoing(verdict(fixture), outcomes))
    target, runner, code = run(fixture, f"first-{label.replace(' ', '-')}",
                               [first, PASS])

    assert code == 0, label
    assert in_force_at(runner, fixture.verifier_name, 1) == []
    assert state_of(target)["retry_count"] == 1
    assert attempt_dirs(target) == ["attempt-1"]
    assert self_route_names(target) == []
    assert [entry["retry_stage"] for entry in routed_retries(target)] == \
        [fixture.destination]


def test_the_invented_entry_that_passes_first_does_escalate_when_guided(
    fixture,
):
    """The control for the parametrization above.

    The same echo — one entry no guidance carries — on an attempt routed with
    guidance. It escalates as a mismatch, so the first verification's untouched
    routing is a statement about there being nothing in force.
    """
    target, _, code = run(
        fixture, "invented-when-guided",
        [under_delivery(fixture),
         echoing(verdict(fixture), lambda entries: [{"guidance": INVENTED}])])

    assert code == 2
    assert MISMATCH_PHRASE in escalation_of(target)["retry_reason"]


# --------------------------------------------------------------------------
# 10. Every routing behaviour outside this check is preserved exactly
# --------------------------------------------------------------------------


def test_a_passing_verdict_still_runs_the_clean_clone_check(fixture):
    target, _, code = run(fixture, "preserved-passing", [PASS])

    assert code == 0
    assert (run_dir_of(target) / fixture.clean_clone_result).is_file()
    assert any(entry["event"].startswith("clean-clone")
               for entry in history_of(target))
    assert state_of(target)["status"] == "completed"


def test_a_recommended_retry_below_the_ceiling_still_reroutes(fixture):
    for category in fixture.categories:
        destination = fixture.routes[category]["stage"]
        target, _, code = run(fixture, f"preserved-reroute-{category}",
                              [verdict(fixture, target=category), PASS])

        assert code == 0, category
        assert [entry["retry_stage"] for entry in routed_retries(target)] == \
            [destination]
        assert [entry["retry_category"] for entry in routed_retries(target)] == \
            [category]


def test_a_recommended_retry_naming_no_category_still_escalates(fixture):
    target, _, code = run(fixture, "preserved-no-category",
                          [verdict(fixture, target=OMITTED)])

    assert code == 2
    message = escalation_of(target)["message"]
    for category in fixture.categories:
        assert category in message, category
    assert state_of(target)["retry_count"] == 0
    assert attempt_dirs(target) == []


def test_an_unknown_category_still_escalates_naming_the_defined_ones(fixture):
    unknown = fixture.unknown_category()
    target, _, code = run(fixture, "preserved-unknown", [verdict(fixture,
                                                                target=unknown)])

    assert code == 2
    message = escalation_of(target)["message"]
    assert unknown in message
    for category in fixture.categories:
        assert category in message, category
    assert state_of(target)["retry_count"] == 0


def test_an_exhausted_retry_budget_still_escalates_naming_the_ceiling(fixture):
    target, _, code = run(fixture, "preserved-exhausted",
                          [under_delivery(fixture)])

    assert code == 2
    assert state_of(target)["retry_count"] == fixture.ceiling
    assert str(fixture.ceiling) in escalation_of(target)["retry_reason"]
    assert len(events(target, "escalated")) == 1


def test_unfinishable_by_retry_still_escalates_on_first_sighting(fixture):
    """No guidance is in force at a first verification, so the judgement is
    reached exactly as it was before these branches existed."""
    target, _, code = run(
        fixture, "preserved-unfinishable",
        [dict(failing(retry=False), **{UNFINISHABLE: UNFINISHABLE_JUDGEMENT})])

    assert code == 2
    assert story_coordinator.escalation_reason(run_dir_of(target)) == \
        UNFINISHABLE_JUDGEMENT
    assert state_of(target)["retry_count"] == 0
    assert attempt_dirs(target) == []


def test_a_failed_verdict_that_recommends_no_retry_still_escalates(fixture):
    target, _, code = run(fixture, "preserved-declined",
                          [verdict(fixture, retry=False, target=OMITTED)])

    assert code == 2
    assert state_of(target)["retry_count"] == 0
    assert attempt_dirs(target) == []
    assert self_route_names(target) == []


# --------------------------------------------------------------------------
# 11. The schemas — the shipped declarations are the subject here
# --------------------------------------------------------------------------


def current_focus_items(schema: dict) -> dict:
    return schema["properties"]["current_focus"]["items"]


def test_the_guidance_schema_declares_current_focus_as_objects(fixture):
    schema = schema_validator.load_schema("retry-guidance")
    items = current_focus_items(schema)

    assert items["type"] == "object"
    assert sorted(items["required"]) == ["focus", "satisfied_when"]
    assert items["properties"]["satisfied_when"]["type"] == "string"
    assert items["properties"]["satisfied_when"]["description"].strip()


def test_the_validator_accepts_the_new_shape_and_rejects_the_old_one():
    """The control for the declaration above: the schema is enforced rather
    than merely worded, and an entry without its condition is refused."""
    schema = schema_validator.load_schema("retry-guidance")
    document = GUIDANCE.document

    assert schema_validator.validate(document, schema) == []
    without = json.loads(json.dumps(document))
    without["current_focus"][0].pop("satisfied_when")
    assert schema_validator.validate(without, schema) != []
    old_shape = dict(document, current_focus=[GUIDANCE.focus])
    assert schema_validator.validate(old_shape, schema) != []


def test_a_guidance_in_history_and_one_at_the_run_root_validate_identically():
    """The two definitions are the same shape, and the validator says so.

    Asserted by running both schemas over the same two documents rather than
    by comparing the two declarations as text: what matters is that a guidance
    accepted at the run root is accepted in history and that one refused at the
    root is refused there too.
    """
    root = schema_validator.load_schema("retry-guidance")
    history = schema_validator.load_schema("retry-history")
    inline = history["items"]["properties"]["guidance"]

    assert current_focus_items(inline)["required"] == \
        current_focus_items(root)["required"]

    good = GUIDANCE.document
    bad = json.loads(json.dumps(good))
    bad["current_focus"][0].pop("satisfied_when")

    assert schema_validator.validate(good, root) == []
    assert schema_validator.validate(good, inline) == []
    assert schema_validator.validate(bad, root) != []
    assert schema_validator.validate(bad, inline) != []


def test_a_recorded_retry_carries_the_new_guidance_shape_and_validates(fixture):
    """The history the harness actually wrote, against the schema it declares."""
    target, _, code = run(fixture, "history-shape",
                          [under_delivery(fixture), PASS])

    assert code == 0
    records = retry_records_of(target)
    assert records[0]["guidance"]["current_focus"] == \
        GUIDANCE.document["current_focus"]
    assert schema_validator.validate(
        records, schema_validator.load_schema("retry-history")) == []


def test_the_verdict_schema_declares_guidance_outcomes_optional():
    schema = schema_validator.load_schema("verification-result")
    declaration = schema["properties"]["guidance_outcomes"]
    items = declaration["items"]

    assert "guidance_outcomes" not in schema["required"]
    assert declaration["type"] == "array"
    assert items["required"] == ["guidance"]
    assert "unmet" not in items["required"]
    assert items["properties"]["unmet"]["type"] == "string"
    # The record of why presence is the signal rather than a boolean with a
    # sibling reason, asserted as the reason being stated rather than as prose.
    assert "presence" in items["properties"]["unmet"]["description"]
    assert "dependentRequired" in items["properties"]["unmet"]["description"]


def test_the_validator_accepts_a_verdict_with_and_without_the_field():
    schema = schema_validator.load_schema("verification-result")
    base = failing()
    met = dict(base, guidance_outcomes=conftest.echo_guidance(GUIDANCE.entries,
                                                              unmet=None))
    unmet = dict(base, guidance_outcomes=conftest.echo_guidance(GUIDANCE.entries,
                                                                unmet=UNMET))

    assert schema_validator.validate(base, schema) == []
    assert schema_validator.validate(met, schema) == []
    assert schema_validator.validate(unmet, schema) == []
    assert schema_validator.validate(
        dict(base, guidance_outcomes=[{"unmet": UNMET}]), schema) != []
    assert schema_validator.validate(
        dict(base, guidance_outcomes=[{"guidance": ["not a string"]}]),
        schema) != []


def test_the_self_route_schema_declares_a_fourth_failure_and_says_why():
    """The fourth value, and what distinguishes it from the three beside it."""
    schema = schema_validator.load_schema("self-route-result")
    declaration = schema["properties"]["failure"]

    enum = declaration["enum"]

    assert DEFECTIVE in enum
    assert len(enum) == len(set(enum))
    # The three it joined are still there, named off the module that owns them
    # rather than written out a second time here.
    assert set(MECHANICAL_FAILURES) <= set(enum)
    assert DEFECTIVE not in MECHANICAL_FAILURES
    assert len(enum) >= len(MECHANICAL_FAILURES) + 1
    described = declaration["description"]
    assert "computed from" in described
    assert "judgement" in described
    assert schema["properties"]["artifacts"]["description"].strip()


def test_the_validator_rejects_a_fifth_failure_value():
    """The control for the declaration above: the enum is enforced."""
    schema = schema_validator.load_schema("self-route-result")
    record = {"stage": "s", "attempt": 1, "try": 1, "failure": DEFECTIVE,
              "reason": "r", "statement": "s"}

    assert schema_validator.validate(record, schema) == []
    assert schema_validator.validate(
        dict(record, failure="not-a-declared-failure"), schema) != []


# --------------------------------------------------------------------------
# 12. The rendered verifier prompt — the shipped template is the subject
# --------------------------------------------------------------------------


#: What the prompt must state, as phrases rather than sentences, so rewording
#: the prose around them costs nothing and dropping one of them reddens.
WHAT_SATISFIED_WHEN_CARRIES = (
    "the observable condition that would satisfy that entry",
    "before you know what the retry will deliver",
)
EVERY_ENTRY_ACCOUNTED_FOR = (
    "answer that guidance entry by entry in guidance_outcomes",
    "must be accounted for, echoed verbatim",
    "against the satisfied_when written when the entry was written",
)
WHAT_THE_SELF_ROUTE_MEANS = (
    "the guidance is what is defective",
    "spends no retry on it",
    "runs this stage again in place",
)


@pytest.fixture
def rendered_verifier_prompt(tmp_path: Path, harness_root: Path):
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
    verifier = next(s for s in workflow["stages"] if "on_failure" in s)
    runner = Runner(
        target,
        Fixture(harness=harness_root, workflow=workflow, ceiling=0,
                build=lambda label: target),
        [PASS])
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, runner) == 0
    name = story_coordinator.prompt_file(verifier["name"], 1)
    return (run_dir_of(target) / name).read_text(encoding="utf-8"), verifier


def flowed(text: str) -> str:
    """One line of it, so a phrase spanning a wrap is still one phrase."""
    return " ".join(text.split())


def prose_of(rendered: str, harness_root: Path) -> str:
    """The rendered prompt with every injected schema removed.

    What is left is what the template itself says, which is where the criteria
    below have to appear — a schema description carrying the same words would
    otherwise satisfy the search.
    """
    for injected in context_assembler.schema_context(harness_root).values():
        rendered = rendered.replace(injected, "")
    return rendered


@pytest.mark.parametrize("phrase", [
    *WHAT_SATISFIED_WHEN_CARRIES, *EVERY_ENTRY_ACCOUNTED_FOR,
    *WHAT_THE_SELF_ROUTE_MEANS,
])
def test_the_rendered_verifier_prompt_states_the_new_criteria(
    rendered_verifier_prompt, harness_root, phrase,
):
    rendered, _ = rendered_verifier_prompt
    assert phrase in flowed(prose_of(rendered, harness_root))


def test_the_prompt_injects_the_two_schemas_the_criteria_refer_to(
    rendered_verifier_prompt, harness_root,
):
    """The shape belongs to the schema; the prompt injects it rather than
    restating it, so the definition the verifier is asked to satisfy is the
    file the coordinator enforces."""
    rendered, _ = rendered_verifier_prompt
    injected = context_assembler.schema_context(harness_root)

    assert injected["retry_guidance_schema"] in rendered
    assert injected["verification_result_schema"] in rendered
    assert "satisfied_when" in injected["retry_guidance_schema"]
    assert "guidance_outcomes" in injected["verification_result_schema"]


def test_the_rendered_verifier_prompt_resolves_every_placeholder(
    rendered_verifier_prompt,
):
    rendered, _ = rendered_verifier_prompt
    assert PLACEHOLDER.search(rendered) is None


def test_the_placeholder_check_sees_the_ones_the_template_carries(
    rendered_verifier_prompt, harness_root,
):
    """The control for the absence above, against the same regex and the
    template the prompt was rendered from, which does carry them."""
    _, verifier = rendered_verifier_prompt
    template = context_assembler.load_template(harness_root, verifier["prompt"])

    assert PLACEHOLDER.search(template) is not None
    assert "{{retry_guidance_schema}}" in template
