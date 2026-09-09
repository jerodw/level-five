"""Where a planning session and a run stand while they work.

Planning and running shared one checkout until story-117: `l5-plan` committed
its artifact on whatever branch the developer happened to be standing on, and a
run then cut its story branch in that same checkout. A plan written while a run
was in flight landed its commit in the middle of the run's branch, and the two
could not be done at once without mixing the commits.

This module holds the whole worktree concept, so no worktree knowledge lives in
a script and none of it is spelled twice: where worktrees live, creating one at
a start point, finding an existing one for a branch, reporting whether a tree
stands on a branch, removing a worktree together with its local branch, and
giving a tree the build state the target's configured commands name.

That last one is not only a worktree's problem, which is why it lives here
rather than beside either caller. A fresh worktree holds tracked files alone, so
an interpreter the target configures inside its own tree — gitignored, and
therefore absent — is not there; a fresh clone has the same absence for the same
reason, which is why the clean-clone check has linked those directories in since
before worktrees existed. `interpreter_roots` and `link_build_state` are that
one derivation with one home, read by the clone path and by the worktree path
alike, so the two cannot disagree about what an interpreter's root is.

Every function returns what happened rather than printing it, the shape
`plan_commit` and `plan_run_offer` already have. Nothing here raises and nothing
here prints: a caller decides what a failure is worth and how to say it.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

#: The configured key naming where worktrees live. Declared in
#: schemas/harness-config.schema.json, which is what makes it a key the harness
#: reads rather than one the pre-flight refuses.
WORKTREE_DIR_KEY = "worktree_dir"

#: Appended to the primary working tree's own name to make the default sibling
#: directory. A sibling rather than a child, so nothing this mechanism creates
#: ever appears inside the repository: no path the harness globs, no clean-clone
#: build and no blocked-path list gains an entry for worktrees.
DEFAULT_SUFFIX = "-worktrees"


@dataclass(frozen=True)
class WorktreeResult:
    """What an attempt to create a worktree did.

    `problems` empty is the whole of "it is there": either it was created now,
    or the caller asked for one that already stood at that path. Anything else
    is git's own words, one problem per line, in the shape `base_problems` and
    `_checkout_story_branch` already use, so a caller refuses through the shared
    refusal rather than composing its own wording.
    """

    path: Path
    branch: str
    problems: list[str] = field(default_factory=list)

    @property
    def created(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class Removal:
    """What an attempt to remove a worktree and its local branch did.

    `removed` is whether the worktree is gone. `branch_removed` is whether the
    local branch went with it, which is a second question: a worktree that
    could not be removed holds its branch checked out, so the branch stays too.
    """

    path: Path
    branch: str
    removed: bool
    branch_removed: bool
    problems: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BuildState:
    """What an attempt to give a tree the build state its commands name did.

    `linked` is the names a symlink was newly created for, `excluded` the names
    newly appended to the repository's common exclude file. Both are empty on a
    second call over the same tree, which is what makes the linking idempotent:
    a name already present is neither relinked nor re-excluded.

    `problems` is what could not be done, in git's own words or the operating
    system's. It is reported rather than raised because no caller refuses on it:
    an interpreter that could not be linked leaves the existing reports for a
    command that could not be run exactly as they are.
    """

    linked: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )


def _problems(result: subprocess.CompletedProcess, fallback: str) -> list[str]:
    """Git's own words, one problem per line, never an empty list on failure.

    A refusal with no problems under it says only that something went wrong, so
    a git that said nothing still reports something.
    """
    lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    return lines or [fallback]


def interpreter_roots(words: Sequence[str | None]) -> list[str]:
    """The top-level directory each configured command word lives under.

    A word that is absolute names something outside the tree, and one with a
    single component is a bare name looked up on PATH; neither has a root inside
    the tree to link, so neither contributes. What is left is the first path
    component, deduplicated and in the order the words were given.

    This is the derivation the clean-clone path made inline before story-119,
    moved here so a worktree and a clone cannot disagree about what an
    interpreter's root is. No directory name is written down: the names come
    from the commands the target already configures, so a target naming its
    environments differently needs no change.
    """
    roots: list[str] = []
    for word in words:
        if not word:
            continue
        path = Path(word)
        if path.is_absolute() or len(path.parts) < 2:
            continue
        root = path.parts[0]
        if root not in roots:
            roots.append(root)
    return roots


def _exclude_file(root: Path) -> tuple[Path | None, list[str]]:
    """The repository's common exclude file for the tree at `root`.

    Found with `git rev-parse --git-common-dir` resolved against the root, which
    answers the per-worktree `.git` file correctly — a worktree's own git
    directory holds no `info/exclude` git reads — and answers `.git` for an
    ordinary clone. One derivation therefore serves both callers.
    """
    result = _git(root, "rev-parse", "--git-common-dir")
    if result.returncode != 0:
        return None, _problems(result, "git could not name the common git directory")
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = root / common
    return common / "info" / "exclude", []


def link_build_state(
    source_root: Path, destination: Path, names: Sequence[str]
) -> BuildState:
    """Link named directories from `source_root` into `destination`, excluded.

    For each name the source holds as a directory and the destination does not
    already have, a symlink is created and the name appended to the
    repository's common exclude file — the exclusion being what keeps the link
    invisible to git, since a `.gitignore` entry naming a directory does not
    match a symlink standing in its place.

    A name already on a line of that file is not appended again, because for a
    worktree that file belongs to the developer's own repository and a run must
    not grow it. Together with skipping a destination that already has the path,
    that makes a second call over the same tree do nothing at all.

    Nothing is created, moved or removed under `source_root`: it is read for
    what it holds and linked to.
    """
    wanted = [
        name
        for name in names
        if (source_root / name).is_dir() and not (destination / name).exists()
    ]
    if not wanted:
        return BuildState()
    exclude, problems = _exclude_file(destination)
    if exclude is None:
        # Nothing is linked where the exclusion cannot be written: a link with
        # no exclusion beside it is a path git reports, and dirtying the tree a
        # run commits is worse than an interpreter that is not there.
        return BuildState(problems=problems)

    linked: list[str] = []
    for name in wanted:
        try:
            (destination / name).symlink_to(
                source_root / name, target_is_directory=True
            )
        except OSError as error:
            problems.append(f"could not link {name} into {destination}: {error}")
            continue
        linked.append(name)

    already = set()
    if exclude.is_file():
        already = {
            line.strip() for line in exclude.read_text(encoding="utf-8").splitlines()
        }
    excluded = [name for name in linked if name not in already]
    if excluded:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with open(exclude, "a", encoding="utf-8") as handle:
            handle.write("\n".join(["", *excluded]) + "\n")
    return BuildState(linked, excluded, problems)


def worktree_root(target_root: Path, config: dict) -> Path:
    """The directory worktrees are created under, derived in one place.

    The configured value when there is one, resolved against the target root
    when it is relative so a target may name a path inside its own parent
    without knowing where that parent is. Unset, a sibling of the repository's
    *primary* working tree, named for that tree — so the answer is the same
    asked from any tree of the repository, and the tree a run happens to be
    invoked from does not decide where the repository's trees live. Asked from
    a linked worktree at `<repo>-worktrees/story-x`, deriving from the target
    root itself would answer `<repo>-worktrees/story-x-worktrees` and nest a
    second directory of working trees inside the first.

    Either way the directory is outside the repository, so nothing the harness
    globs and no clean clone ever meets a worktree.
    """
    configured = config.get(WORKTREE_DIR_KEY)
    if configured:
        path = Path(configured)
        if path.is_absolute():
            return path
        # Normalised rather than joined and left, so a value naming the
        # target root's own parent reads as the directory it is rather than
        # as a path with a `..` in the middle of it.
        return Path(os.path.normpath(target_root / path))
    primary = primary_root(target_root)
    return primary.parent / f"{primary.name}{DEFAULT_SUFFIX}"


def worktree_path(target_root: Path, config: dict, branch: str) -> Path:
    """Where the worktree for `branch` lives, derived in one function.

    A branch name carries slashes and a directory name may not, so the
    separators are flattened. Derived here and nowhere else, so the caller that
    creates a worktree and the caller that looks for one cannot disagree about
    where it would be.
    """
    return worktree_root(target_root, config) / branch.replace("/", "-")


def standing_branch(root: Path) -> str:
    """The branch `root` stands on, or "" when it stands on none.

    A detached HEAD, a path that is not a git tree at all, and a git that
    failed for any other reason all answer "": the one-directional bias every
    other reader of a target repository here takes, where nothing establishable
    is reported as nothing rather than as something false.
    """
    result = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if result.returncode != 0:
        return ""
    name = result.stdout.strip()
    return "" if name in ("", "HEAD") else name


def stands_on(root: Path, branch: str) -> bool:
    """Whether `root` is a working tree standing on `branch`."""
    return bool(branch) and standing_branch(root) == branch


def primary_root(root: Path) -> Path:
    """The repository's primary working tree, for a path anywhere inside it.

    A linked worktree and the checkout it was cut from share one repository,
    and some things belong to the repository rather than to a tree of it —
    where its worktrees are created among them, since a tree cut beside
    whichever tree happened to invoke the harness nests one worktree inside
    another. This is where "which tree is the repository's own" is answered
    for that question. The durable filing queue asks the same question and
    reaches its own answer by its own route; this function is not what it
    calls, and this module names no queue and reaches none.

    Git is asked for the common directory, which is the primary tree's `.git`
    whichever tree the question is asked from; a relative answer is resolved
    against `root`, and that directory's parent is the primary working tree.

    It answers with `root` unchanged wherever git cannot say: a git that
    failed, a directory that is not a repository, an answer that does not
    resolve to a directory that exists, and a derived parent that is not itself
    a working tree — which is what a repository whose primary is bare has. That
    is the one-directional bias `standing_branch` and `working_trees` already
    take in this module: nothing establishable is answered with the path we
    were given rather than with something false, and nothing here raises.

    For a linked worktree the answer is git's own spelling of the primary tree,
    which on a platform whose temporary directories are reached through a
    symlink is the resolved one. A caller comparing it against a path it built
    itself resolves both sides, which is what every worktree comparison in this
    repository's suite already does.
    """
    common = _git(root, "rev-parse", "--git-common-dir")
    if common.returncode != 0:
        return root
    answer = common.stdout.strip()
    if not answer:
        return root
    git_dir = Path(answer)
    if not git_dir.is_absolute():
        git_dir = root / git_dir
    if not git_dir.is_dir():
        return root
    candidate = git_dir.parent
    # A directory beside a git directory is not thereby a working tree: a bare
    # repository's parent is whatever it happens to sit in. Git is asked which
    # tree that directory belongs to, and the answer has to be that directory
    # itself for it to be the primary working tree.
    toplevel = _git(candidate, "rev-parse", "--show-toplevel")
    if toplevel.returncode != 0 or not toplevel.stdout.strip():
        return root
    if Path(toplevel.stdout.strip()).resolve() != candidate.resolve():
        return root
    return candidate


def working_trees(root: Path) -> list[tuple[Path, str]]:
    """Every working tree this repository has, as (path, branch) pairs.

    Parsed from `git worktree list --porcelain`, whose output is one block per
    tree: a `worktree <path>` line, then `HEAD <sha>`, then either
    `branch refs/heads/<name>` or `detached`. A detached tree reports "" for
    its branch, which is what `standing_branch` answers for one as well.
    """
    result = _git(root, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        return []
    found: list[tuple[Path, str]] = []
    path: Path | None = None
    branch = ""
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if path is not None:
                found.append((path, branch))
            path = Path(line[len("worktree "):])
            branch = ""
        elif line.startswith("branch refs/heads/"):
            branch = line[len("branch refs/heads/"):]
    if path is not None:
        found.append((path, branch))
    return found


def find(root: Path, branch: str) -> Path | None:
    """The existing worktree standing on `branch`, or None when there is none.

    A run whose branch already has a worktree works in that worktree rather
    than creating a second one, which is what stops two runs of one story
    standing in two trees on one branch — a thing git refuses anyway, and would
    refuse halfway through creating the second.
    """
    if not branch:
        return None
    for path, standing in working_trees(root):
        if standing == branch:
            return path
    return None


def add(
    root: Path, path: Path, branch: str, start_point: str | None = None
) -> WorktreeResult:
    """Create a worktree at `path` standing on `branch`.

    `start_point` names what a branch that does not exist yet is cut from; with
    none the branch is expected to exist already and is simply checked out
    there. A path that already holds a worktree for that branch is not an
    error — the caller asked for a tree on that branch and there is one.
    """
    standing = find(root, branch)
    if standing is not None and standing.resolve() == path.resolve():
        return WorktreeResult(path, branch)
    path.parent.mkdir(parents=True, exist_ok=True)
    if start_point is None:
        args = ["worktree", "add", str(path), branch]
    else:
        args = ["worktree", "add", "-b", branch, str(path), start_point]
    result = _git(root, *args)
    if result.returncode != 0:
        return WorktreeResult(
            path, branch, _problems(result, "git refused to add the worktree")
        )
    return WorktreeResult(path, branch)


def add_detached(root: Path, path: Path, start_point: str) -> WorktreeResult:
    """Create a worktree at `path` standing on no branch, at `start_point`.

    What planning uses: the session happens on a detached checkout of the base,
    and the branch is named afterwards from the id the artifact carries rather
    than from the id the invocation guessed it would carry.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    result = _git(root, "worktree", "add", "--detach", str(path), start_point)
    if result.returncode != 0:
        return WorktreeResult(
            path, "", _problems(result, "git refused to add the worktree")
        )
    return WorktreeResult(path, "")


def create_branch(root: Path, branch: str) -> list[str]:
    """Name the branch a detached worktree is standing on, at where it stands.

    Reported rather than raised, one problem per line git wrote, so a caller
    refuses the way its neighbours refuse.
    """
    result = _git(root, "checkout", "-b", branch)
    if result.returncode != 0:
        return _problems(result, "git refused to create the branch")
    return []


def remove(root: Path, path: Path, branch: str = "") -> Removal:
    """Remove a worktree and, when named, the local branch it stood on.

    The branch is deleted only once the worktree is gone, because git will not
    delete a branch a worktree has checked out — so a worktree that could not
    be removed keeps its branch, which is reported rather than attempted.

    Nothing here is conditional on what the worktree contains: a caller decides
    whether removal is safe. This repository's callers remove one only after the
    push that made the work durable elsewhere has landed.
    """
    removed = _git(root, "worktree", "remove", "--force", str(path))
    if removed.returncode != 0:
        return Removal(
            path,
            branch,
            False,
            False,
            _problems(removed, "git refused to remove the worktree"),
        )
    if not branch:
        return Removal(path, branch, True, False)
    deleted = _git(root, "branch", "-D", branch)
    if deleted.returncode != 0:
        return Removal(
            path,
            branch,
            True,
            False,
            _problems(deleted, "git refused to delete the branch"),
        )
    return Removal(path, branch, True, True)
