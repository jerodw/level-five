"""Independent validation for story-211: a board read that failed on a dropped
connection is tried again before the update is given up.

When a story finishes, the harness asks the tracker to move that story's item
into its ready-to-merge column, and before it can do that it has to read the
board. A single failed read used to abandon the whole update, which is the
right answer when the board is genuinely misconfigured and the wrong one when
the connection simply dropped — the two look identical from outside. The
tracker command now asks a refused read again a small number of times before
giving up, and asks a refused write exactly once.

What is asserted here is behaviour, driven at the shipped script against the
stub `gh` `tests/test_filed_query.py` wrote. A retry is observed by what the
tracker was asked for and what it answered, which the stub records in its
ledger; nothing here measures how long anything took, and the wait between
attempts is zero in every drive this stub stands behind.

The subjects are kept apart deliberately:

  * **a read refused once, on each of the three jobs.** The sync branch's read
    of one item's field values, the query branch's read of a brief, and the
    item branch's read of the URL a board takes an item by. Each is refused
    once and answers on the attempt after, and each drive must then complete
    the work it was part of — the entry lands in its column, the brief comes
    back whole, the item moves to the column the status names.

  * **a read refused on every attempt.** The same three drives against a
    tracker that never answers, compared against a rendering of the same
    script with the retry taken out: the same exit code and the same words on
    stderr, so the failure a developer reads is the one the command gave
    before this story. What differs between them is how many times the tracker
    was asked, which is what makes the comparison a comparison.

  * **a read that answers with nothing.** A project naming no id, a field list
    naming no fields and an item naming no URL are a misconfigured board
    rather than a dropped connection, and each still gives up on the first
    answer.

  * **the writes.** Creating an issue, creating a label, adding one, adding an
    item to a board and editing a board's field are each refused and each must
    be made exactly once.

  * **the two constants.** The attempt count and the wait are declared at the
    top of both copies, read from environment variables, and named nowhere but
    the helper that reads them — so no call site carries either, and a target
    that wants one attempt gets one attempt.

  * **the text the reads are written in.** The two project reads moved into
    functions of their own and are still literal `gh project <subcommand>`
    invocations, which is what the suite's own sweep of board calls is built
    by reading off the script: a subcommand moved behind a variable would
    shrink that sweep without reddening anything.

Every absence asserted here carries a demonstration that it can fail:

  * "a refused read is answered on a later attempt" sits beside the same drive
    against a rendering of the shipped script with the retry removed, on each
    of the three jobs, where the same refusal ends the drive;
  * "the dedupe search needed no fallback" sits beside the same question
    against that same rendering, where the fallback runs and every path is
    asked about one at a time;
  * "the read that answered with nothing was asked once" sits beside the same
    call refused rather than empty, in the same drive, where it is asked as
    many times as a read is given attempts;
  * "each write was asked for once" sits beside a read refused through the
    same spelling in the same ledger, which is counted at more than one — so a
    count of one is the script making one attempt rather than a ledger that
    cannot count past it;
  * "neither constant is named at a call site" sits beside a rendering of the
    template whose sync branch names one, which the same scan reports.

Nothing here reaches a network or a model.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

import item_update

from test_filed_query import (  # noqa: F401 - shared idioms and fixtures
    A_COLUMN_A_HUMAN_MOVED_IT_TO,
    BOTH_SYNC_COPIES,
    EMPTY_VARIABLE,
    FAIL_VARIABLE,
    GRAPHQL_CALL,
    INSTALLED_SCRIPT,
    INTERPRETER,
    ISSUE_CREATE_CALL,
    ISSUE_LIST_CALL,
    ISSUE_VIEW_CALL,
    LABEL_ADD_CALL,
    LABEL_CREATE_CALL,
    LEDGER_VARIABLE,
    PROJECT_SUBCOMMANDS,
    QUERY_JOB,
    READ_ATTEMPTS,
    READ_ATTEMPTS_VARIABLE,
    REFUSAL_SEPARATOR,
    RETRY_HELPER,
    SYNC_VARIABLE_PREFIX,
    TEMPLATE_SCRIPT,
    TRANSIENT_EXIT,
    a_filed_brief,
    asked,
    attempts,
    board_items,
    branch_source,
    file_through_the_reference_sync,
    fixture_file,
    named_outside,
    needs_jq,
    reference_script,
    stub_tracker,
    sync_constants,
    sync_to_the_board,
    the_column_a_filing_lands_in,
)
from test_a_filed_query_scales_with_its_scope import (  # noqa: F401
    Searches,
    answer_for,
    file_against,
    paths_in,
    scope_of,
    stubbed,
)
from test_a_planned_story_is_published_onto_its_item import (  # noqa: F401
    ARTIFACT,
    STORY,
    an_item_already_filed,
    move_through_the_reference_script,
    publish_through_the_reference_script,
)

#: The two constants the helper reads, by the names the script declares them
#: under. Written for the same reason, and resolved against each copy's own
#: text below so that neither value is stated here.
ATTEMPTS_CONSTANT = "READ_ATTEMPTS"
WAIT_CONSTANT = "READ_RETRY_SECONDS"

#: The board calls that are writes, under the names the stub records them by.
#: Listed rather than derived, because what is asserted is that each of these
#: makes exactly one attempt: a list derived from the script would shrink
#: silently with the claim, and the whole point of naming them is that leaving
#: them alone is visible rather than implied.
THE_WRITES = (
    ISSUE_CREATE_CALL,
    LABEL_CREATE_CALL,
    LABEL_ADD_CALL,
    "item-add",
    "item-edit",
)

#: The board calls the suite's own sweep is built out of, by reading them off
#: the shipped script. Written here because what is asserted is that the sweep
#: still finds them: a list derived from the same text it is compared against
#: would agree with it whatever either of them said.
THE_BOARD_SUBCOMMANDS = ("field-list", "item-add", "item-edit", "view")

#: The three reads whose answer can be empty without the call having failed,
#: and the words the script gives up with when it is. Each is reached by the
#: item branch moving an item, in the order it reads them.
THE_EMPTY_ANSWERS = (
    pytest.param(ISSUE_VIEW_CALL, "named no URL", id="the-items-url"),
    pytest.param("view", "named no id", id="the-projects-id"),
    pytest.param("field-list", "named no fields", id="the-projects-fields"),
)


# --------------------------------------------------------------------------
# Telling the stub what to do, and rendering a script with no retry in it
# --------------------------------------------------------------------------


def refused(call: str, times: int | None = None) -> dict:
    """The environment that makes the stub refuse `call`.

    With a count, it is refused that many times and answers afterwards, which
    is the connection that dropped at the moment of one call. Without one,
    every attempt is refused, which is the board that is genuinely
    misconfigured — and the two are indistinguishable from inside the script,
    which is the whole reason this story exists.
    """
    named = call if times is None else f"{call}{REFUSAL_SEPARATOR}{times}"
    return {FAIL_VARIABLE: named}


def answering_nothing(call: str) -> dict:
    """The environment that makes the stub answer `call` with no output.

    A call that exited zero and said nothing: the board was reached and had
    nothing to say. That is not a refusal and the script must not ask it
    again, because a second attempt would answer identically.
    """
    return {EMPTY_VARIABLE: call}


def without_the_retry(text: str) -> str:
    """The same script with its retry helper reduced to a single attempt.

    The helper's body is replaced by running what it was handed, which is what
    every read in this file did before this story: one call, its output, its
    exit status. Everything else is left exactly as it is — the call sites,
    the messages, the exit codes and the emptiness checks — so a drive against
    this rendering differs from a drive against the shipped script in whether
    a refused read is asked again and in nothing else.
    """
    body = branch_source(text, RETRY_HELPER)
    whole = f"\n{RETRY_HELPER}() {{\n{body}\n}}\n"
    assert text.count(whole) == 1, RETRY_HELPER
    return text.replace(whole, f'\n{RETRY_HELPER}() {{\n  "$@"\n}}\n')


def a_copy_with_no_retry(root: Path) -> Path:
    """The shipped template rendered into `root` with the retry taken out.

    Rendered rather than recovered from history, so what the controls below
    show is the retry deciding rather than a file that has since changed for
    other reasons.
    """
    shipped = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    stripped = without_the_retry(shipped)
    assert stripped != shipped
    return fixture_file(root / "with-no-retry", TEMPLATE_SCRIPT.name, stripped)


def a_command_line(script: Path, job: str) -> str:
    """One copy of the script launched at one job, as a configured command."""
    return f"{INTERPRETER} {shlex.quote(str(script))} {job}"


def test_this_module_launches_a_copy_the_way_the_suite_launches_the_shipped_one():
    """The rendering is driven by the sentence the shared helper already says
    about the shipped template, so a control that ran something else — or ran
    it differently — is a failure here rather than a passing control."""
    assert a_command_line(TEMPLATE_SCRIPT, QUERY_JOB) == \
        reference_script(QUERY_JOB)


# --------------------------------------------------------------------------
# One drive per job, each refusing the read that job gives up on
#
# Each takes the copy of the script to drive, a directory of its own to build
# a tracker under, and what the stub is to be broken with. They are used three
# ways below — refused once, refused throughout, and refused once against a
# rendering with no retry — so the three jobs are asserted about in one place
# rather than in three parallel sets of tests.
# --------------------------------------------------------------------------


def a_sync_whose_item_read_is_refused(script: Path, root: Path, breaking: dict):
    """The sync branch: one entry filed, its item's field values read."""
    environment, ledger = stub_tracker(root)
    result = sync_to_the_board(script, root, environment,
                               key="k-the-read-was-refused",
                               payload=a_filed_brief(), breaking=breaking)
    return result, ledger


def a_fetch_whose_brief_read_is_refused(script: Path, root: Path,
                                        breaking: dict):
    """The query branch: one brief filed, then fetched back by its key."""
    environment, ledger = stub_tracker(root)
    url = file_through_the_reference_sync(root, environment, key="k-a-brief",
                                          payload=a_filed_brief())
    result = subprocess.run(
        [INTERPRETER, str(script), QUERY_JOB],
        input=json.dumps({"key": url}), capture_output=True, text=True,
        timeout=60, cwd=root, env={**environment, **breaking})
    return result, ledger


def a_move_whose_url_read_is_refused(script: Path, root: Path, breaking: dict):
    """The item branch: one item filed, then moved to the column a status
    names, which is the update the failure this story exists against lost."""
    environment, ledger = stub_tracker(root)
    item = an_item_already_filed(root, environment)
    result = move_through_the_reference_script(
        root, environment, key=item, story_id=STORY,
        status=item_update.IN_PROGRESS, script=script, extra=breaking)
    return result, ledger


#: The three jobs, each with the read this drive refuses. The argument that a
#: read may be asked again is the same wherever a read is made, so it is
#: asserted wherever one is made.
THE_THREE_JOBS = (
    pytest.param(a_sync_whose_item_read_is_refused, GRAPHQL_CALL, id="sync"),
    pytest.param(a_fetch_whose_brief_read_is_refused, ISSUE_VIEW_CALL,
                 id="query"),
    pytest.param(a_move_whose_url_read_is_refused, ISSUE_VIEW_CALL, id="item"),
)


# ==========================================================================
# 1. A read refused once answers on the attempt after, and the work completes
# ==========================================================================


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_an_entry_whose_item_read_was_refused_once_still_lands_in_its_column(
        script, tmp_path):
    """The sync branch, driven in both shipped copies.

    A read that failed used to leave the entry pending for a later sweep to
    file; refused once and answered on the attempt after, the same entry lands
    now, in the column that copy files into. Both copies are driven because
    both are shipped artifacts and the edit is supposed to be in each of them.
    """
    result, ledger = a_sync_whose_item_read_is_refused(
        script, tmp_path, refused(GRAPHQL_CALL, times=1))

    assert result.returncode == 0, result.stderr
    assert board_items(ledger)[0]["status"] == \
        the_column_a_filing_lands_in(script)
    assert attempts(ledger, GRAPHQL_CALL) == [GRAPHQL_CALL] * 2


@needs_jq
def test_a_brief_whose_read_was_refused_once_still_comes_back_whole(tmp_path):
    """The query branch's fetch, which is how a brief reaches planning.

    Nothing retries behind it: a fetch that fails is a plan that does not
    happen, so the whole brief coming back is what the second attempt bought.
    """
    result, ledger = a_fetch_whose_brief_read_is_refused(
        TEMPLATE_SCRIPT, tmp_path, refused(ISSUE_VIEW_CALL, times=1))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"brief": a_filed_brief()}
    assert attempts(ledger, ISSUE_VIEW_CALL) == [ISSUE_VIEW_CALL] * 2


@needs_jq
def test_an_item_whose_url_read_was_refused_once_is_still_moved(tmp_path):
    """The item branch, which is the update the failure this story exists
    against lost: a finished story left on the board as still in progress.

    The board is asserted to carry the column, not merely the command to have
    exited zero — an item that was not moved must not be read as one that was.
    """
    result, ledger = a_move_whose_url_read_is_refused(
        TEMPLATE_SCRIPT, tmp_path, refused(ISSUE_VIEW_CALL, times=1))

    assert result.returncode == 0, result.stderr
    assert board_items(ledger)[0]["status"] == A_COLUMN_A_HUMAN_MOVED_IT_TO
    assert attempts(ledger, ISSUE_VIEW_CALL) == [ISSUE_VIEW_CALL] * 2


@needs_jq
@pytest.mark.parametrize("drive,call", THE_THREE_JOBS)
def test_that_same_refusal_ends_a_drive_against_a_copy_with_no_retry(
        drive, call, tmp_path):
    """The control for all three above, one job at a time.

    The same drive, the same tracker and the same single refusal, against a
    rendering of the shipped script whose reads make one attempt each. Every
    one of them ends where the shipped script now carries on, and the tracker
    was asked once — so the three passes above are the second attempt deciding
    rather than a stub that never refused anything.
    """
    result, ledger = drive(a_copy_with_no_retry(tmp_path), tmp_path,
                           refused(call, times=1))

    assert result.returncode != 0, result.stdout
    assert result.stderr.strip()
    assert attempts(ledger, call) == [call]


# ==========================================================================
# 2. A read refused on every attempt gives up in the words it gave before
# ==========================================================================


@needs_jq
@pytest.mark.parametrize("drive,call", THE_THREE_JOBS)
def test_a_read_refused_throughout_gives_up_exactly_as_it_did_before(
        drive, call, tmp_path):
    """What a developer reads is unchanged, shown rather than argued.

    The same drive is made twice over two trackers seeded identically: once
    against the shipped script and once against a rendering of it whose reads
    make one attempt each, which is what every read here did before this
    story. The exit code and the whole of stderr must be the same text, and
    the two must differ in how many times the tracker was asked — which is
    what stops the comparison being satisfied by a retry that never happened.
    """
    shipped, shipped_ledger = drive(
        TEMPLATE_SCRIPT, tmp_path / "shipped", refused(call))
    before, before_ledger = drive(
        a_copy_with_no_retry(tmp_path / "before"), tmp_path / "before",
        refused(call))

    assert shipped.returncode == before.returncode != 0
    assert shipped.stderr == before.stderr
    assert shipped.stderr.strip()
    assert attempts(shipped_ledger, call) == [call] * READ_ATTEMPTS
    assert attempts(before_ledger, call) == [call]


@needs_jq
def test_a_target_that_asks_for_one_attempt_gets_one_attempt(tmp_path):
    """The count is read from the environment, driven rather than read.

    The same single refusal that the item branch answers through above ends
    the drive here, because this invocation alone was given one attempt. Its
    control is that test: same script, same stub, same refusal, and the only
    difference is the variable this one sets.
    """
    result, ledger = a_move_whose_url_read_is_refused(
        TEMPLATE_SCRIPT, tmp_path,
        {**refused(ISSUE_VIEW_CALL, times=1), READ_ATTEMPTS_VARIABLE: "1"})

    assert result.returncode != 0, result.stdout
    assert attempts(ledger, ISSUE_VIEW_CALL) == [ISSUE_VIEW_CALL]
    assert board_items(ledger) == []


# ==========================================================================
# 3. A read that answered with nothing is a misconfigured board
# ==========================================================================


@needs_jq
@pytest.mark.parametrize("call,says", THE_EMPTY_ANSWERS)
def test_a_read_that_answers_with_nothing_is_not_asked_again(call, says,
                                                             tmp_path):
    """Only a non-zero exit is asked again.

    A project naming no id, a field list naming no fields and an item naming
    no URL are all a board that answered and said nothing, which a second
    attempt would answer identically. Each gives up on the first answer, in
    the words it gave before.

    The control is the second half: the same call, in the same drive, refused
    rather than answering with nothing, is asked as many times as a read is
    given attempts. Without it "asked once" would be satisfied just as happily
    by a drive that never reached the call at all.
    """
    empty, empty_ledger = a_move_whose_url_read_is_refused(
        TEMPLATE_SCRIPT, tmp_path / "answered-nothing", answering_nothing(call))

    assert empty.returncode != 0, empty.stdout
    assert says in empty.stderr, empty.stderr
    assert attempts(empty_ledger, call) == [call]

    gave_up, refused_ledger = a_move_whose_url_read_is_refused(
        TEMPLATE_SCRIPT, tmp_path / "was-refused", refused(call))

    assert gave_up.returncode != 0, gave_up.stdout
    assert attempts(refused_ledger, call) == [call] * READ_ATTEMPTS


# ==========================================================================
# 4. Every write makes exactly one attempt
# ==========================================================================


@needs_jq
@pytest.mark.parametrize("call", THE_WRITES)
def test_a_refused_write_is_not_repeated(call, tmp_path):
    """Whether a failed write should be tried again is a different question
    carrying a different risk, and this command does not ask it.

    Every write the sync branch makes is refused in turn — the create, the
    label create, the label add, the board add and the board edit — and each
    must have been made once. The board add would be defensible, because it
    reports an item already there rather than adding a second one, and it is
    left alone too, so the writes are one rule rather than a rule with an
    exception.
    """
    environment, ledger = stub_tracker(tmp_path)
    result = sync_to_the_board(TEMPLATE_SCRIPT, tmp_path, environment,
                               key="k-a-write-was-refused",
                               payload=a_filed_brief(), breaking=refused(call))

    assert result.returncode == TRANSIENT_EXIT, (result.returncode,
                                                 result.stderr)
    assert attempts(ledger, call) == [call]


@needs_jq
def test_the_item_branchs_body_write_is_not_repeated_either(tmp_path):
    """The one write the item branch makes, on the branch where giving up is
    most expensive — which is not a reason to write twice.

    The read above it is asserted beside it: that one answered on its first
    attempt, so the single write is the rule rather than a drive that stopped
    early.
    """
    environment, ledger = stub_tracker(tmp_path)
    item = an_item_already_filed(tmp_path, environment)

    result = publish_through_the_reference_script(
        tmp_path, environment, key=item, story_id=STORY,
        document=item_update.projection(STORY, ARTIFACT),
        extra=refused(LABEL_ADD_CALL))

    assert result.returncode != 0, result.stdout
    assert attempts(ledger, LABEL_ADD_CALL) == [LABEL_ADD_CALL]
    assert attempts(ledger, ISSUE_VIEW_CALL) == [ISSUE_VIEW_CALL]


@needs_jq
def test_the_same_ledger_counts_a_refused_read_at_more_than_one(tmp_path):
    """The control for every "exactly one attempt" above.

    The same stub, the same spelling of a refusal and the same reading of the
    ledger, pointed at a read instead of a write, counts more than one. So a
    write counted at one is the script making one attempt rather than a ledger
    that cannot count past it.
    """
    environment, ledger = stub_tracker(tmp_path)
    result = sync_to_the_board(TEMPLATE_SCRIPT, tmp_path, environment,
                               key="k-a-read-was-refused",
                               payload=a_filed_brief(),
                               breaking=refused(GRAPHQL_CALL))

    assert result.returncode == TRANSIENT_EXIT, (result.returncode,
                                                 result.stderr)
    assert attempts(ledger, GRAPHQL_CALL) == [GRAPHQL_CALL] * READ_ATTEMPTS
    assert READ_ATTEMPTS > 1


# ==========================================================================
# 5. The dedupe search, where a failure costs a duplicate brief
# ==========================================================================


@needs_jq
def test_a_dedupe_search_refused_once_answers_without_falling_back(tmp_path):
    """A failed dedupe search is read by the harness as knowing nothing, so it
    files anyway and the board collects duplicate briefs.

    The question is put through the harness's own path, and the batch is
    refused once. It is asked again, answers, and the per-path fallback behind
    it is not needed at all — which is the retry doing what the fallback would
    otherwise have had to.
    """
    environment, ledger = stub_tracker(tmp_path)
    paths = scope_of(2)
    filed = file_against(tmp_path, environment, "k-searched-twice", [paths[1]])
    already = len(attempts(ledger, ISSUE_LIST_CALL))

    answer, searches = answer_for(tmp_path, environment, paths,
                                  **refused(ISSUE_LIST_CALL, times=1))

    assert answer.answered is True, answer.reason
    assert [one.key for one in answer.items] == [filed]
    assert len(attempts(ledger, ISSUE_LIST_CALL)) - already == 2
    assert searches.per_path == []


@needs_jq
def test_without_the_retry_that_same_search_costs_the_fallback(tmp_path):
    """The control: the same question, the same single refusal, against a
    rendering with no retry.

    The batch is refused and not asked again, so every path is asked about one
    at a time instead. The answer survives here because the fallback exists —
    which is exactly why the assertion above is about the fallback not having
    run rather than about the answer.
    """
    environment, ledger = stub_tracker(tmp_path)
    paths = scope_of(2)
    filed = file_against(tmp_path, environment, "k-fell-back", [paths[1]])
    without = a_copy_with_no_retry(tmp_path)

    counted = Searches(Path(environment[LEDGER_VARIABLE]))
    with stubbed(environment, **refused(ISSUE_LIST_CALL, times=1)):
        answer = asked(a_command_line(without, QUERY_JOB), tmp_path,
                       paths=paths)
    counted.close()

    assert answer.answered is True, answer.reason
    assert [one.key for one in answer.items] == [filed]
    assert paths_in(counted.per_path) == sorted(paths)


# ==========================================================================
# 6. The two constants, in both shipped copies
# ==========================================================================


def named_away_from_the_helper(text: str, name: str) -> list[str]:
    """Every line naming `name` that is neither its own declaration nor part
    of the helper that reads it.

    What the story asks is that neither constant is written into a call site:
    one declaration at the top, one reading of it in one place, and nothing
    anywhere else that a copy could disagree with. The scan is the shared one,
    which is also how the suite decides which of a script's constants a board
    value could be read out of — the two questions are the same question, so
    they are one scan.
    """
    return named_outside(text, name, RETRY_HELPER)


@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_both_copies_declare_the_count_and_the_wait_at_the_top(script):
    """Both copies are shipped artifacts and both are the subject.

    Each constant is declared in the shape every editable constant in these
    files takes — a name, an environment variable, a default written in the
    file — and each is read by the helper and by nothing else. That the helper
    does name them is asserted beside the absence, so "named nowhere else" is
    a scan that can see them rather than one that has stopped looking.
    """
    text = script.read_text(encoding="utf-8")
    declared = sync_constants(text)

    for name in (ATTEMPTS_CONSTANT, WAIT_CONSTANT):
        variable, default = declared[name]
        assert variable.startswith(SYNC_VARIABLE_PREFIX), variable
        assert default, name
        assert name in branch_source(text, RETRY_HELPER), name
        assert named_away_from_the_helper(text, name) == [], name


def test_the_sweep_of_board_calls_still_finds_every_subcommand():
    """The two project reads are still written as literal invocations.

    The suite builds its sweep of board calls by reading `gh project
    <subcommand>` out of the shipped template, and this story moved both of
    those reads into functions of their own. A subcommand that went behind a
    variable in the moving would shrink that sweep silently — the
    parametrized board-failure cases in `tests/test_filed_query.py` would go
    on passing over fewer calls — so what the sweep finds is asserted here
    rather than left to be noticed.
    """
    assert set(PROJECT_SUBCOMMANDS) >= set(THE_BOARD_SUBCOMMANDS), \
        PROJECT_SUBCOMMANDS
    for script in (TEMPLATE_SCRIPT, INSTALLED_SCRIPT):
        text = script.read_text(encoding="utf-8")
        for subcommand in THE_BOARD_SUBCOMMANDS:
            assert f"gh project {subcommand}" in text, (script.name, subcommand)


def test_the_two_copies_declare_the_same_count_and_the_same_wait():
    """Neither is a statement about a particular board, so neither is among
    the values the two copies differ in.

    The assertion that they differ in nothing *but* their board values is
    `test_filed_query.py`'s, together with its planted-difference control;
    this one says these two are not among them.
    """
    template = sync_constants(TEMPLATE_SCRIPT.read_text(encoding="utf-8"))
    installed = sync_constants(INSTALLED_SCRIPT.read_text(encoding="utf-8"))

    for name in (ATTEMPTS_CONSTANT, WAIT_CONSTANT):
        assert template[name] == installed[name], name


def test_that_scan_reports_a_copy_that_names_the_count_at_a_call_site():
    """The control: a rendering of the template whose sync branch reads the
    attempt count, which the same scan must report.

    Rendered here rather than written to the tree, so the control is about the
    scan and not about this repository.
    """
    template = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
    planted = template.replace(
        "\ndo_sync() {\n",
        f'\ndo_sync() {{\n  echo "${{{ATTEMPTS_CONSTANT}}}" >&2\n', 1)
    assert planted != template

    reported = named_away_from_the_helper(planted, ATTEMPTS_CONSTANT)
    assert reported, "the scan sees no call site it should report"
    assert any(ATTEMPTS_CONSTANT in line for line in reported), reported
