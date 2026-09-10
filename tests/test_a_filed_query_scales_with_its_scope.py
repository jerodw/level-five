"""story-131 validation: a filed query answers for a scope of any size.

The tracker tier of the inspector's dedupe never ran, because the query branch
of the reference tracker command searched once per path in a sequential loop:
the command was killed at the harness's bound partway through a scope of sixty
files and never answered. This story stops the number of searches being
proportional to the scope — several path markers ride in one search — and the
harness's bound, the scope it hands over and what an answer means are all left
where they were.

The subjects are kept apart deliberately:

  * **how many searches a scope costs.** Counted at the tracker rather than
    inferred from how long the answer took: the stub `gh` records every search
    it was made in the order it was made, so a question carrying more paths
    than one batch holds is asserted to cost one search per batch and no more.

  * **what the batched answer says.** The same tracker state is asked twice —
    once as the branch now searches and once with the batch size set to one,
    which is the per-path loop this story replaced — and the two answers are
    required to be the same items, the same keys, the same titles and the same
    per-item path attribution. An item whose markers span two batches is filed
    deliberately, because two searches returning it is exactly how a union
    that stopped deduplicating would report it twice.

  * **the two answers that cannot be trusted.** A batch whose search failed and
    a batch whose page filled to the limit are each constructed at the stub and
    each required to be re-asked one path at a time. The filled page is the
    sharper of the two: the truncated page is short of an item the per-path
    searches find, so the answer carrying it is the fallback having run rather
    than a page that happened to be complete.

  * **the failure that is still a failure.** A per-path search that fails after
    the fallback fails the whole answer: the script exits non-zero naming the
    path, and the harness reads that as nothing known rather than as nothing
    filed.

  * **the two shipped copies.** `templates/scripts/github.sh` and this
    repository's installed `.harness/scripts/github.sh` are live harness
    artifacts and are the subject of the assertions that name them: the query
    branch is required to be the same text in both, while each copy keeps the
    board constants it is configured with.

Every absence asserted here carries a demonstration that it can fail:

  * "no per-path search was made" sits beside the same scope against a stub
    whose batched search fails and against one whose page fills to the limit,
    where the per-path searches are made and are counted;
  * "the query branch has not drifted between the two copies" sits beside a
    rendering of the template whose query branch differs by a line of
    mechanics, which the same comparison reports;
  * "no batching constant is named outside the query job" sits beside a
    rendering of the template that names one inside the sync branch, which the
    same scan reports.

Nothing here reaches a network or a model: the shipped script is run as it
ships against the stub `gh` `tests/test_filed_query.py` wrote, first on `PATH`,
and every question is put through `filed_query.query` — the harness's own path,
under the harness's own default bound.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import re
import subprocess
from pathlib import Path

from test_filed_query import (  # noqa: F401 - shared idioms and fixtures
    BATCHED_SEARCH_CALL,
    FAIL_VARIABLE,
    INSTALLED_SCRIPT,
    INTERPRETER,
    ISSUE_LIST_CALL,
    LEDGER_VARIABLE,
    QUERY_JOB,
    TEMPLATE_CONSTANTS,
    TEMPLATE_SCRIPT,
    asked,
    branch_source,
    declared_marker,
    file_through_the_reference_sync,
    knows_nothing,
    ledger_state,
    lines_that_differ,
    needs_jq,
    reference_script,
    stub_tracker,
)

#: The marker the sync branch writes and the query branch searches for, read
#: out of the one place the shipped script declares it — so a test that reads a
#: search back reads it by the same declaration both branches refer to.
PATH_MARKER = declared_marker(TEMPLATE_SCRIPT.read_text(encoding="utf-8"))

#: The query job's own constants, resolved out of the shipped script rather
#: than written here: each is read from the environment, so the variable is how
#: a test drives the branch at a size it chooses, and the default is what a
#: scope is measured against. A constant renamed or dropped is a resolution
#: that fails here rather than an override that silently stops overriding.
BATCH_VARIABLE, BATCH_DEFAULT = TEMPLATE_CONSTANTS["BATCH"]
LIMIT_VARIABLE, LIMIT_DEFAULT = TEMPLATE_CONSTANTS["LIMIT"]

BATCH = int(BATCH_DEFAULT)
LIMIT = int(LIMIT_DEFAULT)

#: Every constant the query job alone reads, by the job its variable names.
#: Derived rather than listed, so a constant added to that job is covered by
#: the confinement scan below without this module being edited.
QUERY_CONSTANTS = tuple(sorted(
    name for name, (variable, _default) in TEMPLATE_CONSTANTS.items()
    if QUERY_JOB.upper() in variable.split("_")))

#: The branches of the shipped script this module reads: the query job's own
#: entry point and the batch helper it calls, which together are the whole of
#: what this story changed, and the two branches it did not touch.
QUERY_BRANCHES = ("do_query", "query_batch")
OTHER_BRANCHES = ("do_sync", "do_item")

#: The scope the failure this story exists against was measured at: story-130's
#: post-story inspection carried sixty files, one search cost roughly 0.85
#: seconds, and the query is held to the harness's default bound.
THE_SCOPE_THAT_DID_NOT_ANSWER = 60


def scope_of(size: int) -> tuple[str, ...]:
    """A question's worth of paths, distinct and in a stable order."""
    return tuple(f"src/module_{index:03d}.py" for index in range(size))


def terms_of(search: str) -> list[str]:
    """The markers one search carried, read as the branch writes them: quoted
    runs joined by OR, or the whole text where a search carries no quotes."""
    quoted = re.findall(r'"([^"]*)"', search)
    if quoted:
        return [term for term in quoted if term]
    return [search] if search else []


def paths_in(searches: list[str]) -> list[str]:
    """Which paths a list of searches asked about, with the marker removed.

    Sorted rather than deduplicated, so a path asked about twice is visible
    here as two entries rather than folded into one.
    """
    return sorted(term[len(PATH_MARKER):]
                  for one in searches for term in terms_of(one)
                  if term.startswith(PATH_MARKER))


class Searches:
    """Every search one question cost, held apart from the searches the filings
    before it cost.

    The stub records every search in one ledger, and a filing searches too — so
    what a scope cost is the slice of that list its question wrote: marked when
    the question is put and closed when it is answered. Both ends are needed,
    not just the first — a test that asks the same tracker a second question
    would otherwise read the second question's searches into the first
    question's bill.
    """

    def __init__(self, ledger: Path):
        self.ledger = ledger
        self.mark = len(ledger_state(ledger)["searches"])
        self.end: int | None = None

    def close(self) -> "Searches":
        """Fix the far end of the slice, at the moment its question answered."""
        self.end = len(ledger_state(self.ledger)["searches"])
        return self

    @property
    def made(self) -> list[str]:
        return ledger_state(self.ledger)["searches"][self.mark:self.end]

    @property
    def batched(self) -> list[str]:
        """The searches carrying more than one marker."""
        return [one for one in self.made if len(terms_of(one)) > 1]

    @property
    def per_path(self) -> list[str]:
        """The searches carrying exactly one marker, which is what a fallback
        and what the loop this story replaced both make."""
        return [one for one in self.made if len(terms_of(one)) == 1]


@contextlib.contextmanager
def stubbed(environment: dict, **overrides):
    """The stub tracker on `PATH` for the duration, plus whatever the test sets.

    The query is put through `filed_query.query`, which launches the command
    with the process environment it is called in — so the stub is reached by
    setting that environment rather than by handing one over.
    """
    previous = dict(os.environ)
    os.environ.update({name: environment[name]
                       for name in ("PATH", LEDGER_VARIABLE)})
    os.environ.update({name: str(value) for name, value in overrides.items()})
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def answer_for(tmp_path: Path, environment: dict, paths, **overrides):
    """One question put to the shipped query branch, and what it cost.

    Returns `(answer, searches)`. The bound is deliberately not overridden:
    every question here is answered under the harness's own default, which is
    the bound the branch used to be killed at.
    """
    counted = Searches(Path(environment[LEDGER_VARIABLE]))
    with stubbed(environment, **overrides):
        answer = asked(reference_script(QUERY_JOB), tmp_path, paths=paths)
    return answer, counted.close()


def reported(answer) -> list[tuple]:
    """What an answer said, item by item, in a form two answers compare in."""
    return [(item.key, item.title, tuple(item.paths)) for item in answer.items]


def file_against(tmp_path: Path, environment: dict, key: str, paths) -> str:
    """One brief filed by the shipped sync branch, carrying `paths`.

    Filed through the sync branch rather than written into the stub's ledger,
    so what the query branch searches for is what the sync branch wrote — the
    one marker declaration both of them refer to.
    """
    return file_through_the_reference_sync(
        tmp_path, environment, key=key,
        payload={"title": f"the finding filed under {key}",
                 "body": "what it says", "paths": list(paths)})


# ==========================================================================
# How many searches a scope costs
# ==========================================================================


@needs_jq
def test_a_scope_costs_one_search_per_batch_rather_than_one_per_path(tmp_path):
    """The criterion the story turns on, counted at the tracker.

    The scope is deliberately not a multiple of the batch size, so the last
    short batch is one search of its own rather than something the arithmetic
    happens to absorb.
    """
    environment, ledger = stub_tracker(tmp_path)
    paths = scope_of(BATCH * 2 + 1)

    answer, searches = answer_for(tmp_path, environment, paths)

    assert answer.answered is True, answer.reason
    assert len(searches.made) == math.ceil(len(paths) / BATCH)
    assert len(searches.made) < len(paths)
    # Every path was asked about exactly once, and no search carried more than
    # one batch: the count above is the whole scope in fewer searches rather
    # than part of the scope in the right number of them.
    assert max(len(terms_of(one)) for one in searches.made) <= BATCH
    assert paths_in(searches.made) == sorted(paths)


@needs_jq
def test_the_scope_that_did_not_answer_is_answered_under_the_default_bound(
        tmp_path):
    """The failure this story exists against, driven at the size it happened at.

    Sixty paths were sixty searches and the command was killed at the harness's
    default bound partway through. The bound is not overridden here — the
    question is put through `filed_query.query` exactly as an inspection puts
    it — so an answer at all is an answer inside it, and the search count
    beside it is what makes that a property of the branch rather than of this
    machine's speed.
    """
    environment, _ledger = stub_tracker(tmp_path)
    paths = scope_of(THE_SCOPE_THAT_DID_NOT_ANSWER)

    answer, searches = answer_for(tmp_path, environment, paths)

    assert answer.answered is True, answer.reason
    assert len(searches.made) == math.ceil(len(paths) / BATCH)
    assert len(searches.made) < len(paths)
    assert paths_in(searches.made) == sorted(paths)


# ==========================================================================
# What the batched answer says
# ==========================================================================


@needs_jq
def test_the_batched_answer_is_the_answer_the_per_path_loop_gave(tmp_path):
    """The same tracker state, asked twice: as the branch now searches, and
    with the batch size set to one — which is the loop this story replaced.

    The two must agree in every item, every key, every title and every path
    attribution, and must differ in what they cost. One of the filed briefs
    carries markers in two different batches on purpose: two searches return
    it, so a union that stopped deduplicating by url would report it twice
    here and nowhere else.
    """
    environment, _ledger = stub_tracker(tmp_path)
    paths = scope_of(BATCH * 2 + 1)
    spanning = file_against(tmp_path, environment, "k-across-two-batches",
                            [paths[0], paths[BATCH]])
    inside = file_against(tmp_path, environment, "k-inside-one-batch",
                          [paths[BATCH + 1]])

    batched, batched_cost = answer_for(tmp_path, environment, paths)
    per_path, per_path_cost = answer_for(tmp_path, environment, paths,
                                         **{BATCH_VARIABLE: 1})

    assert batched.answered is per_path.answered is True, batched.reason
    assert reported(batched) == reported(per_path)
    assert {item.key for item in batched.items} == {spanning, inside}
    assert len(batched.items) == len({item.key for item in batched.items}) == 2
    # The item whose markers span two batches keeps both of its paths and is
    # reported once, which is the union deduplicating rather than concatenating.
    across = [item for item in batched.items if item.key == spanning][0]
    assert set(across.paths) == {paths[0], paths[BATCH]}

    # The same answer, at a different price: what changed is the number of
    # searches and nothing the answer says.
    assert len(per_path_cost.made) == len(paths)
    assert len(batched_cost.made) == math.ceil(len(paths) / BATCH)


# ==========================================================================
# The two answers a batch's search cannot be trusted for
# ==========================================================================


@needs_jq
def test_a_batch_whose_search_failed_is_re_asked_one_path_at_a_time(tmp_path):
    """A tracker that answers a per-path search and refuses a batched one.

    That is the tracker the fallback exists for — one whose limit on how long a
    query may be is tighter than this one's — and against it the whole question
    is still answered, with the item that was filed in it.
    """
    environment, _ledger = stub_tracker(tmp_path)
    paths = scope_of(BATCH + 2)
    filed = file_against(tmp_path, environment, "k-1", [paths[1]])

    answer, searches = answer_for(tmp_path, environment, paths,
                                  **{FAIL_VARIABLE: BATCHED_SEARCH_CALL})

    assert answer.answered is True, answer.reason
    assert [item.key for item in answer.items] == [filed]
    assert set(answer.items[0].paths) == {paths[1]}
    # Every batch was tried and every batch's paths were then asked about one
    # at a time, which is the fallback rather than a batch quietly skipped.
    assert len(searches.batched) == math.ceil(len(paths) / BATCH)
    assert paths_in(searches.per_path) == sorted(paths)


@needs_jq
def test_a_per_path_search_that_fails_after_the_fallback_fails_the_answer(
        tmp_path):
    """What is filed is reported as not known rather than as nothing filed.

    Driven twice over: at the script, where the exit status is non-zero and the
    words name the path that could not be searched for, and through the
    harness, which reads that as knowing nothing.
    """
    environment, _ledger = stub_tracker(tmp_path)
    paths = scope_of(BATCH + 2)
    broken = {**environment, FAIL_VARIABLE: ISSUE_LIST_CALL}

    result = subprocess.run(
        [INTERPRETER, str(TEMPLATE_SCRIPT), QUERY_JOB],
        input=json.dumps({"paths": list(paths)}),
        capture_output=True, text=True, timeout=120, cwd=tmp_path,
        env=broken)

    assert result.returncode != 0, result.stdout
    assert any(path in result.stderr for path in paths), result.stderr

    with stubbed(environment, **{FAIL_VARIABLE: ISSUE_LIST_CALL}):
        answer = asked(reference_script(QUERY_JOB), tmp_path, paths=paths)
    assert knows_nothing(answer)


@needs_jq
def test_a_batch_whose_page_filled_to_the_limit_is_re_asked_one_path_at_a_time(
        tmp_path):
    """A filled page is a page the tracker may have truncated, and a truncated
    page read as complete is a duplicate filed.

    The two halves are the same scope against the same tracker state, and the
    only thing that differs is the limit one search is capped at. Under the
    default limit the page is complete, one search answers and no per-path
    search is made — which is the control for the absence. Under a limit
    smaller than the number of briefs filed against the batch, the page comes
    back full, the batch is re-asked path by path, and the answer carries the
    brief the truncated page had cut off.
    """
    environment, _ledger = stub_tracker(tmp_path)
    paths = scope_of(4)
    filed = [file_against(tmp_path, environment, f"k-{index}", [path])
             for index, path in enumerate(paths)]
    a_limit_the_page_fills = len(paths) - 1
    assert a_limit_the_page_fills < len(filed) <= LIMIT

    complete, unfilled_cost = answer_for(tmp_path, environment, paths)

    assert complete.answered is True, complete.reason
    assert {item.key for item in complete.items} == set(filed)
    assert len(unfilled_cost.made) == 1
    assert unfilled_cost.per_path == []

    truncated, filled_cost = answer_for(
        tmp_path, environment, paths,
        **{LIMIT_VARIABLE: a_limit_the_page_fills})

    assert truncated.answered is True, truncated.reason
    # The whole answer, including the brief the filled page did not carry: the
    # batched search returned three of the four, so an answer carrying four is
    # the per-path searches having been made and read.
    assert {item.key for item in truncated.items} == set(filed)
    assert len(truncated.items) == len(filed)
    assert len(filled_cost.batched) == 1
    assert paths_in(filled_cost.per_path) == sorted(paths)


# ==========================================================================
# The two shipped copies, and where the batching is allowed to reach
# ==========================================================================


def query_branch(text: str) -> str:
    """The query job's whole branch: its entry point and the batch helper."""
    return "\n".join(branch_source(text, name) for name in QUERY_BRANCHES)


def test_both_shipped_copies_carry_the_same_query_branch():
    """Both are shipped artifacts and both are the subject.

    This repository's installed copy is what its own runs ask, so a batching
    that reached only the template would leave the failure this story exists
    against in place here. What each copy keeps is its own constants: the
    comparison is of the branch, and the assertion that the two files differ
    nowhere else is `test_filed_query.py`'s.
    """
    template = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    installed = INSTALLED_SCRIPT.read_text(encoding="utf-8")

    assert query_branch(installed) == query_branch(template)
    # The constants live above the branches and are each copy's own, so the
    # batch size is declared in both rather than shared through the branch.
    for text in (template, installed):
        for name in QUERY_CONSTANTS:
            assert f"{name}=" in text, name


def test_that_comparison_reports_a_query_branch_that_drifted(tmp_path):
    """The control: a rendering of the template whose query branch differs by a
    line of mechanics, which the same comparison must report.

    Rendered here rather than written to the tree, so the control is about the
    comparison and not about this repository.
    """
    template = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    drifted = template.replace("query_batch\n", "query_batch # drifted\n", 1)
    assert drifted != template

    assert query_branch(drifted) != query_branch(template)
    assert any("drifted" in line for line in
               lines_that_differ(query_branch(template), query_branch(drifted)))


def branches_naming(text: str, names) -> list[str]:
    """Which of `names` each branch outside the query job refers to."""
    return [f"{branch}:{name}"
            for branch in OTHER_BRANCHES
            for name in names
            if name in branch_source(text, branch)]


def test_the_batching_is_the_query_jobs_alone():
    """The sync and the item branch make no per-path search and were left
    alone, which is what stops this story reaching a branch it has no business
    in. Asserted as the query job's own constants being named in the query job
    and nowhere else."""
    template = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    installed = INSTALLED_SCRIPT.read_text(encoding="utf-8")
    assert QUERY_CONSTANTS, "the query job declares no constants of its own"

    for text in (template, installed):
        assert branches_naming(text, QUERY_CONSTANTS) == []
        for name in QUERY_CONSTANTS:
            assert name in query_branch(text), name


def test_that_scan_reports_a_query_constant_reaching_another_branch(tmp_path):
    """The control: a rendering of the template whose sync branch reads the
    batch size, which the same scan must report."""
    template = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    planted = template.replace(
        "\ndo_sync() {\n",
        f'\ndo_sync() {{\n  echo "${{{QUERY_CONSTANTS[0]}}}" >&2\n', 1)
    assert planted != template

    reported_here = branches_naming(planted, QUERY_CONSTANTS)
    assert reported_here, "the scan sees no branch it should report"
    assert any(one.startswith("do_sync:") for one in reported_here), \
        reported_here
