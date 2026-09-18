"""story-160 validation: the dedupe query answers for a change of any size
against a tracker of any size.

Story-131 stopped the number of searches being proportional to the scope, and
`tests/test_a_filed_query_scales_with_its_scope.py` holds that: a scope costs
one search per batch, a batch whose page fills or whose search fails is re-asked
one path at a time. What returned by a different road is the cost of that
fallback on a tracker that has held every brief of a long-running repository:
a batch whose page fills at the old limit of 50 is re-asked once per path, so
the fallback multiplies the batch's cost by its size and a 60-file scope in
which two or three batches fill runs past the harness's bound. And the per-path
search the fallback makes had no page-fill check of its own, so a single path
with more briefs against it than a page holds was answered short and reported
as whole.

This story raises the page limit to a size the fallback is not reached at,
gives the per-path search the page-fill check the batched one had, and declares
an item cap in this repository's configuration the whole answer fits under. The
subjects, kept apart:

  * **a scope answered without the fallback.** Sixty paths against a stub
    tracker where every batch holds more briefs than the old page of 50 held
    and fewer than the new default: counted at the tracker, it costs one
    batched search per batch and no per-path search, is answered under the
    harness's default bound, and carries every brief filed against the scope
    with its per-item path attribution. The seeded count is asserted against
    the old page size, so a stub that seeded too few to have filled the old
    page proves nothing and says so.

  * **a per-path page that fills is refused.** There is nothing narrower than
    one path to re-ask, so a per-path page holding the limit is a page the
    tracker may have truncated with no way to find out. Driven at the script,
    where the exit is non-zero and stderr names the path and the limit, and
    through the harness, which reads it as nothing known. Its discriminating
    control is a limit the batched page fills and no per-path page does, where
    the fallback runs and answers whole.

  * **the item cap this repository declares.** `.harness/config.yaml` is a
    shipped artifact and the subject: it declares `filed_query_max_items`,
    `resolve_settings` reads it as the cap, and a question answered with more
    items than the source default carries them all under the configured cap
    and drops the excess under the default, saying so.

  * **what is deliberately unchanged.** The source defaults for the item cap
    and the bound, the absence of a declared bound in this repository's
    configuration, and the page limit now shipped in both copies.

  * **what the comments now say.** The comment above the page limit, the query
    contract's two batching passages and the query module's docstring carry
    this story's measurement and no longer describe the limit as merely a
    bound on what the tracker is asked for.

Every absence asserted here carries a demonstration that it can fail:

  * "no per-path search was made" against the seeded tracker sits beside the
    same scope under a forced limit its batched pages fill, where the per-path
    searches are made and counted — and where the answer is the same items,
    which is the fallback and the batched search agreeing at this size;
  * "nothing was dropped from the answer" under the configured cap sits beside
    the same answer under the source default, where the excess is dropped and
    named;
  * "the old description of the limit is gone" sits beside a rendering of the
    template that puts it back, which the same scan reports.

Nothing here reaches a network or a model: the shipped script is run as it
ships against the stub `gh` `tests/test_filed_query.py` wrote, first on `PATH`,
and every question through the harness goes through `filed_query.query` under
the harness's own default bound.
"""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

import filed_query
import harness_config

from test_a_filed_query_scales_with_its_scope import (  # noqa: F401
    BATCH,
    BATCH_VARIABLE,
    LIMIT,
    LIMIT_VARIABLE,
    QUERY_BRANCHES,
    Searches,
    THE_SCOPE_THAT_DID_NOT_ANSWER,
    answer_for,
    file_against,
    paths_in,
    reported,
    scope_of,
    stubbed,
)
from test_filed_query import (  # noqa: F401 - shared idioms and fixtures
    DEFAULT_MAX_ITEMS,
    DEFAULT_TIMEOUT_SECONDS,
    INSTALLED_CONSTANTS,
    INSTALLED_SCRIPT,
    INTERPRETER,
    LEDGER_VARIABLE,
    MAX_ITEMS_KEY,
    QUERY_JOB,
    REPO_ROOT,
    TEMPLATE_CONSTANTS,
    TEMPLATE_SCRIPT,
    TIMEOUT_KEY,
    asked,
    branch_source,
    knows_nothing,
    needs_jq,
    reference_script,
    stub_tracker,
)

#: The page one search was capped at before this story, which is the size the
#: measured batch filled: a batch carrying the coordinator's path matched 106
#: of the tracker's 198 issues on 2026-09-17, and one path alone matched 75.
#: A measurement rather than a value read off the tree, because the tree no
#: longer carries it — it is what the seeded tracker below must exceed to have
#: reproduced the failure.
THE_PAGE_THAT_FILLED = 50

#: The page limit this story ships, in both copies. A shipped artifact and the
#: subject of the assertion that reads it, so stated rather than derived.
THE_PAGE_LIMIT_NOW_SHIPPED = 500

#: The item cap this repository declares for its own tracker, which is the one
#: value in `.harness/config.yaml` this story adds. Stated because the file is
#: the subject: the assertion is that this repository declares this.
THIS_REPOSITORYS_ITEM_CAP = 200

#: The headings of the query contract's two batching passages, and the heading
#: of the passage that follows them, which bound the text this story rewrote.
FIRST_BATCHING_PASSAGE = "ONE SEARCH PER PATH DOES NOT SCALE"
SECOND_BATCHING_PASSAGE = "BATCHING IS ONLY SAFE WITH A FALLBACK"
THE_PASSAGE_AFTER_THEM = "THE QUERY BRANCH IS THE SYNC BRANCH'S PAIR"

#: The sentence the old comment above the limit ended on, which described the
#: limit as a bound on what the tracker is asked for and nothing more. What is
#: asserted is that it is gone from both copies.
THE_OLD_DESCRIPTION_OF_THE_LIMIT = \
    "this bound is about what the tracker is asked for"

#: The figures the description gives, which the rewritten prose is required to
#: carry: the per-path cost of the fallback, the batched cost at the raised
#: limit, and the tracker it was measured against.
THE_FALLBACKS_COST_PER_PATH = "0.6 to 0.9"
THE_BATCHED_COST_AT_THE_RAISED_LIMIT = "2.9"
THE_TRACKER_MEASURED = "198"


def constant_default(constants: dict, name: str) -> int:
    """One shipped copy's default for `name`, as the number it is."""
    _variable, default = constants[name]
    return int(default)


def answer_under(tmp_path: Path, environment: dict, paths, *,
                 config: dict | None = None, **overrides):
    """`answer_for`, with a configuration handed to the query as well.

    The item cap is a configuration key `filed_query.query` reads out of the
    target's config, not a variable the script reads out of its environment —
    so it goes in the config dict `asked` builds, while a forced page limit
    still goes to the process environment the script is launched with. The
    searches are counted on the same ledger `answer_for` counts on.
    """
    counted = Searches(Path(environment[LEDGER_VARIABLE]))
    with stubbed(environment, **overrides):
        answer = asked(reference_script(QUERY_JOB), tmp_path, paths=paths,
                       **(config or {}))
    return answer, counted.close()


# ==========================================================================
# The seeded tracker: more briefs against every batch than the old page held
# ==========================================================================


def seed_every_batch_past_the_old_page(tmp_path: Path, environment: dict,
                                       paths) -> dict[str, tuple[str, ...]]:
    """Briefs filed so that every batch of `paths` matches more than the old
    page of 50 held, and fewer than the new default.

    Each spanning brief carries one path from every batch, so one filing counts
    against every batch's search at once and a brief's markers spanning every
    batch is exactly what a union that stopped deduplicating would report
    several times. A few briefs are filed against a single path beside them, so
    the per-item attribution asserted on the answer is not the same set of
    paths on every item.

    Returns each filed brief's key mapped to the paths it was filed against.
    """
    batches = math.ceil(len(paths) / BATCH)
    filed: dict[str, tuple[str, ...]] = {}
    for index in range(THE_PAGE_THAT_FILLED + 1):
        # The paths rotate through each batch, so the briefs against one batch
        # are spread across its paths rather than stacked on one of them: no
        # single path holds more than a page, which is the property that keeps
        # the forced-limit control below in the fallback rather than refused.
        carrying = tuple(
            paths[batch * BATCH + (index % BATCH)]
            for batch in range(batches)
            if batch * BATCH + (index % BATCH) < len(paths))
        filed[file_against(tmp_path, environment, f"k-spanning-{index}",
                           carrying)] = carrying
    for batch in range(batches):
        single = (paths[batch * BATCH],)
        filed[file_against(tmp_path, environment, f"k-single-in-{batch}",
                           single)] = single
    return filed


def briefs_against_each_batch(filed: dict[str, tuple[str, ...]],
                              paths) -> list[int]:
    """How many filed briefs each batch's search matches, by batch."""
    return [
        sum(1 for carrying in filed.values()
            if any(path in carrying for path in paths[start:start + BATCH]))
        for start in range(0, len(paths), BATCH)]


@needs_jq
def test_a_scope_whose_every_batch_outgrew_the_old_page_answers_without_the_fallback(
        tmp_path):
    """The criterion the story turns on, counted at the tracker.

    Sixty paths, every batch holding more briefs than the old page of 50 held:
    at the old limit every batched page filled and every batch was re-asked
    once per path, which is where the bound went. Under the limit now shipped
    no page fills, the scope costs one search per batch and no per-path search,
    and the answer is whole. The bound is deliberately not overridden — the
    question is put through `filed_query.query` exactly as an inspection puts
    it — so an answer at all is an answer inside it, and the search count is
    what makes that a property of the branch rather than of this machine.
    """
    environment, _ledger = stub_tracker(tmp_path)
    paths = scope_of(THE_SCOPE_THAT_DID_NOT_ANSWER)
    filed = seed_every_batch_past_the_old_page(tmp_path, environment, paths)

    # The precondition the test exists on: every batch outgrew the old page and
    # no batch reaches the new one. Asserted rather than assumed, so a seeding
    # that fell short would fail here rather than pass the assertions below on
    # a tracker that never reproduced the failure.
    per_batch = briefs_against_each_batch(filed, paths)
    assert len(per_batch) == math.ceil(len(paths) / BATCH)
    assert all(THE_PAGE_THAT_FILLED < count < LIMIT for count in per_batch), \
        per_batch

    answer, searches = answer_under(tmp_path, environment, paths,
                                    config={MAX_ITEMS_KEY: len(filed)})

    assert answer.answered is True, answer.reason
    assert len(searches.made) == math.ceil(len(paths) / BATCH)
    assert searches.per_path == []
    assert paths_in(searches.batched) == sorted(paths)

    # Every brief, once, with the paths it was filed against — the spanning
    # briefs came back from every batch's search and are reported once each.
    assert {item.key: tuple(sorted(item.paths)) for item in answer.items} == \
        {key: tuple(sorted(carrying)) for key, carrying in filed.items()}
    assert len(answer.items) == len(filed)
    assert answer.excluded == ()


@needs_jq
def test_the_same_scope_under_a_limit_its_pages_fill_is_re_asked_and_agrees(
        tmp_path):
    """The control for the absence above, and story-131's property at this
    size: under a limit the batched pages fill, every batch is re-asked one
    path at a time, the per-path searches are made and counted, and the
    fallback's answer is the same items as the batched one.

    The forced limit is above what any single path holds, so it is the batched
    page alone that fills; the per-path refusal is the next section's subject.
    """
    environment, _ledger = stub_tracker(tmp_path)
    paths = scope_of(THE_SCOPE_THAT_DID_NOT_ANSWER)
    filed = seed_every_batch_past_the_old_page(tmp_path, environment, paths)
    most_against_one_path = max(
        sum(1 for carrying in filed.values() if path in carrying)
        for path in paths)
    a_limit_the_batched_pages_fill = most_against_one_path + 1
    assert a_limit_the_batched_pages_fill < min(
        briefs_against_each_batch(filed, paths))

    whole, unfilled_cost = answer_under(tmp_path, environment, paths,
                                        config={MAX_ITEMS_KEY: len(filed)})
    fallen_back, filled_cost = answer_under(
        tmp_path, environment, paths,
        config={MAX_ITEMS_KEY: len(filed)},
        **{LIMIT_VARIABLE: a_limit_the_batched_pages_fill})

    assert whole.answered is fallen_back.answered is True, fallen_back.reason
    assert unfilled_cost.per_path == []
    assert len(filled_cost.batched) == math.ceil(len(paths) / BATCH)
    assert paths_in(filled_cost.per_path) == sorted(paths)
    assert sorted(reported(fallen_back)) == sorted(reported(whole))
    assert len(fallen_back.items) == len(filed)


# ==========================================================================
# A per-path page that fills is refused rather than answered short
# ==========================================================================


def a_path_holding_more_than_a_page(tmp_path: Path, environment: dict,
                                    paths, *, briefs: int) -> tuple[str, list]:
    """One path with `briefs` filed against it, inside a scope of `paths`."""
    hot = paths[1]
    filed = [file_against(tmp_path, environment, f"k-hot-{index}", [hot])
             for index in range(briefs)]
    return hot, filed


@needs_jq
def test_a_per_path_page_that_fills_fails_the_whole_answer_at_the_script(
        tmp_path):
    """Driven at the script: the exit status is non-zero and the words name
    the path whose page filled and the limit it filled."""
    environment, _ledger = stub_tracker(tmp_path)
    paths = scope_of(BATCH + 2)
    hot, filed = a_path_holding_more_than_a_page(tmp_path, environment, paths,
                                                 briefs=3)
    a_limit_one_path_fills = len(filed) - 1

    result = subprocess.run(
        [INTERPRETER, str(TEMPLATE_SCRIPT), QUERY_JOB],
        input=json.dumps({"paths": list(paths)}),
        capture_output=True, text=True, timeout=120, cwd=tmp_path,
        env={**environment, LIMIT_VARIABLE: str(a_limit_one_path_fills)})

    assert result.returncode != 0, result.stdout
    assert hot in result.stderr, result.stderr
    assert str(a_limit_one_path_fills) in result.stderr, result.stderr


@needs_jq
def test_a_per_path_page_that_fills_is_read_as_nothing_known(tmp_path):
    """Driven through the harness: with the limit forced below the number of
    briefs against one path, a scope containing that path is answered as
    nothing known rather than as an answer carrying the truncated page; the
    same scope under the default limit is answered with every brief."""
    environment, _ledger = stub_tracker(tmp_path)
    paths = scope_of(BATCH + 2)
    hot, filed = a_path_holding_more_than_a_page(tmp_path, environment, paths,
                                                 briefs=3)
    a_limit_one_path_fills = len(filed) - 1
    assert a_limit_one_path_fills < len(filed) <= LIMIT

    refused, refused_cost = answer_for(
        tmp_path, environment, paths,
        **{LIMIT_VARIABLE: a_limit_one_path_fills})

    assert knows_nothing(refused)
    assert hot in refused.reason, refused.reason
    # The batched page filled first and the fallback ran: the refusal came
    # from a per-path search, not from the batched one being read short.
    assert len(refused_cost.batched) >= 1
    assert hot in paths_in(refused_cost.per_path)

    whole, whole_cost = answer_for(tmp_path, environment, paths)

    assert whole.answered is True, whole.reason
    assert {item.key for item in whole.items} == set(filed)
    assert all(tuple(item.paths) == (hot,) for item in whole.items)
    assert whole_cost.per_path == []


@needs_jq
def test_a_limit_only_the_batched_page_fills_still_falls_back_and_answers(
        tmp_path):
    """The discriminating control: the refusal is for a per-path page that
    fills, not for a batched page that fills.

    Several briefs on one path and one each on two others in the same batch: a
    limit one short of their total, and above what the hot path holds, fills
    the batched page and no per-path page, so the batch is re-asked one path
    at a time and answered whole — the fallback story-131 added, still running
    beneath the refusal this story added.
    """
    environment, _ledger = stub_tracker(tmp_path)
    paths = scope_of(BATCH)
    hot, on_hot = a_path_holding_more_than_a_page(tmp_path, environment, paths,
                                                  briefs=3)
    beside = [file_against(tmp_path, environment, f"k-beside-{index}", [path])
              for index, path in enumerate((paths[2], paths[3]))]
    filed = set(on_hot) | set(beside)
    a_limit_only_the_batch_fills = len(filed) - 1
    assert len(on_hot) < a_limit_only_the_batch_fills < len(filed)

    answer, cost = answer_for(
        tmp_path, environment, paths,
        **{LIMIT_VARIABLE: a_limit_only_the_batch_fills})

    assert answer.answered is True, answer.reason
    assert {item.key for item in answer.items} == filed
    assert len(cost.batched) == 1
    assert paths_in(cost.per_path) == sorted(paths)


# ==========================================================================
# The item cap this repository declares, and what it did not raise
# ==========================================================================


def this_repositorys_config() -> dict:
    return harness_config.load_config(REPO_ROOT)


def test_this_repository_declares_an_item_cap_the_settings_read():
    """A shipped artifact and the subject: the one key this story adds to
    `.harness/config.yaml`, read by the harness's own resolution rather than
    off the file alone."""
    config = this_repositorys_config()
    assert MAX_ITEMS_KEY in config, MAX_ITEMS_KEY

    settings, problem = filed_query.resolve_settings(config)
    assert problem == ""
    assert settings is not None
    assert settings.max_items == THIS_REPOSITORYS_ITEM_CAP
    assert settings.max_items > DEFAULT_MAX_ITEMS


def test_the_bound_is_not_raised():
    """What the story leaves alone, asserted so that a raise smuggled in beside
    the cap is a red assertion rather than a quiet change: the source defaults
    stand, and this repository declares no bound of its own."""
    config = this_repositorys_config()
    assert TIMEOUT_KEY not in config, TIMEOUT_KEY

    settings, _problem = filed_query.resolve_settings(config)
    assert settings.timeout == DEFAULT_TIMEOUT_SECONDS
    assert DEFAULT_TIMEOUT_SECONDS == 30.0
    assert DEFAULT_MAX_ITEMS == 50


@needs_jq
def test_the_configured_cap_is_what_the_query_obeys_rather_than_the_default(
        tmp_path):
    """A question answered with more items than the source default: under the
    cap this repository declares every item is carried and nothing is dropped;
    under the default the excess is dropped and the drop is named — which is
    the control for the empty `excluded` above, and the item the inspector
    never dedupes against."""
    environment, _ledger = stub_tracker(tmp_path)
    paths = scope_of(BATCH)
    filed = {file_against(tmp_path, environment, f"k-{index}",
                          [paths[index % len(paths)]])
             for index in range(DEFAULT_MAX_ITEMS + 1)}
    settings, _problem = filed_query.resolve_settings(this_repositorys_config())
    assert DEFAULT_MAX_ITEMS < len(filed) <= settings.max_items

    under_the_configured_cap, _ = answer_under(
        tmp_path, environment, paths,
        config={MAX_ITEMS_KEY: settings.max_items})
    under_the_default, _ = answer_for(tmp_path, environment, paths)

    assert under_the_configured_cap.answered is True, \
        under_the_configured_cap.reason
    assert {item.key for item in under_the_configured_cap.items} == filed
    assert under_the_configured_cap.excluded == ()

    assert under_the_default.answered is True, under_the_default.reason
    assert len(under_the_default.items) == DEFAULT_MAX_ITEMS
    assert {item.key for item in under_the_default.items} < filed
    stated = " ".join(under_the_default.excluded)
    assert MAX_ITEMS_KEY in stated, stated
    assert str(len(filed) - DEFAULT_MAX_ITEMS) in stated, stated


# ==========================================================================
# The page limit both copies ship
# ==========================================================================


def test_both_copies_ship_the_raised_page_limit():
    """Both are shipped artifacts and both are the subject: the default one
    search is capped at is the raised one in the template and in this
    repository's installed copy, and it is above what the measured batch
    filled, so the fallback is not reached for any batch that tracker holds."""
    for constants in (TEMPLATE_CONSTANTS, INSTALLED_CONSTANTS):
        assert constant_default(constants, "LIMIT") == THE_PAGE_LIMIT_NOW_SHIPPED
    assert LIMIT == THE_PAGE_LIMIT_NOW_SHIPPED
    assert LIMIT > THE_PAGE_THAT_FILLED
    # The default limit can still be driven through its variable, which is how
    # every forced-limit test above reaches the fallback and the refusal.
    assert LIMIT_VARIABLE == TEMPLATE_CONSTANTS["LIMIT"][0]


def test_the_per_path_page_fill_check_is_in_the_query_branch_of_both_copies():
    """The refusal is the query branch's and is the same text in both copies:
    the batch helper counts the per-path page and names the limit in its
    refusal, in the template and in the installed copy alike."""
    for script in (TEMPLATE_SCRIPT, INSTALLED_SCRIPT):
        text = script.read_text(encoding="utf-8")
        helper = branch_source(text, QUERY_BRANCHES[-1])
        # Two page counts in the helper — the batched search's and, after this
        # story, the per-path search's — rather than one.
        assert helper.count("jq 'length'") >= 2, script
        assert "${LIMIT}" in helper or '"$LIMIT"' in helper, script


# ==========================================================================
# What the comments now say
# ==========================================================================


def comment_above(text: str, assignment: str) -> str:
    """The run of comment lines immediately above an assignment line."""
    lines = text.splitlines()
    at = next(index for index, line in enumerate(lines)
              if line.startswith(assignment))
    above: list[str] = []
    while at > 0 and lines[at - 1].startswith("#"):
        at -= 1
        above.insert(0, lines[at])
    return "\n".join(above)


def batching_passages(text: str) -> str:
    """The query contract's two batching passages, from the first heading to
    the heading of the passage after them."""
    start = text.index(FIRST_BATCHING_PASSAGE)
    end = text.index(THE_PASSAGE_AFTER_THEM, start)
    passages = text[start:end]
    assert SECOND_BATCHING_PASSAGE in passages
    return passages


def limit_assignment(constants: dict) -> str:
    variable, _default = constants["LIMIT"]
    return f'LIMIT="${{{variable}:-'


def test_the_limits_comment_says_it_decides_whether_the_fallback_runs():
    """The comment above the page limit no longer describes it as merely a
    bound on what the tracker is asked for: it says the limit decides whether
    a batch reaches the fallback, and what the fallback costs a path."""
    for script, constants in ((TEMPLATE_SCRIPT, TEMPLATE_CONSTANTS),
                              (INSTALLED_SCRIPT, INSTALLED_CONSTANTS)):
        comment = comment_above(script.read_text(encoding="utf-8"),
                                limit_assignment(constants))
        assert "fallback" in comment, script
        assert THE_FALLBACKS_COST_PER_PATH in comment, script
        assert THE_OLD_DESCRIPTION_OF_THE_LIMIT not in comment, script


def test_that_scan_reports_the_old_description_put_back():
    """The control: a rendering of the template whose comment above the limit
    carries the old sentence again, which the same scan must report."""
    template = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    assignment = limit_assignment(TEMPLATE_CONSTANTS)
    restored = template.replace(
        assignment, f"# {THE_OLD_DESCRIPTION_OF_THE_LIMIT}.\n{assignment}", 1)
    assert restored != template

    assert THE_OLD_DESCRIPTION_OF_THE_LIMIT not in comment_above(template,
                                                                 assignment)
    assert THE_OLD_DESCRIPTION_OF_THE_LIMIT in comment_above(restored,
                                                             assignment)


def test_the_batching_passages_carry_this_storys_measurement_beside_story_131s():
    """The query contract's two batching passages, in both copies, carry the
    measurement in the terms the story gives: the fallback's cost per path,
    the batched cost at the raised limit, and the tracker it was measured
    against — beside story-131's own figures, which are still there."""
    for script in (TEMPLATE_SCRIPT, INSTALLED_SCRIPT):
        passages = batching_passages(script.read_text(encoding="utf-8"))
        assert THE_FALLBACKS_COST_PER_PATH in passages, script
        assert THE_BATCHED_COST_AT_THE_RAISED_LIMIT in passages, script
        assert THE_TRACKER_MEASURED in passages, script
        # Story-131's measurement is still recorded beside this one.
        assert "0.83" in passages, script
    template = batching_passages(TEMPLATE_SCRIPT.read_text(encoding="utf-8"))
    installed = batching_passages(INSTALLED_SCRIPT.read_text(encoding="utf-8"))
    assert template == installed


def test_the_query_modules_docstring_says_the_page_size_bounds_the_cost():
    """The module docstring's passage on how the command scales says the
    batched query is bounded by its page size rather than by the scope, and
    that a target whose answers run past the default declares the cap; the
    comment above the default says the default is not a statement about any
    tracker."""
    docstring = filed_query.__doc__
    assert THE_BATCHED_COST_AT_THE_RAISED_LIMIT in docstring
    assert THE_FALLBACKS_COST_PER_PATH in docstring
    assert MAX_ITEMS_KEY in docstring
    assert "page" in docstring

    source = Path(filed_query.__file__).read_text(encoding="utf-8")
    comment = comment_above(source, "DEFAULT_MAX_ITEMS = ")
    assert MAX_ITEMS_KEY in comment
    assert "meant to be small" not in comment
    assert "not small" in comment
