"""A passing suite run supersedes a failing one only if it ran at least as much.

A declared suite run fails, a later invocation of the declaring stage runs the
suite again, and that rerun passes. The pass is the more recent result, so
before this it became the current one and the workflow advanced — whether the
rerun had run as much as the failure did or had been narrowed until the answer
was convenient. `suite_run_shadows` decides that question, by set containment
over the scopes story-083 recorded, and a pass that shadows nothing leaves the
failure standing.

What a standing failure *does* is story-137's, and it is the one thing here that
moved: neither the failure nor the non-shadowing rerun brings the declaring
stage back in place any more. Both record and advance, and the failure they
leave in state is what the refusal at the end of the run reads.

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
  * That an outstanding failure survives a resume as the failure it was, and
    what then clears it: the clean-clone check the verifying stage declares,
    which since story-159 is put to the same rule a green declared run is. A
    run resumed past the declaring stage never makes a declared run again, so
    before that story it could pass every check it had and still be refused at
    the end of its workflow, with no route out but resuming earlier for no
    reason except to clear the field. Now the resume completes where the check
    ran and exited zero at a scope no narrower than the failure's, and is
    refused on the same terms a narrowed declared run is — where the check's
    recorded scope is narrower, and where the check did not run at all.
  * That a run whose verifying stage declares no clean-clone check still
    reaches the refusal over a standing failure, because there is then no run
    after the declaring stage that could shadow it.
  * That the record's two paths follow the move a resume makes (story-161). A
    resume moves the closed entry's counter-keyed artifacts under the entry
    directory it opens, and the retained pair the record names is among them;
    before that story the record was left as it was, so the shadowing note, the
    carry-forward note and the end-of-run refusal — all composed off the record
    — named paths that no longer held the files, and the case reading the note
    had to compose where the pair had gone from `entry_dir` instead. Now the
    record is repointed by `repoint_outstanding_failure` in the resume branch,
    and every case here reads the pair at the paths the record and the lines
    name. The helper is asserted directly beside the driven cases: it moves
    both paths in the forms they already take, leaves every other field alone,
    leaves a record whose pair did not move in this resume alone, and names the
    resumes directory nowhere.
  * That a declared rerun's clear is recorded as the clean-clone check's is
    (story-162). Both blocks clear the field on the same rule, and until that
    story only the clean-clone block wrote the clear down: the declared
    block's only trace in events.log was the absence of a carry-forward line
    after the rerun's announcement. Now the declared block, where a rerun
    passes at a scope no narrower than the failure's, saves the emptied field
    and then appends a note composed by
    `suite_failure_shadowed_by_declared_rerun_note` — the clean-clone
    composer's sibling, naming the cleared failure's stage, exit code and
    retained pair off the same record — under the declaring stage's name. The
    cases read that note off the red-then-green run and off the narrowing
    rerun, read the pair at the paths the note names, and observe the save
    preceding the note the way the clean-clone case does.

The declared suite run and the clean-clone check both run the configured
command unnarrowed, so every one of them records an empty scope and today every
rerun shadows. That is the property the harness wants and it is asserted below
as the unaffected case; it is also why the refusing cases substitute for
`suite_run_check` and for `clean_clone_check` to make two runs of one story
differ in their recorded scope. What is substituted is the result the routing
reads — its scope, or whether it ran — and nothing else: the real check still
runs the target's real command, writes its real record and returns its real exit
code, because the subject of those cases is the routing decision taken on the
result, and a result no run can differ in leaves that decision unexercised.

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
  * "the non-shadowing rerun takes no self-route" sits beside the carry-forward
    line it does write, and beside the same reading of a run whose suite never
    failed, which finds no such line;
  * "neither the clean-clone check's functions nor the revert check's read or
    write the field" is enumerated over every definition under `orchestration/`
    rather than sampled, and sits beside the same scan over that source with a
    write of the field planted in a definition of its own, which the scan
    reports. The clean-clone check is inside the rule now and its functions
    still do not name the field, because the comparison is made in the loop
    where the exit code is read — as it is for the declared run, whose check
    does not touch the field either;
  * "the resumed run refused on a narrowed clean-clone pass writes no shadowing
    note and no completion report" sits beside the identical resume with the
    check's scope left as recorded, which writes both;
  * "the resume with the field already clear writes no shadowing note" sits
    beside the same resume with the field set, which writes one — so the note
    is written where a failure was cleared rather than at every pass;
  * "no second rule decides the clear" is shown by substituting for the one
    rule: the resume that completes is refused when `suite_run_shadows` is made
    to say no, at the very scope it says yes to unsubstituted;
  * "the repointing helper names the resumes directory nowhere" sits beside the
    same scan of `entry_dir`, the one place that directory is named, which the
    scan reports;
  * "the retry record is not rewritten by the resume" sits beside the state
    file the same resume did rewrite, read the same way;
  * "a run whose declared suite never failed writes no declared-clear note"
    and "the narrowed pass writes none" sit beside the red-then-green run and
    the narrowing rerun's unnarrowed pass, where the same reading finds exactly
    one — so the note is written where a declared rerun cleared a failure
    rather than at every green declared run;
  * "the declared clear is not recorded as the clean-clone composer's line"
    sits beside the resume cases, where the same reading of that line finds it
    — so the two composers are told apart by what they say, and the note the
    declared block writes is its own composer's.

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
    FAILED,
    MORE_THAN_ANY_RUN_TAKES,
    PASS,
    DECLARING,
    REPAIR,
    SENTINEL,
    STAGE_NAMES,
    STORY_ID,
    SUITE_ARTIFACT,
    VERIFYING,
    WORKFLOW,
    Runner,
    all_three_run,
    carry_forward_lines,
    drive,
    harness_root,
    history_of,
    make_target,
    materialize,
    plan_touching_nothing_after,
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
    the invocation that comes back to the declaring stage repairs it. Both runs
    record the same scope, because the coordinator narrows neither.

    What brings the stage back is the retry the verifier's failed verdict
    routes, which since story-137 is the only thing that does: the red suite
    itself advances rather than re-entering.
    """
    return drive(target_root, harness_root,
                 plan_touching_nothing_after([BROKEN, REPAIR]),
                 verdicts=[FAILED, PASS])


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
    """Observed while the run is still going, off the state the invocation
    *after* the declaring stage opened on: the failure is outstanding from the
    moment it happens, and everything a refusal has to name is there — the
    stage, the attempt, the try, the recorded scope, the exit code and the pair
    of paths that run's evidence was retained under. That record's shape and its
    values are what this block wrote before story-137 moved where the failure
    goes; only the routing changed.

    Which invocation that is says the other half of it: the stage the run
    advanced into is the judging one rather than the declaring one run again.
    The first invocation's snapshot is the control beside it — the same file,
    one iteration earlier, carries no failure.
    """
    runner = Snapshotting(target_root,
                          plan_touching_nothing_after([BROKEN, REPAIR]),
                          verdicts=[FAILED, PASS])
    code = story_coordinator.run_story(
        STORY_ID, harness_root, target_root, runner)
    run_dir = run_dir_of(target_root)
    before, after = runner.snapshots[0], runner.snapshots[1]
    result, output = retained_pair(run_dir, 1, 0)

    assert code == 0
    assert runner.calls[:2] == [DECLARING, VERIFYING]
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
    """The ordinary fix-and-rerun: the rerun runs the same scope, so it
    supersedes the failure, the workflow advances over the pass and the run
    completes with nothing outstanding.

    What the rerun *is* moved with story-137 — a retry the verifier routed
    rather than a self-route the suite failure took — so no self-route record
    is written anywhere in it.
    """
    code, runner, run_dir = red_then_green_run

    assert code == 0
    assert runner.calls == [DECLARING, VERIFYING, DECLARING, VERIFYING]
    assert state_of(run_dir)["status"] == "completed"
    assert failure_in(run_dir) == {}
    assert self_route_records(run_dir) == []


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
    third invocation reruns unnarrowed — so the failure standing through the
    narrowed pass is visible and so is the way out of it.

    Each invocation of the declaring stage after the first is a retry the
    verifier routed, which is what brings that stage back now.
    """
    used = scoping(monkeypatch, [(), NARROWED, ()])
    return drive(target_root, harness_root,
                 plan_touching_nothing_after([BROKEN, REPAIR, REPAIR]),
                 verdicts=[FAILED, FAILED, PASS]), used


def test_the_fixture_really_makes_two_runs_of_differing_scope(narrowing_rerun):
    """The premise the cases below rest on: the substitution happened, and the
    passing rerun's scope is genuinely not a subset of the failure's."""
    (_, _, _), used = narrowing_rerun

    assert used[:2] == [(), NARROWED]
    assert SHADOWS(used[1], used[0]) is False


def test_a_narrowing_rerun_advances_and_leaves_the_failure_standing(
    narrowing_rerun,
):
    """The pass is the more recent result and the workflow does not *supersede*
    the failure on it. Since story-137 the workflow advances anyway — that is
    what a standing red suite does by every path — so what is left behind is
    the failure rather than a re-entry: no self-route, no re-run prompt, and
    the field still set when the run reaches the judging stage again.

    The way out is unchanged and is asserted beside it: a rerun the comparison
    can see is no narrower shadows, the failure is cleared and the run ends.
    """
    (code, runner, run_dir), _ = narrowing_rerun

    assert runner.calls == [DECLARING, VERIFYING] * 3
    assert self_route_records(run_dir) == []
    assert not (run_dir / story_coordinator.prompt_file(DECLARING, 2, 1)).exists()
    assert code == 0
    assert failure_in(run_dir) == {}
    assert state_of(run_dir)["status"] == "completed"


def test_the_failure_stands_while_the_narrowed_pass_is_the_latest_result(
    target_root, harness_root, monkeypatch,
):
    """The middle of that run, which its end does not show: at the moment the
    judging stage is entered after the narrowed pass, the record in state is
    still the *original* failure's — the pass that ran less did not clear it.

    Its control is the same snapshot one iteration later in the same run, after
    the unnarrowed rerun, where the field is empty.
    """
    scoping(monkeypatch, [(), NARROWED, ()])
    runner = Snapshotting(target_root,
                          plan_touching_nothing_after([BROKEN, REPAIR, REPAIR]),
                          verdicts=[FAILED, FAILED, PASS])
    story_coordinator.run_story(STORY_ID, harness_root, target_root, runner)
    run_dir = run_dir_of(target_root)
    failing_result, _ = retained_pair(run_dir, 1, 0)

    # snapshots: [declaring, verifier, declaring, verifier, declaring, verifier]
    after_the_narrowed_pass = runner.snapshots[3][FIELD]
    after_the_unnarrowed_one = runner.snapshots[5][FIELD]

    assert after_the_narrowed_pass["stage"] == DECLARING
    assert after_the_narrowed_pass["attempt"] == 1
    assert after_the_narrowed_pass["result_path"] == failing_result
    assert read_json(run_dir / failing_result)["exit_code"] != 0
    assert after_the_unnarrowed_one == {}


def test_the_narrowing_rerun_records_no_self_route_and_spends_no_retry_of_its_own(
    narrowing_rerun,
):
    """What the advance over a standing failure costs: nothing a self-route
    spends and nothing extra a retry spends.

    Every retry this run took was one the verifier asked for — three
    verifications, two retries — so the count is the verdicts rather than the
    reruns, and the self-route budget is untouched.
    """
    (_, runner, run_dir), _ = narrowing_rerun

    assert self_route_records(run_dir) == []
    assert [e for e in history_of(run_dir) if e["event"] == "self-routed"] == []
    assert state_of(run_dir)["retry_count"] == runner.calls.count(VERIFYING) - 1


def test_the_carry_forward_line_cites_the_original_failures_retained_evidence(
    narrowing_rerun,
):
    """A reader of events.log is pointed at the failure that still stands rather
    than at the rerun that passed: the line the non-shadowing rerun writes names
    the *first* invocation's red run — its stage, its exit code and the pair its
    evidence was retained under.

    The passing rerun's own retained pair is the control beside it: it exists,
    it records a pass, and it is not what the line names.
    """
    (_, _, run_dir), _ = narrowing_rerun
    failing_result, failing_output = retained_pair(run_dir, 1, 0)
    narrowed_result, _ = retained_pair(run_dir, 2, 0)
    lines = carry_forward_lines(run_dir)

    assert len(lines) == 2
    for line in lines:
        assert line["message"].count(DECLARING) >= 1
        assert failing_result in line["message"]
        assert failing_output in line["message"]
        assert narrowed_result not in line["message"]
    assert read_json(run_dir / failing_result)["exit_code"] != 0
    assert read_json(run_dir / narrowed_result)["exit_code"] == 0
    assert Path(failing_output).is_file()


def test_a_run_whose_suite_never_failed_writes_no_such_line(green_run):
    """The control for that reading: the same scan over a run that made the
    same declared suite run and passed it finds nothing, so the lines above are
    written where a failure stood rather than at every advance."""
    _, _, run_dir = green_run
    assert carry_forward_lines(run_dir) == []


def declared_clear_lines(run_dir: Path, outstanding: dict) -> list[dict]:
    """Every event saying a declared rerun cleared this failure.

    Read as the line the coordinator composes for that failure, the way
    `shadow_lines` reads the clean-clone clear, so the reading is derived
    rather than spelled; a list rather than an assertion, so the same reading
    can be made of a run that cleared nothing, which is the control.
    """
    line = story_coordinator.suite_failure_shadowed_by_declared_rerun_note(
        outstanding)
    return [entry for entry in history_of(run_dir) if entry["message"] == line]


def cleared_failure_notes(run_dir: Path) -> list[dict]:
    """Every note saying some failure was cleared, whichever composer said it.

    Read by the words both shadowing composers share rather than by a record,
    so a control run with no failure to compose a record from can still be
    read — and so a note composed off a record other than the one a test
    expected is found rather than missed.
    """
    return [entry for entry in history_of(run_dir)
            if entry["event"] == "note"
            and "that failure is cleared" in entry["message"]]


def snapshotted_run(target_root: Path, harness: Path, plan, verdicts):
    """A run driven with the snapshotting runner, so the outstanding record can
    be read off the state the stage after the failure opened on rather than
    composed here: returns the exit code, the runner and the run directory."""
    runner = Snapshotting(target_root, plan, verdicts=verdicts)
    code = story_coordinator.run_story(STORY_ID, harness, target_root, runner)
    return code, runner, run_dir_of(target_root)


def test_the_declared_rerun_records_the_clear_it_made_in_the_events_log(
    target_root, harness_root,
):
    """A reader of events.log meets the declared clear the way they meet the
    clean-clone one: as one note from the declaring stage, composed by the
    sibling composer, naming the failure it cleared — its stage, its exit code
    and the pair its evidence was retained under, verbatim off the record the
    judging stage opened on after the red run — and the pair it names still
    exists and still records that failure. The same run says nothing in the
    clean-clone composer's words: that line is the resume cases' and the
    reading that finds it there finds nothing here, so the note is the
    declared block's own rather than the other block's borrowed."""
    code, runner, run_dir = snapshotted_run(
        target_root, harness_root,
        plan_touching_nothing_after([BROKEN, REPAIR]), [FAILED, PASS])
    outstanding = runner.snapshots[1][FIELD]
    (line,) = declared_clear_lines(run_dir, outstanding)
    record, output = named_pair(run_dir, outstanding)

    assert code == 0
    assert runner.calls == [DECLARING, VERIFYING, DECLARING, VERIFYING]
    assert line["stage"] == DECLARING
    assert line["event"] == "note"
    assert outstanding["stage"] in line["message"]
    assert f"exited {outstanding['exit_code']}" in line["message"]
    assert outstanding["result_path"] in line["message"]
    assert outstanding["output_path"] in line["message"]
    assert record.is_file() and output.is_file()
    assert read_json(record)["exit_code"] == outstanding["exit_code"] != 0
    assert line["message"] in (run_dir / "events.log").read_text(
        encoding="utf-8")
    assert cleared_failure_notes(run_dir) == [line]
    assert shadow_lines(run_dir, outstanding) == []
    assert failure_in(run_dir) == {}


def test_the_declared_clear_is_written_after_the_rerun_and_after_the_carry_forward(
    target_root, harness_root,
):
    """Where in the stream the note sits: after the rerun that cleared the
    failure was announced, and after the carry-forward line the red run wrote
    — so a reader following the log meets the failure, then its carry-forward,
    then the rerun, then the clear, in the order the run took them."""
    _, runner, run_dir = snapshotted_run(
        target_root, harness_root,
        plan_touching_nothing_after([BROKEN, REPAIR]), [FAILED, PASS])
    outstanding = runner.snapshots[1][FIELD]
    history = history_of(run_dir)
    (note,) = declared_clear_lines(run_dir, outstanding)
    announcements = [i for i, e in enumerate(history)
                     if e["event"] == "suite-rerun-started"
                     and e.get("artifacts") == [SUITE_ARTIFACT]]
    carried = [i for i, e in enumerate(history)
               if e in carry_forward_lines(run_dir)]

    assert len(announcements) == 2
    assert len(carried) == 1
    assert history.index(note) > announcements[-1]
    assert history.index(note) > carried[-1]


def test_a_run_whose_declared_suite_never_failed_writes_no_declared_clear_note(
    green_run,
):
    """The control for that reading, on the run that made the same declared
    suite run and passed it first time: read as the line composed off the
    record the block would have written had that run failed — the declaring
    stage, its retained pair, its exit code — and read by the words both
    shadowing composers share, the same run says nothing about clearing a
    failure. So the note above is written where a failure was cleared rather
    than at every green declared run."""
    _, _, run_dir = green_run
    result, output = retained_pair(run_dir, 1, 0)
    had_it_failed = {"stage": DECLARING, "attempt": 1, "try": 0, "scope": [],
                     "exit_code": read_json(run_dir / result)["exit_code"],
                     "result_path": result, "output_path": output}

    assert read_json(run_dir / result)["exit_code"] == 0
    assert declared_clear_lines(run_dir, had_it_failed) == []
    assert cleared_failure_notes(run_dir) == []
    assert carry_forward_lines(run_dir) == []


def test_a_narrowed_pass_writes_no_declared_clear_and_the_unnarrowed_one_writes_it(
    target_root, harness_root, monkeypatch,
):
    """The absence and its demonstration in one run: the pass narrowed to a
    scope the failure did not carry clears nothing and says nothing about
    clearing, and the later rerun at a scope no narrower clears the failure
    and writes exactly one note naming it. That the note is the third run's
    and not the second's is read off the stream: it sits after the third
    declared run's announcement and after both carry-forward lines, and there
    is one where there were two passes. What it names is the *original*
    failure's retained pair; the narrowed pass's own pair, which exists and
    records a pass, is the control beside it."""
    scoping(monkeypatch, [(), NARROWED, ()])
    _, runner, run_dir = snapshotted_run(
        target_root, harness_root,
        plan_touching_nothing_after([BROKEN, REPAIR, REPAIR]),
        [FAILED, FAILED, PASS])
    # snapshots: [declaring, verifier, declaring, verifier, declaring, verifier]
    outstanding = runner.snapshots[1][FIELD]
    after_the_narrowed_pass = runner.snapshots[3][FIELD]
    history = history_of(run_dir)
    (note,) = declared_clear_lines(run_dir, outstanding)
    narrowed_result, narrowed_output = retained_pair(run_dir, 2, 0)
    announcements = [i for i, e in enumerate(history)
                     if e["event"] == "suite-rerun-started"
                     and e.get("artifacts") == [SUITE_ARTIFACT]]
    carried = [i for i, e in enumerate(history)
               if e in carry_forward_lines(run_dir)]

    assert runner.calls == [DECLARING, VERIFYING] * 3
    assert after_the_narrowed_pass == outstanding
    assert cleared_failure_notes(run_dir) == [note]
    assert note["stage"] == DECLARING
    assert len(announcements) == 3
    assert len(carried) == 2
    assert history.index(note) > announcements[-1]
    assert history.index(note) > carried[-1]
    assert outstanding["result_path"] in note["message"]
    assert outstanding["output_path"] in note["message"]
    assert narrowed_result not in note["message"]
    assert narrowed_output not in note["message"]
    assert read_json(run_dir / narrowed_result)["exit_code"] == 0
    assert read_json(run_dir / outstanding["result_path"])["exit_code"] != 0
    assert failure_in(run_dir) == {}


def test_the_declared_clear_reaches_the_state_before_the_note_is_appended(
    target_root, harness_root, monkeypatch,
):
    """The clear reaches state.json before the note is appended, in the order
    the clean-clone block already uses, so a crash between the decision and
    the note cannot leave a failure recorded that a shadowing rerun has
    superseded. Observed by interleaving every save with every event: the
    first save carrying an empty field after the failure was recorded precedes
    the note. The saves between the failure and the clear are the control —
    every one of them carries the failure, so the empty save is the
    decision's rather than one from before the suite ran red."""
    sequence: list[tuple] = []
    real_save, real_append = (story_coordinator.save_state,
                              story_coordinator.append_event)

    def saving(run_dir, state, *args, **kwargs):
        sequence.append(("save", dict(getattr(state, FIELD))))
        return real_save(run_dir, state, *args, **kwargs)

    def appending(run_dir, message, *args, **kwargs):
        sequence.append(("event", message))
        return real_append(run_dir, message, *args, **kwargs)

    monkeypatch.setattr(story_coordinator, "save_state", saving)
    monkeypatch.setattr(story_coordinator, "append_event", appending)

    code, _, _ = drive(target_root, harness_root,
                       plan_touching_nothing_after([BROKEN, REPAIR]),
                       verdicts=[FAILED, PASS])

    failing_saves = [i for i, (kind, value) in enumerate(sequence)
                     if kind == "save" and value]
    outstanding = sequence[failing_saves[0]][1]
    line = story_coordinator.suite_failure_shadowed_by_declared_rerun_note(
        outstanding)
    note_at = sequence.index(("event", line))
    empty_saves_after_the_failure = [
        i for i, (kind, value) in enumerate(sequence)
        if kind == "save" and value == {} and i > failing_saves[0]]

    assert code == 0
    assert outstanding["stage"] == DECLARING
    assert empty_saves_after_the_failure
    assert empty_saves_after_the_failure[0] < note_at
    assert all(value == outstanding
               for kind, value in sequence[failing_saves[0]:note_at]
               if kind == "save" and value)
    assert failing_saves[-1] < empty_saves_after_the_failure[0]


def test_a_run_whose_suite_stays_red_ends_on_the_verifiers_exhausted_retries(
    target_root, harness_root,
):
    """A run that never narrows anything and never repairs the suite still
    stops, and what stops it is the retry ceiling the judging stage's own
    recommendations spend rather than the declaring stage's self-route budget —
    which this run never touches.

    The failure that ended it is outstanding in the state the run left, which is
    what the resume cases below are built on.
    """
    code, runner, run_dir = drive(
        target_root, harness_root,
        plan_touching_nothing_after([BROKEN] * MORE_THAN_ANY_RUN_TAKES),
        verdicts=[FAILED])
    reason = story_coordinator.escalation_reason(run_dir)

    assert code == 2
    assert "retries are exhausted" in reason
    assert self_route_records(run_dir) == []
    assert runner.calls.count(DECLARING) == runner.calls.count(VERIFYING)
    assert failure_in(run_dir)["stage"] == DECLARING


# --------------------------------------------------------------------------
# A resume carries the failure, and the clean-clone check is what clears it
#
# The run below is story-158's shape exactly: refused at the retry ceiling with
# the suite red, repaired by hand, and re-entered at the verifying stage. That
# re-entry makes no declared suite run — it is past the stage that declares
# one — so the only run of the suite it makes is the clean-clone check the
# verifying stage declares, and that check is now what decides whether the
# failure it carries in is the failure it carries out.
# --------------------------------------------------------------------------


#: The artifact the verifying stage declares for its clean-clone check, read
#: off the loaded definition rather than spelled here.
CLEAN_CLONE_ARTIFACT = next(
    stage["clean_clone"]["result"] for stage in WORKFLOW["stages"]
    if "clean_clone" in stage)


def test_the_verifying_stage_declares_the_check_and_the_declaring_one_does_not():
    """The premise the resume cases rest on: the check that can shadow a
    failure is declared on the stage a resume re-enters at, and nowhere
    earlier — so a re-entry there makes exactly one run of the suite."""
    declaring = [s["name"] for s in WORKFLOW["stages"] if "clean_clone" in s]
    assert declaring == [VERIFYING]
    assert CLEAN_CLONE_ARTIFACT not in COORDINATOR_SOURCE


def leave_a_failure_outstanding(target_root: Path, harness: Path,
                                workflow: dict | None = None) -> Path:
    """Drive a run until the retry ceiling stops it with the suite still red,
    which is how a real run leaves the field set; then repair the suite by
    hand, commit, and mark the run directory as one interrupted at the
    verifying stage — the shape a crashed run leaves, which re-enters with no
    guard. Returns the run directory, re-enterable past the declaring stage.
    """
    code, _, run_dir = drive(
        target_root, harness,
        plan_touching_nothing_after([BROKEN] * MORE_THAN_ANY_RUN_TAKES),
        verdicts=[FAILED], workflow=workflow)
    assert code == 2, "the shape was meant to escalate"
    assert failure_in(run_dir), "the shape was meant to leave a failure"

    write(target_root / SENTINEL, "repaired\n")
    conftest.commit_setup(target_root, "the developer repaired the suite")
    write_state(run_dir, {"status": "running", "current_stage": VERIFYING})
    return run_dir


@pytest.fixture
def resumable_run(target_root, harness_root):
    """A run left with a failure outstanding and re-enterable past the stage
    that declares the suite run, under the workflow whose verifying stage
    declares the clean-clone check."""
    return target_root, leave_a_failure_outstanding(target_root, harness_root)


def shadow_lines(run_dir: Path, outstanding: dict) -> list[dict]:
    """Every event saying a clean-clone pass cleared this failure.

    Read as the line the coordinator composes for that failure, so the reading
    is derived rather than spelled; a list rather than an assertion, so the
    same reading can be made of a run that cleared nothing, which is the
    control.
    """
    line = story_coordinator.suite_failure_shadowed_by_clean_clone_note(
        outstanding)
    return [entry for entry in history_of(run_dir) if entry["message"] == line]


def clean_clone_record(run_dir: Path) -> dict:
    return record_of(run_dir, CLEAN_CLONE_ARTIFACT)


def named_pair(run_dir: Path, outstanding: dict) -> tuple[Path, Path]:
    """The outstanding failure's retained record and output, at the paths the
    record names and nothing else: result_path joined to the run directory,
    output_path as given. Every line composed from the record names these two
    paths verbatim, so reading here is reading where the lines point."""
    return (Path(run_dir) / outstanding["result_path"],
            Path(outstanding["output_path"]))


def moved_under(run_dir: Path, outstanding: dict, entry: int = 1) -> dict:
    """The record as a resume opening `entry` should leave it: the same record
    with its two paths one directory further down, result_path still
    run-relative and output_path still absolute.

    Composed from `entry_dir` rather than spelled, so the directory name is
    written nowhere here; composed here rather than through the helper under
    test, so a driven case compares the coordinator's result against an
    expectation it did not produce.
    """
    opened = story_coordinator.entry_dir(Path(run_dir), entry)
    below = opened.relative_to(run_dir)
    return {**outstanding,
            "result_path": str(below / outstanding["result_path"]),
            "output_path": str(
                opened / Path(outstanding["output_path"]).relative_to(run_dir))}


def resume(target_root: Path, harness: Path, workflow: dict | None = None):
    """The re-entry, driven with the snapshotting runner: returns the exit
    code, the runner and the outstanding record as the resumed stage opened on
    it — which is the record after the resume's move, and the one every line
    the re-entry composes is read off. The run's end is not where to read it:
    a re-entry that clears the failure ends with the field empty."""
    runner = Snapshotting(target_root, workflow=workflow)
    code = story_coordinator.run_story(STORY_ID, harness, target_root, runner)
    return code, runner, runner.snapshots[0].get(FIELD, {})


def test_a_resume_restores_the_outstanding_failure(resumable_run, harness_root):
    """It is evidence that a failure is unrepaired rather than a live allowance,
    so the re-entry neither clears it on loading nor writes it afresh: the
    record the resumed verifying stage opens on is the one the interrupted
    entry left, field for field, differing in exactly the two paths — which
    follow the pair the resume moved under its entry directory, in the forms
    they already took, and hold the files there. What clears it afterwards is
    a run of the suite, asserted below, and the same run's ending is the
    control here — the field is empty by then, so the snapshot at entry is
    where the restored record is observable rather than a reading of something
    never cleared."""
    target_root, run_dir = resumable_run
    left_behind = failure_in(run_dir)
    before_the_move = named_pair(run_dir, left_behind)
    assert all(path.is_file() for path in before_the_move)

    code, runner, restored = resume(target_root, harness_root)
    record, output = named_pair(run_dir, restored)

    assert code == 0
    assert runner.calls == [VERIFYING]
    assert restored == moved_under(run_dir, left_behind)
    assert restored != left_behind
    assert {key for key in restored if restored[key] != left_behind[key]} == {
        "result_path", "output_path"}
    assert not Path(restored["result_path"]).is_absolute()
    assert Path(restored["output_path"]).is_absolute()
    assert record.is_file() and output.is_file()
    assert read_json(record)["exit_code"] == left_behind["exit_code"] != 0
    assert not any(path.exists() for path in before_the_move)
    assert left_behind["stage"] == DECLARING
    assert failure_in(run_dir) == {}


def test_the_resume_rewrites_the_state_and_not_the_retry_record(
    resumable_run, harness_root,
):
    """The judgement the story records, driven: the outstanding-failure field
    is the one record a resume repoints, because it is a live pointer the
    coordinator routes on and reads back into prose. The retry record is
    append-only evidence whose moved archive paths the log resolves, and the
    same resume leaves it byte for byte as it was. The state file, rewritten
    by the same resume and read the same way, is the control for the absence.
    The archive note is the other thing left as it was: it names what moved
    by the run-relative names the move was made under, which is the pair's
    pre-resume name rather than the repointed one."""
    target_root, run_dir = resumable_run
    retry_record = story_coordinator._retry_record_file(run_dir)
    assert story_coordinator.load_retry_records(run_dir), (
        "the run was meant to have taken retries before it was stopped")
    retries_before = retry_record.read_bytes()
    state_before = (run_dir / "state.json").read_bytes()
    left_behind = failure_in(run_dir)

    code, _, restored = resume(target_root, harness_root)
    archive_notes = [e for e in history_of(run_dir)
                     if e["event"] == "note" and "archived under" in e["message"]]

    assert code == 0
    assert retry_record.read_bytes() == retries_before
    assert (run_dir / "state.json").read_bytes() != state_before
    assert len(archive_notes) == 1
    assert left_behind["result_path"] in archive_notes[0]["message"]
    assert restored["result_path"] not in archive_notes[0]["message"]


def test_a_resume_completes_when_the_clean_clone_check_shadows_the_failure(
    resumable_run, harness_root,
):
    """The headline: the re-entry runs only the stages after the one that
    declares the suite run, and the clean-clone check it makes ran, exited
    zero, and recorded a scope no narrower than the failure's — so it
    supersedes the failure, the field is cleared and the run completes with a
    completion report rather than escalating at the end of its workflow."""
    target_root, run_dir = resumable_run
    outstanding = failure_in(run_dir)

    code, runner, _ = drive(target_root, harness_root)
    clean = clean_clone_record(run_dir)

    assert code == 0
    assert runner.calls == [VERIFYING]
    assert clean["ran"] is True
    assert clean["exit_code"] == 0
    assert SHADOWS(clean["scope"], outstanding["scope"]) is True
    assert failure_in(run_dir) == {}
    assert state_of(run_dir)["status"] == "completed"
    assert (run_dir / "completion-report.md").is_file()
    assert "story-completed" in [e["event"] for e in history_of(run_dir)]


def test_the_shadowing_clear_is_reconstructable_from_the_events_log(
    resumable_run, harness_root,
):
    """A reader of events.log meets the clear as a note from the verifying
    stage naming the failure it cleared — its stage, its exit code and the pair
    its evidence was retained under — and the pair it names still exists and
    still records that failure, so the clear is reconstructable without
    state.json. The pair is read at the paths the note names and nowhere
    else: the resume that cleared the failure moved the entry those paths
    belong to under its entry directory before the verifying stage ran, and
    the record the note is composed from followed the move — so the paths are
    the pair's current ones, not the ones the failure was first recorded
    under. The pre-resume paths are the control: the same reading finds no
    note composed from them, and nothing is at them any more. The clean-clone
    pass itself is announced beside it."""
    target_root, run_dir = resumable_run
    left_behind = failure_in(run_dir)

    _, _, outstanding = resume(target_root, harness_root)
    (line,) = shadow_lines(run_dir, outstanding)
    record, output = named_pair(run_dir, outstanding)

    assert line["stage"] == VERIFYING
    assert line["event"] == "note"
    assert outstanding["stage"] in line["message"]
    assert f"exited {outstanding['exit_code']}" in line["message"]
    assert outstanding["result_path"] in line["message"]
    assert outstanding["output_path"] in line["message"]
    assert record.is_file() and output.is_file()
    assert read_json(record)["exit_code"] != 0
    assert line["message"] in (run_dir / "events.log").read_text(
        encoding="utf-8")
    assert [e["event"] for e in history_of(run_dir)
            if e["event"] == "clean-clone-passed"] == ["clean-clone-passed"]
    assert shadow_lines(run_dir, left_behind) == []
    assert not any(path.exists() for path in named_pair(run_dir, left_behind))


def test_the_state_is_saved_at_the_decision_and_not_only_at_the_end(
    resumable_run, harness_root, monkeypatch,
):
    """The clear reaches state.json before the note is appended, so a crash
    between the decision and the run's end cannot leave a failure recorded
    that a shadowing pass has superseded. Observed by interleaving every save
    with every event: the first save carrying an empty field precedes the
    note. The saves before the decision are the control — every one of them
    still carries the failure, in the form the resume's move left it, so the
    empty save is the decision's rather than the resume's."""
    target_root, run_dir = resumable_run
    outstanding = moved_under(run_dir, failure_in(run_dir))
    line = story_coordinator.suite_failure_shadowed_by_clean_clone_note(
        outstanding)
    sequence: list[tuple] = []
    real_save, real_append = (story_coordinator.save_state,
                              story_coordinator.append_event)

    def saving(run_dir, state, *args, **kwargs):
        sequence.append(("save", dict(getattr(state, FIELD))))
        return real_save(run_dir, state, *args, **kwargs)

    def appending(run_dir, message, *args, **kwargs):
        sequence.append(("event", message))
        return real_append(run_dir, message, *args, **kwargs)

    monkeypatch.setattr(story_coordinator, "save_state", saving)
    monkeypatch.setattr(story_coordinator, "append_event", appending)

    code, _, _ = drive(target_root, harness_root)

    assert code == 0
    note_at = sequence.index(("event", line))
    empty_saves = [i for i, (kind, value) in enumerate(sequence)
                   if kind == "save" and value == {}]
    assert empty_saves and empty_saves[0] < note_at
    assert all(value == outstanding for kind, value in sequence[:empty_saves[0]]
               if kind == "save")
    assert any(kind == "save" for kind, _ in sequence[:empty_saves[0]])


def test_that_resume_costs_no_declared_suite_run(resumable_run, harness_root):
    """The clear is decided on the clean-clone check's run rather than by
    making the declared run again: the re-entry announces no run of the
    declared artifact. The runs the interrupted entry announced are the
    control — the same reading of the same stream finds those, so the absence
    is about this entry rather than about the reading — and the clean-clone
    announcement the re-entry does make is beside them."""
    target_root, run_dir = resumable_run
    before = declared_runs_announced(run_dir)
    clean_before = runs_announced(run_dir, CLEAN_CLONE_ARTIFACT)

    drive(target_root, harness_root)

    assert before, "the interrupted entry announced no run to compare against"
    assert declared_runs_announced(run_dir) == before
    assert runs_announced(run_dir, CLEAN_CLONE_ARTIFACT) == clean_before + 1


def runs_announced(run_dir: Path, artifact: str) -> int:
    return len([event for event in history_of(run_dir)
                if event["event"] == "suite-rerun-started"
                and event.get("artifacts") == [artifact]])


def declared_runs_announced(run_dir: Path) -> int:
    return runs_announced(run_dir, SUITE_ARTIFACT)


def test_the_identical_resume_with_the_failure_already_clear_writes_no_note(
    resumable_run, harness_root,
):
    """The control for the note: the same run directory, resumed the same way,
    differing in the one field — and it completes identically but says
    nothing about clearing a failure, because there was none to clear. So the
    note above is written where a failure stood rather than at every clean-
    clone pass."""
    target_root, run_dir = resumable_run
    outstanding = failure_in(run_dir)
    write_state(run_dir, {FIELD: {}})

    code, runner, _ = drive(target_root, harness_root)

    assert code == 0
    assert runner.calls == [VERIFYING]
    assert state_of(run_dir)["status"] == "completed"
    assert (run_dir / "completion-report.md").is_file()
    assert clean_clone_record(run_dir)["exit_code"] == 0
    assert shadow_lines(run_dir, outstanding) == []
    assert shadow_lines(run_dir, moved_under(run_dir, outstanding)) == []


# --------------------------------------------------------------------------
# The refusal, rebuilt on the clean-clone check
#
# A clean-clone pass is refused as evidence on the same terms a narrowed
# declared run is, and a check that did not run is not a pass. Both are made
# by substituting for `clean_clone_check` the way `scoping` substitutes for
# `suite_run_check`: the real check runs, writes its real record and returns
# its real result, and only the result the routing reads is altered.
# --------------------------------------------------------------------------


def clean_clone_scoping(monkeypatch, scope: tuple) -> list:
    """Make the clean-clone check report `scope` while still really running.
    Returns the list the real results are appended to, so a test can assert
    the check did run and did pass underneath the substitution."""
    real = story_coordinator.clean_clone_check
    ran: list = []

    def substituting(*args, **kwargs):
        result = real(*args, **kwargs)
        ran.append(result)
        return dataclasses.replace(result, scope=scope)

    monkeypatch.setattr(story_coordinator, "clean_clone_check", substituting)
    return ran


NOT_RUN_REASON = "substituted for this test: the check did not run"


def clean_clone_not_running(monkeypatch) -> list:
    """Make the clean-clone check report that it did not run, while the real
    check still runs underneath — so a refusal here is about the result the
    routing read rather than about a suite that failed."""
    real = story_coordinator.clean_clone_check
    ran: list = []

    def substituting(*args, **kwargs):
        result = real(*args, **kwargs)
        ran.append(result)
        return dataclasses.replace(
            result, ran=False, exit_code=None, output_tail=None,
            output_path=None, clone_path=None, reason=NOT_RUN_REASON)

    monkeypatch.setattr(story_coordinator, "clean_clone_check", substituting)
    return ran


def test_the_substitution_narrows_the_check_below_what_failed(
    resumable_run, harness_root, monkeypatch,
):
    """The premise the refusal rests on: the substituted scope is genuinely
    not a subset of the failure's, the real check ran, and it passed — so the
    refusal below is about the scope and about nothing else."""
    target_root, run_dir = resumable_run
    outstanding = failure_in(run_dir)
    ran = clean_clone_scoping(monkeypatch, NARROWED)

    drive(target_root, harness_root)

    assert SHADOWS(NARROWED, outstanding["scope"]) is False
    assert [(r.ran, r.exit_code) for r in ran] == [(True, 0)]
    assert SHADOWS(ran[0].scope, outstanding["scope"]) is True


def test_a_clean_clone_pass_at_a_narrower_scope_leaves_the_refusal_standing(
    resumable_run, harness_root, monkeypatch,
):
    """The refusal the story keeps: a clean-clone pass that recorded a scope
    narrower than the failure's established nothing about what it stopped
    running, so it clears nothing, the carry-forward line is written where the
    declared block would write it, and the run escalates at the end of its
    workflow naming the failure that still stands and where its record and its
    output are kept — which, on this resumed run, is under the entry directory
    the resume moved them to, so both the reason and the carry-forward line
    name paths that exist. The pre-resume paths are the control: neither line
    names them and nothing is at them."""
    target_root, run_dir = resumable_run
    left_behind = failure_in(run_dir)
    clean_clone_scoping(monkeypatch, NARROWED)

    code, runner, outstanding = resume(target_root, harness_root)
    reason = story_coordinator.escalation_reason(run_dir)
    carried = [line for line in carry_forward_lines(run_dir)
               if line["stage"] == VERIFYING]
    record, output = named_pair(run_dir, outstanding)

    assert code == 2
    assert state_of(run_dir)["status"] == "escalated"
    assert runner.calls == [VERIFYING]
    assert outstanding == moved_under(run_dir, left_behind)
    assert failure_in(run_dir) == outstanding
    assert outstanding["stage"] in reason
    assert f"exited {outstanding['exit_code']}" in reason
    assert outstanding["result_path"] in reason
    assert outstanding["output_path"] in reason
    assert record.is_file() and output.is_file()
    assert read_json(record)["exit_code"] == outstanding["exit_code"] != 0
    assert not (run_dir / "completion-report.md").exists()
    assert "story-completed" not in [e["event"] for e in history_of(run_dir)]
    assert shadow_lines(run_dir, outstanding) == []
    assert shadow_lines(run_dir, left_behind) == []
    assert len(carried) == 1
    assert outstanding["result_path"] in carried[0]["message"]
    assert outstanding["output_path"] in carried[0]["message"]
    assert left_behind["output_path"] not in carried[0]["message"]
    assert left_behind["output_path"] not in reason
    assert not any(path.exists() for path in named_pair(run_dir, left_behind))


def test_a_clean_clone_check_that_did_not_run_clears_nothing(
    resumable_run, harness_root, monkeypatch,
):
    """Not a pass: a check that reports it did not run escalates as it did
    before this story, naming the reason it could not run, and the failure it
    found outstanding is the failure it leaves — no clear, no note, no
    completion. The real check underneath passed, which is the control: the
    refusal is about the result the routing read rather than about the
    suite."""
    target_root, run_dir = resumable_run
    outstanding = moved_under(run_dir, failure_in(run_dir))
    ran = clean_clone_not_running(monkeypatch)

    code, runner, _ = drive(target_root, harness_root)
    reason = story_coordinator.escalation_reason(run_dir)

    assert code == 2
    assert state_of(run_dir)["status"] == "escalated"
    assert runner.calls == [VERIFYING]
    assert "could not run" in reason
    assert NOT_RUN_REASON in reason
    assert failure_in(run_dir) == outstanding
    assert shadow_lines(run_dir, outstanding) == []
    assert not (run_dir / "completion-report.md").exists()
    assert [(r.ran, r.exit_code) for r in ran] == [(True, 0)]


def test_the_clear_is_decided_by_the_one_rule_and_by_no_second_one(
    resumable_run, harness_root, monkeypatch,
):
    """The comparison is `suite_run_shadows` and nothing written beside it:
    with that one name made to answer no, the resume that completes
    unsubstituted — same run directory, same check, same recorded scopes — is
    refused instead. The rule was asked with the check's recorded scope and
    the failure's, and recency decided nothing: the pass was the more recent
    result and did not clear the failure on it."""
    target_root, run_dir = resumable_run
    outstanding = moved_under(run_dir, failure_in(run_dir))
    asked: list[tuple] = []

    def refusing(passing_scope, failing_scope):
        asked.append((tuple(passing_scope), tuple(failing_scope)))
        return False

    monkeypatch.setattr(story_coordinator, "suite_run_shadows", refusing)

    code, _, _ = drive(target_root, harness_root)
    clean = clean_clone_record(run_dir)

    assert code == 2
    assert state_of(run_dir)["status"] == "escalated"
    assert clean["exit_code"] == 0
    assert asked == [(tuple(clean["scope"]), tuple(outstanding["scope"]))]
    assert failure_in(run_dir) == outstanding
    assert shadow_lines(run_dir, outstanding) == []


def without_the_clean_clone_declaration(tmp_path: Path,
                                        name: str = "no-clean-clone"
                                        ) -> tuple[Path, dict]:
    """The same workflow with the clean-clone declaration removed from every
    stage, which is the shadowing run disabled: the key is the switch."""
    workflow = json.loads(json.dumps(WORKFLOW))
    for stage in workflow["stages"]:
        stage.pop("clean_clone", None)
    workflow["name"] = name
    root = materialize(workflow, tmp_path / name)
    return root, workflow


def test_a_verifier_declaring_no_clean_clone_check_still_reaches_the_refusal(
    make_target, tmp_path,
):
    """The end-of-run refusal is unchanged: under a workflow whose verifying
    stage declares no clean-clone check, the same resume makes no run of the
    suite at all, nothing can shadow the failure, and the run escalates naming
    it exactly as it did before this story. The resume that completes above,
    differing only in the declaration, is the control."""
    harness, workflow = without_the_clean_clone_declaration(tmp_path)
    target_root = make_target("undeclared-clean-clone", workflow=workflow["name"])
    run_dir = leave_a_failure_outstanding(target_root, harness, workflow)
    outstanding = moved_under(run_dir, failure_in(run_dir))
    announced_before = len([e for e in history_of(run_dir)
                            if e["event"] == "suite-rerun-started"])

    code, runner, _ = drive(target_root, harness, workflow=workflow)
    reason = story_coordinator.escalation_reason(run_dir)
    record, output = named_pair(run_dir, outstanding)

    assert code == 2
    assert state_of(run_dir)["status"] == "escalated"
    assert runner.calls == [VERIFYING]
    assert failure_in(run_dir) == outstanding
    assert outstanding["stage"] in reason
    assert f"exited {outstanding['exit_code']}" in reason
    assert outstanding["result_path"] in reason
    assert outstanding["output_path"] in reason
    assert record.is_file() and output.is_file()
    assert not (run_dir / "completion-report.md").exists()
    assert len([e for e in history_of(run_dir)
                if e["event"] == "suite-rerun-started"]) == announced_before
    assert shadow_lines(run_dir, outstanding) == []


# --------------------------------------------------------------------------
# The helper that repoints the record after a resume's move
#
# Called directly, with values written here: the subject is a pure function
# over a record, the names a resume moved, the entry directory it opened and
# the run directory, and nothing about it needs a run driven. The driven cases
# above show the coordinator calling it where the move is made.
# --------------------------------------------------------------------------


REPOINT = story_coordinator.repoint_outstanding_failure


def a_failure_under(run_dir: Path) -> dict:
    """`A_FAILURE` with its output where the suite-run check would have put
    it: absolute, directly beneath the run directory, so its run-relative
    name is the one a move would list."""
    return {**A_FAILURE, "output_path": str(run_dir / "kept-output.txt")}


def the_pair_of(record: dict, run_dir: Path) -> list[str]:
    """The record's pair as a move would name it: run-relative, both halves."""
    return [record["result_path"],
            str(Path(record["output_path"]).relative_to(run_dir))]


def test_the_helper_repoints_both_paths_in_the_forms_they_already_took(tmp_path):
    """The moved case: result_path stays run-relative and goes one directory
    further down, output_path stays absolute and goes the same way, and the
    expectation is composed from `entry_dir` here rather than read back off
    the helper."""
    run_dir = tmp_path / "run"
    record = a_failure_under(run_dir)
    opened = story_coordinator.entry_dir(run_dir, 1)

    repointed = REPOINT(record, the_pair_of(record, run_dir), opened, run_dir)

    assert repointed == moved_under(run_dir, record)
    assert not Path(repointed["result_path"]).is_absolute()
    assert Path(repointed["output_path"]).is_absolute()
    assert run_dir / repointed["result_path"] == opened / record["result_path"]
    assert Path(repointed["output_path"]) == opened / "kept-output.txt"


def test_the_helper_leaves_every_other_field_as_it_was(tmp_path):
    """Stage, attempt, try, scope and exit code pass through untouched, and the
    record handed in is not mutated: the caller assigns the result back, and a
    helper that edited its argument would have repointed the state before the
    assignment said so."""
    run_dir = tmp_path / "run"
    record = a_failure_under(run_dir)
    handed_in = json.loads(json.dumps(record))

    repointed = REPOINT(record, the_pair_of(record, run_dir),
                        story_coordinator.entry_dir(run_dir, 1), run_dir)

    assert {key for key in record if repointed[key] != record[key]} == {
        "result_path", "output_path"}
    assert set(repointed) == set(record)
    assert record == handed_in


def test_the_helper_leaves_a_record_whose_pair_did_not_move_alone(tmp_path):
    """The unmoved case: a result_path not among the moved names is a pair this
    resume did not move — never retained, or already under an earlier entry
    directory — and the record comes back exactly as it went in. The moved
    case beside it, same record and same directory differing only in the list,
    is the control: the list is what decides."""
    run_dir = tmp_path / "run"
    record = a_failure_under(run_dir)
    opened = story_coordinator.entry_dir(run_dir, 1)
    something_else = ["a-prompt.md", "attempts"]

    assert REPOINT(record, something_else, opened, run_dir) == record
    assert REPOINT(record, [], opened, run_dir) == record
    assert REPOINT(record, the_pair_of(record, run_dir), opened, run_dir) != record


def test_the_helper_does_not_repoint_a_record_twice(tmp_path):
    """The already-repointed case, which is what a second resume meets: the
    record names the pair beneath the first entry directory, the second resume
    moves the run-root names as before, and the record's result_path — one
    directory down — is not among them, so it is left exactly as it was. The
    first resume beside it is the control, moving the same record from the
    run root under the first entry directory."""
    run_dir = tmp_path / "run"
    record = a_failure_under(run_dir)
    moved_names = the_pair_of(record, run_dir)
    first = story_coordinator.entry_dir(run_dir, 1)
    second = story_coordinator.entry_dir(run_dir, 2)

    once = REPOINT(record, moved_names, first, run_dir)
    twice = REPOINT(once, moved_names, second, run_dir)

    assert once == moved_under(run_dir, record, entry=1)
    assert twice == once
    assert twice != moved_under(run_dir, once, entry=2)


def test_the_helper_leaves_an_empty_record_empty(tmp_path):
    """A run with no failure outstanding carries an empty record through its
    resume, and the helper gives it back as it was rather than inventing
    paths on it."""
    run_dir = tmp_path / "run"
    assert REPOINT({}, ["kept-result.json"],
                   story_coordinator.entry_dir(run_dir, 1), run_dir) == {}


@pytest.mark.parametrize("output_path", ["", None],
                         ids=["empty", "absent"])
def test_the_helper_repoints_the_record_alone_when_no_output_was_retained(
    tmp_path, output_path,
):
    """A record with no output path: the result is repointed and the output is
    left as it was, rather than composed into a path nothing was ever written
    to."""
    run_dir = tmp_path / "run"
    record = {**A_FAILURE, "output_path": output_path}
    opened = story_coordinator.entry_dir(run_dir, 1)

    repointed = REPOINT(record, [record["result_path"]], opened, run_dir)

    assert repointed["result_path"] == str(
        opened.relative_to(run_dir) / record["result_path"])
    assert repointed["output_path"] == output_path


def test_the_helper_leaves_an_output_the_move_did_not_list_where_it_was(
    tmp_path,
):
    """The record moved and the output did not — an output outside the run
    directory, or one the move did not find. Only what moved is repointed;
    the output beneath the run directory and among the names is the control,
    which the case above repoints."""
    run_dir = tmp_path / "run"
    elsewhere = {**A_FAILURE, "output_path": str(tmp_path / "elsewhere.txt")}
    unlisted = a_failure_under(run_dir)
    opened = story_coordinator.entry_dir(run_dir, 1)

    for record in (elsewhere, unlisted):
        repointed = REPOINT(record, [record["result_path"]], opened, run_dir)
        assert repointed["result_path"] != record["result_path"]
        assert repointed["output_path"] == record["output_path"]


def string_constants_in(function) -> list[str]:
    """Every string literal a function's body reaches for, its docstring set
    aside: what the code spells, rather than what it says about itself."""
    (definition,) = ast.parse(inspect.getsource(function)).body
    body = definition.body[1:] if (
        isinstance(definition.body[0], ast.Expr)
        and isinstance(definition.body[0].value, ast.Constant)
        and isinstance(definition.body[0].value.value, str)
    ) else definition.body
    return [node.value for statement in body for node in ast.walk(statement)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def test_the_helper_names_the_resumes_directory_nowhere():
    """The record follows the move by the names the resume moved and by the
    directory the caller derived from `entry_dir`, never by matching that
    directory's spelling — so `entry_dir` stays the one place it is named.
    `entry_dir` itself is the control: the same reading of its body finds the
    spelling."""
    spelled_in_helper = [literal for literal in string_constants_in(REPOINT)
                         if "resume" in literal.lower()]
    spelled_in_entry_dir = [
        literal for literal in string_constants_in(story_coordinator.entry_dir)
        if "resume" in literal.lower()]

    assert spelled_in_helper == []
    assert spelled_in_entry_dir


#: Every composer that reads the outstanding-failure record into a line: the
#: carry-forward note and the two shadowing siblings, one per path that clears
#: the field.
COMPOSERS_OFF_THE_RECORD = (
    story_coordinator.suite_failure_carried_forward_note,
    story_coordinator.suite_failure_shadowed_by_clean_clone_note,
    story_coordinator.suite_failure_shadowed_by_declared_rerun_note,
)


def test_the_composers_read_the_record_and_change_nothing_else_in_what_they_say(
    tmp_path,
):
    """The fix is to what the lines read, not to what they say: each composer
    given the repointed record says exactly what it said of the original with
    the two paths substituted and nothing else different. Asserted for the
    composers that are functions — the carry-forward line and the two
    shadowing siblings — and for the end-of-run refusal's shape as the driven
    refusal cases above show it, where the reason names the repointed paths
    in the wording it named the original ones in."""
    run_dir = tmp_path / "run"
    record = a_failure_under(run_dir)
    repointed = REPOINT(record, the_pair_of(record, run_dir),
                        story_coordinator.entry_dir(run_dir, 1), run_dir)

    for compose in COMPOSERS_OFF_THE_RECORD:
        original = compose(record)
        assert compose(repointed) == (
            original
            .replace(record["output_path"], repointed["output_path"])
            .replace(record["result_path"], repointed["result_path"]))
        assert compose(repointed) != original


@pytest.mark.parametrize("compose", COMPOSERS_OFF_THE_RECORD,
                         ids=lambda function: function.__name__)
def test_each_composer_names_the_stage_the_exit_code_and_the_pair(compose):
    """What every line composed off the record carries, whichever block
    appends it: the failure's stage, its exit code and both retained paths,
    verbatim. A record whose values are distinct sentinels is the control
    against a composer that names the right fields off the wrong record: each
    value appears, and a value from a second record does not."""
    record = {**A_FAILURE, "stage": "a-stage-of-its-own", "exit_code": 42,
              "result_path": "kept/one.json", "output_path": "/kept/one.txt"}
    other = {**A_FAILURE, "stage": "another-stage", "exit_code": 43,
             "result_path": "kept/two.json", "output_path": "/kept/two.txt"}
    line = compose(record)

    assert record["stage"] in line
    assert f"exited {record['exit_code']}" in line
    assert record["result_path"] in line
    assert record["output_path"] in line
    assert not any(value in line for value in (
        other["stage"], f"exited {other['exit_code']}",
        other["result_path"], other["output_path"]))


def test_the_two_shadowing_siblings_say_different_things_of_the_same_record():
    """The siblings are told apart by what they say rather than by where they
    are appended: the declared-rerun line and the clean-clone line composed off
    one record differ, each says the failure is cleared, and neither is the
    carry-forward line — so a reader of events.log knows which run cleared the
    failure from the words alone, and `declared_clear_lines` and
    `shadow_lines` read disjoint sets of events."""
    record = a_failure_under(Path("/a/run"))
    declared = story_coordinator.suite_failure_shadowed_by_declared_rerun_note(
        record)
    clean = story_coordinator.suite_failure_shadowed_by_clean_clone_note(record)
    carried = story_coordinator.suite_failure_carried_forward_note(record)

    assert declared != clean
    assert "that failure is cleared" in declared
    assert "that failure is cleared" in clean
    assert "that failure is cleared" not in carried
    assert len({declared, clean, carried}) == 3


# --------------------------------------------------------------------------
# Which definitions may name the field
#
# Enumerated over every definition under `orchestration/` rather than sampled,
# because what is asserted is that nothing outside the state and the run loop
# touches the field. The clean-clone check is inside the rule now, and its
# functions still do not name the field: the comparison is made in the loop
# where the exit code is read, as the declared run's is. The revert check is
# the one check the rule leaves alone.
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

#: The functions implementing the clean-clone check, which is inside the rule
#: and decided by the loop: they run the configured command unnarrowed and
#: return a result, and the comparison on that result is made where its exit
#: code is read — so neither of them names the field, exactly as the declared
#: run's own check does not.
INSIDE_THE_RULE_DECIDED_BY_THE_LOOP = [story_coordinator.clean_clone_check,
                                       story_coordinator.run_clean_clone]

#: The functions implementing the one check the rule leaves outside it: the
#: revert check with the selector runs it makes, which are narrowed by design.
OUTSIDE_THE_RULE = [story_coordinator.revert_check,
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


@pytest.mark.parametrize(
    "function", INSIDE_THE_RULE_DECIDED_BY_THE_LOOP + OUTSIDE_THE_RULE,
    ids=lambda function: function.__name__)
def test_no_check_function_is_among_them(function):
    """Said of the checks by name as well as by enumeration, so a reader
    looking for the story's constraint finds it stated rather than inferred.
    Each function's own source is parsed, so a read or a write anywhere inside
    it — including in a nested definition — is what is being denied."""
    assert function.__name__ not in MAY_TOUCH_IT
    assert definitions_naming_the_field(inspect.getsource(function)) == set()


def test_the_clean_clone_block_of_the_loop_calls_the_rule_and_the_note():
    """The comparison the loop makes for the clean-clone check reaches the one
    rule and the note that names the cleared failure, read off the loop's own
    source: both names appear in it, and the note's composer names the field
    nowhere — it takes the record as a value, the way the carry-forward line
    does. Beside it, `test_the_clear_is_decided_by_the_one_rule_and_by_no_second_one`
    shows the same thing driven."""
    loop = inspect.getsource(story_coordinator.run_story)
    note = story_coordinator.suite_failure_shadowed_by_clean_clone_note

    assert story_coordinator.suite_run_shadows.__name__ in loop
    assert note.__name__ in loop
    assert definitions_naming_the_field(inspect.getsource(note)) == set()
    assert (story_coordinator.suite_failure_carried_forward_note.__name__
            in loop)


def test_the_declared_block_of_the_loop_calls_the_sibling_note():
    """The same reading for the other path: the declared block reaches the
    sibling composer, read off the loop's own source, and that composer names
    the field nowhere — it takes the record as a value, exactly as its sibling
    and the carry-forward line do. So the loop and the state stay the only
    definitions naming the field, which the enumeration above holds, and the
    new composer is not what widened it. Beside it, the driven cases on the
    red-then-green run show the note appended."""
    loop = inspect.getsource(story_coordinator.run_story)
    note = story_coordinator.suite_failure_shadowed_by_declared_rerun_note

    assert note.__name__ in loop
    assert note.__name__ not in MAY_TOUCH_IT
    assert definitions_naming_the_field(inspect.getsource(note)) == set()
    assert FIELD not in inspect.getsource(note)


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
