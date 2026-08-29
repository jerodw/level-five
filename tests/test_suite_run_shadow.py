"""A passing suite run supersedes a failing one only if it ran at least as much.

A declared suite run fails, the stage self-routes, and the rerun after it
passes. The pass is the more recent result, so before this it became the current
one and the workflow advanced — whether the rerun had run as much as the failure
did or had been narrowed until the answer was convenient. `suite_run_shadows`
decides that question, by set containment over the scopes story-083 recorded,
and a pass that shadows nothing leaves the failure standing.

What is asserted here, and what each assertion's subject is:

  * The rule itself, called directly: a pure function over two sequences of
    strings, so the cases are written as values and nothing is resolved.
  * Its docstring, because the story asks the limit of a string comparison to be
    stated where the rule is written rather than left for a reader to discover.
  * The two places it fires and the routing a refusal takes, driven through
    `story_coordinator.run_story` with a fake runner against a target repository
    built under `tmp_path`. The workflow, the target and the fake runner are
    `tests/test_coordinator_runs_the_suite.py`'s, reused rather than rebuilt:
    that module already builds the one definition declaring all three of the
    coordinator's suite runs, and the mechanism is the subject here while the
    stage list and the artifact names are inputs to it.
  * That an outstanding failure survives a resume, and that a run reaching the
    end of its workflow carrying one escalates rather than completing.

The declared suite run runs the configured command unnarrowed, so every one of
them records an empty scope and today every rerun shadows. That is the property
the harness wants and it is asserted below as the unaffected case; it is also
why the refusing cases substitute for `suite_run_check` to make two runs of one
story differ in their recorded scope. What is substituted is the scope alone —
the real check still runs the target's real command, writes its real records and
returns its real exit code — because the subject of those cases is the routing
decision taken on the scope, and a scope no run can differ in leaves that
decision unexercised.

Every absence asserted here carries a demonstration that it can fail:

  * "the rule interprets no entry" is asserted behaviourally, by entries that a
    split, a strip, a case-fold or a glob would relate and the rule does not,
    beside the entries it does relate — so the verdicts differ by what the rule
    does rather than by the scan looking nowhere — and structurally, by a scan
    of the rule's own source that reports an interpreting body planted in it;
  * "the docstring states the limit" sits beside the same reading of a docstring
    that states the rule and stops there, which the reading rejects;
  * "the field is empty by default" sits beside the same load of a state file
    carrying a failure, which reads it back;
  * "no self-route names a failure kind other than the one that already exists"
    sits beside the enumeration of the kinds the refusing run did take;
  * "the clean-clone check and the revert check neither read nor write the
    field" is enumerated over every definition under `orchestration/` rather
    than sampled, and sits beside the same scan over that source with a write of
    the field planted in a definition of its own, which the scan reports;
  * "the resumed run escalates and writes no completion report" sits beside the
    identical resume of the identical run directory with the one field cleared,
    which completes and writes one.

`.harness/docs/ARCHITECTURE.md` is not asserted on: this story's plan assigns it
to the documenter, the stage that runs after this one.
"""
import ast
import dataclasses
import inspect
import json
from pathlib import Path

import pytest

import conftest
import story_coordinator

# The workflow declaring a suite run, the target whose suite a stage can leave
# red or repair, the fake runner that drives it and the readers of what a run
# wrote. Reused rather than copied so a regression in any of them reddens both
# files.
from test_coordinator_runs_the_suite import (  # noqa: F401
    ALL_THREE,
    BROKEN,
    BUDGET,
    DECLARING,
    REPAIR,
    SENTINEL,
    STAGE_NAMES,
    STORY_ID,
    SUITE_ARTIFACT,
    VERIFYING,
    Runner,
    all_three_run,
    drive,
    harness_root,
    history_of,
    make_target,
    read_json,
    record_of,
    run_dir_of,
    self_route_records,
    state_of,
    target_root,
)
from test_self_routing_retry import write

SHADOWS = story_coordinator.suite_run_shadows

#: The field the outstanding failure is held in. Written here once, because it
#: *is* the subject: the story asks for a field of this name on `RunState`.
FIELD = "unshadowed_suite_failure"

ORCHESTRATION_DIR = Path(story_coordinator.__file__).resolve().parent
ORCHESTRATION_MODULES = sorted(ORCHESTRATION_DIR.glob("*.py"))
COORDINATOR_SOURCE = Path(story_coordinator.__file__).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The rule
#
# Called directly, with values written here: the subject is a pure function over
# two sequences of strings, and there is nothing about it to resolve.
# --------------------------------------------------------------------------


#: A failing run's scope, in the three shapes one can have: narrowed by nothing,
#: narrowed to one selection, narrowed to several.
UNNARROWED = ()
ONE_SELECTION = ("tests/test_thing.py::test_the_behaviour",)
TWO_SELECTIONS = ONE_SELECTION + ("tests/test_other.py",)


@pytest.mark.parametrize("failing", [UNNARROWED, ONE_SELECTION, TWO_SELECTIONS],
                         ids=["nothing", "one selection", "two selections"])
def test_a_rerun_narrowed_by_nothing_shadows_whatever_failed(failing):
    """The unfiltered rerun, which is the whole suite run again: it ran at least
    as much as anything can have, so it supersedes any failure."""
    assert SHADOWS(UNNARROWED, failing) is True


@pytest.mark.parametrize("scope", [UNNARROWED, ONE_SELECTION, TWO_SELECTIONS],
                         ids=["nothing", "one selection", "two selections"])
def test_a_rerun_at_the_same_scope_shadows(scope):
    """The ordinary fix-and-rerun. The rule rejects narrowing, not correction,
    so a rerun filtered exactly as the failure was supersedes it."""
    assert SHADOWS(scope, scope) is True


def test_a_rerun_narrowed_by_less_than_the_failure_shadows_it():
    """Broader than the failure, by having dropped one of its selections."""
    assert SHADOWS(ONE_SELECTION, TWO_SELECTIONS) is True


@pytest.mark.parametrize("failing", [UNNARROWED, ONE_SELECTION],
                         ids=["nothing", "one selection"])
def test_a_rerun_carrying_a_selection_the_failure_did_not_shadows_nothing(
    failing,
):
    """The narrowing rerun the rule exists to reject: it filtered more than the
    failure did, so it established nothing about what it stopped running."""
    assert SHADOWS(TWO_SELECTIONS, failing) is False


#: Two strings that are not selectors in any syntax, so the verdicts below are
#: about set containment rather than about anything the harness understands.
NOT_A_SELECTOR = "the third thing the developer said out loud"
NOR_IS_THIS = "\N{SNOWMAN} \t {}"


@pytest.mark.parametrize("passing, failing, shadows", [
    ((), (NOT_A_SELECTOR,), True),
    ((NOT_A_SELECTOR,), (NOT_A_SELECTOR,), True),
    ((NOT_A_SELECTOR,), (NOT_A_SELECTOR, NOR_IS_THIS), True),
    ((NOT_A_SELECTOR, NOR_IS_THIS), (NOT_A_SELECTOR,), False),
    ((NOR_IS_THIS,), (NOT_A_SELECTOR,), False),
], ids=["empty", "equal", "subset", "superset", "disjoint"])
def test_strings_that_are_no_selection_at_all_get_the_same_answers(
    passing, failing, shadows,
):
    """The comparison reads the recorded strings and nothing else. Every
    relation asserted of selector-shaped entries above holds identically of
    entries no selector syntax would accept, so the verdict comes from
    containment rather than from an understanding of what was selected."""
    assert SHADOWS(passing, failing) is shadows


@pytest.mark.parametrize("passing, failing", [
    (("a b",), ("a", "b")),
    (("a",), ("a b",)),
    ((" a ",), ("a",)),
    (("A",), ("a",)),
    (("*",), ("anything at all",)),
    (("tests/",), ("tests/test_thing.py",)),
    (("tests/test_thing.py::test_case",), ("tests/test_thing.py",)),
], ids=["a split", "a join", "a strip", "a case fold", "a glob",
        "a prefix", "a selector's own syntax"])
def test_an_entry_is_neither_split_stripped_folded_nor_globbed(passing, failing):
    """Each pair is one a split, a strip, a case fold, a glob or a reading of
    the target's own selector syntax would relate. None of them is related, so
    the rule performs none of those readings.

    The equal pair beside them is the control: containment over these same
    strings is not universally false, so a `False` here says the entries differ
    as strings rather than that the rule refuses everything.
    """
    assert SHADOWS(passing, failing) is False
    assert SHADOWS(passing, passing) is True


RULE_SOURCE = inspect.getsource(story_coordinator.suite_run_shadows)

#: The one construction the comparison is allowed to reach for, so that reaching
#: for anything else is what the scan reports.
CONTAINMENT = {"set", "frozenset"}


def interpretation_in(source: str) -> list[str]:
    """Every name a body reaches for beyond building the two sets it compares.

    An attribute on an entry and a call to something else are the two shapes an
    interpretation would take — `entry.split()`, `normalise(entry)` — and both
    are reported, whatever they turn out to do. Docstrings and comments are not
    read, because what is asserted is what the code reaches rather than what it
    says. A list rather than an assertion, so the same statement can be made of
    a body that does interpret, which is the control.
    """
    reached = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute):
            reached.add(node.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            reached.add(node.func.id)
    return sorted(reached - CONTAINMENT)


def test_the_rule_reaches_no_code_path_that_interprets_an_entry():
    """Structural, beside the behavioural cases above: the body compares two
    sets and calls nothing else, so there is no path through it — here or in
    something it hands the entries to — that could read inside one."""
    assert interpretation_in(RULE_SOURCE) == []


def test_the_same_scan_reports_an_interpreting_body_planted_in_it():
    """The control. The scan is looking, and it reports the readings when a body
    performs them — so the green above is a fact about the rule rather than
    about where this test is looking."""
    planted = RULE_SOURCE.replace(
        "return set(passing_scope) <= set(failing_scope)",
        "return set(entry.strip().split()[0] for entry in passing_scope) <= "
        "set(normalise(entry) for entry in failing_scope)",
        1)
    assert planted != RULE_SOURCE
    assert interpretation_in(planted) == ["normalise", "split", "strip"]


# --------------------------------------------------------------------------
# What the rule's docstring has to say
# --------------------------------------------------------------------------


DOCSTRING = inspect.getdoc(story_coordinator.suite_run_shadows).lower()

#: A docstring that states the rule and stops there. The control for the limit
#: being stated: it is what the real one would be with the limit dropped, and
#: nothing about it needs resolving out of a history.
RULE_WITHOUT_THE_LIMIT = (
    "whether a passing suite run supersedes an earlier failing one. the rule: "
    "a later pass supersedes an earlier failure only when the pass ran at "
    "least as much as the failure did, which is recorded scope by set "
    "containment - the passing run's scope must be a subset of the failing "
    "run's."
)


def states_the_rule(text: str) -> bool:
    return "subset" in text and "supersede" in text


def states_the_limit(text: str) -> bool:
    """Whether the text says an entry is compared as an opaque string, and says
    what that costs: a rerun made broader by rewriting a selection rather than
    by dropping one is not seen as broader."""
    return ("opaque" in text and "string" in text
            and "rewrit" in text and "broader" in text)


def test_the_docstring_states_the_rule_and_states_the_limit():
    """Both halves, because either alone misleads: a rule with no limit reads as
    an understanding of what the rerun covered, and a limit with no rule leaves
    a reader to guess which way containment runs."""
    assert states_the_rule(DOCSTRING)
    assert states_the_limit(DOCSTRING)


def test_the_same_reading_rejects_a_docstring_that_states_the_rule_alone():
    """The control for the absence the limit half is asserting away: a docstring
    saying only what the rule is passes the first reading and fails the
    second."""
    assert states_the_rule(RULE_WITHOUT_THE_LIMIT)
    assert not states_the_limit(RULE_WITHOUT_THE_LIMIT)


# --------------------------------------------------------------------------
# The field on the state
# --------------------------------------------------------------------------


STATE_FIELDS = {f.name: f for f in dataclasses.fields(story_coordinator.RunState)}

#: A failure record of the shape the coordinator writes, used where a test needs
#: one without having driven the run that produced it.
A_FAILURE = {"stage": DECLARING, "attempt": 1, "try": 0, "scope": [],
             "exit_code": 1, "result_path": "kept-result.json",
             "output_path": "/tmp/kept-output.txt"}


def test_the_field_is_declared_and_defaulted():
    """Defaulted, because a state file written before this story carries no such
    key and must still load. The undefaulted fields of the same dataclass are
    the control: having a default is a property this dataclass's fields differ
    in, so finding it of this one says something."""
    undefaulted = [name for name, field in STATE_FIELDS.items()
                   if field.default is dataclasses.MISSING
                   and field.default_factory is dataclasses.MISSING]
    field = STATE_FIELDS[FIELD]

    assert field.default_factory is dict
    assert undefaulted, "every field is defaulted; the control says nothing"


def test_a_state_file_written_before_this_story_reads_as_no_failure_outstanding(
    green_run,
):
    """Driven rather than argued: the key is removed from a state file a real
    run wrote, and the coordinator's own loader reads it back. Beside it the
    same file carrying a failure, which loads as that failure — so the empty
    read is the default being taken rather than the loader dropping the key."""
    _, _, run_dir = green_run
    before = {key: value for key, value in state_of(run_dir).items()
              if key != FIELD}
    write_state(run_dir, before, replace=True)

    assert getattr(story_coordinator.load_state(run_dir), FIELD) == {}

    write_state(run_dir, {FIELD: A_FAILURE})
    assert getattr(story_coordinator.load_state(run_dir), FIELD) == A_FAILURE


# --------------------------------------------------------------------------
# Driving the two places the rule fires
# --------------------------------------------------------------------------


def write_state(run_dir: Path, changes: dict, *, replace: bool = False) -> None:
    """Rewrite state.json in place, the way an inspecting developer would."""
    path = Path(run_dir) / "state.json"
    state = {} if replace else json.loads(path.read_text(encoding="utf-8"))
    state.update(changes)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


#: A scope a narrowed rerun would record: one selection the failing run did not
#: carry, which is what makes it a strict subset of what failed.
NARROWED = ("-k the_one_test_that_passes",)


def scoping(monkeypatch, scopes: list[tuple]) -> list[tuple]:
    """Make the declared suite runs of one story differ in recorded scope.

    The coordinator's declared suite run is the configured command as
    configured, so every one of them records an empty scope and no run of it can
    reach the refusing branch. The real check still runs: it executes the
    target's command, writes both records and returns its own exit code, and
    only the scope on the result the routing reads is substituted, in call
    order. Returns the list the substitutions are appended to, so a test can
    assert the runs really did differ.
    """
    real = story_coordinator.suite_run_check
    used: list[tuple] = []

    def substituting(*args, **kwargs):
        result = real(*args, **kwargs)
        scope = scopes[min(len(used), len(scopes) - 1)]
        used.append(scope)
        return dataclasses.replace(result, scope=scope)

    monkeypatch.setattr(story_coordinator, "suite_run_check", substituting)
    return used


class Snapshotting(Runner):
    """The same fake runner, recording `state.json` as each invocation begins.

    The coordinator saves the state at the top of every stage iteration, so the
    file an invocation opens on holds what the iteration before it decided. That
    is where an outstanding failure is observable while the run is still going,
    rather than only in whatever the run ended with.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.snapshots: list[dict] = []

    def __call__(self, prompt, **kwargs):
        self.snapshots.append(state_of(self.run_dir))
        return super().__call__(prompt, **kwargs)


@pytest.fixture
def green_run(target_root, harness_root):
    """The unaffected run: no declared suite run fails."""
    return drive(target_root, harness_root)


@pytest.fixture
def red_then_green_run(target_root, harness_root):
    """The ordinary fix-and-rerun: the first invocation leaves the suite red and
    the invocation the coordinator brings the stage back for repairs it. Both
    runs record the same scope, because the coordinator narrows neither."""
    return drive(target_root, harness_root, {DECLARING: [BROKEN, REPAIR]})


def failure_in(run_dir: Path) -> dict:
    return state_of(run_dir).get(FIELD, {})


def retained_pair(run_dir: Path, attempt: int, try_number: int) -> list[str]:
    """The result and output a suite run keyed by this attempt and try kept,
    named through the coordinator's own helpers rather than spelled here."""
    result = story_coordinator.retained_suite_result_file(
        SUITE_ARTIFACT, DECLARING, attempt, try_number)
    return [result, str(Path(run_dir) / story_coordinator.suite_output_file(
        result))]


def test_a_red_declared_suite_run_leaves_the_failure_outstanding(
    target_root, harness_root,
):
    """Observed while the run is still going, off the state the second
    invocation of the declaring stage opened on: the failure is outstanding from
    the moment it happens, and everything a refusal has to name is there — the
    stage, the attempt, the try, the recorded scope, the exit code and the pair
    of paths that run's evidence was retained under.

    The first invocation's snapshot is the control beside it: the same file, one
    iteration earlier, carries no failure.
    """
    runner = Snapshotting(target_root, {DECLARING: [BROKEN, REPAIR]})
    code = story_coordinator.run_story(
        STORY_ID, harness_root, target_root, runner)
    run_dir = run_dir_of(target_root)
    before, after = runner.snapshots[0], runner.snapshots[1]
    result, output = retained_pair(run_dir, 1, 0)

    assert code == 0
    assert before[FIELD] == {}
    assert after[FIELD] == {
        "stage": DECLARING,
        "attempt": 1,
        "try": 0,
        "scope": [],
        "exit_code": read_json(run_dir / result)["exit_code"],
        "result_path": result,
        "output_path": output,
    }
    assert after[FIELD]["exit_code"] != 0


def test_a_rerun_at_the_same_scope_clears_the_failure_and_the_run_completes(
    red_then_green_run,
):
    """The ordinary fix-and-rerun, unchanged by this story: one self-route, the
    workflow advances over the pass, the run completes, and the state it ends
    with carries no outstanding failure."""
    code, runner, run_dir = red_then_green_run

    assert code == 0
    assert runner.calls == [DECLARING, DECLARING, VERIFYING]
    assert state_of(run_dir)["status"] == "completed"
    assert failure_in(run_dir) == {}
    assert len(self_route_records(run_dir)) == 1


def test_a_run_in_which_no_declared_suite_run_fails_is_routed_as_before(
    green_run,
):
    """The unaffected run: one invocation of each stage, no self-route, nothing
    outstanding at any point, and completion reached by the path it was reached
    by before this story."""
    code, runner, run_dir = green_run

    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert not self_route_records(run_dir)
    assert failure_in(run_dir) == {}
    assert state_of(run_dir)["status"] == "completed"
    assert (run_dir / "completion-report.md").is_file()


@pytest.fixture
def narrowing_rerun(target_root, harness_root, monkeypatch):
    """The run this story is about: the declared suite run fails unnarrowed, the
    rerun passes narrowed to one selection the failure did not carry, and the
    third invocation reruns unnarrowed — so the refusal is visible and so is the
    way out of it."""
    used = scoping(monkeypatch, [(), NARROWED, ()])
    return drive(target_root, harness_root,
                 {DECLARING: [BROKEN, REPAIR, REPAIR]}), used


def test_the_fixture_really_makes_two_runs_of_differing_scope(narrowing_rerun):
    """The premise the cases below rest on: the substitution happened, and the
    passing rerun's scope is genuinely not a subset of the failure's."""
    (_, _, _), used = narrowing_rerun

    assert used[:2] == [(), NARROWED]
    assert SHADOWS(used[1], used[0]) is False


def test_a_narrowing_rerun_does_not_advance_the_workflow(narrowing_rerun):
    """The pass is the more recent result and the workflow does not move on it:
    the declaring stage is brought back a second time, which is one invocation
    more than the same run takes when the rerun is unnarrowed."""
    (code, runner, run_dir), _ = narrowing_rerun

    assert runner.calls == [DECLARING, DECLARING, DECLARING, VERIFYING]
    assert len(self_route_records(run_dir)) == 2
    # And the way out is a rerun the comparison can see is no narrower: the
    # third invocation's run shadows, the failure is cleared and the run ends.
    assert code == 0
    assert failure_in(run_dir) == {}
    assert state_of(run_dir)["status"] == "completed"


def test_the_refusal_introduces_no_failure_kind_of_its_own(narrowing_rerun):
    """What stands is the suite failure, so it is routed as one. Both records
    the run wrote name the failure kind that already existed, and the second is
    the refusal — keyed as the declaring stage's second try, spending no retry
    budget, exactly as the first."""
    (_, _, run_dir), _ = narrowing_rerun
    records = [record for _, record in self_route_records(run_dir)]

    assert {record["failure"] for record in records} == {
        story_coordinator.SUITE_FAILED}
    assert [record["try"] for record in records] == [1, 2]
    assert state_of(run_dir)["retry_count"] == 0
    assert not (run_dir / "retry-history.json").exists()


def test_the_refusal_cites_the_original_failures_retained_evidence(
    narrowing_rerun,
):
    """The re-running stage is pointed at the failure that still stands rather
    than at the rerun that passed: the artifacts are the pair the *first*
    invocation's red run retained, and the record at that path is the failing
    one. The passing rerun's own retained pair is the control beside it — it
    exists, it records a pass, and it is not what the refusal cites."""
    (_, _, run_dir), _ = narrowing_rerun
    _, refusal = self_route_records(run_dir)[1]
    failing_result, failing_output = retained_pair(run_dir, 1, 0)
    passing_result, _ = retained_pair(run_dir, 1, 1)

    assert refusal["artifacts"] == [failing_result, failing_output]
    assert read_json(run_dir / failing_result)["exit_code"] != 0
    assert Path(failing_output).is_file()
    assert read_json(run_dir / passing_result)["exit_code"] == 0
    assert passing_result not in refusal["artifacts"]


def test_the_refusal_names_the_failure_and_says_what_the_rerun_ran(
    narrowing_rerun,
):
    """The reason a reader meets: which stage's suite run failed, what it exited
    with, and that the rerun that passed ran a strict subset of it — so the
    refusal is legible without opening the state."""
    (_, _, run_dir), _ = narrowing_rerun
    _, red = self_route_records(run_dir)[0]
    _, refusal = self_route_records(run_dir)[1]
    failing_result, _ = retained_pair(run_dir, 1, 0)
    exit_code = read_json(run_dir / failing_result)["exit_code"]

    assert DECLARING in refusal["reason"]
    assert f"exited {exit_code}" in refusal["reason"]
    assert "strict subset" in refusal["reason"]
    # The reason is the whole of what is said about this refusal in particular:
    # the statement beside it is the one a suite failure is already stated in,
    # word for word what the red run's own record carries, because the refusal
    # routes as that failure and cites the same retained pair.
    assert refusal["statement"] == red["statement"]


@pytest.fixture
def narrowing_past_the_budget(target_root, harness_root, monkeypatch):
    """Every rerun after the failure passes narrowed, so the refusal is taken
    until the stage's own self-route budget is gone."""
    scoping(monkeypatch, [(), NARROWED])
    return drive(target_root, harness_root,
                 {DECLARING: [BROKEN] + [REPAIR] * (BUDGET + 1)})


def test_a_refusal_past_the_budget_escalates_with_the_decisions_own_reason(
    narrowing_past_the_budget,
):
    """No escalation path was written for this: the exhausted-budget clause is
    the one `self_route` already composes, naming the stage and the budget it
    exhausted, and the refusal's own reason is carried in front of it."""
    code, runner, run_dir = narrowing_past_the_budget
    reason = story_coordinator.escalation_reason(run_dir)

    assert code == 2
    assert state_of(run_dir)["status"] == "escalated"
    assert runner.calls.count(DECLARING) == BUDGET + 1
    assert VERIFYING not in runner.calls
    assert f"{DECLARING} has exhausted its self-route budget of {BUDGET}" in reason
    assert "strict subset" in reason


def test_the_same_clause_ends_a_run_whose_suite_simply_stays_red(
    target_root, harness_root,
):
    """The control beside it: a run that never narrows anything and never
    repairs the suite escalates through the same clause, so the escalation above
    is the existing route being taken rather than one added for the refusal."""
    code, _, run_dir = drive(target_root, harness_root,
                             {DECLARING: [BROKEN] * (BUDGET + 1)})
    reason = story_coordinator.escalation_reason(run_dir)

    assert code == 2
    assert f"{DECLARING} has exhausted its self-route budget of {BUDGET}" in reason
    assert "strict subset" not in reason
    # The failure that ended it is outstanding in the state the run left.
    assert failure_in(run_dir)["stage"] == DECLARING


# --------------------------------------------------------------------------
# A resume carries the failure, and a run may not complete over one
# --------------------------------------------------------------------------


@pytest.fixture
def resumable_run(target_root, harness_root):
    """A run left with a failure outstanding and re-enterable past the stage
    that declares the suite run.

    The run is driven until its suite failure exhausts the declaring stage's
    budget, which is how a real run leaves the field set. The developer then
    repairs the suite by hand and commits, and the run directory is marked as
    one that was interrupted at the verifying stage — the shape a crashed run
    leaves, which re-enters with no guard. The re-entry therefore makes no
    declared suite run at all, which is the only way the end of a workflow is
    reached with a failure still outstanding.
    """
    code, _, run_dir = drive(target_root, harness_root,
                             {DECLARING: [BROKEN] * (BUDGET + 1)})
    assert code == 2, "the shape was meant to escalate"
    assert failure_in(run_dir), "the shape was meant to leave a failure"

    write(target_root / SENTINEL, "repaired\n")
    conftest.commit_setup(target_root, "the developer repaired the suite")
    write_state(run_dir, {"status": "running", "current_stage": VERIFYING})
    return target_root, run_dir


def test_a_resume_restores_the_outstanding_failure(resumable_run, harness_root):
    """It is evidence that a failure is unrepaired rather than a live allowance,
    so the re-entry neither clears it nor writes it afresh: the record the
    resumed run carries is the one the interrupted entry left, field for
    field."""
    target_root, run_dir = resumable_run
    left_behind = failure_in(run_dir)

    drive(target_root, harness_root)

    assert failure_in(run_dir) == left_behind
    assert left_behind["stage"] == DECLARING


def test_a_run_reaching_the_end_over_an_outstanding_failure_escalates(
    resumable_run, harness_root,
):
    """The guard before completion. The re-entry runs the stages after the one
    that declares the suite run, so nothing supersedes the failure, and the run
    escalates rather than completing — naming the failure that still stands and
    where its record and its output were kept."""
    target_root, run_dir = resumable_run
    outstanding = failure_in(run_dir)

    code, runner, _ = drive(target_root, harness_root)
    reason = story_coordinator.escalation_reason(run_dir)

    assert code == 2
    assert state_of(run_dir)["status"] == "escalated"
    assert runner.calls == [VERIFYING]
    assert outstanding["stage"] in reason
    assert f"exited {outstanding['exit_code']}" in reason
    assert outstanding["result_path"] in reason
    assert outstanding["output_path"] in reason
    assert not (run_dir / "completion-report.md").exists()
    assert "story-completed" not in [e["event"] for e in history_of(run_dir)]


def test_that_guard_costs_no_declared_suite_run(resumable_run, harness_root):
    """It is decided off the state rather than by asking the suite again: the
    re-entry announces no run of the declared artifact. The runs the interrupted
    entry announced are the control — the same reading of the same stream finds
    those, so the absence is about this entry rather than about the reading."""
    target_root, run_dir = resumable_run
    before = declared_runs_announced(run_dir)

    drive(target_root, harness_root)

    assert before, "the interrupted entry announced no run to compare against"
    assert declared_runs_announced(run_dir) == before


def declared_runs_announced(run_dir: Path) -> int:
    return len([event for event in history_of(run_dir)
                if event["event"] == "suite-rerun-started"
                and event.get("artifacts") == [SUITE_ARTIFACT]])


def test_the_identical_resume_with_the_failure_cleared_completes(
    resumable_run, harness_root,
):
    """The control for the guard: the same run directory, resumed the same way,
    differing in the one field — and it completes, writes its completion report
    and escalates nothing. So the escalation above is the guard firing rather
    than the resumed run being unable to finish."""
    target_root, run_dir = resumable_run
    write_state(run_dir, {FIELD: {}})

    code, runner, _ = drive(target_root, harness_root)

    assert code == 0
    assert runner.calls == [VERIFYING]
    assert state_of(run_dir)["status"] == "completed"
    assert (run_dir / "completion-report.md").is_file()


# --------------------------------------------------------------------------
# The two checks the rule leaves alone
#
# Enumerated over every definition under `orchestration/` rather than sampled,
# because what is asserted is that nothing outside the declared suite run and
# the completion guard touches the field.
# --------------------------------------------------------------------------


def _names_the_field(node: ast.AST) -> bool:
    return any(
        (isinstance(child, ast.Attribute) and child.attr == FIELD)
        or (isinstance(child, ast.Name) and child.id == FIELD)
        or (isinstance(child, ast.Constant) and child.value == FIELD)
        for child in ast.walk(node))


def definitions_naming_the_field(source: str) -> set[str]:
    """Every top-level definition in `source` that reads or writes the field."""
    return {node.name for node in ast.parse(source).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef))
            and _names_the_field(node)}


#: The two definitions that may touch it: the state that declares it and the
#: loop that holds both places the rule fires. Named rather than derived because
#: naming them is the assertion — a third definition reaching the field is what
#: this is looking for.
MAY_TOUCH_IT = {story_coordinator.RunState.__name__,
                story_coordinator.run_story.__name__}

#: The functions implementing the two checks the rule leaves outside it: the
#: clean-clone run, which always runs the configured command unnarrowed, and the
#: revert check with the selector runs it makes, which are narrowed by design.
OUTSIDE_THE_RULE = [story_coordinator.clean_clone_check,
                    story_coordinator.run_clean_clone,
                    story_coordinator.revert_check,
                    story_coordinator.run_nomination,
                    story_coordinator._run_selection]


def test_only_the_state_and_the_run_loop_name_the_field():
    assert definitions_naming_the_field(COORDINATOR_SOURCE) == MAY_TOUCH_IT


@pytest.mark.parametrize("module", ORCHESTRATION_MODULES,
                         ids=lambda path: path.name)
def test_no_other_module_under_orchestration_names_it(module):
    if module.name == Path(story_coordinator.__file__).name:
        pytest.skip("asserted exactly above")
    assert definitions_naming_the_field(module.read_text(encoding="utf-8")) == set()


@pytest.mark.parametrize("function", OUTSIDE_THE_RULE,
                         ids=lambda function: function.__name__)
def test_neither_check_the_rule_leaves_alone_is_among_them(function):
    """Said of the two checks by name as well as by enumeration, so a reader
    looking for the story's constraint finds it stated rather than inferred.
    Each function's own source is parsed, so a read or a write anywhere inside
    it — including in a nested definition — is what is being denied."""
    assert function.__name__ not in MAY_TOUCH_IT
    assert definitions_naming_the_field(inspect.getsource(function)) == set()


#: A write of the field in a definition of its own, appended to a source to show
#: the scan reports one. Parsed rather than run, so where it sits is irrelevant.
PLANTED_WRITE = (
    f"\ndef a_check_that_touches_it(state, result):\n"
    f"    state.{FIELD} = {{}}\n")


def test_the_same_scan_reports_a_definition_planted_in_that_source():
    """The control. The scan is looking, and a definition that writes the field
    is reported — so the enumeration above is a fact about the source rather
    than about a scan that finds nothing anywhere."""
    reported = definitions_naming_the_field(COORDINATOR_SOURCE + PLANTED_WRITE)
    assert reported == MAY_TOUCH_IT | {"a_check_that_touches_it"}


def test_a_run_the_revert_check_made_and_failed_leaves_nothing_outstanding(
    all_three_run,
):
    """The same thing driven rather than read: one run making all three of the
    coordinator's suite runs, in which the revert check's run with the governed
    edits reverted exits non-zero — a failing suite run made by a check outside
    the rule — and the story still completes with nothing outstanding. The
    declared suite run of that same story is the control beside it: it passed,
    and it is the one run of the three whose failure would have set the field."""
    code, _, run_dir = all_three_run
    revert_record = record_of(run_dir, next(
        stage["revert_check"]["result"] for stage in ALL_THREE["stages"]
        if "revert_check" in stage))

    assert code == 0
    assert revert_record["exit_code"] != 0
    assert record_of(run_dir)["exit_code"] == 0
    assert failure_in(run_dir) == {}
    assert state_of(run_dir)["status"] == "completed"
