"""Which remote answers for this repository, wherever the question is asked.

Several places here each need to know which remote a repository uses, and until
story-200 they each worked it out their own way and did not agree. The
plan-time code looked for a remote named `origin` and gave up when it found
none; the run-time code fell back to whatever remote the repository did have.
In a clone whose only remote is named something else that disagreement decided
something real: planning concluded there was no remote to reserve a story id
on, claimed the id purely locally and pushed nothing, so two clones could plan
the same id — the exact collision the reservation mechanism exists to remove —
while the same repository's run path fetched story branches from that remote
quite happily.

So the question has one answer here and every asker calls it: the remote the
branch is configured to track, else `origin` where a remote of that name
exists, else the first remote git lists, else nothing at all. Deleting the
plan-time function rather than keeping it as a second name for the same idea is
what makes that a property of the code rather than of today's agreement between
two copies.

Every function returns what happened rather than printing it, the shape
`plan_commit`, `story_ids` and `worktrees` already have. Nothing here raises and
nothing here prints: a git that failed, a path that is not a repository and a
repository with no remote are each answered with the empty string, which is the
one-directional bias every other reader of a target repository here takes —
nothing establishable is reported as nothing rather than as something false.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(target_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(target_root), *args],
        capture_output=True,
        text=True,
    )


def current_branch(target_root: Path) -> str:
    """The abbreviated HEAD at `target_root`, or "" when git cannot say.

    A detached HEAD abbreviates to the literal `HEAD`, which is returned as it
    is rather than translated: what a caller does about a tree standing on no
    branch is the caller's, and `resolve` below reads that value as a branch
    that tracks nothing.
    """
    result = _git(target_root, "rev-parse", "--abbrev-ref", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else ""


def resolve(target_root: Path, branch: str | None = None) -> str:
    """The remote that answers for `target_root`, or "" when none does.

    Git's own record of the branch first — `branch.<name>.remote`, which a
    branch that tracks something has stated — so a branch that tracks a remote
    it has never been pushed to still resolves to it. The repository's remotes
    otherwise, preferring `origin` and taking the first git lists when there is
    no `origin`, because a clone whose one remote is named something else has
    named it and there is nothing else it could mean.

    `branch` is the branch asked about; omitted, it is the one checked out. A
    detached HEAD is a branch that tracks nothing rather than a failure, so it
    falls through to the listing and resolves to the remote the repository has.

    Empty means there is no remote to reach, which a caller reports rather than
    attempts.
    """
    if branch is None:
        branch = current_branch(target_root)
    if branch and branch != "HEAD":
        configured = _git(target_root, "config", f"branch.{branch}.remote")
        remote = configured.stdout.strip() if configured.returncode == 0 else ""
        if remote:
            return remote
    remotes = _git(target_root, "remote")
    names = remotes.stdout.split() if remotes.returncode == 0 else []
    if not names:
        return ""
    return "origin" if "origin" in names else names[0]
