"""Independent validation for story-025: plan time validates the artifact it
just wrote.

The subject is again *a script that outlives its interactive session*, so most
of what is asserted here is asserted end-to-end: a throwaway target repository
is built under tmp_path, a stub `claude` is put on PATH, and the real
`scripts/l5-plan` is run against it as a subprocess. The stub reads no prompt
and runs no git, so a commit that exists afterwards was made by the script and
a commit that does not exist was refused by it.

The fixture, the stub and the runner are imported from
`test_plan_commit` rather than copied. That file built them for the
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
    command over the same repository;
  * since story-177, "the scope-reason check passes an entry with a reason"
    sits beside the same entry with its parenthetical emptied, which the same
    check reports, and "pre-flight does not refuse a bare entry" sits beside
    the plan-time composition refusing the same text;
  * since story-181, "a quoted search pattern holding a stage name beside a
    prefix is not reported" sits beside the same sentence with its delimiters
    stripped, which the same check reports in the words it always has;
  * since story-222, "a gap carrying a reason passes" sits beside the same
    gap with its parenthetical emptied, which the same check reports; "a path
    beside a blocked one is not reported" sits beside the same story handed a
    blocked list that does block it; "pre-flight does not refuse either new
    class" sits beside the plan-time composition refusing the same text and a
    schema-invalid artifact pre-flight does refuse; and "no code in a run
    reads gaps_left" sits beside the same scan over a copy of a module with a
    read planted in it.

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
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import NothingToCompareAgainst, load_script
import conftest
from test_plan_commit import (
    ARTIFACT,
    Planning,
    artifact,
    as_the_session_left_it,
    bare_remote,
    committed_paths,
    conferring_paths,
    kept_worktree,
    make_planning,
    python_code,
    refs_carrying_a_new_commit,
    remote_refs,
    run_plan,
    stamped,
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

WORKFLOW = conftest.shipped_workflow(HARNESS_ROOT, "story-workflow")
STAGES = WORKFLOW["stages"]
STAGE_NAMES = [stage["name"] for stage in STAGES]
RESTRICTIONS = story_coordinator.stage_restrictions(STAGES)
#: The stage/prefix pair the strictness check is about, and a stage the
#: workflow defines but never restricted, which is what makes an over-broad
#: stage_exceptions grant a grant of nothing.
#: Selected by its sense rather than by its position: the strictness check
#: reports only the creation sense — a confinement has no over-strict
#: restatement, since it governs everything outside its prefixes whatever the
#: act — so the restriction these assertions are about has to be one of those.
RESTRICTION = next(restriction for restriction in RESTRICTIONS
                   if restriction.sense == story_coordinator.CREATE_RESTRICTION)
RESTRICTED_STAGE, RESTRICTED_PREFIX = RESTRICTION.stage, RESTRICTION.prefix
UNRESTRICTED_STAGE = next(
    name for name in STAGE_NAMES
    if name not in {restriction.stage for restriction in RESTRICTIONS}
)

#: The confinement beside it, for the assertions about what the strictness
#: check leaves alone. Read off the same workflow, so a definition that stopped
#: declaring one would fail the anchor below rather than pass vacuously.
CONFINEMENT = next(restriction for restriction in RESTRICTIONS
                   if restriction.sense == story_coordinator.CONFINEMENT)
UNDEFINED_STAGE = "cartographer"


def test_the_workflow_this_file_reads_still_has_something_to_say():
    """The derivations above are load-bearing; an empty one would go green."""
    assert RESTRICTIONS, "the workflow declares no path restriction"
    assert UNDEFINED_STAGE not in STAGE_NAMES
    assert UNRESTRICTED_STAGE in STAGE_NAMES
    # Both senses are declared, so the assertions that turn on the difference
    # between them have a real difference to turn on.
    assert RESTRICTION.sense != CONFINEMENT.sense
    assert CONFINEMENT.stage in STAGE_NAMES


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


#: The one do_not_modify line the control artifact carries, and the same line
#: with its reason stripped or emptied. The path is read off the artifact
#: rather than written here, so these say nothing about which path it is;
#: what they change is the shape of the entry and nothing else.
DO_NOT_MODIFY_LINE = re.search(r"^    - .+ \(.+\)$", ARTIFACT, re.MULTILINE)
assert DO_NOT_MODIFY_LINE is not None, \
    "the control artifact carries no do_not_modify entry with a reason"
REASONED_ENTRY_LINE = DO_NOT_MODIFY_LINE.group(0)
BARE_ENTRY_LINE = re.sub(r" \(.+\)$", "", REASONED_ENTRY_LINE)
EMPTY_PARENTHETICAL_LINE = f"{BARE_ENTRY_LINE} ()"


def scope_reason_artifact(entry_line: str, story_id: str = "story-900") -> str:
    """The control artifact with its do_not_modify entry replaced by `entry_line`.

    The story-177 class is decided on the shape of that one entry, so every
    other byte of the artifact is the control's: an artifact that is refused
    is refused for the entry and for nothing else about it.
    """
    text = artifact(story_id)
    assert REASONED_ENTRY_LINE in text
    return text.replace(REASONED_ENTRY_LINE, entry_line)


#: The reason `reasoned` writes onto a bare entry. Its text is never read by
#: the check, so any reason at all would do; this one says what it is.
COPY_REASON = "a reason the test wrote onto its copy, since story-177"


def reasoned(text: str) -> str:
    """Artifact text with a reason on every bare scope.do_not_modify entry.

    The counterpart of `stamped` for the story-177 class: the committed
    artifacts written before this story carry bare entries, and a committed
    artifact is an execution record that is never edited to satisfy a contract
    written after it. A module that validates a *copy* of one as a plan would
    be validated today reasons the copy, exactly as it stamps it, and leaves
    the file under the stories directory alone.

    A text edit rather than a re-serialisation, for the reason `without_grants`
    in the forced-adaptation module gives: the artifacts are read by the
    harness's own parser, and dumping a parse back out would be asserting
    against a document this repository never wrote. Only the single-line items
    directly under `do_not_modify:` are touched, and an entry that already
    carries a reason is left as it is.
    """
    lines = text.splitlines(keepends=True)
    start = next((index for index, line in enumerate(lines)
                  if line.strip() == "do_not_modify:"), None)
    if start is None:
        return text
    indent = len(lines[start]) - len(lines[start].lstrip())
    item = " " * (indent + 2) + "- "
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.startswith(item):
            break
        entry = line[len(item):].rstrip("\n")
        if plan_validation.carries_a_reason(entry):
            continue
        lines[index] = f"{item}{entry.rstrip()} ({COPY_REASON})\n"
    return "".join(lines)


def test_reasoned_reasons_every_bare_entry_and_only_those():
    """The helper the corpus modules lean on, asserted before it is used.

    Positive: a copy with two bare entries and one reasoned entry comes back
    with all three carrying a reason, and only those three lines differ.
    Control: text carrying no do_not_modify block comes back byte for byte,
    so a helper that found nothing could not pass as one that reasoned it.
    """
    text = scope_reason_artifact(BARE_ENTRY_LINE).replace(
        BARE_ENTRY_LINE, f"{BARE_ENTRY_LINE}\n{BARE_ENTRY_LINE}-more\n"
        f"{REASONED_ENTRY_LINE}")
    before = story_coordinator.read_story(stamped(text)).parsed
    assert plan_validation.scope_reason_problems(before) != []

    after = story_coordinator.read_story(stamped(reasoned(text))).parsed
    assert plan_validation.scope_reason_problems(after) == []
    assert len(after["scope"]["do_not_modify"]) == 3
    assert after["scope"]["do_not_modify"][2] == before["scope"]["do_not_modify"][2]
    changed = [(a, b) for a, b in zip(text.splitlines(), reasoned(text).splitlines())
               if a != b]
    assert [a for a, _ in changed] == [BARE_ENTRY_LINE, f"{BARE_ENTRY_LINE}-more"]
    assert all(b.endswith(f" ({COPY_REASON})") for _, b in changed)

    without_block = text.replace("  do_not_modify:\n", "  do_not_modify: []\n")
    for line in (BARE_ENTRY_LINE, f"{BARE_ENTRY_LINE}-more", REASONED_ENTRY_LINE):
        without_block = without_block.replace(line + "\n", "")
    assert reasoned(without_block) == without_block


#: The defects l5-plan must report, and what each is defective about. Keyed
#: by name so the parametrised tests below say which one failed.
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
#: The scope-reason class since story-177 is plan-time only for the same
#: reason — committed artifacts carry bare entries and are not rewritten —
#: and is absent here and asserted absent from pre-flight the same way. So
#: are story-222's two classes, the gap-reason class for a reason of its own
#: and the blocked-work class for the strictness class's.
PRE_FLIGHT_DEFECTS = dict(DEFECTS)

DEFECTS["strictness"] = strict_artifact()
DEFECTS["scope-reason-bare"] = scope_reason_artifact(BARE_ENTRY_LINE)
DEFECTS["scope-reason-empty"] = scope_reason_artifact(EMPTY_PARENTHETICAL_LINE)


# --------------------------------------------------------------------------
# story-222's two classes, as artifacts. The eighth refuses a gaps_left entry
# carrying no reason; the ninth refuses a plan scheduling work on a path the
# execution rules block for every stage. Each defect is the control artifact
# with lines added to it and none of its own changed, so what refuses it is
# the added entry and nothing riding along with it.
# --------------------------------------------------------------------------

#: The field a plan records each gap it leaves in, read from where the
#: coordinator spells it once rather than written here.
GAPS_LEFT = story_coordinator.GAPS_LEFT

#: A gap carrying the developer's reason, and the same gap bare and with its
#: parenthetical emptied. The reason is never read by the check, so any reason
#: would do; this one names a ground, as the planner is told a reason does.
REASONED_GAP = ("a gap this test's plan leaves (it lies in a different part "
                "of the system)")
BARE_GAP = re.sub(r" \(.+\)$", "", REASONED_GAP)
EMPTY_GAP = f"{BARE_GAP} ()"


def gaps_artifact(*entries: str, story_id: str = "story-900") -> str:
    """The control artifact with a gaps_left list holding `entries` after it."""
    return artifact(story_id) + f"\n{GAPS_LEFT}:\n" + "".join(
        f"  - {entry}\n" for entry in entries)


#: The paths l5-plan validates against: the execution rules under the harness
#: root it runs from, which is this repository, read through the reader the
#: coordinator reads them with. An input to the end-to-end cases rather than a
#: subject — the script resolves its own harness root, so these are the rules
#: it applies — and nothing below depends on which paths they are: the first
#: one written as a directory is taken, and a file is put beneath it and
#: beside it.
BLOCKED_PATHS = harness_config.load_rules(HARNESS_ROOT)["blocked_paths"]
BLOCKED_PREFIX = next(prefix for prefix in BLOCKED_PATHS if prefix.endswith("/"))
BLOCKED_FILE = f"{BLOCKED_PREFIX}zzz-planned-work.md"
BESIDE_FILE = f"{BLOCKED_PREFIX.rstrip('/')}-beside/zzz-planned-work.md"

#: The control artifact's own scope.modify line, read off it rather than
#: written here, and how many entries that list holds — which is the index an
#: entry added after them takes.
MODIFY_LINE = re.search(r"^  modify:\n((?:    - .+\n)+)", ARTIFACT, re.MULTILINE)
assert MODIFY_LINE is not None, "the control artifact carries no modify entry"
MODIFY_ENTRY_LINES = MODIFY_LINE.group(1)
MODIFY_ENTRIES = MODIFY_ENTRY_LINES.count("    - ")


def modify_artifact(path: str, story_id: str = "story-900") -> str:
    """The control artifact with `path` added to scope.modify after the
    entries it already carries."""
    text = artifact(story_id)
    assert MODIFY_ENTRY_LINES in text
    return text.replace(MODIFY_ENTRY_LINES, f"{MODIFY_ENTRY_LINES}    - {path}\n", 1)


def likely_file_artifact(path: str, story_id: str = "story-900") -> str:
    """The control artifact with a technical_plan naming `path` as its one
    likely file change, assigned to a stage the workflow defines and restricts
    nowhere, so no other class has anything to say about the entry."""
    return artifact(story_id) + (
        "\ntechnical_plan:\n"
        "  implementation_steps:\n"
        "    - do the sample work\n"
        "  likely_file_changes:\n"
        f"    - file: {path}\n"
        f"      stage: {UNRESTRICTED_STAGE}\n"
        "      reason: the sample work changes it\n")


DEFECTS["gap-reason-bare"] = gaps_artifact(BARE_GAP)
DEFECTS["gap-reason-empty"] = gaps_artifact(EMPTY_GAP)
DEFECTS["blocked-modify"] = modify_artifact(BLOCKED_FILE)
DEFECTS["blocked-likely-file"] = likely_file_artifact(BLOCKED_FILE)

#: Each of those defects with the one thing wrong with it put right — the gap
#: given a reason, the path moved beside the blocked one — which is what the
#: end-to-end cases commit as their control; where a refusal of it points; and
#: the entry it quotes.
STORY_222_DEFECTS = {
    "gap-reason-bare": (gaps_artifact(REASONED_GAP), f"$.{GAPS_LEFT}[0]",
                        BARE_GAP),
    "gap-reason-empty": (gaps_artifact(REASONED_GAP), f"$.{GAPS_LEFT}[0]",
                         EMPTY_GAP),
    "blocked-modify": (modify_artifact(BESIDE_FILE),
                       f"$.scope.modify[{MODIFY_ENTRIES}]", BLOCKED_FILE),
    "blocked-likely-file": (likely_file_artifact(BESIDE_FILE),
                            "$.technical_plan.likely_file_changes[0]",
                            BLOCKED_FILE),
}


def plan_time_problems(text: str) -> list[str]:
    """Every plan-time problem an artifact's text has, through the readers
    `artifact_problems` composes, without the filesystem the composition
    needs. What a defect below is defective about is one of these."""
    reading = story_coordinator.read_story(text)
    found = list(reading.problems)
    if reading.parsed is not None:
        found += story_coordinator.stage_exception_problems(reading.parsed, STAGES)
        found += plan_validation.strictness_problems(reading.parsed, STAGES)
        found += plan_validation.scope_reason_problems(reading.parsed)
        found += plan_validation.gap_reason_problems(reading.parsed)
        found += plan_validation.blocked_work_problems(reading.parsed,
                                                      BLOCKED_PATHS)
    return found


def test_every_defect_this_file_uses_is_actually_defective():
    """Otherwise a refusal test could pass on an artifact nothing objects to."""
    for name, text in DEFECTS.items():
        assert plan_time_problems(text), \
            f"the {name} artifact has nothing wrong with it"
    # Stamped, because this is the one artifact here that must have nothing
    # wrong with it, and an artifact with no mandate has something wrong with
    # it since story-087. The defects above are what a *session* writes, which
    # is before the stamp, so they carry none.
    assert plan_time_problems(stamped(artifact())) == []


def test_the_scope_reason_defects_differ_from_the_control_in_the_one_entry_alone():
    """The anchor for the scope-reason class: each of its defects is the
    control artifact with one line changed, that line is the do_not_modify
    entry, and the pre-flight readers find nothing wrong with either — so
    what refuses them below is the shape of that entry and not a schema,
    exception or strictness defect riding along with it."""
    control = artifact("story-900").splitlines()
    for name in ("scope-reason-bare", "scope-reason-empty"):
        defective = DEFECTS[name].splitlines()
        assert len(defective) == len(control), name
        changed = [(before, after) for before, after in zip(control, defective)
                   if before != after]
        assert changed == [(REASONED_ENTRY_LINE, {
            "scope-reason-bare": BARE_ENTRY_LINE,
            "scope-reason-empty": EMPTY_PARENTHETICAL_LINE,
        }[name])], name
        reading = story_coordinator.read_story(stamped(DEFECTS[name]))
        assert reading.problems == [], name
        assert story_coordinator.stage_exception_problems(reading.parsed, STAGES) == []
        assert plan_validation.strictness_problems(reading.parsed, STAGES) == []
        assert plan_validation.scope_reason_problems(reading.parsed) != [], name
    # And the bare line really is bare, and the empty one really is empty.
    assert "(" not in BARE_ENTRY_LINE
    assert EMPTY_PARENTHETICAL_LINE.endswith(" ()")


def test_the_story_222_defects_add_their_entry_to_the_control_and_nothing_else():
    """The anchor for story-222's two classes, in the scope-reason one's
    idiom: each defect is the control artifact with lines added and none of
    its own changed, every reader that ran before this story finds nothing
    wrong with it, and of the two new classes exactly its own reports it — so
    what refuses it below is the added entry. Each one's repaired counterpart,
    which the end-to-end cases commit as their control, has nothing wrong with
    it at all.

    The gap entries really are bare and empty, the blocked file really is
    beneath a path the execution rules block, and the file beside it really
    is not — each decided by the coordinator's own reading of a blocked
    path."""
    control = artifact("story-900").splitlines(keepends=True)
    gap_defects = {"gap-reason-bare", "gap-reason-empty"}
    for name, (repaired, _, _) in STORY_222_DEFECTS.items():
        defective = DEFECTS[name].splitlines(keepends=True)
        remaining = iter(defective)
        assert all(line in remaining for line in control), name
        assert len(defective) > len(control), name
        reading = story_coordinator.read_story(stamped(DEFECTS[name]))
        assert reading.problems == [], name
        assert story_coordinator.stage_exception_problems(reading.parsed, STAGES) == []
        assert plan_validation.strictness_problems(reading.parsed, STAGES) == []
        assert plan_validation.scope_reason_problems(reading.parsed) == []
        gaps = plan_validation.gap_reason_problems(reading.parsed)
        work = plan_validation.blocked_work_problems(reading.parsed, BLOCKED_PATHS)
        expected = (1, 0) if name in gap_defects else (0, 1)
        assert (len(gaps), len(work)) == expected, name
        assert plan_time_problems(stamped(repaired)) == [], name
    assert "(" not in BARE_GAP and EMPTY_GAP.endswith(" ()")
    assert story_coordinator.is_blocked(BLOCKED_FILE, BLOCKED_PATHS)
    assert not story_coordinator.is_blocked(BESIDE_FILE, BLOCKED_PATHS)


# --------------------------------------------------------------------------
# The repository, the stub, and the pre-story harness.
# --------------------------------------------------------------------------


@pytest.fixture
def planning(tmp_path: Path) -> Planning:
    """A target repository with a stub `claude` on PATH and a bare origin."""
    planning = make_planning(tmp_path)
    planning.remote = bare_remote(tmp_path, planning, upstream=True)
    return planning


#: The pre-story texts this module runs, carried as committed fixtures rather
#: than resolved out of this repository's commit graph. Every one of them is an
#: *input* — an earlier version of a script or a template, run below to show
#: what it did not do — and resolving an input out of a commit graph made the
#: comparison move whenever something was committed, renamed, squashed or
#: rebased, none of which is a property of the code under test.
PRE_STORY_FIXTURES = {
    "orchestration/story_coordinator.py":
        "story_coordinator.at-story-025-baseline.py.txt",
    "scripts/l5-plan": "l5-plan.at-story-025-baseline.py.txt",
    "scripts/l5-run": "l5-run.at-story-025-baseline.py.txt",
    "prompts/planner.md": "prompts-planner.at-story-025-baseline.md.txt",
}


def show(path: str) -> str:
    """One file as it was before this story.

    story-029 folded this module's private `git show` into
    `conftest.repository_file_at`, which resolved the same baseline this
    already resolved for itself; story-053 replaced the resolution with the
    committed fixture holding the same text. Subject and strictness unchanged.

    A path with no fixture answers empty, which is what a path that did not
    exist at the baseline answered before — the caller below relies on it to
    say that a file the story added carried no caller of the parser.
    """
    name = PRE_STORY_FIXTURES.get(path)
    return conftest.history_fixture(name) if name else ""


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
        conftest.with_todays_restriction_derivation(
            conftest.repointed_at_todays_signature(
                show("orchestration/story_coordinator.py"))),
        encoding="utf-8")
    for script in ("l5-plan", "l5-run"):
        written = root / "scripts" / script
        written.write_text(
            conftest.repointed_at_todays_signature(show(f"scripts/{script}")),
            encoding="utf-8")
        written.chmod(0o755)
    return root


def run_script(script: Path, planning: Planning, *args: str,
               **stub) -> subprocess.CompletedProcess:
    # Stdin is a terminal with a declining reply already in it: since
    # story-087 l5-plan stamps the mandate a run resolves only where there is
    # one, and an artifact carrying none is refused before it is committed.
    # This module's subject is what the plan-time checks do, so its sessions
    # need the terminal that lets them reach those checks at all.
    with conftest.a_terminal_for_stdin() as stdin:
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=planning.root, env=planning.env(**stub),
            stdin=stdin, capture_output=True, text=True,
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

    problems = plan_validation.artifact_problems([path], STAGES, HARNESS_ROOT,
        HARNESS_ROOT, WORKFLOW['name'])

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
    monkeypatch.setattr(plan_validation, "scope_reason_problems",
                        lambda *a: seen.append("scope-reason") or [])
    path = tmp_path / "story-900.yaml"
    path.write_text(DEFECTS["unparseable"], encoding="utf-8")

    assert plan_validation.artifact_problems([path], STAGES, HARNESS_ROOT,
        HARNESS_ROOT, WORKFLOW['name'])[path]
    assert seen == []

    # Control: a well-formed artifact does reach all of them, in the order
    # the composition calls them — the story-177 check after the ones that
    # were there before it — so the emptiness above is the guard rather than
    # a recorder never being wired up.
    path.write_text(stamped(artifact()), encoding="utf-8")
    assert plan_validation.artifact_problems([path], STAGES, HARNESS_ROOT,
        HARNESS_ROOT, WORKFLOW['name']) == {}
    assert seen == ["exceptions", "strictness", "scope-reason"]


def test_artifact_problems_holds_only_the_artifacts_with_problems(tmp_path: Path):
    good, bad = tmp_path / "story-900.yaml", tmp_path / "story-901.yaml"
    good.write_text(stamped(artifact("story-900")), encoding="utf-8")
    bad.write_text(DEFECTS["schema"], encoding="utf-8")

    problems = plan_validation.artifact_problems([good, bad], STAGES, HARNESS_ROOT,
        HARNESS_ROOT, WORKFLOW['name'])

    assert list(problems) == [bad]
    assert plan_validation.artifact_problems([good], STAGES, HARNESS_ROOT,
        HARNESS_ROOT, WORKFLOW['name']) == {}


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


#: The modules outside the coordinator that validate an artifact against a
#: schema, exempted by name and by nothing else. Each one's subject is a
#: different artifact: `workflow_selection` checks what the first of l5-plan's
#: two planner invocations wrote, an agent-authored answer held to the schema
#: the prompt asked it to satisfy exactly as every other agent-authored
#: artifact is; `outbox` checks what it reads back out of its own queue, which
#: is how a poisoned entry is a decidable condition rather than a judgement;
#: `filed_query` checks what a target's own command printed on stdout, which is
#: how an answer the harness cannot use is nothing-known with a reason rather
#: than a crash in a caller; `inspection` checks the findings file an Inspector
#: invocation wrote, and each finding in it separately against the brief
#: schema, which is how one malformed finding is dropped and named rather than
#: costing the ones beside it; `brief_fetch` checks what a target's command
#: answered a brief-fetch question with, against the envelope and then the
#: brief it carries against the brief schema on its own, which is how a brief
#: that is malformed in a field is refused naming the field rather than the
#: shape; `brief_filing` checks the brief document an assist session wrote
#: before it is enqueued, against that same brief schema, which is how a brief
#: that does not satisfy it is refused naming the field that failed rather than
#: being filed. The rule these tests hold is about the *story* artifact having one
#: reader, and each exemption is held shut from both sides below so it cannot
#: quietly become a second one.
VALIDATES_ANOTHER_ARTIFACT = (
    "orchestration/workflow_selection.py",
    "orchestration/outbox.py",
    "orchestration/filed_query.py",
    "orchestration/inspection.py",
    "orchestration/brief_fetch.py",
    "orchestration/brief_filing.py",
)


def test_the_only_caller_of_the_parser_and_the_validator_is_still_the_coordinator():
    """Asserted by search over the harness, and unchanged from the baseline."""
    def callers(read) -> dict:
        found = {}
        for directory in ("orchestration", "scripts"):
            for path in sorted((HARNESS_ROOT / directory).iterdir()):
                if not path.is_file() or path.name == "__init__.py":
                    continue
                relative = str(path.relative_to(HARNESS_ROOT))
                if relative in VALIDATES_ANOTHER_ARTIFACT:
                    continue
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


@pytest.mark.parametrize("exempt", VALIDATES_ANOTHER_ARTIFACT)
def test_the_exempt_module_validates_something_that_is_not_a_story(exempt):
    """The other side of each exemption, so none can go stale or widen.

    An exemption naming a module that has stopped validating anything is a
    hole nobody notices; one naming a module that has started reading story
    artifacts is the very thing the rule above forbids, hidden behind the
    name. So every exempt module must exist, must actually call the validator,
    must call the story parser nowhere, and must name neither the story schema
    nor the story artifact's own directory.
    """
    source = (HARNESS_ROOT / exempt).read_text(
        encoding="utf-8")
    assert "schema_validator.validate(" in source
    assert "story_parser" not in source
    assert "story.schema.json" not in source
    assert '"story"' not in source
    # And the coordinator is still held to the rule in full: it is not exempt
    # from anything, so a story read moving into the exempt module would leave
    # the assertion above with nothing to find here.
    coordinator = (HARNESS_ROOT / "orchestration" / "story_coordinator.py"
                   ).read_text(encoding="utf-8")
    assert "story_parser.parse(" in coordinator


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


# --------------------------------------------------------------------------
# A confinement has no over-strict restatement, so none is reported
#
# The check reports a sentence that states a *create* restriction as an
# unscoped one: a stage forbidden only to add files under a path may still
# modify one there, so an unscoped sentence demands more than the workflow
# does. A confinement makes no such distinction — it governs everything
# outside the paths it names whatever the act — so a sentence naming the stage
# together with the prefix it is confined to restates the rule accurately, and
# reporting it would refuse a planner for writing down what the workflow says.
#
# The pair below is the whole of the difference, and it is driven against one
# synthetic workflow declaring both senses over *the same prefix*: nothing but
# the sense differs between the two sentences, so a check that had stopped
# reading the sense would have to report both or neither.
# --------------------------------------------------------------------------


#: One stage confined to a prefix, and another forbidden to create under that
#: same prefix. Neither name is one this repository deploys.
BOTH_SENSES_STAGES = [
    {"name": "cartographer", "may_only_change": ["atlas/"]},
    {"name": "surveyor", "may_not_create": ["atlas/"]},
]
BOTH_SENSES = {restriction.sense: restriction for restriction
               in story_coordinator.stage_restrictions(BOTH_SENSES_STAGES)}


def unscoped(restriction) -> str:
    """A sentence naming a restriction's stage beside its prefix, unscoped.

    Composed from the restriction rather than written per sense, so the two
    sentences differ in the stage the workflow attached each sense to and in
    nothing else.
    """
    return f"the {restriction.stage} leaves {restriction.prefix} alone entirely"


def test_the_synthetic_pair_differs_in_nothing_but_its_sense():
    """The anchor: same prefix, two senses, two distinct stages."""
    confinement = BOTH_SENSES[story_coordinator.CONFINEMENT]
    creation = BOTH_SENSES[story_coordinator.CREATE_RESTRICTION]
    assert confinement.prefix == creation.prefix
    assert confinement.stage != creation.stage
    assert confinement.stage not in STAGE_NAMES
    assert creation.stage not in STAGE_NAMES


def test_a_sentence_restating_a_confinement_accurately_is_not_reported():
    confinement = BOTH_SENSES[story_coordinator.CONFINEMENT]
    assert plan_validation.strictness_problems(
        {"constraints": [unscoped(confinement)]}, BOTH_SENSES_STAGES) == []


def test_the_same_sentence_about_a_create_restriction_is_still_reported():
    """The control the absence above needs, and the criterion's second half.

    Same shape of sentence, same prefix, same workflow — only the sense of the
    restriction its stage carries differs — and this one is reported, in the
    words it has always been reported with.
    """
    creation = BOTH_SENSES[story_coordinator.CREATE_RESTRICTION]
    (problem,) = plan_validation.strictness_problems(
        {"constraints": [unscoped(creation)]}, BOTH_SENSES_STAGES)

    assert problem.startswith("$.constraints[0]:")
    assert "states a restriction the workflow does not" in problem
    assert "without scoping it to creation" in problem
    # And the restriction is quoted in the workflow's own words.
    assert creation.wording in problem


def test_the_shipped_confinement_is_not_reported_either():
    """The same absence against what this repository deploys.

    Its control is the test above it, which reports the create-sense sentence
    built the same way — so silence here is the sense being read rather than
    the check having stopped seeing anything.
    """
    assert plan_validation.strictness_problems(
        {"constraints": [unscoped(CONFINEMENT)]}, STAGES) == []
    assert plan_validation.strictness_problems(
        {"constraints": [unscoped(RESTRICTION)]}, STAGES) != []


def test_the_module_names_no_stage_and_no_restricted_prefix():
    """Searched for, not inspected — docstring and comments included.

    The control plants each literal into a copy of the module and shows the
    same scanner reporting it, so a clean scan is the module being clean
    rather than the scanner looking for the wrong thing.
    """
    source = PLAN_VALIDATION.read_text(encoding="utf-8")
    literals = STAGE_NAMES + [restriction.prefix
                              for restriction in RESTRICTIONS]
    found = [literal for literal in literals
             if re.search(re.escape(literal), source, re.IGNORECASE)]
    assert found == []
    for literal in literals:
        planted = f'{source}\nSTAGE = "{literal}"\n'
        assert [x for x in literals
                if re.search(re.escape(x), planted, re.IGNORECASE)] != []


# --------------------------------------------------------------------------
# A quoted span is text about text, not a restatement
#
# story-181's distinction. l5-plan refused story-179's artifact for two lines
# that quoted a search pattern — the stage name beside the prefix, as a grep
# argument — and asserted the pattern is found nowhere. The pattern holds
# both halves by construction, and the check read it as a clause about the
# stage. Now a span delimited by backticks or by double quotes is a quoted
# span; a pairing whose two halves both sit inside one such span is not
# reported, and a pairing with either half outside every span is reported
# exactly as before. Spans are found on the whole entry before clause
# splitting, and single quotes are not a delimiter.
#
# Every sentence below is composed from RESTRICTED_STAGE and RESTRICTED_PREFIX
# as the rest of this file does, and the last assertion in the section scans
# the section's own source for a literal of either. Each absence — "this
# quoted sentence is not reported" — sits beside the same sentence with its
# delimiters stripped, which the same check reports.
#
# The architecture document is not asserted on here. This story assigns its
# strictness_problems entry to the stage that runs after this one, in
# `likely_file_changes`.
# --------------------------------------------------------------------------


#: The two delimiters of a quoted span, and the one that is deliberately not.
QUOTE_DELIMITERS = ["`", '"']
NOT_A_DELIMITER = "'"


def quoted_pattern(delimiter: str, joiner: str = "|") -> str:
    """The story-179 shape: a search pattern holding both halves, quoted.

    The joiner sits between the stage and the prefix inside the span, so a
    clause boundary can be placed inside the quoted pattern without changing
    anything else about the sentence.
    """
    return (f"grep for {delimiter}{RESTRICTED_STAGE}{joiner}{RESTRICTED_PREFIX}"
            f"{delimiter} over a file returns nothing")


def unquoted_restatement() -> str:
    """The shape the check has always reported, with no quote mark in it."""
    return f"the {RESTRICTED_STAGE} leaves {RESTRICTED_PREFIX} alone"


def strictness(entry: str) -> list[str]:
    return plan_validation.strictness_problems({"constraints": [entry]}, STAGES)


def reported_in_the_usual_words(entry: str, clause: str = None) -> str:
    """The one message the check has always produced for a clause, verbatim.

    Composed from the same pieces the module composes it from, so a change in
    the message text — not only in the verdict — would show here.
    """
    clause = entry if clause is None else clause
    return (f"$.constraints[0]: {entry!r} states a restriction the workflow "
            f"does not. The clause {clause!r} names {RESTRICTED_STAGE} together "
            f"with {RESTRICTED_PREFIX} without scoping it to creation, while "
            f"the workflow declares only: {RESTRICTION.wording}")


@pytest.mark.parametrize("delimiter", QUOTE_DELIMITERS)
def test_a_quoted_search_pattern_holding_both_halves_is_not_reported(
        delimiter: str):
    """The first two criteria: backticks, then double quotes."""
    entry = quoted_pattern(delimiter)
    assert strictness(entry) == []
    # Control: the same words with the delimiters removed are one problem, so
    # the silence above is the span and not the sentence being invisible.
    stripped = entry.replace(delimiter, "")
    assert len(strictness(stripped)) == 1
    assert RESTRICTED_STAGE in strictness(stripped)[0]
    assert RESTRICTED_PREFIX in strictness(stripped)[0]


def test_an_unquoted_restatement_is_still_reported_in_the_usual_words():
    entry = unquoted_restatement()
    assert strictness(entry) == [reported_in_the_usual_words(entry)]


def test_a_quoted_span_elsewhere_in_the_entry_excuses_nothing():
    """The exclusion is held to the span, not to a quote mark anywhere."""
    entry = f"{unquoted_restatement()}, see `README`"
    (problem,) = strictness(entry)
    assert problem == reported_in_the_usual_words(entry, unquoted_restatement())
    # Control: the same entry with the pairing moved inside the span it
    # already carries is not reported, so it is the position of the halves
    # relative to the span that decides.
    moved = f"see `{unquoted_restatement()}`, README"
    assert strictness(moved) == []


@pytest.mark.parametrize("shape", [
    "the `{stage}` leaves {prefix} alone",
    "the {stage} leaves `{prefix}` alone",
    "the `{stage}` leaves `{prefix}` alone",
    'the "{stage}" leaves {prefix} alone',
])
def test_a_pairing_with_either_half_outside_the_span_is_still_reported(
        shape: str):
    """Only a span holding both halves excludes the pairing. The third shape
    quotes each half in its own span, and neither span holds both."""
    entry = shape.format(stage=RESTRICTED_STAGE, prefix=RESTRICTED_PREFIX)
    (problem,) = strictness(entry)
    assert problem.startswith(f"$.constraints[0]: {entry!r} states a restriction "
                              "the workflow does not.")
    assert "without scoping it to creation" in problem
    assert RESTRICTION.wording in problem
    # Control: the whole pairing inside one span is not reported.
    both = f"the `{RESTRICTED_STAGE} leaves {RESTRICTED_PREFIX}` alone"
    assert strictness(both) == []


@pytest.mark.parametrize("joiner", [", ", " and ", "; "])
def test_a_clause_boundary_inside_a_quoted_span_stays_inside_it(joiner: str):
    """Spans are found on the whole entry before it is split into clauses,
    so a comma, a semicolon or the word "and" inside the quoted pattern does
    not put the two halves in different clauses with the span cut in two."""
    # The criterion's shape. On its own it cannot tell a whole-entry span
    # search from a per-clause one: cut at the boundary, each piece holds one
    # half, so neither search reports it.
    assert strictness(quoted_pattern("`", joiner)) == []
    # The shape that can tell them apart: cut at the boundary, the piece after
    # it holds both halves and no complete span, so a check that found its
    # spans per clause would report it. Found on the whole entry, the span
    # holds both halves and the pairing is excluded.
    entry = (f"grep for `{RESTRICTED_PREFIX}{joiner}{RESTRICTED_STAGE} "
             f"{RESTRICTED_PREFIX}` over a file returns nothing")
    assert strictness(entry) == []
    # Control: the same entry with its backticks stripped is that per-clause
    # reading, and it is reported — the piece after the boundary is the
    # clause named.
    (problem,) = strictness(entry.replace("`", ""))
    assert "without scoping it to creation" in problem
    assert f"{RESTRICTED_STAGE} {RESTRICTED_PREFIX}" in problem


def test_a_single_quoted_span_is_not_a_span():
    """Single quotes are not a delimiter — an apostrophe would open a span an
    ordinary possessive never closes."""
    entry = quoted_pattern(NOT_A_DELIMITER)
    (problem,) = strictness(entry)
    assert problem.startswith(f"$.constraints[0]: {entry!r}")
    assert "without scoping it to creation" in problem
    # Control: the same sentence with a real delimiter in place of the single
    # quotes is not reported, so the delimiter is the whole difference.
    for delimiter in QUOTE_DELIMITERS:
        assert strictness(entry.replace(NOT_A_DELIMITER, delimiter)) == []


def test_an_unquoted_pairing_beside_a_quoted_one_is_reported_once_verbatim():
    """A quoted span excuses its own pairing and nothing else, and the
    message quotes the entry as the planner wrote it, span and all."""
    entry = f"{unquoted_restatement()}; {quoted_pattern('`')}"
    (problem,) = strictness(entry)
    assert problem == reported_in_the_usual_words(entry, unquoted_restatement())
    assert repr(entry) in problem


def test_the_exclusion_reads_its_halves_off_the_workflow_it_is_handed():
    """Against a workflow restricting something else, a quoted pattern of
    the synthetic pair is not reported and the same pattern unquoted is, so
    the span test reads the definition rather than a copy of it."""
    synthetic = [{"name": "cartographer", "may_not_create": ["atlas/"]},
                 {"name": "surveyor"}]
    quoted = "grep for `cartographer|atlas/` over a file returns nothing"
    unquoted = "the cartographer leaves atlas/ alone"
    assert plan_validation.strictness_problems(
        {"constraints": [quoted]}, synthetic) == []
    assert len(plan_validation.strictness_problems(
        {"constraints": [unquoted]}, synthetic)) == 1


def test_a_span_pairing_halves_of_different_restrictions_excludes_nothing():
    """Under a workflow declaring two create restrictions, a span holding one
    restriction's stage beside the other's prefix is no pairing the clause
    loop would report, so blanking it excludes nothing real — and it must not
    be blanked, or a genuine restatement sitting in the same span is hidden.
    A pooled reading, which blanks a span holding any stage beside any
    prefix, passes every single-restriction case above; this is the case
    that tells the per-restriction reading from it."""
    synthetic = [{"name": "cartographer", "may_not_create": ["atlas/"]},
                 {"name": "surveyor", "may_not_create": ["survey/"]}]
    crossed = "the surveyor must leave `cartographer|survey/` alone"
    (problem,) = plan_validation.strictness_problems(
        {"constraints": [crossed]}, synthetic)
    assert "surveyor" in problem
    assert "survey/" in problem
    assert "without scoping it to creation" in problem
    # Control: the same workflow, with the span holding both halves of one
    # restriction, is not reported — so it is the halves belonging to one
    # restriction that decides, not the second restriction being declared.
    paired = "grep for `surveyor|survey/` over a file returns nothing"
    assert plan_validation.strictness_problems(
        {"constraints": [paired]}, synthetic) == []


def test_the_docstring_says_what_a_quoted_span_is_and_why_it_is_exempt():
    doc = plan_validation.strictness_problems.__doc__
    assert "quoted span" in doc
    assert re.search(r"backticks?", doc)
    assert re.search(r"double quotes?", doc)
    assert re.search(r"single quotes? (?:are|is) not", doc)
    assert "text about text" in doc
    assert re.search(r"restat", doc)


def quoted_span_section() -> str:
    """The source of this section alone, between its header and the next."""
    source = VALIDATION_FILE.read_text(encoding="utf-8")
    start = source.index("# A quoted span is text about text")
    end = source.index("# The scope-reason check", start)
    return source[start:end]


def test_this_section_names_no_stage_and_no_restricted_prefix():
    """The same scanner the file applies to the module under test, over the
    section that composes every sentence above, with the same control."""
    section = quoted_span_section()
    assert "def quoted_pattern" in section
    assert "def test_a_single_quoted_span_is_not_a_span" in section
    literals = STAGE_NAMES + [restriction.prefix
                              for restriction in RESTRICTIONS]
    found = [literal for literal in literals
             if re.search(re.escape(literal), section, re.IGNORECASE)]
    assert found == []
    for literal in literals:
        planted = f'{section}\nSTAGE = "{literal}"\n'
        assert [x for x in literals
                if re.search(re.escape(x), planted, re.IGNORECASE)] != []


# --------------------------------------------------------------------------
# The scope-reason check: shape, not prose
#
# story-177's class. Every scope.do_not_modify entry is a path or area
# followed by the developer's reason in a closing parenthetical, and the check
# decides on that shape alone: non-empty text before a closing parenthetical
# holding at least one word character passes, and nothing about what the
# reason says is read. The stories below are built in place rather than read
# off any artifact, so the check is handed exactly the shape each test is
# about and nothing rides along.
# --------------------------------------------------------------------------


def with_do_not_modify(*entries) -> dict:
    return {"scope": {"modify": ["src/"], "do_not_modify": list(entries)}}


def test_a_bare_path_is_reported_naming_its_index_and_quoting_the_entry():
    (problem,) = plan_validation.scope_reason_problems(
        with_do_not_modify("docs/zzz-elsewhere/"))
    assert problem.startswith("$.scope.do_not_modify[0]:")
    assert "'docs/zzz-elsewhere/'" in problem
    assert "carries no reason" in problem
    # The form a passing entry takes is shown, so a planner repairing the
    # entry sees what to write rather than only what was wrong.
    assert "(<the developer's reason for leaving it alone>)" in problem


def test_an_empty_parenthetical_is_reported_the_same_way_as_a_bare_path():
    """The criterion's own words: refused the same way. Same pointer, same
    quoting, same shown form; only the quoted entry differs."""
    bare = plan_validation.scope_reason_problems(
        with_do_not_modify("docs/zzz-elsewhere/"))
    empty = plan_validation.scope_reason_problems(
        with_do_not_modify("docs/zzz-elsewhere/ ()"))
    assert len(bare) == len(empty) == 1
    assert empty[0].startswith("$.scope.do_not_modify[0]:")
    assert "'docs/zzz-elsewhere/ ()'" in empty[0]
    assert empty[0].replace("'docs/zzz-elsewhere/ ()'", "'docs/zzz-elsewhere/'") \
        == bare[0]


@pytest.mark.parametrize("entry", [
    "docs/zzz-elsewhere/ (  )",
    "docs/zzz-elsewhere/ (--)",
    "docs/zzz-elsewhere/ ( ... )",
])
def test_a_parenthetical_holding_no_word_character_is_reported(entry: str):
    """Empty in the sense the check reads: whitespace or punctuation alone is
    not a reason any reader could weigh, so it is refused as an empty one."""
    (problem,) = plan_validation.scope_reason_problems(with_do_not_modify(entry))
    assert problem.startswith("$.scope.do_not_modify[0]:")
    assert repr(entry) in problem


@pytest.mark.parametrize("entry", [
    "docs/zzz-elsewhere/ (the companion story owns it)",
    "the other modules carrying a sample fixture (they never pass through "
    "plan-time validation)",
    "src/zzz.py (see the brief (filed at plan time) for why)",
    "src/zzz.py (x)",
    "src/zzz.py(no space before the parenthetical)",
])
def test_an_entry_with_a_reason_passes_whatever_the_reason_says(entry: str):
    """A path, an area described in words rather than a path, a reason that
    itself carries parentheses, a one-character reason and one with no space
    before it: shape is what is read, and each of these has the shape."""
    assert plan_validation.scope_reason_problems(with_do_not_modify(entry)) == []
    # Control for the silence: the same entry with its parenthetical emptied
    # is reported, so the check saw the entry and passed it for its reason.
    emptied = re.sub(r"\(.*\)$", "()", entry)
    assert emptied != entry
    assert plan_validation.scope_reason_problems(with_do_not_modify(emptied)) != []


@pytest.mark.parametrize("entry", [
    "(a reason with no path before it)",
    "   (a reason with only whitespace before it)",
    "docs/zzz-elsewhere/ (a reason) and then more path",
])
def test_a_parenthetical_that_is_not_at_the_end_or_has_nothing_before_it_is_reported(
        entry: str):
    """The form is text *then* a closing parenthetical: a parenthetical alone
    names nothing to leave alone, and one followed by more text is not the
    entry's closing one."""
    (problem,) = plan_validation.scope_reason_problems(with_do_not_modify(entry))
    assert problem.startswith("$.scope.do_not_modify[0]:")


def test_the_check_reads_no_prose_so_a_reason_it_cannot_score_still_passes():
    """The constraint that the check reads shape and not prose, held from the
    side that matters: a reason that is nonsense, a reason that is the path
    again, and a reason in another script all pass. Whether a reason is a
    good one is a reviewer's to weigh, and the check has no opinion."""
    for reason in ("qqq", "docs/zzz-elsewhere/", "ζζζ", "42"):
        assert plan_validation.scope_reason_problems(
            with_do_not_modify(f"docs/zzz-elsewhere/ ({reason})")) == [], reason


def test_one_problem_per_offending_entry_each_naming_its_own_index():
    problems = plan_validation.scope_reason_problems(with_do_not_modify(
        "docs/zzz-one/",
        "docs/zzz-two/ (a reason)",
        "docs/zzz-three/ ()",
        "docs/zzz-four/ (another reason)",
        "docs/zzz-five/",
    ))
    assert [problem.split(":")[0] for problem in problems] == [
        "$.scope.do_not_modify[0]",
        "$.scope.do_not_modify[2]",
        "$.scope.do_not_modify[4]",
    ]
    assert "'docs/zzz-one/'" in problems[0]
    assert "'docs/zzz-three/ ()'" in problems[1]
    assert "'docs/zzz-five/'" in problems[2]


def test_an_empty_list_a_missing_list_and_a_missing_scope_yield_nothing():
    assert plan_validation.scope_reason_problems(with_do_not_modify()) == []
    assert plan_validation.scope_reason_problems({"scope": {"modify": ["src/"]}}) == []
    assert plan_validation.scope_reason_problems({}) == []
    # Control: the same reader with one bare entry in the list is not silent,
    # so the three silences above are the empty inputs rather than a reader
    # that never looks.
    assert plan_validation.scope_reason_problems(
        with_do_not_modify("docs/zzz-elsewhere/")) != []


def test_a_non_string_entry_is_left_to_the_schema():
    """The schema declares the items strings and refuses anything else; this
    check neither crashes on one nor reports it twice."""
    assert plan_validation.scope_reason_problems(
        with_do_not_modify({"path": "docs/zzz-elsewhere/"}, 7, None)) == []


def test_the_check_reaches_the_composed_problems_for_a_real_artifact(tmp_path: Path):
    """Through `artifact_problems`, on the defect the end-to-end tests drive:
    the composed problems for the bare-entry artifact are exactly the ones
    the check reports for it on its own, so what l5-plan prints is this
    check's message and nothing else the composition found."""
    path = tmp_path / "story-900.yaml"
    path.write_text(stamped(DEFECTS["scope-reason-bare"]), encoding="utf-8")
    problems = plan_validation.artifact_problems([path], STAGES, HARNESS_ROOT,
        HARNESS_ROOT, WORKFLOW['name'])
    story = story_coordinator.read_story(stamped(DEFECTS["scope-reason-bare"])).parsed
    assert problems[path] == plan_validation.scope_reason_problems(story)
    assert len(problems[path]) == 1
    assert problems[path][0].startswith("$.scope.do_not_modify[0]:")


def test_the_module_states_the_seventh_class_and_why_it_is_plan_time_only():
    """Beside the account of the classes before it, in the module's own
    docstring, where a reader of the module meets it."""
    lowered = " ".join(plan_validation.__doc__.lower().split())
    assert "what the seventh check is" in lowered
    seventh = lowered[lowered.index("what the seventh check is"):]
    assert "do_not_modify" in seventh
    assert "closing parenthetical" in seventh
    assert "plan-time only" in seventh
    assert "reads shape and not prose" in seventh
    # The reason is the one the third, fourth and fifth are given, and the
    # docstring says so rather than giving a new one.
    assert "for the reason the third, fourth and fifth are" in seventh


# --------------------------------------------------------------------------
# story-222: the gap-reason check and the blocked-work check
#
# The eighth class refuses a gaps_left entry carrying no reason, on the terms
# the seventh refuses a do_not_modify entry and through the same predicate.
# The ninth refuses a scope.modify entry or a likely_file_changes file at or
# beneath a path the execution rules block for every stage, decided by the
# coordinator's is_blocked. Both are driven here against stories built in
# place and against blocked lists this module writes, so each check is handed
# exactly the shape a test is about; the list the shipped rules hold is an
# input only to the end-to-end cases further down, where l5-plan reads it
# itself.
# --------------------------------------------------------------------------


def with_gaps(*entries) -> dict:
    return {GAPS_LEFT: list(entries)}


#: The grounds a refusal of a gap shows a reason may name, as phrases a
#: planner repairing the entry would look for.
GROUNDS_A_REASON_MAY_NAME = (
    "a different part of the system",
    "the story's workflow",
    "a decision the developer chooses not to make now",
)


def test_a_bare_gap_is_reported_naming_its_index_quoting_it_and_showing_the_form():
    (problem,) = plan_validation.gap_reason_problems(with_gaps(BARE_GAP))
    assert problem.startswith(f"$.{GAPS_LEFT}[0]:")
    assert repr(BARE_GAP) in problem
    assert "carries no reason" in problem
    # The form a passing entry takes, and the grounds a reason may name, are
    # shown, so a planner repairing the entry sees what to write rather than
    # only what was wrong.
    assert re.search(r"<[^>]+> \(<[^>]*reason[^>]*>\)", problem)
    for ground in GROUNDS_A_REASON_MAY_NAME:
        assert ground in problem, ground


def test_an_empty_parenthetical_gap_is_reported_the_same_way_as_a_bare_one():
    """Same pointer, same quoting, same shown form; only the quoted entry
    differs."""
    bare = plan_validation.gap_reason_problems(with_gaps(BARE_GAP))
    empty = plan_validation.gap_reason_problems(with_gaps(EMPTY_GAP))
    assert len(bare) == len(empty) == 1
    assert empty[0].replace(repr(EMPTY_GAP), repr(BARE_GAP)) == bare[0]


@pytest.mark.parametrize("entry", [
    f"{BARE_GAP} (  )",
    f"{BARE_GAP} (--)",
    f"{BARE_GAP} ( ... )",
    "(a reason with no gap before it)",
    f"{BARE_GAP} (a reason) and then more gap",
])
def test_a_gap_whose_parenthetical_is_empty_misplaced_or_alone_is_reported(entry: str):
    """Empty in the sense the predicate reads — no word character — and a
    parenthetical that is not the entry's closing one, or has nothing before
    it, are each refused as a bare gap is."""
    (problem,) = plan_validation.gap_reason_problems(with_gaps(entry))
    assert problem.startswith(f"$.{GAPS_LEFT}[0]:")


@pytest.mark.parametrize("entry", [
    REASONED_GAP,
    f"{BARE_GAP} (it waits on a decision the developer chooses not to make now)",
    f"{BARE_GAP} (see the brief (filed at plan time) for why)",
    f"{BARE_GAP} (x)",
    f"{BARE_GAP} (qqq)",
    f"{BARE_GAP}(no space before the parenthetical)",
])
def test_a_gap_carrying_any_reason_passes_whatever_the_reason_says(entry: str):
    """A reason naming a ground, one carrying parentheses of its own, a
    one-character one, one no reader could weigh and one with no space before
    it: shape is what is read, and each of these has the shape. Whether a
    reason names a real ground is the developer's to give and a reviewer's to
    weigh, and the check has no opinion."""
    assert plan_validation.gap_reason_problems(with_gaps(entry)) == []
    # Control for the silence: the same entry with its parenthetical emptied
    # is reported, so the check saw the entry and passed it for its reason.
    emptied = re.sub(r"\(.*\)$", "()", entry)
    assert emptied != entry
    assert plan_validation.gap_reason_problems(with_gaps(emptied)) != []


def test_one_problem_per_offending_gap_each_naming_its_own_index():
    problems = plan_validation.gap_reason_problems(with_gaps(
        BARE_GAP, REASONED_GAP, EMPTY_GAP, REASONED_GAP, f"{BARE_GAP} again"))
    assert [problem.split(":")[0] for problem in problems] == [
        f"$.{GAPS_LEFT}[0]", f"$.{GAPS_LEFT}[2]", f"$.{GAPS_LEFT}[4]"]


def test_no_gaps_left_an_empty_list_and_entries_that_are_not_strings_yield_nothing():
    """A story leaving no gap, and the shapes the schema refuses before this
    check could be asked, are refused for nothing here. The control is the
    same check over one bare entry, which it reports, so the silences are the
    inputs rather than a check that never looks."""
    assert plan_validation.gap_reason_problems({}) == []
    assert plan_validation.gap_reason_problems(with_gaps()) == []
    assert plan_validation.gap_reason_problems({GAPS_LEFT: BARE_GAP}) == []
    assert plan_validation.gap_reason_problems(with_gaps(7, None, {"gap": 1})) == []
    assert plan_validation.gap_reason_problems(with_gaps(BARE_GAP)) != []


#: Entry shapes the reason predicate tells apart, for the parity below: some
#: it passes and some it refuses.
REASON_SHAPES = (
    BARE_GAP, EMPTY_GAP, "(x)", "   (x)", "x (  )", "x (--)",
    "x (a) and more", "x (a)", "x(a)", "x (a (b) c)", "ζ (ζ)", "x\n(a)",
)


def test_a_gap_and_a_do_not_modify_entry_are_held_to_one_reading_of_a_reason():
    """The criterion's own terms: whether a gaps_left entry carries a reason
    is decided by the definition that decides it for a do_not_modify entry.
    Over every shape above, the two checks refuse exactly the same entries,
    and the name plan_validation decides through is the coordinator's own
    predicate. Both verdicts occur among the shapes, so the agreement is the
    two checks deciding alike rather than both answering one way."""
    verdicts = {}
    for entry in REASON_SHAPES:
        gap = bool(plan_validation.gap_reason_problems(with_gaps(entry)))
        kept = bool(plan_validation.scope_reason_problems(
            with_do_not_modify(entry)))
        assert gap == kept, entry
        verdicts[entry] = gap
    assert set(verdicts.values()) == {True, False}
    assert plan_validation.carries_a_reason is story_coordinator.carries_a_reason


def test_the_gap_check_decides_through_the_predicate_it_imports(monkeypatch):
    """Recorded rather than inferred: the predicate plan_validation imports
    is consulted once per entry, and its answer is the verdict — a stand-in
    passing everything refuses nothing, even a bare gap, and one passing
    nothing refuses a reasoned one."""
    consulted = []
    monkeypatch.setattr(plan_validation, "carries_a_reason",
                        lambda entry: consulted.append(entry) or True)
    assert plan_validation.gap_reason_problems(with_gaps(BARE_GAP, EMPTY_GAP)) == []
    assert consulted == [BARE_GAP, EMPTY_GAP]

    monkeypatch.setattr(plan_validation, "carries_a_reason", lambda entry: False)
    assert len(plan_validation.gap_reason_problems(with_gaps(REASONED_GAP))) == 1


#: A blocked list this module writes: a directory, and a directory beneath
#: another, neither of which any repository's own rules hold.
FIXTURE_BLOCKED = ["zzz-sealed/", "zzz-vault/inner/"]


def with_work(modify=(), files=()) -> dict:
    """A story planning work on `modify` in its scope and on `files` in its
    technical plan, each file assigned to a stage the workflow defines."""
    story = {"scope": {"modify": list(modify), "do_not_modify": []}}
    if files:
        story["technical_plan"] = {"likely_file_changes": [
            {"file": file, "stage": UNRESTRICTED_STAGE,
             "reason": "the work changes it"} for file in files]}
    return story


@pytest.mark.parametrize("prefix", FIXTURE_BLOCKED)
def test_a_modify_entry_at_or_beneath_a_blocked_path_is_reported_naming_both(prefix):
    """The blocked path itself, a file beneath it and a file deeper still:
    each is one problem, pointing at its own entry, quoting it, naming the
    blocked path, and saying the work goes in gaps_left instead."""
    for entry in (prefix, f"{prefix}x.md", f"{prefix}deeper/x.md"):
        (problem,) = plan_validation.blocked_work_problems(
            with_work(modify=["src/", entry]), FIXTURE_BLOCKED)
        assert problem.startswith("$.scope.modify[1]:"), entry
        assert repr(entry) in problem
        assert repr(prefix) in problem
        assert GAPS_LEFT in problem


@pytest.mark.parametrize("prefix", FIXTURE_BLOCKED)
def test_a_likely_file_at_or_beneath_a_blocked_path_is_reported_the_same_way(prefix):
    for entry in (f"{prefix}x.md", f"{prefix}deeper/x.md"):
        (problem,) = plan_validation.blocked_work_problems(
            with_work(files=["src/app.py", entry]), FIXTURE_BLOCKED)
        assert problem.startswith("$.technical_plan.likely_file_changes[1]:"), entry
        assert repr(entry) in problem
        assert repr(prefix) in problem
        assert GAPS_LEFT in problem


@pytest.mark.parametrize("entry", [
    "zzz-sealed-beside/x.md",
    "zzz-sealed",
    "src/zzz-sealed/x.md",
    "zzz-vault/x.md",
    "zzz-vault/innerness/x.md",
])
def test_a_path_beside_a_blocked_one_is_not_reported(entry: str):
    """A directory whose name only begins with a blocked one's, the blocked
    name without its separator, the blocked name further down another path,
    and the parent of a blocked directory: none is at or beneath a blocked
    path, in either list. The control is the same story handed a blocked list
    that does hold the entry, where both of its entries are reported — so the
    silence is the path's position rather than a check that saw nothing."""
    story = with_work(modify=[entry], files=[entry])
    assert plan_validation.blocked_work_problems(story, FIXTURE_BLOCKED) == []
    assert len(plan_validation.blocked_work_problems(
        story, [*FIXTURE_BLOCKED, entry])) == 2


def test_the_blocked_check_decides_through_the_coordinators_is_blocked(monkeypatch):
    """Recorded rather than inferred: is_blocked is asked about every entry
    of both lists, against the paths the check was handed, and its answer is
    the verdict — a stand-in blocking everything refuses an entry nothing
    blocks, and one blocking nothing refuses a blocked entry not at all. So
    the check has no reading of a blocked path of its own."""
    asked = []
    real = story_coordinator.is_blocked

    def recording(path, blocked):
        asked.append((path, tuple(blocked)))
        return real(path, blocked)

    monkeypatch.setattr(story_coordinator, "is_blocked", recording)
    plan_validation.blocked_work_problems(
        with_work(modify=["src/x.md"], files=["docs/y.md"]), FIXTURE_BLOCKED)
    assert {path for path, _ in asked} == {"src/x.md", "docs/y.md"}
    assert {prefix for _, blocked in asked for prefix in blocked} \
        == set(FIXTURE_BLOCKED)

    monkeypatch.setattr(story_coordinator, "is_blocked", lambda path, blocked: True)
    assert len(plan_validation.blocked_work_problems(
        with_work(modify=["src/x.md"]), FIXTURE_BLOCKED)) == 1
    monkeypatch.setattr(story_coordinator, "is_blocked", lambda path, blocked: False)
    assert plan_validation.blocked_work_problems(
        with_work(modify=[f"{FIXTURE_BLOCKED[0]}x.md"]), FIXTURE_BLOCKED) == []


def test_one_problem_per_offending_entry_across_both_lists():
    problems = plan_validation.blocked_work_problems(with_work(
        modify=["src/", "zzz-sealed/a.md", "docs/", "zzz-vault/inner/b.md"],
        files=["zzz-sealed/c.md", "src/app.py"]), FIXTURE_BLOCKED)
    assert [problem.split(":")[0] for problem in problems] == [
        "$.scope.modify[1]", "$.scope.modify[3]",
        "$.technical_plan.likely_file_changes[0]"]


def test_no_scope_no_plan_no_blocked_paths_and_entries_of_other_shapes_yield_nothing():
    """A story with nothing planned, a blocked list holding nothing, and the
    shapes the schema refuses before this check could be asked are refused
    for nothing here. The control is the same check over one entry beneath a
    blocked path, which it reports."""
    for story in ({}, {"scope": {}}, {"scope": "src/"}, {"technical_plan": {}},
                  {"technical_plan": {"likely_file_changes": [
                      {"stage": UNRESTRICTED_STAGE}, "zzz-sealed/x.md",
                      {"file": 7}]}},
                  {"scope": {"modify": [7, None, {"path": "zzz-sealed/x"}]}}):
        assert plan_validation.blocked_work_problems(story, FIXTURE_BLOCKED) == [], story
    blocked = with_work(modify=["zzz-sealed/x.md"])
    assert plan_validation.blocked_work_problems(blocked, []) == []
    assert plan_validation.blocked_work_problems(blocked, FIXTURE_BLOCKED) != []


def blocking_harness(root: Path, blocked: list[str]) -> tuple[Path, dict]:
    """A harness root carrying a workflow this module builds and execution
    rules blocking `blocked`, and the workflow itself."""
    workflow = conftest.build_workflow(conftest.workflow_stage())
    conftest.materialize_workflow(workflow, root,
                                  rules={"blocked_paths": list(blocked)})
    return root, workflow


def test_the_composition_reads_the_blocked_paths_from_the_harness_roots_rules(
        tmp_path: Path):
    """The blocked list is the execution rules under the harness root the
    composition is handed — the file the coordinator enforces at run time —
    and not a list of its own. Two harness roots, each blocking a different
    directory, judge the same pair of artifacts oppositely: each refuses the
    one planning work beneath its own blocked directory, naming it, and
    passes the other."""
    first, workflow = blocking_harness(tmp_path / "first", ["zzz-sealed/"])
    second, _ = blocking_harness(tmp_path / "second", ["zzz-other/"])
    sealed, other = tmp_path / "story-900.yaml", tmp_path / "story-901.yaml"
    sealed.write_text(stamped(modify_artifact("zzz-sealed/x.md", "story-900")),
                      encoding="utf-8")
    other.write_text(stamped(modify_artifact("zzz-other/x.md", "story-901")),
                     encoding="utf-8")

    def judged(harness_root: Path) -> dict:
        return plan_validation.artifact_problems(
            [sealed, other], workflow["stages"], tmp_path, harness_root,
            workflow["name"])

    under_first, under_second = judged(first), judged(second)
    assert list(under_first) == [sealed]
    assert list(under_second) == [other]
    (problem,) = under_first[sealed]
    assert problem.startswith(f"$.scope.modify[{MODIFY_ENTRIES}]:")
    assert "'zzz-sealed/'" in problem
    assert "'zzz-other/'" in under_second[other][0]


def test_the_two_classes_run_after_the_reason_check_and_never_on_an_unread_artifact(
        monkeypatch, tmp_path: Path):
    """Composed into artifact_problems after the seventh class, and handed
    the parse read_story made only when read_story had nothing to say — the
    pre-flight order the composition already keeps. The blocked-work check is
    handed the list the harness root's rules hold."""
    harness_root, workflow = blocking_harness(tmp_path / "harness",
                                              ["zzz-sealed/"])
    seen, handed = [], []
    monkeypatch.setattr(plan_validation, "scope_reason_problems",
                        lambda *a: seen.append("scope-reason") or [])
    monkeypatch.setattr(plan_validation, "gap_reason_problems",
                        lambda *a: seen.append("gap-reason") or [])
    monkeypatch.setattr(
        plan_validation, "blocked_work_problems",
        lambda story, blocked: seen.append("blocked-work")
        or handed.append(list(blocked)) or [])
    path = tmp_path / "story-900.yaml"

    def composed() -> dict:
        return plan_validation.artifact_problems(
            [path], workflow["stages"], tmp_path, harness_root, workflow["name"])

    path.write_text(DEFECTS["unparseable"], encoding="utf-8")
    assert composed()[path]
    assert seen == []

    path.write_text(stamped(artifact()), encoding="utf-8")
    assert composed() == {}
    assert seen == ["scope-reason", "gap-reason", "blocked-work"]
    assert handed == [["zzz-sealed/"]]


def ninth_check_section() -> str:
    lowered = " ".join(plan_validation.__doc__.lower().split())
    return lowered[lowered.index("what the ninth check is"):]


def test_the_module_states_the_eighth_and_ninth_classes_and_why_each_is_plan_time_only():
    """Beside the account of the classes before them, in the module's own
    docstring: the opening account names the ninth among the classes sharing
    one reason for being plan-time only and gives the eighth a reason of its
    own, and each has a section saying what it refuses, what decides it and
    why it is plan-time only. That the ninth's reason is true is held below,
    against the committed artifact it names; that the eighth's is — no check,
    routing or pre-flight decision in a run reads the list — by the scan after
    it."""
    lowered = " ".join(plan_validation.__doc__.lower().split())
    opening = lowered[: lowered.index("what the third check is")]
    assert "the third, fourth, fifth, seventh and ninth classes are this " \
        "module's own" in opening
    assert "the eighth is this module's own and runs at plan time only for " \
        "a reason of its own" in opening

    eighth = lowered[lowered.index("what the eighth check is"):
                     lowered.index("what the ninth check is")]
    for phrase in (GAPS_LEFT, "closing parenthetical", "carries_a_reason",
                   "plan-time only", "the seventh class's reason is not its "
                   "reason", "no check, routing or pre-flight decision in a "
                   "run reads the list", "every stage is handed it only as "
                   "part of the story text"):
        assert phrase in eighth, phrase

    ninth = ninth_check_section()
    for phrase in ("scope.modify", "likely_file_changes", "is_blocked",
                   "execution rules", f"{GAPS_LEFT} instead", "plan-time only",
                   "for the reason the third, fourth, fifth and seventh are"):
        assert phrase in ninth, phrase


def test_the_committed_artifact_the_ninth_class_s_reason_names_plans_blocked_work():
    """The ninth class's reason, held rather than remembered: each committed
    artifact its section names as having run with work planned on a blocked
    path does carry such work, by the same check over the rules the
    coordinator enforces — so a pre-flight that refused the class would refuse
    a story that has already run. Read as a subject: a claim about what this
    repository has committed. The control is the same check over the control
    artifact, which plans no such work and is refused for nothing."""
    named = sorted(set(re.findall(r"story-\d+", ninth_check_section())))
    assert named, "the ninth class's section names no committed artifact"
    stories = HARNESS_ROOT.joinpath(".harness", "stories")
    for story_id in named:
        parsed = story_coordinator.read_story(
            (stories / f"{story_id}.yaml").read_text(encoding="utf-8")).parsed
        assert parsed is not None, story_id
        assert plan_validation.blocked_work_problems(parsed, BLOCKED_PATHS) != [], \
            story_id
    assert plan_validation.blocked_work_problems(
        story_coordinator.read_story(stamped(artifact())).parsed,
        BLOCKED_PATHS) == []


#: Where a run is read from: the coordinator and every module beside it, and
#: the entry points. l5-plan is among them, because what it reads at the
#: approval is the subject of the scan's one permitted exception.
RUN_SOURCE_DIRECTORIES = ("orchestration", "scripts")

#: The modules that may name gaps_left in their code: the plan-time checks
#: and the planning entry point, which reads the gaps for the approval
#: display. Both read it through the coordinator's reader, whose definition
#: is taken out of the coordinator's source before the scan, since defining
#: the reader is not reading the list in a run.
PERMITTED_GAP_READERS = ["orchestration/plan_validation.py", "scripts/l5-plan"]
COORDINATOR = "orchestration/story_coordinator.py"


def names_the_gaps(source: str) -> bool:
    """Whether a source's code — its prose stripped, as the sibling scans
    strip it — names the field or the coordinator's constant for it."""
    return re.search(rf"\b{re.escape(GAPS_LEFT)}\b", python_code(source),
                     re.IGNORECASE) is not None


def without_the_gaps_reader(source: str) -> str:
    """The coordinator's source with the field's constant and its reader
    taken out, and nothing else."""
    tree = ast.parse(source)
    reader = story_coordinator.gaps_left.__name__
    kept = [node for node in tree.body
            if not (isinstance(node, ast.FunctionDef) and node.name == reader)
            and not (isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name)
                and getattr(story_coordinator, target.id, None) is GAPS_LEFT
                for target in node.targets))]
    assert len(kept) == len(tree.body) - 2, "the reader or its constant moved"
    tree.body = kept
    return ast.unparse(tree)


def run_sources() -> dict[str, str]:
    """Every Python source under those directories, by repository path: the
    modules by their extension and the extensionless entry points by the
    interpreter their first line names."""
    sources = {}
    for directory in RUN_SOURCE_DIRECTORIES:
        for path in sorted((HARNESS_ROOT / directory).iterdir()):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.suffix == ".py" or (
                    not path.suffix and "python" in text.split("\n", 1)[0]):
                sources[str(path.relative_to(HARNESS_ROOT))] = text
    return sources


def test_nothing_in_a_run_reads_gaps_left():
    """The eighth class's reason for being plan-time only, and the
    constraint the story sets itself, held as one scan: outside the
    coordinator's reader, only the plan-time checks and l5-plan's approval
    display name the field in their code. So no pre-flight, retry, resume or
    routing decision of the run reads what a plan leaves. What the scan does
    not see, and does not claim, is the story text every stage is handed
    whole, which carries the list to each stage's agent.

    The coordinator is scanned with its reader taken out, and the scan of it
    whole is shown to see the reader, so its silence is the run path's
    rather than a scan that cannot see the field there."""
    sources = run_sources()
    assert COORDINATOR in sources and len(sources) > 5
    assert names_the_gaps(sources[COORDINATOR])
    sources[COORDINATOR] = without_the_gaps_reader(sources[COORDINATOR])
    assert sorted(name for name, source in sources.items()
                  if names_the_gaps(source)) == PERMITTED_GAP_READERS


@pytest.mark.parametrize("planted", [
    "def _zzz(story):\n    return story_coordinator.{field}(story)\n",
    "def _zzz(story):\n    return story.get('{field}')\n",
    "def _zzz(story):\n    return story[GAPS_LEFT]\n",
])
def test_the_scan_reports_a_read_planted_in_the_run_path(planted: str):
    """The control for the absence above: the same scan over a copy of the
    coordinator's run path, and of a module beside it, with a read of the
    field planted in code reports each — while the same words planted in a
    comment are not a read and are not reported."""
    read = planted.format(field=GAPS_LEFT)
    sources = run_sources()
    coordinator = without_the_gaps_reader(sources[COORDINATOR])
    assert not names_the_gaps(coordinator)
    assert names_the_gaps(f"{coordinator}\n{read}")
    neighbour = sources["orchestration/context_assembler.py"]
    assert not names_the_gaps(neighbour)
    assert names_the_gaps(f"{neighbour}\n{read}")
    assert not names_the_gaps(
        f"{neighbour}\n# {read.splitlines()[1].strip()}\n")


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
    # Since story-117 the invocation reserves its id before the session runs,
    # so the remote is not untouched; what it must hold is no commit this
    # invocation made. The control is the valid artifact below, where the same
    # reader reports the plan that was pushed.
    assert refs_carrying_a_new_commit(planning, refs_before) == {}
    # And the artifact is where the session wrote it: in the worktree a refusal
    # keeps and names, rather than in the developer's own checkout.
    tree = kept_worktree(result)
    written = tree / planning.stories_dir.relative_to(planning.root) \
        / "story-900.yaml"
    assert written.is_file()
    # story-087: the mandate is conferred between the snapshot and the
    # validation, so what a refusal leaves is the session's bytes plus the
    # block this process appended. Every byte the session wrote is still
    # compared exactly; the reader asserts the rest is a mandate and nothing
    # else.
    assert as_the_session_left_it(written) == text
    # `-uall` rather than the default: in a worktree cut from the base the whole
    # stories directory is untracked, and porcelain's default collapses an
    # untracked directory to its own name, so the file the assertion is about
    # would never be named whatever happened to it.
    assert "?? .harness/stories/story-900.yaml" in planning.git(
        "-C", str(tree), "status", "--porcelain", "-uall").stdout
    # And the developer's own checkout is untouched by any of it.
    assert planning.status() == ""
    assert "story-900.yaml" in result.stderr
    assert "committed nothing" in result.stdout


def test_the_control_for_every_refusal_above_is_the_valid_artifact(
        planning: Planning):
    """The same fixture, the same stub, an artifact nothing objects to."""
    before, refs_before = planning.head(), remote_refs(planning.remote)

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", artifact())))

    assert result.returncode == 0, result.stderr
    # The plan is committed on the story branch and pushed there; the invoked
    # checkout's HEAD does not move, which is what the developer keeps.
    assert planning.planned_head() != before
    assert refs_carrying_a_new_commit(planning, refs_before) != {}
    assert planning.planned_paths() == [".harness/stories/story-900.yaml"]
    assert planning.status() == ""


@pytest.mark.parametrize("defect", ["scope-reason-bare", "scope-reason-empty"])
def test_a_scope_reason_refusal_names_the_field_and_the_entry_it_refused(
        defect: str, planning: Planning):
    """story-177's criterion, in the words the developer reads: the problem
    l5-plan prints names $.scope.do_not_modify and quotes the offending entry.
    The parametrised refusal above holds that nothing was committed and the
    artifact was left byte for byte; this holds what was said about it."""
    bare_entry = DEFECTS[defect].splitlines()[
        artifact("story-900").splitlines().index(REASONED_ENTRY_LINE)]
    entry_text = bare_entry.strip()[2:]
    before = planning.head()

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", DEFECTS[defect])))

    assert result.returncode != 0, result.stdout
    assert planning.head() == before
    assert "$.scope.do_not_modify[0]" in result.stderr
    assert repr(entry_text) in result.stderr
    assert "carries no reason" in result.stderr
    assert "committed nothing" in result.stdout

    # The control the criterion states: the same artifact with a reason in a
    # closing parenthetical on the entry commits and pushes as before. The
    # reason is put on the very line the refusal quoted, so what changed
    # between the refusal and the commit is that line and nothing else.
    refs_before = remote_refs(planning.remote)
    repaired = DEFECTS[defect].replace(bare_entry, REASONED_ENTRY_LINE)
    assert repaired == artifact("story-900")
    control = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", repaired)))
    assert control.returncode == 0, control.stderr
    assert "$.scope.do_not_modify" not in control.stderr
    assert refs_carrying_a_new_commit(planning, refs_before) != {}
    assert planning.planned_paths() == [".harness/stories/story-900.yaml"]


@pytest.mark.parametrize("defect", sorted(STORY_222_DEFECTS))
def test_a_gap_reason_or_blocked_work_refusal_names_what_it_refused(
        defect: str, planning: Planning):
    """story-222's two classes, in the words the developer reads: what
    l5-plan prints points at the offending entry, quotes it, and says where
    such a thing goes — gaps_left — and nothing is committed. The parametrised
    refusal above holds that the artifact was left byte for byte.

    The control is the same artifact with that one entry put right — the gap
    given a reason, the path moved beside the blocked one — which commits and
    pushes as before, with nothing about the entry printed. So the refusal is
    the entry's, and a path beside a blocked one is not refused end to end."""
    repaired, pointer, entry = STORY_222_DEFECTS[defect]
    before = planning.head()

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", DEFECTS[defect])))

    assert result.returncode != 0, result.stdout
    assert planning.head() == before
    assert pointer in result.stderr
    assert repr(entry) in result.stderr
    assert GAPS_LEFT in result.stderr
    assert "committed nothing" in result.stdout

    refs_before = remote_refs(planning.remote)
    control = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", repaired)))
    assert control.returncode == 0, control.stderr
    assert pointer not in control.stderr
    assert refs_carrying_a_new_commit(planning, refs_before) != {}
    assert planning.planned_paths() == [".harness/stories/story-900.yaml"]


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
    assert refs_carrying_a_new_commit(planning, refs_before) == {}
    # Both are where the session wrote them: in the worktree the refusal kept.
    tree = kept_worktree(result)
    stories = tree / planning.stories_dir.relative_to(planning.root)
    left = planning.git(
        "-C", str(tree), "status", "--porcelain", "-uall").stdout
    for name, text in (("story-903", good), ("story-904", bad)):
        assert as_the_session_left_it(stories / f"{name}.yaml") == text
        assert f"?? .harness/stories/{name}.yaml" in left
    assert "story-904.yaml" in result.stderr

    # Control: the same two artifacts with the second one valid is one commit
    # holding both, so "none committed" is the refusal and not the fixture.
    control = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-903.yaml", good),
        (".harness/stories/story-904.yaml", artifact("story-904")),
    ))
    assert control.returncode == 0, control.stderr
    assert planning.planned_paths("story-903") == [
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
    # Since story-117 the session ran in a worktree, so the developer's own
    # checkout is byte for byte what it was — the refusal leaves nothing here
    # at all, not even the artifact it refused.
    assert planning.tree() == tree_before

    # And what it does leave, in the worktree it kept: the artifact the session
    # wrote, unrepaired and unrewritten. story-087: the session's bytes are
    # still every byte of it but the mandate this process conferred before the
    # validation refused it.
    tree = kept_worktree(result)
    written = tree / planning.stories_dir.relative_to(planning.root) \
        / "story-900.yaml"
    assert as_the_session_left_it(written) == text

    # The control: the same byte comparison against the same file reports a
    # one-line edit. It is compared against the bytes the refusal left rather
    # than against the session's text, which the stamp above already differs
    # from — a control that could not tell the two apart would prove nothing.
    left = written.read_bytes()
    written.write_text(
        written.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    assert written.read_bytes() != left


def test_a_refused_artifact_is_the_developers_and_no_later_session_commits_it(
        planning: Planning):
    """What a refusal leaves is an ordinary uncommitted file, where it wrote it.

    story-023's rule is unchanged by this story: the script commits what
    *appeared* during a session. Since story-117 the refused artifact is left
    in the worktree that session kept, where the developer can repair it,
    commit it or throw it away, and a later session gets a worktree and an id
    of its own — so it commits only what it wrote and cannot pick the refused
    one up.
    """
    refusal = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", DEFECTS["schema"])))
    assert refusal.returncode != 0
    kept = kept_worktree(refusal)
    refused = kept / planning.stories_dir.relative_to(planning.root) \
        / "story-900.yaml"

    later = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-901.yaml", artifact("story-901"))))

    assert later.returncode == 0, later.stderr
    assert planning.planned_paths("story-901") == [
        ".harness/stories/story-901.yaml"]
    assert as_the_session_left_it(refused) == DEFECTS["schema"]
    assert "?? .harness/stories/story-900.yaml" in planning.git(
        "-C", str(kept), "status", "--porcelain", "-uall").stdout


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

    # The artifact is where the refusal left it — since story-117 that is the
    # worktree the session ran in — so pre-flight reads exactly the file plan
    # time read. Taking it into the checkout and committing it is what a
    # developer who ignored the refusal would have done.
    relative = planning.stories_dir.relative_to(planning.root) / "story-900.yaml"
    kept = kept_worktree(planned)
    (planning.root / relative).write_bytes((kept / relative).read_bytes())
    planning.git("add", "-A")
    planning.git("commit", "-q", "-m", "committed in spite of the refusal")
    ran = run_script(L5_RUN, planning, "story-900")

    # Each refusal names the artifact it read, and since story-117 those are two
    # different files: plan time read the one in the worktree the session ran in,
    # pre-flight the one the developer committed into their own checkout. So the
    # directory holding it is substituted and every other byte is compared
    # exactly — the defect, the pointer at it and the closing sentence.
    assert str(kept) in planned.stderr, \
        "the plan-time refusal did not name the worktree it read the artifact in"
    assert ran.stderr == planned.stderr.replace(str(kept), str(planning.root))
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

    # Control: a valid artifact reaches the recorder from neither path. It
    # carries the mandate a stamped artifact carries, because this call is made
    # in-process where there is no terminal and therefore nothing to confer one.
    (stories / "story-900.yaml").write_text(stamped(artifact()), encoding="utf-8")
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

    # Control: the same fixture with an artifact written does move the plan's
    # branch and the remote, so the stillness above is the no-artifact path
    # rather than a broken run. Since story-117 the commit is on the story
    # branch in a worktree, so the invoked checkout's HEAD is not what moves.
    control = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", artifact())))
    assert control.returncode == 0, control.stderr
    assert planning.planned_head() != before
    assert refs_carrying_a_new_commit(planning, refs_before) != {}


#: What story-117 gave l5-plan to say, which a script from before it had no
#: notion of: the id it reserved, the worktree it planned in, a claim it
#: released and a worktree it removed. Each is subtracted by what it says, so
#: every other byte of the report is still compared exactly against the older
#: script's.
SAID_SINCE_STORY_117 = (
    "l5-plan: reserved ",
    "l5-plan: planning in ",
    "l5-plan: released the claim on ",
    "l5-plan: removed the worktree ",
    "l5-plan: the session wrote ",
)


def without_story_117(text: str) -> str:
    """`text` with the lines story-117 added to l5-plan's report removed."""
    return "".join(
        line for line in text.splitlines(keepends=True)
        if not any(line.startswith(marker) for marker in SAID_SINCE_STORY_117)
    )


def test_the_no_artifact_path_reads_the_same_as_before_this_story(
        tmp_path: Path, planning: Planning):
    old = run_script(pre_story_harness(tmp_path) / "scripts" / "l5-plan",
                     planning, "add a thing", L5_STUB_EXIT=5)
    new = run_plan(planning, "add a thing", L5_STUB_EXIT=5)

    assert new.stdout != old.stdout, \
        "the story-117 report was meant to be subtracted, not to be absent"
    assert (new.returncode, without_story_117(new.stdout), new.stderr) == \
        (old.returncode, old.stdout, old.stderr)


def test_a_valid_session_commits_and_pushes_exactly_as_it_did_before(
        tmp_path: Path):
    """The old script and the new one, each over its own identical repository."""
    old_repo = make_planning(tmp_path / "old")
    old_remote = bare_remote(tmp_path / "old", old_repo, upstream=True)
    new_repo = make_planning(tmp_path / "new")
    new_remote = bare_remote(tmp_path / "new", new_repo, upstream=True)
    # Where the plan of the new script lands: a declined offer removes the
    # planning worktree and its local branch, so the commit is read off the
    # remote the push put it on.
    new_repo.remote = new_remote
    written = writes((".harness/stories/story-900.yaml", artifact()))

    old = run_script(pre_story_harness(tmp_path) / "scripts" / "l5-plan",
                     old_repo, "add a thing", L5_STUB_WRITE=written)
    new = run_plan(new_repo, "add a thing", L5_STUB_WRITE=written)

    # Things the new script says and the old one had no notion of: story-059
    # ends a successful push by offering to run what was committed, story-087
    # says which mandate it conferred and that it committed the record of it,
    # and story-097 gives the approval question its subject by naming each
    # artifact's story id and title above it. Each is subtracted by what it
    # says, each must appear on exactly one line, and everything else — the
    # status, the stderr and every other byte of the commit-and-push report —
    # is still compared exactly.
    #
    # Each is mapped to the number of lines it is expected on rather than
    # assumed to be on one, because since story-117 "run story-900" is on two.
    # The offer prompt ends without a newline of its own, so before this story
    # the "run it with" line that follows a decline was printed onto the end of
    # it and the two were one line; the decline now reports the worktree it
    # removed between them, which ends that line. Both are still subtracted, so
    # what is compared below is unchanged; what would go unnoticed if this said
    # "at least one" is a third.
    SAID_SINCE = {
        "runs on a mandate conferred by": 1,
        "Record the mandate conferred for": 1,
        "run story-900": 2,
        "l5-plan: story-900: ": 1,
    }
    lines = new.stdout.splitlines(keepends=True)
    for marker, on_lines in SAID_SINCE.items():
        assert sum(marker in line for line in lines) == on_lines, \
            (marker, new.stdout)
    without_them = without_story_117("".join(
        line for line in lines
        if not any(marker in line for marker in SAID_SINCE)
    ))
    # The one line story-117 did not add but changed: the push names the story
    # branch the plan was committed on rather than the branch the checkout
    # stands on. It is taken out of both texts and asserted as its own fact
    # below, so everything either script says about the commit and the push is
    # still compared byte for byte.
    pushed = re.compile(r"^l5-plan: pushed .*\n", re.M)
    assert new.stdout != old.stdout
    assert (new.returncode, pushed.sub("", without_them), new.stderr) == \
        (old.returncode, pushed.sub("", old.stdout), old.stderr)
    assert f"l5-plan: pushed {new_repo.planned_branch()} to origin\n" in new.stdout
    assert "l5-plan: pushed main to origin\n" in old.stdout
    assert new.returncode == 0, new.stderr
    # The same commit, holding the same paths under the same subject. Since
    # story-117 the new script puts it on the story branch rather than on the
    # branch the checkout stands on, so where it is read from is what differs
    # and what is read is what is compared.
    assert new_repo.planned_paths() == committed_paths(old_repo.root)
    assert new_repo.planned_subject() == old_repo.subject()
    assert remote_refs(new_remote)[
        f"refs/heads/{new_repo.planned_branch()}"] == new_repo.planned_head()
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
    # Stamped: a committed artifact is one l5-plan has already conferred a
    # mandate on, and pre-flight refuses one without a mandate above the
    # clean-tree check this test is about reaching.
    install(planning, "story-900", stamped(strict_artifact()))
    # Since story-117 the clean-tree check reads the tree the run *works* in,
    # and a dirty developer checkout is expressly not what refuses a run. So
    # the checkout is put on the story branch, where it is its own run root,
    # and dirtied there — which is the tree that check is about.
    planning.git("checkout", "-q", "-b",
                 story_coordinator.story_branch(
                     harness_config.load_config(planning.root), "story-900"))
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


@pytest.mark.parametrize("defect", ["scope-reason-bare", "scope-reason-empty"])
def test_pre_flight_does_not_start_refusing_the_scope_reason_class(
        defect: str, planning: Planning):
    """story-177's class is plan-time only, held the way the strictness one
    is: a committed artifact whose do_not_modify entry carries no reason gets
    past the story checks to the clean-tree refusal, which names the dirty
    path and not the artifact. That is what keeps every artifact committed
    before the rule runnable without being rewritten."""
    install(planning, "story-900", stamped(DEFECTS[defect]))
    planning.git("checkout", "-q", "-b",
                 story_coordinator.story_branch(
                     harness_config.load_config(planning.root), "story-900"))
    (planning.root / "dirty.txt").write_text("developer's own\n", encoding="utf-8")

    result = run_script(L5_RUN, planning, "story-900")

    assert result.returncode == 1
    assert "dirty.txt" in result.stderr
    assert "story-900.yaml" not in result.stderr
    assert "do_not_modify" not in result.stderr
    # Control: the same artifact is refused at plan time — the same text,
    # through the composition l5-plan calls — so getting past pre-flight
    # above is the class being plan-time only rather than the artifact
    # having nothing wrong with it. And the same command with a
    # schema-invalid artifact is refused for the artifact instead.
    assert plan_validation.scope_reason_problems(
        story_coordinator.read_story(stamped(DEFECTS[defect])).parsed) != []
    install(planning, "story-900", DEFECTS["schema"])
    refused = run_script(L5_RUN, planning, "story-900")
    assert "story-900.yaml" in refused.stderr


@pytest.mark.parametrize("defect", sorted(STORY_222_DEFECTS))
def test_pre_flight_does_not_start_refusing_the_gap_reason_or_blocked_work_class(
        defect: str, planning: Planning):
    """story-222's classes are plan-time only, held the way story-177's is: a
    committed artifact carrying a gap with no reason, or work planned on a
    blocked path, gets past l5-run's story checks to the clean-tree refusal,
    which names the dirty path and not the artifact.

    Controls: the same text is refused through the composition l5-plan calls,
    so getting past pre-flight is the class being plan-time only rather than
    the artifact having nothing wrong with it; and the same command over a
    schema-invalid artifact is refused for the artifact."""
    install(planning, "story-900", stamped(DEFECTS[defect]))
    planning.git("checkout", "-q", "-b",
                 story_coordinator.story_branch(
                     harness_config.load_config(planning.root), "story-900"))
    (planning.root / "dirty.txt").write_text("developer's own\n", encoding="utf-8")

    result = run_script(L5_RUN, planning, "story-900")

    assert result.returncode == 1
    assert "dirty.txt" in result.stderr
    assert "story-900.yaml" not in result.stderr
    assert GAPS_LEFT not in result.stderr
    _, pointer, _ = STORY_222_DEFECTS[defect]
    assert pointer not in result.stderr

    assert any(problem.startswith(pointer)
               for problem in plan_time_problems(stamped(DEFECTS[defect])))
    install(planning, "story-900", DEFECTS["schema"])
    refused = run_script(L5_RUN, planning, "story-900")
    assert "story-900.yaml" in refused.stderr


def test_the_committed_artifacts_the_leniency_exists_for_do_carry_bare_entries():
    """The reason the class is plan-time only, held rather than remembered:
    artifacts already committed under this repository's stories directory
    carry do_not_modify entries with no reason, so a pre-flight that refused
    the class would refuse stories that have already run. Read as a subject
    — a claim about what this repository has committed — and the control is
    that the same reader finds a reasoned entry somewhere too, so a bare one
    is a distinct shape it can tell apart."""
    # Spelled with `joinpath`, as the sibling corpus modules spell it: the
    # guard in tests/test_artifact_schemas.py forbids joining the harness
    # root to ".harness" with the operator, because that is the only way a
    # test could reach the gitignored run directory.
    stories = HARNESS_ROOT.joinpath(".harness", "stories")
    bare, with_reason = set(), set()
    for path in sorted(stories.glob("story-*.yaml")):
        parsed = story_coordinator.read_story(
            path.read_text(encoding="utf-8")).parsed
        if parsed is None:
            continue
        entries = (parsed.get("scope") or {}).get("do_not_modify") or []
        for entry in entries:
            if not isinstance(entry, str):
                continue
            (with_reason if plan_validation.carries_a_reason(entry) else bare).add(
                path.name)
    assert bare, "no committed artifact carries a bare entry"
    assert with_reason, "no committed artifact carries a reasoned entry"


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


def test_the_schema_declares_the_entry_form_and_the_plan_time_refusal():
    """The shipped schema as the subject: its description of
    scope.do_not_modify states the form an entry takes, that the reason is
    the developer's decision recorded for a later reader, that an entry
    carrying none is refused at plan time, and that artifacts committed
    before the rule are not held to it. The items stay strings, which is the
    constraint that keeps every committed artifact parsing."""
    schema = json.loads((HARNESS_ROOT / "schemas" / "story.schema.json")
                        .read_text(encoding="utf-8"))
    field = schema["properties"]["scope"]["properties"]["do_not_modify"]
    assert field["items"] == {"type": "string"}
    description = " ".join(field["description"].lower().split())
    assert "closing parenthetical" in description
    assert "<path or area> (<" in description
    assert "developer's decision" in description
    assert "later reader" in description
    assert "refused at plan time" in description
    assert re.search(r"committed before this rule are not held to it", description)
    assert re.search(r"pre-flight does not refuse", description)


def test_the_plan_time_checks_the_prompt_lists_include_the_reason_rule():
    """The sentence listing what l5-plan checks when the session ends names
    the reason rule beside the strictness rule, in the same step."""
    prompt = PLANNER_PROMPT.read_text(encoding="utf-8")
    step = re.search(r"(?s)l5-plan validates.*?\n\n", prompt).group(0)
    assert "scope the restriction to creation" in step
    assert re.search(r"reason rule", step)
    assert re.search(r"do_not_modify entry carrying no reason", step)
    assert re.search(r"closing\s+parenthetical", step)
    assert "blocks the commit" in step
    # Control: the pre-story text of the same file says none of it.
    old = show("prompts/planner.md")
    assert "reason rule" not in old
    assert "do_not_modify entry carrying no reason" not in old


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
    """The composition and the decision live in orchestration.

    The script named `read_story` nowhere at all until story-097, which gave
    the approval prompt and the run offer their subject and so had to read each
    artifact's title. That is a read of a story and not a validation of one, so
    what is asserted here is the claim the absence stood for rather than the
    absence: no reader of a story artifact is reimplemented or reached for
    directly, the one reading that happens goes through the coordinator's own
    reader, and it is there to derive a title rather than to compose a check
    `plan_validation` owns. Held shut from both sides — a second reading, one
    that does not go through the coordinator, or one whose result does not
    reach the bounded-title function, each fails here.
    """
    source = L5_PLAN.read_text(encoding="utf-8")
    assert "plan_validation.artifact_problems" in source
    assert "strictness_problems" not in source
    assert "stage_exception_problems" not in source
    assert "story_parser" not in source
    assert "schema_validator" not in source
    assert source.count("read_story") == 1
    assert "story_coordinator.read_story" in source
    assert "story_coordinator.story_title(" in source
    assert "story_coordinator.refuse_bad_story" in source


def test_scripts_l5_plan_writes_nothing_and_removes_nothing_on_a_refusal():
    """Searched for, since a repair or a deletion would be code in the script.

    Scoped to `report`, the function that validates and refuses, rather than to
    the whole script. It was the whole script while the script wrote and
    removed nothing anywhere; since story-076 phase one writes a transcript and
    removes its own answer file, neither of which is on this path, so a
    whole-file scan would report the wrong subject.
    """
    source = conftest.function_source(
        L5_PLAN.read_text(encoding="utf-8"), "report")
    for pattern in (r"\.unlink\(", r"\.write_text\(", r"shutil\.", r"os\.remove"):
        assert not re.search(pattern, source), pattern
    assert re.search(r"\.unlink\(", f"{source}\npath.unlink()\n")
