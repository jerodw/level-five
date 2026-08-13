"""Independent validation for story-032: a plan that assigns work to a stage
that cannot own it is refused at plan time, and a grant may name one path.

Written from the story's acceptance criteria. Three subjects, and each is
asserted at the altitude it actually lives at:

  * the new plan-time check is a pure function over a parsed story and the
    loaded workflow, so it is driven directly, and end-to-end through the
    real `scripts/l5-plan` against a throwaway repository with a stub
    `claude` on PATH — the same fixture story-023 and story-025 built, reused
    rather than copied, so a regression in the process model reddens all
    three;
  * the widened grant contract is driven at `stage_exception_problems`, whose
    two refusal messages must read exactly as they read today;
  * the exemption's granularity is not read off the coordinator's source at
    all. It is driven: a real target repository with a real pytest suite, a
    fake implementer that edits its working tree, and the coordinator run —
    the question "is this path exempt" answered by what the run does.

Every absence asserted here carries a demonstration that it can fail:

  * "the granted path is exempt from the revert check" sits beside the same
    run with the grant removed, which escalates on that very path;
  * "a second file beneath the same prefix is still governed" is the control
    for the grant not being a prefix match, and the escalation is required to
    name that second file — a prefix match would name nothing;
  * "the enforced list reaches both checks unshortened" is read off spies on
    the two checks in a run whose story grants the *whole* prefix, which is
    exactly the case the retired code shortened it in;
  * "grant_covers is the single decision all three readers make" is not
    argued from imports: the function is replaced at run time and each of the
    three readers is required to change its answer;
  * "assignment_problems names no stage and no prefix" sits beside the same
    scan over a copy of that source with each literal planted in it, and
    beside a run against a synthetic workflow, which reports the synthetic
    pair and stays silent about the real one;
  * "a conflicting artifact is not committed" is a HEAD comparison, a remote
    ref comparison and a byte comparison of the artifact left in the working
    tree, each beside the two clean resolutions, which are committed;
  * "prompts/planner.md names no stage and no restricted prefix" sits beside
    the same scan over that text with each literal planted.

No stage name and no path prefix is written as a literal anywhere the checks
below are about the *harness* writing none: both come off the loaded workflow
definition, the way the modules under test read them.

`.harness/docs/ARCHITECTURE.md` is not asserted on here. This story assigns it
to the documenter in `likely_file_changes`, which is the stage that runs after
this one — and that assignment being load-bearing is the whole subject of the
story.

Nothing here invokes a model: every coordinator run goes through a fake agent
runner, and every planning run goes through the stub session.
"""
import inspect
import json
import re
import sys
from pathlib import Path

import pytest

from conftest import load_mutant

from test_story_017_validation import (  # noqa: F401 - fixtures used by name
    APP_ADDITIVE,
    TEST_APP_AT_HEAD,
    ADDED_COVERAGE,
    TEST_EXTRA_PLUS_COVERAGE,
    append_to_story,
    clone_calls,
    executable_source,
    harness_root,
    run,
    run_dir_of,
    target,
    write,
)
from test_story_023_validation import (  # noqa: F401
    ARTIFACT,
    Planning,
    artifact,
    bare_remote,
    make_planning,
    remote_refs,
    run_plan,
    writes,
)
from test_story_025_validation import L5_RUN, install, run_script

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "orchestration"))

import harness_config  # noqa: E402
import plan_validation  # noqa: E402
import story_coordinator  # noqa: E402

#: The committed corpus. Written with `joinpath` rather than the `/` operator
#: because story-004 holds the suite to naming no path under the repository's
#: own run directory that way; story-005 and the parser corpus test reach the
#: stories the same way for the same reason.
STORIES_DIR = HARNESS_ROOT.joinpath(".harness", "stories")
COORDINATOR = HARNESS_ROOT / "orchestration" / "story_coordinator.py"
PLANNER_PROMPT = HARNESS_ROOT / "prompts" / "planner.md"

# --------------------------------------------------------------------------
# Everything about the workflow is read off the workflow.
# --------------------------------------------------------------------------

WORKFLOW = harness_config.load_workflow(HARNESS_ROOT, "story-workflow")
STAGES = WORKFLOW["stages"]
STAGE_NAMES = [stage["name"] for stage in STAGES]
RESTRICTIONS = story_coordinator.stage_restrictions(STAGES)
RESTRICTED_STAGE, RESTRICTED_PREFIX = RESTRICTIONS[0]
RESTRICTED_STAGES = {stage for stage, _ in RESTRICTIONS}
UNRESTRICTED_STAGE = next(n for n in STAGE_NAMES if n not in RESTRICTED_STAGES)
UNDEFINED_STAGE = "cartographer"
RESTRICTED_DECLARATION = next(
    stage for stage in STAGES if stage["name"] == RESTRICTED_STAGE
)

#: The entry story-031's artifact carried, reconstructed from the workflow
#: rather than recovered from history: the file it named sits beneath the
#: restricted prefix and it named the restricted stage, which is the whole of
#: what made it a conflict. Writing the pair as literals here would make this
#: file agree with a copy of the workflow rather than with the workflow.
STORY_031_FILE = f"{RESTRICTED_PREFIX}test_baseline_honesty.py"
STORY_031_STAGE = RESTRICTED_STAGE

#: A file beneath the restricted prefix that no story owns, used wherever a
#: second governed path is needed.
OUTSIDE_EVERY_PREFIX = "orchestration/story_coordinator.py"


def test_the_workflow_this_file_reads_still_has_something_to_say():
    """Every derivation above is load-bearing; an empty one would go green."""
    assert RESTRICTIONS, "the workflow declares no may_not_create restriction"
    assert UNRESTRICTED_STAGE in STAGE_NAMES
    assert UNDEFINED_STAGE not in STAGE_NAMES
    assert STORY_031_FILE.startswith(RESTRICTED_PREFIX)
    assert not OUTSIDE_EVERY_PREFIX.startswith(RESTRICTED_PREFIX)
    for _, prefix in RESTRICTIONS:
        assert not OUTSIDE_EVERY_PREFIX.startswith(prefix)


# --------------------------------------------------------------------------
# Story dictionaries, built here rather than parsed, so a check can be handed
# an entry the schema would reject — which is exactly the shape the story
# requires it not to raise on.
# --------------------------------------------------------------------------


def plan(*entries: dict) -> dict:
    return {"technical_plan": {"likely_file_changes": list(entries)}}


def entry(file: str | None, stage: str | None) -> dict:
    made = {"reason": "because the plan says so"}
    if file is not None:
        made["file"] = file
    if stage is not None:
        made["stage"] = stage
    return made


def with_grant(story: dict, create: str, stage: str = RESTRICTED_STAGE) -> dict:
    granted = dict(story)
    granted["stage_exceptions"] = [
        {"stage": stage, "create": create, "reason": "the deliverable needs it"}
    ]
    return granted


#: The conflict, as story-031's artifact carried it.
CONFLICT = plan(entry(STORY_031_FILE, STORY_031_STAGE))


# --------------------------------------------------------------------------
# assignment_problems: what it reports
# --------------------------------------------------------------------------


def test_the_conflict_story_031_carried_is_reported():
    problems = plan_validation.assignment_problems(CONFLICT, STAGES)
    assert len(problems) == 1, problems


def test_the_reported_problem_names_the_file_the_stage_the_prefix_and_both_ways_out():
    """Asserted on the text, not on a problem having been returned.

    A planner reading this message has to be able to see all four things
    without going to the workflow definition, which is the point of stating
    the restriction in the workflow's own words.
    """
    (problem,) = plan_validation.assignment_problems(CONFLICT, STAGES)

    assert STORY_031_FILE in problem
    assert STORY_031_STAGE in problem
    assert RESTRICTED_PREFIX in problem
    # Resolution one: give the file to a stage that may own it.
    assert re.search(r"(?i)assign .*to a stage that may own it", problem), problem
    # Resolution two: declare a grant naming it.
    assert re.search(r"(?i)stage_exceptions grant naming", problem), problem
    # The restriction in the workflow's own words, not a paraphrase.
    assert f"{RESTRICTED_STAGE} may not create files under {RESTRICTED_PREFIX}" \
        in problem


def test_the_same_plan_naming_a_stage_that_may_own_the_file_yields_nothing():
    """The first clean resolution."""
    resolved = plan(entry(STORY_031_FILE, UNRESTRICTED_STAGE))
    assert plan_validation.assignment_problems(resolved, STAGES) == []


def test_the_same_plan_with_a_grant_naming_that_exact_file_yields_nothing():
    """The second clean resolution, at the granularity of one file."""
    resolved = with_grant(CONFLICT, STORY_031_FILE)
    assert plan_validation.assignment_problems(resolved, STAGES) == []


def test_a_grant_naming_the_whole_prefix_also_yields_nothing():
    resolved = with_grant(CONFLICT, RESTRICTED_PREFIX)
    assert plan_validation.assignment_problems(resolved, STAGES) == []


def test_a_grant_naming_a_different_file_beneath_the_prefix_does_not_suppress_it():
    """The grant is not a prefix match here either."""
    other = with_grant(CONFLICT, f"{RESTRICTED_PREFIX}test_something_else.py")
    assert len(plan_validation.assignment_problems(other, STAGES)) == 1


def test_a_grant_on_another_stage_does_not_suppress_it():
    other = with_grant(CONFLICT, STORY_031_FILE, stage=UNRESTRICTED_STAGE)
    assert len(plan_validation.assignment_problems(other, STAGES)) == 1


@pytest.mark.parametrize("stage", STAGE_NAMES)
def test_a_file_outside_every_declared_prefix_yields_nothing_whatever_the_stage(stage):
    outside = plan(entry(OUTSIDE_EVERY_PREFIX, stage))
    assert plan_validation.assignment_problems(outside, STAGES) == []


@pytest.mark.parametrize(
    "stage", [n for n in STAGE_NAMES if n not in RESTRICTED_STAGES]
)
def test_a_file_beneath_a_prefix_assigned_to_an_unrestricted_stage_yields_nothing(
        stage):
    allowed = plan(entry(STORY_031_FILE, stage))
    assert plan_validation.assignment_problems(allowed, STAGES) == []


# --------------------------------------------------------------------------
# assignment_problems: what it declines to decide, and never raises on
# --------------------------------------------------------------------------


@pytest.mark.parametrize("story", [
    pytest.param({}, id="no-technical-plan"),
    pytest.param({"technical_plan": {}}, id="no-likely-file-changes"),
    pytest.param({"technical_plan": "a free-form block scalar"}, id="plan-not-a-map"),
    pytest.param(plan(entry(STORY_031_FILE, None)), id="entry-with-no-stage"),
    pytest.param(plan(entry(None, STORY_031_STAGE)), id="entry-with-no-file"),
    pytest.param(plan(entry(None, None)), id="entry-with-neither"),
    pytest.param({"technical_plan": {"likely_file_changes": ["a string"]}},
                 id="entry-not-a-map"),
])
def test_a_half_it_cannot_see_yields_no_problem_and_raises_nothing(story):
    """An absent half is nothing to report, and nothing to fail on either.

    Non-vacuous because the same call over the complete entry — the control
    directly below — does report it.
    """
    assert plan_validation.assignment_problems(story, STAGES) == []


def test_the_control_for_every_incomplete_entry_above_is_the_complete_one():
    assert plan_validation.assignment_problems(CONFLICT, STAGES) != []


# --------------------------------------------------------------------------
# Both halves come off the workflow, and the module writes neither
# --------------------------------------------------------------------------


def synthetic_stages() -> list[dict]:
    """A workflow whose restriction is one the real workflow does not declare."""
    return [
        {"name": UNDEFINED_STAGE, "may_not_create": ["cartography/"]},
        {"name": RESTRICTED_STAGE},
    ]


def test_both_halves_of_the_match_come_off_the_loaded_workflow():
    """Handed another workflow, the check reports that one's pairs and no other."""
    story = plan(
        entry("cartography/atlas.py", UNDEFINED_STAGE),
        entry(STORY_031_FILE, STORY_031_STAGE),
    )

    problems = plan_validation.assignment_problems(story, synthetic_stages())

    assert len(problems) == 1, problems
    assert "cartography/atlas.py" in problems[0]
    assert UNDEFINED_STAGE in problems[0]
    # The real pair is silent here, and the synthetic pair is silent against
    # the real workflow: neither is written into the module.
    assert STORY_031_FILE not in problems[0]
    assert plan_validation.assignment_problems(story, STAGES) != []
    assert "cartography" not in " ".join(
        plan_validation.assignment_problems(story, STAGES))


def test_a_workflow_that_restricts_nothing_reports_nothing():
    unrestricted = [{"name": name} for name in STAGE_NAMES]
    assert story_coordinator.stage_restrictions(unrestricted) == []
    assert plan_validation.assignment_problems(CONFLICT, unrestricted) == []


def literals_named(text: str) -> list[str]:
    """Every stage name and restricted prefix appearing in executable source."""
    body = executable_source(text)
    found = [name for name in STAGE_NAMES if name in body]
    found += [prefix for _, prefix in RESTRICTIONS if prefix in body]
    return found


def test_the_new_check_names_no_stage_and_no_restricted_prefix():
    """Beside the same scan over a copy with each literal planted in it."""
    source = inspect.getsource(plan_validation.assignment_problems)

    assert literals_named(source) == []
    # Stripping kept the code: a scanner handed an empty string reports
    # nothing either.
    assert "likely_file_changes" in executable_source(source)
    for planted in [RESTRICTED_STAGE, RESTRICTED_PREFIX, UNRESTRICTED_STAGE]:
        mutant = source + f'\n    unreachable = "{planted}"\n'
        assert planted in literals_named(mutant), planted


def test_the_module_as_a_whole_names_no_stage_and_no_restricted_prefix():
    module = (HARNESS_ROOT / "orchestration" / "plan_validation.py").read_text(
        encoding="utf-8")
    assert literals_named(module) == []


# --------------------------------------------------------------------------
# Composition: the check runs beside the strictness check, below the gate
# --------------------------------------------------------------------------


def write_artifact(tmp_path: Path, text: str, name: str = "story-900.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def plan_block(*entries: tuple[str, str]) -> str:
    lines = ["\ntechnical_plan:", "  likely_file_changes:"]
    for file, stage in entries:
        lines += [f"    - file: {file}", f"      stage: {stage}",
                  "      reason: the plan expects this"]
    return "\n".join(lines) + "\n"


def exceptions_block(stage: str, create: str) -> str:
    return (
        "\nstage_exceptions:\n"
        f"  - stage: {stage}\n"
        f"    create: {create}\n"
        "    reason: the story's own deliverable needs it\n"
    )


CONFLICTING_ARTIFACT = artifact("story-900") + plan_block(
    (STORY_031_FILE, STORY_031_STAGE))
REASSIGNED_ARTIFACT = artifact("story-900") + plan_block(
    (STORY_031_FILE, UNRESTRICTED_STAGE))
GRANTED_ARTIFACT = (artifact("story-900")
                    + plan_block((STORY_031_FILE, STORY_031_STAGE))
                    + exceptions_block(RESTRICTED_STAGE, STORY_031_FILE))


def test_every_artifact_this_file_uses_is_what_it_claims_to_be():
    """The refusal tests below would pass on an artifact nothing objects to."""
    for name, text in (("conflicting", CONFLICTING_ARTIFACT),
                       ("reassigned", REASSIGNED_ARTIFACT),
                       ("granted", GRANTED_ARTIFACT)):
        reading = story_coordinator.read_story(text)
        assert reading.problems == [], (name, reading.problems)
        assert story_coordinator.stage_exception_problems(
            reading.parsed, STAGES) == [], name
        assert plan_validation.strictness_problems(reading.parsed, STAGES) == [], name
    assert plan_validation.assignment_problems(
        story_coordinator.read_story(CONFLICTING_ARTIFACT).parsed, STAGES) != []
    for clean in (REASSIGNED_ARTIFACT, GRANTED_ARTIFACT):
        assert plan_validation.assignment_problems(
            story_coordinator.read_story(clean).parsed, STAGES) == []


def test_artifact_problems_reports_the_new_class(tmp_path: Path):
    path = write_artifact(tmp_path, CONFLICTING_ARTIFACT)
    found = plan_validation.artifact_problems([path], STAGES)
    assert list(found) == [path]
    assert any(STORY_031_FILE in problem for problem in found[path])


def test_artifact_problems_holds_the_clean_resolutions_back(tmp_path: Path):
    for index, text in enumerate((REASSIGNED_ARTIFACT, GRANTED_ARTIFACT)):
        path = write_artifact(tmp_path, text, f"story-90{index}.yaml")
        assert plan_validation.artifact_problems([path], STAGES) == {}


def test_a_story_that_fails_the_gate_yields_that_and_nothing_further(tmp_path: Path):
    """The existing order is kept: a story read_story objects to stops there.

    Non-vacuous because the same artifact, made parseable, does reach the new
    check — the second half below.
    """
    unparseable = write_artifact(tmp_path, "this: is: not: a story\n\t- ?\n")
    found = plan_validation.artifact_problems([unparseable], STAGES)
    assert found[unparseable]
    assert not any(STORY_031_FILE in problem for problem in found[unparseable])

    # Schema-invalid, and carrying the conflict: still only the schema problem.
    invalid = write_artifact(
        tmp_path,
        CONFLICTING_ARTIFACT.replace("tasks:\n  - do the sample work\n", ""),
        "story-901.yaml")
    found = plan_validation.artifact_problems([invalid], STAGES)
    assert found[invalid]
    assert not any(STORY_031_FILE in problem for problem in found[invalid])

    reached = write_artifact(tmp_path, CONFLICTING_ARTIFACT, "story-902.yaml")
    assert any(STORY_031_FILE in problem
               for problem in plan_validation.artifact_problems(
                   [reached], STAGES)[reached])


def test_the_strictness_check_still_reports_beside_the_new_one(tmp_path: Path):
    """Both classes on one artifact, so neither displaced the other."""
    # The over-strict sentence lands in `constraints`, the last array the
    # template writes, so the plan block goes on after it.
    both = (artifact("story-900")
            + f"  - the {RESTRICTED_STAGE} leaves {RESTRICTED_PREFIX} alone "
              f"entirely\n"
            + plan_block((STORY_031_FILE, STORY_031_STAGE)))
    path = write_artifact(tmp_path, both)
    problems = plan_validation.artifact_problems([path], STAGES)[path]
    assert any("states a restriction the workflow does not" in p for p in problems)
    assert any("a stage that may own it" in p for p in problems)


# --------------------------------------------------------------------------
# The committed corpus, read from disk
# --------------------------------------------------------------------------


def corpus() -> dict[str, dict]:
    parsed = {}
    for path in sorted(STORIES_DIR.glob("story-*.yaml")):
        reading = story_coordinator.read_story(path.read_text(encoding="utf-8"))
        if reading.parsed is not None:
            parsed[path.stem] = reading.parsed
    return parsed


def test_the_committed_artifact_story_029_shipped_is_reported_by_the_new_check():
    """Intended, and stated as intended: it is the artifact story-031 inherited.

    Read from disk, through the same reader a run uses, so this is the
    committed corpus rather than a fixture resembling it.
    """
    stories = corpus()
    assert stories, "no committed story artifact parsed"
    reported = {name for name, story in stories.items()
                if plan_validation.assignment_problems(story, STAGES)}

    assert "story-029" in reported
    # Not everything is reported, so "reported" is a property of the artifact
    # rather than of the check saying yes to whatever it is handed.
    assert reported != set(stories)


def test_no_committed_artifact_becomes_unrunnable(tmp_path: Path):
    """The new check is plan-time only: pre-flight's answer is unchanged.

    Pre-flight's story checks are read_story and stage_exception_problems.
    Every committed artifact the new check reports still passes both, so
    nothing that has already run stops being runnable.
    """
    stories = corpus()
    reported = [story for name, story in stories.items()
                if plan_validation.assignment_problems(story, STAGES)]
    assert reported
    for story in reported:
        assert story_coordinator.stage_exception_problems(story, STAGES) == []


def test_pre_flight_does_not_start_refusing_the_assignment_class(planning: Planning):
    """Driven through the real l5-run, as story-025 drove the strictness class.

    Asserted by pre-flight getting past the story checks — it reaches the
    clean-tree refusal, which names the dirty path rather than the artifact.
    """
    install(planning, "story-900", CONFLICTING_ARTIFACT)
    (planning.root / "dirty.txt").write_text("developer's own\n", encoding="utf-8")

    result = run_script(L5_RUN, planning, "story-900")

    assert result.returncode == 1
    assert "dirty.txt" in result.stderr
    assert "story-900.yaml" not in result.stderr
    # Control: an artifact pre-flight *does* refuse is refused for the
    # artifact, so getting past it above means something.
    install(planning, "story-900",
            CONFLICTING_ARTIFACT + exceptions_block(UNDEFINED_STAGE,
                                                    RESTRICTED_PREFIX))
    refused = run_script(L5_RUN, planning, "story-900")
    assert "story-900.yaml" in refused.stderr


# --------------------------------------------------------------------------
# End to end, through the real scripts/l5-plan
# --------------------------------------------------------------------------


@pytest.fixture
def planning(tmp_path: Path) -> Planning:
    """A target repository with a stub `claude` on PATH and a bare origin."""
    made = make_planning(tmp_path)
    made.remote = bare_remote(tmp_path, made, upstream=True)
    return made


ARTIFACT_PATH = ".harness/stories/story-900.yaml"


def test_l5_plan_leaves_a_conflicting_artifact_uncommitted_and_prints_the_problem(
        planning: Planning):
    """HEAD unmoved, nothing pushed, the artifact still exactly as written."""
    before, refs_before = planning.head(), remote_refs(planning.remote)

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (ARTIFACT_PATH, CONFLICTING_ARTIFACT)))

    assert result.returncode != 0
    assert planning.head() == before
    assert remote_refs(planning.remote) == refs_before

    written = (planning.root / ARTIFACT_PATH)
    assert written.read_text(encoding="utf-8") == CONFLICTING_ARTIFACT
    assert ARTIFACT_PATH in planning.status()

    printed = result.stdout + result.stderr
    assert STORY_031_FILE in printed
    assert STORY_031_STAGE in printed
    assert RESTRICTED_PREFIX in printed
    assert re.search(r"(?i)a stage that may own it", printed), printed
    assert re.search(r"(?i)stage_exceptions grant naming", printed), printed


@pytest.mark.parametrize("resolution,text", [
    ("reassigned", REASSIGNED_ARTIFACT),
    ("granted", GRANTED_ARTIFACT),
])
def test_each_clean_resolution_produces_an_artifact_l5_plan_commits(
        resolution: str, text: str, planning: Planning):
    """The control for the refusal above: same fixture, same stub, committed."""
    before, refs_before = planning.head(), remote_refs(planning.remote)

    result = run_plan(planning, L5_STUB_WRITE=writes((ARTIFACT_PATH, text)))

    assert result.returncode == 0, result.stderr
    assert planning.head() != before, resolution
    assert remote_refs(planning.remote) != refs_before
    assert planning.status() == ""


# --------------------------------------------------------------------------
# stage_exception_problems: what it now accepts, and what it still refuses
# --------------------------------------------------------------------------


def grant_story(create: str, stage: str = RESTRICTED_STAGE) -> dict:
    return {"stage_exceptions": [
        {"stage": stage, "create": create, "reason": "the deliverable needs it"}
    ]}


@pytest.mark.parametrize("create,description", [
    (RESTRICTED_PREFIX, "the declared prefix itself, unchanged"),
    (f"{RESTRICTED_PREFIX}test_one_file.py", "a single file beneath it"),
    (f"{RESTRICTED_PREFIX}subdirectory/", "a directory beneath it"),
    (f"{RESTRICTED_PREFIX}subdirectory/test_deeper.py", "a file further down"),
])
def test_a_value_at_or_beneath_a_declared_prefix_is_accepted(create, description):
    assert story_coordinator.stage_exception_problems(
        grant_story(create), STAGES) == [], description


def test_a_value_beneath_no_declared_prefix_is_still_refused_with_todays_message():
    outside = "orchestration/"
    (problem,) = story_coordinator.stage_exception_problems(
        grant_story(outside), STAGES)
    assert problem == (
        f"$.stage_exceptions[0]: grants '{outside}' to stage "
        f"'{RESTRICTED_STAGE}', which was never restricted from creating it"
    )


def test_a_grant_to_a_stage_restricted_on_nothing_is_still_refused():
    (problem,) = story_coordinator.stage_exception_problems(
        grant_story(RESTRICTED_PREFIX, stage=UNRESTRICTED_STAGE), STAGES)
    assert problem == (
        f"$.stage_exceptions[0]: grants '{RESTRICTED_PREFIX}' to stage "
        f"'{UNRESTRICTED_STAGE}', which was never restricted from creating it"
    )


def test_a_stage_the_workflow_does_not_define_is_still_refused_with_todays_message():
    (problem,) = story_coordinator.stage_exception_problems(
        grant_story(RESTRICTED_PREFIX, stage=UNDEFINED_STAGE), STAGES)
    assert problem == (
        f"$.stage_exceptions[0]: names stage '{UNDEFINED_STAGE}', which the "
        f"loaded workflow does not define"
    )


def test_a_value_that_only_shares_a_textual_prefix_is_not_beneath_it():
    """`tests-archive/` is not beneath `tests/`; the slash is part of the match."""
    sibling = RESTRICTED_PREFIX.rstrip("/") + "-archive/"
    assert story_coordinator.stage_exception_problems(
        grant_story(sibling), STAGES) != []


# --------------------------------------------------------------------------
# grant_covers: one function, three readers
# --------------------------------------------------------------------------


DIRECTORY_GRANT = RESTRICTED_PREFIX
FILE_GRANT = f"{RESTRICTED_PREFIX}test_one_file.py"


@pytest.mark.parametrize("granted,path,covered", [
    ([DIRECTORY_GRANT], f"{DIRECTORY_GRANT}anything.py", True),
    ([DIRECTORY_GRANT], f"{DIRECTORY_GRANT}deep/down/anything.py", True),
    ([DIRECTORY_GRANT], DIRECTORY_GRANT, True),
    ([DIRECTORY_GRANT], OUTSIDE_EVERY_PREFIX, False),
    ([FILE_GRANT], FILE_GRANT, True),
    ([FILE_GRANT], f"{RESTRICTED_PREFIX}test_another_file.py", False),
    ([FILE_GRANT], FILE_GRANT + ".bak", False),
    ([FILE_GRANT], f"{FILE_GRANT}/deeper.py", False),
    ([], FILE_GRANT, False),
    ([FILE_GRANT, DIRECTORY_GRANT], f"{DIRECTORY_GRANT}other.py", True),
])
def test_a_granted_value_covers_beneath_it_only_when_it_ends_in_a_slash(
        granted, path, covered):
    assert story_coordinator.grant_covers(granted, path) is covered


def test_granted_paths_reads_one_stages_grants_in_declared_order():
    story = {"stage_exceptions": [
        {"stage": RESTRICTED_STAGE, "create": FILE_GRANT, "reason": "one"},
        {"stage": UNRESTRICTED_STAGE, "create": DIRECTORY_GRANT, "reason": "two"},
        {"stage": RESTRICTED_STAGE, "create": DIRECTORY_GRANT, "reason": "three"},
    ]}
    assert story_coordinator.granted_paths(story, RESTRICTED_STAGE) == [
        FILE_GRANT, DIRECTORY_GRANT]
    assert story_coordinator.granted_paths(story, UNRESTRICTED_STAGE) == [
        DIRECTORY_GRANT]
    assert story_coordinator.granted_paths({}, RESTRICTED_STAGE) == []


def changed_record(tmp_path: Path, **groups) -> Path:
    """A stage record on disk, which is what both run-time checks read."""
    record = {"modified": [], "created": [], "deleted": []}
    record.update(groups)
    (tmp_path / "changed-files.json").write_text(
        json.dumps(record), encoding="utf-8")
    return tmp_path


def test_the_three_readers_all_go_through_the_one_matcher(monkeypatch,
                                                          tmp_path: Path):
    """Not argued from imports: the function is replaced and each must change.

    A reader that had re-derived "does this grant cover this path" for itself
    would answer the same either way, which is exactly the disagreement the
    story exists to make impossible.
    """
    granted = [FILE_GRANT]
    run_dir = changed_record(tmp_path, created=[FILE_GRANT], modified=[FILE_GRANT])
    prefixes = [RESTRICTED_PREFIX]
    granted_story = with_grant(plan(entry(FILE_GRANT, RESTRICTED_STAGE)), FILE_GRANT)

    # With the real matcher, all three say "covered".
    assert plan_validation.assignment_problems(granted_story, STAGES) == []
    assert story_coordinator._ownership_violation(
        run_dir, "changed-files.json", prefixes, granted) is None
    assert story_coordinator.governed_edits(
        run_dir, "changed-files.json", prefixes, granted).paths == ()

    monkeypatch.setattr(story_coordinator, "grant_covers",
                        lambda granted, path: False)

    assert plan_validation.assignment_problems(granted_story, STAGES) != []
    assert story_coordinator._ownership_violation(
        run_dir, "changed-files.json", prefixes, granted) is not None
    assert story_coordinator.governed_edits(
        run_dir, "changed-files.json", prefixes, granted).paths == (FILE_GRANT,)


def test_the_retired_prefix_reader_is_gone_and_nothing_calls_it():
    source = COORDINATOR.read_text(encoding="utf-8")
    assert "_granted_prefixes" not in source
    # Control: the name that replaced it is there, and this reader can see it.
    assert "granted_paths" in source


def test_the_matcher_names_no_stage():
    """It takes granted values and a path; a stage cannot reach it."""
    body = executable_source(inspect.getsource(story_coordinator.grant_covers))
    assert "endswith" in body                       # stripping kept the code
    for name in STAGE_NAMES:
        assert name not in body, name


# --------------------------------------------------------------------------
# The exemption, driven at the coordinator
#
# Every assertion below is about what a run *did*, not about what the source
# says. The target repository carries a real pytest suite, so "is this edit
# permitted" is answered by the revert check actually running.
# --------------------------------------------------------------------------


GRANTED_FILE = f"{RESTRICTED_PREFIX}test_extra.py"
SECOND_FILE = f"{RESTRICTED_PREFIX}test_app.py"
NEW_FILE = f"{RESTRICTED_PREFIX}test_a_second_module.py"

NEW_MODULE = '''\
def test_a_second_module_is_still_arithmetic():
    assert 3 + 4 == 7
'''


def test_the_three_paths_this_section_uses_are_what_it_assumes():
    for path in (GRANTED_FILE, SECOND_FILE, NEW_FILE):
        assert path.startswith(RESTRICTED_PREFIX)
    assert len({GRANTED_FILE, SECOND_FILE, NEW_FILE}) == 3
    assert not story_coordinator.grant_covers([GRANTED_FILE], SECOND_FILE)
    assert not story_coordinator.grant_covers([GRANTED_FILE], NEW_FILE)


def grant(target_root: Path, *creates: str) -> None:
    """Append one stage_exceptions entry per granted value to the story."""
    block = "\nstage_exceptions:\n"
    for create in creates:
        block += (f"  - stage: {RESTRICTED_STAGE}\n"
                  f"    create: {create}\n"
                  "    reason: this story's own deliverable needs it\n")
    append_to_story(target_root, block)


def unforced_edit_to_the_granted_file(root: Path) -> dict:
    """An addition to the module, and unforced coverage in the granted file."""
    write(root / "src" / "app.py", APP_ADDITIVE)
    write(root / RESTRICTED_PREFIX / "test_extra.py", TEST_EXTRA_PLUS_COVERAGE)
    return {"modified": ["src/app.py", GRANTED_FILE], "created": [], "deleted": []}


def unforced_edits_to_both_files(root: Path) -> dict:
    """Unforced coverage in the granted file and in a second governed file."""
    write(root / "src" / "app.py", APP_ADDITIVE)
    write(root / RESTRICTED_PREFIX / "test_extra.py", TEST_EXTRA_PLUS_COVERAGE)
    write(root / RESTRICTED_PREFIX / "test_app.py",
          TEST_APP_AT_HEAD + ADDED_COVERAGE)
    return {"modified": ["src/app.py", GRANTED_FILE, SECOND_FILE],
            "created": [], "deleted": []}


def creates_a_second_file(root: Path) -> dict:
    """The granted file edited, and a second file beneath the prefix created."""
    write(root / "src" / "app.py", APP_ADDITIVE)
    write(root / RESTRICTED_PREFIX / "test_extra.py", TEST_EXTRA_PLUS_COVERAGE)
    write(root / NEW_FILE, NEW_MODULE)
    return {"modified": ["src/app.py", GRANTED_FILE], "created": [NEW_FILE],
            "deleted": []}


def reverted_paths(clone_calls: list) -> set[str]:
    return {path for call in clone_calls for path in call}


def escalation_reason(target_root: Path) -> str:
    """The escalation's own sentence, read through the coordinator's reader.

    The reason rather than the whole summary: the summary also lists the run
    directory's file names, and an assertion that a path is *absent* must not
    be able to trip over an unrelated section.
    """
    reason = story_coordinator.escalation_reason(run_dir_of(target_root))
    assert reason, "the run did not escalate, so there is no reason to read"
    return reason


def test_a_path_level_grant_exempts_that_path_from_the_revert_check(
        target: Path, harness_root: Path, clone_calls):
    """The run completes, and the granted path is never put to the check.

    The control is the next test: the very same edit, in the very same
    fixture, with the grant removed — which escalates on this path.
    """
    grant(target, GRANTED_FILE)

    code, _ = run(target, harness_root, {"implementer":
                                         unforced_edit_to_the_granted_file})

    assert code == 0
    assert GRANTED_FILE not in reverted_paths(clone_calls)


def test_the_same_edit_without_the_grant_escalates_on_the_revert_check(
        target: Path, harness_root: Path, clone_calls):
    """The control for the exemption above."""
    code, _ = run(target, harness_root, {"implementer":
                                         unforced_edit_to_the_granted_file})

    assert code != 0
    reason = escalation_reason(target)
    assert GRANTED_FILE in reason
    assert "reverted" in reason
    assert GRANTED_FILE in reverted_paths(clone_calls)


def test_a_second_file_created_beneath_the_same_prefix_still_escalates(
        target: Path, harness_root: Path):
    """The grant is not a prefix match, and the escalation names the second file.

    A prefix match would have exempted this path too, and the run would have
    completed; naming it is what distinguishes the two implementations.
    """
    grant(target, GRANTED_FILE)

    code, _ = run(target, harness_root, {"implementer": creates_a_second_file})

    assert code != 0
    reason = escalation_reason(target)
    assert NEW_FILE in reason
    assert RESTRICTED_PREFIX in reason
    assert "created" in reason
    # The granted path is not what escalated.
    assert GRANTED_FILE not in reason


def test_a_second_file_edited_beneath_the_same_prefix_is_still_reverted(
        target: Path, harness_root: Path, clone_calls):
    """The revert check still governs everything the grant does not name."""
    grant(target, GRANTED_FILE)

    code, _ = run(target, harness_root, {"implementer":
                                         unforced_edits_to_both_files})

    assert code != 0
    reason = escalation_reason(target)
    assert SECOND_FILE in reason
    assert GRANTED_FILE not in reason
    assert reverted_paths(clone_calls) == {SECOND_FILE}


def test_a_whole_prefix_grant_behaves_exactly_as_it_does_today(
        target: Path, harness_root: Path, clone_calls):
    """Every path beneath it is exempt from both checks, as it always was.

    Non-vacuous because the same three edits without the grant escalate: the
    creation is the ownership check's, the two unforced edits the revert
    check's, and both controls are the two tests above.
    """
    grant(target, RESTRICTED_PREFIX)

    def everything(root: Path) -> dict:
        record = unforced_edits_to_both_files(root)
        write(root / NEW_FILE, NEW_MODULE)
        record["created"] = [NEW_FILE]
        return record

    code, _ = run(target, harness_root, {"implementer": everything})

    assert code == 0
    assert reverted_paths(clone_calls) & {GRANTED_FILE, SECOND_FILE, NEW_FILE} \
        == set()


def stage_exception_events(target_root: Path) -> list[str]:
    log = (run_dir_of(target_root) / "events.log").read_text(encoding="utf-8")
    return [line for line in log.splitlines() if "stage exception applied" in line]


def test_one_stage_exception_applied_event_per_grant_naming_the_granted_value(
        target: Path, harness_root: Path):
    """Two grants of different granularity, two lines, each naming its value."""
    grant(target, GRANTED_FILE, RESTRICTED_PREFIX)

    run(target, harness_root, {"implementer": unforced_edit_to_the_granted_file})

    lines = stage_exception_events(target)
    assert len(lines) == 2, lines
    assert any(line.endswith(f"{RESTRICTED_STAGE} may create {GRANTED_FILE}")
               for line in lines), lines
    assert any(line.endswith(f"{RESTRICTED_STAGE} may create {RESTRICTED_PREFIX}")
               for line in lines), lines


def test_a_run_with_no_grant_writes_no_such_event(target: Path,
                                                  harness_root: Path):
    """The control for the count above."""
    run(target, harness_root, {"implementer": unforced_edit_to_the_granted_file})
    assert stage_exception_events(target) == []


def test_the_enforced_list_reaches_both_checks_unshortened(
        target: Path, harness_root: Path, monkeypatch):
    """Read off the two checks in a run granting the *whole* prefix.

    That is the case the retired code shortened the list in — it removed the
    granted prefix outright — so a list that still holds it is this story's
    change and not an accident of the fixture.
    """
    declared = list(RESTRICTED_DECLARATION["may_not_create"])
    assert RESTRICTED_PREFIX in declared
    seen: list[tuple[str, list[str], list[str]]] = []

    for name in ("_ownership_violation", "governed_edits"):
        original = getattr(story_coordinator, name)

        def spy(run_dir, record_name, prefixes, granted, _o=original, _n=name):
            seen.append((_n, list(prefixes), list(granted)))
            return _o(run_dir, record_name, prefixes, granted)

        monkeypatch.setattr(story_coordinator, name, spy)

    grant(target, RESTRICTED_PREFIX)
    code, _ = run(target, harness_root, {"implementer":
                                         unforced_edit_to_the_granted_file})

    assert code == 0
    # Every stage carrying a changed-files record goes through both checks,
    # and only the restricted stage has a grant; the calls that carry one are
    # the restricted stage's.
    granted_calls = [(name, prefixes) for name, prefixes, granted in seen
                     if granted == [RESTRICTED_PREFIX]]
    assert {name for name, _ in granted_calls} == {"_ownership_violation",
                                                   "governed_edits"}
    for name, prefixes in granted_calls:
        assert prefixes == declared, name
    # Control: the reader can see a shortened list. The retired model removed
    # the granted prefix outright, which for this stage empties it — and an
    # empty list is what the *unrestricted* stages' calls carry, so the
    # comparison above is not one an empty list would have satisfied.
    assert [p for _, p, granted in seen if not granted] != [declared]
    assert declared != [p for p in declared if p != RESTRICTED_PREFIX]


# --------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------


def planner_prompt() -> str:
    return PLANNER_PROMPT.read_text(encoding="utf-8")


def flowed(text: str) -> str:
    """Prose with its line wrapping removed.

    A sentence in a hand-wrapped document is a sentence wherever the line
    breaks fall, so searching for one must not depend on the wrapping. Used
    only for the assertions that read prose; the ones that read for a literal
    name go to the raw text.
    """
    return " ".join(text.split())


def test_the_planner_prompt_states_the_standing_test_module_convention():
    prompt = flowed(planner_prompt())
    assert re.search(r"(?i)standing test module", prompt)
    assert re.search(r"(?i)outlives", prompt)
    assert re.search(r"(?i)needs no grant", prompt)
    # Its cost, which the story requires stated rather than implied.
    assert re.search(r"(?i)cost of the convention", prompt)


def test_the_planner_prompt_says_a_grant_may_name_a_single_path():
    prompt = flowed(planner_prompt())
    assert re.search(r"(?i)a single file or directory beneath it", prompt)
    assert re.search(r"(?i)narrowest grant", prompt)


def test_the_planner_prompt_states_the_refusal_and_both_resolutions():
    prompt = flowed(planner_prompt())
    assert re.search(r"(?i)reassign the file to a stage that may own it", prompt)
    assert re.search(r"(?i)stage_exceptions grant naming it", prompt)


def test_the_planner_prompt_still_names_no_stage_and_no_restricted_prefix():
    """The template's standing promise, beside a scan that can see a violation."""
    prompt = planner_prompt()

    for name in STAGE_NAMES:
        assert not re.search(rf"\b{re.escape(name)}\b", prompt), name
    for _, prefix in RESTRICTIONS:
        assert prefix not in prompt, prefix

    # Control: the same two readings over the same text with each planted.
    for planted in (RESTRICTED_STAGE, UNRESTRICTED_STAGE):
        assert re.search(rf"\b{re.escape(planted)}\b", prompt + f"\n{planted}\n")
    assert RESTRICTED_PREFIX in prompt + f"\n{RESTRICTED_PREFIX}\n"


# --------------------------------------------------------------------------
# The schema stays the contract
# --------------------------------------------------------------------------


def test_the_schema_states_the_widened_create_rule_and_what_a_grant_exempts():
    schema = json.loads(
        (HARNESS_ROOT / "schemas" / "story.schema.json").read_text(
            encoding="utf-8"))
    exceptions = schema["properties"]["stage_exceptions"]
    create = exceptions["items"]["properties"]["create"]["description"]

    assert re.search(r"(?i)at or beneath", create)
    assert re.search(r"(?i)ending in a slash|ends in a slash|ending with a slash",
                     create)
    assert re.search(r"(?i)exactly and only itself", create)
    assert re.search(r"(?i)refused", create)

    array = exceptions["description"]
    assert re.search(r"(?i)neither the ownership check nor the revert check",
                     array)
    assert re.search(r"(?i)reviewer weighs the reason", array)


def test_the_schema_still_accepts_a_grant_at_every_granularity():
    """The contract and the coordinator agree: both accept the same values.

    read_story validates against schemas/story.schema.json, so a grant the
    schema rejected would fail here before stage_exception_problems ever saw
    it — which is the half of the contract that lives in the schema.
    """
    for create in (RESTRICTED_PREFIX, FILE_GRANT,
                   f"{RESTRICTED_PREFIX}subdirectory/"):
        text = artifact("story-900") + exceptions_block(RESTRICTED_STAGE, create)
        reading = story_coordinator.read_story(text)
        assert reading.problems == [], (create, reading.problems)
        assert story_coordinator.stage_exception_problems(
            reading.parsed, STAGES) == [], create


# --------------------------------------------------------------------------
# The module docstring says what the new check is
# --------------------------------------------------------------------------


def test_the_module_docstring_distinguishes_the_structural_check_from_the_scan():
    doc = flowed(plan_validation.__doc__)
    assert re.search(r"(?i)structural", doc)
    assert re.search(r"(?i)plan time only|plan-time only|plan time is the only",
                     doc)
    assert re.search(r"(?i)pre-flight would refuse|adding (?:it|either) to "
                     r"pre-flight", doc)
    assert re.search(r"(?i)likely_file_changes", doc)
    assert re.search(r"(?i)scope\.modify", doc)


def test_the_new_check_states_why_likely_file_changes_is_its_subject():
    doc = flowed(plan_validation.assignment_problems.__doc__)
    assert "likely_file_changes" in doc
    assert "scope.modify" in doc


def test_the_clause_level_scan_is_untouched_by_this_story(tmp_path: Path):
    """Its behaviour, not its text: the four story-025 cases still decide the same.

    A mutation control sits beside it — the scan with its creation vocabulary
    emptied reports the scoped clause too — so "unchanged" is a reading that
    can fail.
    """
    over_strict = {"constraints": [
        f"the {RESTRICTED_STAGE} leaves {RESTRICTED_PREFIX} alone entirely"]}
    scoped = {"constraints": [
        f"the {RESTRICTED_STAGE} creates no files under {RESTRICTED_PREFIX}"]}

    assert len(plan_validation.strictness_problems(over_strict, STAGES)) == 1
    assert plan_validation.strictness_problems(scoped, STAGES) == []

    mutant = load_mutant(
        HARNESS_ROOT / "orchestration" / "plan_validation.py",
        [("creat(?:e|es|ed|ing|ion)", "nothing(?:-at-all)")],
        name="plan_validation_without_the_creation_word", tmp_path=tmp_path)
    assert mutant.strictness_problems(scoped, STAGES) != []
