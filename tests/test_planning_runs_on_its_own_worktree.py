"""story-117: planning and running each happen on a worktree and branch of their own.

Everything here is built under a temporary directory: a target repository, a
*bare* repository standing in for its remote, and a stub `claude` on PATH. No
test reaches the network — the "remote" is a directory — and none of them reads
this repository's own commit graph, because the subject is what the harness
does to a repository rather than what this repository's history holds.

The module is organised by the acts the story adds: where a worktree lives, who
reserves an id and who consumes or releases one, what cleanup is conditional
on, and which tree a run works in.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
import harness_config
import plan_commit
import story_coordinator
import story_ids
import worktrees
from agent_runner import AgentResult

HARNESS_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
L5_PLAN = HARNESS_ROOT / "scripts" / "l5-plan"
DEFAULT_BRANCH = "main"

STORY_ID = "story-001"

CONFIG = """\
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
tests_dir: tests/
"""

ARTIFACT = """\
story:
  id: {story_id}
  title: {title}
  description: |
    A story planned by the stub session.
  workflow: story-workflow

tasks:
  - do the thing

acceptance_criteria:
  - the thing is done

technical_plan:
  implementation_steps:
    - do it

scope:
  modify:
    - src/
  do_not_modify:
    - rules/

verification_requirements:
  - confirm the behaviour

constraints:
  - keep it simple
"""

#: A stub `claude` that writes the files named in L5_STUB_WRITE, relative to
#: the working directory it is given — which is the whole of how these tests
#: observe *where* the session ran.
STUB = """\
#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

log = os.environ.get("L5_STUB_LOG")
if log:
    Path(log).write_text(json.dumps({"argv": sys.argv, "cwd": os.getcwd()}),
                         encoding="utf-8")
for relative, text in json.loads(os.environ.get("L5_STUB_WRITE", "{}")).items():
    path = Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
sys.exit(int(os.environ.get("L5_STUB_EXIT", "0")))
"""


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class Target:
    """A target repository, its bare remote, and the stub the session runs."""

    def __init__(self, root: Path, bin_dir: Path, log: Path,
                 remote: Path | None = None):
        self.root = root
        self.bin_dir = bin_dir
        self.log = log
        self.remote = remote

    # -- reading the repository ------------------------------------------
    def git(self, *args: str) -> subprocess.CompletedProcess:
        return git(self.root, *args)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.strip()

    def branch(self) -> str:
        return self.git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    def status(self) -> str:
        return self.git("status", "--porcelain", "-uall").stdout

    def index(self) -> str:
        return self.git("ls-files", "--stage").stdout

    def branches(self) -> list[str]:
        return sorted(line[2:].strip()
                      for line in self.git("branch", "--list").stdout.splitlines())

    def config(self) -> dict:
        return harness_config.load_config(self.root)

    def story_branch(self, story_id: str) -> str:
        return story_coordinator.story_branch(self.config(), story_id)

    def worktree_for(self, story_id: str) -> Path:
        return worktrees.worktree_path(
            self.root, self.config(), self.story_branch(story_id))

    # -- running the script ----------------------------------------------
    def env(self, **stub) -> dict:
        environment = dict(os.environ)
        environment["PATH"] = f"{self.bin_dir}{os.pathsep}{environment['PATH']}"
        environment["L5_STUB_LOG"] = str(self.log)
        environment.update({key: str(value) for key, value in stub.items()})
        return environment

    def session(self) -> dict:
        return json.loads(self.log.read_text(encoding="utf-8"))

    def plan(self, *argv: str, **stub) -> subprocess.CompletedProcess:
        """One l5-plan invocation, with a terminal for stdin.

        The terminal is what lets the session reach the mandate stamp and the
        commit; the reply approves the plan and declines the run offer, which
        is the ordinary path a developer takes.
        """
        with conftest.a_terminal_for_stdin() as stdin:
            return subprocess.run(
                [sys.executable, str(L5_PLAN), "--workflow", "story-workflow",
                 *argv],
                cwd=self.root, env=self.env(**stub), stdin=stdin,
                capture_output=True, text=True,
            )


def build_target(root: Path, *, with_remote: bool = True) -> Target:
    """A target repository, optionally tracking a bare repository as origin."""
    write(root / ".harness" / "config.yaml", CONFIG)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "src" / "app.py", "print('hello')\n")
    write(root / ".gitignore", ".harness/runs/\n.harness/logs/\n")
    (root / ".harness" / "stories").mkdir(parents=True, exist_ok=True)
    conftest.init_repository(root, "initial")
    git(root, "branch", "-M", DEFAULT_BRANCH)

    bin_dir = root.parent / f"{root.name}-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "claude"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)

    remote = None
    if with_remote:
        remote = root.parent / f"{root.name}-origin.git"
        # `cwd` stated, as every git call in this suite must state where it
        # runs: the path argument says what to create, and this says the call
        # is not about the repository the suite is running in.
        # The bare repository's HEAD is named rather than inherited. Left to
        # `git init`, HEAD points at the machine's `init.defaultBranch`, which
        # this fixture then never creates — so a clone of this remote checks
        # out nothing and every file the clone is asked for is absent.
        subprocess.run(["git", "init", "--bare", "-q", "-b", DEFAULT_BRANCH,
                        str(remote)],
                       cwd=str(root.parent), check=True)
        git(root, "remote", "add", "origin", str(remote))
        git(root, "push", "-q", "-u", "origin", DEFAULT_BRANCH)
    return Target(root, bin_dir, root.parent / f"{root.name}-session.json", remote)


@pytest.fixture
def target(tmp_path: Path) -> Target:
    return build_target(tmp_path / "target")


def remote_refs(remote: Path) -> dict:
    listed = subprocess.run(
        ["git", "-C", str(remote), "for-each-ref",
         "--format=%(refname) %(objectname)"],
        capture_output=True, text=True, check=True)
    return dict(line.split() for line in listed.stdout.splitlines() if line.strip())


def planned(story_id: str = "story-001", title: str = "A planned story") -> str:
    return json.dumps({
        f".harness/stories/{story_id}.yaml":
            ARTIFACT.format(story_id=story_id, title=title)})


# --------------------------------------------------------------------------
# Where a worktree lives, and that planning happens in it
# --------------------------------------------------------------------------


def test_planning_happens_in_a_worktree_outside_the_target_root(target: Target):
    """The session's working directory is the worktree, and it is outside the
    repository — so nothing this mechanism creates is ever inside it.

    The session is observed by where it wrote: the stub writes its artifact
    relative to the directory it was given, and that artifact is in the
    worktree rather than in the developer's checkout.
    """
    result = target.plan("a request", L5_STUB_WRITE=planned())
    assert result.returncode == 0, result.stdout + result.stderr

    session_cwd = Path(target.session()["cwd"]).resolve()
    root = worktrees.worktree_root(target.root, target.config())
    assert session_cwd.parent == root.resolve()
    assert root.resolve() not in target.root.resolve().parents
    assert not str(root.resolve()).startswith(str(target.root.resolve()) + os.sep)
    # The default is a sibling of the target root named for it.
    assert root == target.root.parent / f"{target.root.name}-worktrees"


def test_the_worktree_dir_key_is_where_the_planning_worktree_is_created(
        tmp_path: Path):
    """The configured directory is obeyed, varied to a value the harness would
    never pick and relative so the resolution against the target root is
    exercised too.

    The control is the sibling the harness picks for itself, which is not
    created at all — so the assertion is the key being read rather than a
    directory that would have been there anyway.
    """
    target = build_target(tmp_path / "configured")
    config = target.root / ".harness" / "config.yaml"
    config.write_text(config.read_text(encoding="utf-8")
                      + "worktree_dir: ../xyzzy-planning\n", encoding="utf-8")
    conftest.commit_setup(target.root, "configure worktree_dir")
    target.git("push", "-q", "origin", DEFAULT_BRANCH)

    result = target.plan("a request", L5_STUB_WRITE=planned())
    assert result.returncode == 0, result.stdout + result.stderr

    configured = target.root.parent / "xyzzy-planning"
    assert Path(target.session()["cwd"]).resolve().parent == configured.resolve()
    assert not (target.root.parent / f"{target.root.name}-worktrees").exists()


def test_a_planning_invocation_leaves_the_developers_checkout_exactly_as_it_was(
        target: Target):
    """The branch, the working tree, the index and the commit HEAD stands on,
    compared before and after — the property the story exists for.

    The developer's own uncommitted work is in the comparison rather than
    described: a modified tracked file, an untracked one and a staged one, none
    of which the planning session may see, absorb or disturb.
    """
    write(target.root / "src" / "app.py", "print('hello')\n# mine\n")
    write(target.root / "mine.txt", "untracked and mine\n")
    write(target.root / "staged.txt", "staged and mine\n")
    target.git("add", "staged.txt")
    before = (target.branch(), target.head(), target.status(), target.index())

    result = target.plan("a request", L5_STUB_WRITE=planned())
    assert result.returncode == 0, result.stdout + result.stderr

    assert (target.branch(), target.head(), target.status(),
            target.index()) == before
    # The control for that equality: the same four readings do change when
    # something really does touch this tree.
    target.git("commit", "-q", "-am", "the developer commits their own work")
    assert (target.branch(), target.head(), target.status(),
            target.index()) != before


# --------------------------------------------------------------------------
# The reservation: claiming an id on the remote
# --------------------------------------------------------------------------


def test_the_id_is_claimed_by_pushing_a_ref_that_must_not_already_exist(
        target: Target):
    """The claim creates the story branch's ref on the remote, and a second
    claim of the same id is refused rather than overwriting it.

    The refusal is the remote's, which is what makes two clones unable to plan
    the same id: the expected value in the lease is empty, so the ref must not
    be there.
    """
    branch = target.story_branch("story-042")
    made, detail = story_ids.claim(target.root, "origin", branch, DEFAULT_BRANCH)
    assert made, detail
    assert f"refs/heads/{branch}" in remote_refs(target.remote)
    held = remote_refs(target.remote)[f"refs/heads/{branch}"]

    target.git("commit", "-q", "--allow-empty", "-m", "a second clone moves on")
    again, refusal = story_ids.claim(
        target.root, "origin", branch, DEFAULT_BRANCH)

    assert not again
    assert refusal
    assert remote_refs(target.remote)[f"refs/heads/{branch}"] == held


def test_a_claim_on_a_ref_standing_at_the_very_commit_being_pushed_is_refused(
        target: Target):
    """The case a lease alone cannot answer, and the one an id collides on.

    Two invocations from the same clone with the base unmoved between them push
    the identical commit to the identical ref. git has nothing to do, so it
    exits zero saying "Everything up-to-date" without ever consulting the
    lease — and an id somebody else already holds at this base would read as
    free, which is exactly the id two clones would then both plan. The claim is
    therefore decided on whether the push *created* the ref rather than on its
    exit status.

    The control is the first claim above it: the same call against a ref that
    is not there does create it and is made, so a refusal here is the ref
    already existing rather than this reader refusing everything.
    """
    branch = target.story_branch("story-042")
    made, detail = story_ids.claim(target.root, "origin", branch, DEFAULT_BRANCH)
    assert made, detail
    held = remote_refs(target.remote)[f"refs/heads/{branch}"]

    again, refusal = story_ids.claim(
        target.root, "origin", branch, DEFAULT_BRANCH)

    assert not again, "a ref that is already there was read as a free id"
    assert refusal
    assert remote_refs(target.remote)[f"refs/heads/{branch}"] == held


def test_a_refused_claim_takes_the_next_id_and_the_retry_is_bounded(
        target: Target):
    """Two ids already claimed, so the reservation walks past both.

    And the bound: with the same remote refusing every claim, the reservation
    stops at the bound it was given and says what the bound was, rather than
    walking for ever.
    """
    config = target.config()
    # Claimed at a commit the reservation below will not be pushing, so the
    # remote is deciding on the ref being there rather than on the push having
    # nothing to do.
    target.git("commit", "-q", "--allow-empty", "-m", "the base moves on")
    for number in (1, 2):
        made, detail = story_ids.claim(
            target.root, "origin",
            story_coordinator.story_branch(config, f"story-{number:03d}"),
            "HEAD~1")
        assert made, detail

    reservation = story_ids.reserve(target.root, config, DEFAULT_BRANCH, "origin")
    assert reservation.reserved
    assert reservation.story_id == "story-003"
    assert reservation.attempts == 3

    # The bound. The base moves again, so the three ids already on the remote
    # are refused by the lease rather than being pushes with nothing to do, and
    # a hook that exits non-zero refuses every id past them.
    target.git("commit", "-q", "--allow-empty", "-m", "the base moves again")
    hook = target.remote / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho refused >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    bounded = story_ids.reserve(
        target.root, config, DEFAULT_BRANCH, "origin", maximum=3)
    assert not bounded.reserved
    assert bounded.attempts == 3
    assert any("3" in problem and "bound" in problem
               for problem in bounded.problems), bounded.problems


def test_a_repository_with_no_remote_plans_on_a_local_claim_and_says_so(
        tmp_path: Path):
    """No remote to ask, so nothing is reserved and the reservation says that
    outright rather than presenting a local decision as a claim.

    The control is the same call in a repository that does have a remote,
    where the same fields say the opposite.
    """
    remoteless = build_target(tmp_path / "remoteless", with_remote=False)
    reservation = story_ids.reserve(
        remoteless.root, remoteless.config(), "HEAD", "")

    assert reservation.local is True
    assert reservation.remote == ""
    assert reservation.story_id == "story-001"

    with_remote = build_target(tmp_path / "with-remote")
    claimed = story_ids.reserve(
        with_remote.root, with_remote.config(), DEFAULT_BRANCH, "origin")
    assert claimed.local is False
    assert claimed.remote == "origin"


def test_a_plan_commit_consumes_the_reservation_and_releases_nothing(
        target: Target):
    """A successful planning session leaves the story branch on the remote and
    no other ref behind: the commit is pushed onto the ref the claim created.

    The refs the remote holds are compared before and after, so a claim left
    dangling under some other id would be seen rather than assumed absent.
    """
    before = set(remote_refs(target.remote))

    result = target.plan("a request", L5_STUB_WRITE=planned())
    assert result.returncode == 0, result.stdout + result.stderr

    gained = set(remote_refs(target.remote)) - before
    assert len(gained) == 1
    branch = gained.pop().removeprefix("refs/heads/")
    assert branch.startswith(target.config()["branch_prefix"])
    # And that ref carries the plan commit rather than the base it was
    # claimed at: the reservation was consumed rather than merely made.
    subject = subprocess.run(
        ["git", "-C", str(target.remote), "log", "-1", "--format=%s", branch],
        capture_output=True, text=True).stdout.strip()
    assert subject.startswith("Plan ")


def test_an_artifact_carrying_another_id_branches_on_it_and_releases_the_claim(
        tmp_path: Path):
    """The session wrote a story under an id other than the one reserved for
    it, which is the one case where the reservation and the artifact disagree.

    The artifact wins, because the artifact is what the run will be about: the
    disagreement is reported naming both ids, the branch carries the artifact's
    id, and the claim nothing consumed is given back rather than left as a
    silently burnt id.

    The control is the agreeing path in the same reading — a second target
    whose session writes the id it was reserved — where the same three
    questions answer the other way: no disagreement is reported, nothing is
    released, and the ref the claim created is the one the plan commit lands
    on. Without it, "the reserved ref is gone" would pass just as happily for a
    harness that never claimed a ref at all.
    """
    disagreeing = build_target(tmp_path / "disagreeing")
    before = set(remote_refs(disagreeing.remote))

    result = disagreeing.plan("a request", L5_STUB_WRITE=planned("story-777"))
    assert result.returncode == 0, result.stdout + result.stderr

    reserved = result.stdout.split("l5-plan: reserved ")[1].split()[0]
    assert reserved != "story-777", result.stdout
    # (a) both ids named, and the branch said to carry the artifact's.
    assert reserved in result.stdout
    assert "story-777" in result.stdout
    assert "the id the artifact carries" in result.stdout
    assert disagreeing.story_branch("story-777") in result.stdout

    gained = set(remote_refs(disagreeing.remote)) - before
    # (b) the ref the remote gained is the artifact's branch, carrying the plan
    # commit, and (c) the reserved id's ref is not there.
    assert gained == {f"refs/heads/{disagreeing.story_branch('story-777')}"}
    assert f"refs/heads/{disagreeing.story_branch(reserved)}" \
        not in remote_refs(disagreeing.remote)
    assert "released the claim on " + reserved in result.stdout

    # The control: the agreeing path, where each of the three answers inverts.
    agreeing = build_target(tmp_path / "agreeing")
    also_before = set(remote_refs(agreeing.remote))
    control = agreeing.plan("a request", L5_STUB_WRITE=planned("story-001"))
    assert control.returncode == 0, control.stdout + control.stderr

    assert control.stdout.split("l5-plan: reserved ")[1].split()[0] == "story-001"
    assert "the id the artifact carries" not in control.stdout
    assert "released the claim" not in control.stdout
    assert set(remote_refs(agreeing.remote)) - also_before \
        == {f"refs/heads/{agreeing.story_branch('story-001')}"}
    landed = subprocess.run(
        ["git", "-C", str(agreeing.remote), "log", "-1", "--format=%s",
         agreeing.story_branch("story-001")],
        capture_output=True, text=True).stdout.strip()
    assert landed.startswith("Plan ")


def test_a_session_that_writes_no_artifact_releases_its_claim(target: Target):
    """Nothing was planned, so the id goes back: the ref the claim created is
    deleted and the remote holds exactly what it held before.

    The control is the session above that did write one, which leaves a ref
    behind — so the emptiness here is the release and not a claim that never
    happened.
    """
    before = set(remote_refs(target.remote))

    result = target.plan("a request")          # the stub writes nothing
    assert result.returncode == 0, result.stdout + result.stderr

    assert set(remote_refs(target.remote)) == before
    assert "released" in result.stdout or "release" in result.stdout


def test_a_release_deletes_the_ref_and_reports_one_it_could_not(target: Target):
    """`release` directly, both ways round.

    A ref that is there is deleted and reported released; a remote that
    refuses the delete is reported rather than passed over, which is the
    control for the first — a release that could not fail would say nothing
    about whether the delete happened.
    """
    branch = target.story_branch("story-050")
    story_ids.claim(target.root, "origin", branch, DEFAULT_BRANCH)
    assert f"refs/heads/{branch}" in remote_refs(target.remote)

    released = story_ids.release(target.root, "origin", branch)
    assert released.released, released.detail
    assert f"refs/heads/{branch}" not in remote_refs(target.remote)

    other = target.story_branch("story-051")
    story_ids.claim(target.root, "origin", other, DEFAULT_BRANCH)
    hook = target.remote / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho refused >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    refused = story_ids.release(target.root, "origin", other)
    assert not refused.released
    assert refused.detail
    assert f"refs/heads/{other}" in remote_refs(target.remote)


def test_the_planner_is_told_the_reserved_id_rather_than_deriving_one(
        target: Target):
    """The rendered prompt names an id, and the shipped template asks for that
    id rather than for a listing of the stories directory.

    The template is the subject here — it is what this harness ships to a
    planner — so it is read as shipped; the id itself is read off the render,
    which is what the session was actually handed.
    """
    result = target.plan("a request", L5_STUB_WRITE=planned())
    assert result.returncode == 0, result.stdout + result.stderr

    argv = target.session()["argv"]
    prompt = argv[argv.index("--append-system-prompt") + 1]
    assert "The story id is story-" in prompt
    assert "None" not in prompt.split("The story id is ")[1].split("\n")[0]

    template = (HARNESS_ROOT / "prompts" / "planner.md").read_text(
        encoding="utf-8")
    assert "{{story_id}}" in template
    assert "Do not derive an" in template


# --------------------------------------------------------------------------
# Cleanup, conditional on the push
# --------------------------------------------------------------------------


def test_declining_the_offer_after_a_landed_push_removes_the_worktree(
        target: Target):
    """The plan survives on the remote and the developer's checkout is as it
    was, so there is nothing in the worktree to keep — it and the local branch
    both go, and the message says where the plan is and how to run it.
    """
    result = target.plan("a request", L5_STUB_WRITE=planned())
    assert result.returncode == 0, result.stdout + result.stderr

    planned_branch = [ref.removeprefix("refs/heads/")
                      for ref in remote_refs(target.remote)
                      if ref.startswith("refs/heads/story/")]
    assert len(planned_branch) == 1
    branch = planned_branch[0]

    assert worktrees.find(target.root, branch) is None
    assert branch not in target.branches()
    assert not worktrees.worktree_path(target.root, target.config(), branch).exists()
    assert "l5-run" in result.stdout


def test_a_push_that_failed_removes_neither_and_names_them(tmp_path: Path):
    """The commit is only in that worktree, so removing either would be the one
    act that loses the plan. Both are named because a developer whose push
    failed needs to know where to push from.

    The remote refuses the plan branch and accepts the claim, so the failure is
    the push rather than anything earlier.
    """
    target = build_target(tmp_path / "rejecting")
    branch = target.story_branch("story-001")
    hook = target.remote / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        "#!/bin/sh\n"
        "while read old new ref; do\n"
        f"  if [ \"$old\" != 0000000000000000000000000000000000000000 ] && "
        f"[ \"$ref\" = \"refs/heads/{branch}\" ]; then\n"
        "    echo refusing >&2; exit 1\n"
        "  fi\n"
        "done\n"
        "exit 0\n",
        encoding="utf-8")
    hook.chmod(0o755)

    result = target.plan("a request", L5_STUB_WRITE=planned())

    assert result.returncode != 0
    worktree = worktrees.find(target.root, branch)
    assert worktree is not None, result.stdout
    assert branch in target.branches()
    assert str(worktree) in result.stdout
    assert branch in result.stdout
    assert (worktree / ".harness" / "stories" / "story-001.yaml").is_file()


def test_a_session_whose_artifact_is_refused_keeps_its_worktree_and_names_it(
        target: Target):
    """An artifact that does not validate is not committed, and the tree it is
    in is where a developer has to go to repair it — so it is kept and named.
    """
    result = target.plan("a request", L5_STUB_WRITE=json.dumps(
        {".harness/stories/story-001.yaml": "this: is: not: a story\n\t- ?\n"}))

    assert result.returncode != 0
    assert "kept the worktree" in result.stdout
    kept = Path(result.stdout.split("kept the worktree ")[1].split(";")[0])
    assert kept.is_dir()
    assert (kept / ".harness" / "stories" / "story-001.yaml").is_file()


def test_an_unreachable_remote_refuses_before_anything_is_created(
        tmp_path: Path):
    """Above the worktree, above the session and above anything written: the
    remote is asked first, and a remote that cannot be reached ends it there.

    The control is the same repository with the remote pointed back at the
    bare repository, which plans.
    """
    target = build_target(tmp_path / "unreachable")
    target.git("remote", "set-url", "origin",
               str(tmp_path / "nothing-is-here.git"))
    root = worktrees.worktree_root(target.root, target.config())

    result = target.plan("a request", L5_STUB_WRITE=planned())

    assert result.returncode != 0
    assert not root.exists(), "a worktree was created"
    assert not target.log.exists(), "the session was invoked"
    assert not (target.root / ".harness" / "stories" / "story-001.yaml").exists()

    target.git("remote", "set-url", "origin", str(target.remote))
    assert target.plan("a request", L5_STUB_WRITE=planned()).returncode == 0


# --------------------------------------------------------------------------
# The tree a run works in
# --------------------------------------------------------------------------


class Runner:
    """A fake agent runner: every stage writes what its declaration requires,
    into the run directory it is handed, and edits the tree it is given."""

    def __init__(self, workflow: dict):
        self.workflow = workflow
        self.calls: list[str] = []
        self.trees: list[Path] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None, run_dir=None, **extra):
        self.calls.append(stage)
        self.trees.append(Path(cwd))
        declaration = next(s for s in self.workflow["stages"]
                           if s["name"] == stage)
        directory = Path(run_dir)
        for artifact in story_coordinator.required_artifacts(declaration):
            path = directory / artifact
            path.parent.mkdir(parents=True, exist_ok=True)
            if artifact == conftest.VERIFICATION_RESULT:
                path.write_text(json.dumps({
                    "status": "passed", "blocking_issues": [],
                    "unverified": [], "retry_recommended": False,
                }), encoding="utf-8")
            elif artifact.endswith("changed-files.json"):
                path.write_text(json.dumps(
                    {"modified": [], "created": [], "deleted": []}),
                    encoding="utf-8")
            elif artifact == conftest.TEST_RESULTS:
                path.write_text(json.dumps({"tests_written": 1}),
                                encoding="utf-8")
            else:
                path.write_text(f"{stage} wrote this.\n", encoding="utf-8")
        return AgentResult(ok=True, result_text=f"{stage} done")


WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result"}),
    name="worktree-workflow",
)


@pytest.fixture
def harness(tmp_path: Path) -> Path:
    return conftest.materialize_workflow(WORKFLOW, tmp_path / "worktree-harness")


def planned_story(target: Target, story_id: str = STORY_ID) -> None:
    """A story artifact committed on the target's own branch."""
    write(target.root / ".harness" / "stories" / f"{story_id}.yaml",
          conftest.STORY.replace("story-001", story_id)
          .replace("workflow: story-workflow",
                   f"workflow: {WORKFLOW['name']}"))
    config = target.root / ".harness" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "workflow: story-workflow", f"workflow: {WORKFLOW['name']}"),
        encoding="utf-8")
    conftest.commit_setup(target.root, "the story to run")
    target.git("push", "-q", "origin", DEFAULT_BRANCH)


def run(target: Target, harness: Path, story_id: str = STORY_ID):
    runner = Runner(WORKFLOW)
    code = story_coordinator.run_story(story_id, harness, target.root, runner)
    return code, runner


def test_a_fresh_run_cuts_a_worktree_and_works_in_it(target: Target, harness):
    """The run directory, the state and the stage's working directory are all
    in the worktree, and the developer's checkout is where it was.

    The control for "the developer's checkout is untouched" is the worktree
    itself, which the same readings do report as standing on the story branch.
    """
    planned_story(target)
    before = (target.branch(), target.head())

    code, runner = run(target, harness)
    assert code == 0, runner.calls

    tree = target.worktree_for(STORY_ID)
    assert tree.is_dir()
    assert worktrees.standing_branch(tree) == target.story_branch(STORY_ID)
    assert (tree / ".harness" / "runs" / STORY_ID / "state.json").is_file()
    assert runner.trees == [tree] * len(runner.calls)
    assert (target.branch(), target.head()) == before
    assert not (target.root / ".harness" / "runs" / STORY_ID).exists()


def test_a_run_invoked_from_a_tree_already_on_the_branch_creates_no_worktree(
        target: Target, harness):
    """The plan-run offer's path: the developer's session is already standing
    on the story branch, so that tree is the run's and nothing is created.
    """
    planned_story(target)
    branch = target.story_branch(STORY_ID)
    target.git("checkout", "-q", "-b", branch)

    code, runner = run(target, harness)
    assert code == 0, runner.calls

    assert runner.trees == [target.root] * len(runner.calls)
    assert (target.root / ".harness" / "runs" / STORY_ID / "state.json").is_file()
    assert not target.worktree_for(STORY_ID).exists()
    assert [path for path, _ in worktrees.working_trees(target.root)] \
        == [target.root]


def test_a_run_whose_branch_already_has_a_worktree_runs_in_that_one(
        target: Target, harness):
    """A second worktree for one branch is what git refuses and what would put
    two runs of one story in two trees, so a run finds the tree that is there.
    """
    planned_story(target)
    branch = target.story_branch(STORY_ID)
    elsewhere = target.root.parent / "somewhere-else-entirely"
    made = worktrees.add(target.root, elsewhere, branch, DEFAULT_BRANCH)
    assert not made.problems, made.problems

    code, runner = run(target, harness)
    assert code == 0, runner.calls

    assert runner.trees == [elsewhere] * len(runner.calls)
    assert (elsewhere / ".harness" / "runs" / STORY_ID / "state.json").is_file()
    assert not target.worktree_for(STORY_ID).exists()


def test_a_run_from_a_dirty_checkout_is_not_refused_and_commits_none_of_it(
        target: Target, harness):
    """The clean-tree pre-flight reads the tree the run works in, and a fresh
    run's tree is one it just cut — so the developer's own uncommitted work
    neither refuses the run nor reaches a commit it makes.

    The control is the same file made uncommitted in the run's *own* tree,
    where the same pre-flight does refuse.
    """
    planned_story(target)
    write(target.root / "mine.txt", "the developer's own, uncommitted\n")

    code, runner = run(target, harness)
    assert code == 0, runner.calls

    tree = target.worktree_for(STORY_ID)
    committed = git(tree, "log", "--name-only", "--format=",
                    f"{DEFAULT_BRANCH}..HEAD").stdout.split()
    assert "mine.txt" not in committed
    assert not (tree / "mine.txt").exists()
    assert (target.root / "mine.txt").is_file()

    # The control: the same dirtiness in the run's own tree refuses a resume.
    state = story_coordinator.load_state(
        tree / ".harness" / "runs" / STORY_ID)
    state.status = "escalated"
    story_coordinator.save_state(tree / ".harness" / "runs" / STORY_ID, state)
    write(tree / "half-finished.py", "value = ")
    refused, blocked = run(target, harness)
    assert refused == 1
    assert blocked.calls == []


def test_a_story_that_lives_only_on_its_branch_is_fetched_and_run(
        tmp_path: Path, harness):
    """A clone that never planned the story: the artifact is on the story
    branch on the remote and in no tree here, which is exactly what a declined
    run offer leaves behind.

    The control is the same clone asked for a story no branch carries, which
    is refused and says so.
    """
    origin = build_target(tmp_path / "origin-side")
    planned_story(origin)
    branch = origin.story_branch(STORY_ID)
    origin.git("push", "-q", "origin", f"{DEFAULT_BRANCH}:{branch}")

    clone = tmp_path / "fresh-clone"
    # `cwd` stated, for the reason `build_target`'s bare init states it: both
    # paths are arguments, and this says the call is not about the repository
    # the suite is running in.
    subprocess.run(["git", "clone", "-q", str(origin.remote), str(clone)],
                   cwd=str(tmp_path), check=True)
    git(clone, "config", "user.email", "test@example.com")
    git(clone, "config", "user.name", "Test")
    # The story is on the branch and not in this tree, which is the state the
    # resolution exists for.
    git(clone, "rm", "-q", "--cached", f".harness/stories/{STORY_ID}.yaml")
    (clone / ".harness" / "stories" / f"{STORY_ID}.yaml").unlink()
    git(clone, "commit", "-q", "-m", "this clone has no story artifact")
    assert not (clone / ".harness" / "stories" / f"{STORY_ID}.yaml").exists()

    runner = Runner(WORKFLOW)
    assert story_coordinator.run_story(STORY_ID, harness, clone, runner) == 0
    assert runner.calls

    missing = Runner(WORKFLOW)
    assert story_coordinator.run_story("story-404", harness, clone, missing) == 1
    assert missing.calls == []


def test_no_glob_clean_clone_or_blocked_path_gains_an_entry_for_worktrees(
        target: Target, harness):
    """Nothing the harness sweeps, clones or blocks knows about worktrees,
    because they are outside the repository — which is the whole reason the
    default is a sibling directory.

    Absence with its control in the same reading: the blocked-path list does
    hold the entries it has always held, and the clean clone this run builds
    does get built from a worktree and pass.
    """
    planned_story(target)
    code, runner = run(target, harness)
    assert code == 0, runner.calls

    blocked = harness_config.load_rules(harness)["blocked_paths"]
    assert blocked, "the blocked-path list was read as empty"
    assert not [entry for entry in blocked if "worktree" in entry]

    tree = target.worktree_for(STORY_ID)
    assert not [path for path in git(tree, "ls-files").stdout.split()
                if "worktree" in path]
    # The clean clone is built from the tree the run works in, which is a
    # worktree, and it still builds: the run reached completion above, and a
    # clone that could not be built would have ended it.
    assert story_coordinator.load_state(
        tree / ".harness" / "runs" / STORY_ID).status == "completed"


# --------------------------------------------------------------------------
# The base check, asked without its HEAD leg
# --------------------------------------------------------------------------


def test_the_head_leg_is_dropped_for_a_caller_that_cuts_from_a_named_ref(
        target: Target):
    """One repository in one state, asked both ways.

    Asked as a caller that cuts from HEAD, the leg reports where the developer
    is standing. Asked as a caller that cuts from the base by name — which
    since this story is every caller in the harness — it says nothing, while
    the ref-resolves question and the base-agrees-with-its-remote leg are
    asked either way.
    """
    target.git("checkout", "-q", "-b", "feature-elsewhere")

    from_head = story_coordinator.base_problems(
        target.root, DEFAULT_BRANCH, False)
    assert len(from_head) == 1
    assert "feature-elsewhere" in from_head[0]

    assert story_coordinator.base_problems(
        target.root, DEFAULT_BRANCH, False, from_head=False) == []
    assert story_coordinator.base_problems(
        target.root, "no-such-ref", True, from_head=False) != []

    target.git("commit", "-q", "--allow-empty", "-m", "never pushed")
    target.git("checkout", "-q", DEFAULT_BRANCH)
    target.git("merge", "-q", "feature-elsewhere")
    drifted = story_coordinator.base_problems(
        target.root, DEFAULT_BRANCH, False, from_head=False)
    assert len(drifted) == 1
    assert "origin/main" in drifted[0]


def test_the_run_and_the_plan_both_pass_from_head_false(target: Target):
    """Stated where it is decided rather than only through what it produces:
    both callers that cut a branch into a worktree drop the leg.

    Read out of the two files as source rather than run, because what is being
    asserted is which argument the call site passes — and the control is that
    the same reading finds the parameter's definition with its default.
    """
    coordinator = (HARNESS_ROOT / "orchestration" / "story_coordinator.py"
                   ).read_text(encoding="utf-8")
    script = L5_PLAN.read_text(encoding="utf-8")

    assert "from_head: bool = True" in coordinator
    assert "from_head=False" in coordinator
    assert "from_head=False" in script


# --------------------------------------------------------------------------
# The offer runs in the planning worktree
# --------------------------------------------------------------------------


def test_launch_run_starts_the_run_in_the_directory_it_is_given(tmp_path: Path):
    """`launch_run` takes the working directory the run is to start in, which
    is what makes an accepted offer continue in the planning worktree rather
    than in the directory l5-plan was invoked from.

    Driven with `subprocess.run` replaced by a recorder rather than by
    starting a run, because the subject is which directory is handed over.
    """
    import plan_run_offer

    seen = {}

    class Recorded:
        returncode = 0

    def recording(argv, cwd=None, **kwargs):
        seen["argv"] = list(argv)
        seen["cwd"] = cwd
        return Recorded()

    where = tmp_path / "a-planning-worktree"
    where.mkdir()
    real = plan_run_offer.subprocess.run
    plan_run_offer.subprocess.run = recording
    try:
        plan_run_offer.launch_run(HARNESS_ROOT, "story-001", cwd=where)
    finally:
        plan_run_offer.subprocess.run = real

    assert seen["cwd"] == str(where)
    assert any("story-001" in str(part) for part in seen["argv"])

    # The control: with no directory named, the working directory is left
    # alone — which is what every caller got before this argument existed.
    plan_run_offer.subprocess.run = recording
    try:
        plan_run_offer.launch_run(HARNESS_ROOT, "story-001")
    finally:
        plan_run_offer.subprocess.run = real
    assert seen["cwd"] is None
