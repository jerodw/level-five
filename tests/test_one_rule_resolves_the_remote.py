"""Independent validation for story-200: one rule decides which remote this
repository uses, wherever the question is asked.

Until this story several places each worked out which remote a repository uses
in their own way, and they did not agree: the plan-time code looked for a
remote named `origin` and gave up when it found none, while the run-time code
fell back to whatever remote the repository did have. In a clone whose only
remote is named something else that disagreement decided something real —
planning concluded there was nothing to reserve a story id on and claimed the
id locally, so two clones could plan the same id, while the same repository's
run path fetched story branches from that remote quite happily.

So this module is a standing one rather than a record of a repair. It holds
four things:

  * **the resolver over throwaway repositories.** Each leg of
    `git_remote.resolve` driven against a repository built for the case: no
    remote, one remote that is not `origin`, `origin` beside it, a branch
    configured to track the non-`origin` one, a detached HEAD, and a `branch`
    argument naming a branch other than the one checked out.
  * **the call sites answering alike.** In one repository, the shared
    resolver, the remote `_base_remote` answers for the base and the remote
    `fetch_story_branch` fetches from are the same remote — asserted for an
    `upstream`-only clone and for a clone holding both.
  * **the reservation end to end.** A clone whose single remote is named
    `upstream` and whose branch tracks nothing reserves its story id on
    `upstream`, asserted by the ref that is on the bare remote afterwards
    rather than by anything l5-plan printed; and a clone with no remote at all
    still plans on a local claim and still says so.
  * **the decision composed in one place.** A scan over the source under
    `orchestration/` and `scripts/` for a site that lists a repository's
    remotes in order to choose between them, which must report exactly one
    file.

Every absence asserted here is shown able to fail. The one-site scan is run
again over a copy of that same source with a second site planted in it, and
reports both. The reader that says `plan_commit` defines neither
`resolve_remote` nor `current_branch` is run again over a copy that defines one
and reports it. The reader that says `scripts/l5-plan` names the deleted
plan-time function nowhere is run again over a copy that names it. And the
clone that reaches `fetch_story_branch`'s no-remote sentence without spawning a
fetch sits beside a clone that does spawn one, so the empty answer is reached
for the reason it exists for rather than because nothing was looking.

Nothing here reaches the network and nothing invokes a model: every remote is a
bare repository under a temporary directory, and the one end-to-end invocation
runs the real `scripts/l5-plan` against a session stub that writes files and
exits.

The repositories, the bare remotes and the planning invocation are the fixtures
`tests/test_plan_commit.py` already builds, reused rather than composed a
second time here.
"""
import ast
import shutil
import sys
from pathlib import Path

import pytest

import conftest
from test_plan_commit import (
    Planning,
    artifact,
    bare_remote,
    make_planning,
    remote_refs,
    run_plan,
    writes,
)

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "orchestration"))

import git_remote  # noqa: E402
import harness_config  # noqa: E402
import plan_commit  # noqa: E402
import story_coordinator  # noqa: E402

#: The branch `make_planning` leaves its repository standing on.
DEFAULT_BRANCH = "main"

#: The two modules whose source is read below, and the entry point beside them.
#: Read as text because what is being asserted is what the source says — which
#: function it defines, which resolver it calls — rather than what a run of it
#: does.
PLAN_COMMIT_REL = "orchestration/plan_commit.py"
COORDINATOR_REL = "orchestration/story_coordinator.py"
L5_PLAN_REL = "scripts/l5-plan"

#: The two directories the one-site scan covers: the orchestration modules and
#: the entry points beside them, which between them are every place in this
#: harness that has ever asked which remote answers for a repository.
SCANNED_DIRS = ("orchestration", "scripts")

#: The function the plan-time code used to hold, deleted by this story, and the
#: call site in the entry point that used to reach it. Named here so the two
#: absence assertions below and the planted copies that control them are stated
#: in one place.
DELETED_RESOLVER = "resolve_remote"
DELETED_CALL = f"plan_commit.{DELETED_RESOLVER}"

#: The clause l5-plan prints when it could not reserve an id on a remote and
#: took a local claim instead. It is the line the `upstream`-only invocation
#: must not print and the line the remoteless invocation must, so the two cases
#: are each other's control over one phrase.
LOCAL_CLAIM = "no reservation was made"

#: A story branch that exists in none of the repositories built here, so
#: `fetch_story_branch` always reaches past its already-resolved answer.
UNFETCHED_BRANCH = "story/story-900"


# --------------------------------------------------------------------------
# The repositories, built out of the planning fixtures
# --------------------------------------------------------------------------


def a_clone(tmp_path: Path, name: str, *, remotes: tuple[str, ...] = (),
            tracking: str | None = None) -> tuple[Planning, dict]:
    """A repository with the named remotes, and the bare repository each is.

    `make_planning` and `bare_remote` are `tests/test_plan_commit.py`'s, so the
    repository here is the one that module's own cases are decided against: a
    target with a configuration, a commit, and a bare repository of its own for
    each remote it names.

    Every remote is added without tracking configuration, and `tracking` — when
    a case wants one — is written afterwards as the two keys git writes,
    `branch.<name>.remote` and `branch.<name>.merge`. Writing it last matters:
    `bare_remote` unsets those keys for each untracked remote it adds, so a
    tracking remote added first would be untracked again by the next one.
    """
    where = tmp_path / name
    planning = make_planning(where)
    bare = {}
    for remote_name in remotes:
        bare[remote_name] = bare_remote(where, planning, name=remote_name,
                                        upstream=False)
    if tracking is not None:
        planning.git("config", f"branch.{DEFAULT_BRANCH}.remote", tracking)
        planning.git("config", f"branch.{DEFAULT_BRANCH}.merge",
                     f"refs/heads/{DEFAULT_BRANCH}")
    planning.remote = bare.get(tracking) or next(
        (bare[name] for name in remotes), None)
    return planning, bare


def a_branch_tracking(planning: Planning, branch: str, remote: str) -> str:
    """A second branch in `planning`, configured to track `remote`.

    Created without being checked out, so the repository stays on the branch it
    was built on and an assertion about an explicit `branch` argument is about
    that argument rather than about what HEAD happens to be.
    """
    planning.git("branch", branch)
    planning.git("config", f"branch.{branch}.remote", remote)
    planning.git("config", f"branch.{branch}.merge", f"refs/heads/{branch}")
    return branch


# --------------------------------------------------------------------------
# The resolver, leg by leg
# --------------------------------------------------------------------------


#: One case per leg the resolver is documented to have: the remotes the
#: repository configures, the remote its branch is configured to track, and the
#: remote the resolver must answer with.
LEGS = [
    pytest.param((), None, "", id="no remote configured resolves to nothing"),
    pytest.param(("upstream",), None, "upstream",
                 id="a single remote answers whatever it is named"),
    pytest.param(("upstream", "origin"), None, "origin",
                 id="origin beside upstream is preferred"),
    pytest.param(("origin", "upstream"), "upstream", "upstream",
                 id="a branch that tracks upstream resolves to upstream"),
]


@pytest.mark.parametrize("remotes,tracking,expected", LEGS)
def test_the_resolver_answers_each_leg_it_is_documented_to_have(
        tmp_path: Path, remotes: tuple[str, ...], tracking: str | None,
        expected: str):
    """The whole of the rule, one repository per leg.

    The third and fourth cases are each other's control: the same two remotes,
    differing only in whether the branch states which one it tracks, and the
    answer follows the configuration rather than the listing.
    """
    planning, _ = a_clone(tmp_path, "legs", remotes=remotes, tracking=tracking)

    assert git_remote.resolve(planning.root) == expected


def test_a_branch_argument_asks_about_that_branch_rather_than_the_checkout(
        tmp_path: Path):
    """An explicit `branch` is the branch asked about, and the checkout is not.

    The repository holds both remotes and stands on a branch that tracks
    neither, so the defaulted call answers `origin` off the listing. The second
    branch tracks `upstream` and is not checked out, so the two answers differ
    by exactly the argument.
    """
    planning, _ = a_clone(tmp_path, "explicit", remotes=("origin", "upstream"))
    branch = a_branch_tracking(planning, "feature", "upstream")

    assert git_remote.resolve(planning.root) == "origin"
    assert git_remote.resolve(planning.root, branch) == "upstream"


def test_a_detached_head_is_a_branch_that_tracks_nothing(tmp_path: Path):
    """Detached, the repository's own remote answers rather than a failure.

    Two repositories, because a detached HEAD in a clone with one remote would
    answer `upstream` whether the tracking configuration was consulted or not.
    The second holds `origin` beside `upstream` and has its branch configured to
    track `upstream`: checked out it resolves to `upstream`, and detached it
    resolves to `origin` off the listing — so the fall-through is shown
    happening rather than assumed.
    """
    single, _ = a_clone(tmp_path, "detached-single", remotes=("upstream",))
    single.git("checkout", "--detach", "--quiet", DEFAULT_BRANCH)
    assert git_remote.current_branch(single.root) == "HEAD"
    assert git_remote.resolve(single.root) == "upstream"

    both, _ = a_clone(tmp_path, "detached-both", remotes=("origin", "upstream"),
                      tracking="upstream")
    assert git_remote.resolve(both.root) == "upstream"
    both.git("checkout", "--detach", "--quiet", DEFAULT_BRANCH)
    assert git_remote.current_branch(both.root) == "HEAD"
    assert git_remote.resolve(both.root) == "origin"


def test_a_path_that_is_no_repository_is_answered_rather_than_raised(
        tmp_path: Path):
    """Nothing here raises: a git that could not answer answers with nothing.

    The control is the repository beside it, where the same two calls answer
    with a branch and a remote — so the empty answers above are the resolver's
    one-directional bias rather than a call that never reached git.
    """
    nowhere = tmp_path / "not-a-repository"
    nowhere.mkdir()

    assert git_remote.current_branch(nowhere) == ""
    assert git_remote.resolve(nowhere) == ""

    planning, _ = a_clone(tmp_path, "somewhere", remotes=("upstream",))
    assert git_remote.current_branch(planning.root) == DEFAULT_BRANCH
    assert git_remote.resolve(planning.root) == "upstream"


# --------------------------------------------------------------------------
# The call sites, answering alike
# --------------------------------------------------------------------------


def fetch_through(planning: Planning, monkeypatch,
                  branch: str = UNFETCHED_BRANCH) -> tuple:
    """What `fetch_story_branch` answered, and every fetch it spawned.

    The spawn is replaced rather than allowed: what this module asks is which
    remote the function reached for, and a fetch of a branch no bare repository
    holds would answer that question by failing for a second reason. The
    replacement reports a fetch that exited non-zero, which is a remote that
    answered with an error — the one outcome that leaves the function's own
    sentence naming the remote it asked.
    """
    spawned = []

    def recording(argv: list[str]):
        spawned.append(argv)
        return story_coordinator._FetchOutcome(
            returncode=1, stderr="the fixture's remote declined")

    monkeypatch.setattr(story_coordinator, "_bounded_fetch", recording)
    answer = story_coordinator.fetch_story_branch(
        planning.root, harness_config.load_config(planning.root), branch)
    return answer, spawned


def remote_fetched_from(spawned: list) -> str:
    """The remote a recorded fetch was pointed at."""
    assert len(spawned) == 1, spawned
    argv = spawned[0]
    return argv[argv.index("fetch") + 1]


@pytest.mark.parametrize("remotes,tracking,expected", [
    pytest.param(("upstream",), None, "upstream", id="upstream only"),
    pytest.param(("origin", "upstream"), None, "origin",
                 id="origin beside upstream"),
])
def test_every_asker_in_one_repository_reaches_the_same_remote(
        tmp_path: Path, monkeypatch, remotes: tuple[str, ...],
        tracking: str | None, expected: str):
    """The shared resolver, the base's remote and the fetch's, in one clone.

    This is the disagreement the story exists to close, asked as one question:
    the three answers are compared with each other rather than each against a
    literal, and the literal is there as well so a repository where all three
    had become empty could not pass.
    """
    planning, _ = a_clone(tmp_path, "askers", remotes=remotes,
                          tracking=tracking)

    resolved = git_remote.resolve(planning.root)
    base = story_coordinator._base_remote(planning.root, DEFAULT_BRANCH)
    _, spawned = fetch_through(planning, monkeypatch)

    assert resolved == base == remote_fetched_from(spawned) == expected


def test_a_clone_with_no_remote_says_so_without_spawning_a_fetch(
        tmp_path: Path, monkeypatch):
    """The sentence a clone that could not ask still carries, and no fetch.

    The control is the clone beside it: one remote, the same call, the same
    branch — and there a fetch *is* spawned and the problem names the remote.
    So the empty answer above is the no-remote branch being reached rather than
    a replacement that recorded nothing.
    """
    planning, _ = a_clone(tmp_path, "remoteless")

    answer, spawned = fetch_through(planning, monkeypatch)

    assert spawned == []
    assert answer.resolved is False
    assert answer.problem == (
        f"there is no remote configured in {planning.root} to ask for "
        f"branch {UNFETCHED_BRANCH}"
    )

    with_one, _ = a_clone(tmp_path, "with-one", remotes=("upstream",))
    answered, also_spawned = fetch_through(with_one, monkeypatch)
    assert remote_fetched_from(also_spawned) == "upstream"
    assert answered.resolved is False
    assert "upstream" in answered.problem


# --------------------------------------------------------------------------
# The push, resolved through the shared module when a caller omits one
# --------------------------------------------------------------------------


def test_an_omitted_remote_is_resolved_and_pushed_to(tmp_path: Path):
    """A clone whose one remote is not `origin` is pushed to that remote.

    Asserted by where the commit is afterwards — the ref on the bare
    repository — rather than by what the result said, and the reported branch
    is the one the tree stands on.
    """
    planning, bare = a_clone(tmp_path, "omitted", remotes=("upstream",))
    (planning.root / "README.md").write_text("a commit to push\n",
                                             encoding="utf-8")
    planning.git("commit", "-q", "-am", "something to push")

    result = plan_commit.push_commit(planning.root)

    assert result.pushed is True
    assert result.remote == "upstream"
    assert result.branch == DEFAULT_BRANCH
    assert remote_refs(bare["upstream"])[f"refs/heads/{DEFAULT_BRANCH}"] \
        == planning.head()


def test_a_clone_with_no_remote_reports_it_and_leaves_the_commit(
        tmp_path: Path):
    """The remoteless path is exactly what it was: a report, and no move.

    The commit is still HEAD and the tree is still clean afterwards, so
    "reports rather than fails" is asserted by what the repository holds as
    well as by what the result says.
    """
    planning, _ = a_clone(tmp_path, "nowhere-to-push")
    (planning.root / "README.md").write_text("a commit with nowhere to go\n",
                                             encoding="utf-8")
    planning.git("commit", "-q", "-am", "nothing will push this")
    before = planning.head()

    result = plan_commit.push_commit(planning.root)

    assert result.pushed is False
    assert result.remote == ""
    assert result.branch == DEFAULT_BRANCH
    assert result.detail == "no remote configured"
    assert planning.head() == before
    assert planning.status() == ""


# --------------------------------------------------------------------------
# The reservation, end to end
# --------------------------------------------------------------------------


def story_refs(remote: Path) -> list[str]:
    """Every story branch a bare repository holds."""
    return sorted(ref for ref in remote_refs(remote)
                  if ref.startswith("refs/heads/story/"))


def test_an_upstream_only_clone_reserves_its_id_on_upstream(tmp_path: Path):
    """The collision this story exists to remove, asked end to end.

    The repository's single remote is named `upstream` and its branch tracks
    nothing, which is exactly the clone the old plan-time rule concluded had no
    remote at all. The assertion is the ref on the bare repository afterwards —
    an id claimed where another clone can see it — and not the wording the
    invocation printed; the printed line is asserted only for what it must not
    say, and the remoteless case below is that line's control.
    """
    planning, bare = a_clone(tmp_path, "upstream-only", remotes=("upstream",))
    assert planning.git("config", f"branch.{DEFAULT_BRANCH}.remote") \
        .stdout.strip() == ""
    assert story_refs(bare["upstream"]) == []

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", artifact())))

    assert result.returncode == 0, result.stderr
    assert story_refs(bare["upstream"])
    assert LOCAL_CLAIM not in result.stdout, result.stdout


def test_a_clone_with_no_remote_still_plans_on_a_local_claim_and_says_so(
        tmp_path: Path):
    """The repository that behaves today exactly as it behaved before.

    Nothing was reserved, the invocation says so in as many words, and the
    artifact was still planned: the plan commit is on the story branch in the
    worktree the failed push kept, which is where a repository with nowhere to
    push leaves it.
    """
    planning, _ = a_clone(tmp_path, "no-remote-at-all")
    assert planning.git("remote").stdout.strip() == ""

    result = run_plan(planning, L5_STUB_WRITE=writes(
        (".harness/stories/story-900.yaml", artifact())))

    assert LOCAL_CLAIM in result.stdout, result.stdout
    assert "<no remote>" in result.stdout, result.stdout


# --------------------------------------------------------------------------
# The decision composed in one place
# --------------------------------------------------------------------------


def _literal_arguments(node: ast.Call) -> list[str]:
    """Every string literal this call passes, in order, list literals included.

    Both shapes the source writes are the same call: `_git(root, "remote")`,
    which hands the arguments to a module helper, and
    `subprocess.run(["git", "-C", str(root), "remote"])`, which spells the
    argument list out. A computed argument — a path, an f-string's field —
    contributes nothing, which is what lets the position of `remote` in the
    remaining literals say whether anything followed it.
    """
    literals = []
    for argument in node.args:
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            literals.append(argument.value)
        elif isinstance(argument, ast.List):
            for element in argument.elts:
                if isinstance(element, ast.Constant) \
                        and isinstance(element.value, str):
                    literals.append(element.value)
    return literals


def lists_remotes(source: str) -> list[int]:
    """The lines where this source asks git for the repository's remotes.

    `git remote` with nothing after it is a *listing*, which is the only form
    that can be used to choose between remotes. `git remote add` and `git
    remote set-head` name a remote the caller already had and are not that, so
    a subcommand following the word is what tells them apart.
    """
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        literals = _literal_arguments(node)
        if "remote" not in literals:
            continue
        if literals[literals.index("remote") + 1:]:
            continue
        lines.append(node.lineno)
    return sorted(lines)


def files_listing_remotes(root: Path) -> list[str]:
    """Every file under the scanned directories that lists a repository's remotes.

    The directories are walked rather than named file by file, so a module
    added later is covered the moment it lands.
    """
    found = []
    for directory in SCANNED_DIRS:
        for path in sorted((root / directory).iterdir()):
            if not path.is_file() or path.suffix in (".pyc",):
                continue
            if lists_remotes(path.read_text(encoding="utf-8")):
                found.append(f"{directory}/{path.name}")
    return sorted(found)


def test_exactly_one_file_decides_which_remote_answers(tmp_path: Path):
    """One site, and the demonstration that a second one would be reported.

    The control is the same scan over a copy of the same two directories with a
    second listing planted in `plan_commit` — the very place the deleted
    plan-time resolver stood — and it reports both files. So the single answer
    above is the source having one site rather than the scan having stopped
    seeing them.
    """
    assert files_listing_remotes(HARNESS_ROOT) == ["orchestration/git_remote.py"]

    planted = tmp_path / "planted"
    planted.mkdir()
    for directory in SCANNED_DIRS:
        shutil.copytree(HARNESS_ROOT / directory, planted / directory,
                        ignore=shutil.ignore_patterns("__pycache__"))
    copy = planted / PLAN_COMMIT_REL
    copy.write_text(
        copy.read_text(encoding="utf-8")
        + '\n\ndef a_second_site(target_root: Path) -> str:\n'
        '    names = _git(target_root, "remote").stdout.split()\n'
        '    return "origin" if "origin" in names else names[0]\n',
        encoding="utf-8")

    assert files_listing_remotes(planted) == [
        "orchestration/git_remote.py", "orchestration/plan_commit.py"]


def top_level_functions(source: str) -> set[str]:
    """Every function this source defines at the top level."""
    return {node.name for node in ast.parse(source).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_plan_commit_defines_neither_of_the_functions_it_gave_up(tmp_path: Path):
    """Both are gone, and the reader is shown reporting one that is not.

    The same reader names the functions `plan_commit` does define, so its
    silence about these two is a silence about these two rather than about
    everything.
    """
    source = (HARNESS_ROOT / PLAN_COMMIT_REL).read_text(encoding="utf-8")
    defined = top_level_functions(source)

    assert DELETED_RESOLVER not in defined
    assert "current_branch" not in defined
    assert "push_commit" in defined

    planted = tmp_path / "plan_commit_with_the_resolver.py"
    planted.write_text(
        source + f'\n\ndef {DELETED_RESOLVER}(target_root):\n    return ""\n',
        encoding="utf-8")
    assert DELETED_RESOLVER in top_level_functions(
        planted.read_text(encoding="utf-8"))


def names_the_deleted_call(source: str) -> bool:
    """Whether this source still reaches the plan-time resolver by name."""
    return DELETED_CALL in source


def test_l5_plan_resolves_through_the_shared_module_and_not_the_deleted_one(
        tmp_path: Path):
    """The entry point calls the shared resolver, and names the old one nowhere.

    The absence carries its control: the same reader over a copy of the entry
    point with the old call written back into it reports it.
    """
    source = (HARNESS_ROOT / L5_PLAN_REL).read_text(encoding="utf-8")

    assert "git_remote.resolve(target_root)" in source
    assert names_the_deleted_call(source) is False

    planted = tmp_path / "l5-plan-with-the-old-call"
    planted.write_text(
        source.replace("git_remote.resolve(target_root)",
                       f"{DELETED_CALL}(target_root)", 1), encoding="utf-8")
    assert names_the_deleted_call(
        planted.read_text(encoding="utf-8")) is True


def callers_of(source: str, name: str) -> set[str]:
    """Every top-level function in `source` that calls `name`."""
    callers = set()
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) \
                    and inner.func.id == name:
                callers.add(node.name)
    return callers


def test_the_base_keeps_its_one_derivation_and_both_its_readers():
    """`_base_remote` survives as a name, and both readers still reach it.

    What the name holds is the remote the *base* belongs to, which is what the
    base check and the base refresh each ask for; dissolving it into two direct
    calls would give that question two homes again, one story after it was
    given one. Its body is the shared resolver.
    """
    source = (HARNESS_ROOT / COORDINATOR_REL).read_text(encoding="utf-8")

    assert "_base_remote" in top_level_functions(source)
    assert callers_of(source, "_base_remote") == {"_base_tracking_ref",
                                                  "refresh_base"}
    assert "git_remote.resolve(target_root, base)" in conftest.function_source(
        source, "_base_remote")


def test_the_story_branch_fetch_reaches_the_same_resolver():
    """The other coordinator site, read where it stands."""
    source = (HARNESS_ROOT / COORDINATOR_REL).read_text(encoding="utf-8")

    assert "git_remote.resolve(target_root)" in conftest.function_source(
        source, "fetch_story_branch")
