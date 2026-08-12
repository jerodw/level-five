"""Independent validation for story-025: plan time validates the artifact it
just wrote.

The subject is again *a script that outlives its interactive session*, so most
of what is asserted here is asserted end-to-end: a throwaway target repository
is built under tmp_path, a stub `claude` is put on PATH, and the real
`scripts/l5-plan` is run against it as a subprocess. The stub reads no prompt
and runs no git, so a commit that exists afterwards was made by the script and
a commit that does not exist was refused by it.

The fixture, the stub and the runner are imported from
`test_story_023_validation` rather than copied. That file built them for the
script this story changes, and one home for one fact is why the change is
visible here at all: if the process model regresses, both files go red.

Every assertion here that claims an absence carries a control showing the same
check reporting the violation it exists to catch:

  * "a failing artifact is not committed" sits beside the same fixture given a
    valid artifact, where HEAD and the remote both move, and beside the
    *pre-story* script read out of git, which commits the very same failing
    artifact;
  * "the artifact was neither deleted nor rewritten" is a byte comparison
    against what the stub wrote, and sits beside a control showing that
    comparison reporting a one-byte edit;
  * "plan_validation names no stage and no restricted prefix" sits beside the
    same scanner over a copy of the module with each literal planted in it,
    and beside a run of strictness_problems against a synthetic workflow,
    which reports the synthetic pair and stays silent about the real one;
  * "no second parser and no second schema validator was introduced" sits
    beside the same scanner over a copy of the module with each call planted;
  * "l5-run's pre-flight created no run directory, no state.json, no log and
    no branch" sits beside the same readers run after those very things are
    created by hand;
  * "l5-run refuses what it refused before" is a comparison against the
    pre-story coordinator, read at this story's baseline and run as the same
    command over the same repository.

The baseline for anything read out of git is `conftest.story_commit_range`,
never HEAD and never the working tree against the repository root: the
coordinator commits the tree at the end of a successful run, so those go
vacuously green the moment this story commits.

No stage name and no restricted path prefix is written as a literal in the
places where this file checks that the *harness* writes none: both are read
off the loaded workflow definition, the same way the module under test reads
them.

No model is invoked anywhere in this file.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (BASELINE, NothingToCompareAgainst, load_script,
                      repository_file_at, story_commit_range)
from test_story_023_validation import (
    ARTIFACT,
    Planning,
    artifact,
    bare_remote,
    committed_paths,
    make_planning,
    python_code,
    remote_refs,
    run_plan,
    writes,
)

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "orchestration"))

import harness_config  # noqa: E402
import plan_validation  # noqa: E402
import story_coordinator  # noqa: E402

L5_PLAN = HARNESS_ROOT / "scripts" / "l5-plan"
L5_RUN = HARNESS_ROOT / "scripts" / "l5-run"
PLAN_VALIDATION = HARNESS_ROOT / "orchestration" / "plan_validation.py"
PLANNER_PROMPT = HARNESS_ROOT / "prompts" / "planner.md"
VALIDATION_FILE = Path(__file__).resolve()

# --------------------------------------------------------------------------
# Everything about the workflow is read off the workflow, here as in the
# module under test. A literal stage name in this file would make the checks
# below agree with a copy of the workflow rather than with the workflow.
# --------------------------------------------------------------------------

WORKFLOW = harness_config.load_workflow(HARNESS_ROOT, "story-workflow")
STAGES = WORKFLOW["stages"]
STAGE_NAMES = [stage["name"] for stage in STAGES]
RESTRICTIONS = story_coordinator.stage_restrictions(STAGES)
#: The stage/prefix pair the strictness check is about, and a stage the
#: workflow defines but never restricted, which is what makes an over-broad
#: stage_exceptions grant a grant of nothing.
RESTRICTED_STAGE, RESTRICTED_PREFIX = RESTRICTIONS[0]
UNRESTRICTED_STAGE = next(
    name for name in STAGE_NAMES if name not in {s for s, _ in RESTRICTIONS}
)
UNDEFINED_STAGE = "cartographer"


def test_the_workflow_this_file_reads_still_has_something_to_say():
    """The derivations above are load-bearing; an empty one would go green."""
    assert RESTRICTIONS, "the workflow declares no may_not_create restriction"
    assert UNDEFINED_STAGE not in STAGE_NAMES
    assert UNRESTRICTED_STAGE in STAGE_NAMES


# --------------------------------------------------------------------------
# The artifacts. One valid, and one per class of defect.
# --------------------------------------------------------------------------


def exceptions_block(stage: str, create: str) -> str:
    return (
        "\nstage_exceptions:\n"
        f"  - stage: {stage}\n"
        f"    create: {create}\n"
        "    reason: the story's own deliverable needs it\n"
    )


def strict_artifact(story_id: str = "story-900") -> str:
    """A story that states the workflow's restriction more strictly than it is.

    The over-strict sentence lands in `constraints`, which is the last array
    the template writes, and is the second entry there.
    """
    return artifact(story_id) + (
        f"  - the {RESTRICTED_STAGE} leaves {RESTRICTED_PREFIX} alone entirely\n"
    )


#: The four defects l5-plan must report, and what each is defective about.
#: Keyed by name so the parametrised tests below say which one failed.
DEFECTS = {
    "unparseable": "this: is: not: a story\n\t- ?\n",
    "schema": ARTIFACT.format(story_id="story-900", title="No tasks at all").replace(
        "tasks:\n  - do the sample work\n", ""
    ),
    "undefined-stage": artifact("story-900")
    + exceptions_block(UNDEFINED_STAGE, RESTRICTED_PREFIX),
    "unrestricted-prefix": artifact("story-900")
    + exceptions_block(UNRESTRICTED_STAGE, RESTRICTED_PREFIX),
}

#: The defects l5-run also refuses. The strictness class is plan-time only by
#: design, so it is absent here and is asserted absent from pre-flight below.
PRE_FLIGHT_DEFECTS = dict(DEFECTS)

DEFECTS["strictness"] = strict_artifact()


def test_every_defect_this_file_uses_is_actually_defective():
    """Otherwise a refusal test could pass on an artifact nothing objects to."""
    for name, text in DEFECTS.items():
        reading = story_coordinator.read_story(text)
        found = list(reading.problems)
        if reading.parsed is not None:
            found += story_coordinator.stage_exception_problems(reading.parsed, STAGES)
            found += plan_validation.strictness_problems(reading.parsed, STAGES)
        assert found, f"the {name} artifact has nothing wrong with it"
    valid = story_coordinator.read_story(artifact())
    assert valid.problems == []
    assert story_coordinator.stage_exception_problems(valid.parsed, STAGES) == []
    assert plan_validation.strictness_problems(valid.parsed, STAGES) == []


# --------------------------------------------------------------------------
# The repository, the stub, and the pre-story harness.
# --------------------------------------------------------------------------


@pytest.fixture
def planning(tmp_path: Path) -> Planning:
    """A target repository with a stub `claude` on PATH and a bare origin."""
    planning = make_planning(tmp_path)
    planning.remote = bare_remote(tmp_path, planning, upstream=True)
    return planning


def baseline() -> str:
    return story_commit_range(VALIDATION_FILE).baseline


def show(path: str) -> str:
    """One file as it was before this story.

    story-029 folded this module's private `git show` into
    `conftest.repository_file_at`, which resolves the same baseline this
    already resolved for itself. Subject and strictness unchanged.
    """
    return repository_file_at(path, validation_file=VALIDATION_FILE,
                              bound=BASELINE, repo=HARNESS_ROOT)


def pre_story_harness(tmp_path: Path) -> Path:
    """A harness root running this story's scripts and coordinator as they were.

    The scripts and `story_coordinator.py` are written out of git at the
    story's baseline; every other orchestration module, and the prompts,
    schemas, workflows and rules, are symlinks to the real ones, so the old
    code loads the same definitions the new code does and the comparison is
    about this story's change rather than about two different workflows.
    """
    root = tmp_path / "pre-story-harness"
    (root / "scripts").mkdir(parents=True)
    (root / "orchestration").mkdir()
    for name in ("prompts", "schemas", "workflows", "rules"):
        os.symlink(HARNESS_ROOT / name, root / name)
    for module in sorted((HARNESS_ROOT / "orchestration").glob("*.py")):
        if module.name in ("story_coordinator.py", "plan_validation.py"):
            continue
        os.symlink(module, root / "orchestration" / module.name)
    (root / "orchestration" / "story_coordinator.py").write_text(
        show("orchestration/story_coordinator.py"), encoding="utf-8")
    for script in ("l5-plan", "l5-run"):
        written = root / "scripts" / script
        written.write_text(show(f"scripts/{script}"), encoding="utf-8")
        written.chmod(0o755)
    return root


def run_script(script: Path, planning: Planning, *args: str,
               **stub) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=planning.root, env=planning.env(**stub),
        capture_output=True, text=True,
    )


def install(planning: Planning, story_id: str, text: str) -> Path:
    """Write and commit an artifact, as a developer who planned it earlier would."""
    path = planning.stories_dir / f"{story_id}.yaml"
    path.write_text(text, encoding="utf-8")
    planning.git("add", "-A")
    planning.git("commit", "-q", "-m", f"install {story_id}")
    # Setup, not subject: since story-030 a run whose base differs from its
    # remote is refused above the checks these tests are about, and a developer
    # who planned an artifact earlier would have had it pushed — l5-plan pushes
    # it. Without this the refusal these tests read would be the base one.
    planning.git("push", "-q", "origin", "main")
    return path


# --------------------------------------------------------------------------
# The composition: plan_validation reports through the coordinator's readers.
# --------------------------------------------------------------------------


def test_schema_conformance_is_reported_by_calling_read_story(monkeypatch,
                                                              tmp_path: Path):
    """The parse is the coordinator's, and its problems are passed through whole.

    Recorded rather than inferred: `read_story` is wrapped, and the wrapper
    sees the artifact's own text and returns the real reading, so the problems
    reported are the ones the coordinator produced.
    """
    calls = []
    real = story_coordinator.read_story

    def recording(text, harness_root=None):
        calls.append(text)
        return real(text, harness_root)

    monkeypatch.setattr(story_coordinator, "read_story", recording)
    path = tmp_path / "story-900.yaml"
    path.write_text(DEFECTS["schema"], encoding="utf-8")

    problems = plan_validation.artifact_problems([path], STAGES)

    assert calls == [DEFECTS["schema"]]
    assert problems[path] == real(DEFECTS["schema"]).problems
    assert problems[path], "the reading had nothing to report"


def test_an_artifact_read_story_rejects_is_not_carried_into_the_later_checks(
        monkeypatch, tmp_path: Path):
    """A parse the coordinator refused has no shape the later checks may assume."""
    seen = []
    monkeypatch.setattr(story_coordinator, "stage_exception_problems",
                        lambda *a: seen.append("exceptions") or [])
    monkeypatch.setattr(plan_validation, "strictness_problems",
                        lambda *a: seen.append("strictness") or [])
    path = tmp_path / "story-900.yaml"
    path.write_text(DEFECTS["unparseable"], encoding="utf-8")

    assert plan_validation.artifact_problems([path], STAGES)[path]
    assert seen == []

    # Control: a well-formed artifact does reach both, so the emptiness above
    # is the guard rather than the recorder never being wired up.
    path.write_text(artifact(), encoding="utf-8")
    assert plan_validation.artifact_problems([path], STAGES) == {}
    assert seen == ["exceptions", "strictness"]


def test_artifact_problems_holds_only_the_artifacts_with_problems(tmp_path: Path):
    good, bad = tmp_path / "story-900.yaml", tmp_path / "story-901.yaml"
    good.write_text(artifact("story-900"), encoding="utf-8")
    bad.write_text(DEFECTS["schema"], encoding="utf-8")

    problems = plan_validation.artifact_problems([good, bad], STAGES)

    assert list(problems) == [bad]
    assert plan_validation.artifact_problems([good], STAGES) == {}


#: A second reader: any route to a parse or a schema validation that does not
#: go through the coordinator's `read_story`.
SECOND_READER = (
    re.compile(r"\bstory_parser\b"),
    re.compile(r"\bschema_validator\b"),
    re.compile(r"\byaml\.\w*load\w*\("),
    re.compile(r"\bjson\.loads?\("),
)


def second_readers(source: str) -> list[str]:
    return [match.group(0) for pattern in SECOND_READER
            for match in pattern.finditer(source)]


def test_plan_validation_introduces_no_second_parser_or_validator():
    """The divergence story-005 removed is not reintroduced at plan time.

    The scan is over the code with its prose stripped: the module's docstring
    names `schema_validator` while saying it does *not* call it, and a
    sentence about a module is not a call to it. The controls plant each route
    in code, so the stripping cannot hide a real one.
    """
    source = python_code(PLAN_VALIDATION.read_text(encoding="utf-8"))
    assert second_readers(source) == []
    for planted in (
        "parsed = story_parser.parse(text, schema)",
        "problems = schema_validator.validate(parsed, schema)",
        "parsed = yaml.safe_load(text)",
        "parsed = json.loads(text)",
    ):
        assert second_readers(f"{source}\n{planted}\n") != [], planted


def test_the_only_caller_of_the_parser_and_the_validator_is_still_the_coordinator():
    """Asserted by search over the harness, and unchanged from the baseline."""
    def callers(read) -> dict:
        found = {}
        for directory in ("orchestration", "scripts"):
            for path in sorted((HARNESS_ROOT / directory).iterdir()):
                if not path.is_file() or path.name == "__init__.py":
                    continue
                relative = str(path.relative_to(HARNESS_ROOT))
                hits = [
                    line.strip() for line in read(relative).splitlines()
                    if re.search(r"\b(?:story_parser\.parse|schema_validator\.validate)\(",
                                 line)
                ]
                if hits:
                    found[relative] = hits
        return found

    now = callers(lambda relative: (HARNESS_ROOT / relative).read_text(
        encoding="utf-8", errors="replace"))
    assert set(now) == {"orchestration/story_coordinator.py"}

    def at_baseline(relative: str) -> str:
        try:
            return show(relative)
        except NothingToCompareAgainst:
            return ""

    assert now == callers(at_baseline)


# --------------------------------------------------------------------------
# The strictness check, and where it reads its two halves from.
# --------------------------------------------------------------------------


def test_a_clause_naming_a_stage_and_its_restricted_prefix_is_reported():
    story = story_coordinator.read_story(strict_artifact()).parsed
    problems = plan_validation.strictness_problems(story, STAGES)

    assert len(problems) == 1
    assert RESTRICTED_STAGE in problems[0] and RESTRICTED_PREFIX in problems[0]
    assert problems[0].startswith("$.constraints[1]:")


@pytest.mark.parametrize("clause", [
    "the {stage} does not create files under {prefix}",
    "no file under {prefix} is created by the {stage}",
    "the {stage} adds nothing under {prefix}",
    "{prefix} gains no new file from the {stage}",
])
def test_the_same_pairing_scoped_to_creation_is_not_reported(clause: str):
    """The workflow's own rule, restated, is not a stricter version of it."""
    entry = clause.format(stage=RESTRICTED_STAGE, prefix=RESTRICTED_PREFIX)
    assert plan_validation.strictness_problems({"constraints": [entry]}, STAGES) == []
    # Control: the same sentence with its creation word removed is reported,
    # so the silence above is the creation word and not the sentence being
    # invisible to the check.
    stripped = re.sub(
        r"\b(?:creates?|created|creating|adds?|new)\b", "touches", entry)
    assert plan_validation.strictness_problems({"constraints": [stripped]}, STAGES)


def test_a_clause_is_the_unit_so_a_creation_word_elsewhere_does_not_excuse_it():
    """The historical instances confirm a creation in one clause and
    over-restrict in the next; an entry-level test would let every one pass."""
    entry = (
        f"{RESTRICTED_PREFIX} is created by the {UNRESTRICTED_STAGE}, and the "
        f"{RESTRICTED_STAGE} does not touch {RESTRICTED_PREFIX}"
    )
    problems = plan_validation.strictness_problems({"constraints": [entry]}, STAGES)

    assert len(problems) == 1
    assert "does not touch" in problems[0]


@pytest.mark.parametrize("field",
                         ["acceptance_criteria", "verification_requirements",
                          "constraints"])
def test_all_three_free_text_arrays_are_scanned(field: str):
    entry = f"the {RESTRICTED_STAGE} leaves {RESTRICTED_PREFIX} alone"
    problems = plan_validation.strictness_problems({field: [entry]}, STAGES)
    assert len(problems) == 1
    assert f"$.{field}[0]" in problems[0]


def test_an_entry_saying_the_same_wrong_thing_twice_is_reported_once():
    entry = (f"the {RESTRICTED_STAGE} leaves {RESTRICTED_PREFIX} alone; the "
             f"{RESTRICTED_STAGE} never edits {RESTRICTED_PREFIX}")
    assert len(plan_validation.strictness_problems({"constraints": [entry]},
                                                   STAGES)) == 1


def test_both_halves_of_the_match_come_off_the_loaded_workflow():
    """Run against a workflow that restricts something else entirely.

    The synthetic pair is reported and the real one is not, which is only
    possible if the check reads the definition it is handed.
    """
    synthetic = [{"name": "cartographer", "may_not_create": ["atlas/"]},
                 {"name": "surveyor"}]
    entry = "the cartographer leaves atlas/ alone"
    real = f"the {RESTRICTED_STAGE} leaves {RESTRICTED_PREFIX} alone"

    assert len(plan_validation.strictness_problems({"constraints": [entry]},
                                                   synthetic)) == 1
    assert plan_validation.strictness_problems({"constraints": [real]},
                                               synthetic) == []
    assert plan_validation.strictness_problems({"constraints": [entry]}, STAGES) == []


def test_a_workflow_that_restricts_nothing_reports_nothing():
    stages = [{"name": name} for name in STAGE_NAMES]
    entry = f"the {RESTRICTED_STAGE} leaves {RESTRICTED_PREFIX} alone"
    assert plan_validation.strictness_problems({"constraints": [entry]}, stages) == []


def test_the_module_names_no_stage_and_no_restricted_prefix():
    """Searched for, not inspected — docstring and comments included.

    The control plants each literal into a copy of the module and shows the
    same scanner reporting it, so a clean scan is the module being clean
    rather than the scanner looking for the wrong thing.
    """
    source = PLAN_VALIDATION.read_text(encoding="utf-8")
    literals = STAGE_NAMES + [prefix for _, prefix in RESTRICTIONS]
    found = [literal for literal in literals
             if re.search(re.escape(literal), source, re.IGNORECASE)]
    assert found == []
    for literal in literals:
        planted = f'{source}\nSTAGE = "{literal}"\n'
        assert [x for x in literals
                if re.search(re.escape(x), planted, re.IGNORECASE)] != []


# --------------------------------------------------------------------------
# End to end: a failing artifact is reported, and nothing is committed.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("defect", sorted(DEFECTS))
def test_a_failing_artifact_is_reported_and_never_committed(defect: str,
                                                            planning: Planning):
    """Exit non-zero, HEAD where it was, the remote where it was, the artifact
    still on disk byte for byte as the stub wrote it."""
    text = DEFECTS[defect]
    before, refs_before = planning.head(), remote_refs(planning.remote)

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", text)))

    assert result.returncode != 0, result.stdout
    assert planning.head() == before
    assert remote_refs(planning.remote) == refs_before
    written = planning.stories_dir / "story-900.yaml"
    assert written.is_file()
    assert written.read_text(encoding="utf-8") == text
    assert "?? .harness/stories/story-900.yaml" in planning.status()
    assert "story-900.yaml" in result.stderr
    assert "committed nothing" in result.stdout


def test_the_control_for_every_refusal_above_is_the_valid_artifact(
        planning: Planning):
    """The same fixture, the same stub, an artifact nothing objects to."""
    before, refs_before = planning.head(), remote_refs(planning.remote)

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", artifact())))

    assert result.returncode == 0, result.stderr
    assert planning.head() != before
    assert remote_refs(planning.remote) != refs_before
    assert committed_paths(planning.root) == [".harness/stories/story-900.yaml"]
    assert planning.status() == ""


def test_when_one_of_several_artifacts_fails_none_of_them_is_committed(
        planning: Planning):
    """One commit for the session's artifacts, or no commit at all."""
    before, refs_before = planning.head(), remote_refs(planning.remote)
    good, bad = artifact("story-903"), DEFECTS["schema"]

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-903.yaml", good),
        (".harness/stories/story-904.yaml", bad),
    ))

    assert result.returncode != 0, result.stdout
    assert planning.head() == before
    assert remote_refs(planning.remote) == refs_before
    for name, text in (("story-903", good), ("story-904", bad)):
        written = planning.stories_dir / f"{name}.yaml"
        assert written.read_text(encoding="utf-8") == text
        assert f"?? .harness/stories/{name}.yaml" in planning.status()
    assert "story-904.yaml" in result.stderr

    # Control: the same two artifacts with the second one valid is one commit
    # holding both, so "none committed" is the refusal and not the fixture.
    planning.git("rm", "-q", "-f", "--ignore-unmatch",
                 ".harness/stories/story-903.yaml")
    for name in ("story-903", "story-904"):
        (planning.stories_dir / f"{name}.yaml").unlink()
    control = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-903.yaml", good),
        (".harness/stories/story-904.yaml", artifact("story-904")),
    ))
    assert control.returncode == 0, control.stderr
    assert committed_paths(planning.root) == [
        ".harness/stories/story-903.yaml",
        ".harness/stories/story-904.yaml",
    ]


def test_a_refusal_deletes_no_artifact_and_rewrites_none(planning: Planning):
    """Automatic repair is not attempted; the tree is what the session left.

    The control shows the same byte comparison reporting a one-byte edit, so
    "unmodified" is a comparison that can fail.
    """
    text = DEFECTS["strictness"]
    existing = install(planning, "story-800", artifact("story-800"))
    tree_before = planning.tree()

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", text)))

    assert result.returncode != 0
    assert existing.is_file()
    written = planning.stories_dir / "story-900.yaml"
    tree_after = planning.tree()
    assert tree_after.pop(".harness/stories/story-900.yaml") == text.encode()
    assert tree_after == tree_before

    written.write_text(text + "# edited\n", encoding="utf-8")
    assert planning.tree()[".harness/stories/story-900.yaml"] != text.encode()


def test_a_refused_artifact_is_the_developers_and_no_later_session_commits_it(
        planning: Planning):
    """What a refusal leaves is an ordinary uncommitted file in the tree.

    story-023's rule is unchanged by this story: the script commits what
    *appeared* during a session. The refused artifact was already there when
    the next session started, so that session commits only what it added and
    the refused file stays where the developer can repair it, commit it, or
    throw it away.
    """
    assert run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", DEFECTS["schema"]))).returncode != 0
    refused = planning.stories_dir / "story-900.yaml"

    later = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-901.yaml", artifact("story-901"))))

    assert later.returncode == 0, later.stderr
    assert committed_paths(planning.root) == [".harness/stories/story-901.yaml"]
    assert refused.read_text(encoding="utf-8") == DEFECTS["schema"]
    assert "?? .harness/stories/story-900.yaml" in planning.status()


# --------------------------------------------------------------------------
# One message, one function: plan time and pre-flight.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("defect", sorted(PRE_FLIGHT_DEFECTS))
def test_plan_time_and_pre_flight_print_the_same_text(defect: str,
                                                      planning: Planning):
    """The same artifact, the same defect, byte-identical refusals."""
    text = PRE_FLIGHT_DEFECTS[defect]
    planned = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", text)))
    assert planned.returncode != 0

    # The artifact is where the refusal left it, so pre-flight reads exactly
    # the file plan time read. Committing it is what a developer who ignored
    # the refusal would have done.
    planning.git("add", "-A")
    planning.git("commit", "-q", "-m", "committed in spite of the refusal")
    ran = run_script(L5_RUN, planning, "story-900")

    assert ran.stderr == planned.stderr
    assert ran.stderr.strip(), "neither path printed anything"
    assert ran.returncode == 1


def load_l5_plan():
    """`scripts/l5-plan` as a module, so `report` can be called directly.

    Through `conftest.load_script`, the shared loader for the extensionless
    entry points, since story-029: building a module is done in one place
    under tests/ so that recovering one out of git history has nowhere to
    happen quietly.
    """
    return load_script("l5-plan", name="l5_plan_under_test")


def test_both_paths_print_through_the_one_refusal_function(monkeypatch,
                                                           tmp_path: Path,
                                                           target_root: Path):
    """Recorded on the coordinator's own attribute, which both call by name.

    Replacing `story_coordinator.refuse_bad_story` once and seeing both the
    plan-time path and `run_story`'s pre-flight arrive in the recorder is the
    demonstration that it is one function rather than two that agree today.
    """
    import plan_commit

    calls = []
    monkeypatch.setattr(story_coordinator, "refuse_bad_story",
                        lambda path, problems: calls.append((path, problems)) or 1)

    stories = tmp_path / "stories"
    stories.mkdir()
    before = plan_commit.snapshot(stories)
    (stories / "story-900.yaml").write_text(DEFECTS["schema"], encoding="utf-8")
    l5_plan = load_l5_plan()
    assert l5_plan.report(tmp_path, stories, before, STAGES) == 1
    assert [path.name for path, _ in calls] == ["story-900.yaml"]

    (target_root / ".harness" / "stories" / "story-001.yaml").write_text(
        DEFECTS["schema"], encoding="utf-8")
    assert story_coordinator.run_story("story-001", HARNESS_ROOT, target_root) == 1
    assert [path.name for path, _ in calls] == ["story-900.yaml", "story-001.yaml"]
    assert calls[0][1] == calls[1][1]

    # Control: a valid artifact reaches the recorder from neither path.
    (stories / "story-900.yaml").write_text(artifact(), encoding="utf-8")
    l5_plan.report(tmp_path, stories, before, STAGES)
    assert len(calls) == 2


def test_the_refusal_helpers_are_public_and_the_private_names_are_gone():
    source = (HARNESS_ROOT / "orchestration" / "story_coordinator.py").read_text(
        encoding="utf-8")
    assert callable(story_coordinator.refuse)
    assert callable(story_coordinator.refuse_bad_story)
    assert "_refuse_bad_story" not in source
    assert not re.search(r"\b_refuse\b", source)
    # Control: the pre-story source did carry both private names.
    old = show("orchestration/story_coordinator.py")
    assert "_refuse_bad_story" in old and re.search(r"\b_refuse\b", old)


# --------------------------------------------------------------------------
# Everything this story did not change.
# --------------------------------------------------------------------------


def test_a_session_that_added_nothing_still_says_so_and_exits_on_its_own_status(
        planning: Planning):
    before, refs_before = planning.head(), remote_refs(planning.remote)

    for code in (0, 5):
        result = run_plan(planning, L5_STUB_EXIT=code)
        assert result.returncode == code, result.stdout + result.stderr
        assert "committed nothing" in result.stdout
        assert result.stderr == ""
        assert planning.head() == before
        assert remote_refs(planning.remote) == refs_before

    # Control: the same fixture with an artifact written does move both, so
    # the stillness above is the no-artifact path rather than a broken run.
    control = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", artifact())))
    assert control.returncode == 0, control.stderr
    assert planning.head() != before
    assert remote_refs(planning.remote) != refs_before


def test_the_no_artifact_path_reads_the_same_as_before_this_story(
        tmp_path: Path, planning: Planning):
    old = run_script(pre_story_harness(tmp_path) / "scripts" / "l5-plan",
                     planning, "add a thing", L5_STUB_EXIT=5)
    new = run_plan(planning, "add a thing", L5_STUB_EXIT=5)

    assert (new.returncode, new.stdout, new.stderr) == \
        (old.returncode, old.stdout, old.stderr)


def test_a_valid_session_commits_and_pushes_exactly_as_it_did_before(
        tmp_path: Path):
    """The old script and the new one, each over its own identical repository."""
    old_repo = make_planning(tmp_path / "old")
    old_remote = bare_remote(tmp_path / "old", old_repo, upstream=True)
    new_repo = make_planning(tmp_path / "new")
    new_remote = bare_remote(tmp_path / "new", new_repo, upstream=True)
    written = writes((".harness/stories/story-900.yaml", artifact()))

    old = run_script(pre_story_harness(tmp_path) / "scripts" / "l5-plan",
                     old_repo, "add a thing", L5_STUB_WRITE=written)
    new = run_plan(new_repo, "add a thing", L5_STUB_WRITE=written)

    assert (new.returncode, new.stdout, new.stderr) == \
        (old.returncode, old.stdout, old.stderr)
    assert new.returncode == 0, new.stderr
    assert committed_paths(new_repo.root) == committed_paths(old_repo.root)
    assert new_repo.subject() == old_repo.subject()
    assert remote_refs(new_remote)["refs/heads/main"] == new_repo.head()
    assert remote_refs(old_remote)["refs/heads/main"] == old_repo.head()


def test_the_sessions_own_status_still_wins_over_a_refusal(planning: Planning):
    """story-023's process model survives: the session's status is the script's."""
    result = run_plan(
        planning,
        L5_STUB_WRITE=writes((".harness/stories/story-900.yaml", DEFECTS["schema"])),
        L5_STUB_EXIT=7,
    )
    assert result.returncode == 7
    assert "story-900.yaml" in result.stderr


# --------------------------------------------------------------------------
# l5-run's pre-flight, unchanged.
# --------------------------------------------------------------------------


def branches(planning: Planning) -> list[str]:
    return sorted(planning.git("for-each-ref", "--format=%(refname)",
                               "refs/heads").stdout.split())


@pytest.mark.parametrize("defect", sorted(PRE_FLIGHT_DEFECTS))
def test_pre_flight_refuses_what_it_refused_before_this_story(
        defect: str, tmp_path: Path, planning: Planning):
    """The same command over the same repository, old coordinator and new."""
    install(planning, "story-900", PRE_FLIGHT_DEFECTS[defect])

    old = run_script(pre_story_harness(tmp_path) / "scripts" / "l5-run",
                     planning, "story-900")
    new = run_script(L5_RUN, planning, "story-900")

    assert new.returncode == old.returncode == 1
    assert new.stderr == old.stderr
    assert new.stdout == old.stdout


def test_a_refused_run_creates_no_run_directory_state_log_or_branch(
        planning: Planning):
    """Asserted by looking, with a control that shows the readers can see.

    Each absence is re-read after the very thing is created by hand, so a
    green assertion is the run having created nothing rather than the reader
    looking somewhere nothing is ever written.
    """
    install(planning, "story-900", DEFECTS["schema"])
    branches_before = branches(planning)

    result = run_script(L5_RUN, planning, "story-900")

    run_dir = planning.root / ".harness" / "runs" / "story-900"
    logs = planning.root / ".harness" / "logs"
    assert result.returncode == 1
    assert not run_dir.exists()
    assert not (run_dir / "state.json").exists()
    assert sorted(logs.glob("*")) == []
    assert branches(planning) == branches_before
    assert planning.status() == ""

    # Control: the same four readers over a repository where each exists.
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("{}", encoding="utf-8")
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "story-900.log").write_text("x", encoding="utf-8")
    planning.git("branch", "story/story-900")
    assert run_dir.exists() and (run_dir / "state.json").exists()
    assert list(logs.glob("*")) != []
    assert branches(planning) != branches_before


def test_pre_flight_does_not_start_refusing_the_strictness_class(
        planning: Planning):
    """Plan time is the only place it is caught; committed stories still run.

    Asserted by pre-flight getting past the story checks — it reaches the
    clean-tree refusal, which names the dirty path rather than the artifact.
    """
    install(planning, "story-900", strict_artifact())
    (planning.root / "dirty.txt").write_text("developer's own\n", encoding="utf-8")

    result = run_script(L5_RUN, planning, "story-900")

    assert result.returncode == 1
    assert "dirty.txt" in result.stderr
    assert "story-900.yaml" not in result.stderr
    # Control: the same command with a schema-invalid artifact is refused for
    # the artifact instead, so getting past it above means something.
    install(planning, "story-900", DEFECTS["schema"])
    refused = run_script(L5_RUN, planning, "story-900")
    assert "story-900.yaml" in refused.stderr


# --------------------------------------------------------------------------
# Non-vacuity: the pre-story script committed every one of these.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("defect", sorted(DEFECTS))
def test_the_pre_story_script_committed_the_failing_artifact(
        defect: str, tmp_path: Path, planning: Planning):
    """The control for every refusal in this file.

    Run the script as it was at this story's baseline over the same repository
    and the same stub: it commits and pushes the artifact this story refuses.
    So "not committed" is this story's change and not a property of the
    fixture, the stub or the artifact.
    """
    before, refs_before = planning.head(), remote_refs(planning.remote)

    result = run_script(pre_story_harness(tmp_path) / "scripts" / "l5-plan",
                        planning, "add a thing",
                        L5_STUB_WRITE=writes(
                            (".harness/stories/story-900.yaml", DEFECTS[defect])))

    assert result.returncode == 0, result.stdout + result.stderr
    assert planning.head() != before
    assert remote_refs(planning.remote) != refs_before
    assert committed_paths(planning.root) == [".harness/stories/story-900.yaml"]


def test_the_pre_story_harness_has_no_plan_validation_module(tmp_path: Path):
    """The control above is only a control if the old code really is old."""
    root = pre_story_harness(tmp_path)
    assert not (root / "orchestration" / "plan_validation.py").exists()
    assert "plan_validation" not in (root / "scripts" / "l5-plan").read_text(
        encoding="utf-8")
    assert "plan_validation" in L5_PLAN.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The prompt says what the mechanism now does.
# --------------------------------------------------------------------------


def test_the_planner_prompt_states_that_plan_time_validates_before_committing():
    prompt = PLANNER_PROMPT.read_text(encoding="utf-8")
    assert re.search(r"(?i)l5-plan validates", prompt)
    assert re.search(r"(?i)fails validation is not committed", prompt)
    assert re.search(r"(?i)scope the restriction to creation", prompt)
    # Control: the pre-story text of the same file, at the story's baseline,
    # says none of it — so these are this story's sentences.
    old = show("prompts/planner.md")
    assert not re.search(r"(?i)l5-plan validates", old)
    assert not re.search(r"(?i)fails validation is not committed", old)


#: A step telling the planner to run a validation command itself. The
#: enforcement is the deterministic check in the script; a command an agent or
#: a developer must remember is the approach this story rejects.
VALIDATION_INSTRUCTIONS = (
    re.compile(r"(?i)\bl5-validate\b"),
    re.compile(r"(?im)^\s*(?:\d+\.\s*)?(?:run|invoke)\b[^.\n]*\bvalidat"),
    re.compile(r"(?i)\byou (?:must |should |then )?validate\b"),
)


def validation_instructions(text: str) -> list[str]:
    return [match.group(0) for pattern in VALIDATION_INSTRUCTIONS
            for match in pattern.finditer(text)]


def test_no_prompt_or_script_asks_anyone_to_run_a_validation_command():
    candidates = sorted((HARNESS_ROOT / "prompts").glob("*.md")) + \
        sorted(p for p in (HARNESS_ROOT / "scripts").iterdir() if p.is_file())
    assert len(candidates) > 5, "the sweep found almost nothing to sweep"
    offenders = {
        str(path.relative_to(HARNESS_ROOT)): found
        for path in candidates
        if (found := validation_instructions(path.read_text(
            encoding="utf-8", errors="replace")))
    }
    assert offenders == {}
    assert not (HARNESS_ROOT / "scripts" / "l5-validate").exists()
    # Control: the same sweep with the rejected step planted into one of them.
    planted = "6. Run l5-validate on the artifact before the session ends."
    assert validation_instructions(planted) != []
    assert validation_instructions(
        PLANNER_PROMPT.read_text(encoding="utf-8") + planted) != []


# --------------------------------------------------------------------------
# The module says what it does not catch.
# --------------------------------------------------------------------------


def test_the_module_states_the_two_classes_it_does_not_catch():
    """Written where a reader of the module meets it, not in a story artifact."""
    docstring = plan_validation.__doc__
    assert docstring
    # Whitespace-collapsed, so a sentence the module wraps over two lines is
    # still the sentence.
    lowered = " ".join(docstring.lower().split())
    assert "does not catch" in lowered
    assert "neither the stage nor the prefix" in lowered
    # The single clause that both restricts creation and restricts more.
    assert "single clause that both restricts creation and restricts more" in lowered


def test_the_module_returns_problems_rather_than_printing_them():
    """The caller decides; a module that printed could not be composed."""
    source = PLAN_VALIDATION.read_text(encoding="utf-8")
    assert not re.search(r"^\s*print\(", source, re.MULTILINE)
    assert not re.search(r"\bsys\.exit\b", source)
    assert re.search(r"\bprint\(", f"{source}\nprint('x')\n")


def test_the_script_stays_a_thin_entry_point():
    """The composition and the decision live in orchestration."""
    source = L5_PLAN.read_text(encoding="utf-8")
    assert "plan_validation.artifact_problems" in source
    assert "strictness_problems" not in source
    assert "stage_exception_problems" not in source
    assert "read_story" not in source
    assert "story_coordinator.refuse_bad_story" in source


def test_scripts_l5_plan_writes_nothing_and_removes_nothing_on_a_refusal():
    """Searched for, since a repair or a deletion would be code in the script."""
    source = L5_PLAN.read_text(encoding="utf-8")
    for pattern in (r"\.unlink\(", r"\.write_text\(", r"shutil\.", r"os\.remove"):
        assert not re.search(pattern, source), pattern
    assert re.search(r"\.unlink\(", f"{source}\npath.unlink()\n")
