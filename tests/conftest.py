import ast
import importlib.machinery
import importlib.util
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "orchestration"))


# --------------------------------------------------------------------------
# The one honest baseline resolution the per-story validation files share.
#
# A per-story assertion that its story left some path alone must not be
# written as `git diff HEAD -- <path>`. That asks whether the working tree is
# dirty there, which is a question about whoever is working *now*: the
# coordinator commits the working tree in `_complete`, so on the finished
# branch the answer is "clean" for every path in the repository and the
# assertion holds no matter what the story did.
#
# Resolve the story's own run commit instead — the commit that added that
# story's validation file — and bound the comparison at both ends: that
# commit against its parent. The answer survives a commit, a rebase and a
# squash merge, because it is a search through the file's own history rather
# than a pinned SHA or a fixed distance back from HEAD. While the story is
# still in flight the file has no adding commit, and the working tree
# against HEAD *is* the correct pre-story baseline.
#
# The repository parameter exists so this same code path can be exercised
# against a synthetic history in which the story is already committed — the
# condition this repository cannot be in while these tests decide whether it
# commits.
# --------------------------------------------------------------------------


class NothingToCompareAgainst(RuntimeError):
    """The history does not reach the commit that added the validation file.

    Raised rather than degrading to a baseline that would make the caller's
    assertion vacuous: a comparison with nothing to compare against is worse
    than no comparison, because it reports green.
    """


@dataclass(frozen=True)
class StoryRange:
    """The commit range one story's own change occupies.

    `endpoint` is None while the story is still uncommitted, in which case
    the end of the range is the working tree.
    """

    baseline: str
    endpoint: str | None

    @property
    def committed(self) -> bool:
        return self.endpoint is not None

    def diff_command(self, repo: Path, *options: str) -> list[str]:
        revisions = [self.baseline] if self.endpoint is None else [self.baseline,
                                                                  self.endpoint]
        return ["git", "-C", str(repo), "diff", *revisions, *options]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
    )


# --------------------------------------------------------------------------
# Which story a validation module validates, declared rather than inferred
#
# The resolution above identifies a story by the commit that *added* the
# module validating it, which reads the module's current path. That is
# exactly right until the module is renamed: a rename commit adds the new
# path, so every differential assertion in the module would then compare the
# rename against its parent, find nothing, and pass. Vacuously green is the
# defect class story-015, story-016, story-026, story-029 and story-031 each
# removed, and a rename is not worth reintroducing it.
#
# So a module may *declare* the historical path whose add-commit identifies
# its story, and the resolution consults the declaration instead of the
# module's name. A declaration rather than a derivation, deliberately:
#
#   - it survives every future rename, because nothing about it is derived
#     from the name;
#   - it survives a module merged from two stories, which no rename
#     heuristic does — such a module declares both origins and each call
#     says which one it means;
#   - and `git log --follow`, the derivation that was weighed against it,
#     is documented as a heuristic on a single path. It recovers the
#     add-commit after a pure rename and fails on a content-editing rename
#     and on a split — silently, into the same green this exists to avoid.
#
# A module with no entry resolves its own path, exactly as before, so a
# module written after this costs nothing to declare.
# --------------------------------------------------------------------------


STORY_ORIGINS: dict[str, tuple[str, ...]] = {
    "test_changed_files_records.py": ("tests/test_story_002_validation.py",),
    "test_artifact_schemas.py": ("tests/test_story_004_validation.py",),
    "test_schema_directed_parsing.py": ("tests/test_story_005_validation.py",),
    "test_single_story_reader.py": ("tests/test_story_006_single_reader.py",),
    "test_stage_output_ownership.py": ("tests/test_story_007_validation.py",),
    "test_planner_injection.py": ("tests/test_story_008_validation.py",
                                  "tests/test_story_009_validation.py"),
    "test_attempt_archiving.py": ("tests/test_story_010_validation.py",),
    "test_execution_history.py": ("tests/test_story_011_validation.py",),
    "test_retry_history.py": ("tests/test_story_012_validation.py",),
    "test_schema_inventory_location.py": ("tests/test_story_013_validation.py",),
    "test_clean_clone_check.py": ("tests/test_story_014_validation.py",
                                  "tests/test_story_033_validation.py"),
    "test_shared_baseline_resolution.py": ("tests/test_story_015_validation.py",),
    "test_contract_assertions_bite.py": ("tests/test_story_016_validation.py",),
    "test_revert_check.py": ("tests/test_story_017_validation.py",),
    "test_revert_baseline.py": ("tests/test_story_019_validation.py",),
    "test_escalation_resume.py": ("tests/test_story_020_validation.py",),
    "test_foreign_work_refusal.py": ("tests/test_story_021_validation.py",),
    "test_required_output_freshness.py": ("tests/test_story_022_validation.py",),
    "test_plan_commit.py": ("tests/test_story_023_validation.py",),
    "test_escalation_summary.py": ("tests/test_story_024_validation.py",),
    "test_plan_time_validation.py": ("tests/test_story_025_validation.py",),
    "test_baseline_resolution_is_single.py": ("tests/test_story_026_validation.py",),
    "test_rerun_refusal.py": ("tests/test_story_027_validation.py",),
    "test_retry_routing.py": ("tests/test_story_028_validation.py",),
    "test_git_history_loading_retired.py": ("tests/test_story_029_validation.py",),
    "test_branch_base.py": ("tests/test_story_030_validation.py",),
    "test_mutation_controls.py": ("tests/test_story_031_validation.py",),
    "test_plan_assignment_refusal.py": ("tests/test_story_032_validation.py",),
    "test_resume_guard.py": ("tests/test_story_034_validation.py",),
    "test_stage_tool_grants.py": ("tests/test_story_035_validation.py",),
    "test_self_routing_retry.py": ("tests/test_story_036_validation.py",),
    "test_stage_baseline.py": ("tests/test_story_037_validation.py",),
}


def declared_origins(validation_file: Path) -> tuple[str, ...]:
    """The origins `validation_file`'s module declares, if it declares any."""
    return STORY_ORIGINS.get(Path(validation_file).name, ())


def _origin_path(validation_file: Path, repo: Path,
                 origin: str | None) -> str:
    """The path whose add-commit identifies the story to resolve.

    Undeclared, that is the module's own path — the behaviour every module
    had before declarations existed. Declared once, it is the declaration.
    Declared several times it is the one the caller named, and a caller that
    named none is refused rather than silently given a lineage: a module
    merged from two stories has two answers, and picking one is how a
    comparison ends up bounded at the wrong story's commits.
    """
    declared = declared_origins(validation_file)
    if not declared:
        if origin is not None:
            raise NothingToCompareAgainst(
                f"{_relative_to(validation_file, repo)} declares no origin in "
                f"STORY_ORIGINS, so {origin} cannot be the one it meant"
            )
        return _relative_to(validation_file, repo)
    if origin is None:
        if len(declared) == 1:
            return declared[0]
        raise NothingToCompareAgainst(
            f"{Path(validation_file).name} declares {len(declared)} origins "
            f"({', '.join(declared)}), so this call must name which one it "
            f"means rather than being given one of them"
        )
    if origin not in declared:
        raise NothingToCompareAgainst(
            f"{Path(validation_file).name} declares {', '.join(declared)}, "
            f"not {origin}"
        )
    return origin


def story_commit_range(validation_file: Path,
                       repo: Path = HARNESS_ROOT,
                       origin: str | None = None) -> StoryRange:
    """The commit range of the story `validation_file`'s module validates.

    The run commit is the *oldest* commit that added the file identifying
    that story, so a later revert-and-restore cannot be mistaken for the
    story's own run, and a planning or hotfix commit on the same story —
    which modifies the file rather than adding it — is never returned.

    Which file identifies the story is `STORY_ORIGINS`' answer when the
    module declares one, and the module's own path otherwise.
    """
    relative = _origin_path(validation_file, repo, origin)
    log = _git(repo, "log", "--diff-filter=A", "--format=%H", "--", relative)
    if log.returncode != 0:
        raise NothingToCompareAgainst(
            f"git log failed for {relative} in {repo}: {log.stderr.strip()}"
        )
    additions = log.stdout.split()
    if not additions:
        if _committed(repo, relative):
            raise NothingToCompareAgainst(
                f"{relative} is committed in {repo} but this history does not "
                f"reach the commit that added it, so the comparison has "
                f"nothing to compare against"
            )
        return StoryRange(baseline="HEAD", endpoint=None)
    run_commit = additions[-1]
    parent = _git(repo, "rev-parse", "--verify", "--quiet", f"{run_commit}^")
    if parent.returncode != 0:
        raise NothingToCompareAgainst(
            f"the commit that added {relative} ({run_commit[:12]}) has no "
            f"parent in this history, so the comparison has nothing to "
            f"compare against"
        )
    return StoryRange(baseline=parent.stdout.strip(), endpoint=run_commit)


def story_diff(paths: list[str], *, validation_file: Path,
               repo: Path = HARNESS_ROOT, diff_filter: str | None = None,
               options: tuple[str, ...] = (),
               origin: str | None = None) -> str:
    """The diff `validation_file`'s own story made to `paths`.

    Empty output means the story left those paths alone. Callers assert on
    emptiness; `options` and `diff_filter` only shape what a non-empty
    result looks like and which change kinds it counts.
    """
    command = story_commit_range(validation_file, repo,
                                 origin).diff_command(repo, *options)
    if diff_filter is not None:
        command.append(f"--diff-filter={diff_filter}")
    command += ["--", *paths]
    return subprocess.run(
        command, capture_output=True, text=True, check=True,
    ).stdout


# --------------------------------------------------------------------------
# The one reader of a repository file's text at a bound
#
# Eleven modules under tests/ carried a private copy of `git show <rev>:<path>`
# — usually a `pre_story`/`at_story_endpoint` pair resolved through
# `story_commit_range` above. One question, eleven answers, and each copy was
# a place the both-ends bounding could be re-derived slightly differently;
# the architecture document records six repairs of exactly that.
#
# So the reader lives here, beside the resolution it is built on, and
# `tests/test_baseline_honesty.py` holds the suite to it: no module under
# tests/ other than this one invokes git for a repository file's text.
# --------------------------------------------------------------------------


BASELINE = "baseline"
ENDPOINT = "endpoint"


def _resolved_revision(*, revision: str | None, validation_file: Path | None,
                       bound: str | None, repo: Path,
                       origin: str | None = None) -> str | None:
    """The revision a caller named, directly or as one end of a story's range.

    Returns None only for the endpoint of a story still in flight, which has
    no commit yet and whose correct answer is the working tree.
    """
    if revision is not None:
        if validation_file is not None or bound is not None:
            raise TypeError("name a revision or a story bound, not both")
        return revision
    if validation_file is None or bound is None:
        raise TypeError("name either a revision or a validation file and bound")
    if bound not in (BASELINE, ENDPOINT):
        raise ValueError(f"bound must be {BASELINE!r} or {ENDPOINT!r}")
    return getattr(story_commit_range(validation_file, repo, origin), bound)


def repository_file_at(relative: str, *, revision: str | None = None,
                       validation_file: Path | None = None,
                       bound: str | None = None,
                       repo: Path = HARNESS_ROOT,
                       origin: str | None = None) -> str:
    """One repository file's text, at a revision or at a story's own bound.

    `bound=BASELINE` is the parent of the commit that added `validation_file`;
    `bound=ENDPOINT` is that commit itself, falling back to the working tree
    while the story is still in flight, which is the only moment the working
    tree is that story's endpoint. Reading an endpoint from the working tree
    at any other moment is the trap the architecture document records under
    the HEAD-baseline bullets, and it is written here once so no caller can
    re-derive it wrongly.
    """
    resolved = _resolved_revision(revision=revision,
                                  validation_file=validation_file,
                                  bound=bound, repo=repo, origin=origin)
    if resolved is None:
        return (Path(repo) / relative).read_text(encoding="utf-8")
    result = _git(repo, "show", f"{resolved}:{relative}")
    if result.returncode != 0:
        raise NothingToCompareAgainst(
            f"{relative} cannot be read at {resolved} in {repo}: "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def function_source(source: str, name: str) -> str:
    """One top-level function's own text, decorators included.

    Decorators are part of what a comparison of a function's source is about —
    `_escalate` carries one that decides when its work is committed — so they
    are included, as `inspect.getsource` includes them.
    """
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            lines = source.splitlines(keepends=True)
            return "".join(lines[first - 1:node.end_lineno])
    raise AssertionError(f"{name} is not defined at the top level of this source")


def function_source_at(relative: str, name: str, *, revision: str | None = None,
                       validation_file: Path | None = None,
                       bound: str | None = None,
                       repo: Path = HARNESS_ROOT,
                       origin: str | None = None) -> str:
    """A named function's source, read out of a file's text at a bound.

    The text half of what a differential test used to get by loading the file
    as a module: comparing what a function *says* needs no running module, and
    a module recovered out of history stops running as soon as anything it
    imports changes shape.
    """
    return function_source(
        repository_file_at(relative, revision=revision,
                           validation_file=validation_file, bound=bound,
                           repo=repo, origin=origin),
        name)


# --------------------------------------------------------------------------
# The two loaders, and the only place under tests/ that builds a module
# --------------------------------------------------------------------------


def load_mutant(source_path: Path, replacements: Sequence[tuple[str, str]], *,
                name: str, tmp_path: Path):
    """A working-tree module with named substitutions applied, as its own module.

    Deliberately takes a *path in the working tree* and the substitutions to
    make in it, rather than arbitrary source text. Every mutation-loading
    caller under tests/ mutates today's code to show a check can fail, and
    taking a path means source recovered out of git history is not a value
    this helper naturally accepts — the practice being retired cannot come
    back through it without a caller reading the history itself, which
    `tests/test_baseline_honesty.py` reports.

    Each `old` must occur, so a mutation whose anchor has moved fails as
    itself rather than as a mutant that silently changed nothing.
    """
    source = source_path.read_text(encoding="utf-8")
    for old, new in replacements:
        assert old in source, (name, old)
        source = source.replace(old, new, 1)
    module_path = Path(tmp_path) / f"{name}.py"
    module_path.write_text(source, encoding="utf-8")
    return _load_module(name, module_path)


def load_script(script_name: str, *, name: str | None = None):
    """One of `scripts/`'s extensionless entry points, as a module.

    They have no `.py` suffix, so they cannot be imported; a loader named
    explicitly is the only way to call `main` or `report` directly. One
    helper rather than one per module, for the reason the reader above is
    shared.
    """
    module_name = name or f"{script_name.replace('-', '_')}_under_test"
    return _load_module(module_name, HARNESS_ROOT / "scripts" / script_name)


def _load_module(name: str, path: Path):
    """Build and execute a module. The one construction site under tests/.

    Registered in `sys.modules` before execution because `@dataclass` resolves
    a field's annotations through `sys.modules[cls.__module__]`, which is None
    for a module that has been created but never registered; removed
    afterwards so the real module of that name is never shadowed.
    """
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def _relative_to(path: Path, repo: Path) -> str:
    resolved = Path(path)
    if resolved.is_absolute():
        return resolved.resolve().relative_to(Path(repo).resolve()).as_posix()
    return resolved.as_posix()


def _committed(repo: Path, relative: str) -> bool:
    return _git(repo, "cat-file", "-e", f"HEAD:{relative}").returncode == 0

STORY = """\
story:
  id: story-001
  title: Sample story for coordinator tests
  description: |
    A stand-in story used to exercise the workflow deterministically.

tasks:
  - do the sample work

acceptance_criteria:
  - the sample behavior exists
  - existing behavior is preserved

scope:
  modify:
    - src/
  do_not_modify:
    - rules/

verification_requirements:
  - confirm the sample behavior

constraints:
  - preserve existing behavior
"""

CONFIG = """\
project: sample-target
workflow: story-workflow
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: echo tests-ok
"""


@pytest.fixture
def target_root(tmp_path: Path) -> Path:
    root = tmp_path / "sample-target"
    for sub in (".harness/standards", ".harness/stories", ".harness/runs", ".harness/logs", ".harness/docs", "src"):
        (root / sub).mkdir(parents=True)
    (root / ".harness" / "config.yaml").write_text(CONFIG, encoding="utf-8")
    (root / ".harness" / "stories" / "story-001.yaml").write_text(STORY, encoding="utf-8")
    (root / ".harness" / "standards" / "coding.md").write_text("# Coding Standards\n- keep it simple\n", encoding="utf-8")
    (root / ".harness" / "standards" / "testing.md").write_text("# Testing Standards\n- test everything\n", encoding="utf-8")
    (root / ".harness" / "docs" / "ARCHITECTURE.md").write_text("# Sample Architecture\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    return root


def commit_setup(root: Path, message: str = "setup for this test") -> None:
    """Commit what a test set up in a target repository after building it.

    story-021 added a clean-tree pre-flight: `run_story` refuses a fresh run
    whose target tree already holds work no stage produced, naming the paths,
    because a run commits the tree it ends on and so has to start from one it
    can account for. A test's own setup — the story artifact it installs, the
    config key it overrides — is part of the repository the run starts *from*,
    not something the run produced, so committing it is exactly what the
    refusal asks for and leaves every assertion pointed where it was.

    It lives here rather than being copied per module for the same reason the
    baseline resolution above does: one home for one fact.
    """
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", message],
                   cwd=root, check=True)



def first_retry_route(workflow: dict) -> tuple[str, str]:
    """The first retry category a workflow declares, and where it routes.

    story-028 replaced the verifier's constant `on_failure.retry_stage` with
    a category-keyed routing table, so a verdict recommending a retry has to
    name a category for the coordinator to route it on. A test that drives a
    retry reads the pair off the loaded workflow here rather than writing its
    own derivation of it, for the same reason the baseline resolution above
    lives here: one home for one fact.
    """
    for stage in workflow["stages"]:
        routes = stage.get("on_failure", {}).get("retry_routing", {})
        for category, route in routes.items():
            return category, route["stage"]
    raise AssertionError("the loaded workflow declares no retry routes")


@pytest.fixture
def harness_root() -> Path:
    return HARNESS_ROOT
