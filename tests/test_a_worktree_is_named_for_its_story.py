"""Independent validation for story-197: a story's worktree directory is named
for the story, so it reads `story-172` rather than `story-story-172`.

Written from the story's acceptance criteria rather than from the
implementation. The subjects are kept apart:

  * **the derivation.** `worktrees.worktree_path` is a function over a
    configuration and a branch name, so most of what the story decides needs no
    repository at all: a configured worktree directory given outright, a branch,
    and the answer. Asked under the default prefix, under a prefix varied to
    something the harness would never pick, with the key unset, for a branch
    outside the prefix and for a branch that is exactly the prefix.

  * **what a run and a planning session do with it.** Driven end to end: a
    fresh run through `story_coordinator.run_story` against a fake agent
    runner, and a real `scripts/l5-plan` invocation against a stub `claude` on
    PATH. Both are observed by the directory they actually worked in — the
    stage's own working directory, and the directory the stub session wrote in
    — rather than by a path a derivation predicts, because the derivation is
    the subject and cannot also be the expectation.

  * **the resume path**, which this story must not have touched: a branch a
    worktree already stands on is answered by `worktrees.find`, so the run
    works in that tree and the derivation never decides anything.

  * **that the derivation is reached rather than restated**, read off the
    suite's own modules: no module under `tests/` flattens a branch itself in
    place of calling the function. What makes a flattening that one rather than
    some other name built out of a branch is the expression it is joined onto,
    resolved through the name that expression was bound to where it was bound
    in the same scope — so a recomputation split across two lines, which is how
    one would most naturally be written, is seen as readily as one written on a
    single line.

Nothing here reads this repository's own commit graph. Every repository is
built under a temporary directory, its "remote" is a bare repository beside it,
and no test reaches the network or invokes a model. The fixtures are the ones
`test_planning_runs_on_its_own_worktree` built for story-117 — the target with
its bare remote and stub session, the fake runner and the workflow the run
executes — imported rather than copied.

No prefix is written down here as the thing the function is expected to strip.
Every branch a derivation is asked about is built by
`story_coordinator.story_branch` from the same configuration the function is
handed, which is the story's own requirement that the two read one key with one
default: a story that changed the key or the default in one of them and not the
other reddens here as a disagreement rather than passing because this file
happened to spell the same literal.

Every absence asserted here carries a demonstration that it can fail:

  * `before_this_story` is this working tree's own `worktrees.py` with the
    stripping taken back out, loaded as its own module, so "the tree is not at
    the doubled name" is a reading of two different answers rather than a claim
    about one, and "no directory stands at the doubled name" is looking at the
    path that module names;
  * "a branch outside the prefix is unchanged" is that same pair answering
    *identically*, which is what makes unchanged a reading rather than a claim;
  * "no test module recomputes the flattening" is run beside the same scan over
    a directory carrying planted recomputations — one written on a single line
    and one split across two — both of which it reports, and beside a planted
    module building some other name out of a branch, which it does not; and it
    reads what the scan walked as well as what it found, because a scan pointed
    at a directory that is not there reports nothing just as contentedly as one
    reading a clean suite.
"""
import ast
import json
import re
from pathlib import Path

import pytest

import conftest
import story_coordinator
import worktrees

from test_planning_runs_on_its_own_worktree import (
    DEFAULT_BRANCH, STORY_ID, WORKFLOW, Runner, Target, build_target, planned,
    planned_story)

#: The working tree's own module file, which `before_this_story` mutates.
WORKTREES_SOURCE = Path(worktrees.__file__).resolve()

#: The two spellings of the derivation: the one this working tree carries, and
#: the one it carried before this story, which flattened the branch whole. The
#: substitution is asserted to have found its anchor by `load_mutant` itself, so
#: a story that rewrites this again reddens here as itself rather than quietly
#: producing a mutant identical to the original.
TODAYS_STRIPPING = (
    '    prefix = config.get("branch_prefix", "story/")\n'
    "    name = branch\n"
    "    if prefix and name.startswith(prefix) and name != prefix:\n"
    "        name = name[len(prefix):]"
)
NO_STRIPPING_AT_ALL = "    name = branch"

#: The configured key naming the prefix a story branch carries. Named here
#: rather than read off the module under test, and tied to
#: `story_coordinator.story_branch` by the first test below rather than
#: asserted on its own — the story's requirement is that the function stripping
#: the prefix and the function building the branch read one key with one
#: default.
BRANCH_PREFIX_KEY = "branch_prefix"

#: A story id no fixture reserves, used where a derivation is asked directly.
A_STORY = "story-172"

#: A prefix the harness would never pick, so a target configuring it proves the
#: stripping is against the configured value rather than against a literal.
AN_UNUSUAL_PREFIX = "xyzzy-branch/"

#: A branch that is not a story branch under any prefix here, and the directory
#: it has always named. The directory is stated rather than computed from the
#: branch, because computing it here would be the very restatement of the
#: flattening this story takes out of the suite.
A_BRANCH_OUTSIDE_THE_PREFIX = "feature/x"
THE_DIRECTORY_IT_HAS_ALWAYS_NAMED = "feature-x"

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"

#: A branch name flattened into a directory name: a slash replaced by a hyphen.
#: Used only to spare the parse below on a module that cannot contain one; the
#: scan itself reads the syntax tree rather than this.
FLATTENING = re.compile(r"""\.replace\(\s*(['"])/\1\s*,\s*(['"])-\2\s*\)""")

#: What makes such a flattening a *worktree's* name rather than some other name
#: a test builds out of a branch — a suite is free to make a filename or a
#: scope's label that way, and two modules here do. The recomputation this
#: story removes is the one that decides where a worktree lives, so what is
#: judged is the expression the flattening is joined onto and the value the
#: name in that expression was bound to, never the text of the line alone.
A_WORKTREE = re.compile(r"worktree", re.IGNORECASE)

#: The scopes a name can be bound in for the resolution below. A flattening is
#: judged against the bindings of the function it sits in, or of the module
#: where it sits in none, which is as far as a reading of one file can go
#: without becoming an interpreter.
SCOPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)


@pytest.fixture
def before_this_story(tmp_path: Path):
    """`worktrees` with the stripping this story added taken back out.

    The control for every absence below. It is the working tree's module with a
    single substitution, so the difference between it and the real one is this
    story's change and nothing else — which is what makes "the real module
    answers elsewhere" a demonstration of the stutter rather than a description
    of it.
    """
    return conftest.load_mutant(
        WORKTREES_SOURCE,
        [(TODAYS_STRIPPING, NO_STRIPPING_AT_ALL)],
        name="worktrees_before_story_197",
        tmp_path=tmp_path,
    )


@pytest.fixture
def target(tmp_path: Path) -> Target:
    return build_target(tmp_path / "target")


@pytest.fixture
def harness(tmp_path: Path) -> Path:
    return conftest.materialize_workflow(
        WORKFLOW, tmp_path / "named-for-its-story-harness")


def trees_at(tmp_path: Path, **config) -> dict:
    """A configuration naming where worktrees live, outright.

    Absolute, so `worktree_root` answers it verbatim and no repository has to
    exist for a question about the *name within* that directory — which is the
    whole of what this story decides.
    """
    return {worktrees.WORKTREE_DIR_KEY: str(tmp_path / "trees"), **config}


def flattenings(tree: ast.AST):
    """Every `branch.replace("/", "-")` in `tree`, as a call node.

    Read off the syntax tree rather than off the text, so how the call is
    spelled — which quotes, where the line breaks fall — cannot hide it, and so
    a flattening written inside a string literal a module merely carries is not
    one this module performs.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute) or node.func.attr != "replace":
            continue
        separators = [argument.value for argument in node.args
                      if isinstance(argument, ast.Constant)
                      and isinstance(argument.value, str)]
        if separators == ["/", "-"] and len(node.args) == 2:
            yield node


def names_a_worktree(call: ast.Call, parents: dict, scope: ast.AST) -> bool:
    """Whether `call`'s answer is where a worktree lives.

    What is judged is the expression the flattening is *joined onto* — the
    other side of each `/` it is an operand of — together with the statement
    holding it and, for every plain name in those, the value that name was
    bound to in `scope`. So both spellings of one recomputation are seen: the
    single line

        return worktree_directory(target) / branch.replace("/", "-")

    by the expression itself, and the same thing split in two

        root = worktree_directory(target)
        path = root / branch.replace("/", "-")

    by resolving `root` back through its binding. A branch flattened to build
    some other name — a filename, a scope's label — is joined onto no worktree
    and named by no worktree, so it is not reported.

    The resolution goes one binding deep and reads only assignments, which is
    what a reading of a file can do without becoming an interpreter. A
    recomputation that launders the directory through a second binding, or
    through a container or a call's return, is past what this can see.
    """
    joined: list[ast.AST] = []
    node: ast.AST = call
    statement: ast.AST = call
    while node in parents:
        parent = parents[node]
        if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Div):
            joined.append(parent.left if parent.right is node else parent.right)
        if isinstance(parent, ast.stmt):
            statement = parent
            break
        node = parent

    read = [ast.unparse(statement)] + [ast.unparse(side) for side in joined]
    wanted = {name.id for side in joined for name in ast.walk(side)
              if isinstance(name, ast.Name)}
    for bound in ast.walk(scope):
        if not isinstance(bound, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id in wanted
               for target in bound.targets):
            read.append(ast.unparse(bound.value))
    return any(A_WORKTREE.search(text) for text in read)


def modules_scanned(directory: Path) -> list[Path]:
    """Every module the scan below walks, in the order it walks them.

    The walk is asked for separately from its verdict because the verdict alone
    cannot say whether anything was read: a directory holding no recomputation,
    a directory holding nothing, and a directory that is not there at all each
    answer with an empty list, and `rglob` over the last of those raises
    nothing. A caller asserting an absence asks this first, so that green says
    the suite was read and carries no recomputation rather than that the scan
    was pointed somewhere it found no files to read.
    """
    return [path.resolve() for path in sorted(directory.rglob("*.py"))]


def recomputations(directory: Path) -> list[str]:
    """Every place under `directory` that names a worktree's directory itself.

    Reported as `file:line` so a failure names where to look rather than only
    saying that somewhere does. The line is where the flattening is written;
    what decided it is `names_a_worktree`, which reads the expression rather
    than the line, so a recomputation split across lines is reported at the one
    the flattening falls on.
    """
    found: list[str] = []
    for path in modules_scanned(directory):
        source = path.read_text(encoding="utf-8")
        if not FLATTENING.search(source):
            continue
        tree = ast.parse(source)
        parents = {child: parent for parent in ast.walk(tree)
                   for child in ast.iter_child_nodes(parent)}
        for call in flattenings(tree):
            scope = call
            while scope in parents and not isinstance(scope, SCOPES):
                scope = parents[scope]
            if names_a_worktree(call, parents, scope):
                found.append(f"{path.name}:{call.lineno}")
    return sorted(found)


# --------------------------------------------------------------------------
# The derivation
# --------------------------------------------------------------------------


def test_the_prefix_stripped_is_the_one_the_branch_was_built_with(
        tmp_path: Path):
    """The key and the default this function reads are the key and the default
    the branch was built with, which is what stops the two disagreeing.

    Read as an equality rather than as a pair of literals: for a configuration
    with the prefix set and for one with it unset, the branch
    `story_coordinator.story_branch` builds is the prefix followed by the id,
    and the directory `worktree_path` answers is named for the id alone. A
    change to either the key or the default in one function and not the other
    leaves these two readings disagreeing.
    """
    for config in (trees_at(tmp_path), trees_at(tmp_path, **{
            BRANCH_PREFIX_KEY: AN_UNUSUAL_PREFIX})):
        branch = story_coordinator.story_branch(config, A_STORY)
        # The branch still carries the prefix: only the directory is renamed.
        assert branch != A_STORY
        assert branch.endswith(A_STORY)

        path = worktrees.worktree_path(tmp_path, config, branch)

        assert path.name == A_STORY
        assert path.parent == worktrees.worktree_root(tmp_path, config)


def test_a_story_branch_under_the_default_prefix_names_the_story(
        tmp_path: Path, before_this_story):
    """The first criterion, with the stutter built beside it.

    The control is the same question asked of the module as it was before this
    story, which answers the directory saying the word twice — so "the
    directory is not that one" is a reading of two different answers.
    """
    config = trees_at(tmp_path)
    branch = story_coordinator.story_branch(config, A_STORY)

    path = worktrees.worktree_path(tmp_path, config, branch)

    assert path == worktrees.worktree_root(tmp_path, config) / A_STORY

    stuttering = before_this_story.worktree_path(tmp_path, config, branch)
    assert stuttering != path
    assert stuttering.name.count(A_STORY.split("-")[0]) == 2, stuttering.name


def test_a_branch_outside_the_configured_prefix_is_unchanged(
        tmp_path: Path, before_this_story):
    """The second criterion: a branch that does not carry the prefix keeps
    exactly today's answer.

    Read as an equality between this working tree's module and the module as it
    was before the story, so "unchanged" is the two answering the same rather
    than an assertion about a path that happens to look right.
    """
    config = trees_at(tmp_path)
    root = worktrees.worktree_root(tmp_path, config)

    path = worktrees.worktree_path(tmp_path, config, A_BRANCH_OUTSIDE_THE_PREFIX)

    assert path == root / THE_DIRECTORY_IT_HAS_ALWAYS_NAMED
    assert path == before_this_story.worktree_path(
        tmp_path, config, A_BRANCH_OUTSIDE_THE_PREFIX)
    # And the pair is capable of differing, which is what makes the equality
    # above a reading: for a story branch the same two answer differently.
    story_branch = story_coordinator.story_branch(config, A_STORY)
    assert worktrees.worktree_path(
        tmp_path, config, story_branch) != before_this_story.worktree_path(
            tmp_path, config, story_branch)


def test_the_stripping_is_against_the_configured_prefix_not_a_literal(
        tmp_path: Path, before_this_story):
    """The third criterion: a target configuring a prefix of its own has *that*
    one stripped, and a target configuring none has the default stripped.

    The unusual prefix and the default are read against each other: a branch
    under the unusual prefix names the story when that prefix is configured,
    and is flattened whole when it is not — so the configuration decided,
    rather than a name written into orchestration. "Flattened whole" is read as
    the answer the module gave before this story, which is what flattening a
    branch whole was.
    """
    unusual = trees_at(tmp_path, **{BRANCH_PREFIX_KEY: AN_UNUSUAL_PREFIX})
    default = trees_at(tmp_path)
    unusual_branch = story_coordinator.story_branch(unusual, A_STORY)
    default_branch = story_coordinator.story_branch(default, A_STORY)

    assert worktrees.worktree_path(
        tmp_path, unusual, unusual_branch).name == A_STORY
    assert worktrees.worktree_path(
        tmp_path, default, unusual_branch) == before_this_story.worktree_path(
            tmp_path, default, unusual_branch)

    # And the same the other way round: a branch under the default prefix is
    # flattened whole for a target configuring the unusual one.
    assert worktrees.worktree_path(
        tmp_path, default, default_branch).name == A_STORY
    assert worktrees.worktree_path(
        tmp_path, unusual, default_branch) == before_this_story.worktree_path(
            tmp_path, unusual, default_branch)


def test_a_branch_that_is_only_the_prefix_still_names_a_directory(
        tmp_path: Path):
    """The fourth criterion: the answer is never the worktree root itself.

    Asked for each configured prefix, because an empty name would be the root
    under any of them. The directory is a child of the root with a name of its
    own, which is what a `git worktree add` can be given.
    """
    for config in (trees_at(tmp_path), trees_at(tmp_path, **{
            BRANCH_PREFIX_KEY: AN_UNUSUAL_PREFIX})):
        root = worktrees.worktree_root(tmp_path, config)
        prefix = story_coordinator.story_branch(config, "")

        path = worktrees.worktree_path(tmp_path, config, prefix)

        assert path != root
        assert path.parent == root
        assert path.name


# --------------------------------------------------------------------------
# What a run and a planning session create
# --------------------------------------------------------------------------


def test_a_fresh_run_works_in_a_tree_named_for_the_story(
        target: Target, harness, before_this_story):
    """The fifth criterion for a run, driven end to end.

    The tree is observed by where the stages actually ran and where the state
    was written. Its *name* is read against the story's own id — the fact the
    story is about — rather than against a path recomputed from the branch.

    The control for "nothing stands at the doubled name" is the path the module
    as it was before this story derives, which is where the tree would have
    been: the assertion is looking somewhere a failure could show.
    """
    planned_story(target)
    runner = Runner(WORKFLOW)

    code = story_coordinator.run_story(STORY_ID, harness, target.root, runner)
    assert code == 0, runner.calls

    trees = {tree.resolve() for tree in runner.trees}
    assert len(trees) == 1, runner.trees
    tree = trees.pop()
    assert tree.name == STORY_ID
    assert (tree / ".harness" / "runs" / STORY_ID / "state.json").is_file()
    # The branch itself is untouched: it still carries the prefix, and it is
    # the directory alone that stopped saying the word twice.
    branch = target.story_branch(STORY_ID)
    assert worktrees.standing_branch(tree) == branch
    assert branch != tree.name

    stuttering = before_this_story.worktree_path(
        target.root, target.config(), branch)
    assert stuttering.resolve() != tree
    assert not stuttering.exists(), f"a tree at the doubled name {stuttering}"


def test_a_planning_session_works_in_a_tree_named_for_the_story(
        target: Target, before_this_story):
    """The fifth criterion for planning, driven end to end.

    Observed by where the session wrote — the stub writes its artifact relative
    to the directory it is handed — because the worktree is removed once the
    plan is pushed and the run offer is declined, and the directory the session
    ran in is the fact the criterion is about.

    The id and the branch are read off what the script itself reported it
    reserved, so nothing here predicts either.
    """
    result = target.plan("a request", L5_STUB_WRITE=planned())
    assert result.returncode == 0, result.stdout + result.stderr

    reserved = re.search(r"reserved (\S+) on \S+ as (\S+)\.", result.stdout)
    assert reserved, result.stdout
    story_id, branch = reserved.group(1), reserved.group(2)
    assert branch.endswith(story_id) and branch != story_id

    session_cwd = Path(target.session()["cwd"]).resolve()
    assert session_cwd.name == story_id

    # The control: before this story the same configuration and branch named a
    # directory saying the word twice, which is not the one the session ran in.
    stuttering = before_this_story.worktree_path(
        target.root, target.config(), branch)
    assert stuttering.name != session_cwd.name
    assert stuttering.name.endswith(story_id) and stuttering.name != story_id


def test_a_run_for_a_branch_with_a_tree_still_works_in_that_tree(
        target: Target, harness):
    """The sixth criterion: the resume path is answered by `worktrees.find`,
    above the derivation, so this story cannot have moved it.

    The existing tree is deliberately placed somewhere neither derivation would
    name — neither the story's name nor the doubled one — so that working in it
    is `find` answering rather than a path either version could have produced.
    """
    planned_story(target)
    branch = target.story_branch(STORY_ID)
    elsewhere = target.root.parent / "a-tree-named-nothing-like-either"
    made = worktrees.add(target.root, elsewhere, branch, DEFAULT_BRANCH)
    assert not made.problems, made.problems

    runner = Runner(WORKFLOW)
    code = story_coordinator.run_story(STORY_ID, harness, target.root, runner)
    assert code == 0, runner.calls

    assert {tree.resolve() for tree in runner.trees} == {elsewhere.resolve()}
    assert (elsewhere / ".harness" / "runs" / STORY_ID / "state.json").is_file()
    # And nothing was created for that branch where the derivation would have
    # put it, which is the other half: `find` answered first.
    assert not worktrees.worktree_path(
        target.root, target.config(), branch).exists()


# --------------------------------------------------------------------------
# The derivation is reached, not restated
# --------------------------------------------------------------------------


def test_no_test_module_flattens_a_branch_in_place_of_the_derivation():
    """That the derivation is reached rather than restated, read off the
    suite's own modules.

    An absence over every module under `tests/`, and an absence needs two
    different things shown before green means anything.

    That the scan can still *see* a recomputation is shown by the planted
    directory below, which it reports — in both spellings, one line and two, so
    green says it sees the form somebody would write rather than only the form
    it was written against.

    That the scan was pointed at *this suite* is shown here, by asking what it
    walked before asking what it found. `TESTS_DIR` is derived from this file's
    own location, and a directory that is not there yields no files and raises
    nothing, so an empty verdict over a mis-derived path reads exactly like an
    empty verdict over a clean suite. Finding this module among the modules
    walked tells the two apart, and it is what moving this file one directory
    down would break.
    """
    walked = modules_scanned(TESTS_DIR)
    assert Path(__file__).resolve() in walked, TESTS_DIR

    assert recomputations(TESTS_DIR) == []


def test_the_scan_for_a_restated_flattening_reports_one(tmp_path: Path):
    """The control for the assertion above, and the reason it is a reading.

    Three modules are planted, and what they show together is where the scan's
    line falls. One names a worktree's directory out of a branch on a single
    line. One does the identical thing split across two lines, binding the
    directory first and flattening onto the bound name — the form the two
    converted sites would most naturally have been rewritten as, and the form a
    scan reading one line at a time cannot see. Both are reported. The third
    builds some other name out of a branch the same way, which is the shape two
    modules in this suite legitimately have, and is not.

    The planted lines are assembled from pieces rather than written out,
    because a source line spelling the recomputation would be found in *this*
    module by the very scan it controls.

    The third builds its name by concatenation rather than by interpolation,
    and that is a constraint rather than a preference: the scan parses what is
    planted with the interpreter it is running under, so the plant has to be
    source every interpreter this suite is parsed by accepts. An f-string
    carrying the flattening inside it nests a double quote inside a
    double-quoted f-string, which is a syntax error before Python 3.12 and so
    fails under the interpreter `verification_runner` names — where the whole
    suite is run by the revert and clean-clone checks.
    """
    planted = tmp_path / "modules"
    planted.mkdir()
    flatten = ".replace({slash}, {hyphen})".format(slash='"/"', hyphen='"-"')
    (planted / "test_a_planted_recomputation.py").write_text(
        "def where(target, branch):\n"
        f"    return worktree_directory(target) / branch{flatten}\n",
        encoding="utf-8")
    (planted / "test_a_recomputation_over_two_lines.py").write_text(
        "def where(target, branch):\n"
        "    root = worktree_directory(target)\n"
        f"    path = root / branch{flatten}\n"
        "    return path\n",
        encoding="utf-8")
    (planted / "test_a_name_that_is_not_a_tree.py").write_text(
        "def marker(branch):\n"
        f'    return "only-on-" + branch{flatten} + ".txt"\n',
        encoding="utf-8")

    assert recomputations(planted) == [
        "test_a_planted_recomputation.py:2",
        "test_a_recomputation_over_two_lines.py:3",
    ]


def test_the_scan_reads_the_directory_it_is_given(tmp_path: Path):
    """And it reads that directory rather than always this repository's, which
    is what lets the control above mean anything.

    It also shows why the standing assertion asks what was walked: an empty
    directory and a directory that was never created answer the verdict
    identically, and only the walk distinguishes them.
    """
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    assert recomputations(empty) == []
    assert modules_scanned(empty) == []

    never_created = tmp_path / "not-a-directory-at-all"
    assert not never_created.exists()
    assert recomputations(never_created) == []
    assert modules_scanned(never_created) == []

    assert modules_scanned(TESTS_DIR) != []


# --------------------------------------------------------------------------
# The rule as the schema states it
# --------------------------------------------------------------------------


def test_the_schema_says_the_prefix_is_dropped_before_the_name_is_made():
    """The configuration schema describes this key to whoever configures it, so
    its prose would otherwise stand contradicting the function.

    Read as the presence of what it now has to say rather than as the absence
    of the old wording, so it fails loudly on its own. The architecture
    document states the same rule and is written by a later stage than this
    one, so what it says is judged by verification rather than asserted here.
    """
    schema = json.loads(
        (REPO_ROOT / "schemas" / "harness-config.schema.json").read_text(
            encoding="utf-8"))
    described = schema["properties"][worktrees.WORKTREE_DIR_KEY]["description"]
    assert BRANCH_PREFIX_KEY in described, described
    assert "dropped" in described or "stripped" in described, described
