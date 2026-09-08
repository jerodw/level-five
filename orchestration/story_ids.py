"""Reserving the story id a planning session is about to write.

The planner used to choose an id by listing the stories directory, which is a
reading of one clone: two clones planning at once both see the same highest id
and both write it. The id is now reserved *before* the session starts, by
pushing the story branch's ref to the remote with a claim that succeeds only
when the ref does not already exist — so the remote decides, and two clones can
never plan the same id.

A claim is *consumed* by the plan commit that is pushed onto the ref it
reserved: a successful session releases nothing and leaves no ref behind beyond
the story branch itself. A claim that was never consumed — a session that wrote
no artifact, or one whose artifact carries an id other than the reserved one —
is released, and a release that could not be made is reported rather than
passed over.

Every function returns what happened rather than printing it, the shape
`plan_commit` and `worktrees` have. Nothing here raises and nothing here prints.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import story_coordinator

#: A story id is `story-` and a number, which is the shape `story_branch`
#: composes a branch name from and the shape a story artifact is named with.
STORY_NUMBER = re.compile(r"story-(\d+)$")

#: How many ids one invocation may try to claim before it gives up. A refused
#: claim means somebody else took that id, so the next one is tried; a bound
#: exists because a remote refusing every claim for some reason other than the
#: ref existing would otherwise be walked forever, and the bound is named when
#: it is reached so the refusal says what stopped rather than only that
#: something did.
MAX_CLAIM_ATTEMPTS = 10


@dataclass(frozen=True)
class Reservation:
    """What an attempt to reserve an id did.

    `story_id` is the id reserved, or the last one tried when nothing was.
    `remote` is empty where the repository configures none, and then `reserved`
    is true on a *local* claim: nothing was pushed, nothing has to be released,
    and `local` says so, because a caller telling a developer their id is
    reserved when no remote heard of it would be telling them something false.
    """

    story_id: str
    branch: str
    remote: str
    reserved: bool
    local: bool = False
    attempts: int = 0
    problems: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Release:
    """What an attempt to release an unconsumed claim did."""

    branch: str
    remote: str
    released: bool
    detail: str = ""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )


def _numbers(names) -> list[int]:
    return [
        int(match.group(1))
        for match in (STORY_NUMBER.search(name) for name in names)
        if match is not None
    ]


def highest_known(target_root: Path, config: dict) -> int:
    """The highest story id this clone knows of, from three places at once.

    The stories directory, because an artifact planned and not yet pushed is
    still an id somebody used. Local branches under the configured prefix,
    because a branch cut and not yet pushed is the same. And remote-tracking
    refs under it, because a fetch is how this clone learns what other clones
    have claimed. Zero when none of the three knows anything, so the first id a
    repository ever plans is one.

    It is a *candidate*, not an answer: the answer comes from the remote
    refusing or accepting the claim built on it.
    """
    prefix = config.get("branch_prefix", "story/")
    stories_dir = target_root / config.get("stories_dir", ".harness/stories")
    known: list[int] = []
    if stories_dir.is_dir():
        known += _numbers(path.stem for path in stories_dir.glob("*.yaml"))
    listed = _git(
        target_root,
        "for-each-ref",
        "--format=%(refname:short)",
        f"refs/heads/{prefix}*",
        f"refs/remotes/*/{prefix.rstrip('/')}*",
    )
    if listed.returncode == 0:
        known += _numbers(listed.stdout.split())
    return max(known, default=0)


def remote_unreachable(target_root: Path, remote: str) -> str:
    """Why `remote` cannot be reached, or "" when it can.

    Asked before a worktree is created, before an agent is invoked and before
    anything is written, because a reservation that cannot be made means the
    whole invocation cannot do what it was asked to do — and discovering that
    after a session has run costs the session.
    """
    if not remote:
        return ""
    result = _git(target_root, "ls-remote", "--exit-code", "--heads", remote, "HEAD")
    # An exit code of 2 is "no matching refs", which is a remote that answered
    # about a repository with no branches. Only a failure to reach it counts.
    if result.returncode in (0, 2):
        return ""
    lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    return "; ".join(lines) or f"git could not reach {remote}"


def claim(
    target_root: Path, remote: str, branch: str, start_point: str
) -> tuple[bool, str]:
    """Push `start_point` to `branch` on `remote`, expecting the ref to be absent.

    `--force-with-lease=<ref>:` with an empty expected value is git's way of
    saying the named ref must not already exist, so the remote is what decides
    whether this id is free. Returns whether the claim was made and, when it
    was not, what git said — which the caller reads as "somebody else has that
    id" and tries the next one.
    """
    result = _git(
        target_root,
        "push",
        f"--force-with-lease=refs/heads/{branch}:",
        remote,
        f"{start_point}:refs/heads/{branch}",
    )
    if result.returncode == 0:
        return True, ""
    lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    return False, "; ".join(lines) or "git refused the claim without saying why"


def reserve(
    target_root: Path,
    config: dict,
    start_point: str,
    remote: str,
    maximum: int = MAX_CLAIM_ATTEMPTS,
) -> Reservation:
    """Reserve the next free story id, or say why none could be reserved.

    The candidate is `highest_known` plus one; a refused claim means the remote
    already holds that ref, so the next id is tried, bounded by `maximum` and
    naming the bound when it is reached.

    A repository with no remote configured plans on a *local* claim: there is
    nothing to push to, so the candidate is taken as it stands and the
    reservation says outright that nothing was reserved. That is weaker than a
    claim and is reported as weaker rather than presented as one.
    """
    candidate = highest_known(target_root, config) + 1
    if not remote:
        story_id = f"story-{candidate:03d}"
        return Reservation(
            story_id,
            story_coordinator.story_branch(config, story_id),
            "",
            True,
            local=True,
            attempts=1,
        )
    refusals: list[str] = []
    for attempt in range(1, maximum + 1):
        story_id = f"story-{candidate:03d}"
        branch = story_coordinator.story_branch(config, story_id)
        made, detail = claim(target_root, remote, branch, start_point)
        if made:
            return Reservation(story_id, branch, remote, True, attempts=attempt)
        refusals.append(f"{story_id}: {detail}")
        candidate += 1
    story_id = f"story-{candidate - 1:03d}"
    return Reservation(
        story_id,
        story_coordinator.story_branch(config, story_id),
        remote,
        False,
        attempts=maximum,
        problems=[
            f"{maximum} consecutive claims were refused, which is "
            f"{MAX_CLAIM_ATTEMPTS if maximum == MAX_CLAIM_ATTEMPTS else maximum} "
            f"— the bound on how many ids one invocation tries",
            *refusals,
        ],
    )


def release(target_root: Path, remote: str, branch: str) -> Release:
    """Delete a ref a claim created and no plan commit was pushed onto.

    A reservation nothing consumed is an id nobody can ever use again, so it is
    given back. A repository that made a local claim has nothing to give back
    and says so; a delete that failed is reported rather than passed over,
    because an unreleased claim is a silently burnt id.
    """
    if not remote:
        return Release(branch, "", True, "no remote, so the claim was local")
    result = _git(target_root, "push", remote, "--delete", branch)
    if result.returncode != 0:
        lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
        return Release(
            branch,
            remote,
            False,
            "; ".join(lines) or "git refused the delete without saying why",
        )
    return Release(branch, remote, True)
