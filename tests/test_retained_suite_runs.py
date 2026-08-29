"""Independent validation for story-084: a rerun keeps the output that explains it.

A self-route caused by a red suite used to point its reader at a file the rerun
then wrote over. This story keeps every suite run the coordinator makes: the
canonical pair at the run-directory root keeps its present meaning exactly — the
most recent run — and beside it each run writes a pair keyed by the stage, the
attempt and the try, so no run's evidence is written over by the run after it.

The subject is *what a finished run directory holds*, so almost nothing here is
asserted from source. Every case is driven through `story_coordinator.run_story`
with a fake agent runner against a target repository built under `tmp_path`, and
what is asserted is what a real run wrote. Nothing here invokes a model.

The target, the workflow, the fake runner and the fixtures are
`tests/test_coordinator_runs_the_suite.py`'s. Reused rather than copied so a
regression in that machinery reddens both files, and because the two modules are
about the same mechanism: that one is about the run the coordinator makes, this
one is about what survives of it. The workflow those runs execute is the
builder's rather than the one this repository deploys, for the reason that
module states: the stage list, the budgets and the artifact names are inputs to
this mechanism rather than its subject.

Every absence asserted here carries a demonstration that it can fail:

  * "the canonical record does not point at a retained output, and a retained
    record does not point at the canonical one" is asserted as two equalities
    against the file beside each record, with both pointers followed and the two
    records required to differ in the run they describe — so a pair of pointers
    that had collapsed onto one file would redden;
  * "a retained run of the failing turn is not the run that ended the story"
    sits beside the canonical record from the same run directory, which is the
    passing one;
  * "the self-route reason carries no more than the truncated summary it
    carried before" sits beside the retained output file it cites, which does
    hold the line the reason lacks — so the absence is about the reason rather
    than about a marker that was never printed;
  * "no coordinator decision reads a retained file" is a run whose retained
    files are deleted at the end of every turn, routing identically to the run
    that keeps them, and it sits beside the same deletion applied to the
    stage's declared required outputs, which changes the routing entirely;
  * "a workflow declaring no suite run writes neither pair" sits beside the
    identical run under the declaring workflow, which writes both;
  * "the revert check and the clean-clone check gained no retained pair" sits
    beside the suite run in the same run directory, which has one;
  * "no orchestration module spells a retained filename" sits beside the same
    scan over that source with one planted in it, and beside those filenames
    being what a driven run actually wrote;
  * "a resume does not write over the interrupted attempt's retained runs" —
    every byte of them found again under the entry the resume opened — sits
    beside the root copies of those same names, which the resumed run *did*
    write over, so the survival is a fact about the archive.

`.harness/docs/ARCHITECTURE.md` is not asserted on: this story's plan assigns it
to the documenter, the stage that runs after this one.
"""
import json
from pathlib import Path

import pytest

import story_coordinator

from test_self_routing_retry import git, write
from test_coordinator_runs_the_suite import (  # noqa: F401 - fixtures by name
    BROKEN,
    BUDGET,
    DECLARING,
    EARLY_MARKER,
    REPAIR,
    REPO_ROOT,
    STORY_ID,
    SUITE_ARTIFACT,
    THREE_ARTIFACTS,
    UNRUNNABLE_COMMAND,
    VERIFYING,
    WORKFLOW,
    Runner,
    all_three_run,
    drive,
    events_of,
    green_run,
    harness_root,
    make_target,
    never_repaired_run,
    read_json,
    record_of,
    red_then_green_run,
    rendered_prompt,
    run_dir_of,
    self_route_records,
    state_of,
    target_root,
    without_the_declaration,
)

DECLARING_STAGE = next(s for s in WORKFLOW["stages"] if s["name"] == DECLARING)

ORCHESTRATION_MODULES = sorted((REPO_ROOT / "orchestration").glob("*.py"))
COORDINATOR_SOURCE = (
    REPO_ROOT / "orchestration" / "story_coordinator.py").read_text(
        encoding="utf-8")


# --------------------------------------------------------------------------
# Reading a run directory's retained pairs
#
# Every name below comes through the coordinator's own two name-shaping
# functions, given the artifact the *fixture's* workflow declares. Nothing here
# spells a filename, so a change to either shape moves the assertions with it.
# --------------------------------------------------------------------------


def retained_result(try_number, stage=DECLARING, attempt=1,
                    artifact=SUITE_ARTIFACT) -> str:
    return story_coordinator.retained_suite_result_file(
        artifact, stage, attempt, try_number)


def retained_output(try_number, **kwargs) -> str:
    return story_coordinator.suite_output_file(retained_result(try_number, **kwargs))


def canonical_output(artifact: str = SUITE_ARTIFACT) -> str:
    return story_coordinator.suite_output_file(artifact)


def retained_globs(artifact: str = SUITE_ARTIFACT) -> tuple[str, str]:
    """The two patterns matching every retained name of one declared artifact.

    The wildcarded spelling the coordinator's own discovery uses, so what these
    find and what a resume finds cannot drift apart.
    """
    result = story_coordinator.retained_suite_result_file(
        artifact, "*", "*", "*")
    return result, story_coordinator.suite_output_file(result)


def retained_names(run_dir: Path, artifact: str = SUITE_ARTIFACT) -> list[str]:
    return sorted(path.name
                  for pattern in retained_globs(artifact)
                  for path in Path(run_dir).glob(pattern))


def tries_present(run_dir: Path, name_of, count: int) -> set[int]:
    """Which of the first `count` try numbers `name_of` finds a file for.

    A set rather than an assertion so the prompts' keys and the retained runs'
    keys can be compared to each other rather than each to a literal.
    """
    return {try_number for try_number in range(count)
            if (Path(run_dir) / name_of(try_number)).is_file()}


# --------------------------------------------------------------------------
# Both runs of a red-then-green story survive
# --------------------------------------------------------------------------


def test_the_fixture_really_makes_two_suite_runs(red_then_green_run):
    """The premise every case in this section rests on, stated so a change to
    the fixture reddens here rather than quietly halving the assertions."""
    code, runner, run_dir = red_then_green_run
    assert code == 0
    # Two invocations of the stage that declares the suite run, which is what
    # makes two coordinator suite runs of it, and the one self-route between
    # them is the red suite that caused the second.
    assert runner.calls.count(DECLARING) == 2
    assert [record["failure"] for _, record in self_route_records(run_dir)] == [
        story_coordinator.SUITE_FAILED]


def test_both_suite_runs_are_readable_when_the_run_ends(red_then_green_run):
    """The story's central guarantee: each run's result record and each run's
    whole combined output, still there after the run that followed."""
    _, _, run_dir = red_then_green_run

    for try_number in (0, 1):
        record = run_dir / retained_result(try_number)
        output = run_dir / retained_output(try_number)
        assert record.is_file(), record.name
        assert output.is_file(), output.name
        assert EARLY_MARKER in output.read_text(encoding="utf-8")


def test_each_retained_run_holds_the_verdict_of_the_turn_it_judged(
    red_then_green_run,
):
    """The two runs are distinguishable, which is what makes the survival above
    worth anything: the first turn left the suite red and the second repaired
    it, and the retained records say exactly that."""
    _, _, run_dir = red_then_green_run
    assert record_of(run_dir, retained_result(0))["exit_code"] == 1
    assert record_of(run_dir, retained_result(1))["exit_code"] == 0


def test_the_retained_runs_are_keyed_by_the_prompt_files_own_try_number(
    red_then_green_run,
):
    """The invocation's prompt and the suite run judging that turn share a key,
    so the two can be read together. Compared as sets of try numbers against
    each other rather than against literals: what has to hold is that they
    agree, and the first suite run of an attempt is the one whose prompt is
    try 0."""
    _, runner, run_dir = red_then_green_run
    invocations = len(runner.prompts[DECLARING])
    # One more than were taken, so a retained run keyed past the last prompt
    # would show up as a disagreement rather than going unlooked-for.
    horizon = invocations + 1

    prompts = tries_present(
        run_dir, lambda n: story_coordinator.prompt_file(DECLARING, 1, n),
        horizon)
    retained = tries_present(run_dir, retained_result, horizon)

    assert prompts == retained == set(range(invocations))
    assert 0 in retained, "the first suite run of an attempt is try 0"


def test_the_self_route_cites_the_run_it_was_caused_by(red_then_green_run):
    """The whole point of the keying, read the way a human reads it: follow the
    paths the self-route record names and arrive at the failing run — in a run
    whose later suite run passed."""
    _, _, run_dir = red_then_green_run
    _, record = self_route_records(run_dir)[0]
    cited_record, cited_output = record["artifacts"]

    assert read_json(run_dir / cited_record)["exit_code"] == 1
    assert Path(cited_output).is_file()
    assert EARLY_MARKER in Path(cited_output).read_text(encoding="utf-8")
    # And the run it sits in is the one that ended green, so the cited record
    # is not simply the only run there was.
    assert record_of(run_dir)["exit_code"] == 0
    assert state_of(run_dir)["status"] == "completed"


# --------------------------------------------------------------------------
# Each record points at the file beside it
# --------------------------------------------------------------------------


def test_every_record_of_a_two_run_story_names_the_output_beside_it(
    red_then_green_run,
):
    """Both pointers followed, in both directions: the canonical record names
    the canonical output and each retained record names its own, so neither
    points at the other's. The two are required to describe different runs,
    which is what a collapsed pair could not do."""
    _, _, run_dir = red_then_green_run
    canonical = record_of(run_dir)

    assert canonical["output_path"] == str(run_dir / canonical_output())
    for try_number in (0, 1):
        record = record_of(run_dir, retained_result(try_number))
        assert record["output_path"] == str(
            run_dir / retained_output(try_number))
        assert Path(record["output_path"]).is_file()

    failing = record_of(run_dir, retained_result(0))
    assert failing["output_path"] != canonical["output_path"]
    assert failing["exit_code"] != canonical["exit_code"]


def test_the_canonical_pair_still_holds_the_most_recent_run(red_then_green_run):
    """The compatibility guarantee: every existing reader of these two names
    sees what it saw before this story — the run that happened last."""
    _, _, run_dir = red_then_green_run
    canonical = record_of(run_dir)
    latest = record_of(run_dir, retained_result(1))

    assert (run_dir / canonical_output()).is_file()
    assert canonical["exit_code"] == latest["exit_code"] == 0
    assert canonical["output_tail"] == latest["output_tail"]


def test_the_verifier_is_still_given_the_canonical_record(red_then_green_run):
    """What the rendered context carries is unmoved: the canonical record, and
    the canonical output path in it. The retained failing run is *not* there —
    and the control beside that absence is the re-run's own prompt, which does
    carry it."""
    _, _, run_dir = red_then_green_run
    verifier_prompt = rendered_prompt(run_dir, VERIFYING)
    failing_output = str(run_dir / retained_output(0))

    assert str(run_dir / canonical_output()) in verifier_prompt
    assert '"exit_code": 0' in verifier_prompt
    assert failing_output not in verifier_prompt
    assert failing_output in rendered_prompt(run_dir, DECLARING, 1, 1)


# --------------------------------------------------------------------------
# One suite run is not a special case
# --------------------------------------------------------------------------


def test_a_story_with_a_single_suite_run_writes_the_retained_pair(green_run):
    """The common case has the same shape as the uncommon one: the retention is
    not conditional on a self-route having happened."""
    code, runner, run_dir = green_run
    assert code == 0
    assert runner.calls.count(DECLARING) == 1
    assert not self_route_records(run_dir)

    assert (run_dir / retained_result(0)).is_file()
    assert (run_dir / retained_output(0)).is_file()
    assert record_of(run_dir, retained_result(0))["exit_code"] == 0
    # Exactly the one pair: the run made one suite run and kept one.
    assert retained_names(run_dir) == sorted(
        [retained_result(0), retained_output(0)])


def test_a_run_that_could_not_start_the_command_keeps_a_pair_too(
    make_target, harness_root,
):
    """The path that records a run which did not happen. Both records are
    written and neither names an output, because there was none — the
    optional-by-absence convention the surrounding records already use."""
    target = make_target("unrunnable-retained", test_command=UNRUNNABLE_COMMAND)
    code, _, run_dir = drive(target, harness_root)

    assert code == 2
    for record in (record_of(run_dir), record_of(run_dir, retained_result(0))):
        assert record["ran"] is False
        assert "output_path" not in record
    assert not (run_dir / retained_output(0)).exists()


# --------------------------------------------------------------------------
# The reason a self-route carries is the reason it carried
# --------------------------------------------------------------------------


def test_the_self_route_reason_still_truncates_to_a_summary(red_then_green_run):
    """Only the path moved. The reason is still one line naming the exit status
    and the summary, and it is *not* the whole output — which the file it now
    points at is, so the absence is about the reason rather than about a line
    the suite never printed."""
    _, _, run_dir = red_then_green_run
    _, record = self_route_records(run_dir)[0]
    reason = record["reason"]

    assert f"exited {record_of(run_dir, retained_result(0))['exit_code']}" in reason
    assert "\n" not in reason
    assert EARLY_MARKER not in reason
    assert EARLY_MARKER in (run_dir / retained_output(0)).read_text(
        encoding="utf-8")


# --------------------------------------------------------------------------
# Nothing routes on a retained file
# --------------------------------------------------------------------------


class SweepingRunner(Runner):
    """The fixture's runner, with a sweep at the end of every turn.

    Every file in the run directory matching one of the patterns is removed
    once the stage's turn has ended and before the coordinator's suite run for
    that turn — so at the moment any decision could consult one, there is
    nothing to consult. Deleting rather than corrupting, because a decision
    that read a missing file would fail loudly rather than quietly agreeing.
    """

    def __init__(self, target_root, plan=None, verdicts=None, workflow=None,
                 *, sweep=()):
        super().__init__(target_root, plan, verdicts, workflow)
        self.sweep = tuple(sweep)
        self.swept: list[str] = []

    def __call__(self, prompt, **kwargs):
        result = super().__call__(prompt, **kwargs)
        for pattern in self.sweep:
            for path in sorted(self.run_dir.glob(pattern)):
                path.unlink()
                self.swept.append(path.name)
        return result


#: The declared required outputs of the stage that declares the suite run, read
#: off the workflow rather than written here. Sweeping these is the control:
#: they are files the coordinator demonstrably does decide on.
REQUIRED_GLOBS = tuple(story_coordinator.required_artifacts(DECLARING_STAGE))

RED_THEN_GREEN = {DECLARING: [BROKEN, REPAIR]}


def routing_of(code: int, runner: Runner, run_dir: Path) -> tuple:
    """Everything about a run that is a routing decision.

    Which stages were invoked in which order, every event the run recorded,
    the failure and try of every self-route, and the exit status. Content the
    run merely wrote is deliberately not in here: the question is whether the
    run went the same way.
    """
    return (
        code,
        tuple(runner.calls),
        tuple(events_of(run_dir)),
        tuple((record["failure"], record["try"])
              for _, record in self_route_records(run_dir)),
        state_of(run_dir)["status"],
    )


def sweep_run(target: Path, harness: Path, patterns) -> tuple:
    runner = SweepingRunner(target, RED_THEN_GREEN, sweep=patterns)
    code = story_coordinator.run_story(STORY_ID, harness, target, runner)
    return routing_of(code, runner, run_dir_of(target)), runner


def test_deleting_every_retained_file_changes_no_routing_decision(
    make_target, harness_root, red_then_green_run,
):
    """The absence, held as behaviour rather than argued from source: a run
    whose retained files are gone the moment they are written takes exactly the
    route the run that keeps them takes."""
    code, runner, run_dir = red_then_green_run
    kept = routing_of(code, runner, run_dir)

    target = make_target("swept-retained")
    swept, sweeper = sweep_run(target, harness_root, retained_globs())

    assert sweeper.swept, "the sweep found no retained file to remove"
    assert swept == kept


def test_the_same_deletion_of_a_routed_on_file_changes_the_run(
    make_target, harness_root, red_then_green_run,
):
    """The control beside it. The same runner, the same plan, the same moment
    in the turn — and applied to the stage's declared required outputs, which
    the coordinator does decide on, the run goes somewhere else entirely. So
    the identity above is a fact about retained files rather than about a sweep
    that could never have mattered."""
    code, runner, run_dir = red_then_green_run
    kept = routing_of(code, runner, run_dir)

    target = make_target("swept-required")
    swept, sweeper = sweep_run(target, harness_root, REQUIRED_GLOBS)

    assert sweeper.swept, "the sweep found no required output to remove"
    assert swept != kept
    assert swept[0] != kept[0]
    assert story_coordinator.MISSING_REQUIRED_ARTIFACTS in {
        failure for failure, _ in swept[3]}


# --------------------------------------------------------------------------
# A workflow declaring no suite run writes neither pair
# --------------------------------------------------------------------------


def test_a_workflow_declaring_no_suite_run_writes_neither_pair(
    make_target, tmp_path,
):
    """Exactly as it wrote neither file before: the declaration is the switch,
    and the retained pair is behind the same switch as the canonical one."""
    harness, workflow = without_the_declaration(tmp_path, "no-suite-run-retained")
    stage = workflow["stages"][0]["name"]
    target = make_target("undeclared-retained", workflow=workflow["name"])
    code, _, run_dir = drive(target, harness, {stage: [BROKEN]},
                             workflow=workflow)

    assert code == 0
    assert not (run_dir / SUITE_ARTIFACT).exists()
    assert not (run_dir / canonical_output()).exists()
    assert retained_names(run_dir) == []


def test_the_identical_run_under_the_declaring_workflow_writes_both(
    make_target, harness_root,
):
    """The control beside it: same runner, same plan, same target shape, and
    the declaration is the only difference."""
    target = make_target("declared-retained-control")
    _, _, run_dir = drive(target, harness_root, {DECLARING: [BROKEN]})

    assert (run_dir / SUITE_ARTIFACT).is_file()
    assert (run_dir / canonical_output()).is_file()
    assert retained_names(run_dir) != []


# --------------------------------------------------------------------------
# The other two coordinator suite runs are untouched
# --------------------------------------------------------------------------


#: The suite run's artifact, and the revert check's and the clean-clone check's,
#: off the fixture that declares all three.
SUITE_RUN_ARTIFACT, REVERT_ARTIFACT, CLEAN_CLONE_ARTIFACT = THREE_ARTIFACTS


@pytest.mark.parametrize("artifact", [REVERT_ARTIFACT, CLEAN_CLONE_ARTIFACT])
def test_the_other_two_checks_write_what_they_wrote_under_the_names_they_used(
    all_three_run, artifact,
):
    """Neither the revert check nor the clean-clone check is what this story is
    about. Each still writes one record and one output at the run root, the
    record still names the output beside it, and neither gained a retained
    pair — each runs once per run and overwrites nothing."""
    code, _, run_dir = all_three_run
    record = record_of(run_dir, artifact)

    assert code == 0
    assert record["output_path"] == str(run_dir / canonical_output(artifact))
    assert Path(record["output_path"]).is_file()
    assert retained_names(run_dir, artifact) == []


def test_the_suite_run_in_that_same_directory_does_have_one(all_three_run):
    """The control for the absence above: the same scan, over the same run
    directory, for the one check this story keyed — so an empty result there is
    a fact about those two checks rather than about where the scan looked."""
    _, _, run_dir = all_three_run
    assert retained_names(run_dir, SUITE_RUN_ARTIFACT) != []
    assert SUITE_RUN_ARTIFACT not in (REVERT_ARTIFACT, CLEAN_CLONE_ARTIFACT)


# --------------------------------------------------------------------------
# The names come off the declaration, not out of the harness
# --------------------------------------------------------------------------


#: The four names one suite run of the fixture's workflow writes. Every one of
#: them is built from the artifact the *workflow* declares, so finding any of
#: them written into the harness's own source would mean a name had been
#: spelled there instead of derived.
DERIVED_NAMES = (SUITE_ARTIFACT, canonical_output(),
                 retained_result(0), retained_output(0))


def names_spelled_in(source: str) -> list[str]:
    """Every derived filename a text writes out.

    A list rather than an assertion so the same statement can be made of a
    source that does spell one, which is the control.
    """
    return sorted(name for name in DERIVED_NAMES if name in source)


def test_the_scan_has_modules_to_scan():
    """Otherwise the parametrization below could quietly collect nothing."""
    assert len(ORCHESTRATION_MODULES) > 1
    assert any(path.name == "story_coordinator.py"
               for path in ORCHESTRATION_MODULES)


@pytest.mark.parametrize("module", ORCHESTRATION_MODULES,
                         ids=[p.name for p in ORCHESTRATION_MODULES])
def test_no_orchestration_module_spells_a_retained_or_canonical_name(module):
    assert names_spelled_in(module.read_text(encoding="utf-8")) == []


def test_a_run_nevertheless_writes_all_four_of_those_names(green_run):
    """The other half of the absence: the names are not missing from the source
    because nothing writes them. A run writes every one of them, from the
    declaration alone."""
    _, _, run_dir = green_run
    for name in DERIVED_NAMES:
        assert (run_dir / name).is_file(), name


def test_the_same_scan_reports_a_name_planted_in_that_source():
    """The control: the same scan over the same source with a retained filename
    written into it reports it."""
    planted = COORDINATOR_SOURCE.replace(
        "    argv = shlex.split(command)",
        f'    _planted = "{retained_result(0)}"\n    argv = shlex.split(command)',
        1)
    assert planted != COORDINATOR_SOURCE
    assert names_spelled_in(planted) == [retained_result(0)]


# --------------------------------------------------------------------------
# A resume does not write over the interrupted attempt's retained runs
# --------------------------------------------------------------------------


def ready_to_resume(target: Path, marker: str = "by hand") -> None:
    """The one thing a resume asks of a developer, as a named act: the tree is
    changed and then committed, so the dirty-tree pre-flight is satisfied."""
    write(target / "src" / "app.py", f"print('{marker}')\n")
    git(target, "add", "-A")
    git(target, "commit", "-q", "--allow-empty", "-m", f"decided: {marker}")


def contents_at_root(run_dir: Path, names: list[str]) -> dict[str, bytes]:
    return {name: (run_dir / name).read_bytes() for name in names}


def test_the_interrupted_attempt_wrote_a_retained_pair_per_suite_run(
    never_repaired_run,
):
    """The premise the resume case rests on: the run that escalated made a
    suite run per invocation and kept every one of them."""
    code, runner, run_dir = never_repaired_run
    assert code == 2
    assert state_of(run_dir)["status"] == "escalated"
    assert tries_present(run_dir, retained_result, BUDGET + 2) == set(
        range(runner.calls.count(DECLARING)))


def test_a_resume_does_not_write_over_the_interrupted_attempts_retained_runs(
    never_repaired_run, target_root, harness_root,
):
    """The archive happens before the resumed stage's first suite run lands on
    those names, so every byte of the interrupted attempt is still findable
    afterwards — while the root copies of those same names now hold the
    resumed run, which is what makes the survival a fact about the archive."""
    _, _, run_dir = never_repaired_run
    before = contents_at_root(run_dir, retained_names(run_dir))
    assert before, "the interrupted attempt retained nothing to write over"

    ready_to_resume(target_root)
    code, runner, _ = drive(target_root, harness_root, {DECLARING: [REPAIR]})
    assert code == 0

    for name, content in before.items():
        survivors = [path for path in run_dir.rglob(name)
                     if path.read_bytes() == content]
        # Somewhere other than the run root, because the root is exactly where
        # the resumed run's own first suite run lands.
        assert any(path.parent != run_dir for path in survivors), (
            f"{name} was not kept anywhere but the root the resume writes on")

    # The control: the root copies were written over, by the resumed run.
    assert (run_dir / retained_result(0)).read_bytes() != before[
        retained_result(0)]
    assert record_of(run_dir, retained_result(0))["exit_code"] == 0


def test_the_resume_discovery_functions_name_the_retained_pairs(
    never_repaired_run,
):
    """The same fact by search over the functions that decide what a resume
    takes with it, rather than by driving one: both the attempt archive's
    discovery and the entry move's report every retained name at the root."""
    _, _, run_dir = never_repaired_run
    stages = WORKFLOW["stages"]
    names = set(retained_names(run_dir))
    assert names

    assert names <= set(story_coordinator.interrupted_attempt_artifacts(
        stages, 1, run_dir=run_dir))
    assert names <= set(story_coordinator.entry_artifacts(run_dir, stages))


def test_those_functions_report_none_of_them_without_the_declaration(
    never_repaired_run,
):
    """The control: the same two functions, over the same directory holding the
    same files, with the suite-run declaration removed from the stage list —
    so what they report comes off the workflow rather than off a name shape
    written into them."""
    _, _, run_dir = never_repaired_run
    bare = json.loads(json.dumps(WORKFLOW["stages"]))
    for stage in bare:
        stage.pop("suite_run", None)
    names = set(retained_names(run_dir))
    assert names

    assert not names & set(story_coordinator.interrupted_attempt_artifacts(
        bare, 1, run_dir=run_dir))
    assert not names & set(story_coordinator.entry_artifacts(run_dir, bare))
