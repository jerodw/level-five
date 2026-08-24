"""Independent validation for story-068: a plan may declare a forced test
adaptation, and the declaration is read at plan time and nowhere else.

Written from the story's acceptance criteria rather than from the change. The
rule has two halves and they live at different altitudes, so nothing here is
asserted at one altitude and argued at the other:

  * **plan time.** `assignment_problems` is a pure function over a parsed
    story, the loaded workflow and a target root, so it is driven directly,
    and through `artifact_problems` — the function `l5-plan` calls — over
    artifacts written to disk.
  * **run time.** "no run-time check consults the declaration" is not read off
    the source of either check. A real target repository with a real pytest
    suite is built, its story is given the declaration on the very path the
    stage then edits, and the coordinator is run: whether the declaration
    reached anything is answered by what the run did.

The workflow is an **input** to the plan-time half and not its subject: which
stage this deployment restricts, and under what prefix, is a deployment fact,
and an assertion about how the check matches must not redden when it moves. So
that half derives its stage names and its prefix from a fixture workflow this
repository does not ship, through the same `story_coordinator.stage_restrictions`
the check itself reads them through — no stage name and no prefix is written
into a test below any more than into the module under test. Where the subject
genuinely *is* a shipped artifact — story-060's committed plan, story-056's,
the schema, the module's own prose, `prompts/planner.md` — the shipped thing is
what is read.

Every absence asserted here carries a demonstration that it can fail:

  * "a declared entry yields no problem" sits beside the very same entry with
    the declaration removed, which is reported once, and beside the same
    declared entry resolved against a root that does not hold the path, which
    is reported as a creation — so the silence is the declaration's doing
    rather than a check that stopped looking;
  * "story-060's plan reconstructed with the declaration is reported by
    nothing" sits beside the same reconstruction carrying neither grant nor
    declaration, which is reported at that entry;
  * "story-056's artifact with its grants stripped is still reported" is the
    unchanged case, and its control is the committed artifact itself, which is
    reported by nothing;
  * "the field name appears in no orchestration module but one, and in no
    workflow or rules file" sits beside the same search over the three files
    that do hold it, and beside the same search over a planted copy of a file
    that does not;
  * "no run-time check consults it" is driven rather than inspected, and its
    control is that the very same story is accepted at plan time *because* of
    the declaration — a typo'd or inert field would make the run-time silence
    say nothing at all;
  * "the corrected prose no longer states the removed absolute" sits beside
    the same search over that prose with the retired sentence planted back in.

`.harness/docs/ARCHITECTURE.md` is not asserted on here. This story assigns it
to the documenter in `likely_file_changes`, which is the stage that runs after
this one.

Nothing here invokes a model: every coordinator run goes through a fake agent
runner.
"""
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

import conftest

# The driven half borrows the target repository, the fake runner and the
# workflow tests/test_revert_check.py builds for itself, rather than building a
# fifth one beside them. That workflow is a fixture there and stays one here:
# the stage these runs execute and the prefix it is governed under are read off
# that definition, never written down.
from test_revert_check import WORKFLOW as RUN_WORKFLOW  # noqa: F401
from test_revert_check import (  # noqa: F401 - fixtures used by name
    APP_ADDITIVE,
    added_coverage,
    append_to_story,
    clone_calls,
    harness_root,
    run,
    run_dir_of,
    target,
    write,
)
from test_plan_commit import artifact

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "orchestration"))

import plan_validation  # noqa: E402
import schema_validator  # noqa: E402
import story_coordinator  # noqa: E402

#: The committed corpus. Written with `joinpath` rather than the `/` operator
#: because story-004 holds the suite to naming no path under the repository's
#: own run directory that way.
STORIES_DIR = HARNESS_ROOT.joinpath(".harness", "stories")
PLAN_VALIDATION = HARNESS_ROOT / "orchestration" / "plan_validation.py"
PLANNER_PROMPT = HARNESS_ROOT / "prompts" / "planner.md"
STORY_SCHEMA = HARNESS_ROOT / "schemas" / "story.schema.json"

#: The field this story adds. One string, written once, and every reading below
#: derives from it — including the containment scan, whose subject it is.
FIELD = "reverting_breaks_the_suite"

# --------------------------------------------------------------------------
# The fixture workflow: a deployment this repository does not ship
# --------------------------------------------------------------------------

FIXTURE_STAGES = [
    {"name": "cartographer", "may_not_create": ["charts/"]},
    {"name": "engraver"},
]
FIXTURE_RESTRICTIONS = story_coordinator.stage_restrictions(FIXTURE_STAGES)
(FIXTURE_STAGE, FIXTURE_PREFIX), = FIXTURE_RESTRICTIONS
FIXTURE_FREE_STAGE = next(
    stage["name"] for stage in FIXTURE_STAGES
    if stage["name"] not in {name for name, _ in FIXTURE_RESTRICTIONS}
)

#: A path beneath the governed prefix that the holding root below carries, and
#: one it does not. The pair is the whole of what existence can decide.
GOVERNED_FILE = f"{FIXTURE_PREFIX}chart_of_the_bay.py"
UNWRITTEN_FILE = f"{FIXTURE_PREFIX}chart_not_drawn_yet.py"
UNGOVERNED_FILE = "logbook/entry.py"

#: A declaration's text, which no check reads. It is a sentence a reviewer
#: weighs, so it is written as one rather than as a marker word.
DECLARED = ("the survey change in this story renames the datum this chart "
            "asserts, so reverting this edit leaves the suite red")

#: The shipped workflow, read only where a shipped artifact is the subject:
#: story-060's committed plan and story-056's were written against this
#: deployment's restriction, and reconstructing them against a fixture would be
#: reconstructing a different story.
SHIPPED_WORKFLOW = conftest.shipped_workflow(HARNESS_ROOT, "story-workflow")
SHIPPED_STAGES = SHIPPED_WORKFLOW["stages"]

#: The workflow a planning session was rendered against, which
#: artifact_problems requires beside the harness root. No artifact this module
#: puts to it declares a workflow of its own, so the check it feeds reports
#: nothing here; it is supplied because the function requires it rather than
#: because anything in this module is about it.
RENDERED_AGAINST = SHIPPED_WORKFLOW["name"]


def plan(*entries: dict) -> dict:
    return {"technical_plan": {"likely_file_changes": list(entries)}}


def entry(file: str, stage: str, declaring: str | None = None) -> dict:
    made = {"file": file, "stage": stage, "reason": "because the plan says so"}
    if declaring is not None:
        made[FIELD] = declaring
    return made


def with_grant(story: dict, create: str, stage: str = FIXTURE_STAGE) -> dict:
    granted = dict(story)
    granted["stage_exceptions"] = [
        {"stage": stage, "create": create, "reason": "the deliverable needs it"}
    ]
    return granted


def roots(tmp_path: Path) -> tuple[Path, Path]:
    """A root holding this module's governed file, and one holding none of it.

    Both are real directories, so "absent" is a repository that does not hold
    the file rather than a directory that is not there at all — the weaker
    condition, and the one a plan is actually written against.
    """
    holding, empty = tmp_path / "holds", tmp_path / "lacks"
    for path in (GOVERNED_FILE, UNGOVERNED_FILE):
        (holding / path).parent.mkdir(parents=True, exist_ok=True)
        (holding / path).write_text("# already here\n", encoding="utf-8")
    (empty / FIXTURE_PREFIX).mkdir(parents=True, exist_ok=True)
    return holding, empty


def root_named(tmp_path: Path, name: str) -> Path:
    return dict(zip(("holding", "empty"), roots(tmp_path)))[name]


def fault(problem: str) -> str:
    """The sentence between the declared restriction and the resolutions.

    The wordings differ here, so reading this slice is what lets an assertion
    say which fault was named rather than only that something was reported.
    """
    after = problem.split(f"under {FIXTURE_PREFIX}. ", 1)[1]
    return after.split("Either assign", 1)[0].strip()


def test_the_fixture_workflow_is_one_this_repository_does_not_ship(tmp_path: Path):
    """Every derivation above is load-bearing, and none of it is the shipped one.

    A fixture that happened to name a shipped stage or prefix would make the
    assertions below agree with the deployment they are meant to be
    independent of, and an empty derivation would make them vacuous.
    """
    shipped_names = [stage["name"] for stage in SHIPPED_STAGES]
    shipped_restrictions = story_coordinator.stage_restrictions(SHIPPED_STAGES)

    assert FIXTURE_RESTRICTIONS
    assert shipped_restrictions, "the shipped workflow declares no restriction"
    assert FIXTURE_STAGE not in shipped_names
    assert FIXTURE_FREE_STAGE not in shipped_names
    for _, prefix in shipped_restrictions:
        assert not FIXTURE_PREFIX.startswith(prefix)
        assert not prefix.startswith(FIXTURE_PREFIX)

    for path in (GOVERNED_FILE, UNWRITTEN_FILE):
        assert path.startswith(FIXTURE_PREFIX)
    assert not UNGOVERNED_FILE.startswith(FIXTURE_PREFIX)

    holding, empty = roots(tmp_path)
    assert (holding / GOVERNED_FILE).is_file()
    assert not (holding / UNWRITTEN_FILE).exists()
    for path in (GOVERNED_FILE, UNWRITTEN_FILE):
        assert not (empty / path).exists()


# --------------------------------------------------------------------------
# The contract: the schema declares the field, and a story carrying one parses
# --------------------------------------------------------------------------


def schema() -> dict:
    return json.loads(STORY_SCHEMA.read_text(encoding="utf-8"))


def plan_entry_schema(loaded: dict) -> dict:
    return (loaded["properties"]["technical_plan"]["properties"]
            ["likely_file_changes"]["items"])


def test_the_schema_declares_the_field_as_an_optional_string_on_a_plan_entry():
    """A string, beside `file`, `reason` and `stage`, and required by nothing.

    "Not required" is an absence, and its control is the required list itself:
    the same reading finds the three names that *are* required there, so an
    empty or renamed list cannot make this pass by holding nothing.
    """
    item = plan_entry_schema(schema())

    assert item["properties"][FIELD]["type"] == "string"
    required = item["required"]
    assert FIELD not in required
    for named in ("file", "reason", "stage"):
        assert named in required, named


def test_the_schema_states_what_the_declaration_decides_and_what_it_does_not():
    """The description is where a planner and the parser meet the contract.

    Positive readings of produced prose, each failing on its own the moment
    the description stops saying its half.
    """
    described = plan_entry_schema(schema())["properties"][FIELD]["description"]
    flat = " ".join(described.split())

    assert re.search(r"(?i)optional", flat), flat
    # What its presence decides at plan time, and the condition on it.
    assert re.search(r"(?i)without a grant", flat), flat
    assert re.search(r"(?i)already holds", flat), flat
    # What it does not promise about the run.
    assert re.search(r"(?i)no run-time check consults it|no run-time check "
                     r"reads it", flat), flat
    assert re.search(r"(?i)revert check governs", flat), flat
    # And that the text is for a reviewer rather than for a check.
    assert re.search(r"(?i)parses, matches or scores", flat), flat


def declared_artifact(declaring: str | None, file: str = GOVERNED_FILE) -> str:
    """A story artifact whose one plan entry optionally carries a declaration."""
    text = (artifact("story-900")
            + "\ntechnical_plan:\n  likely_file_changes:\n"
            + f"    - file: {file}\n"
            + f"      stage: {FIXTURE_STAGE}\n"
            + "      reason: the plan expects this\n")
    if declaring is not None:
        text += f"      {FIELD}: {declaring}\n"
    return text


def parsed_entry(text: str) -> dict:
    reading = story_coordinator.read_story(text)
    assert reading.problems == [], reading.problems
    (only,) = reading.parsed["technical_plan"]["likely_file_changes"]
    return only


def test_a_story_carrying_a_declaration_reads_it_into_the_entry_as_a_string():
    """Through `read_story`, the reader a run and plan time both use.

    Its control is the same entry without the field, which reads clean and
    carries no such key — so the key above came from the artifact rather than
    from the parser inventing one.
    """
    carried = parsed_entry(declared_artifact(DECLARED))
    assert carried[FIELD] == DECLARED
    assert isinstance(carried[FIELD], str)

    assert FIELD not in parsed_entry(declared_artifact(None))


def test_the_schema_is_what_types_it_and_a_non_string_is_refused():
    """Driven at the validator, against a structure built here.

    A parse cannot show this: the parse is schema-directed, so it reads a bare
    `42` back as the string the schema asks for. What the schema types is
    visible where a structure is validated rather than read, and the control is
    the same structure carrying a sentence, which is accepted.
    """
    loaded = schema()
    story = {
        "story": {"id": "story-900", "title": "t", "description": "d"},
        "tasks": ["do the sample work"],
        "acceptance_criteria": ["the sample behavior exists"],
        "scope": {"modify": ["src/"], "do_not_modify": ["rules/"]},
        "verification_requirements": ["confirm the sample behavior"],
        "constraints": ["preserve existing behavior"],
        "technical_plan": {"likely_file_changes": [
            entry(GOVERNED_FILE, FIXTURE_STAGE, declaring=DECLARED)]},
    }
    only = story["technical_plan"]["likely_file_changes"][0]

    assert schema_validator.validate(story, loaded) == []

    only[FIELD] = 42
    problems = schema_validator.validate(story, loaded)
    assert len(problems) == 1, problems
    assert FIELD in problems[0]

    del only[FIELD]
    assert schema_validator.validate(story, loaded) == []


# --------------------------------------------------------------------------
# Plan time: what the declaration accepts, and what it does not reach
# --------------------------------------------------------------------------


DECLARED_ENTRY = plan(entry(GOVERNED_FILE, FIXTURE_STAGE, declaring=DECLARED))
UNDECLARED_ENTRY = plan(entry(GOVERNED_FILE, FIXTURE_STAGE))


def test_a_declared_entry_naming_a_path_the_root_holds_yields_no_problem(
        tmp_path: Path):
    """The accepting case: governed, ungranted, declared, and already there.

    Its control is the test directly below — the very same story with the
    declaration removed, against the very same root — which is reported.
    """
    holding, _ = roots(tmp_path)

    assert plan_validation.assignment_problems(
        DECLARED_ENTRY, FIXTURE_STAGES, holding) == []
    assert "stage_exceptions" not in DECLARED_ENTRY


def test_the_same_entry_without_the_declaration_is_reported_as_a_modification(
        tmp_path: Path):
    """The control for the acceptance above: the declaration is the difference."""
    holding, _ = roots(tmp_path)

    (problem,) = plan_validation.assignment_problems(
        UNDECLARED_ENTRY, FIXTURE_STAGES, holding)

    assert GOVERNED_FILE in problem
    assert FIXTURE_STAGE in problem
    assert "modification" in fault(problem), problem


def test_a_declared_entry_the_root_does_not_hold_is_still_reported_as_a_creation(
        tmp_path: Path):
    """The declaration never reaches the case the ownership check refuses.

    Two ways of being absent, both refused: the governed file against a root
    that does not hold it, and a file no root here holds against the root that
    does. The control for both is the accepting case above, which differs from
    the first of them in the root alone.
    """
    holding, empty = roots(tmp_path)

    (from_empty,) = plan_validation.assignment_problems(
        DECLARED_ENTRY, FIXTURE_STAGES, empty)
    assert GOVERNED_FILE in from_empty
    assert "creation" in fault(from_empty), from_empty

    unwritten = plan(entry(UNWRITTEN_FILE, FIXTURE_STAGE, declaring=DECLARED))
    (from_holding,) = plan_validation.assignment_problems(
        unwritten, FIXTURE_STAGES, holding)
    assert UNWRITTEN_FILE in from_holding
    assert "creation" in fault(from_holding), from_holding


def test_the_creation_wording_names_no_declaration_as_a_way_out(tmp_path: Path):
    """Declaring nothing helps a file that is not there, and the message says so.

    Its control is the modification wording, which does name it, so this is a
    resolution the message withholds for this fault rather than one the module
    never states.
    """
    holding, empty = roots(tmp_path)

    (creation,) = plan_validation.assignment_problems(
        UNDECLARED_ENTRY, FIXTURE_STAGES, empty)
    (modification,) = plan_validation.assignment_problems(
        UNDECLARED_ENTRY, FIXTURE_STAGES, holding)

    assert FIELD not in creation, creation
    assert FIELD in modification, modification


def test_a_declaration_that_says_nothing_silences_nothing(tmp_path: Path):
    """An empty declaration is not a judgement, and the field's whole point is one.

    The control is the same entry carrying a sentence, which is accepted
    against the same root.
    """
    holding, _ = roots(tmp_path)

    for empty_text in ("", "   ", "\t"):
        story = plan(entry(GOVERNED_FILE, FIXTURE_STAGE, declaring=empty_text))
        assert len(plan_validation.assignment_problems(
            story, FIXTURE_STAGES, holding)) == 1, repr(empty_text)

    assert plan_validation.assignment_problems(
        DECLARED_ENTRY, FIXTURE_STAGES, holding) == []


def test_a_grant_still_silences_a_declared_and_an_undeclared_entry_alike(
        tmp_path: Path):
    """The grant short-circuits above everything, either side of the filesystem.

    Its control is the ungranted pair: undeclared is reported against both
    roots, and declared is reported against the root that lacks the file — so
    the silence here is the grant's.
    """
    holding, empty = roots(tmp_path)

    for story in (DECLARED_ENTRY, UNDECLARED_ENTRY):
        granted = with_grant(story, GOVERNED_FILE)
        for root in (holding, empty):
            assert plan_validation.assignment_problems(
                granted, FIXTURE_STAGES, root) == []

    assert plan_validation.assignment_problems(
        UNDECLARED_ENTRY, FIXTURE_STAGES, holding) != []
    assert plan_validation.assignment_problems(
        DECLARED_ENTRY, FIXTURE_STAGES, empty) != []


def test_a_declaration_on_an_entry_no_restriction_governs_changes_nothing(
        tmp_path: Path):
    """A declaration is read inside the restriction loop and nowhere else.

    Neither the ungoverned path nor the unrestricted stage is reported with the
    declaration or without it, and the control is the governed entry beside
    them, which is reported when it does not declare.
    """
    holding, _ = roots(tmp_path)

    for declaring in (DECLARED, None):
        ungoverned = plan(
            entry(UNGOVERNED_FILE, FIXTURE_STAGE, declaring=declaring),
            entry(GOVERNED_FILE, FIXTURE_FREE_STAGE, declaring=declaring),
        )
        assert plan_validation.assignment_problems(
            ungoverned, FIXTURE_STAGES, holding) == [], declaring

    assert len(plan_validation.assignment_problems(
        UNDECLARED_ENTRY, FIXTURE_STAGES, holding)) == 1


def test_the_declaration_silences_this_check_and_no_other(tmp_path: Path):
    """It is one branch of one check, not a way to quiet plan-time validation.

    A declared entry naming a module for a story number is still reported by
    the naming check, and a story whose prose over-restricts a stage is still
    reported by the strictness check. The control is the assignment check
    itself, which the same declared entry does silence.
    """
    holding, _ = roots(tmp_path)
    numbered = f"{FIXTURE_PREFIX}test_story_068_declaration.py"
    (holding / numbered).write_text("# already here\n", encoding="utf-8")
    story = plan(entry(numbered, FIXTURE_STAGE, declaring=DECLARED))
    story["constraints"] = [
        f"the {FIXTURE_STAGE} leaves {FIXTURE_PREFIX} alone entirely"]

    assert plan_validation.assignment_problems(story, FIXTURE_STAGES, holding) == []
    assert len(plan_validation.naming_problems(story)) == 1
    assert len(plan_validation.strictness_problems(story, FIXTURE_STAGES)) == 1


# --------------------------------------------------------------------------
# Plan time, one level up: through the function `l5-plan` calls
# --------------------------------------------------------------------------


def write_artifact(tmp_path: Path, text: str, name: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_artifact_problems_accepts_the_declared_artifact_against_either_reading(
        tmp_path: Path):
    """The same pair at `artifact_problems`, over artifacts written to disk.

    Both halves: the declared artifact against the root that holds the file is
    reported by nothing, and the two controls — the same artifact undeclared,
    and the same artifact against the root that lacks the file — are reported.
    """
    holding, empty = roots(tmp_path)
    declared = write_artifact(tmp_path, declared_artifact(DECLARED),
                              "story-900.yaml")
    undeclared = write_artifact(tmp_path, declared_artifact(None),
                                "story-901.yaml")

    assert plan_validation.artifact_problems(
        [declared], FIXTURE_STAGES, holding,
        HARNESS_ROOT, RENDERED_AGAINST) == {}

    (from_empty,) = plan_validation.artifact_problems(
        [declared], FIXTURE_STAGES, empty,
        HARNESS_ROOT, RENDERED_AGAINST)[declared]
    assert "creation" in fault(from_empty), from_empty

    (undeclared_problem,) = plan_validation.artifact_problems(
        [undeclared], FIXTURE_STAGES, holding,
        HARNESS_ROOT, RENDERED_AGAINST)[undeclared]
    assert "modification" in fault(undeclared_problem), undeclared_problem


# --------------------------------------------------------------------------
# The committed corpus: the two runs this rule was reasoned from
#
# Here a shipped artifact is the subject, so the shipped workflow is what the
# reconstructions are checked against. Neither committed file is edited: each
# is read from disk and reconstructed in a temporary directory.
# --------------------------------------------------------------------------


def committed(story_id: str) -> str:
    return (STORIES_DIR / f"{story_id}.yaml").read_text(encoding="utf-8")


def without_grants(text: str) -> str:
    """The artifact text with its top-level stage_exceptions block removed.

    A text edit rather than a re-serialisation: the artifacts are read by the
    harness's own parser, and dumping a parse back out would be asserting
    against a document this repository never wrote.
    """
    lines = text.splitlines(keepends=True)
    start = next(index for index, line in enumerate(lines)
                 if line.startswith("stage_exceptions:"))
    end = next(index for index in range(start + 1, len(lines))
               if lines[index].strip() and not lines[index][0].isspace())
    return "".join(lines[:start] + lines[end:])


def declaring_entry(text: str, path: str, sentence: str) -> str:
    """The same text with `sentence` declared on the entry naming `path`."""
    lines = text.splitlines(keepends=True)
    index = next(number for number, line in enumerate(lines)
                 if line.strip() == f"- file: {path}")
    indent = " " * (len(lines[index]) - len(lines[index].lstrip()) + 2)
    lines.insert(index + 1, f"{indent}{FIELD}: {sentence}\n")
    return "".join(lines)


STORY_060_FILE = "tests/test_shipped_workflow_is_valid.py"
STORY_060_DECLARATION = (
    "the workflow change this story makes reddens the assertion in this "
    "module, so reverting the edit breaks the suite")


def test_the_two_reconstructions_below_are_of_the_artifacts_they_claim():
    """The surgery above is load-bearing, so it is asserted before it is used.

    Each reconstruction has to still parse, to have lost exactly its grants,
    and — for story-060 — to have gained the declaration on the entry named.
    Without this, a reconstruction that silently lost its plan would make every
    acceptance below vacuous.
    """
    for story_id in ("story-056", "story-060"):
        text = committed(story_id)
        reading = story_coordinator.read_story(text)
        assert reading.problems == [], (story_id, reading.problems)
        assert reading.parsed.get("stage_exceptions"), story_id

        stripped = story_coordinator.read_story(without_grants(text))
        assert stripped.problems == [], (story_id, stripped.problems)
        assert "stage_exceptions" not in stripped.parsed, story_id
        assert (stripped.parsed["technical_plan"]["likely_file_changes"]
                == reading.parsed["technical_plan"]["likely_file_changes"]), story_id

    reconstructed = story_coordinator.read_story(declaring_entry(
        without_grants(committed("story-060")), STORY_060_FILE,
        STORY_060_DECLARATION))
    assert reconstructed.problems == [], reconstructed.problems
    declared = [item for item
                in reconstructed.parsed["technical_plan"]["likely_file_changes"]
                if item.get(FIELD)]
    assert [item["file"] for item in declared] == [STORY_060_FILE]
    assert declared[0][FIELD] == STORY_060_DECLARATION
    assert (HARNESS_ROOT / STORY_060_FILE).exists()


def test_story_060s_plan_with_the_declaration_in_place_of_its_grant_is_accepted(
        tmp_path: Path):
    """The run this story was reasoned from, replanned under the new rule.

    story-060 was forced into a grant for an edit the revert check went on to
    permit. Reconstructed with the declaration instead — its grant gone — the
    plan is reported by nothing against a root that holds the file. The control
    is the same reconstruction carrying neither, directly below.
    """
    reconstructed = write_artifact(
        tmp_path,
        declaring_entry(without_grants(committed("story-060")),
                        STORY_060_FILE, STORY_060_DECLARATION),
        "story-960.yaml")

    assert plan_validation.artifact_problems(
        [reconstructed], SHIPPED_STAGES, HARNESS_ROOT,
        HARNESS_ROOT, RENDERED_AGAINST) == {}


def test_the_same_plan_carrying_neither_grant_nor_declaration_is_reported(
        tmp_path: Path):
    """The control for the acceptance above: the declaration is the difference.

    Reported at that entry, with the modification wording, because the file is
    one this repository holds — which is the category the declaration is for
    and the reason the grant was reached for instead.
    """
    stripped = write_artifact(tmp_path, without_grants(committed("story-060")),
                              "story-961.yaml")

    (problem,) = plan_validation.artifact_problems(
        [stripped], SHIPPED_STAGES, HARNESS_ROOT,
        HARNESS_ROOT, RENDERED_AGAINST)[stripped]

    assert STORY_060_FILE in problem
    assert "describes a modification" in problem, problem


def test_story_056s_artifact_with_its_grants_stripped_is_still_reported(
        tmp_path: Path):
    """The case nothing about changed: no entry there declares anything.

    story-056's implementation lived beneath the governed prefix and reverting
    it left the suite green, which is what its grants are for. Stripped of
    them and declaring nothing, its governed entries are reported exactly as
    they were. The control is the committed artifact itself, which is reported
    by nothing, so this is the grants' removal rather than the check reporting
    whatever it is handed.
    """
    text = committed("story-056")
    committed_path = write_artifact(tmp_path, text, "story-956.yaml")
    stripped = write_artifact(tmp_path, without_grants(text), "story-957.yaml")

    assert plan_validation.artifact_problems(
        [committed_path], SHIPPED_STAGES, HARNESS_ROOT,
        HARNESS_ROOT, RENDERED_AGAINST) == {}

    story = story_coordinator.read_story(without_grants(text)).parsed
    restricted = {stage for stage, _ in
                  story_coordinator.stage_restrictions(SHIPPED_STAGES)}
    governed = [item for item
                in story["technical_plan"]["likely_file_changes"]
                if item["stage"] in restricted
                and any(item["file"].startswith(prefix) for stage, prefix
                        in story_coordinator.stage_restrictions(SHIPPED_STAGES)
                        if stage == item["stage"])]
    assert governed, "story-056 no longer carries the entries this is about"
    assert not any(item.get(FIELD) for item in governed)

    problems = plan_validation.artifact_problems(
        [stripped], SHIPPED_STAGES, HARNESS_ROOT,
        HARNESS_ROOT, RENDERED_AGAINST)[stripped]
    assert len(problems) == len(governed)
    for named, problem in zip(governed, problems):
        assert named["file"] in problem
        assert "describes a modification" in problem, problem


# --------------------------------------------------------------------------
# Containment: the field name lives in one module and one contract
# --------------------------------------------------------------------------


def files_naming_the_field(directory: Path, relative_to: Path = None) -> list[str]:
    """Every readable file beneath `directory` whose text holds the field name.

    Reported relative to `relative_to`, which defaults to `directory`'s parent,
    so a scan over a copy of a directory answers in the same names as a scan
    over the directory itself.
    """
    base = relative_to or directory.parent
    found = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if FIELD in text:
            found.append(str(path.relative_to(base)))
    return found


def test_the_field_name_appears_in_one_orchestration_module_and_no_other(
        tmp_path: Path):
    """The plan-time check is the only code that knows the field exists.

    The control is the same scan over a copy of that directory with the name
    planted in a module that does not hold it, which the scan reports — so the
    short answer above it is a measured absence rather than a scan that stopped
    seeing. The plant goes into the copy: a control has no business editing the
    tree it is a control for, however carefully it puts it back.
    """
    orchestration = HARNESS_ROOT / "orchestration"

    assert files_naming_the_field(orchestration) == [
        "orchestration/plan_validation.py"]

    copied = tmp_path / "orchestration"
    shutil.copytree(orchestration, copied,
                    ignore=shutil.ignore_patterns("__pycache__"))
    planted = copied / "story_coordinator.py"
    planted.write_text(planted.read_text(encoding="utf-8") + f"\n# {FIELD}\n",
                       encoding="utf-8")

    assert sorted(files_naming_the_field(copied)) == [
        "orchestration/plan_validation.py",
        "orchestration/story_coordinator.py",
    ]


def test_no_workflow_and_no_rule_file_names_the_field():
    """No run-time artifact is told the field exists, workflow and rules alike.

    Their control is the directory that does hold it: the same scan over
    `schemas/` and `prompts/` reports the contract and the template, so a scan
    that had stopped reading files would fail there rather than pass here.
    """
    for directory in ("workflows", "rules"):
        assert files_naming_the_field(HARNESS_ROOT / directory) == [], directory

    assert files_naming_the_field(HARNESS_ROOT / "schemas") == [
        "schemas/story.schema.json"]
    assert files_naming_the_field(HARNESS_ROOT / "prompts") == [
        "prompts/planner.md"]
    assert str(Path(__file__).relative_to(HARNESS_ROOT)) in files_naming_the_field(
        HARNESS_ROOT / "tests")


# --------------------------------------------------------------------------
# Run time: nothing consults the declaration
#
# Driven rather than inspected. The target repository carries a real pytest
# suite, so "was this edit permitted" is answered by the revert check actually
# running, and the story the run executes carries the declaration on the very
# path the stage then edits.
# --------------------------------------------------------------------------


RUN_STAGES = RUN_WORKFLOW["stages"]
(RUN_STAGE, RUN_PREFIX), = story_coordinator.stage_restrictions(RUN_STAGES)

#: The two governed paths these runs use: one the target already holds and the
#: unforced edit lands in, and one no commit holds, which the stage creates.
RUN_MODIFIED = f"{RUN_PREFIX}test_app.py"
RUN_CREATED = f"{RUN_PREFIX}test_a_further_module.py"

FURTHER_MODULE = '''\
def test_a_further_module_is_still_arithmetic():
    assert 5 + 6 == 11
'''

RUN_DECLARATION = ("the change to the module in this story is what forces "
                   "this edit, so reverting it breaks the suite")


def declared_plan(path: str, declaring: bool) -> str:
    """A technical_plan block for the story the runs below execute."""
    block = ("\ntechnical_plan:\n  likely_file_changes:\n"
             f"    - file: {path}\n"
             f"      stage: {RUN_STAGE}\n"
             "      reason: the plan expects this\n")
    if declaring:
        block += f"      {FIELD}: {RUN_DECLARATION}\n"
    return block


def creates_a_further_module(root: Path) -> dict:
    """The module changed, and a new file created beneath the governed prefix."""
    write(root / "src" / "app.py", APP_ADDITIVE)
    write(root / RUN_CREATED, FURTHER_MODULE)
    return {"modified": ["src/app.py"], "created": [RUN_CREATED], "deleted": []}


def escalation_reason(target_root: Path) -> str:
    """The escalation's own sentence, read through the coordinator's reader."""
    reason = story_coordinator.escalation_reason(run_dir_of(target_root))
    assert reason, "the run did not escalate, so there is no reason to read"
    return reason


def story_of(target_root: Path) -> dict:
    reading = story_coordinator.read_story(
        (target_root / ".harness" / "stories" / "story-001.yaml").read_text(
            encoding="utf-8"))
    assert reading.problems == [], reading.problems
    return reading.parsed


@pytest.mark.parametrize("declaring", [True, False], ids=["declared",
                                                          "undeclared"])
def test_an_unforced_edit_to_a_declared_path_is_reverted_and_escalates(
        target: Path, harness_root: Path, clone_calls, declaring: bool):
    """The revert check governs a declared path exactly as an undeclared one.

    Both rows assert the whole outcome — the run escalates, the escalation
    names the path, and the path was put to the check — so the pair is a
    comparison of two complete answers rather than of one answer against a
    silence.

    The control that the declaration is real, and not an inert string that
    could have been misspelled without anything noticing, is the last two
    lines: at plan time this very story is accepted when it declares and
    reported when it does not.
    """
    append_to_story(target, declared_plan(RUN_MODIFIED, declaring))

    # Read before the run, because plan time is before the run: existence is
    # resolved against the tree a planner writes against, not against whatever
    # the stage leaves behind.
    accepted = plan_validation.assignment_problems(
        story_of(target), RUN_STAGES, target)
    assert (accepted == []) is declaring, accepted

    code, _ = run(target, harness_root, {RUN_STAGE: added_coverage})

    assert code != 0
    reason = escalation_reason(target)
    assert RUN_MODIFIED in reason
    assert "reverted" in reason
    assert RUN_MODIFIED in {path for call in clone_calls for path in call}


@pytest.mark.parametrize("declaring", [True, False], ids=["declared",
                                                          "undeclared"])
def test_a_created_file_beneath_the_prefix_still_escalates_when_declared(
        target: Path, harness_root: Path, declaring: bool):
    """The ownership check refuses a creation, declaration or not.

    Both rows assert the same complete outcome, and the plan-time control is
    the last line: a declaration naming a path the target lacks is refused at
    plan time too, so neither half of the harness accepts this entry.
    """
    append_to_story(target, declared_plan(RUN_CREATED, declaring))

    # Read before the run, for the same reason: the stage below writes this very
    # path, so afterwards the root holds a file the planner's root did not.
    assert plan_validation.assignment_problems(
        story_of(target), RUN_STAGES, target) != []

    code, _ = run(target, harness_root, {RUN_STAGE: creates_a_further_module})

    assert code != 0
    reason = escalation_reason(target)
    assert RUN_CREATED in reason
    assert "created" in reason


# --------------------------------------------------------------------------
# The prose the story corrects
#
# `.harness/docs/ARCHITECTURE.md` is the documenter's and is not read here.
# --------------------------------------------------------------------------


#: The sentence the docstring and the architecture document both carried, which
#: five consecutive runs contradict. Planted below as the control for each
#: absence assertion, so "this is no longer stated" is a reading that can fail.
RETIRED_ABSOLUTE = ("Two run-time checks act on a stage beneath a governed "
                    "prefix, and between them they leave no version of that "
                    "entry that a run can accept.")

#: What the retired premise reads like wherever it is stated: a claim that
#: between the two run-time checks nothing ungranted survives.
STATES_THE_ABSOLUTE = re.compile(
    r"(?i)(?:leave|leaves|there is) no version of|no ungranted governed entry "
    r"a run can accept")


def flowed(text: str) -> str:
    """Prose with its line wrapping removed.

    A sentence in a hand-wrapped document is a sentence wherever the line
    breaks fall, so searching for one must not depend on the wrapping.
    """
    return " ".join(text.split())


def modification_comment() -> str:
    """The comment block introducing the modification fault, and only that.

    Read as the run of `#:` lines immediately above the constant, so an
    assertion about this comment cannot be satisfied by prose somewhere else in
    the module.
    """
    lines = PLAN_VALIDATION.read_text(encoding="utf-8").splitlines()
    index = next(number for number, line in enumerate(lines)
                 if line.startswith("_MODIFICATION = "))
    block = []
    while index and lines[index - 1].lstrip().startswith("#"):
        index -= 1
        block.insert(0, lines[index].lstrip("# "))
    assert block, "the modification fault carries no comment to read"
    return flowed(" ".join(block))


def fourth_check_section() -> str:
    """The docstring section this story corrects, and only that section.

    Sliced at its own heading and the next one, for the same reason: a
    correction asserted against the whole docstring would be satisfied by any
    other section happening to say the words.
    """
    doc = plan_validation.__doc__
    after = doc.split("What the fourth check is", 1)[1]
    return flowed(after.split("What the fifth check is", 1)[0])


@pytest.mark.parametrize("named,reading", [
    ("the module docstring's fourth-check section", fourth_check_section),
    ("the modification fault's comment", modification_comment),
])
def test_the_corrected_prose_no_longer_states_the_removed_absolute(
        named: str, reading):
    """Neither passage claims the two run-time checks leave nothing acceptable.

    Its control is the same reading over the same text with the retired
    sentence planted back into it, which the same search reports — so this is
    an absence that can fail rather than a pattern that matches nothing
    anywhere.
    """
    text = reading()

    assert not STATES_THE_ABSOLUTE.search(text), text
    assert STATES_THE_ABSOLUTE.search(f"{text} {RETIRED_ABSOLUTE}")


@pytest.mark.parametrize("named,reading", [
    ("the module docstring's fourth-check section", fourth_check_section),
    ("the modification fault's comment", modification_comment),
])
def test_both_passages_name_the_forced_adaptation_beside_the_two_they_had(
        named: str, reading):
    """Three categories, and what each of the two run-time checks does with it.

    Positive readings of produced prose, each failing on its own the moment
    the passage stops saying its half.
    """
    text = reading()

    assert re.search(r"(?i)implementation change", text), (named, text)
    assert re.search(r"(?i)comment-only", text), (named, text)
    assert re.search(r"(?i)forced", text), (named, text)
    assert re.search(r"(?i)revert check", text), (named, text)
    assert FIELD in text, (named, text)


def test_the_docstring_says_plan_time_cannot_compute_which_category_it_is():
    """The reason the judgement is the planner's rather than a check's."""
    section = fourth_check_section()

    assert re.search(r"(?i)nothing at plan time can compute|plan time cannot "
                     r"compute", section), section
    assert re.search(r"(?i)does not exist yet", section), section
    # And the declaration is stated as where the planner says it, beside the
    # grant it is not interchangeable with.
    assert re.search(r"(?i)grant", section), section
    assert re.search(r"(?i)no run-time check is weakened, anticipated or told",
                     section), section


def test_the_planner_prompt_no_longer_says_a_grant_is_needed_either_way():
    """The instruction that produced the perverse incentive is gone.

    Its control is the same search over the same text with the retired
    sentence planted back in, which finds it.
    """
    prompt = flowed(PLANNER_PROMPT.read_text(encoding="utf-8"))
    retired = ("it needs one whether or not the file is already in the target "
               "repository")

    assert not re.search(r"(?i)whether or not the file is already in the "
                         r"target", prompt), prompt
    assert re.search(r"(?i)whether or not the file is already in the target",
                     f"{prompt} {retired}")


def test_the_planner_prompt_distinguishes_the_declaration_from_the_grant():
    """By what each does to the revert check, which is the whole difference.

    Positive readings of produced prose: the two instruments, the condition on
    each, and the run-time consequence that separates them.
    """
    prompt = flowed(PLANNER_PROMPT.read_text(encoding="utf-8"))

    assert FIELD in prompt
    # The creation case, unchanged: reassign or grant, and nothing else.
    assert re.search(r"(?i)if the file is not in the target repository", prompt)
    assert re.search(r"(?i)reassign the file to a stage that may own it", prompt)
    assert re.search(r"(?i)stage_exceptions grant naming it", prompt)
    # The modification case, split by whether the edit was forced.
    assert re.search(r"(?i)needs a grant", prompt)
    assert re.search(r"(?i)needs no grant", prompt)
    assert re.search(r"(?i)forced", prompt)
    # And the difference between them, stated at the revert check.
    assert re.search(r"(?i)exempts the path from the revert check", prompt)
    assert re.search(r"(?i)leaves the revert check governing", prompt)


def test_the_planner_prompt_still_names_no_stage_and_no_restricted_prefix():
    """The template's standing promise, beside a scan that can see a violation.

    This story rewrote three of its paragraphs, and the promise is the kind a
    rewrite breaks silently.
    """
    prompt = PLANNER_PROMPT.read_text(encoding="utf-8")
    shipped_names = [stage["name"] for stage in SHIPPED_STAGES]

    for name in shipped_names:
        assert not re.search(rf"\b{re.escape(name)}\b", prompt), name
    for _, prefix in story_coordinator.stage_restrictions(SHIPPED_STAGES):
        assert prefix not in prompt, prefix

    for planted in shipped_names:
        assert re.search(rf"\b{re.escape(planted)}\b", f"{prompt}\n{planted}\n")
    for _, prefix in story_coordinator.stage_restrictions(SHIPPED_STAGES):
        assert prefix in f"{prompt}\n{prefix}\n"
