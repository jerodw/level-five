import ast
import importlib.machinery
import importlib.util
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "orchestration"))

import harness_config  # noqa: E402


# --------------------------------------------------------------------------
# Loading the shipped workflow the way a run loads it.
#
# A workflow declaration may reference the target's configuration -- the
# implementer's create restriction is the token `{{tests_dir}}` -- and the
# reference is resolved when the definition loads. A module that wants the
# definition a run of *this* repository executes therefore has to load it
# against *this* repository's configuration, which is what these two do. A
# module that learns the restricted prefix must learn it this way rather than
# by reading `workflows/story-workflow.json` as text, where it would find the
# token rather than the value.
# --------------------------------------------------------------------------


def repository_config(root: Path = HARNESS_ROOT) -> dict:
    """This repository's own `.harness/config.yaml`, loaded."""
    return harness_config.load_config(root)


#: `load_workflow` gained a required `config` argument when a workflow
#: declaration became able to reference configuration. A module that recovers
#: an entry point out of git to compare its behaviour against today's is
#: comparing the change that story made, not the arity of a call that story
#: never touched, so the recovered call site is repointed — minimally, at the
#: one line, keeping the recovered code otherwise byte for byte what it was.
#: Without it the recovered script raises TypeError and the comparison stops
#: being about its own subject.
HISTORICAL_WORKFLOW_LOADS = (
    # scripts/l5-plan
    ('harness_config.load_workflow(\n'
     '        HARNESS_ROOT, config.get("workflow", "story-workflow")\n'
     '    )',
     'harness_config.load_workflow(\n'
     '        HARNESS_ROOT, config.get("workflow", "story-workflow"), config\n'
     '    )'),
    # orchestration/story_coordinator.py
    ('harness_config.load_workflow(harness_root, '
     'config.get("workflow", "story-workflow"))',
     'harness_config.load_workflow(harness_root, '
     'config.get("workflow", "story-workflow"), config)'),
)


def repointed_at_todays_signature(source: str) -> str:
    """Recovered source, with each historical `load_workflow` call repointed.

    Only that call is touched, and only where it appears; everything else the
    revision carried is byte for byte what it was.
    """
    for old, new in HISTORICAL_WORKFLOW_LOADS:
        source = source.replace(old, new)
    return source


def shipped_workflow(root: Path = HARNESS_ROOT,
                     name: str = "story-workflow") -> dict:
    """The named workflow under `root`, resolved against this repository's config.

    `root` is a harness root, which is this repository unless a test has
    mirrored one; the configuration stays this repository's, because a
    mirrored harness root has no target configuration of its own.
    """
    return harness_config.load_workflow(root, name, repository_config())


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


def revision_carrying(relative: str, *needles: str,
                      repo: Path = HARNESS_ROOT) -> str:
    """The newest revision at which one line of `relative` carries every needle.

    A search rather than a pinned sha, for the reason the architecture
    document records under the baseline bullets: a sha survives neither a
    rebase nor a squash merge, and a rebased-away sha is *unreachable in a
    clone* even while it still resolves in the working repository — which is
    exactly how a suite that passed locally failed in the clean clone.

    Several needles, and all of them on **one line**, because what a caller is
    looking for is a sentence rather than a document: a later revision can
    carry the same phrase in a different sentence — a document describing a
    check quotes the very figure the check is about — and a whole-document
    search answers with that revision instead. Requiring the needles together
    on a line is what makes the sentence, not the phrase, the thing found.

    Raises `NothingToCompareAgainst` when no revision carries the text, so a
    search that finds nothing cannot degrade into a caller reading whatever
    the working tree happens to hold.
    """
    if not needles:
        raise ValueError("revision_carrying needs at least one needle")
    result = _git(repo, "log", "--format=%H", "--", relative)
    if result.returncode != 0:
        raise NothingToCompareAgainst(
            f"the history of {relative} cannot be read in {repo}: "
            f"{result.stderr.strip()}"
        )
    for revision in result.stdout.split():
        try:
            text = repository_file_at(relative, revision=revision, repo=repo)
        except NothingToCompareAgainst:
            # `git log -- <path>` reports the commit that *removed* the path
            # as well as the ones that wrote it; a revision holding no blob
            # answers the question with a no rather than an error.
            continue
        if any(all(needle in line for needle in needles)
               for line in text.splitlines()):
            return revision
    raise NothingToCompareAgainst(
        f"no revision of {relative} in {repo} carries {list(needles)!r} "
        f"on one line"
    )


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
workflow: {workflow}
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: echo tests-ok
tests_dir: tests/
"""


@pytest.fixture
def configured_workflow() -> str:
    """The workflow `target_root` configures its target to run.

    A seam rather than a literal at the write below: a module that has built
    its own workflow overrides this with the built definition's name, and the
    same target fixture then drives a run under a definition this repository
    does not ship. Defaulted to the shipped name so a module that has not been
    converted configures exactly what it configured before.
    """
    return "story-workflow"


@pytest.fixture
def target_root(tmp_path: Path, configured_workflow: str) -> Path:
    root = tmp_path / "sample-target"
    for sub in (".harness/standards", ".harness/stories", ".harness/runs", ".harness/logs", ".harness/docs", "src"):
        (root / sub).mkdir(parents=True)
    (root / ".harness" / "config.yaml").write_text(
        CONFIG.format(workflow=configured_workflow), encoding="utf-8")
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



# --------------------------------------------------------------------------
# Building a history instead of reaching for this repository's own
#
# The five resolvers above answer a question about a commit graph, and they are
# the sanctioned route to *this* repository's. What they are not is a source of
# ordinary inputs. A module that wanted a sentence, an earlier version of a
# function, or the set of paths some past change touched was using the history
# as an instrument, and its answers then moved when something was committed,
# renamed, squashed or rebased -- none of which is a property of the code under
# test. story-051 measured the price: a pinned revision rebased away by a
# squash merge, then a content search that collided with the story's own
# documentation, and a whole retry budget spent on one sentence.
#
# So a test that needs a commit graph builds one, exactly as a test that needs
# a workflow builds one. `constructed_story` is that builder: a repository
# under a temporary directory in which one story has already run and committed,
# with the paths it respected and the paths it violated named by the caller. It
# is the same shape `target_root` builds for a run, and the same shape the
# pre-story control family in `tests/test_shared_baseline_resolution.py`
# established -- generalised here once rather than copied into each of the
# modules that needs it.
#
# And a test that needs a *text* this repository once carried -- the source of
# a file before a repair, the wording of a document before a correction --
# carries it as a committed fixture under `tests/history-fixtures/` and reads
# it with `history_fixture`. Committed, so it is evidence the repository holds
# rather than an answer git recomputes; read from the working tree, so no
# rebase, squash or rename moves it.
# --------------------------------------------------------------------------


#: Where a constructed story's validation file sits inside a repository a test
#: builds. Deliberately not the name of any module under `tests/`: the
#: resolution consults `STORY_ORIGINS` by basename, and a constructed
#: repository holds exactly one story, whose only lineage is its own path.
CONSTRUCTED_VALIDATION_REL = "tests/test_constructed_story_validation.py"

#: What the builder writes as the constructed story's own validation file. Its
#: content is irrelevant to every assertion -- what matters is the commit that
#: adds it -- so it is a file that would run if anybody ran it.
CONSTRUCTED_VALIDATION_SOURCE = "def test_the_constructed_story():\n    assert True\n"


def _sample_under(guarded: str) -> tuple[str, str]:
    """A file the guarded pathspec covers, and a second one it would also cover.

    A pathspec naming a directory -- `schemas/`, or `.harness/stories` -- is
    covered by anything beneath it, and a pathspec naming a file is covered by
    itself. The second name is what an *addition* inside the story's own run
    commit looks like, which is the case a `--diff-filter=MD` assertion
    deliberately lets through.
    """
    if guarded.endswith("/") or not Path(guarded).suffix:
        base = guarded.rstrip("/")
        return f"{base}/kept.txt", f"{base}/brand-new.txt"
    return guarded, f"{guarded}.new"


def constructed_story(tmp_path: Path, *,
                      respected: Sequence[str] = (),
                      violated: Sequence[str] = (),
                      violation: str = "modify",
                      validation_rel: str = CONSTRUCTED_VALIDATION_REL,
                      name: str = "constructed-story") -> Path:
    """A repository in which one story has already run and committed.

    Commit 1 is the pre-story state: every path named in `respected` and in
    `violated` exists there. Commit 2 is the story's own run commit -- it adds
    the validation file, makes an unrelated change the story is entitled to,
    and touches each path in `violated` and nothing else.

    That is the shape of a finished branch, and the shape in which `git diff
    HEAD` reports nothing no matter what the story did. `story_diff` bounded at
    the story's own range reports the violated paths and stays empty for the
    respected ones, which is what lets a scope assertion be *checked* here
    rather than recalled out of a history that moves.

    `violation` says how the run commit touches a violated path: `modify`
    rewrites it, `delete` removes it, and `add` leaves it alone and adds a
    sibling beside it instead.
    """
    if violation not in ("modify", "delete", "add"):
        raise ValueError(f"violation must be modify, delete or add, not "
                         f"{violation!r}")
    root = Path(tmp_path) / name
    root.mkdir(parents=True)
    _run_git(root, "init", "-q")
    for guarded in (*respected, *violated):
        subject, _ = _sample_under(guarded)
        _write(root, subject, "the pre-story content\n")
    _write(root, "unrelated.txt", "something the story may touch\n")
    _commit(root, "pre-story")

    _write(root, validation_rel, CONSTRUCTED_VALIDATION_SOURCE)
    _write(root, "unrelated.txt", "the story's own legitimate change\n")
    for guarded in violated:
        subject, sibling = _sample_under(guarded)
        if violation == "modify":
            _write(root, subject, "rewritten inside the story's own run commit\n")
        elif violation == "delete":
            (root / subject).unlink()
        else:
            _write(root, sibling, "an addition inside the story's own run commit\n")
    _commit(root, "the story's own run commit")
    return root


def constructed_story_range(root: Path,
                            validation_rel: str = CONSTRUCTED_VALIDATION_REL
                            ) -> StoryRange:
    """The commit range of the story `constructed_story` built, as it resolves.

    One call rather than a `root / validation_rel` spelled at every site, and
    the same resolution the live suite uses -- pointed at a repository the test
    owns.
    """
    return story_commit_range(Path(root) / validation_rel, Path(root))


def constructed_story_diff(root: Path, paths: Sequence[str], **kwargs) -> str:
    """`story_diff` for the story `constructed_story` built.

    The caller's own predicate keeps its shape -- empty means the constructed
    story left those paths alone -- and the repository it asks is the one the
    test built.
    """
    return story_diff(list(paths),
                      validation_file=Path(root) / kwargs.pop(
                          "validation_rel", CONSTRUCTED_VALIDATION_REL),
                      repo=Path(root), **kwargs)


def build_history(root: Path, commits: Sequence[dict]) -> list[str]:
    """A repository built commit by commit, returning each commit's sha.

    Each entry describes one commit: `write` maps a repository-relative path to
    its text, `delete` names paths to remove, `rename` maps an old path to a
    new one, and `message` names the commit. It exists so a module proving one
    of the resolvers can build the *shape* it is about -- a rename, a
    revert-and-restore, a squash -- rather than a repository plus four
    hand-rolled `subprocess` calls beside it.

    Renames are applied first, then writes, then deletions, so one commit can
    move a path and rewrite it at its new name -- which is the rename this
    repository actually made, and the one that silently empties a read bounded
    by a path's own add-commit.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    _run_git(root, "init", "-q")
    shas = []
    for index, entry in enumerate(commits):
        for old, new in entry.get("rename", {}).items():
            (root / new).parent.mkdir(parents=True, exist_ok=True)
            _run_git(root, "mv", old, new)
        for relative, text in entry.get("write", {}).items():
            _write(root, relative, text)
        for relative in entry.get("delete", ()):
            (root / relative).unlink()
        shas.append(_commit(root, entry.get("message", f"commit {index + 1}")))
    return shas


def squash_onto(root: Path, base: str, message: str = "squashed") -> str:
    """Every commit since `base`, replayed as one commit on top of it.

    The shape a squash merge leaves behind, which is the shape that makes a
    pinned sha unreachable: the individual commits are gone from the branch and
    the tree they produced is carried by a single new one.
    """
    root = Path(root)
    _run_git(root, "reset", "-q", "--soft", base)
    return _commit(root, message)


def _write(root: Path, relative: str, text: str) -> Path:
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run_git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


def _commit(root: Path, message: str) -> str:
    _run_git(root, "add", "-A")
    _run_git(root, "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "--allow-empty", "-m", message)
    return _run_git(root, "rev-parse", "HEAD").strip()


#: Where a text this repository once carried is kept, once committed. A
#: directory rather than a module, and a `.py.txt` suffix rather than `.py`, so
#: the suite's own scans -- each of which globs `tests/*.py` -- neither collect
#: a fixture as a test module nor report the very defect a pre-repair fixture
#: exists to carry.
HISTORY_FIXTURES = Path(__file__).resolve().parent / "history-fixtures"


def history_fixture(name: str) -> str:
    """A text this repository once carried, read from the fixture that holds it.

    The alternative is `git show <revision>:<path>`, which answers the same
    question until the revision is rebased away, the path is renamed or the
    branch is squashed -- and then answers a different one, or none. A
    committed fixture is the same evidence with none of that: it is in the tree
    this repository ships, it is diffable, and a story that changes it changes
    it visibly.

    Raises rather than returning empty, so a fixture that has been moved fails
    as itself rather than as an assertion that has stopped seeing anything.
    """
    path = HISTORY_FIXTURES / name
    if not path.is_file():
        raise AssertionError(
            f"{name} is not carried under {HISTORY_FIXTURES.name}/; a text this "
            f"repository once carried is committed there rather than resolved "
            f"out of the commit graph")
    return path.read_text(encoding="utf-8")


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


def echo_guidance(entries: Sequence[str], *,
                  unmet: str | None = "the retry did not close this") -> list[dict]:
    """The `guidance_outcomes` a verdict must carry to account for `entries`.

    story-050 made a failed verdict answer the guidance that directed the
    attempt it judges, entry by entry: every `current_focus` focus and every
    `preserve_behavior` string, echoed verbatim. A verdict that does not is
    escalated by the coordinator as a mismatch, so any fixture whose fake
    verifier fails a *retried* attempt has to echo the guidance in force for
    it. That composition lives here rather than once per module, for the same
    reason the baseline resolution and the retry route do.

    `unmet` marks every entry as not met, which is the ordinary under-delivery
    case and routes exactly as a failed verdict always has. Passing `None`
    reports every entry met, which is the contradiction the
    defective-guidance branch exists for.
    """
    return [
        {"guidance": entry} if unmet is None
        else {"guidance": entry, "unmet": unmet}
        for entry in entries
    ]


def guidance_in_force(run_dir: Path) -> list[str]:
    """The guidance entries the coordinator recorded as directing this attempt.

    Read off `state.json`, which is where story-050 put the routing input for
    the defective-guidance check: a fake verifier that answers this answers
    exactly what the coordinator will compare it against, whichever path
    routed the attempt — including the clean-clone reroute, which writes no
    guidance and so leaves none in force.
    """
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    return list(state.get("guidance_in_force", []))


def answering_guidance(verdict: dict, run_dir: Path, *,
                       unmet: str | None = "the retry did not close this") -> dict:
    """`verdict` with the guidance in force accounted for, when there is any.

    The one call a fixture whose subject is something else needs: a failed
    verdict on a retried attempt reports every entry unmet, and a verdict with
    no guidance in force is returned untouched, so a first verification
    carries no `guidance_outcomes` key at all.
    """
    entries = guidance_in_force(run_dir)
    if not entries or verdict.get("status") != "failed":
        return verdict
    return {**verdict, "guidance_outcomes": echo_guidance(entries, unmet=unmet)}


@pytest.fixture
def harness_root() -> Path:
    return HARNESS_ROOT


# --------------------------------------------------------------------------
# Building a workflow instead of reaching for the shipped one
#
# Almost every module under tests/ that loads `workflows/story-workflow.json`
# is not testing that definition. It is testing a mechanism -- does the
# coordinator self-route, route a retry, refuse a malformed declaration,
# enforce a boundary -- and it reached for the live artifact to avoid writing a
# stage name or a restricted prefix into the test. The rule that produced that
# is right and stays: a test writes no stage name, no prefix and no artifact
# name of its own. What was wrong is *which* workflow the names were derived
# from. Deriving them from what this repository happens to deploy makes a
# deployment fact into something the suite enforces: story-047 granted one
# stage a `max_self_routes` budget, a correct one-line change, and reddened
# four assertions in a module with nothing to say about whether that grant was
# right.
#
# So a test builds the workflow it needs and derives its names from that. The
# builder is deliberately small and compositional rather than one canonical
# fixture: a single definition serving thirty modules accretes until it is a
# second shipped workflow with the same coupling. A test asks for the stages it
# needs and the declarations it is about; every key it does not ask for is
# absent from what it gets, so a module can build a case this repository does
# not deploy.
#
# It resolves nothing this repository ships. `build_workflow` reads no file at
# all -- no workflow, no configuration, no rules -- which is a property
# `tests/test_shipped_workflow_is_valid.py` demonstrates by running it against
# a filesystem where this repository is not reachable rather than by asserting
# about its source.
# --------------------------------------------------------------------------


#: The one stage name the harness itself writes. `story_coordinator` keys its
#: verdict handling on `name == "verifier"`, so a built workflow whose run must
#: reach a verdict has to call that stage this. It is a fact about the harness
#: rather than about what this repository deploys, and it is written *here*,
#: once, so a module that needs it derives it from the fixture exactly as it
#: derives every other name -- rather than spelling it at a call site.
#: `tests/test_shipped_workflow_is_valid.py` holds the coordinator to it.
VERIFYING_STAGE = "verifier"

#: The artifact names the *harness* writes down, as distinct from the ones a
#: workflow declares. `context_assembler.build_context` reads each of these off
#: the run directory by a fixed name to fill a prompt placeholder, and
#: `story_coordinator` reads the verdict by a fixed name to route on it, so a
#: built workflow whose run must reach those code paths has to call its
#: artifacts these. They are facts about the harness rather than about what this
#: repository deploys — changing the shipped workflow does not change one of
#: them — and they are written here, once, for the reason `VERIFYING_STAGE` is:
#: a module that needs one derives it from the fixture rather than spelling it
#: at a call site. `tests/test_shipped_workflow_is_valid.py` holds the harness
#: to them.
CHANGED_FILES = "changed-files.json"
TESTER_CHANGED_FILES = "tester-changed-files.json"
DOCUMENTER_CHANGED_FILES = "documenter-changed-files.json"
IMPLEMENTATION_SUMMARY = "implementation-summary.md"
TEST_RESULTS = "test-results.json"
DOCUMENTATION_REPORT = "documentation-report.md"
VERIFICATION_RESULT = "verification-result.json"
RETRY_GUIDANCE = "retry-guidance.json"
CLEAN_CLONE_RESULT = "clean-clone-result.json"
CLAIM_SUPPORT_RESULT = "claim-support-result.json"


class StageRef:
    """A reference to a stage of the workflow being built, by position.

    A route names its destination stage, and the destination's name is
    assigned by the builder rather than by the caller -- so a caller that
    wanted to write the name would have to know what the builder was going to
    choose. It names the position instead and the builder substitutes the
    name, which keeps the rule intact from the caller's side: the test never
    writes a stage name, it points at one.
    """

    __slots__ = ("index",)

    def __init__(self, index: int):
        self.index = index

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"StageRef({self.index})"


def workflow_stage(*, name: str | None = None, prompt: str | None = None,
                   outputs: Sequence[str] | None = None,
                   changed_files: str | None = None,
                   may_not_create: Sequence[str] | None = None,
                   max_self_routes: object = None,
                   revert_check: dict | None = None,
                   clean_clone: dict | None = None,
                   retry_routing: dict | None = None,
                   schemas: dict | None = None,
                   **extra) -> dict:
    """One stage declaration, carrying exactly what the caller asked for.

    Every argument defaults to a sentinel meaning "not asked for", and an
    argument not asked for produces no key: a stage built with no
    `max_self_routes` declares no budget, which is a different thing from
    declaring zero, and the coordinator treats the two differently. `extra`
    exists so a test needing a key nobody has needed yet can pass it without
    the builder growing an argument for it first -- but a key more than one
    module needs should become an argument here rather than being spelled at
    each call site.

    `max_self_routes` is checked against a sentinel rather than against None
    because zero and False are both values a test builds deliberately, to
    drive the pre-flight that refuses a budget which is not a count.
    """
    declaration: dict = {"name": name} if name is not None else {}
    if prompt is not None:
        declaration["prompt"] = prompt
    if outputs is not None:
        declaration["outputs"] = list(outputs)
    if changed_files is not None:
        declaration["changed_files"] = changed_files
    if may_not_create is not None:
        declaration["may_not_create"] = list(may_not_create)
    if max_self_routes is not None:
        declaration["max_self_routes"] = max_self_routes
    if revert_check is not None:
        declaration["revert_check"] = dict(revert_check)
    if clean_clone is not None:
        declaration["clean_clone"] = dict(clean_clone)
    if retry_routing is not None:
        declaration["on_failure"] = {"retry_routing":
                                     {category: dict(route)
                                      for category, route in retry_routing.items()}}
    if schemas is not None:
        declaration["schemas"] = dict(schemas)
    declaration.update(extra)
    return declaration


def _substitute_refs(value, names: list[str]):
    """Replace every StageRef in a built definition with the stage's name."""
    if isinstance(value, StageRef):
        if not 0 <= value.index < len(names):
            raise AssertionError(
                f"this workflow has {len(names)} stages, so {value!r} names "
                f"nothing; a route must point at a stage the builder built")
        return names[value.index]
    if isinstance(value, dict):
        return {key: _substitute_refs(item, names) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute_refs(item, names) for item in value]
    return value


def build_workflow(*stages: dict, name: str = "built-workflow",
                   escalation_rules: dict | None = None) -> dict:
    """A workflow definition assembled from what the caller asked for.

    Reads nothing. Every stage the caller did not describe is absent, every
    key a stage did not ask for is absent, and the names are the builder's --
    positional unless a caller named one -- so a test derives its stage names
    from the definition it built exactly as it used to derive them from the
    definition this repository ships.

    A stage's `prompt` defaults to a file named for the stage, because
    `materialize_workflow` writes one per stage and the coordinator loads a
    template by the name the declaration carries.
    """
    names = [stage.get("name") or f"stage-{index + 1}"
             for index, stage in enumerate(stages)]
    if len(set(names)) != len(names):
        raise AssertionError(f"two stages of {name} share a name: {names}")
    built = []
    for stage_name, stage in zip(names, stages):
        declaration = {"name": stage_name,
                       "prompt": stage.get("prompt", f"{stage_name}.md")}
        for key, value in stage.items():
            if key in ("name", "prompt"):
                continue
            declaration[key] = value
        built.append(_substitute_refs(declaration, names))
    definition: dict = {"name": name, "stages": built}
    if escalation_rules is not None:
        definition["escalation_rules"] = _substitute_refs(escalation_rules, names)
    return definition


#: The context fields a materialized stage prompt renders. Named here, in the
#: fixture, for the same reason the builder assigns stage names here: a module
#: asserting on a rendered prompt derives the field it is looking for from what
#: the fixture declares rather than from what this repository's own templates
#: happen to say today. A test needing a field this does not list passes its
#: own template to `materialize_workflow`.
BUILT_PROMPT_FIELDS = (
    "story", "acceptance_criteria", "stage_exceptions", "run_dir",
    "test_command", "tests_dir", "repository_standards", "testing_standards",
    "architecture_docs", "architecture_doc_paths", "workflow_stages",
    "stage_create_restrictions", "retry_routes", "blocked_paths",
    "changed_files", "tester_changed_files", "documenter_changed_files",
    "implementation_summary", "documentation_report", "test_results",
    "verification_result", "latest_verifier_finding", "retry_guidance",
    "clean_clone_result", "self_route_result", "retry_state",
)

#: The shared partial the assembler resolves before injecting it, mirrored here
#: so a run against a built harness root renders a `{{harness_layer}}` the way a
#: run against a deployed one does. Its text is the fixture's own.
BUILT_HARNESS_LAYER = """\
[Built Harness Layer]

Blocked paths:
{{blocked_paths}}

Granted commands:
{{allowed_tools}}
"""


def built_stage_prompt(stage_name: str) -> str:
    """The template `materialize_workflow` writes for a stage it was given.

    Every field the fixture declares, labelled on its own line with the
    placeholder on the line below it, so a module asserting that some artifact
    reached a stage can find the span by the label it derived from
    `BUILT_PROMPT_FIELDS` -- and so a multi-line value renders as a block the
    way it does in the shipped templates rather than trailing off a label.

    The verifying stage's template omits the retry-guidance field, mirroring
    the division the harness itself draws: guidance is *written by* the stage
    that judges an attempt and *read by* the stage the retry is routed to, so
    rendering it back into the judge's own prompt would make the two renderings
    of one attempt indistinguishable. A fixture-level fact, stated here once,
    rather than a template a module has to compose for itself.
    """
    omitted = {"retry_guidance"} if stage_name == VERIFYING_STAGE else set()
    lines = [f"# {stage_name}", "", "{{harness_layer}}", ""]
    for field in BUILT_PROMPT_FIELDS:
        if field in omitted:
            continue
        lines += [f"{field}:", f"{{{{{field}}}}}", ""]
    return "\n".join(lines)


def materialize_workflow(workflow: dict, root: Path, *,
                         prompts: dict[str, str] | None = None,
                         rules: dict | None = None,
                         copy: Sequence[str] = (),
                         shipped_root: Path = HARNESS_ROOT) -> Path:
    """Write a built workflow into a harness root the caller owns.

    The point of the builder is a definition a test can *run*, not one it can
    only inspect: a converted module has to exercise the same code path the
    module reading the shipped definition exercised, which means a real
    coordinator loading a real file. So the definition is written where a run
    finds it, and a prompt template is written for every stage it declares --
    the shipped templates are named for the shipped stages and a built
    workflow's stages are not.

    `rules` and `schemas` are the harness's, not the workflow's, and a test
    converting away from the shipped *workflow* is not thereby building its own
    rule set: they are linked at the shipped ones unless the caller supplies
    otherwise, exactly as the probe-harness idiom this generalises did.

    Returns the root, so a fixture can be one line.
    """
    root = Path(root)
    (root / "workflows").mkdir(parents=True, exist_ok=True)
    (root / "workflows" / f"{workflow['name']}.json").write_text(
        json.dumps(workflow, indent=2) + "\n", encoding="utf-8")

    prompt_dir = root / "prompts"
    prompt_dir.mkdir(exist_ok=True)
    (prompt_dir / "harness-layer.md").write_text(BUILT_HARNESS_LAYER,
                                                 encoding="utf-8")
    supplied = prompts or {}
    for stage in workflow["stages"]:
        text = supplied.get(stage["name"], built_stage_prompt(stage["name"]))
        (prompt_dir / stage["prompt"]).write_text(text, encoding="utf-8")

    if rules is None:
        if not (root / "rules").exists():
            (root / "rules").symlink_to(shipped_root / "rules")
    else:
        (root / "rules").mkdir(exist_ok=True)
        (root / "rules" / "execution-rules.json").write_text(
            json.dumps(rules, indent=2) + "\n", encoding="utf-8")
    if not (root / "schemas").exists():
        (root / "schemas").symlink_to(shipped_root / "schemas")
    # Copied rather than linked, and only when asked for: an entry point under
    # `scripts/` resolves its own harness root as `Path(__file__).resolve()`,
    # and `resolve()` follows a symlink straight back to this repository --
    # which would load the shipped workflow and undo the whole point of
    # building one. A caller driving a run through an entry point asks for the
    # directories it needs; every other caller pays nothing.
    for name in copy:
        destination = root / name
        if not destination.exists():
            shutil.copytree(shipped_root / name, destination,
                            ignore=shutil.ignore_patterns("__pycache__"))
    return root
