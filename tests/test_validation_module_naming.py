"""Independent validation for story-038: a test module is named for what it
checks, and the story identity a renamed module resolves is declared.

Written from the story's acceptance criteria rather than from the
implementation. Four subjects, each asserted at the altitude it lives at:

  * **the names.** A search over `tests/` rather than a comparison against a
    listing — a listing is a second copy of the answer and goes stale the
    moment a module lands that nobody added to it.
  * **the resolution.** `conftest.story_commit_range` is a function over a
    repository, so it is driven directly: against this repository for the six
    ranges the story recorded before the rename, and against synthetic
    histories built here for the edges, including the exact history the
    declaration exists to survive — a story's run commit, then a rename.
  * **the plan-time refusal.** A pure function over a parsed story, driven
    directly, then end to end through the real `scripts/l5-plan` against a
    throwaway repository with a stub `claude` on PATH, reusing the fixture
    story-023, story-025 and story-032 built.
  * **the prose.** `prompts/tester.md` and `.harness/docs/ARCHITECTURE.md` are
    read and searched, never eyeballed.

Every absence asserted here carries a demonstration that it can fail:

  * "no module under tests/ is named for a story number" sits beside the same
    search over a directory with two such modules planted in it, and beside
    the two story-*subject* modules, which the same search must not report;
  * "a renamed module still resolves its own story's commits" sits beside the
    same history with the declaration absent, where the comparison goes
    vacuously green — which is the whole reason the declaration exists;
  * "every story-range call in a merged module names its origin" sits beside
    the same scan over that source with the qualification removed, and beside
    the live call, which raises when the origin is dropped;
  * "every tests/ path in the architecture document exists" sits beside the
    same search over that text with a stale path planted in it;
  * "no committed story artifact was rewritten" is resolved through the shared
    story range and sits beside a synthetic history whose run commit edits
    one, which the same comparison reports.

The recorded pairs in `test_the_six_recorded_ranges_resolve_after_the_rename`
are compared against rather than trusted: they were resolved while the story
was planned, before the rename, and are written into the story artifact for
exactly that purpose.

Nothing here invokes a model: every planning run goes through the stub
session.
"""
import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
from conftest import (NothingToCompareAgainst, declared_origins,
                      story_commit_range, story_diff)

from test_plan_commit import (  # noqa: F401 - fixtures used by name
    Planning,
    artifact,
    bare_remote,
    make_planning,
    remote_refs,
    run_plan,
    writes,
)
from test_plan_time_validation import L5_RUN, install, run_script

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"
sys.path.insert(0, str(REPO_ROOT / "orchestration"))

import harness_config  # noqa: E402
import plan_validation  # noqa: E402
import story_coordinator  # noqa: E402

WORKFLOW = conftest.shipped_workflow(REPO_ROOT, "story-workflow")
STAGES = WORKFLOW["stages"]

#: The committed corpus. Reached with `joinpath` rather than the `/` operator
#: because story-004 holds the suite to naming no path under the repository's
#: own run directory that way.
STORIES_DIR = REPO_ROOT.joinpath(".harness", "stories")

TESTER_PROMPT = REPO_ROOT / "prompts" / "tester.md"
ARCHITECTURE = REPO_ROOT.joinpath(".harness", "docs", "ARCHITECTURE.md")


def git(root: Path, *args: str) -> str:
    """One git command against a repository this file built.

    Every invocation states where it runs, which `tests/test_baseline_honesty
    .py` requires of every module, and every target here is a throwaway
    repository under tmp_path rather than this one.
    """
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def commit(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
        "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD").strip()


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# 1. The names, established by a search over the directory
# --------------------------------------------------------------------------


#: This module's own copy of the pattern, written from the story's words —
#: `test_story_` followed by digits — rather than imported from either
#: mechanism it holds to account. Importing it would make this file agree with
#: whatever the pattern happens to be rather than with what the story asked
#: for, and the two mechanisms are then compared against *this* below.
STORY_NUMBERED = re.compile(r"^test_story_\d+")

#: The two modules named for a story *subject*. The rule must leave both
#: alone, and they are named here so the control is about these files.
NAMED_FOR_A_SUBJECT = ("test_story_parser.py", "test_story_coordinator.py")


def story_numbered_under(directory: Path) -> list[str]:
    """Every module under `directory` whose name is a story number.

    A search, taking the directory as a parameter, so the same search the live
    suite is held to is the one run over a directory with violations planted
    in it.
    """
    return sorted(path.name for path in Path(directory).glob("*.py")
                  if STORY_NUMBERED.match(path.name))


def test_no_module_under_tests_is_named_for_a_story_number():
    """The first acceptance criterion, by search rather than by listing."""
    found = story_numbered_under(TESTS_DIR)
    assert found == [], found
    # The companion assertion the search needs: a search finding no files
    # reports nothing for a reason that has nothing to do with the names.
    assert len(list(TESTS_DIR.glob("*.py"))) >= 30


def test_the_same_search_reports_planted_violations(tmp_path):
    """The negative control for the absence above: the search is shown finding
    what it is looking for, in a directory built to contain it."""
    planted = tmp_path / "tests"
    planted.mkdir()
    for name in ("test_revert_check.py", "test_story_017_validation.py",
                 "test_story_006_single_reader.py", "conftest.py"):
        (planted / name).write_text("def test_it():\n    pass\n",
                                    encoding="utf-8")

    assert story_numbered_under(planted) == ["test_story_006_single_reader.py",
                                             "test_story_017_validation.py"]


def test_the_two_modules_named_for_a_story_subject_are_untouched():
    """The second criterion: both still exist under those names, and the
    search leaves both alone because it matches on the digits."""
    for name in NAMED_FOR_A_SUBJECT:
        assert (TESTS_DIR / name).is_file(), name
        assert STORY_NUMBERED.match(name) is None, name

    # The control for that control: the prefix alone is not what is matched.
    assert STORY_NUMBERED.match("test_story_017_validation.py")


def test_the_renamed_modules_all_exist_under_their_new_names():
    """The rename landed as a rename: every module the resolution declares an
    origin for is present, and thirty-two of them replace thirty-four."""
    declared = set(conftest.STORY_ORIGINS)
    present = {path.name for path in TESTS_DIR.glob("*.py")}
    assert declared <= present, sorted(declared - present)
    assert len(declared) == 32
    origins = {path for paths in conftest.STORY_ORIGINS.values() for path in paths}
    assert len(origins) == 34


# --------------------------------------------------------------------------
# 2. The standing scan in the suite, and the plan-time pattern, agree with the
#    story's words
# --------------------------------------------------------------------------


#: One corpus of names, fed to both mechanisms and to this file's own pattern.
#: Three independent statements of one convention are worth having only if they
#: decide the same names the same way.
NAME_CORPUS = (
    ("test_story_017_validation.py", True),
    ("test_story_006_single_reader.py", True),
    ("test_story_38.py", True),
    ("test_story_parser.py", False),
    ("test_story_coordinator.py", False),
    ("test_revert_check.py", False),
    ("test_stories_of_a_kind.py", False),
    ("conftest.py", False),
)


@pytest.mark.parametrize("name,is_story_numbered", NAME_CORPUS)
def test_all_three_statements_of_the_convention_decide_a_name_alike(
        name: str, is_story_numbered: bool):
    """This file's reading of the story, the standing scan, and the plan-time
    refusal, over the same names."""
    import test_baseline_honesty as scan

    assert bool(STORY_NUMBERED.match(name)) is is_story_numbered
    assert bool(scan.STORY_NUMBERED_MODULE.match(name)) is is_story_numbered
    assert bool(
        plan_validation.STORY_NUMBERED_MODULE.match(name)) is is_story_numbered


def test_the_standing_scan_is_in_the_suite_and_runs_over_the_directory():
    """The convention is held by a mechanism rather than by this file alone:
    the scan exists, searches `tests/`, and reports a planted violation."""
    import test_baseline_honesty as scan

    assert scan.story_numbered_modules(TESTS_DIR) == []
    assert set(NAMED_FOR_A_SUBJECT) <= {p.name for p in scan.all_modules()}


def test_the_standing_scan_reports_a_violation_planted_in_a_directory(tmp_path):
    import test_baseline_honesty as scan

    (tmp_path / "test_story_099_validation.py").write_text("x = 1\n",
                                                           encoding="utf-8")
    (tmp_path / "test_story_parser.py").write_text("x = 1\n", encoding="utf-8")
    assert scan.story_numbered_modules(tmp_path) == [
        "test_story_099_validation.py"]


# --------------------------------------------------------------------------
# 3. The resolution: the six recorded ranges, and the declaration's edges
# --------------------------------------------------------------------------


#: Resolved while this story was planned — before the rename — and written
#: into the artifact so that what the resolution returns afterwards is
#: compared against a recorded value rather than trusted.
RECORDED_RANGES = (
    ("tests/test_single_story_reader.py", "707685f7c7a2", "e0a2b1b9b209"),
    ("tests/test_execution_history.py", "2239a23b3059", "973fdb549bda"),
    ("tests/test_revert_check.py", "95afa16c8840", "fe565a379369"),
    ("tests/test_required_output_freshness.py", "d3e0be29477d", "033a39d17f01"),
    ("tests/test_git_history_loading_retired.py", "2c63960796fe", "edad04e97e4a"),
    ("tests/test_stage_baseline.py", "fdb7cfd25216", "8c5ac264e07a"),
)


@pytest.mark.parametrize("rel,baseline,endpoint", RECORDED_RANGES)
def test_the_six_recorded_ranges_resolve_after_the_rename(
        rel: str, baseline: str, endpoint: str):
    """A renamed module's story range is still its own story's commits."""
    resolved = story_commit_range(REPO_ROOT / rel)
    assert resolved.baseline.startswith(baseline), (rel, resolved.baseline)
    assert resolved.endpoint.startswith(endpoint), (rel, resolved.endpoint)


def test_the_recorded_ranges_come_from_the_declaration_and_not_from_the_name():
    """The companion the six assertions need.

    Six equalities against recorded values say nothing about *why* they hold,
    so what makes them evidence is stated separately: each range ends at the
    commit that added the module's declared origin, and at no commit that
    added the module under its current name. While this story is in flight the
    current path has no adding commit at all; once it commits, the rename is
    one — and it is not the endpoint either way.
    """
    for rel, _, endpoint in RECORDED_RANGES:
        origin = conftest.STORY_ORIGINS[Path(rel).name][0]
        added_then = git(REPO_ROOT, "log", "--diff-filter=A", "--format=%H",
                         "--", origin).split()
        assert added_then, origin
        assert added_then[-1].startswith(endpoint), rel

        added_now = git(REPO_ROOT, "log", "--diff-filter=A", "--format=%H",
                        "--", rel).split()
        assert not any(sha.startswith(endpoint) for sha in added_now), rel


def test_a_module_with_no_declaration_resolves_its_own_path(tmp_path):
    """A module written after this story needs no declaration.

    Driven against a synthetic history rather than argued from the source: the
    module has no entry, and the range returned is the commit that added it.
    """
    root = tmp_path / "undeclared"
    root.mkdir()
    git(root, "init", "-q")
    write(root, "src/app.py", "the pre-story content\n")
    parent = commit(root, "pre-story")

    rel = "tests/test_a_module_this_story_invented.py"
    assert declared_origins(Path(rel)) == ()
    validation_file = write(root, rel, "def test_it():\n    assert True\n")
    write(root, "src/app.py", "what the story changed\n")
    run_commit = commit(root, "the story's own run commit")

    resolved = story_commit_range(validation_file, root)
    assert resolved.baseline == parent
    assert resolved.endpoint == run_commit
    assert story_diff(["src/"], validation_file=validation_file,
                      repo=root).strip() != ""


def test_this_modules_own_resolution_is_the_undeclared_one():
    """Stated about this file rather than only about a synthetic one: it
    declares no origin, so it resolves its own path — which is what makes the
    differential assertions at the end of this module bite."""
    assert Path(__file__).name not in conftest.STORY_ORIGINS
    assert declared_origins(Path(__file__)) == ()


@pytest.mark.parametrize("name", ["test_planner_injection.py",
                                  "test_clean_clone_check.py"])
def test_a_merged_module_declares_two_origins(name: str):
    origins = conftest.STORY_ORIGINS[name]
    assert len(origins) == 2, origins
    for origin in origins:
        assert STORY_NUMBERED.match(Path(origin).name), origin


@pytest.mark.parametrize("name", ["test_planner_injection.py",
                                  "test_clean_clone_check.py"])
def test_a_call_naming_no_origin_on_a_merged_module_raises(name: str):
    """It refuses rather than guessing, and the message says what it could not
    decide: the module, and the origins it might have meant."""
    with pytest.raises(NothingToCompareAgainst) as raised:
        story_commit_range(TESTS_DIR / name)

    message = str(raised.value)
    assert name in message
    for origin in conftest.STORY_ORIGINS[name]:
        assert origin in message, message

    # The control: the same call naming one of them resolves.
    for origin in conftest.STORY_ORIGINS[name]:
        assert story_commit_range(TESTS_DIR / name, origin=origin).committed


def test_each_declared_origin_resolves_a_different_story():
    """Refusing to pick is only worth doing if the two answers differ."""
    for name in ("test_planner_injection.py", "test_clean_clone_check.py"):
        endpoints = {story_commit_range(TESTS_DIR / name, origin=origin).endpoint
                     for origin in conftest.STORY_ORIGINS[name]}
        assert len(endpoints) == 2, (name, endpoints)


def test_an_origin_a_module_does_not_declare_is_refused():
    """A stale call site fails as itself rather than resolving something."""
    with pytest.raises(NothingToCompareAgainst) as named_wrongly:
        story_commit_range(TESTS_DIR / "test_planner_injection.py",
                           origin="tests/test_story_014_validation.py")
    assert "test_story_014_validation.py" in str(named_wrongly.value)

    with pytest.raises(NothingToCompareAgainst) as undeclared:
        story_commit_range(Path(__file__),
                           origin="tests/test_story_038_validation.py")
    assert Path(__file__).name in str(undeclared.value)


# --------------------------------------------------------------------------
# 4. Why the declaration exists: the same history, with and without it
# --------------------------------------------------------------------------


def renamed_story(tmp_path: Path, origin: str, current: str, guarded: str,
                  *, violate: bool = True) -> Path:
    """A repository in which a story ran and was later renamed.

    Three commits, which is the shape this repository is now in: a pre-story
    state carrying the guarded path, the story's own run commit — which adds
    the validation file under its *original* name and, when `violate` says so,
    edits the guarded path in the same commit — and a later commit that
    renames the module and touches nothing else.

    That third commit is the whole subject. Resolving the story from the
    module's current path lands on it, and a comparison bounded there sees
    nothing whatever the story did.
    """
    root = tmp_path / "renamed"
    root.mkdir(parents=True)
    git(root, "init", "-q")
    write(root, guarded, "the pre-story content\n")
    commit(root, "pre-story")

    write(root, origin, "def test_it():\n    assert True\n")
    if violate:
        write(root, guarded, "what the story actually changed\n")
    commit(root, "the story's own run commit")

    git(root, "mv", origin, current)
    commit(root, "story-038: name the module for what it checks")
    return root


#: A sample of renamed modules, each with a path its story is known to have
#: changed. Enough breadth to cover a plain rename and a merged module,
#: without re-asserting the same mechanism thirty-two times.
RENAMED_SAMPLE = (
    ("tests/test_revert_check.py", "orchestration/story_coordinator.py"),
    ("tests/test_execution_history.py", "orchestration/story_coordinator.py"),
    ("tests/test_stage_baseline.py", "orchestration/story_coordinator.py"),
    ("tests/test_attempt_archiving.py", "orchestration/context_assembler.py"),
    ("tests/test_single_story_reader.py", "orchestration/story_coordinator.py"),
    ("tests/test_clean_clone_check.py", "orchestration/story_coordinator.py"),
)


@pytest.mark.parametrize("current,guarded", RENAMED_SAMPLE)
def test_a_renamed_modules_differential_assertion_still_goes_red(
        tmp_path, current: str, guarded: str):
    """The rename did not cost a module its bite.

    The declared origin is the real one out of `conftest.STORY_ORIGINS`, so
    this exercises the mapping the suite actually resolves through rather than
    a stand-in for it.
    """
    origins = conftest.STORY_ORIGINS[Path(current).name]
    origin = origins[-1]
    root = renamed_story(tmp_path, origin, current, guarded)
    validation_file = root / current

    qualified = {} if len(origins) == 1 else {"origin": origin}
    assert story_diff([guarded], validation_file=validation_file, repo=root,
                      **qualified).strip() != ""


@pytest.mark.parametrize("current,guarded", RENAMED_SAMPLE)
def test_the_same_assertion_passes_when_its_subject_is_respected(
        tmp_path, current: str, guarded: str):
    """The other half: it is a comparison rather than a constant."""
    origins = conftest.STORY_ORIGINS[Path(current).name]
    origin = origins[-1]
    root = renamed_story(tmp_path, origin, current, guarded, violate=False)
    qualified = {} if len(origins) == 1 else {"origin": origin}
    assert story_diff([guarded], validation_file=root / current, repo=root,
                      **qualified).strip() == ""


def test_without_the_declaration_the_same_violation_goes_vacuously_green(
        tmp_path):
    """Why the resolution was settled before the rename, shown rather than
    argued.

    The same three-commit history, with the module renamed to a name nothing
    declares an origin for. The resolution falls back to the module's own
    path, lands on the rename commit, and a comparison bounded there is empty
    — while the declared resolution over the identical history reports the
    violation.
    """
    origin = "tests/test_story_017_validation.py"
    guarded = "orchestration/story_coordinator.py"
    undeclared = "tests/test_a_rename_nothing_declares.py"
    assert declared_origins(Path(undeclared)) == ()

    root = renamed_story(tmp_path, origin, undeclared, guarded)
    inferred = story_commit_range(root / undeclared, root)
    assert git(root, "log", "-1", "--format=%s",
               inferred.endpoint).strip().startswith("story-038")
    assert story_diff([guarded], validation_file=root / undeclared,
                      repo=root).strip() == ""

    # The identical history under the declared name, which does report it.
    declared_root = renamed_story(tmp_path / "declared", origin,
                                  "tests/test_revert_check.py", guarded)
    assert story_diff([guarded],
                      validation_file=declared_root / "tests/test_revert_check.py",
                      repo=declared_root).strip() != ""


# --------------------------------------------------------------------------
# 5. Every story-range call in a merged module names the origin it means
# --------------------------------------------------------------------------


#: The four functions in `tests/conftest.py` that resolve a story's range.
#: `repository_file_at` and `function_source_at` do so only when handed a
#: validation file; handed a bare revision they resolve nothing and are not
#: this scan's business.
RANGE_CONSUMERS = ("story_commit_range", "story_diff", "repository_file_at",
                   "function_source_at")


def unqualified_range_calls(source: str) -> list[int]:
    """The lines of every story-range call in one module naming no origin."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name) else None)
        if name not in RANGE_CONSUMERS:
            continue
        keywords = {keyword.arg for keyword in node.keywords}
        resolves_a_story = name in ("story_commit_range", "story_diff") \
            or "validation_file" in keywords
        if resolves_a_story and "origin" not in keywords:
            lines.append(node.lineno)
    return lines


def range_calls(source: str) -> int:
    return len([node for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Call)
                and isinstance(node.func, (ast.Name, ast.Attribute))
                and (node.func.attr if isinstance(node.func, ast.Attribute)
                     else node.func.id) in RANGE_CONSUMERS])


@pytest.mark.parametrize("name", ["test_planner_injection.py",
                                  "test_clean_clone_check.py"])
def test_every_story_range_call_in_a_merged_module_names_its_origin(name: str):
    source = (TESTS_DIR / name).read_text(encoding="utf-8")
    assert unqualified_range_calls(source) == []
    # The companion the absence needs: there are calls to qualify.
    assert range_calls(source) >= 1, name


@pytest.mark.parametrize("name", ["test_planner_injection.py",
                                  "test_clean_clone_check.py"])
def test_the_scan_reports_the_same_module_with_the_qualification_removed(
        name: str):
    """The negative control, on the real source: strip the origin keyword and
    the same scan reports every call it was on."""
    source = (TESTS_DIR / name).read_text(encoding="utf-8")
    stripped = re.sub(r",?\s*origin=[A-Za-z_][A-Za-z_0-9]*", "", source)
    assert stripped != source
    assert unqualified_range_calls(stripped), name


@pytest.mark.parametrize("name", ["test_planner_injection.py",
                                  "test_clean_clone_check.py"])
def test_dropping_the_origin_is_a_refusal_rather_than_a_wrong_answer(name: str):
    """Not only reported by a scan: the call itself refuses, so a module that
    lost its qualification cannot go quietly green."""
    with pytest.raises(NothingToCompareAgainst):
        story_diff(["orchestration/"], validation_file=TESTS_DIR / name)


# --------------------------------------------------------------------------
# 6. The plan-time refusal
# --------------------------------------------------------------------------


#: The stage a plan may assign a file under tests/ to. Read off the loaded
#: workflow rather than written as a literal: the implementer is restricted
#: from creating files there, and an entry naming it would be refused by the
#: assignment check instead of by the one these tests are about.
BARRED_FROM_TESTS = {stage for stage, prefix
                     in story_coordinator.stage_restrictions(STAGES)
                     if "tests/".startswith(prefix)}
TESTS_STAGE = next(stage["name"] for stage in STAGES
                   if stage["name"] not in BARRED_FROM_TESTS)


def plan_block(*files: str) -> str:
    lines = ["\ntechnical_plan:", "  likely_file_changes:"]
    for path in files:
        lines += [f"    - file: {path}", f"      stage: {TESTS_STAGE}",
                  "      reason: the plan expects this"]
    return "\n".join(lines) + "\n"


OFFENDING = "tests/test_story_900_validation.py"
WELL_NAMED = "tests/test_validation_module_naming.py"

OFFENDING_ARTIFACT = artifact("story-900") + plan_block(OFFENDING, WELL_NAMED)
WELL_NAMED_ARTIFACT = artifact("story-900") + plan_block(WELL_NAMED)


def test_both_artifacts_this_file_uses_are_what_they_claim_to_be():
    """The refusal tests below would pass on an artifact nothing objects to,
    and the clean one would pass if it were unreadable."""
    for name, text in (("offending", OFFENDING_ARTIFACT),
                       ("well-named", WELL_NAMED_ARTIFACT)):
        reading = story_coordinator.read_story(text)
        assert reading.problems == [], (name, reading.problems)
        assert plan_validation.strictness_problems(reading.parsed, STAGES) == [], name
        assert plan_validation.assignment_problems(reading.parsed, STAGES, REPO_ROOT) == [], name


def test_the_check_reports_the_offending_entry_and_only_it():
    problems = plan_validation.naming_problems(
        story_coordinator.read_story(OFFENDING_ARTIFACT).parsed)
    assert len(problems) == 1, problems
    assert OFFENDING in problems[0]
    assert WELL_NAMED not in problems[0]

    assert plan_validation.naming_problems(
        story_coordinator.read_story(WELL_NAMED_ARTIFACT).parsed) == []


def test_the_message_says_what_to_name_the_module_instead():
    problem = plan_validation.naming_problems(
        story_coordinator.read_story(OFFENDING_ARTIFACT).parsed)[0]
    assert re.search(r"(?i)name a validation module for the behaviour it "
                     r"validates", problem), problem
    assert re.search(r"(?i)not for the story number", problem), problem


@pytest.mark.parametrize("story", [
    pytest.param({}, id="no-technical-plan"),
    pytest.param({"technical_plan": {}}, id="no-likely-file-changes"),
    pytest.param({"technical_plan": {"likely_file_changes": [{"stage": "x"}]}},
                 id="an-entry-with-no-file"),
    pytest.param({"technical_plan": {"likely_file_changes": ["a string"]}},
                 id="an-entry-that-is-not-a-mapping"),
])
def test_a_half_it_cannot_see_yields_no_problem_and_raises_nothing(story: dict):
    assert plan_validation.naming_problems(story) == []


def test_the_control_for_every_incomplete_story_above_is_the_complete_one():
    """Each case above passes for a stated reason rather than because the
    check reports nothing at all."""
    assert plan_validation.naming_problems(
        {"technical_plan": {"likely_file_changes": [{"file": OFFENDING}]}})


def test_artifact_problems_reports_the_new_class(tmp_path: Path):
    path = tmp_path / "story-900.yaml"
    path.write_text(OFFENDING_ARTIFACT, encoding="utf-8")
    found = plan_validation.artifact_problems([path], STAGES, REPO_ROOT)
    assert list(found) == [path]
    assert any(OFFENDING in problem for problem in found[path])


def test_artifact_problems_holds_the_well_named_artifact_back(tmp_path: Path):
    path = tmp_path / "story-901.yaml"
    path.write_text(WELL_NAMED_ARTIFACT, encoding="utf-8")
    assert plan_validation.artifact_problems([path], STAGES, REPO_ROOT) == {}


def test_a_story_that_fails_the_gate_yields_that_and_nothing_further(
        tmp_path: Path):
    """The existing order is kept: a story `read_story` objects to stops there.

    Non-vacuous because the same artifact made parseable does reach the new
    check, which is the second half.
    """
    unparseable = tmp_path / "story-902.yaml"
    unparseable.write_text("this: is: not: a story\n\t- ?\n", encoding="utf-8")
    found = plan_validation.artifact_problems([unparseable], STAGES, REPO_ROOT)
    assert found[unparseable]
    assert not any(OFFENDING in problem for problem in found[unparseable])

    reached = tmp_path / "story-903.yaml"
    reached.write_text(OFFENDING_ARTIFACT, encoding="utf-8")
    assert any(OFFENDING in problem
               for problem in plan_validation.artifact_problems(
                   [reached], STAGES, REPO_ROOT)[reached])


def corpus() -> dict[str, dict]:
    parsed = {}
    for path in sorted(STORIES_DIR.glob("story-*.yaml")):
        reading = story_coordinator.read_story(path.read_text(encoding="utf-8"))
        if reading.parsed is not None:
            parsed[path.stem] = reading.parsed
    return parsed


def test_the_committed_corpus_is_reported_and_stays_runnable():
    """Intended, and stated as intended: every story that named its own
    validation file after its number is reported, and none becomes unrunnable
    — the check is plan-time only, and pre-flight's story checks are
    `read_story` and `stage_exception_problems`.
    """
    stories = corpus()
    assert stories
    reported = {name for name, story in stories.items()
                if plan_validation.naming_problems(story)}
    assert "story-037" in reported
    # Not everything is reported, so "reported" is a property of the artifact
    # rather than of a check that says yes to whatever it is handed.
    assert "story-038" not in reported
    assert reported != set(stories)

    for name in reported:
        assert story_coordinator.stage_exception_problems(
            stories[name], STAGES) == [], name


# --------------------------------------------------------------------------
# 7. End to end, through the real scripts/l5-plan
# --------------------------------------------------------------------------


@pytest.fixture
def planning(tmp_path: Path) -> Planning:
    """A target repository with a stub `claude` on PATH and a bare origin."""
    made = make_planning(tmp_path)
    made.remote = bare_remote(tmp_path, made, upstream=True)
    return made


ARTIFACT_PATH = ".harness/stories/story-900.yaml"


def test_l5_plan_leaves_the_offending_artifact_uncommitted_and_prints_it(
        planning: Planning):
    """HEAD unmoved, nothing pushed, the artifact still exactly as written and
    still in the working tree for the developer to repair."""
    before, refs_before = planning.head(), remote_refs(planning.remote)

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (ARTIFACT_PATH, OFFENDING_ARTIFACT)))

    assert result.returncode != 0
    assert planning.head() == before
    assert remote_refs(planning.remote) == refs_before

    written = planning.root / ARTIFACT_PATH
    assert written.read_text(encoding="utf-8") == OFFENDING_ARTIFACT
    assert ARTIFACT_PATH in planning.status()

    printed = result.stdout + result.stderr
    assert OFFENDING in printed
    assert re.search(r"(?i)name a validation module for the behaviour", printed), \
        printed


def test_the_well_named_artifact_is_committed_by_the_same_run(
        planning: Planning):
    """The control for the refusal above: same fixture, same stub, committed
    and pushed."""
    before, refs_before = planning.head(), remote_refs(planning.remote)

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (ARTIFACT_PATH, WELL_NAMED_ARTIFACT)))

    assert result.returncode == 0, result.stdout + result.stderr
    assert planning.head() != before
    assert remote_refs(planning.remote) != refs_before
    assert planning.status() == ""


def test_pre_flight_does_not_start_refusing_the_naming_class(planning: Planning):
    """The check is plan-time only, driven through the real `scripts/l5-run`.

    Asserted by pre-flight getting past the story checks: it reaches the
    clean-tree refusal, which names the dirty path rather than the artifact.
    """
    install(planning, "story-900", OFFENDING_ARTIFACT)
    (planning.root / "dirty.txt").write_text("developer's own\n", encoding="utf-8")

    result = run_script(L5_RUN, planning, "story-900")

    assert result.returncode == 1
    assert "dirty.txt" in result.stderr
    assert "story-900.yaml" not in result.stderr


# --------------------------------------------------------------------------
# 8. The prose: the prompt and the architecture document
# --------------------------------------------------------------------------


def test_the_tester_prompt_states_the_convention_positively():
    """One sentence, in its own words, telling the tester what to do rather
    than reciting the mechanical rule two other layers enforce."""
    prompt = TESTER_PROMPT.read_text(encoding="utf-8")
    sentence = next((line for line in prompt.splitlines()
                     if re.search(r"(?i)name a validation module", line)), None)
    assert sentence is not None, prompt

    paragraph = prompt[prompt.index(sentence):]
    paragraph = paragraph[:paragraph.index("\n\n")]
    assert re.search(r"(?i)for the behaviour it validates", paragraph), paragraph

    # Positive: it says what to do. It does not restate the pattern the
    # plan-time check and the standing scan match on, which is what "in its own
    # words, without restating a rule the workflow enforces" rules out.
    assert "test_story_" not in paragraph, paragraph
    assert not re.search(r"(?i)\b(must not|never|do not)\b", paragraph), paragraph


def test_the_prompt_check_is_not_satisfied_by_any_paragraph():
    """The control for the search above: prose that says nothing about naming
    is not found by it, and prose that recites the pattern is rejected."""
    unrelated = "New tests belong in tests/ and become permanent assets.\n"
    assert re.search(r"(?i)name a validation module", unrelated) is None

    reciting = ("Name a validation module for the behaviour it validates. "
                "A module must not be named test_story_<digits>.\n")
    assert "test_story_" in reciting


#: A path written in the architecture document. Bounded at a non-word
#: character so `.harness/runs-archive/story-013-vacuous-tests/pre-reset-...`
#: is not read as a `tests/` path — the directory there ends in the word
#: "tests", and the file beneath it is not under `tests/` at all.
DOCUMENT_PATH = re.compile(r"(?<![\w./-])tests/[\w./-]*\.\w+")


def paths_under_tests_named_in(text: str) -> list[str]:
    return sorted({match.group(0) for match in DOCUMENT_PATH.finditer(text)})


def test_every_tests_path_the_architecture_document_names_exists():
    """Established by a search over the document rather than by reading it."""
    named = paths_under_tests_named_in(ARCHITECTURE.read_text(encoding="utf-8"))
    assert len(named) >= 15, named
    missing = [rel for rel in named if not (REPO_ROOT / rel).is_file()]
    assert missing == [], missing


def test_the_same_search_reports_a_stale_path_planted_in_the_document():
    """The negative control the absence needs: the search is shown finding a
    path that does not exist, in the document's own text."""
    planted = (ARCHITECTURE.read_text(encoding="utf-8")
               + "\nA sentence naming `tests/test_story_017_validation.py`.\n")
    named = paths_under_tests_named_in(planted)
    assert "tests/test_story_017_validation.py" in named
    assert [rel for rel in named if not (REPO_ROOT / rel).is_file()] == [
        "tests/test_story_017_validation.py"]


def test_the_search_does_not_read_the_archive_paths_as_tests_paths():
    """The other control: the pattern's boundary is load-bearing, because the
    archive directory's name ends in the word this pattern begins with."""
    archived = ("`.harness/runs-archive/story-013-vacuous-tests/"
                "pre-reset-test_story_013_validation.py`")
    assert paths_under_tests_named_in(archived) == []
    assert paths_under_tests_named_in("see tests/conftest.py") == ["tests/conftest.py"]


# --------------------------------------------------------------------------
# 9. What this story left alone
# --------------------------------------------------------------------------


#: Resolved through this module's own path, which nothing declares an origin
#: for — so the range is story-038's own run commit against its parent, and
#: while the story is in flight it is the working tree against HEAD.
THIS_FILE = Path(__file__).resolve()


def test_no_committed_story_artifact_was_rewritten():
    """They record what was true when they were written; history describing
    the past accurately is not drift.

    Narrowed to modifications and deletions: this story's own artifact was
    added by the planning commit, and an addition is not a rewrite.
    """
    assert story_diff([".harness/stories/"], validation_file=THIS_FILE,
                      diff_filter="MD", options=("--name-only",)).strip() == ""


def test_no_file_under_the_runs_archive_was_rewritten():
    """The archive is read-only evidence and this story adds nothing to it, so
    the comparison is unfiltered."""
    assert story_diff([".harness/runs-archive/"], validation_file=THIS_FILE,
                      options=("--name-only",)).strip() == ""


@pytest.mark.parametrize("guarded", [".harness/stories/story-001.yaml",
                                     ".harness/runs-archive/story-013/before.py"])
def test_the_same_comparison_reports_a_record_the_story_did_rewrite(
        tmp_path, guarded: str):
    """The negative control both absences need, against a synthetic history in
    which the story's own run commit edits a record it claims to have left
    alone — the state this repository cannot be in while these tests decide
    whether it commits."""
    root = tmp_path / "rewritten"
    root.mkdir()
    git(root, "init", "-q")
    write(root, guarded, "what was true when it was written\n")
    commit(root, "pre-story")

    validation_file = write(root, "tests/test_validation_module_naming.py",
                            "def test_it():\n    assert True\n")
    write(root, guarded, "quietly rewritten\n")
    commit(root, "the story's own run commit")

    assert story_diff([str(Path(guarded).parent) + "/"],
                      validation_file=validation_file, repo=root,
                      diff_filter="MD", options=("--name-only",)).strip() != ""


# --------------------------------------------------------------------------
# 10. What the rename commit does to a walk over a historical path
# --------------------------------------------------------------------------
#
# A module that walks its own history newest-first over the path it used to
# have now meets the rename at the head of that log: `git log -- <path>`
# reports the commit that *removed* a path as well as the ones that wrote it,
# and at the rename the historical path carries no blob. The walk is a
# resolution like any other, so it is held to the same rule as the rest of
# this file — it must resolve what it claims, and it must still refuse when
# there is genuinely nothing there.


#: The one module in the suite that resolves its baseline this way. Imported
#: rather than re-implemented, so what is exercised is the resolution the
#: suite actually runs.
from test_contract_assertions_bite import (  # noqa: E402
    REMOVED_TESTS,
    STORY_011_IN_HISTORY,
    WITHOUT_COMPARISONS,
    resolution_against,  # noqa: F401 - fixture used by name
    story_011_before_this_story,
    synthetic_history,
)


def revisions_touching(path: str) -> list[str]:
    """Every commit `git log` reports for `path`, newest first."""
    return git(REPO_ROOT, "log", "--format=%H", "--", path).split()


@pytest.mark.parametrize("origin", sorted(
    {origin for origins in conftest.STORY_ORIGINS.values()
     for origin in origins}))
def test_every_declared_origin_has_a_revision_carrying_no_blob(origin: str):
    """The trap, established over all thirty-four historical paths rather than
    over the one module that fell into it.

    Once the rename is committed, the newest commit `git log` reports for a
    declared origin is the one that removed it, and the path cannot be read
    there. Any newest-first walk over one of these paths that reads before it
    checks raises before its assertions run. This is a positive assertion
    about the repository's history — it fails loudly if the rename was not
    committed as a rename — and it is what makes the guard below necessary
    rather than decorative.
    """
    newest = revisions_touching(origin)
    assert newest, origin
    with pytest.raises(NothingToCompareAgainst):
        conftest.repository_file_at(origin, revision=newest[0],
                                    repo=REPO_ROOT)


def test_the_story_011_baseline_resolves_past_the_rename_commit():
    """The live resolution, run against this repository as the suite runs it.

    It must return the pre-story file rather than raising, and the file it
    returns must be the one it claims: every test story-016 removed is in it.
    """
    resolved = story_011_before_this_story()
    for name in REMOVED_TESTS:
        assert f"def {name}" in resolved, name


def test_the_same_walk_without_the_guard_raises_on_this_history():
    """The negative control the assertion above needs.

    Green from the live resolution says nothing on its own — it would look
    identical if the rename commit had never entered that log. So the same
    walk is run here with the guard removed and nothing else changed, over
    the same repository and the same path, and it must raise. That failure is
    the four the verifier found, reproduced.
    """
    with pytest.raises(NothingToCompareAgainst):
        for revision in revisions_touching(STORY_011_IN_HISTORY):
            source = conftest.repository_file_at(
                STORY_011_IN_HISTORY, revision=revision, repo=REPO_ROOT)
            if all(name in source for name in REMOVED_TESTS):
                break


def test_the_guard_did_not_turn_the_refusal_into_a_pass(tmp_path,
                                                        resolution_against):
    """Skipping an unreadable revision must not mean skipping the refusal.

    A guard written as "swallow the error and carry on" would also swallow a
    history that carries no candidate at all, and return whatever it reached
    last. Against a synthetic history where no revision holds the removed
    tests, the resolution still has to say there is nothing to compare
    against.
    """
    root = synthetic_history(
        tmp_path / "no-candidate",
        [WITHOUT_COMPARISONS, WITHOUT_COMPARISONS + "# a later edit\n"])
    with pytest.raises(AssertionError, match="nothing to compare against"):
        resolution_against(root)
