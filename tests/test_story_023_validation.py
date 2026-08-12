"""Independent validation for story-023: l5-plan commits and pushes the
artifact it caused to be written.

The subject is a *script that outlives its interactive session*, so almost
nothing here is asserted from source. A throwaway target repository is built
under tmp_path, a stub `claude` is put on PATH, and the real
`scripts/l5-plan` is run against it as a subprocess. What the script does with
what the stub wrote is whatever the script does.

The stub's prompt is never read by the stub, and the stub never commits
anything: that is the point of the first test. If the artifact ends up
committed, the script committed it.

Every assertion here that claims an absence carries a control showing the
same check reporting the violation it exists to catch:

  * "the commit holds only the artifact" sits beside the same reader run over
    a control commit made with `git add -A` in the same repository, which
    reports the unrelated files;
  * "a session that added nothing created no commit" sits beside the same
    fixture whose stub added an artifact, where HEAD does move;
  * "nothing was pushed" sits beside the same remote after a run that did
    push, where its refs do move;
  * "no `git add -A` and no staging outside the new artifacts" sits beside the
    same scanner run over copies of this story's own source with each of those
    violations planted in it;
  * "scripts/l5-plan holds no git logic" sits beside the same scanner over a
    copy of the script with a git call planted;
  * "prompts/planner.md no longer tells the planner to commit" sits beside the
    same scanner run over the pre-story text of that same file, read out of
    git history, which does tell it to.

The baseline for anything read out of git is `conftest.story_commit_range`,
never HEAD and never the working tree against the repository root: the
coordinator commits the tree at the end of a successful run, so those go
vacuously green the moment this story commits.

Not asserted here, deliberately: terminal resize behaviour under the new
process model. A test in this file could not observe whether SIGWINCH reaches
the session and the pane redraws correctly; asserting it would be asserting
something the test cannot see. The story's own verification requirements put
that on the developer to confirm by hand, and this file does not claim it was
verified.

No model is invoked anywhere in this file.
"""
import ast
import io
import json
import os
import pty
import re
import selectors
import signal
import subprocess
import sys
import time
import tokenize
from dataclasses import dataclass
from pathlib import Path

import pytest

from conftest import BASELINE, repository_file_at, story_commit_range

HARNESS_ROOT = Path(__file__).resolve().parents[1]
L5_PLAN = HARNESS_ROOT / "scripts" / "l5-plan"
PLAN_COMMIT = HARNESS_ROOT / "orchestration" / "plan_commit.py"
VALIDATION_FILE = Path(__file__).resolve()

#: The source this story adds, which the staging scan reads. Named by path
#: rather than imported, because the scan is over text.
STORY_SOURCE = {
    "orchestration/plan_commit.py": PLAN_COMMIT,
    "scripts/l5-plan": L5_PLAN,
}

CONFIG = """\
project: plan-target
workflow: story-workflow
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: {stories_dir}
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: echo tests-ok
"""

ARTIFACT = """\
story:
  id: {story_id}
  title: {title}
  description: |
    A stand-in story written by the stub session.

tasks:
  - do the sample work

acceptance_criteria:
  - the sample behavior exists

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

#: A stub `claude`. It records the argument list and whether its standard
#: streams are a terminal, writes whatever files it was told to write
#: *relative to the cwd it inherited*, and exits with the status it was told
#: to exit with. It reads none of the prompt it is handed and it runs no git
#: command, so a commit that exists afterwards was made by l5-plan.
STUB = '''\
#!/usr/bin/env python3
import json
import os
import pathlib
import sys
import time

log = os.environ.get("L5_STUB_LOG")
if log:
    pathlib.Path(log).write_text(
        json.dumps({
            "argv": sys.argv,
            "cwd": os.getcwd(),
            "tty": [os.isatty(0), os.isatty(1), os.isatty(2)],
        }),
        encoding="utf-8",
    )
for relative, body in json.loads(os.environ.get("L5_STUB_WRITE", "[]")):
    path = pathlib.Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
sys.stdout.write("stub session\\n")
sys.stdout.flush()
time.sleep(float(os.environ.get("L5_STUB_SLEEP", "0")))
sys.exit(int(os.environ.get("L5_STUB_EXIT", "0")))
'''


# --------------------------------------------------------------------------
# The throwaway repository, the stub on PATH, and the runner.
# --------------------------------------------------------------------------


@dataclass
class Planning:
    """A target repository with a stub `claude` on PATH."""

    root: Path
    bin_dir: Path
    stories_dir: Path
    log: Path
    remote: Path | None = None

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True, text=True,
        )

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.strip()

    def subject(self, revision: str = "HEAD") -> str:
        return self.git("log", "-1", "--format=%s", revision).stdout.strip()

    def status(self) -> str:
        return self.git("status", "--porcelain", "-uall").stdout

    def tree(self) -> dict:
        return {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file() and ".git/" not in str(p.relative_to(self.root))
        }

    def env(self, **stub) -> dict:
        environment = dict(os.environ)
        environment["PATH"] = f"{self.bin_dir}{os.pathsep}{environment['PATH']}"
        environment["L5_STUB_LOG"] = str(self.log)
        environment.update({k: str(v) for k, v in stub.items()})
        return environment

    def session(self) -> dict:
        return json.loads(self.log.read_text(encoding="utf-8"))


def committed_paths(repo: Path, revision: str = "HEAD") -> list[str]:
    """The paths one commit touched.

    Used both to assert what a commit holds and, in the control beside that
    assertion, to show this reader does report unrelated files when they are
    in the commit.
    """
    shown = subprocess.run(
        ["git", "-C", str(repo), "show", "--name-only", "--format=", revision],
        capture_output=True, text=True, check=True,
    )
    return sorted(line for line in shown.stdout.split("\n") if line.strip())


def make_planning(tmp_path: Path, stories_dir: str = ".harness/stories") -> Planning:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "plan-target"
    (root / ".harness").mkdir(parents=True)
    (root / stories_dir).mkdir(parents=True, exist_ok=True)
    (root / ".harness" / "config.yaml").write_text(
        CONFIG.format(stories_dir=stories_dir), encoding="utf-8")
    (root / "README.md").write_text("target\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "initial"],
    ):
        subprocess.run(command, cwd=root, check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)
    return Planning(root, bin_dir, root / stories_dir, tmp_path / "session.json")


@pytest.fixture
def planning(tmp_path: Path) -> Planning:
    """A repository that tracks a bare `origin`, which is the normal case.

    A push that cannot happen is reported and exits non-zero by design, so the
    tests that are about committing give the repository somewhere to push;
    the two that are about the push failing build their own without one.
    """
    planning = make_planning(tmp_path)
    planning.remote = bare_remote(tmp_path, planning, upstream=True)
    return planning


def artifact(story_id: str = "story-900", title: str = "Stub planned story") -> str:
    return ARTIFACT.format(story_id=story_id, title=title)


def writes(*artifacts: tuple[str, str]) -> str:
    return json.dumps([list(pair) for pair in artifacts])


def run_plan(planning: Planning, request: str = "add a thing",
             **stub) -> subprocess.CompletedProcess:
    """Run the real scripts/l5-plan against the throwaway repository."""
    return subprocess.run(
        [sys.executable, str(L5_PLAN), request],
        cwd=planning.root,
        env=planning.env(**stub),
        capture_output=True,
        text=True,
    )


def bare_remote(tmp_path: Path, planning: Planning, name: str = "origin",
                upstream: bool = False) -> Path:
    remote = tmp_path / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)],
                   cwd=tmp_path, check=True)
    planning.git("remote", "add", name, str(remote))
    if upstream:
        planning.git("push", "-q", "-u", name, "main")
    else:
        planning.git("push", "-q", name, "main")
        planning.git("config", "--unset", "branch.main.remote")
        planning.git("config", "--unset", "branch.main.merge")
    return remote


def remote_refs(remote: Path) -> dict:
    listed = subprocess.run(
        ["git", "-C", str(remote), "for-each-ref", "--format=%(refname) %(objectname)"],
        capture_output=True, text=True, check=True,
    )
    return dict(line.split() for line in listed.stdout.splitlines() if line.strip())


# --------------------------------------------------------------------------
# The artifact is committed, by the script, and holds only itself.
# --------------------------------------------------------------------------


def test_session_that_says_nothing_about_committing_ends_with_the_artifact_committed(
        planning: Planning):
    """The stub reads no prompt and runs no git; the commit is the script's."""
    before = planning.head()
    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", artifact())))

    assert result.returncode == 0, result.stderr
    assert planning.head() != before
    assert committed_paths(planning.root) == [".harness/stories/story-900.yaml"]
    assert planning.status() == ""
    assert "commit" not in STUB  # the stub could not have made it


def test_the_commit_holds_the_artifact_alone_while_the_tree_is_dirty(
        planning: Planning):
    """Unrelated work — modified, untracked and already staged — stays out.

    The control below makes the same commit with `git add -A` in the same
    repository and shows `committed_paths` reporting those very files, so the
    assertion above cannot be green because the reader stopped looking.
    """
    (planning.root / "README.md").write_text("edited by the developer\n",
                                             encoding="utf-8")
    (planning.root / "untracked.txt").write_text("mine\n", encoding="utf-8")
    (planning.root / "staged.txt").write_text("also mine\n", encoding="utf-8")
    planning.git("add", "staged.txt")

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", artifact()),
        ("notes.txt", "the session wrote this outside stories_dir\n"),
    ))

    assert result.returncode == 0, result.stderr
    assert committed_paths(planning.root) == [".harness/stories/story-900.yaml"]

    after = planning.status()
    assert " M README.md" in after
    assert "?? untracked.txt" in after
    assert "A  staged.txt" in after
    assert "?? notes.txt" in after

    # Control: the same reader over a commit that did sweep the tree.
    planning.git("add", "-A")
    planning.git("commit", "-q", "-m", "control: swept the tree")
    swept = committed_paths(planning.root)
    assert "README.md" in swept and "untracked.txt" in swept and "notes.txt" in swept


def test_the_configured_stories_dir_is_what_is_watched(tmp_path: Path):
    """A repository configuring another stories_dir commits from there."""
    planning = make_planning(tmp_path, stories_dir="plans")
    bare_remote(tmp_path, planning, upstream=True)
    result = run_plan(planning, L5_STUB_WRITE=writes(
        ("plans/story-901.yaml", artifact("story-901"))))

    assert result.returncode == 0, result.stderr
    assert committed_paths(planning.root) == ["plans/story-901.yaml"]


# --------------------------------------------------------------------------
# Nothing appeared: no commit, no push, and it says so.
# --------------------------------------------------------------------------


def test_a_session_that_added_nothing_commits_nothing_and_says_so(
        planning: Planning):
    """Asserted by HEAD not moving and the remote not moving, not by wording.

    The control is the second run in the same repository: the same fixture
    with an artifact written does move both.
    """
    remote = planning.remote
    before, refs_before = planning.head(), remote_refs(remote)

    result = run_plan(planning)

    assert result.returncode == 0, result.stderr
    assert planning.head() == before
    assert remote_refs(remote) == refs_before
    assert "committed nothing" in result.stdout

    # Control: the same repository, the same remote, one artifact written.
    control = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", artifact())))
    assert control.returncode == 0, control.stderr
    assert planning.head() != before
    assert remote_refs(remote) != refs_before


def test_a_session_that_only_edits_an_existing_artifact_commits_nothing(
        planning: Planning):
    """Appearance is the test, so an edit in place is not the script's to commit."""
    existing = planning.stories_dir / "story-800.yaml"
    existing.write_text(artifact("story-800"), encoding="utf-8")
    planning.git("add", "-A")
    planning.git("commit", "-q", "-m", "an artifact that already existed")
    before = planning.head()

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-800.yaml",
         artifact("story-800", "Edited by the session"))))

    assert result.returncode == 0, result.stderr
    assert planning.head() == before
    assert "committed nothing" in result.stdout
    assert " M .harness/stories/story-800.yaml" in planning.status()

    # Control: the same session adding a file as well does produce a commit,
    # holding only the added one.
    control = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-801.yaml", artifact("story-801"))))
    assert control.returncode == 0, control.stderr
    assert planning.head() != before
    assert committed_paths(planning.root) == [".harness/stories/story-801.yaml"]


# --------------------------------------------------------------------------
# The decision comes off the disk, not off the exit status.
# --------------------------------------------------------------------------


def test_a_session_that_wrote_an_artifact_and_failed_still_commits_it(
        planning: Planning):
    before = planning.head()
    result = run_plan(
        planning,
        L5_STUB_WRITE=writes((".harness/stories/story-900.yaml", artifact())),
        L5_STUB_EXIT=3,
    )

    assert result.returncode == 3, result.stderr
    assert planning.head() != before
    assert committed_paths(planning.root) == [".harness/stories/story-900.yaml"]


def test_the_sessions_exit_status_is_the_scripts_exit_status(planning: Planning):
    for code in (0, 1, 7):
        result = run_plan(planning, L5_STUB_EXIT=code)
        assert result.returncode == code, result.stdout + result.stderr


# --------------------------------------------------------------------------
# The push, its remote resolution, and its two failure modes.
# --------------------------------------------------------------------------


def test_the_commit_is_pushed_to_the_remote_the_branch_tracks(tmp_path: Path):
    """The branch tracks a remote that is not origin, and that is where it goes."""
    planning = make_planning(tmp_path / "tracking")
    remote = bare_remote(tmp_path / "tracking", planning, name="upstream",
                         upstream=True)
    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", artifact())))

    assert result.returncode == 0, result.stderr
    assert remote_refs(remote)["refs/heads/main"] == planning.head()
    assert "upstream" in result.stdout


def test_a_branch_that_tracks_nothing_is_pushed_to_origin(tmp_path: Path):
    planning = make_planning(tmp_path / "untracked")
    remote = bare_remote(tmp_path / "untracked", planning, name="origin",
                         upstream=False)
    assert planning.git("config", "branch.main.remote").stdout.strip() == ""

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", artifact())))

    assert result.returncode == 0, result.stderr
    assert remote_refs(remote)["refs/heads/main"] == planning.head()
    # Planning did not write tracking configuration into the developer's
    # repository as a side effect. Control: the same read after `push -u`
    # does report the remote, so the emptiness above is not the reader
    # looking at a key that never has a value.
    assert planning.git("config", "branch.main.remote").stdout.strip() == ""
    planning.git("push", "-q", "-u", "origin", "main")
    assert planning.git("config", "branch.main.remote").stdout.strip() == "origin"


def test_no_remote_configured_leaves_the_commit_and_reports_it(tmp_path: Path):
    planning = make_planning(tmp_path / "remoteless")
    assert planning.git("remote").stdout.strip() == ""
    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", artifact())))

    assert result.returncode != 0
    assert committed_paths(planning.root) == [".harness/stories/story-900.yaml"]
    assert planning.git("cat-file", "-e", "HEAD").returncode == 0
    assert "main" in result.stdout
    assert "remote" in result.stdout
    assert planning.status() == ""


def test_a_rejected_push_leaves_the_commit_intact_and_unamended(
        tmp_path: Path, planning: Planning):
    remote = planning.remote
    diverged = tmp_path / "diverged"
    subprocess.run(["git", "clone", "-q", str(remote), str(diverged)],
                   cwd=tmp_path, check=True)
    for command in (
        ["git", "config", "user.email", "other@example.com"],
        ["git", "config", "user.name", "Other"],
        ["git", "commit", "-q", "--allow-empty", "-m", "upstream moved on"],
        ["git", "push", "-q", "origin", "main"],
    ):
        subprocess.run(command, cwd=diverged, check=True)
    refs_before = remote_refs(remote)

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", artifact())))

    assert result.returncode != 0
    assert committed_paths(planning.root) == [".harness/stories/story-900.yaml"]
    assert planning.subject().startswith("Plan story-900")
    assert "origin" in result.stdout and "main" in result.stdout
    # Nothing was rolled back, amended or reset: the artifact is still in the
    # tree, the tree is otherwise clean, and the remote is where it was.
    assert (planning.stories_dir / "story-900.yaml").is_file()
    assert planning.status() == ""
    assert remote_refs(remote) == refs_before


# --------------------------------------------------------------------------
# The commit subject.
# --------------------------------------------------------------------------


def test_the_subject_names_the_story_and_carries_its_title(planning: Planning):
    run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", artifact(title="Stub planned story"))))
    assert planning.subject() == "Plan story-900: Stub planned story"


def test_the_fallback_subject_is_used_when_an_artifact_does_not_parse(tmp_path: Path):
    """The subject falls back when the parse fails; the commit is elsewhere.

    Repointed by story-025, which validates before committing, so an
    unparseable artifact no longer reaches a commit at all (that end of it is
    asserted in this file by
    test_an_unparseable_artifact_is_not_committed_and_stays_in_the_tree).
    What this test always established about plan_commit — that a failed parse
    costs the title and nothing more — is unchanged and is asserted on
    commit_subject directly, where it lives.
    """
    sys.path.insert(0, str(HARNESS_ROOT / "orchestration"))
    import plan_commit

    unparseable = tmp_path / "story-902.yaml"
    unparseable.write_text("this: is: not: a story\n\t- ?\n", encoding="utf-8")
    assert plan_commit.commit_subject([unparseable]) == "Plan story-902"


def test_an_unparseable_artifact_is_not_committed_and_stays_in_the_tree(
        planning: Planning):
    """Since story-025 l5-plan validates between the snapshot and the commit."""
    before = planning.head()
    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-902.yaml", "this: is: not: a story\n\t- ?\n")))

    assert result.returncode != 0
    assert planning.head() == before
    assert (planning.stories_dir / "story-902.yaml").is_file()


def test_more_than_one_new_artifact_is_one_commit_naming_each(planning: Planning):
    before = planning.head()
    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-903.yaml", artifact("story-903")),
        (".harness/stories/story-904.yaml", artifact("story-904")),
    ))

    assert result.returncode == 0, result.stderr
    assert committed_paths(planning.root) == [
        ".harness/stories/story-903.yaml",
        ".harness/stories/story-904.yaml",
    ]
    assert planning.git("rev-list", "--count", f"{before}..HEAD").stdout.strip() == "1"
    assert "story-903" in planning.subject()
    assert "story-904" in planning.subject()


# --------------------------------------------------------------------------
# The interactive session under a terminal.
# --------------------------------------------------------------------------


def run_plan_on_a_pty(planning: Planning, **stub):
    """Run l5-plan with a pty for stdin, stdout and stderr."""
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, str(L5_PLAN), "add a thing"],
        cwd=planning.root, env=planning.env(**stub),
        stdin=slave, stdout=slave, stderr=slave,
        start_new_session=True,
    )
    os.close(slave)
    return process, master


def drain(process, master: int) -> tuple[int, str]:
    output, selector = b"", selectors.DefaultSelector()
    selector.register(master, selectors.EVENT_READ)
    while True:
        if not selector.select(timeout=30):
            break
        try:
            chunk = os.read(master, 4096)
        except OSError:
            break
        if not chunk:
            break
        output += chunk
    os.close(master)
    return process.wait(timeout=30), output.decode(errors="replace")


def test_the_developers_terminal_is_the_sessions_terminal(planning: Planning):
    process, master = run_plan_on_a_pty(planning, L5_STUB_EXIT=7)
    status, _ = drain(process, master)

    assert status == 7
    assert planning.session()["tty"] == [True, True, True]


def test_an_interrupt_still_commits_what_was_written_and_exits_130(
        planning: Planning):
    process, master = run_plan_on_a_pty(
        planning,
        L5_STUB_WRITE=writes((".harness/stories/story-900.yaml", artifact())),
        L5_STUB_SLEEP=30,
    )
    written = planning.stories_dir / "story-900.yaml"
    deadline = time.monotonic() + 30
    while not written.is_file() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert written.is_file(), "the stub never got as far as writing the artifact"
    os.killpg(os.getpgid(process.pid), signal.SIGINT)

    status, _ = drain(process, master)

    assert status == 130
    assert committed_paths(planning.root) == [".harness/stories/story-900.yaml"]


# --------------------------------------------------------------------------
# Everything before the session is unchanged.
# --------------------------------------------------------------------------


def pre_story_text(rel: str) -> str:
    """One repository file as it stood before this story's own run.

    story-029 folded this module's three private `git show` calls into
    `conftest.repository_file_at`, which resolves the same shared baseline
    they each resolved for themselves. Subject and strictness unchanged.
    """
    return repository_file_at(rel, validation_file=VALIDATION_FILE,
                              bound=BASELINE, repo=HARNESS_ROOT)


def pre_story_script(tmp_path: Path) -> Path:
    """scripts/l5-plan as it was before this story, runnable.

    Resolved through the shared baseline rather than HEAD, and placed in a
    harness root whose other directories are symlinks to the real ones, so
    the old script loads the same config, workflow, rules and template the
    new one does without anything being written into the repository.
    """
    source = pre_story_text("scripts/l5-plan")
    root = tmp_path / "pre-story-harness"
    (root / "scripts").mkdir(parents=True)
    for name in ("orchestration", "prompts", "schemas", "workflows", "rules"):
        os.symlink(HARNESS_ROOT / name, root / name)
    script = root / "scripts" / "l5-plan"
    script.write_text(source, encoding="utf-8")
    script.chmod(0o755)
    return script


def test_the_argument_list_handed_to_claude_is_what_the_exec_passed(
        tmp_path: Path, planning: Planning):
    """The rendered prompt and every argument, compared against the old script.

    The old script is the one this story replaced, read out of git at the
    story's own baseline and run against the same repository with the same
    stub, so this compares behaviour rather than a copy of it.
    """
    request = "a request with spaces and 'quotes'"
    subprocess.run(
        [sys.executable, str(pre_story_script(tmp_path)), request],
        cwd=planning.root, env=planning.env(), capture_output=True, text=True,
        check=True,
    )
    before = planning.session()

    subprocess.run(
        [sys.executable, str(L5_PLAN), request],
        cwd=planning.root, env=planning.env(), capture_output=True, text=True,
        check=True,
    )
    after = planning.session()

    assert after["argv"] == before["argv"]
    assert after["cwd"] == before["cwd"]
    assert "--append-system-prompt" in after["argv"]
    assert f"Story request: {request}" in after["argv"]


def test_the_old_script_did_not_commit_which_is_what_this_story_changed(
        tmp_path: Path, planning: Planning):
    """The control for the first test in this file.

    It shows that running the *old* script over a session that wrote an
    artifact leaves it uncommitted — so "the artifact ends up committed" is a
    property of this story's change and not of the stub or the fixture.
    """
    script = pre_story_script(tmp_path)
    before = planning.head()
    subprocess.run(
        [sys.executable, str(script), "add a thing"],
        cwd=planning.root,
        env=planning.env(L5_STUB_WRITE=writes(
            (".harness/stories/story-900.yaml", artifact()))),
        capture_output=True, text=True, check=True,
    )

    assert planning.head() == before
    assert "?? .harness/stories/story-900.yaml" in planning.status()


# --------------------------------------------------------------------------
# What the source may and may not contain.
# --------------------------------------------------------------------------


def python_code(source: str) -> str:
    """The source with its comments and docstrings removed.

    Every scan below is a claim about what the *code* does, and a comment
    describing what the code no longer does — "no longer goes through
    os.execvp" — is not the code doing it. Stripping the prose is what makes
    the scan see the subject rather than the description of it; the controls
    beside each scan plant their violation in code, so the stripping cannot
    hide a real one.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            node.body = [
                statement for statement in body
                if not (isinstance(statement, ast.Expr)
                        and isinstance(statement.value, ast.Constant)
                        and isinstance(statement.value.value, str))
            ] or [ast.Pass()]
    return ast.unparse(tree)


def python_prose(source: str) -> str:
    """The comments and docstrings of a python source, and nothing else.

    An instruction addressed to an agent can only live in a script's prose,
    so this is where the prompt sweep looks when the file is code.
    """
    comments = [token.string for token in
                tokenize.generate_tokens(io.StringIO(source).readline)
                if token.type == tokenize.COMMENT]
    docstrings = [
        statement.value.value
        for node in ast.walk(ast.parse(source))
        for statement in (getattr(node, "body", None)
                          if isinstance(getattr(node, "body", None), list) else [])
        if isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    ]
    return "\n".join(comments + docstrings)


#: Ways of staging more than the paths handed in. `git add -A`, `--all` and
#: `add .` in either the argument-list or the shell-string spelling.
STAGING_VIOLATIONS = (
    re.compile(r"""add["'\s,]+["']?-A\b"""),
    re.compile(r"""add["'\s,]+["']?--all\b"""),
    re.compile(r"""add["'\s,]+["']?\.["'\s,)]"""),
    re.compile(r"""["']add["']\s*,\s*["']:/"""),
)


def staging_violations(source: str) -> list[str]:
    """Every way this source stages something other than the paths given it."""
    return [match.group(0) for pattern in STAGING_VIOLATIONS
            for match in pattern.finditer(source)]


@pytest.mark.parametrize("relative", sorted(STORY_SOURCE))
def test_the_story_stages_nothing_but_the_artifacts(relative: str):
    source = python_code(STORY_SOURCE[relative].read_text(encoding="utf-8"))
    assert staging_violations(source) == []


@pytest.mark.parametrize("planted", [
    '_git(target_root, "add", "-A")',
    '_git(target_root, "add", "--all")',
    '_git(target_root, "add", ".")',
    'subprocess.run("git add -A", shell=True)',
    '_git(target_root, "add", ":/")',
])
def test_the_staging_scan_reports_the_violations_it_exists_to_catch(planted: str):
    """The control for the scan above.

    Each spelling is planted into a copy of this story's own source and the
    same scanner is run over it, so a green scan means the source is clean
    rather than the scanner blind.
    """
    source = PLAN_COMMIT.read_text(encoding="utf-8")
    assert staging_violations(python_code(f"{source}\n{planted}\n")) != []


def test_the_committed_pathspec_comes_from_the_artifacts_and_nothing_else():
    """`git add` and `git commit` are handed the artifact paths, not a wildcard."""
    source = PLAN_COMMIT.read_text(encoding="utf-8")
    assert '"add", "--", *relative' in source
    assert '"commit", "-m", subject, "--", *relative' in source
    assert 'relative = tuple(str(a.relative_to(target_root)) for a in artifacts)' \
        in source


def git_calls(source: str) -> list[str]:
    """Every line of this source that runs git."""
    return [line.strip() for line in source.splitlines()
            if re.search(r"""["'\[]git\b""", line)]


def test_scripts_l5_plan_is_wiring_and_holds_no_git_logic():
    """The snapshot, commit, push and remote resolution live in orchestration.

    The control below plants a git call into a copy of the script and shows
    the same reader reporting it.
    """
    source = python_code(L5_PLAN.read_text(encoding="utf-8"))
    assert git_calls(source) == []
    for name in ("snapshot", "new_artifacts", "commit_artifacts", "push_commit"):
        assert f"plan_commit.{name}" in source
    planted = python_code(
        f'{L5_PLAN.read_text(encoding="utf-8")}\nsubprocess.run(["git", "add", "x"])\n')
    assert git_calls(planted) != []


def test_scripts_l5_plan_no_longer_execs():
    source = python_code(L5_PLAN.read_text(encoding="utf-8"))
    assert "execvp" not in source
    assert "subprocess.run(" in source
    # Control: the pre-story script, read at the same baseline, does exec —
    # so "execvp is absent" is a change rather than a word that was never here.
    old = pre_story_text("scripts/l5-plan")
    assert "execvp" in python_code(old)


# --------------------------------------------------------------------------
# One act, one mechanism.
# --------------------------------------------------------------------------


#: An instruction addressed to the agent reading the prompt, telling it to
#: commit. "l5-plan commits the artifact" and "Do not commit anything" are
#: statements about the harness, not instructions to commit, and the control
#: below shows the difference is one this scanner can actually see.
COMMIT_INSTRUCTIONS = (
    re.compile(r"(?im)^\s*(?:\d+\.\s*)?commit\b"),
    re.compile(r"(?i)\byou (?:must |should |then )?commit\b"),
    re.compile(r"(?i)\bcommit (?:that|the|this) (?:file|artifact|story)\b"),
)


def commit_instructions(text: str) -> list[str]:
    return [match.group(0) for pattern in COMMIT_INSTRUCTIONS
            for match in pattern.finditer(text)]


def test_the_planner_prompt_no_longer_asks_the_planner_to_commit():
    prompt = (HARNESS_ROOT / "prompts" / "planner.md").read_text(encoding="utf-8")
    assert commit_instructions(prompt) == []
    assert "l5-plan commits" in prompt

    # Control: the same scanner over the pre-story text of this same file,
    # read at the story's baseline. It told the planner to commit, and the
    # scanner says so.
    old = pre_story_text("prompts/planner.md")
    assert commit_instructions(old) != []


def instructive_text(path: Path) -> str:
    """The part of a file that could carry an instruction to an agent.

    All of a prompt; only the prose of a script, because a script's code is
    the harness acting rather than an agent being told to.
    """
    source = path.read_text(encoding="utf-8")
    if path.suffix == ".md":
        return source
    try:
        return python_prose(source)
    except SyntaxError:
        return source


def test_no_other_prompt_or_script_asks_an_agent_to_commit_the_artifact():
    """The second mechanism must not survive somewhere else either."""
    candidates = sorted((HARNESS_ROOT / "prompts").glob("*.md")) + \
        sorted(p for p in (HARNESS_ROOT / "scripts").iterdir() if p.is_file())
    assert len(candidates) > 5, "the sweep found almost nothing to sweep"
    offenders = {
        str(path.relative_to(HARNESS_ROOT)): found
        for path in candidates
        if (found := commit_instructions(instructive_text(path)))
    }
    assert offenders == {}
    # Control: the same sweep over the same files with the pre-story planner
    # instruction planted into one of them.
    planted = "5. Commit that file, and only that file, on the branch you are on."
    assert commit_instructions(planted) != []
    assert commit_instructions(instructive_text(HARNESS_ROOT / "prompts" /
                                                "implementer.md") + planted) != []


# --------------------------------------------------------------------------
# plan_commit's own decisions, without a session.
# --------------------------------------------------------------------------


def test_snapshot_of_a_stories_dir_that_does_not_exist_is_empty(tmp_path: Path):
    import plan_commit

    assert plan_commit.snapshot(tmp_path / "nothing-here") == frozenset()
    (tmp_path / "stories").mkdir()
    before = plan_commit.snapshot(tmp_path / "stories")
    (tmp_path / "stories" / "story-001.yaml").write_text("x", encoding="utf-8")
    assert plan_commit.new_artifacts(tmp_path / "stories", before) == \
        (tmp_path / "stories" / "story-001.yaml",)


def test_new_artifacts_ignores_a_file_that_was_only_edited(tmp_path: Path):
    import plan_commit

    stories = tmp_path / "stories"
    stories.mkdir()
    existing = stories / "story-001.yaml"
    existing.write_text("before", encoding="utf-8")
    before = plan_commit.snapshot(stories)
    existing.write_text("after, and longer than before", encoding="utf-8")

    assert plan_commit.new_artifacts(stories, before) == ()


def test_the_subject_falls_back_to_the_id_when_the_artifact_cannot_be_read(
        tmp_path: Path):
    import plan_commit

    broken = tmp_path / "story-905.yaml"
    broken.write_text("not: a: story\n\t?\n", encoding="utf-8")
    assert plan_commit.story_title(broken, HARNESS_ROOT) == ""
    assert plan_commit.commit_subject([broken], HARNESS_ROOT) == "Plan story-905"
