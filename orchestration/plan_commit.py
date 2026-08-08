"""Commit and push the story artifact a planning session caused to appear.

`l5-plan` starts an interactive session and, when it ends, commits what that
session wrote. The decision of *what* to commit is made here rather than in the
script, and it is made from the filesystem: the stories directory is
snapshotted before the session and the files that appeared under it afterwards
are the artifacts. Nothing consults the session's exit status, because a
session that wrote a plan and then failed still wrote a plan.

Every function returns what happened rather than printing it; the script
decides what to say. Nothing here stages a path the session did not add, and
nothing here unwinds a commit: a push that fails leaves the commit exactly
where it is, which is the situation this repository already lived with.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import story_coordinator


@dataclass(frozen=True)
class CommitResult:
    """What the commit of the new artifacts did."""

    paths: tuple[str, ...]
    subject: str
    sha: str
    detail: str = ""

    @property
    def committed(self) -> bool:
        return bool(self.sha)


@dataclass(frozen=True)
class PushResult:
    """What the push of that commit did, including why it did not happen."""

    pushed: bool
    remote: str
    branch: str
    detail: str = ""


def _git(target_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(target_root), *args],
        capture_output=True,
        text=True,
    )


def snapshot(stories_dir: Path) -> frozenset[Path]:
    """Every file under `stories_dir` right now.

    A directory that does not exist yet snapshots as empty, so the first story
    a repository ever plans is identified by having appeared like any other.
    """
    if not stories_dir.is_dir():
        return frozenset()
    return frozenset(p for p in stories_dir.rglob("*") if p.is_file())


def new_artifacts(stories_dir: Path, before: Iterable[Path]) -> tuple[Path, ...]:
    """The files that appeared under `stories_dir` since the snapshot.

    Appearance is the whole test. A session that edited an existing artifact
    without adding one produces nothing here, because the script commits only
    files it caused to exist.
    """
    return tuple(sorted(snapshot(stories_dir) - frozenset(before)))


def story_title(artifact: Path, harness_root: Path | None = None) -> str:
    """The artifact's title, or "" when it cannot be read.

    Read through `story_coordinator.read_story`, the run's one reading of a
    story artifact, rather than through a second call into the parser — the
    commit message must describe the artifact the same way the run that
    executes it will.

    The parse feeds the message and nothing else. An artifact that does not
    parse, or that parses but does not satisfy the schema, is still committed;
    validating it is a different concern and a different story. So every
    failure here is answered with the empty string, which the subject falls
    back from to the story id alone.
    """
    try:
        reading = story_coordinator.read_story(
            artifact.read_text(encoding="utf-8"), harness_root
        )
        title = reading.parsed["story"]["title"]
    except (OSError, KeyError, TypeError, ValueError):
        return ""
    return title if isinstance(title, str) else ""


def commit_subject(
    artifacts: Sequence[Path], harness_root: Path | None = None
) -> str:
    """`Plan story-NNN: <title>`, falling back to the ids alone.

    More than one new artifact is one commit naming each id, because they were
    written by one session and splitting them would invent an order the session
    did not have.
    """
    ids = ", ".join(a.stem for a in artifacts)
    if len(artifacts) == 1:
        title = story_title(artifacts[0], harness_root)
        if title:
            return f"Plan {ids}: {title}"
    return f"Plan {ids}"


def commit_artifacts(
    target_root: Path,
    artifacts: Sequence[Path],
    harness_root: Path | None = None,
) -> CommitResult:
    """Commit exactly `artifacts` on the branch the developer is on.

    Only these paths are staged and only these paths are committed — the commit
    carries a pathspec, so unrelated work in the tree stays uncommitted and
    whatever the developer had staged stays staged. No branch is chosen,
    created or switched.
    """
    relative = tuple(str(a.relative_to(target_root)) for a in artifacts)
    subject = commit_subject(artifacts, harness_root)
    staged = _git(target_root, "add", "--", *relative)
    if staged.returncode != 0:
        return CommitResult(relative, subject, "", staged.stderr.strip())
    committed = _git(target_root, "commit", "-m", subject, "--", *relative)
    if committed.returncode != 0:
        return CommitResult(relative, subject, "", committed.stderr.strip())
    revision = _git(target_root, "rev-parse", "HEAD")
    sha = revision.stdout.strip() if revision.returncode == 0 else ""
    return CommitResult(relative, subject, sha)


def current_branch(target_root: Path) -> str:
    result = _git(target_root, "rev-parse", "--abbrev-ref", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else ""


def resolve_remote(target_root: Path) -> str:
    """The remote the current branch tracks, else origin, else "".

    Read from the branch's configured upstream rather than from a remote ref,
    so a branch that tracks a remote it has never been pushed to still resolves
    to it. Empty means no remote to push to, which is reported rather than
    attempted.
    """
    branch = current_branch(target_root)
    if branch and branch != "HEAD":
        configured = _git(target_root, "config", f"branch.{branch}.remote")
        remote = configured.stdout.strip()
        if remote:
            return remote
    remotes = _git(target_root, "remote").stdout.split()
    return "origin" if "origin" in remotes else ""


def push_commit(target_root: Path) -> PushResult:
    """Push HEAD to the resolved remote, reporting rather than failing.

    `git push <remote> HEAD` pushes the branch under its own name without
    writing tracking configuration into the developer's repository as a side
    effect of planning. A rejected push and a repository with no remote are
    both reported and neither rolls anything back: the commit stays where it
    is, reachable, exactly as it was before the push was attempted.
    """
    branch = current_branch(target_root)
    remote = resolve_remote(target_root)
    if not remote:
        return PushResult(False, "", branch, "no remote configured")
    pushed = _git(target_root, "push", remote, "HEAD")
    if pushed.returncode != 0:
        return PushResult(False, remote, branch, pushed.stderr.strip())
    return PushResult(True, remote, branch)
