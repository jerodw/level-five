"""story-016 validation: the coordinator's shape contracts really bite.

story-016 replaces six equality-with-a-historical-implementation tests with
direct assertions about the shapes the coordinator writes, in
`tests/test_coordinator_contract.py`. The one thing that must be true of a
replacement like that is the thing the story says it exists to remove: an
assertion that passes regardless of what the coordinator writes. So this
file does not read the new assertions and agree with them — it violates each
shape *in the coordinator itself* and runs the contract file against the
result, asserting the named contract goes red.

Mutation works on a throwaway repo: `orchestration/` is copied and edited,
every other top-level path is symlinked back at the real one, and pytest
runs there. Nothing under `orchestration/` in this repository is written.

The rest is static: that the six comparisons and the machinery that served
only them are gone, that nothing under `tests/` still loads a coordinator
out of git history, and that every surviving assertion in
`tests/test_story_011_validation.py` is the one that was there before.
"""
import ast
import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import story_coordinator

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"
CONTRACT_FILE = TESTS_DIR / "test_coordinator_contract.py"
STORY_011_FILE = TESTS_DIR / "test_story_011_validation.py"

#: The functions that read a coordinator implementation out of git history.
#: A test may call these for their text — the surviving prompt-scope
#: assertion resolves its baseline through them — but no test may hand the
#: result to a module loader.
HISTORY_SOURCE_READERS = {"coordinator_source_at", "pre_story_coordinator_source"}
MODULE_LOADERS = {"load_variant", "spec_from_file_location", "exec_module",
                  "module_from_spec", "exec"}


# --------------------------------------------------------------------------
# Running the contract file against a mutated coordinator
# --------------------------------------------------------------------------


def mutant_repo(tmp_path: Path, replacements: list[tuple[str, str]]) -> Path:
    """A repo whose `orchestration/` is a mutated copy of this one's.

    Everything else — `workflows/`, `schemas/`, `prompts/`, `.git` — is
    symlinked at the real thing, so the copy is cheap and the contract file
    reads the same workflow definition it reads here.
    """
    root = tmp_path / "mutant-repo"
    root.mkdir()
    skip = {"orchestration", "tests", ".venv", ".harness", "__pycache__"}
    for entry in REPO_ROOT.iterdir():
        if entry.name not in skip:
            (root / entry.name).symlink_to(entry)
    shutil.copytree(REPO_ROOT / "orchestration", root / "orchestration",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (root / "tests").mkdir()
    for name in ("conftest.py", CONTRACT_FILE.name):
        shutil.copy(TESTS_DIR / name, root / "tests" / name)

    path = root / "orchestration" / "story_coordinator.py"
    source = path.read_text(encoding="utf-8")
    for old, new in replacements:
        assert old in source, f"the coordinator no longer contains: {old!r}"
        source = source.replace(old, new, 1)
    path.write_text(source, encoding="utf-8")
    return root


def run_contract(root: Path, names: tuple[str, ...] = ()) -> tuple[int, str]:
    """Run the contract file in `root`; return its exit code and output."""
    command = [sys.executable, "-m", "pytest", f"tests/{CONTRACT_FILE.name}",
               "-q", "-p", "no:cacheprovider"]
    if names:
        command += ["-k", " or ".join(names)]
    # Pin the width pytest formats its summary to. Left to the environment it
    # is the developer's terminal locally and the runner's in CI, so the same
    # failure prints differently in the two places and anything reading this
    # output diverges between them. A fixed wide value makes a local run print
    # what CI prints.
    env = {**os.environ, "COLUMNS": "240"}
    result = subprocess.run(command, cwd=root, capture_output=True, text=True,
                            env=env)
    return result.returncode, result.stdout + result.stderr


def failing_tests(output: str) -> set[str]:
    """The tests pytest's summary reports as failed, by name.

    The node id is taken up to the first space, because pytest appends
    ` - <message>` to the summary line only when the terminal is wide enough
    to hold it. Parsing to end-of-line reads the name plus the message on a
    wide terminal and the bare name on a narrow one, so a test asserting on
    these names passes locally and fails in CI purely on terminal width.
    """
    return {
        line.split("::", 1)[1].split("[", 1)[0].split(" ", 1)[0].strip()
        for line in output.splitlines()
        if line.startswith("FAILED") and "::" in line
    }


#: Each entry violates one shape the contract names, and names the contract
#: tests that must go red — the failure has to be the one the shape predicts,
#: not any failure. Keyed by what the violation is, because that is what a
#: reader of a failure here needs to know.
VIOLATIONS = {
    "a separator inserted into the events.log line format": (
        [('log.write(f"[{stamp}] {message}\\n")',
          'log.write(f"[{stamp}] :: {message}\\n")')],
        ("test_every_events_log_line_matches_the_frozen_format",),
    ),
    "a field added to RunState": (
        [("    artifacts: list[str] = field(default_factory=list)",
          "    artifacts: list[str] = field(default_factory=list)\n"
          '    mood: str = "hopeful"')],
        ("test_state_json_holds_the_fields_run_state_declares",),
    ),
    "a field written to state.json that RunState does not declare": (
        [("json.dumps(asdict(state), indent=2)",
          'json.dumps(asdict(state) | {"mood": "hopeful"}, indent=2)')],
        ("test_state_json_holds_the_fields_run_state_declares",),
    ),
    "a field written to state.json with the wrong type": (
        [("json.dumps(asdict(state), indent=2)",
          'json.dumps(asdict(state) | {"retry_count": str(state.retry_count)}, '
          "indent=2)")],
        ("test_state_json_holds_the_fields_run_state_declares",),
    ),
    "a status outside the pinned set": (
        [('    state.status = "completed"', '    state.status = "finished"')],
        ("test_a_completed_run_ends_completed",
         "test_the_coordinator_writes_no_status_outside_the_pinned_set"),
    ),
    "a changed timestamp format in the events.log line": (
        [('stamp = time.strftime("%Y-%m-%d %H:%M:%S")',
          'stamp = time.strftime("%Y-%m-%dT%H:%M:%S")')],
        ("test_every_events_log_line_matches_the_frozen_format",),
    ),
    "an escalation summary whose heading stops naming the story": (
        [('f"# {state.story_id} Escalation Summary\\n\\n"',
          'f"# Escalation Summary\\n\\n"')],
        ("test_the_escalation_summary_holds_its_five_parts",),
    ),
    "an escalation summary that stops reporting the escalated status": (
        [('f"## Status\\nEscalated\\n\\n"', 'f"## Status\\n\\n"')],
        ("test_the_escalation_summary_holds_its_five_parts",),
    ),
    "an escalation summary that stops pointing at the verification directory": (
        [('f"verification/ directory for verifier findings.\\n"',
          'f"the verifier findings.\\n"')],
        ("test_the_escalation_summary_holds_its_five_parts",),
    ),
    "an escalation summary with no reason": (
        [('f"## Reason\\n{reason}\\n\\n"', '""')],
        ("test_the_escalation_summary_holds_its_five_parts",),
    ),
    "an escalation summary that drops the retry count": (
        [('f"retry count: {state.retry_count}\\n\\n"', 'f"\\n\\n"')],
        ("test_the_escalation_summary_holds_its_five_parts",),
    ),
    "a required artifact a completed run stops writing": (
        [('(run_dir / "completion-report.md").write_text(report, encoding="utf-8")',
          "report  # deliberately not written")],
        ("test_a_completed_run_produces_the_artifacts_it_must",),
    ),
}


@pytest.fixture(scope="session")
def control_repo(tmp_path_factory) -> Path:
    """An unmutated copy, to show the harness itself is not what fails."""
    return mutant_repo(tmp_path_factory.mktemp("control"), [])


def test_the_contract_file_passes_against_an_unmutated_coordinator(control_repo):
    """Attribution: every failure below is the mutation, not the harness."""
    code, output = run_contract(control_repo)
    assert code == 0, output


@pytest.mark.parametrize("violation", sorted(VIOLATIONS))
def test_the_contract_fails_when_the_shape_it_names_is_violated(
    violation, tmp_path,
):
    """The defect this story exists to remove would be an assertion that
    passes regardless of what the coordinator writes. Violate each shape in
    the coordinator and the contract that names it goes red."""
    replacements, expected = VIOLATIONS[violation]
    root = mutant_repo(tmp_path, replacements)
    code, output = run_contract(root, expected)
    assert code != 0, f"{violation}: the contract passed anyway\n{output}"
    assert "no tests ran" not in output, f"{violation}: selected nothing\n{output}"
    failed = failing_tests(output)
    assert failed & set(expected), (
        f"{violation}: the run went red, but not at the contract that names "
        f"the shape — failures were {sorted(failed)}\n{output}"
    )


def test_the_contract_holds_when_a_run_gains_an_artifact_and_an_event(tmp_path):
    """The permission the story exists to grant, driven from the coordinator
    rather than from a file the test drops into the run directory: story-012
    adds retry-history.json and story-014 adds clean-clone-result.json, and
    neither may fail an assertion about artifacts it has nothing to do with."""
    root = mutant_repo(tmp_path, [(
        '(run_dir / "completion-report.md").write_text(report, encoding="utf-8")',
        '(run_dir / "completion-report.md").write_text(report, encoding="utf-8")\n'
        '    (run_dir / "a-later-storys-artifact.json").write_text(\n'
        '        "{}\\n", encoding="utf-8")\n'
        '    append_event(run_dir, "a later story wrote its artifact", kind="note")',
    )])
    code, output = run_contract(root)
    assert code == 0, output


# --------------------------------------------------------------------------
# The removals took only what was unreachable
# --------------------------------------------------------------------------


REMOVED_TESTS = [
    "test_events_log_lines_are_byte_identical_to_the_pre_story_format",
    "test_the_legacy_run_wrote_no_history_and_this_one_did",
    "test_l5_status_renders_a_run_identically_before_and_after",
    "test_l5_status_through_the_script_is_unchanged",
    "test_state_json_is_identical_to_the_pre_story_run",
    "test_the_escalation_summary_is_unchanged",
]
REMOVED_HELPERS = ["both_implementations", "legacy_coordinator", "clone_target",
                   "normalize_timestamps", "SHAPES"]


def test_files_under_tests_parse(tmp_path):
    """Guards every static check below: they read ASTs, so a file that does
    not parse must be a failure here rather than a silent skip there."""
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@pytest.mark.parametrize("name", REMOVED_TESTS)
def test_the_six_comparison_tests_are_gone(name):
    assert name not in STORY_011_FILE.read_text(encoding="utf-8")


def top_level_names(source: str) -> set[str]:
    """What a module defines at the top level: functions, classes, constants."""
    names = set()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets
                         if isinstance(target, ast.Name))
    return names


@pytest.mark.parametrize("name", REMOVED_HELPERS)
def test_the_helpers_that_served_only_them_are_gone(name):
    """They had no other caller, so they go with the tests they served —
    anywhere under tests/, not just in the file they lived in. Defined, not
    mentioned: this file names them to check for them."""
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        assert name not in top_level_names(path.read_text(encoding="utf-8")), path.name


def loader_call_arguments(tree: ast.AST) -> list[ast.AST]:
    """Every argument handed to something that turns source into a module."""
    arguments: list[ast.AST] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(
            func, "id", None)
        if name in MODULE_LOADERS:
            arguments.extend(node.args)
            arguments.extend(keyword.value for keyword in node.keywords)
    return arguments


def test_no_module_under_tests_loads_a_coordinator_out_of_git_history():
    """By search, not by inspection: nothing hands a historical source to a
    module loader. Reading the old source as *text* stays legal — the
    surviving prompt-scope assertion resolves its baseline that way — but
    running it is what has a shelf life."""
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for argument in loader_call_arguments(tree):
            names = {node.id for node in ast.walk(argument)
                     if isinstance(node, ast.Name)} | {
                node.attr for node in ast.walk(argument)
                if isinstance(node, ast.Attribute)}
            leaked = names & HISTORY_SOURCE_READERS
            assert not leaked, f"{path.name} loads {sorted(leaked)} as a module"


def test_the_baseline_resolution_the_prompt_scope_assertion_needs_is_intact():
    """Only the unreachable went. The resolution and the tests guarding it
    stay, because the surviving scope assertion depends on them."""
    source = STORY_011_FILE.read_text(encoding="utf-8")
    for name in ("def pre_story_revision", "def coordinator_source_at",
                 "def story_revision", "def pre_story_coordinator_source",
                 "def test_the_comparison_baseline_is_not_this_implementation",
                 "def test_the_baseline_stays_pre_story_once_this_story_is_committed",
                 "def test_the_baseline_resolution_fails_loudly_when_there_is_nothing_older",
                 "def test_no_prompt_template_was_changed_by_this_story"):
        assert name in source, name


def test_the_surviving_prompt_scope_assertion_still_passes():
    result = subprocess.run(
        [sys.executable, "-m", "pytest", f"tests/{STORY_011_FILE.name}", "-q",
         "-p", "no:cacheprovider", "-k",
         "no_prompt_template_was_changed or baseline"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no tests ran" not in result.stdout


# --------------------------------------------------------------------------
# Everything that survived is what was there before
# --------------------------------------------------------------------------


def strip_docstrings(tree: ast.AST) -> ast.AST:
    """A copy with docstrings dropped, so prose edits are not code edits."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return tree


def functions_of(source: str) -> dict[str, str]:
    """Every top-level function, as a docstring-insensitive AST dump."""
    tree = strip_docstrings(ast.parse(source))
    return {
        node.name: ast.dump(node)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def story_011_file_at(revision: str) -> str:
    """The story-011 validation file's text at one revision."""
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show",
         f"{revision}:tests/{STORY_011_FILE.name}"],
        capture_output=True, text=True, check=True,
    ).stdout


@functools.lru_cache(maxsize=None)
def story_011_before_this_story() -> str:
    """The file as it stood before this story edited it.

    Not `HEAD`. The coordinator commits the working tree at the end of a
    successful run, so a `HEAD` baseline becomes *this* story's own file the
    moment the story commits — the comparisons below would then compare the
    file against itself, `gone` would be empty, and the diff assertions would
    pass while proving nothing. That is not hypothetical: this story's first
    attempt resolved the baseline as `HEAD`, passed in the working tree, and
    failed in a clean clone with the story committed into it.

    So walk the file's own history newest-first and take the first revision
    whose blob still carries the comparison tests this story removed, which
    is the newest revision predating this story's edit — a search that
    survives a rebase or a squash merge, which a pinned SHA would not.
    """
    revisions = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "log", "--format=%H", "--",
         f"tests/{STORY_011_FILE.name}"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    for revision in revisions:
        source = story_011_file_at(revision)
        if all(name in source for name in REMOVED_TESTS):
            return source
    raise AssertionError(
        "no committed revision of tests/test_story_011_validation.py still "
        "carries the comparison tests this story removed; the diff assertions "
        "have nothing to compare against"
    )


def synthetic_history(root: Path, revisions: list[str]) -> Path:
    """A throwaway git repo holding one commit per given file text.

    Enough of a repository for `story_011_before_this_story` to walk: the
    resolution reads `git log` and `git show` for one path, nothing else.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir()
    path = root / "tests" / STORY_011_FILE.name
    run = functools.partial(subprocess.run, cwd=root, check=True,
                            capture_output=True, text=True)
    run(["git", "init", "-q"])
    for index, text in enumerate(revisions):
        path.write_text(text, encoding="utf-8")
        run(["git", "add", "-A"])
        run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
             "-m", f"revision {index}"])
    return root


#: The two states of the file, reduced to what the resolution keys on.
WITH_COMPARISONS = "".join(
    f"def {name}(both_implementations):\n    assert True\n\n"
    for name in REMOVED_TESTS)
WITHOUT_COMPARISONS = "def test_no_prompt_template_was_changed_by_this_story():\n    pass\n"


@pytest.fixture
def resolution_against(monkeypatch):
    """Point the baseline resolution at a synthetic history and call it."""
    def resolve(root: Path) -> str:
        monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", root)
        monkeypatch.setattr(sys.modules[__name__], "STORY_011_FILE",
                            root / "tests" / STORY_011_FILE.name)
        story_011_before_this_story.cache_clear()
        try:
            return story_011_before_this_story()
        finally:
            story_011_before_this_story.cache_clear()
    yield resolve
    story_011_before_this_story.cache_clear()


def test_the_baseline_walks_past_this_storys_own_commit(tmp_path,
                                                        resolution_against):
    """The failure this attempt exists for, reproduced in miniature.

    A `HEAD` baseline returns the newest commit, which once this story is
    committed is this story's own file — `before` and `after` become the same
    text, every diff assertion below goes vacuous, and the suite passes in the
    working tree while failing in a clean clone. Given a history whose newest
    commit is the post-story file, the resolution must return the *older* one.
    """
    root = synthetic_history(tmp_path / "history",
                             [WITH_COMPARISONS, WITHOUT_COMPARISONS])
    resolved = resolution_against(root)
    head = (root / "tests" / STORY_011_FILE.name).read_text(encoding="utf-8")
    assert resolved != head, "the baseline resolved to this story's own file"
    assert resolved == WITH_COMPARISONS
    for name in REMOVED_TESTS:
        assert f"def {name}" in resolved, name


def test_the_baseline_walks_past_every_later_commit_not_just_one(
    tmp_path, resolution_against,
):
    """A rebase, a squash merge or a follow-up edit adds commits after this
    story's. The resolution searches rather than stepping back a fixed
    distance, so more commits on top must not move the answer."""
    root = synthetic_history(
        tmp_path / "history",
        [WITH_COMPARISONS, WITHOUT_COMPARISONS,
         WITHOUT_COMPARISONS + "# a later story edited this file\n",
         WITHOUT_COMPARISONS + "# and another\n"])
    assert resolution_against(root) == WITH_COMPARISONS


def test_a_head_baseline_would_make_the_diff_assertions_vacuous(tmp_path,
                                                                resolution_against):
    """Why the two tests above are worth their runtime, shown rather than
    argued: over the same history, the removed-test set the diff assertions
    compute is empty under a `HEAD` baseline and complete under this one. An
    assertion that computes an empty difference asserts nothing."""
    root = synthetic_history(tmp_path / "history",
                             [WITH_COMPARISONS, WITHOUT_COMPARISONS])
    after = functions_of((root / "tests" / STORY_011_FILE.name)
                         .read_text(encoding="utf-8"))

    head_baseline = story_011_file_at_in(root, "HEAD")
    assert set(functions_of(head_baseline)) - set(after) == set()

    gone = set(functions_of(resolution_against(root))) - set(after)
    assert set(REMOVED_TESTS) <= gone


def story_011_file_at_in(root: Path, revision: str) -> str:
    """`story_011_file_at`, against a repository other than this one."""
    return subprocess.run(
        ["git", "-C", str(root), "show", f"{revision}:tests/{STORY_011_FILE.name}"],
        capture_output=True, text=True, check=True,
    ).stdout


def test_the_baseline_resolution_refuses_rather_than_comparing_nothing(
    tmp_path, resolution_against,
):
    """When no revision carries the removed tests there is nothing to compare
    against. It must say so, not return the newest file and let the diff
    assertions pass against themselves."""
    root = synthetic_history(tmp_path / "history", [WITHOUT_COMPARISONS])
    with pytest.raises(AssertionError, match="nothing to compare"):
        resolution_against(root)


def test_the_baseline_is_not_this_storys_own_file():
    """The positive guard the `HEAD` failure mode demands: the resolved
    baseline really is the older file, whether or not this story is
    committed yet."""
    before = story_011_before_this_story()
    assert before != STORY_011_FILE.read_text(encoding="utf-8")
    for name in REMOVED_TESTS:
        assert f"def {name}" in before, name


def test_every_surviving_assertion_in_story_011_is_unchanged():
    """Read the diff, not the summary: every function that did not take the
    historical coordinator is byte-identical in its code, so no remaining
    assertion's subject or strictness moved."""
    before = functions_of(story_011_before_this_story())
    after = functions_of(STORY_011_FILE.read_text(encoding="utf-8"))
    depended_on_history = {
        name: dump for name, dump in before.items()
        if "legacy_coordinator" in dump or "both_implementations" in dump
    }
    moved = [
        name for name, dump in after.items()
        if name in before and name not in depended_on_history and dump != before[name]
    ]
    assert moved == [], moved


def test_the_functions_that_did_change_are_only_those_that_took_the_baseline():
    """The converse: nothing was removed or re-pointed that did not depend on
    the historical coordinator."""
    before = functions_of(story_011_before_this_story())
    after = functions_of(STORY_011_FILE.read_text(encoding="utf-8"))
    gone = set(before) - set(after)
    for name in gone:
        assert ("legacy_coordinator" in before[name]
                or "both_implementations" in before[name]
                or name in REMOVED_HELPERS), name
    assert set(REMOVED_TESTS) <= gone


def test_the_named_survivors_are_present_and_unchanged():
    """The three the acceptance criteria name outright."""
    before = functions_of(story_011_before_this_story())
    after = functions_of(STORY_011_FILE.read_text(encoding="utf-8"))
    for name in ("test_every_log_line_has_one_history_entry_in_the_same_order",
                 "test_the_retried_run_records_both_attempts_in_one_stream",
                 "test_the_history_a_run_produced_validates_against_the_schema"):
        assert name in after, name
        assert after[name] == before[name], name


# --------------------------------------------------------------------------
# The new file is a contract, not a story record
# --------------------------------------------------------------------------


def test_the_contract_file_exists_and_is_not_named_for_a_story():
    assert CONTRACT_FILE.is_file()
    assert "story" not in CONTRACT_FILE.stem.replace("story_coordinator", "")


def test_the_state_field_set_is_read_from_the_dataclass_not_typed_out():
    """By search: the contract derives the field set from `RunState` itself,
    so it cannot silently disagree with the definition it describes. The
    mutation above shows it behaves that way; this shows it is written that
    way."""
    tree = ast.parse(CONTRACT_FILE.read_text(encoding="utf-8"))
    reads_dataclass = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "fields"
        and any(getattr(argument, "attr", None) == "RunState"
                for argument in node.args)
    ]
    assert reads_dataclass, "the contract does not read dataclasses.fields(RunState)"


def test_the_run_directory_contract_is_a_subset_check_not_an_equality():
    """The constraint the story states outright: an exact set reproduces the
    friction it removes, one story later."""
    source = CONTRACT_FILE.read_text(encoding="utf-8")
    assert "required_artifacts() <= present" in source
    assert "required_artifacts() == present" not in source
