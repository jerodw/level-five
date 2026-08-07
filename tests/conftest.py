import subprocess
import sys
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


def story_commit_range(validation_file: Path,
                       repo: Path = HARNESS_ROOT) -> StoryRange:
    """The commit range of the story that added `validation_file`.

    The run commit is the *oldest* commit that added the file, so a later
    revert-and-restore cannot be mistaken for the story's own run, and a
    planning or hotfix commit on the same story — which modifies the file
    rather than adding it — is never returned.
    """
    relative = _relative_to(validation_file, repo)
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
               options: tuple[str, ...] = ()) -> str:
    """The diff `validation_file`'s own story made to `paths`.

    Empty output means the story left those paths alone. Callers assert on
    emptiness; `options` and `diff_filter` only shape what a non-empty
    result looks like and which change kinds it counts.
    """
    command = story_commit_range(validation_file, repo).diff_command(repo, *options)
    if diff_filter is not None:
        command.append(f"--diff-filter={diff_filter}")
    command += ["--", *paths]
    return subprocess.run(
        command, capture_output=True, text=True, check=True,
    ).stdout


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


@pytest.fixture
def harness_root() -> Path:
    return HARNESS_ROOT
