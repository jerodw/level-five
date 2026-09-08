"""Independent validation: the queue and the receipt index belong to the
repository rather than to a working tree of it.

Written from the story's acceptance criteria rather than from the
implementation. The subjects are kept apart deliberately:

  * **the derivation.** `worktrees.primary_root` is a function over a path, so
    it is driven directly against repositories built here: a checkout with two
    linked worktrees, a repository with no worktree at all, a directory that is
    not a repository, a bare repository and a worktree cut from one, and a git
    invocation that fails — the last arranged by putting a `git` that exits
    non-zero on PATH, so what is exercised is the failure branch rather than a
    monkeypatched stand-in for it.

  * **where the pair resolves.** `outbox.queue_dir` and `outbox.receipts_dir`
    are asked from each tree of one repository and from a directory that is not
    one, and `outbox.receipts_beside` is asked to agree with `receipts_dir`
    everywhere both are defined. The paths are composed from the module's own
    `QUEUE_DIR` and `RECEIPTS_DIR` rather than spelled here: a second spelling
    of where the queue lives is a second thing to keep true, which is the very
    drift this story exists to remove.

  * **filing in one tree and draining from another.** Driven end to end through
    the queue's own operations against the fake transport
    `tests/test_outbox.py` already builds — an entry enqueued from inside a
    worktree through a transport that cannot deliver, then found and landed by
    a drain whose queue was resolved from the primary tree, then held as landed
    when a third tree asks. Nothing here reaches a network.

  * **the one derivation.** A scan over `orchestration/` and `scripts/` for the
    derivation's own name, and a comparison of the sweep call sites against the
    same call sites at this story's baseline, resolved through
    `conftest.story_commit_range`. The shipped modules are the subject of these
    assertions — the claim is about what this repository holds — so they are
    read rather than mirrored.

  * **the design statement.** The comment in `story_coordinator._complete` that
    says what a crashed, escalated or paused run leaves behind is extracted and
    searched, never eyeballed.

Every absence asserted here carries a demonstration that it can fail:

  * "a git that cannot answer leaves the queue beneath the tree it was asked
    from" sits beside the same call made before the failing `git` was put on
    PATH, which answers with the primary tree;
  * "the worktree holds no queue of its own" sits beside the same listing over
    the same worktree with an entry planted beneath it, which the listing
    reports;
  * "no module outside the queue resolves the primary tree for itself" sits
    beside the same scan over the same sources with the derivation planted in
    one of them, which the scan reports;
  * "the sweep call sites are the ones this story found" sits beside the same
    comparison over a source whose sweep is handed a different root, which the
    comparison reports;
  * "this story changed neither the drain script nor the seam nor the queue's
    other readers" sits beside the same diff over the module this story
    rewrote, which is not empty;
  * "the design statement no longer describes a per-tree queue" sits beside the
    same statement with its naming of the repository substituted out, which the
    same reading reports.
"""
from __future__ import annotations

import ast
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

import conftest
import outbox
import outbox_sweep
import worktrees

import test_outbox as queue_tests

REPO_ROOT = Path(outbox.__file__).resolve().parents[1]

#: The coordinator and the drain script as repository-relative paths, since
#: this module reads both out of the repository rather than out of a fixture.
COORDINATOR = "orchestration/story_coordinator.py"
DRAIN_SCRIPT = "scripts/l5-sync"

#: The readers this story must have left alone, named as the story's scope
#: names them: the drain script, the seam every run reaches the queue through,
#: and the three modules that read or write the queue as data.
UNCHANGED_READERS = (
    DRAIN_SCRIPT,
    f"orchestration/{outbox_sweep.__name__}.py",
    "orchestration/run_status.py",
    "orchestration/brief_filing.py",
    "orchestration/inspection.py",
)

#: The module this story rewrote, which is the control for the diff above: a
#: comparison reporting nothing for the readers has to be one that reports
#: something for a file that did change.
THE_MODULE_THIS_STORY_REWROTE = f"orchestration/{outbox.__name__}.py"

#: The derivation's own name, read off the module that defines it so this
#: module spells no function name of its own.
DERIVATION = worktrees.primary_root.__name__

#: The two modules that may name the derivation: the one that defines it, and
#: the one that resolves the pair through it. Every other caller of `queue_dir`
#: and `receipts_dir` passes the root it already had, which is what "the
#: repository holds one derivation of where the queue lives" means.
MODULES_THAT_MAY_NAME_THE_DERIVATION = (
    f"{worktrees.__name__}.py",
    f"{outbox.__name__}.py",
)

#: The sentence in the coordinator's sweep comment this story rewrote, anchored
#: on the run it is about rather than on the claim it makes — so a statement
#: that stopped making the claim is found and reported rather than missed.
THE_RUN_THAT_DID_NOT_FINISH = "crashed, escalated or paused"

IDENTITY = {"kind": "zzz-one-queue-per-repository", "subject": "the-first"}
PAYLOAD = {"title": "something to file", "body": "what it says"}

PENDING = outbox.PENDING
LANDED = outbox.LANDED


# --------------------------------------------------------------------------
# The repositories this module drives, built here rather than reached for
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Trees:
    """One repository's primary working tree and two linked worktrees.

    The shape the story is about: a run works in `first`, a developer drains
    from `primary`, and an assist session somewhere else in the repository is
    `second`. All three are trees of one repository, so all three must name one
    queue.
    """

    primary: Path
    first: Path
    second: Path

    @property
    def all(self) -> tuple[Path, ...]:
        return (self.primary, self.first, self.second)


def a_repository(root: Path) -> Path:
    """A repository at `root` with one commit in it."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("the tree this starts from\n",
                                    encoding="utf-8")
    conftest.init_repository(root)
    return root


def a_worktree(root: Path, path: Path, branch: str) -> Path:
    made = worktrees.add(root, path, branch, conftest.BASE_BRANCH_FALLBACK)
    assert not made.problems, made.problems
    return path


@pytest.fixture
def trees(tmp_path: Path) -> Trees:
    primary = a_repository(tmp_path / "checkout")
    return Trees(
        primary=primary,
        first=a_worktree(primary, tmp_path / "trees" / "first", "story/first"),
        second=a_worktree(primary, tmp_path / "trees" / "second", "story/second"),
    )


@pytest.fixture
def not_a_repository(tmp_path: Path) -> Path:
    """A plain directory, which is what every tmpdir target in this suite is."""
    root = tmp_path / "a-target"
    root.mkdir()
    return root


@pytest.fixture
def bare(tmp_path: Path) -> Path:
    """A repository whose primary working tree does not exist.

    Cloned from an ordinary repository rather than initialised empty, so it
    carries a branch a worktree can be cut from — the case that matters, since
    a bare repository's `.git` sits beside whatever directory it happens to
    have been created in and that directory is not thereby a working tree.
    """
    source = a_repository(tmp_path / "source")
    root = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "--bare", "-q", str(source), str(root)],
                   cwd=tmp_path, check=True)
    return root


@pytest.fixture
def git_that_fails(tmp_path: Path, monkeypatch) -> Path:
    """A `git` on PATH that answers every invocation with a failure.

    The failure branch is reached by making git fail rather than by standing in
    for the module's own call, so what is exercised is the code that runs when
    a real invocation comes back non-zero.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "git"
    shim.write_text("#!/bin/sh\necho 'git: made to fail here' >&2\nexit 1\n",
                    encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


def beneath(root: Path, *names: str) -> Path:
    """`root` with a directory name composed from the queue's own constants."""
    return root.joinpath(*names).resolve()


# --------------------------------------------------------------------------
# The derivation: which tree is the repository's own
# --------------------------------------------------------------------------


def test_a_linked_worktree_answers_with_the_repositorys_primary_tree(trees):
    for tree in (trees.first, trees.second):
        assert worktrees.primary_root(tree).resolve() == trees.primary.resolve()


def test_the_primary_tree_answers_with_itself(trees):
    assert worktrees.primary_root(trees.primary).resolve() \
        == trees.primary.resolve()


def test_a_repository_with_no_worktrees_is_unchanged(tmp_path):
    """The behaviour every repository that never cut a worktree still has."""
    alone = a_repository(tmp_path / "alone")
    assert worktrees.primary_root(alone).resolve() == alone.resolve()


def test_a_directory_that_is_not_a_repository_answers_with_itself(
        not_a_repository):
    assert worktrees.primary_root(not_a_repository) == not_a_repository


def test_a_repository_whose_primary_is_bare_answers_with_the_path_it_was_given(
        bare, tmp_path):
    """Both ways of asking: the bare repository itself, and a worktree of it.

    A bare repository has no working tree, so there is no primary tree to
    answer with and the derivation reports the path it was handed. The worktree
    is the case that would go wrong quietly: its common directory is the bare
    repository, whose parent is an ordinary directory that is nobody's working
    tree.
    """
    assert worktrees.primary_root(bare) == bare

    tree = a_worktree(bare, tmp_path / "trees" / "off-a-bare", "story/bare")
    assert worktrees.primary_root(tree) == tree


def test_a_git_that_fails_answers_with_the_path_it_was_given(
        trees, git_that_fails):
    """The failing git is what changed the answer, which is the control.

    The same repository is asked the same question in the test below, with git
    working, where it answers with the primary tree. So this answer is a fact
    about the failure branch rather than about a derivation that never resolved
    anything, and the queue degrading to the tree it was asked from is what the
    pair does when git cannot say.
    """
    assert worktrees.primary_root(trees.first) == trees.first
    assert outbox.queue_dir(trees.first).resolve() \
        == beneath(trees.first, *outbox.QUEUE_DIR)


def test_the_answer_before_the_git_that_fails_is_the_primary_tree(trees):
    """The other half of the pair above, taken with git working."""
    assert worktrees.primary_root(trees.first).resolve() \
        == trees.primary.resolve()
    assert outbox.queue_dir(trees.first).resolve() \
        == beneath(trees.primary, *outbox.QUEUE_DIR)


def test_the_derivation_reports_rather_than_raises(trees, not_a_repository,
                                                   bare):
    """Every case above through one call, so the module's rule that nothing
    here raises is asserted as the rule rather than case by case."""
    for root in (*trees.all, not_a_repository, bare, Path("/nowhere-at-all")):
        assert isinstance(worktrees.primary_root(root), Path)


# --------------------------------------------------------------------------
# Where the pair resolves
# --------------------------------------------------------------------------


def test_the_queue_and_the_index_of_a_worktree_are_beneath_the_primary_tree(
        trees):
    assert outbox.queue_dir(trees.first).resolve() \
        == beneath(trees.primary, *outbox.QUEUE_DIR)
    assert outbox.receipts_dir(trees.first).resolve() \
        == beneath(trees.primary, *outbox.RECEIPTS_DIR)


def test_every_tree_of_one_repository_names_one_queue_and_one_index(trees):
    queues = {outbox.queue_dir(tree).resolve() for tree in trees.all}
    indexes = {outbox.receipts_dir(tree).resolve() for tree in trees.all}
    assert queues == {beneath(trees.primary, *outbox.QUEUE_DIR)}
    assert indexes == {beneath(trees.primary, *outbox.RECEIPTS_DIR)}


def test_a_directory_that_is_not_a_repository_keeps_its_queue_beneath_itself(
        not_a_repository):
    """Today's answer, which every tmpdir fixture in this suite depends on."""
    assert outbox.queue_dir(not_a_repository) \
        == not_a_repository.joinpath(*outbox.QUEUE_DIR)
    assert outbox.receipts_dir(not_a_repository) \
        == not_a_repository.joinpath(*outbox.RECEIPTS_DIR)


def test_the_index_beside_the_queue_is_the_index_the_pair_resolves(
        trees, not_a_repository, bare):
    """`receipts_beside` derives the index from a queue it is handed, and
    `receipts_dir` derives it from a root. Both roots are now the same root, so
    the two derivations must answer the same path for every tree of a
    repository and for a directory that is not one."""
    for root in (*trees.all, not_a_repository, bare):
        assert outbox.receipts_beside(outbox.queue_dir(root)) \
            == outbox.receipts_dir(root)


# --------------------------------------------------------------------------
# Filing in one tree and draining from another
# --------------------------------------------------------------------------


def test_an_entry_filed_from_a_worktree_is_landed_by_a_drain_from_the_primary(
        trees):
    """The property end to end, in the order it happens in.

    A run working in the worktree enqueues a brief and its sweep cannot reach
    the provider, so the entry is left pending. The developer drains from the
    checkout later, and the entry the run filed is the entry that drain finds.
    """
    key = outbox.enqueue(outbox.queue_dir(trees.first), PAYLOAD, IDENTITY)
    assert key

    unreachable = queue_tests.FakeTransport(answer=queue_tests.transient())
    left = outbox.sync(outbox.queue_dir(trees.first), unreachable)
    assert left.pending_keys == (key,)
    assert left.landed == 0

    delivering = queue_tests.FakeTransport(answer=queue_tests.filing())
    drained = outbox.sync(outbox.queue_dir(trees.primary), delivering)
    assert delivering.filed == [key]
    assert drained.landed_keys == (key,)
    assert drained.pending == 0


def test_the_worktree_holds_no_queue_of_its_own(trees):
    """The absence, and the same listing shown reporting a planted queue.

    Green here has to mean the entry went to the repository's queue rather than
    that the listing has stopped seeing anything, so the same expression is
    asked about a directory that does hold one.
    """
    key = outbox.enqueue(outbox.queue_dir(trees.first), PAYLOAD, IDENTITY)
    per_tree = trees.first.joinpath(*outbox.QUEUE_DIR)
    assert outbox.entry_files(per_tree) == []
    assert [path.stem for path in
            outbox.entry_files(trees.primary.joinpath(*outbox.QUEUE_DIR))] == [key]

    # The control: the same listing over the same directory, with an entry
    # written straight into it.
    outbox.write_entry(per_tree, {
        "key": key, "identity": IDENTITY, "state": PENDING, "payload": PAYLOAD,
        "attempts": 0, "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    })
    assert [path.stem for path in outbox.entry_files(per_tree)] == [key]


def test_a_landed_entry_is_held_as_landed_when_a_second_tree_asks(trees):
    """What local dedupe restarting from empty in every new tree cost: the same
    brief filed once per working tree. The receipt is written by a drain
    resolved from the primary tree and asked for from the second worktree,
    which is neither the tree that filed it nor the tree that landed it."""
    key = outbox.enqueue(outbox.queue_dir(trees.first), PAYLOAD, IDENTITY)
    outbox.sync(outbox.queue_dir(trees.primary),
                queue_tests.FakeTransport(answer=queue_tests.filing()))

    assert outbox.local_state(trees.second, key) == LANDED
    held = outbox.local_index(trees.second)
    assert held.read
    assert key in held.landed
    assert key not in held.queued


# --------------------------------------------------------------------------
# One derivation of where the queue lives
# --------------------------------------------------------------------------


def modules_naming_the_derivation(sources: dict[str, str]) -> list[str]:
    """Which of `sources` names the derivation of the primary working tree."""
    return sorted(name for name, text in sources.items() if DERIVATION in text)


def test_no_module_outside_the_queue_resolves_the_primary_tree_for_itself():
    """Every caller of the pair passes the root it already had.

    A module that resolved the primary tree for itself before asking for a
    queue would be a second derivation of where the queue lives, which is how
    the two would come apart again.
    """
    sources = queue_tests.repository_sources()
    assert modules_naming_the_derivation(sources) == sorted(
        MODULES_THAT_MAY_NAME_THE_DERIVATION)
    # The other side of each exemption: a module that stopped naming the
    # derivation is an exemption nobody notices has gone stale.
    for name in MODULES_THAT_MAY_NAME_THE_DERIVATION:
        assert name in sources, name
        assert DERIVATION in sources[name], name


def test_the_scan_reports_a_second_derivation_planted_in_a_caller():
    """Control: the report above must mean no other module resolves the primary
    tree, not that the scan has stopped seeing the name.

    The victim is derived rather than named, so this stays a control over
    whatever modules the repository holds.
    """
    sources = queue_tests.repository_sources()
    victim = next(name for name in sorted(sources)
                  if name not in MODULES_THAT_MAY_NAME_THE_DERIVATION)
    sources[victim] += (f"\nqueue = {outbox.__name__}.queue_dir("
                        f"{worktrees.__name__}.{DERIVATION}(root))\n")
    assert victim in modules_naming_the_derivation(sources)


def sweep_call_sites(source: str) -> list[str]:
    """Every call through the sweep seam in one source, as it is written.

    The function called and the arguments handed to it, rendered back from the
    parse rather than compared as text, so a reformatting is not read as a
    change of what a call site does and a changed argument is.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not (isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == outbox_sweep.__name__):
            continue
        found.append(ast.unparse(node))
    return sorted(found)


def test_the_coordinator_sweeps_call_what_they_called_and_hand_what_they_handed():
    """The sweeps are unchanged in which function they call and what they hand
    it, compared against this story's own baseline rather than against a
    listing written here — a listing is a second copy of the answer.
    """
    shipped = (REPO_ROOT / COORDINATOR).read_text(encoding="utf-8")
    before = conftest.repository_file_at(
        COORDINATOR, validation_file=Path(__file__), bound=conftest.BASELINE)
    calls = sweep_call_sites(shipped)
    assert calls, "the coordinator reaches the queue through the seam"
    assert calls == sweep_call_sites(before)


def test_that_comparison_reports_a_sweep_handed_a_different_root():
    """Control: the equality above must mean the call sites are unchanged, not
    that the comparison has stopped reading them."""
    shipped = (REPO_ROOT / COORDINATOR).read_text(encoding="utf-8")
    calls = sweep_call_sites(shipped)
    moved = shipped.replace(f"{outbox_sweep.__name__}.sweep(target_root",
                            f"{outbox_sweep.__name__}.sweep(primary", 1)
    assert moved != shipped
    assert sweep_call_sites(moved) != calls


def test_this_story_changed_neither_the_drain_script_nor_the_queues_readers():
    """The drain script and the four modules the story's scope holds shut,
    asked of the story's own commit range rather than of the working tree."""
    assert conftest.story_diff(list(UNCHANGED_READERS),
                               validation_file=Path(__file__)) == ""


def test_that_diff_reports_the_module_this_story_rewrote():
    """Control for the emptiness above: the same comparison over the module the
    story did change is not empty."""
    assert conftest.story_diff([THE_MODULE_THIS_STORY_REWROTE],
                               validation_file=Path(__file__)) != ""


# --------------------------------------------------------------------------
# The design statement about what a run that did not finish leaves behind
# --------------------------------------------------------------------------


def comment_blocks(text: str) -> list[str]:
    """Each run of comment lines in `text`, joined into one paragraph.

    A statement spans lines, so asking whether a sentence says something means
    reading the block rather than the line the phrase happens to fall on.
    """
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            current.append(stripped.lstrip("#").strip())
        elif current:
            blocks.append(" ".join(current).strip())
            current = []
    if current:
        blocks.append(" ".join(current).strip())
    return blocks


def what_a_run_that_did_not_finish_leaves(source: str) -> str:
    """The statement in `_complete` about the entries such a run leaves.

    Found by the run it is about rather than by the claim it makes, so a
    statement that stopped making the claim is still found and reported.
    """
    blocks = [block for block
              in comment_blocks(conftest.function_source(source, "_complete"))
              if THE_RUN_THAT_DID_NOT_FINISH in block]
    assert len(blocks) == 1, blocks
    return blocks[0]


def names_the_repositorys_queue(statement: str) -> bool:
    """Whether the sentence that says what such a run leaves names the queue as
    the repository's, rather than describing a queue belonging to a tree."""
    sentences = [sentence for sentence in statement.split(". ")
                 if THE_RUN_THAT_DID_NOT_FINISH in sentence]
    assert len(sentences) == 1, sentences
    return "repository" in sentences[0] and "queue" in sentences[0]


def test_the_statement_names_the_queue_as_the_repositorys():
    statement = what_a_run_that_did_not_finish_leaves(
        (REPO_ROOT / COORDINATOR).read_text(encoding="utf-8"))
    assert names_the_repositorys_queue(statement)
    # And it still names both readers, which is what makes leaving the entries
    # there a design rather than an accident.
    assert "next run" in statement
    assert Path(DRAIN_SCRIPT).name in statement


def test_that_reading_reports_a_statement_describing_a_per_tree_queue():
    """Control: the same reading over the same statement with its naming of the
    repository substituted out — which is what a per-tree queue reads like —
    must report it."""
    statement = what_a_run_that_did_not_finish_leaves(
        (REPO_ROOT / COORDINATOR).read_text(encoding="utf-8"))
    per_tree = statement.replace("the repository's one queue",
                                 "this working tree's queue", 1)
    assert per_tree != statement
    assert not names_the_repositorys_queue(per_tree)
