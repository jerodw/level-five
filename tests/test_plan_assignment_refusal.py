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

Since story-057 the check requires the grant rather than exempting the file
that exists. story-042 had narrowed it to report only an entry whose file was
absent, on the reading that a file already there is one the stage would modify
rather than create; what that left is a plan the harness accepts and a run the
harness can only refuse, since reverting an implementation or comment-only
change never breaks a suite. An entry beneath a prefix its own stage is
restricted under, with no grant covering it, is now reported whatever the
filesystem holds, and existence chooses only between two wordings. The last
section validates that rule, and every assertion in it is a matched pair or
carries its own control:

  * "the present file is reported" sits beside the very same story and the
    very same entry checked against a root that does not hold the file, which
    is reported too, and the two messages are required to differ only in the
    fault they name;
  * "the root decides the wording" is not reasoned about: the process working
    directory is moved to a root that disagrees with the one passed in, in both
    directions, and the wording follows the argument;
  * "a grant still short-circuits" sits beside the same story with the grant
    removed, and beside the same story with `grant_covers` replaced, so the
    grant is shown to be what decided it;
  * "story-056's committed artifact is reported by nothing" is read off the
    file on disk through the reader a run uses, beside the very same artifact
    with its `stage_exceptions` removed, which is reported once per governed
    entry;
  * "no run reads the check" is driven rather than inspected: both functions
    are spied on across a real coordinator run of a story carrying the
    conflict, and the spies are shown to fire when the check is called.

A final section holds the standing regression coverage for that rule, written
from the acceptance criteria rather than from the change, and it takes the
workflow as an *input*: its stage names and its prefix come from a fixture
definition this repository does not ship, so an assertion about how the check
matches does not redden when the deployment's restriction moves. It covers the
grant decision and the existence question as a matrix rather than a row —
three grants that cover the entry and three that do not, each against a root
that holds the file and a root that does not — one problem per offending entry
and per restriction, which the shipped workflow cannot exhibit at all because
it declares one prefix per stage, and the corpus as a whole rather than only
the part this check reports. Where the subject really is the shipped artifact —
story-056's committed plan, and pre-flight's answers about the corpus — the
shipped definition is what it reads.
"""
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import load_mutant, load_script
import conftest

# story-048 converted tests/test_revert_check.py to a workflow it builds for
# itself, so the stage the borrowed `run` drives is that definition's rather
# than the shipped one. The runs below name it through this import; the
# *shipped* restriction those runs are checked against stays read off the
# deployed workflow, which is this module's own subject.
from test_revert_check import WRITING as WRITING_STAGE  # noqa: F401
from test_revert_check import STAGE_NAMES as RUN_STAGE_NAMES  # noqa: F401
from test_revert_check import (  # noqa: F401 - fixtures used by name
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
from test_plan_commit import (  # noqa: F401
    ARTIFACT,
    Planning,
    artifact,
    bare_remote,
    make_planning,
    remote_refs,
    run_plan,
    writes,
)
from test_plan_time_validation import L5_RUN, install, run_script

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "orchestration"))

import harness_config  # noqa: E402
import plan_commit  # noqa: E402
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

WORKFLOW = conftest.shipped_workflow(HARNESS_ROOT, "story-workflow")
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

#: The root the check resolves existence against, for the assertions here whose
#: subject is the assignment itself. Since story-042 an entry is reported only
#: when the file it names does not exist beneath the given root — a file that
#: is already there is one the stage would modify, not create — and every path
#: this section names is absent beneath this one, so each assertion below still
#: turns on the assignment rather than on what this repository happens to hold.
#: The corpus assertions pass HARNESS_ROOT instead, because the corpus is read
#: from this repository and is about it.
ABSENT_ROOT = HARNESS_ROOT / "a-directory-this-repository-does-not-have"


def test_the_workflow_this_file_reads_still_has_something_to_say():
    """Every derivation above is load-bearing; an empty one would go green."""
    assert RESTRICTIONS, "the workflow declares no may_not_create restriction"
    assert UNRESTRICTED_STAGE in STAGE_NAMES
    assert UNDEFINED_STAGE not in STAGE_NAMES
    assert STORY_031_FILE.startswith(RESTRICTED_PREFIX)
    assert not ABSENT_ROOT.exists()
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
    problems = plan_validation.assignment_problems(CONFLICT, STAGES, ABSENT_ROOT)
    assert len(problems) == 1, problems


def test_the_reported_problem_names_the_file_the_stage_the_prefix_and_both_ways_out():
    """Asserted on the text, not on a problem having been returned.

    A planner reading this message has to be able to see all four things
    without going to the workflow definition, which is the point of stating
    the restriction in the workflow's own words.
    """
    (problem,) = plan_validation.assignment_problems(CONFLICT, STAGES, ABSENT_ROOT)

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
    assert plan_validation.assignment_problems(resolved, STAGES, ABSENT_ROOT) == []


def test_the_same_plan_with_a_grant_naming_that_exact_file_yields_nothing():
    """The second clean resolution, at the granularity of one file."""
    resolved = with_grant(CONFLICT, STORY_031_FILE)
    assert plan_validation.assignment_problems(resolved, STAGES, ABSENT_ROOT) == []


def test_a_grant_naming_the_whole_prefix_also_yields_nothing():
    resolved = with_grant(CONFLICT, RESTRICTED_PREFIX)
    assert plan_validation.assignment_problems(resolved, STAGES, ABSENT_ROOT) == []


def test_a_grant_naming_a_different_file_beneath_the_prefix_does_not_suppress_it():
    """The grant is not a prefix match here either."""
    other = with_grant(CONFLICT, f"{RESTRICTED_PREFIX}test_something_else.py")
    assert len(plan_validation.assignment_problems(other, STAGES, ABSENT_ROOT)) == 1


def test_a_grant_on_another_stage_does_not_suppress_it():
    other = with_grant(CONFLICT, STORY_031_FILE, stage=UNRESTRICTED_STAGE)
    assert len(plan_validation.assignment_problems(other, STAGES, ABSENT_ROOT)) == 1


@pytest.mark.parametrize("stage", STAGE_NAMES)
def test_a_file_outside_every_declared_prefix_yields_nothing_whatever_the_stage(stage):
    outside = plan(entry(OUTSIDE_EVERY_PREFIX, stage))
    assert plan_validation.assignment_problems(outside, STAGES, ABSENT_ROOT) == []


@pytest.mark.parametrize(
    "stage", [n for n in STAGE_NAMES if n not in RESTRICTED_STAGES]
)
def test_a_file_beneath_a_prefix_assigned_to_an_unrestricted_stage_yields_nothing(
        stage):
    allowed = plan(entry(STORY_031_FILE, stage))
    assert plan_validation.assignment_problems(allowed, STAGES, ABSENT_ROOT) == []


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
    assert plan_validation.assignment_problems(story, STAGES, ABSENT_ROOT) == []


def test_the_control_for_every_incomplete_entry_above_is_the_complete_one():
    assert plan_validation.assignment_problems(CONFLICT, STAGES, ABSENT_ROOT) != []


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

    problems = plan_validation.assignment_problems(story, synthetic_stages(), ABSENT_ROOT)

    assert len(problems) == 1, problems
    assert "cartography/atlas.py" in problems[0]
    assert UNDEFINED_STAGE in problems[0]
    # The real pair is silent here, and the synthetic pair is silent against
    # the real workflow: neither is written into the module.
    assert STORY_031_FILE not in problems[0]
    assert plan_validation.assignment_problems(story, STAGES, ABSENT_ROOT) != []
    assert "cartography" not in " ".join(
        plan_validation.assignment_problems(story, STAGES, ABSENT_ROOT))


def test_a_workflow_that_restricts_nothing_reports_nothing():
    unrestricted = [{"name": name} for name in STAGE_NAMES]
    assert story_coordinator.stage_restrictions(unrestricted) == []
    assert plan_validation.assignment_problems(CONFLICT, unrestricted, ABSENT_ROOT) == []


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
        story_coordinator.read_story(CONFLICTING_ARTIFACT).parsed, STAGES, ABSENT_ROOT) != []
    for clean in (REASSIGNED_ARTIFACT, GRANTED_ARTIFACT):
        assert plan_validation.assignment_problems(
            story_coordinator.read_story(clean).parsed, STAGES, ABSENT_ROOT) == []


def test_artifact_problems_reports_the_new_class(tmp_path: Path):
    path = write_artifact(tmp_path, CONFLICTING_ARTIFACT)
    found = plan_validation.artifact_problems([path], STAGES, ABSENT_ROOT,
        HARNESS_ROOT, WORKFLOW['name'])
    assert list(found) == [path]
    assert any(STORY_031_FILE in problem for problem in found[path])


def test_artifact_problems_holds_the_clean_resolutions_back(tmp_path: Path):
    for index, text in enumerate((REASSIGNED_ARTIFACT, GRANTED_ARTIFACT)):
        path = write_artifact(tmp_path, text, f"story-90{index}.yaml")
        assert plan_validation.artifact_problems([path], STAGES, ABSENT_ROOT,
        HARNESS_ROOT, WORKFLOW['name']) == {}


def test_a_story_that_fails_the_gate_yields_that_and_nothing_further(tmp_path: Path):
    """The existing order is kept: a story read_story objects to stops there.

    Non-vacuous because the same artifact, made parseable, does reach the new
    check — the second half below.
    """
    unparseable = write_artifact(tmp_path, "this: is: not: a story\n\t- ?\n")
    found = plan_validation.artifact_problems([unparseable], STAGES, ABSENT_ROOT,
        HARNESS_ROOT, WORKFLOW['name'])
    assert found[unparseable]
    assert not any(STORY_031_FILE in problem for problem in found[unparseable])

    # Schema-invalid, and carrying the conflict: still only the schema problem.
    invalid = write_artifact(
        tmp_path,
        CONFLICTING_ARTIFACT.replace("tasks:\n  - do the sample work\n", ""),
        "story-901.yaml")
    found = plan_validation.artifact_problems([invalid], STAGES, ABSENT_ROOT,
        HARNESS_ROOT, WORKFLOW['name'])
    assert found[invalid]
    assert not any(STORY_031_FILE in problem for problem in found[invalid])

    reached = write_artifact(tmp_path, CONFLICTING_ARTIFACT, "story-902.yaml")
    assert any(STORY_031_FILE in problem
               for problem in plan_validation.artifact_problems(
                   [reached], STAGES, ABSENT_ROOT,
                   HARNESS_ROOT, WORKFLOW["name"])[reached])


def test_the_strictness_check_still_reports_beside_the_new_one(tmp_path: Path):
    """Both classes on one artifact, so neither displaced the other."""
    # The over-strict sentence lands in `constraints`, the last array the
    # template writes, so the plan block goes on after it.
    both = (artifact("story-900")
            + f"  - the {RESTRICTED_STAGE} leaves {RESTRICTED_PREFIX} alone "
              f"entirely\n"
            + plan_block((STORY_031_FILE, STORY_031_STAGE)))
    path = write_artifact(tmp_path, both)
    problems = plan_validation.artifact_problems([path], STAGES, ABSENT_ROOT,
        HARNESS_ROOT, WORKFLOW['name'])[path]
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
                if plan_validation.assignment_problems(story, STAGES,
                                       HARNESS_ROOT)}

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
                if plan_validation.assignment_problems(story, STAGES,
                                       HARNESS_ROOT)]
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
    assert plan_validation.assignment_problems(granted_story, STAGES, ABSENT_ROOT) == []
    assert story_coordinator._ownership_violation(
        run_dir, "changed-files.json", prefixes, granted) is None
    assert story_coordinator.governed_edits(
        run_dir, "changed-files.json", prefixes, granted).paths == ()

    monkeypatch.setattr(story_coordinator, "grant_covers",
                        lambda granted, path: False)

    assert plan_validation.assignment_problems(granted_story, STAGES, ABSENT_ROOT) != []
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
    """Append one stage_exceptions entry per granted value to the story.

    The stage named is the one of the definition these runs actually execute —
    the workflow tests/test_revert_check.py builds, whose fixtures this module
    borrows. A grant naming a stage the loaded workflow does not define is
    refused at pre-flight, which is exactly the check story-032 added.
    """
    block = "\nstage_exceptions:\n"
    for create in creates:
        block += (f"  - stage: {WRITING_STAGE}\n"
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

    code, _ = run(target, harness_root, {WRITING_STAGE:
                                         unforced_edit_to_the_granted_file})

    assert code == 0
    assert GRANTED_FILE not in reverted_paths(clone_calls)


def test_the_same_edit_without_the_grant_escalates_on_the_revert_check(
        target: Path, harness_root: Path, clone_calls):
    """The control for the exemption above."""
    code, _ = run(target, harness_root, {WRITING_STAGE:
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

    code, _ = run(target, harness_root, {WRITING_STAGE: creates_a_second_file})

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

    code, _ = run(target, harness_root, {WRITING_STAGE:
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

    code, _ = run(target, harness_root, {WRITING_STAGE: everything})

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

    run(target, harness_root, {WRITING_STAGE: unforced_edit_to_the_granted_file})

    lines = stage_exception_events(target)
    assert len(lines) == 2, lines
    assert any(line.endswith(f"{WRITING_STAGE} may create {GRANTED_FILE}")
               for line in lines), lines
    assert any(line.endswith(f"{WRITING_STAGE} may create {RESTRICTED_PREFIX}")
               for line in lines), lines


def test_a_run_with_no_grant_writes_no_such_event(target: Path,
                                                  harness_root: Path):
    """The control for the count above."""
    run(target, harness_root, {WRITING_STAGE: unforced_edit_to_the_granted_file})
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
    code, _ = run(target, harness_root, {WRITING_STAGE:
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
    # The grant's required reason is stated by the injected schema and by the
    # problem the check reports, not by this template — `planner.md` states no
    # field of the contract itself, which a sibling module holds it to.
    assert not re.search(r"(?i)reason field is required", prompt), prompt


def test_the_planner_prompt_says_what_a_governed_entry_needs_beside_it():
    """The rule the artifact is now held to, in the paragraph that governs it.

    story-042 wrote that a governed entry needs a grant whether or not the file
    is already in the target repository. story-068 removed that: a grant is one
    of the things such an entry may carry, not the only one, so what the
    paragraph must say is that something is needed and that which one depends
    on the file. Both the removed sentence and the one story-042 itself removed
    are asserted absent, each beside a reading that finds it planted back in.
    """
    prompt = flowed(planner_prompt())
    retired = ("it needs a grant beside it whether or not the file is already "
               "in the target repository, and an entry that is not refused")

    assert re.search(r"(?i)needs something beside it", prompt)
    assert re.search(r"(?i)depends on the file", prompt)
    # Both faults are stated, so a planner reads why either way refuses.
    assert re.search(r"(?i)describes a creation", prompt)
    assert re.search(r"(?i)describes a modification", prompt)
    assert re.search(r"(?i)reverting breaks it", prompt)
    # And neither retired sentence is stated, each beside its own control.
    assert not re.search(r"(?i)whether or not the file is already in the target",
                         prompt), prompt
    assert re.search(r"(?i)whether or not the file is already in the target",
                     f"{prompt} {retired}")
    assert not re.search(r"(?i)is not refused", prompt), prompt
    assert re.search(r"(?i)is not refused", f"{prompt} {retired}")


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


# ==========================================================================
# story-057: the grant is required, and existence only words the message
#
# An entry beneath a prefix its own stage is restricted under, with no grant
# covering it, is reported whatever the filesystem holds: both run-time checks
# refuse the run it describes, the ownership check outright for a created file
# and the revert check for a modification that reverts without breaking the
# suite. What existence decides is which of those two faults the message
# names. Every assertion below is one half of a matched pair: the same story,
# the same entry, two roots that differ only in whether the named file is
# there.
# ==========================================================================


#: The two paths this section names, both beneath the restricted prefix and
#: both assigned to the restricted stage, so the only thing that can differ
#: between an accepted and a refused answer is whether the file exists.
PRESENT_FILE = f"{RESTRICTED_PREFIX}test_already_on_disk.py"
ABSENT_FILE = f"{RESTRICTED_PREFIX}test_not_written_yet.py"
PRESENT_DIRECTORY = f"{RESTRICTED_PREFIX}a_directory_that_is_there"

PRESENT = plan(entry(PRESENT_FILE, RESTRICTED_STAGE))
ABSENT = plan(entry(ABSENT_FILE, RESTRICTED_STAGE))


def roots(tmp_path: Path) -> tuple[Path, Path]:
    """A root holding this section's paths, and one holding none of them.

    Both are real directories, so "absent" is a root that exists and does not
    hold the file rather than a root that is not there at all — the weaker of
    the two conditions, and the one a plan is actually written against.
    """
    holding, empty = tmp_path / "holding", tmp_path / "empty"
    (holding / PRESENT_FILE).parent.mkdir(parents=True, exist_ok=True)
    (holding / PRESENT_FILE).write_text("# already here\n", encoding="utf-8")
    (holding / PRESENT_DIRECTORY).mkdir(parents=True, exist_ok=True)
    (empty / RESTRICTED_PREFIX).mkdir(parents=True, exist_ok=True)
    return holding, empty


def test_the_two_roots_this_section_uses_are_what_it_assumes(tmp_path: Path):
    holding, empty = roots(tmp_path)
    for root in (holding, empty):
        assert root.is_dir()
    assert (holding / PRESENT_FILE).is_file()
    assert (holding / PRESENT_DIRECTORY).is_dir()
    for path in (PRESENT_FILE, ABSENT_FILE, PRESENT_DIRECTORY):
        assert path.startswith(RESTRICTED_PREFIX)
        assert not (empty / path).exists()
    assert not (holding / ABSENT_FILE).exists()


def fault(problem: str) -> str:
    """The sentence between the declared restriction and the resolutions.

    The two wordings differ here and nowhere else, so reading this slice is
    what lets an assertion say which fault was named rather than only that
    something was reported.
    """
    after = problem.split(f"under {RESTRICTED_PREFIX}. ", 1)[1]
    return after.split("Either assign", 1)[0].strip()


def test_an_entry_naming_a_file_that_exists_beneath_the_root_is_still_reported(
        tmp_path: Path):
    """Existence stopped deciding the verdict, so both roots report.

    Same story, same entry, same workflow: the roots are the only difference,
    and the difference no longer reaches the verdict. That the check is asking
    the filesystem anything at all is the pair directly below, which reads the
    two messages.
    """
    holding, empty = roots(tmp_path)

    assert len(plan_validation.assignment_problems(PRESENT, STAGES, holding)) == 1
    assert len(plan_validation.assignment_problems(PRESENT, STAGES, empty)) == 1


def test_the_two_messages_differ_in_the_fault_and_in_one_resolution(
        tmp_path: Path):
    """One verdict, two wordings: the present file names the revert check.

    The two used to differ in the fault alone. story-068 gave a modification a
    third way out — declaring the adaptation forced — and withheld it from a
    creation, where declaring nothing helps a file that is not there, so the
    messages now differ in that clause too. What is still identical is
    everything before the fault and the two resolutions a creation does get, so
    both are compared here rather than only the leading half.

    Non-vacuous in both directions — the faults and the trailing clauses are
    required to differ, and the shared parts to be the same sentence.
    """
    holding, empty = roots(tmp_path)

    (present,) = plan_validation.assignment_problems(PRESENT, STAGES, holding)
    (absent,) = plan_validation.assignment_problems(PRESENT, STAGES, empty)

    assert fault(present) != fault(absent)
    # Everything up to the fault: the assignment, the stage and the declared
    # restriction, which neither root changes.
    assert (present.split(fault(present), 1)[0]
            == absent.split(fault(absent), 1)[0])
    # And the absent message's resolutions are exactly the opening of the
    # present one's, which then goes on to name the declaration.
    present_resolutions = present.split(fault(present), 1)[1].strip()
    absent_resolutions = absent.split(fault(absent), 1)[1].strip()
    assert present_resolutions.startswith(absent_resolutions)
    extra = present_resolutions[len(absent_resolutions):]
    assert "reverting_breaks_the_suite" in extra, extra
    assert "reverting_breaks_the_suite" not in absent, absent

    # The absent file describes a creation, and cites the rule the workflow
    # declares — which the ownership check is what refuses.
    assert re.search(r"(?i)describes a creation", absent), absent
    assert re.search(r"(?i)ownership check", absent), absent
    # The present one describes a modification the revert check governs, and
    # says what that check does rather than implying modifying is forbidden.
    assert re.search(r"(?i)describes a modification", present), present
    assert re.search(r"(?i)revert check", present), present
    assert re.search(r"(?i)reverting them breaks it|breaks the suite", present), \
        present


def test_an_entry_naming_a_file_that_does_not_exist_is_reported_too(
        tmp_path: Path):
    """The creation half, against the root that holds this section's file.

    A change that had swapped the two conditions rather than removing one
    would pass the test above and fail this one.
    """
    holding, _ = roots(tmp_path)

    (problem,) = plan_validation.assignment_problems(ABSENT, STAGES, holding)
    assert ABSENT_FILE in problem
    assert re.search(r"(?i)describes a creation", problem), problem


@pytest.mark.parametrize("root_name,expected_fault", [
    ("holding", "modification"),
    ("empty", "creation"),
])
def test_the_message_is_word_for_word_what_each_fault_says(
        tmp_path: Path, root_name: str, expected_fault: str):
    """Read off the produced text, not off the source that builds it.

    The whole message rather than substrings of it, for both wordings. The
    resolutions the two share are written once here and compared twice; the
    third way out belongs to the modification alone, so it is written beside
    that fault and its absence from the creation is what the creation row
    asserts by comparing the whole string.
    """
    root = dict(zip(("holding", "empty"), roots(tmp_path)))[root_name]
    faults = {
        "creation": (
            "The target root holds no such file, so the entry describes a "
            "creation, which the stage output ownership check refuses outright."
        ),
        "modification": (
            f"The target root already holds that file, so the entry describes "
            f"a modification, which the revert check governs: it restores the "
            f"stage's edits beneath that prefix and re-runs the suite, and "
            f"refuses them unless reverting them breaks it. The entry declares "
            f"no such forced adaptation."
        ),
    }
    declaring = {
        "creation": "",
        "modification": (
            f" If instead this edit is a test adaptation forced by a deliberate "
            f"change elsewhere in this story, so that reverting it would break "
            f"the suite, say so in reverting_breaks_the_suite on the entry: "
            f"unlike a grant, that leaves the revert check governing "
            f"'{PRESENT_FILE}' and deciding the claim when the story runs."
        ),
    }

    (problem,) = plan_validation.assignment_problems(PRESENT, STAGES, root)

    assert problem == (
        f"$.technical_plan.likely_file_changes[0]: assigns "
        f"'{PRESENT_FILE}' to stage '{RESTRICTED_STAGE}', which the workflow "
        f"declares: {RESTRICTED_STAGE} may not create files under "
        f"{RESTRICTED_PREFIX}. {faults[expected_fault]} "
        f"Either assign '{PRESENT_FILE}' to a stage that "
        f"may own it, or declare a stage_exceptions grant naming "
        f"'{PRESENT_FILE}' for {RESTRICTED_STAGE}, whose reason field is "
        f"required." + declaring[expected_fault]
    )


def test_a_path_present_as_a_directory_words_the_message_as_a_modification(
        tmp_path: Path):
    """exists(), not is_file(): a directory is there and cannot be created.

    Beside the same entry against the root that does not hold it, which is
    worded as a creation, so this is not a directory the check simply never
    looked at — and beside a regular file, which gets the same wording rather
    than a second rule.
    """
    holding, empty = roots(tmp_path)
    directory = plan(entry(PRESENT_DIRECTORY, RESTRICTED_STAGE))

    (present,) = plan_validation.assignment_problems(directory, STAGES, holding)
    (absent,) = plan_validation.assignment_problems(directory, STAGES, empty)

    assert "modification" in fault(present)
    assert "creation" in fault(absent)
    (file_present,) = plan_validation.assignment_problems(PRESENT, STAGES, holding)
    assert fault(file_present) == fault(present)


def test_the_root_argument_words_it_and_not_the_process_working_directory(
        tmp_path: Path, monkeypatch):
    """Driven from a working directory that disagrees with the root, both ways.

    The two coincide when the harness is its own target, so a check run from
    the repository root would prove nothing here. Standing in the root that
    holds the file while passing the one that does not must name a creation,
    and standing in the root that does not while passing the one that does must
    name a modification; a check reading `Path.cwd()` fails both.
    """
    holding, empty = roots(tmp_path)

    monkeypatch.chdir(holding)
    (from_empty,) = plan_validation.assignment_problems(PRESENT, STAGES, empty)
    assert "creation" in fault(from_empty)

    monkeypatch.chdir(empty)
    (from_holding,) = plan_validation.assignment_problems(PRESENT, STAGES, holding)
    assert "modification" in fault(from_holding)


def test_a_relative_root_is_still_the_root_it_was_given(tmp_path: Path,
                                                        monkeypatch):
    """The control above, with the root written relative to somewhere else.

    A relative root is resolved by the process working directory, so this is
    the one case where the two are allowed to interact — and the interaction
    is the caller's, not the check's.
    """
    holding, empty = roots(tmp_path)

    monkeypatch.chdir(tmp_path)
    (relative_holding,) = plan_validation.assignment_problems(
        PRESENT, STAGES, Path("holding"))
    (relative_empty,) = plan_validation.assignment_problems(
        PRESENT, STAGES, Path("empty"))
    assert "modification" in fault(relative_holding)
    assert "creation" in fault(relative_empty)


def test_neither_function_hides_the_root_behind_a_default():
    """A two-argument call raises rather than falling back to the cwd."""
    # No parameter of either carries a default, so the root cannot be omitted
    # in favour of the process working directory. artifact_problems carries
    # more parameters than the root — the harness root and the workflow the
    # planning session was rendered against are required of it too — so the
    # claim is made about defaults rather than about how many there are.
    for function in (plan_validation.assignment_problems,
                     plan_validation.artifact_problems):
        parameters = list(inspect.signature(function).parameters.values())
        assert parameters[2].name == "root", function.__name__
        for parameter in parameters:
            assert parameter.default is inspect.Parameter.empty, function.__name__

    with pytest.raises(TypeError):
        plan_validation.assignment_problems(PRESENT, STAGES)
    with pytest.raises(TypeError):
        plan_validation.artifact_problems([], STAGES)
    # Control: the fully-argumented calls those two are missing an argument
    # for do not raise.
    assert plan_validation.assignment_problems(PRESENT, STAGES, ABSENT_ROOT) != []
    assert plan_validation.artifact_problems(
        [], STAGES, ABSENT_ROOT, HARNESS_ROOT, WORKFLOW["name"]) == {}


def test_the_two_checks_that_read_no_filesystem_keep_their_signatures():
    """strictness_problems and naming_problems were left alone."""
    assert list(inspect.signature(
        plan_validation.strictness_problems).parameters) == ["story", "stages"]
    assert list(inspect.signature(
        plan_validation.naming_problems).parameters) == ["story"]


def test_a_grant_still_short_circuits_before_existence_is_asked(tmp_path: Path):
    """grant_covers keeps deciding grants; the new condition decides none of it.

    Two controls: the same story with the grant removed is reported, so the
    grant is what silenced it; and the same story with `grant_covers` replaced
    by a matcher that covers nothing is reported too, so the grant reached the
    answer through that function rather than through the file being anywhere.
    """
    _, empty = roots(tmp_path)
    granted = with_grant(PRESENT, PRESENT_FILE)

    assert plan_validation.assignment_problems(granted, STAGES, empty) == []
    assert len(plan_validation.assignment_problems(PRESENT, STAGES, empty)) == 1


def test_the_grant_is_what_silences_it_and_not_the_existence_question(
        tmp_path: Path, monkeypatch):
    _, empty = roots(tmp_path)
    granted = with_grant(PRESENT, PRESENT_FILE)

    monkeypatch.setattr(story_coordinator, "grant_covers",
                        lambda granted, path: False)

    assert len(plan_validation.assignment_problems(granted, STAGES, empty)) == 1


def test_artifact_problems_reports_against_either_root_and_words_it_from_one(
        tmp_path: Path):
    """The same pair one level up, through the function l5-plan calls.

    Non-vacuous because the same artifact carrying a grant is reported by
    neither root. A grant is not the only thing that silences the check —
    story-068 gave a modification a second way out, and
    tests/test_forced_adaptation_declaration.py holds that one — but it is the
    only one that silences it against a root that does not hold the file, which
    is why the pair here is still a pair.
    """
    holding, empty = roots(tmp_path)
    text = artifact("story-900") + plan_block((PRESENT_FILE, RESTRICTED_STAGE))
    path = write_artifact(tmp_path, text, "story-903.yaml")

    for root, expected in ((holding, "modification"), (empty, "creation")):
        (problem,) = plan_validation.artifact_problems([path], STAGES, root,
        HARNESS_ROOT, WORKFLOW['name'])[path]
        assert PRESENT_FILE in problem
        assert expected in fault(problem), root

    granted = write_artifact(
        tmp_path,
        text + exceptions_block(RESTRICTED_STAGE, PRESENT_FILE),
        "story-904.yaml")
    for root in (holding, empty):
        assert plan_validation.artifact_problems([granted], STAGES, root,
        HARNESS_ROOT, WORKFLOW['name']) == {}


# --------------------------------------------------------------------------
# The committed corpus under the grant requirement
#
# Artifacts written before this rule were held to the previous one, and the
# check is plan-time only, so being reported costs them nothing: pre-flight
# refuses none of them and every one is still runnable. The pair that carries
# the rule is story-056's, whose two grants are what this story requires of
# every plan after it.
# --------------------------------------------------------------------------


def story_on_disk(story_id: str) -> tuple[Path, dict]:
    """One committed artifact and its parse, through the reader a run uses."""
    path = STORIES_DIR / f"{story_id}.yaml"
    reading = story_coordinator.read_story(path.read_text(encoding="utf-8"))
    assert reading.problems == [], (story_id, reading.problems)
    return path, reading.parsed


def governed_entries(story: dict) -> list[dict]:
    """The entries this check is about: restricted stage, restricted prefix."""
    plan = story.get("technical_plan")
    entries = plan.get("likely_file_changes", []) if isinstance(plan, dict) else []
    return [e for e in entries
            if e.get("stage") == RESTRICTED_STAGE
            and e.get("file", "").startswith(RESTRICTED_PREFIX)]


def test_story_056s_committed_artifact_carrying_its_grants_is_reported_by_nothing():
    """The case this story exists for, read from disk through a run's reader.

    Two controls, so this is not an artifact the check merely never looked at:
    the entries are required to be the governed kind, and the same artifact
    with its grants removed is the test directly below.
    """
    path, story = story_on_disk("story-056")

    assert plan_validation.artifact_problems([path], STAGES, HARNESS_ROOT,
        HARNESS_ROOT, WORKFLOW['name']) == {}

    governed = governed_entries(story)
    assert governed, "story-056 no longer carries the entries this is about"
    assert story.get("stage_exceptions"), "story-056 no longer carries its grants"


def test_the_same_artifact_with_its_grants_removed_is_reported_once_per_entry():
    """The control for the acceptance above: only the grants differ.

    Its files are all present here, so this is also the corpus evidence that a
    present file is reported — which is exactly what story-042 exempted.
    """
    _, story = story_on_disk("story-056")
    ungranted = {k: v for k, v in story.items() if k != "stage_exceptions"}

    governed = governed_entries(story)
    problems = plan_validation.assignment_problems(ungranted, STAGES, HARNESS_ROOT)

    assert len(problems) == len(governed)
    for named, problem in zip(governed, problems):
        assert named["file"] in problem
        assert (HARNESS_ROOT / named["file"]).exists(), named["file"]
        assert "modification" in fault(problem)


def test_the_corpus_costs_the_artifacts_it_reports_nothing():
    """Being reported is not being refused, and nothing here has to be edited.

    The check is plan-time only: pre-flight's story checks are read_story and
    stage_exception_problems, and every reported artifact still passes both, so
    an artifact written before this rule stays runnable exactly as it was.
    Non-vacuous because the reported set is neither empty nor everything.
    """
    stories = corpus()
    reported = {name: story for name, story in stories.items()
                if plan_validation.assignment_problems(story, STAGES,
                                                       HARNESS_ROOT)}
    assert reported
    assert set(reported) != set(stories)
    assert "story-056" not in reported
    for name, story in reported.items():
        assert story_coordinator.read_story(
            (STORIES_DIR / f"{name}.yaml").read_text(encoding="utf-8")
        ).problems == [], name
        assert story_coordinator.stage_exception_problems(story, STAGES) == [], name


# --------------------------------------------------------------------------
# The module still names neither half of the restriction, docstring included
# --------------------------------------------------------------------------


def test_the_module_names_no_stage_and_no_prefix_in_its_prose_either():
    """The raw text, not the stripped text: the docstring was rewritten here.

    `literals_named` strips docstrings and comments, which is right for the
    promise about *code* and would miss a stage name written into the
    rewritten prose. Beside the same scan over the same text with each literal
    planted in it.
    """
    module = (HARNESS_ROOT / "orchestration" / "plan_validation.py").read_text(
        encoding="utf-8")

    for name in STAGE_NAMES:
        assert not re.search(rf"\b{re.escape(name)}\b", module), name
    for _, prefix in RESTRICTIONS:
        assert prefix not in module, prefix

    for planted in (RESTRICTED_STAGE, UNRESTRICTED_STAGE):
        assert re.search(rf"\b{re.escape(planted)}\b", module + f"\n{planted}\n")
    assert RESTRICTED_PREFIX in module + f"\n{RESTRICTED_PREFIX}\n"


def test_the_docstring_states_the_rule_as_it_now_stands():
    """What the check decides, what existence decides, and what it leaves alone.

    Each assertion is a positive reading of produced prose, so each fails on
    its own the moment the section stops saying its half.
    """
    doc = flowed(plan_validation.__doc__)
    # The rule: an offending entry needs a grant or, where the file is already
    # there, a declaration that the edit was forced.
    assert re.search(r"(?i)no grant on that stage covers it", doc)
    assert re.search(r"(?i)does not declare the edit a forced test adaptation",
                     doc)
    # What existence decides. story-042 wrote that it decides the wording and
    # not the verdict; story-068 made it decide whether the declaration is read
    # at all, so the second half of that sentence is asserted absent here and
    # the third check's own docstring is where the phrase now lives, if at all.
    assert re.search(r"(?i)existence decides the \*\*wording\*\*|existence "
                     r"decides the wording", doc)
    assert re.search(r"(?i)whether the declaration is read at all", doc)
    assert not re.search(r"(?i)decides the wording and not the verdict", doc), doc
    # Control for that absence: the same search over the same text with the
    # retired clause planted back into it.
    assert re.search(r"(?i)decides the wording and not the verdict",
                     f"{doc} Existence decides the wording and not the verdict.")
    assert re.search(r"(?i)describes a creation", doc)
    assert re.search(r"(?i)describes a modification", doc)
    # Both run-time checks are named, and stated as untouched by this.
    assert re.search(r"(?i)ownership check", doc)
    assert re.search(r"(?i)revert check", doc)
    assert re.search(r"(?i)neither run-time check is weakened, anticipated or "
                     r"duplicated", doc)
    # And what it still is: a plan-time prediction resolved against the target.
    assert re.search(r"(?i)target root", doc)
    assert re.search(r"(?i)neither the harness root nor the process working "
                     r"directory|not the harness root and not the process "
                     r"working directory", doc)
    assert re.search(r"(?i)prediction", doc)


# --------------------------------------------------------------------------
# scripts/l5-plan: the root it passes, and the refusing path unchanged
# --------------------------------------------------------------------------


L5_PLAN_SCRIPT = load_script("l5-plan", name="l5_plan_for_story_042")


def test_report_passes_the_target_root_it_was_given_to_the_check(
        tmp_path: Path, monkeypatch):
    """Not read off the source: the check records the root it was called with."""
    stories_dir = tmp_path / "stories"
    stories_dir.mkdir()
    before = plan_commit.snapshot(stories_dir)
    (stories_dir / "story-900.yaml").write_text(
        artifact("story-900") + plan_block((PRESENT_FILE, RESTRICTED_STAGE)),
        encoding="utf-8")
    _, empty = roots(tmp_path)
    seen: list[Path] = []

    original = plan_validation.artifact_problems

    def spy(artifacts, stages, root, harness_root, selected):
        seen.append(root)
        return original(artifacts, stages, root, harness_root, selected)

    monkeypatch.setattr(plan_validation, "artifact_problems", spy)

    L5_PLAN_SCRIPT.report(empty, stories_dir, before, STAGES)

    assert seen == [empty]


def test_report_prints_and_returns_on_the_refusing_path_exactly_as_today(
        tmp_path: Path, capsys):
    """Byte for byte: the header, the problem, the guidance, the summary line.

    The status too, which is what `main` exits with when the session itself
    succeeded. Every assertion here is a positive one over produced text, so
    each fails on its own the moment a character of the refusing path moves;
    that the accepting root gets past validation instead is the pair above,
    driven through `artifact_problems` and end to end through `l5-plan`.
    """
    stories_dir = tmp_path / "stories"
    stories_dir.mkdir()
    before = plan_commit.snapshot(stories_dir)
    path = stories_dir / "story-900.yaml"
    path.write_text(artifact("story-900") + plan_block(
        (PRESENT_FILE, RESTRICTED_STAGE)), encoding="utf-8")
    _, empty = roots(tmp_path)

    status = L5_PLAN_SCRIPT.report(empty, stories_dir, before, STAGES)
    printed = capsys.readouterr()

    (problem,) = plan_validation.assignment_problems(PRESENT, STAGES, empty)
    assert status == 1
    assert printed.err == (
        f"{path} is not a valid story artifact:\n"
        f"  - {problem}\n"
        "Fix the artifact or re-run planning before executing the story.\n"
    )
    assert printed.out == (
        f"l5-plan: committed nothing; {path} remain in the working tree.\n"
    )


@pytest.fixture
def planning_holding(tmp_path: Path) -> Planning:
    """The `planning` fixture's repository, already holding the planned file.

    The file is committed *before* the bare origin is made, so the two are
    level and the base check has nothing to say — the difference between this
    fixture and `planning` is the file and nothing else.
    """
    made = make_planning(tmp_path)
    path = made.root / PRESENT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# already here\n", encoding="utf-8")
    made.git("add", "-A")
    made.git("commit", "-q", "-m", "the file the plan names")
    made.remote = bare_remote(tmp_path, made, upstream=True)
    return made


PRESENT_ARTIFACT = artifact("story-900") + plan_block(
    (PRESENT_FILE, RESTRICTED_STAGE))


GRANTED_PRESENT_ARTIFACT = PRESENT_ARTIFACT + exceptions_block(
    RESTRICTED_STAGE, PRESENT_FILE)


def test_l5_plan_withholds_a_plan_naming_a_file_the_target_already_holds(
        planning_holding: Planning):
    """End to end, in the repository that does hold the file.

    Its control is the very same artifact with a grant naming that file added
    to it, directly below, which the same fixture and the same stub commit.
    """
    before = planning_holding.head()
    refs_before = remote_refs(planning_holding.remote)

    result = run_plan(planning_holding, L5_STUB_WRITE=writes(
        (ARTIFACT_PATH, PRESENT_ARTIFACT)))

    assert result.returncode != 0
    assert planning_holding.head() == before
    assert remote_refs(planning_holding.remote) == refs_before
    assert ARTIFACT_PATH in planning_holding.status()

    printed = result.stdout + result.stderr
    assert PRESENT_FILE in printed
    assert "modification" in printed
    assert re.search(r"(?i)stage_exceptions grant naming", printed), printed


def test_l5_plan_commits_it_once_a_grant_naming_the_file_is_added(
        planning_holding: Planning):
    """The control for the refusal above: the grant is the only difference."""
    before = planning_holding.head()
    refs_before = remote_refs(planning_holding.remote)

    result = run_plan(planning_holding, L5_STUB_WRITE=writes(
        (ARTIFACT_PATH, GRANTED_PRESENT_ARTIFACT)))

    assert result.returncode == 0, result.stdout + result.stderr
    assert planning_holding.head() != before
    assert remote_refs(planning_holding.remote) != refs_before
    assert planning_holding.status() == ""


def test_l5_plan_refuses_the_same_plan_where_the_target_lacks_the_file(
        planning: Planning):
    """The other repository, and the other wording: refused either way."""
    before = planning.head()

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (ARTIFACT_PATH, PRESENT_ARTIFACT)))

    assert result.returncode != 0
    assert planning.head() == before
    printed = result.stdout + result.stderr
    assert PRESENT_FILE in printed
    assert "creation" in printed
    assert ARTIFACT_PATH in planning.status()


def test_l5_plan_words_the_problem_from_the_target_root_not_its_cwd(
        planning_holding: Planning):
    """Run from a subdirectory, where the two answers differ.

    From `work/`, the plan's path resolved against the working directory is
    not there and resolved against the target root is. Both runs are refused —
    that is this story's change — so what the root decides is the fault named,
    and the control is the second run in the same repository with that file
    removed, which names the other one.
    """
    work = planning_holding.root / "work"
    work.mkdir()
    assert not (work / PRESENT_FILE).exists()

    from_holding = subprocess.run(
        [sys.executable, str(HARNESS_ROOT / "scripts" / "l5-plan"), "add a thing"],
        cwd=work,
        env=planning_holding.env(L5_STUB_WRITE=writes(
            (f"../{ARTIFACT_PATH}", PRESENT_ARTIFACT))),
        capture_output=True, text=True,
    )

    assert from_holding.returncode != 0
    assert "modification" in from_holding.stdout + from_holding.stderr

    (planning_holding.root / PRESENT_FILE).unlink()
    from_empty = subprocess.run(
        [sys.executable, str(HARNESS_ROOT / "scripts" / "l5-plan"), "add a thing"],
        cwd=work,
        env=planning_holding.env(L5_STUB_WRITE=writes(
            (f"../{ARTIFACT_PATH.replace('900', '901')}",
             PRESENT_ARTIFACT.replace("story-900", "story-901")))),
        capture_output=True, text=True,
    )

    assert from_empty.returncode != 0
    assert PRESENT_FILE in from_empty.stdout + from_empty.stderr
    assert "creation" in from_empty.stdout + from_empty.stderr


# --------------------------------------------------------------------------
# No run reads this check
# --------------------------------------------------------------------------


def test_no_run_calls_either_plan_time_function(target: Path, harness_root: Path,
                                                monkeypatch):
    """Driven, not inspected: both functions are spied on across a real run.

    The story the run executes carries exactly the entry the check reports —
    an absent file beneath the restricted prefix on the restricted stage — so
    a coordinator that consulted the check at pre-flight or anywhere else
    would both fire a spy and refuse the run. It does neither, and the run
    completes through all four stages as it does today.
    """
    calls: list[str] = []
    for name in ("assignment_problems", "artifact_problems"):
        original = getattr(plan_validation, name)

        def spy(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(plan_validation, name, spy)

    append_to_story(target, plan_block((ABSENT_FILE, RESTRICTED_STAGE)))
    story = story_coordinator.read_story(
        (target / ".harness" / "stories" / "story-001.yaml").read_text(
            encoding="utf-8"))
    assert story.problems == []

    code, runner = run(target, harness_root, {})

    assert code == 0
    assert runner.calls == RUN_STAGE_NAMES
    assert calls == []
    # Control one: the artifact this run carried is one the check does report,
    # so the silence above is the coordinator's and not the artifact's.
    assert plan_validation.assignment_problems(story.parsed, STAGES, target) != []
    # Control two: that call went through the spy, so the spies were wired.
    assert calls == ["assignment_problems"]


# ==========================================================================
# story-057, standing regression coverage
#
# The section above was repointed by the stage that changed the check, so its
# assertions follow that change by construction. What follows was written
# against the story's acceptance criteria instead, and covers the products the
# repointing left partial:
#
#   * the grant decision and the existence question are independent, which
#     needs the whole matrix rather than one row of it — three grants that
#     cover the entry and three that do not, each against a root that holds
#     the file and a root that does not;
#   * one problem per offending entry *and restriction*, which the shipped
#     workflow cannot exhibit because it declares a single prefix;
#   * the committed corpus stays runnable as a whole, not only the part of it
#     this check reports.
#
# The workflow is an input to all of that and not its subject: which stage the
# harness restricts is a deployment fact, and asserting how the check matches
# should not redden when it changes. So this section derives its stage names
# and prefixes from a fixture workflow this repository does not ship, exactly
# as the section above derives them from the one it does. Where the subject
# genuinely is the shipped artifact — the corpus, and story-056's own plan —
# the shipped definition is read.
# ==========================================================================


#: A workflow definition this repository does not ship. Its stage names and
#: its prefix are written once, here, and every name below is derived from it
#: through the same story_coordinator.stage_restrictions the check uses — so
#: no test below writes a stage or a prefix of its own any more than the
#: section above does.
FIXTURE_STAGES = [
    {"name": "surveyor", "may_not_create": ["charts/"]},
    {"name": "draughtsman"},
]
FIXTURE_RESTRICTIONS = story_coordinator.stage_restrictions(FIXTURE_STAGES)
(FIXTURE_STAGE, FIXTURE_PREFIX), = FIXTURE_RESTRICTIONS
FIXTURE_FREE_STAGE = next(
    stage["name"] for stage in FIXTURE_STAGES
    if stage["name"] not in {name for name, _ in FIXTURE_RESTRICTIONS}
)

#: A second fixture workflow, whose one stage is restricted under a prefix and
#: under a directory inside it. The shipped workflow declares a single prefix
#: per stage and so cannot exhibit an entry that offends against two
#: restrictions at once, which is the "one problem per entry and restriction"
#: half of the check's contract.
FIXTURE_NESTED_STAGES = [
    {"name": FIXTURE_STAGE,
     "may_not_create": [FIXTURE_PREFIX, f"{FIXTURE_PREFIX}coastal/"]},
]

FIXTURE_FILE = f"{FIXTURE_PREFIX}chart_of_the_bay.py"
FIXTURE_SIBLING = f"{FIXTURE_PREFIX}chart_of_the_cape.py"
FIXTURE_SUBDIRECTORY = f"{FIXTURE_PREFIX}coastal/"
FIXTURE_DEEP_FILE = f"{FIXTURE_SUBDIRECTORY}chart_of_the_reef.py"
FIXTURE_OUTSIDE = "logbook/entry.py"


def fixture_plan(*entries: tuple[str, str]) -> dict:
    return plan(*(entry(file, stage) for file, stage in entries))


def fixture_grants(story: dict, *grants: tuple[str, str]) -> dict:
    """The same story with stage_exceptions naming each (stage, path) pair."""
    granted = dict(story)
    granted["stage_exceptions"] = [
        {"stage": stage, "create": create, "reason": "the deliverable needs it"}
        for stage, create in grants
    ]
    return granted


def fault_in(problem: str, prefix: str) -> str:
    """`fault`, above, for a problem reported against an arbitrary prefix.

    The section above reads the slice against the shipped prefix; the fixture
    workflow here declares its own, so the boundary the slice is taken at has
    to come from the restriction the problem was reported for.
    """
    after = problem.split(f"under {prefix}. ", 1)[1]
    return after.split("Either assign", 1)[0].strip()


def fixture_roots(tmp_path: Path) -> tuple[Path, Path]:
    """A root holding every path this section names, and one holding none.

    Both exist as directories, so "absent" is a real repository that does not
    hold the file rather than a missing directory — the case a plan is
    actually written against.
    """
    holding, empty = tmp_path / "holds", tmp_path / "lacks"
    for path in (FIXTURE_FILE, FIXTURE_SIBLING, FIXTURE_DEEP_FILE, FIXTURE_OUTSIDE):
        (holding / path).parent.mkdir(parents=True, exist_ok=True)
        (holding / path).write_text("# already here\n", encoding="utf-8")
    (empty / FIXTURE_PREFIX).mkdir(parents=True, exist_ok=True)
    return holding, empty


def test_the_fixture_workflow_is_one_this_repository_does_not_ship(tmp_path: Path):
    """Every derivation below is load-bearing, and none of it is the shipped one.

    A fixture that happened to name a shipped stage or a shipped prefix would
    make the assertions below agree with the deployment they are meant to be
    independent of, and an empty derivation would make them all vacuous.
    """
    assert FIXTURE_RESTRICTIONS
    assert FIXTURE_STAGE not in STAGE_NAMES
    assert FIXTURE_FREE_STAGE not in STAGE_NAMES
    for _, prefix in RESTRICTIONS:
        assert not FIXTURE_PREFIX.startswith(prefix)
        assert not prefix.startswith(FIXTURE_PREFIX)
    for path in (FIXTURE_FILE, FIXTURE_SIBLING, FIXTURE_SUBDIRECTORY,
                 FIXTURE_DEEP_FILE):
        assert path.startswith(FIXTURE_PREFIX)
    assert not FIXTURE_OUTSIDE.startswith(FIXTURE_PREFIX)
    assert len({FIXTURE_FILE, FIXTURE_SIBLING, FIXTURE_DEEP_FILE}) == 3

    holding, empty = fixture_roots(tmp_path)
    assert (holding / FIXTURE_FILE).is_file()
    for path in (FIXTURE_FILE, FIXTURE_SIBLING, FIXTURE_DEEP_FILE):
        assert not (empty / path).exists()


# --------------------------------------------------------------------------
# The grant decision and the existence question are independent
#
# Each row below is asserted against both roots, so a check that had kept any
# part of the filesystem in its verdict — rather than only in its wording —
# fails one half of every row.
# --------------------------------------------------------------------------


CONFLICTING_ENTRY = (FIXTURE_FILE, FIXTURE_STAGE)


@pytest.mark.parametrize("grants,description", [
    pytest.param([(FIXTURE_STAGE, FIXTURE_FILE)], "that exact file", id="exact"),
    pytest.param([(FIXTURE_STAGE, FIXTURE_PREFIX)], "the whole prefix",
                 id="whole-prefix"),
    pytest.param([(FIXTURE_STAGE, FIXTURE_SIBLING),
                  (FIXTURE_STAGE, FIXTURE_FILE)], "one grant among several",
                 id="among-several"),
])
@pytest.mark.parametrize("root_name", ["holding", "empty"])
def test_a_grant_covering_the_file_silences_it_against_either_root(
        tmp_path: Path, grants, description: str, root_name: str):
    """The grant decides, and it decides the same either side of the filesystem.

    The control is `test_the_ungranted_entry_these_rows_silence_is_reported`
    directly below: the very same story and root with no grant on it at all is
    reported, so an empty result here is the grant's doing rather than an
    entry the check never matched.
    """
    root = dict(zip(("holding", "empty"), fixture_roots(tmp_path)))[root_name]
    story = fixture_grants(fixture_plan(CONFLICTING_ENTRY), *grants)

    assert plan_validation.assignment_problems(
        story, FIXTURE_STAGES, root) == [], (description, root_name)


@pytest.mark.parametrize("root_name", ["holding", "empty"])
def test_the_ungranted_entry_these_rows_silence_is_reported(tmp_path: Path,
                                                            root_name: str):
    """The control for every silencing row above, and for every one below."""
    root = dict(zip(("holding", "empty"), fixture_roots(tmp_path)))[root_name]

    (problem,) = plan_validation.assignment_problems(
        fixture_plan(CONFLICTING_ENTRY), FIXTURE_STAGES, root)
    assert FIXTURE_FILE in problem
    assert FIXTURE_STAGE in problem
    assert FIXTURE_PREFIX in problem


@pytest.mark.parametrize("grants,description", [
    pytest.param([(FIXTURE_STAGE, FIXTURE_SIBLING)],
                 "another file beneath the same prefix", id="sibling-file"),
    pytest.param([(FIXTURE_STAGE, FIXTURE_SUBDIRECTORY)],
                 "a directory beneath it the file is not in", id="sibling-dir"),
    pytest.param([(FIXTURE_FREE_STAGE, FIXTURE_FILE)],
                 "that exact file, granted to another stage", id="other-stage"),
])
@pytest.mark.parametrize("root_name", ["holding", "empty"])
def test_a_grant_that_does_not_cover_it_leaves_it_reported_against_either_root(
        tmp_path: Path, grants, description: str, root_name: str):
    """A single-file grant covers that file alone, and a grant belongs to a stage.

    Positive assertions throughout — each fails on its own the moment the
    entry stops being reported — and the matched pair is the silencing rows
    above, which are the same stories with the covering grant instead.
    """
    root = dict(zip(("holding", "empty"), fixture_roots(tmp_path)))[root_name]
    story = fixture_grants(fixture_plan(CONFLICTING_ENTRY), *grants)

    (problem,) = plan_validation.assignment_problems(story, FIXTURE_STAGES, root)
    assert FIXTURE_FILE in problem, (description, root_name)


@pytest.mark.parametrize("root_name", ["holding", "empty"])
def test_a_directory_grant_covers_a_file_further_down_against_either_root(
        tmp_path: Path, root_name: str):
    """The nested half of the grant contract, at the check rather than at the matcher.

    Paired with the entry naming a file the same grant does not reach, which
    is reported — so the silence is the grant covering this path and not the
    check declining to look beneath a directory.
    """
    root = dict(zip(("holding", "empty"), fixture_roots(tmp_path)))[root_name]
    grants = [(FIXTURE_STAGE, FIXTURE_SUBDIRECTORY)]

    deep = fixture_grants(fixture_plan((FIXTURE_DEEP_FILE, FIXTURE_STAGE)), *grants)
    assert plan_validation.assignment_problems(deep, FIXTURE_STAGES, root) == []

    shallow = fixture_grants(fixture_plan(CONFLICTING_ENTRY), *grants)
    assert len(plan_validation.assignment_problems(
        shallow, FIXTURE_STAGES, root)) == 1


@pytest.mark.parametrize("root_name", ["holding", "empty"])
def test_a_story_with_no_stage_exceptions_naming_no_governed_path_yields_nothing(
        tmp_path: Path, root_name: str):
    """An absent grant is only a problem when there is a governed entry to grant.

    The control is the same story with one entry repointed at the restricted
    stage, which is reported — so the silence is the paths being ungoverned
    rather than a check that reports nothing without stage_exceptions present.
    """
    root = dict(zip(("holding", "empty"), fixture_roots(tmp_path)))[root_name]
    ungoverned = fixture_plan(
        (FIXTURE_OUTSIDE, FIXTURE_STAGE),
        (FIXTURE_FILE, FIXTURE_FREE_STAGE),
    )
    assert "stage_exceptions" not in ungoverned

    assert plan_validation.assignment_problems(ungoverned, FIXTURE_STAGES, root) == []

    governed = fixture_plan((FIXTURE_OUTSIDE, FIXTURE_STAGE), CONFLICTING_ENTRY)
    assert "stage_exceptions" not in governed
    assert len(plan_validation.assignment_problems(
        governed, FIXTURE_STAGES, root)) == 1


# --------------------------------------------------------------------------
# One problem per offending entry, and per restriction
# --------------------------------------------------------------------------


@pytest.mark.parametrize("root_name", ["holding", "empty"])
def test_each_offending_entry_is_reported_once_at_its_own_index(tmp_path: Path,
                                                                root_name: str):
    """Four entries, two of which offend, reported at the indices they sit at.

    The two that do not offend are the controls for the two that do: one is
    granted and one is ungoverned, so a check that reported per story rather
    than per entry, or that lost an entry's index, disagrees here.
    """
    root = dict(zip(("holding", "empty"), fixture_roots(tmp_path)))[root_name]
    story = fixture_grants(
        fixture_plan(
            (FIXTURE_SIBLING, FIXTURE_STAGE),      # 0: granted
            CONFLICTING_ENTRY,                     # 1: offends
            (FIXTURE_OUTSIDE, FIXTURE_STAGE),      # 2: beneath no prefix
            (FIXTURE_DEEP_FILE, FIXTURE_STAGE),    # 3: offends
        ),
        (FIXTURE_STAGE, FIXTURE_SIBLING),
    )

    problems = plan_validation.assignment_problems(story, FIXTURE_STAGES, root)

    assert len(problems) == 2, problems
    assert "likely_file_changes[1]" in problems[0]
    assert FIXTURE_FILE in problems[0]
    assert "likely_file_changes[3]" in problems[1]
    assert FIXTURE_DEEP_FILE in problems[1]
    joined = " ".join(problems)
    assert FIXTURE_SIBLING not in joined
    assert FIXTURE_OUTSIDE not in joined


@pytest.mark.parametrize("root_name", ["holding", "empty"])
def test_an_entry_offending_against_two_restrictions_is_reported_for_each(
        tmp_path: Path, root_name: str):
    """One problem per (entry, restriction), each naming its own prefix.

    The nested fixture workflow declares both prefixes for one stage, which
    the shipped workflow cannot: it declares one prefix per stage, so this
    half of the contract has nowhere else to be driven. The control is the
    same entry against the single-restriction fixture, which is reported once.
    """
    root = dict(zip(("holding", "empty"), fixture_roots(tmp_path)))[root_name]
    story = fixture_plan((FIXTURE_DEEP_FILE, FIXTURE_STAGE))

    problems = plan_validation.assignment_problems(story, FIXTURE_NESTED_STAGES,
                                                   root)

    assert len(problems) == 2, problems
    declared = [prefix for _, prefix in
                story_coordinator.stage_restrictions(FIXTURE_NESTED_STAGES)]
    assert [fault_in(p, prefix) for p, prefix in zip(problems, declared)]
    for problem, prefix in zip(problems, declared):
        assert f"{FIXTURE_STAGE} may not create files under {prefix}" in problem

    assert len(plan_validation.assignment_problems(
        story, FIXTURE_STAGES, root)) == 1


@pytest.mark.parametrize("root_name", ["holding", "empty"])
def test_an_entry_naming_the_restricted_prefix_itself_is_reported(
        tmp_path: Path, root_name: str):
    """The boundary of the match: the prefix is beneath itself.

    Paired with a sibling directory sharing its text but not lying beneath it,
    which is not reported — so the match is the prefix and not a bare
    substring of it.
    """
    root = dict(zip(("holding", "empty"), fixture_roots(tmp_path)))[root_name]

    (problem,) = plan_validation.assignment_problems(
        fixture_plan((FIXTURE_PREFIX, FIXTURE_STAGE)), FIXTURE_STAGES, root)
    assert FIXTURE_PREFIX in problem

    sibling = FIXTURE_PREFIX.rstrip("/") + "-archive/atlas.py"
    assert plan_validation.assignment_problems(
        fixture_plan((sibling, FIXTURE_STAGE)), FIXTURE_STAGES, root) == []


# --------------------------------------------------------------------------
# The wording, driven from the fixture rather than from the deployment
# --------------------------------------------------------------------------


def test_the_wording_follows_the_root_and_the_shared_repair_does_not(
        tmp_path: Path):
    """The same entry against both roots: one verdict, two faults, one repair.

    Asserted as a difference *and* as a sameness. What varies with the root is
    the fault and — since story-068 — the third way out, which a present file
    has and an absent one cannot: declaring nothing rescues a file that is not
    there. What must not vary is everything before the fault and the two
    resolutions both faults share, so a change that moved any of that with the
    root fails here even though both roots still report.
    """
    holding, empty = fixture_roots(tmp_path)
    story = fixture_plan(CONFLICTING_ENTRY)

    (present,) = plan_validation.assignment_problems(story, FIXTURE_STAGES, holding)
    (absent,) = plan_validation.assignment_problems(story, FIXTURE_STAGES, empty)

    present_fault = fault_in(present, FIXTURE_PREFIX)
    absent_fault = fault_in(absent, FIXTURE_PREFIX)
    assert present_fault and absent_fault
    assert present_fault != absent_fault
    assert (present.split(present_fault, 1)[0]
            == absent.split(absent_fault, 1)[0])
    assert present.split(present_fault, 1)[1].startswith(
        absent.split(absent_fault, 1)[1])

    assert re.search(r"(?i)modification", present_fault), present_fault
    assert re.search(r"(?i)revert check", present_fault), present_fault
    assert re.search(r"(?i)creation", absent_fault), absent_fault
    assert re.search(r"(?i)ownership check", absent_fault), absent_fault

    # The repair is stated in both, identically, and names the grant's reason.
    for problem in (present, absent):
        assert re.search(r"(?i)assign '%s' to a stage that may own it"
                         % re.escape(FIXTURE_FILE), problem), problem
        assert re.search(r"(?i)stage_exceptions grant naming '%s' for %s"
                         % (re.escape(FIXTURE_FILE), re.escape(FIXTURE_STAGE)),
                         problem), problem
        assert re.search(r"(?i)reason field is required", problem), problem


def test_the_wording_of_each_entry_is_decided_for_that_entry_alone(
        tmp_path: Path):
    """One story, one root, two entries — one file there and one not.

    A check that had asked the filesystem once per story, or had let the first
    entry's answer stand for the second, reports the same fault twice here.
    """
    holding, _ = fixture_roots(tmp_path)
    missing = f"{FIXTURE_PREFIX}chart_not_drawn_yet.py"
    assert not (holding / missing).exists()

    first, second = plan_validation.assignment_problems(
        fixture_plan(CONFLICTING_ENTRY, (missing, FIXTURE_STAGE)),
        FIXTURE_STAGES, holding)

    assert "modification" in fault_in(first, FIXTURE_PREFIX)
    assert "creation" in fault_in(second, FIXTURE_PREFIX)


# --------------------------------------------------------------------------
# story-056's committed artifact, one grant at a time
#
# Here the shipped artifact is the subject: the pair this story exists for is
# that plan, as committed, against that plan without what makes it legal.
# --------------------------------------------------------------------------


def test_removing_either_of_story_056s_grants_reports_exactly_that_entry():
    """Each grant is load-bearing on its own, not merely as a pair.

    The committed artifact carrying both is reported by nothing — the section
    above asserts that — and dropping the two together is reported once per
    governed entry. Dropping one at a time is what shows the grant that is
    reported is the one that was removed, rather than the artifact tipping
    from clean to dirty as a whole.
    """
    _, story = story_on_disk("story-056")
    grants = story["stage_exceptions"]
    assert len(grants) > 1, "story-056 no longer carries more than one grant"

    for dropped in grants:
        kept = dict(story)
        kept["stage_exceptions"] = [g for g in grants if g is not dropped]

        problems = plan_validation.assignment_problems(kept, STAGES, HARNESS_ROOT)

        assert len(problems) == 1, (dropped["create"], problems)
        assert dropped["create"] in problems[0]
        for other in kept["stage_exceptions"]:
            assert other["create"] not in problems[0]


# --------------------------------------------------------------------------
# The committed corpus stays runnable, all of it
# --------------------------------------------------------------------------


def pre_flight_problems(text: str) -> list[str]:
    """Pre-flight's two story checks, in the order run_story asks them.

    read_story parses and validates against the shipped schema, and
    stage_exception_problems cross-checks the grants against the workflow.
    Neither is this story's, and neither is reached by the plan-time check;
    what a run refuses is exactly what these two say.
    """
    reading = story_coordinator.read_story(text)
    if reading.problems:
        return list(reading.problems)
    return story_coordinator.stage_exception_problems(reading.parsed, STAGES)


def test_every_committed_artifacts_grants_still_satisfy_the_workflow(
        tmp_path: Path):
    """The whole corpus, not only the part the new check reports.

    Every artifact that parses is one whose grants the workflow still accepts,
    so nothing already committed has to be edited to keep running. The control
    is a copy of one committed artifact with its grants repointed at a stage
    the workflow does not define, which the same reading reports.
    """
    paths = sorted(STORIES_DIR.glob("story-*.yaml"))
    assert paths, "no committed story artifact was found"

    parsed = 0
    for path in paths:
        reading = story_coordinator.read_story(path.read_text(encoding="utf-8"))
        if reading.parsed is None:
            continue
        parsed += 1
        assert story_coordinator.stage_exception_problems(
            reading.parsed, STAGES) == [], path.name
    assert parsed, "no committed artifact parsed, so the loop asserted nothing"

    broken = tmp_path / "story-905.yaml"
    broken.write_text(
        (STORIES_DIR / "story-056.yaml").read_text(encoding="utf-8").replace(
            f"stage: {RESTRICTED_STAGE}", f"stage: {UNDEFINED_STAGE}"),
        encoding="utf-8")
    reading = story_coordinator.read_story(broken.read_text(encoding="utf-8"))
    assert reading.parsed is not None
    assert story_coordinator.stage_exception_problems(reading.parsed, STAGES) != []


def test_pre_flight_refuses_no_artifact_for_anything_this_story_added(
        monkeypatch):
    """Whatever pre-flight says about the corpus, it says without this check.

    Driven rather than reasoned about: the plan-time functions are replaced by
    ones that raise, and every committed artifact is put through pre-flight's
    two story checks again. A pre-flight that had grown a call to either would
    raise instead of answering, and the answers are required to be the ones it
    gave with the real functions in place.

    So this is not the absence of a call argued from imports, and the control
    is the last two lines: the replacements do raise when they are called.
    """
    texts = {path.name: path.read_text(encoding="utf-8")
             for path in sorted(STORIES_DIR.glob("story-*.yaml"))}
    assert texts, "no committed story artifact was found"
    before = {name: pre_flight_problems(text) for name, text in texts.items()}
    (sample,) = plan_validation.assignment_problems(PRESENT, STAGES, ABSENT_ROOT)

    def refuse(*args, **kwargs):
        raise AssertionError("pre-flight reached a plan-time check")

    for name in ("assignment_problems", "artifact_problems"):
        monkeypatch.setattr(plan_validation, name, refuse)

    assert {name: pre_flight_problems(text)
            for name, text in texts.items()} == before

    # And the vocabulary this story added appears in none of pre-flight's
    # answers. The control is `sample`, a problem the new check really does
    # report, which the same reading finds the phrase in.
    reported = " ".join(p for problems in before.values() for p in problems)
    assert "stage_exceptions grant naming" not in reported
    assert "stage_exceptions grant naming" in sample

    # And the replacements above do raise, so the equality is pre-flight not
    # calling them rather than the monkeypatching not having taken.
    with pytest.raises(AssertionError):
        plan_validation.assignment_problems(PRESENT, STAGES, ABSENT_ROOT)


# --------------------------------------------------------------------------
# End to end: the second clean resolution, in the repository that holds the file
# --------------------------------------------------------------------------


WHOLE_PREFIX_GRANTED_ARTIFACT = PRESENT_ARTIFACT + exceptions_block(
    RESTRICTED_STAGE, RESTRICTED_PREFIX)

REASSIGNED_PRESENT_ARTIFACT = artifact("story-900") + plan_block(
    (PRESENT_FILE, UNRESTRICTED_STAGE))


@pytest.mark.parametrize("resolution,text", [
    ("granted-the-whole-prefix", WHOLE_PREFIX_GRANTED_ARTIFACT),
    ("reassigned", REASSIGNED_PRESENT_ARTIFACT),
])
def test_l5_plan_commits_either_resolution_where_the_target_holds_the_file(
        resolution: str, text: str, planning_holding: Planning):
    """Both ways out of the modification fault, driven through the real script.

    The refusal these two are the control for is
    `test_l5_plan_withholds_a_plan_naming_a_file_the_target_already_holds`: the
    same fixture, the same stub and the same plan, differing only in the
    resolution applied to it.
    """
    before = planning_holding.head()
    refs_before = remote_refs(planning_holding.remote)

    result = run_plan(planning_holding, L5_STUB_WRITE=writes(
        (ARTIFACT_PATH, text)))

    assert result.returncode == 0, (resolution,
                                    result.stdout + result.stderr)
    assert planning_holding.head() != before, resolution
    assert remote_refs(planning_holding.remote) != refs_before, resolution
    assert planning_holding.status() == "", resolution
