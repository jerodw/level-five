"""story-097 validation: a run, and the questions ahead of it, say which story
they are about.

Every point where a developer decides something about a story named it by its
identifier alone, and the approval prompt named nothing at all: "approve this
plan?" omitted its own subject, which is the act the whole mandate mechanism
rests on. This story gives the messages below the story's title beside its id — the
approval prompt, the run offer, the skip line each artifact gets when the offer
is declined or moot, and the fresh run's workflow-started announcement, which is
both the first line on the console and the first line of `events.log`.

Written from the story's acceptance criteria rather than from the
implementation, at three altitudes:

  * **the bounded title itself** — `story_coordinator.story_title` and the
    constant beside it — is driven directly against parses this module
    constructs, because it is a function over a mapping and needs no artifact,
    no repository and no run.
  * **the messages themselves** are read off the processes that print them: the real
    `scripts/l5-plan` run as a subprocess against the throwaway planning
    repository and stub `claude` that `tests/test_plan_commit.py` builds, and
    the real `story_coordinator.run_story` driven against a target built under
    `tmp_path` with a fake agent runner.
  * **the source** is read where the story constrains it — that the title is
    obtained through the coordinator's own reader and that no call site this
    story added reaches for a YAML parser.

**The workflow every run below executes is built, not shipped.** It is the
definition `tests/test_run_announces_its_workflow.py` assembles from the
`tests/conftest.py` builder, imported rather than rebuilt: the subject here is
what a run *says about* the story it was given, and a definition is an input to
that. Reading `workflows/story-workflow.json` would turn what this repository
deploys into something the suite enforces. The story artifacts are this
module's own, because a title is the one thing every case here varies.

Every absence asserted here carries a demonstration that the same check reports
the violation it exists to catch:

  * "a story with no title, a parse with no story mapping and no parse at all
    each answer with the empty string" sits beside the same call on a titled
    parse, which answers with the title;
  * "an untitled artifact is named by its id alone at the approval prompt" sits
    beside the same fixture carrying a title, where the same line carries it,
    and beside the fault the validation goes on to report;
  * "the subject lines were printed before anything was stamped" is a rejected
    session compared by the artifact's own bytes and an unmoved HEAD, beside
    the same fixture approved, where both move;
  * "the announcement names this story's title and not the other" sits beside
    the same story run under the other title, where the two swap;
  * "the run wrote exactly one `workflow-started` entry" sits beside the same
    run directory with a second one appended, which the same counter reports as
    two;
  * "the resumed announcement carries no title" sits beside the fresh
    announcement in that same run's own stream, which does;
  * "the history entry gained no field" sits beside the same subset check over
    that entry with one more key, which reports it;
  * "no call site added by this story names a YAML parser" sits beside the same
    scanner over a source that names one, which reports it.

No model is invoked anywhere here: every coordinator run goes through the fake
runner imported below, and every `claude` the planning path reaches is the stub.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
import plan_mandate
import plan_run_offer
import schema_validator
import story_coordinator

from test_plan_commit import (
    Planning,
    L5_PLAN,
    PLANNED_WORKFLOW,
    artifact,
    bare_remote,
    committed_paths,
    make_planning,
    run_plan,
    writes,
)
from test_story_mandate import PLANNED_ID, PLANNED_REL, session_writing
#: The built definition, the built target, the fake runner and the readings of
#: one announcement, taken from the module whose subject the announcement's
#: other half is rather than assembled a second time here.
from test_run_announces_its_workflow import (
    EXECUTED,
    FAIL,
    RESUMED,
    STARTED,
    STORY_ID,
    Runner,
    build_harness,
    build_target,
    entries_of_kind,
    events_of,
    first_line,
    git,
    history_of,
    message_of,
    run_dir_of,
    state_of,
    write,
)

HARNESS_ROOT = Path(story_coordinator.__file__).resolve().parents[1]

#: The bound, read from where it is declared rather than spelled here, so a
#: story that revises it moves every expectation below with it.
BOUND = story_coordinator.PRINTED_TITLE_MAX_LENGTH

#: The single character a cut title ends in. Spelled here because it is what
#: the criterion asks for — one character, not three dots — and the assertion
#: below states both halves of that.
ELLIPSIS = "…"


def test_the_bound_is_a_documented_module_constant_and_the_ellipsis_is_one_character():
    """Load-bearing for every expectation this module composes from them."""
    assert isinstance(BOUND, int) and BOUND > 0
    assert len(ELLIPSIS) == 1
    assert ELLIPSIS != "..."


# ==========================================================================
# 1. The bounded title, driven against parses this module constructs
# ==========================================================================


def parsed(title=None, *, story: bool = True) -> dict:
    """A parse shaped as `read_story` returns one, carrying `title` or not."""
    if not story:
        return {"tasks": ["do the sample work"]}
    inner = {"id": PLANNED_ID}
    if title is not None:
        inner["title"] = title
    return {"story": inner}


def test_a_title_that_is_already_one_short_line_comes_back_as_it_was_written():
    assert story_coordinator.story_title(parsed("A run says which story it is")) \
        == "A run says which story it is"


@pytest.mark.parametrize("written, printed", [
    pytest.param("A title the planner\nwrapped across two lines",
                 "A title the planner wrapped across two lines",
                 id="a newline inside the title"),
    pytest.param("  Padded   by   runs of spaces  ",
                 "Padded by runs of spaces",
                 id="runs of spaces and padding either end"),
    pytest.param("A title\n\nwith a blank line\tand a tab",
                 "A title with a blank line and a tab",
                 id="a blank line and a tab"),
])
def test_a_title_is_collapsed_to_one_line_before_it_is_printed(written: str,
                                                               printed: str):
    """One line is what a message naming a story beside its id can carry, and
    a title is prose an agent wrote — nothing in the story contract forbids it
    a newline, so the collapse happens where it is printed."""
    assert story_coordinator.story_title(parsed(written)) == printed


def test_a_title_of_exactly_the_bound_is_printed_whole():
    """The boundary from below: cutting here would cut a title that fits."""
    exact = "t" * BOUND
    assert story_coordinator.story_title(parsed(exact)) == exact


def test_a_title_longer_than_the_bound_is_cut_to_it_and_ended_with_an_ellipsis():
    """The arithmetic is this module's own rather than the function's, so a
    bound applied one character either side of where the story puts it fails
    here rather than agreeing with itself."""
    long = "t" * (BOUND + 40)
    bounded = story_coordinator.story_title(parsed(long))

    assert bounded == "t" * BOUND + ELLIPSIS
    assert len(bounded) == BOUND + 1
    assert not bounded.endswith("...")


def test_a_title_collapsed_below_the_bound_is_not_cut():
    """The collapse happens first: a title whose whitespace makes it longer
    than the bound and whose words do not is printed whole."""
    words = ["w"] * (BOUND // 2)
    spaced, collapsed = "  ".join(words), " ".join(words)
    assert len(spaced) > BOUND >= len(collapsed)
    assert story_coordinator.story_title(parsed(spaced)) == collapsed


@pytest.mark.parametrize("story", [
    pytest.param(None, id="no parse at all"),
    pytest.param({}, id="a parse carrying nothing"),
    pytest.param(parsed(story=False), id="a parse carrying no story mapping"),
    pytest.param(parsed(), id="a story carrying no title"),
    pytest.param({"story": "not a mapping"}, id="a story that is not a mapping"),
    pytest.param(parsed(["not", "a", "string"]), id="a title that is a list"),
])
def test_a_title_that_cannot_be_read_answers_with_the_empty_string(story):
    """So a caller with no title names the story by its id alone rather than by
    a placeholder.

    The control is immediately below: the same call on a parse that does carry
    a title answers with the title, so the empty string here is what was read
    rather than what the function always returns.
    """
    assert story_coordinator.story_title(story) == ""


def test_the_same_call_on_a_titled_parse_answers_with_the_title():
    """The control the empty strings above need."""
    assert story_coordinator.story_title(parsed("A title")) == "A title"


# ==========================================================================
# 2. The planning session: the artifacts, and how this module titles them
# ==========================================================================


def title_line(title: str) -> str:
    """A title as the story dialect carries it, inline or as a block scalar.

    The dialect holds a newline inside a scalar only in a `key: |` block, so a
    title this module wants wrapped across lines is written as one. Both
    spellings are substituted into the same `title:` line, which is why this is
    one helper rather than two artifacts.
    """
    lines = title.split("\n")
    if len(lines) == 1:
        return title
    return "|\n" + "\n".join(f"    {line}" for line in lines)


def titled(title: str, story_id: str = PLANNED_ID) -> str:
    """What the stub session writes: an artifact carrying this title.

    No mandate block: since story-087 the block is `l5-plan`'s to confer when
    the session ends, and an artifact arriving from a session already carrying
    one is refused rather than trusted.
    """
    return artifact(story_id, title_line(title))


#: The title every case that is not about the bound gives its artifact. Chosen
#: so it cannot be found in anything else the script prints.
TITLE = "Zarquon the planned story"

#: A title longer than the bound, and what a message that names it must carry.
LONG_TITLE = ("Zarquon " + "and its consequences " * 8).strip()
BOUNDED_TITLE = LONG_TITLE[:BOUND] + ELLIPSIS

#: A title written across two lines, and what a message that names it carries.
WRAPPED_TITLE = "Zarquon the planned story\nwrapped across two lines"
UNWRAPPED_TITLE = "Zarquon the planned story wrapped across two lines"


def test_the_titles_this_module_plans_with_are_what_it_expects_printed():
    """Load-bearing: the long title must exceed the bound and the wrapped one
    must actually carry a newline, or the two cases below assert nothing."""
    assert len(LONG_TITLE) > BOUND
    assert "\n" in WRAPPED_TITLE
    assert "\n" not in BOUNDED_TITLE and len(BOUNDED_TITLE) == BOUND + 1
    assert title_line(WRAPPED_TITLE).startswith("|\n")


@pytest.fixture
def planning(tmp_path: Path) -> Planning:
    """The throwaway planning repository with a bare origin to push to.

    The same fixture the modules whose subject is the planning session use: a
    session that is approved has to be able to get as far as the push before
    the run offer this module reads is reached at all.
    """
    made = make_planning(tmp_path)
    made.remote = bare_remote(tmp_path, made, upstream=True)
    return made


def decline_plan(planning: Planning, **stub) -> subprocess.CompletedProcess:
    """The real script on a terminal, with the approval question answered no.

    The shared runner's reply approves, because almost every module needs to
    get past that question to reach its own subject. What is being read here is
    what the script printed *above* the question, so this one rejects: the
    subject lines must be there whether or not anything is stamped afterwards.
    """
    with conftest.a_terminal_for_stdin(reply=conftest.DECLINES) as stdin:
        return subprocess.run(
            [sys.executable, str(L5_PLAN), "--workflow", PLANNED_WORKFLOW,
             "a story request"],
            cwd=planning.root, env=planning.env(**stub), stdin=stdin,
            capture_output=True, text=True)


def printed_lines(output: str) -> list[str]:
    """Every line the script wrote, with the trailing newline dropped.

    The offer's prompt ends without one, so it shares its line with nothing:
    it is the last line of the output when the reply is read from a terminal.
    """
    return [line.rstrip() for line in output.splitlines()]


def messages_this_story_bounds(output: str, story_id: str) -> list[str]:
    """The lines this story gives a title to: the approval prompt's subject
    line, and the run offer's prompt and skip line.

    Not every line that names the story, because the title reaches one other
    place the script prints and reaches it whole: `l5-plan` reports the commit
    it made, and the commit subject story-025 composes is `Plan <id>: <title>`
    with the title as the artifact carries it. A commit message is not a
    terminal line a developer reads a question off, and this story does not
    touch it — so the absence asserted below is scoped to the lines this story
    does bound rather than to the whole of stdout, where it would be an
    assertion about story-025's message instead.
    """
    named = (subject_line(story_id), f"l5-plan: run {story_id}")
    return [line for line in printed_lines(output)
            if any(line.startswith(prefix) for prefix in named)]


def subject_line(story_id: str, title: str = "") -> str:
    """The line the approval prompt gives an artifact, as the story asks for
    it: the id alone when there is no title, and the id and the title
    together when there is."""
    named = f"{story_id}: {title}" if title else story_id
    return f"l5-plan: {named}"


# ==========================================================================
# 3. The approval prompt gains a subject
# ==========================================================================


def test_the_approval_prompt_names_each_artifact_by_id_and_title(
        planning: Planning):
    """One line per artifact, above the question, and the question unchanged.

    Two artifacts, because "one line per artifact" is not observable on one:
    each is named on its own line by its own id and its own title, and the
    single question is still asked once for the session rather than once per
    artifact.
    """
    second = "story-901"
    second_title = "Zarquon the second planned story"
    result = run_plan(planning, L5_STUB_WRITE=writes(
        (PLANNED_REL, titled(TITLE)),
        (f".harness/stories/{second}.yaml", titled(second_title, second))))
    assert result.returncode == 0, result.stdout + result.stderr

    lines = printed_lines(result.stdout)
    question = "approve this plan?"
    asked = [index for index, line in enumerate(lines) if question in line]
    assert len(asked) == 1, result.stdout
    for story_id, title in ((PLANNED_ID, TITLE), (second, second_title)):
        named = [index for index, line in enumerate(lines)
                 if line == subject_line(story_id, title)]
        assert len(named) == 1, (story_id, result.stdout)
        assert named[0] < asked[0], result.stdout


def test_the_subject_lines_are_printed_before_anything_is_stamped(
        planning: Planning):
    """A rejected session named its story and stamped nothing.

    Nothing about the invocation differs from the control below but the answer,
    and the answer is read after the lines are printed — so what is asserted
    here is the artifact's own bytes, an unmoved HEAD and unmoved remote refs
    beside a subject line that was printed anyway.
    """
    head = planning.head()
    result = decline_plan(planning, L5_STUB_WRITE=session_writing(titled(TITLE)))

    assert result.returncode == 1
    assert subject_line(PLANNED_ID, TITLE) in printed_lines(result.stdout)
    assert planning.head() == head
    written = (planning.root / PLANNED_REL).read_text(encoding="utf-8")
    assert written == titled(TITLE)
    assert not plan_mandate.carries_a_mandate(written)


def test_the_same_fixture_approved_stamps_and_commits_what_it_named(
        planning: Planning):
    """The control the rejection above needs: the answer is what decided it."""
    head = planning.head()
    result = run_plan(planning, L5_STUB_WRITE=session_writing(titled(TITLE)))

    assert result.returncode == 0, result.stdout + result.stderr
    assert subject_line(PLANNED_ID, TITLE) in printed_lines(result.stdout)
    assert planning.head() != head
    assert plan_mandate.carries_a_mandate(
        (planning.root / PLANNED_REL).read_text(encoding="utf-8"))


@pytest.mark.parametrize("written, printed", [
    pytest.param(LONG_TITLE, BOUNDED_TITLE, id="longer than the bound"),
    pytest.param(WRAPPED_TITLE, UNWRAPPED_TITLE, id="wrapped across lines"),
])
def test_the_prompt_prints_a_bounded_one_line_title(planning: Planning,
                                                    written: str, printed: str):
    """The bound and the collapse, observed where they are for: on the line a
    developer reads before answering.

    The expected text is this module's own arithmetic over its own title rather
    than a second call to the function under test, so a bound applied
    elsewhere, or not at all, fails here.

    Every line this story titles is then held to both halves rather than only
    the one asserted above: each carries the bounded, collapsed title whole —
    which a line that broke at the title's newline, or cut it somewhere else,
    could not — and none carries the title as it was written. The control that
    this selection sees lines at all, and that they can carry a written title,
    is `test_the_bounded_lines_carry_a_short_title_as_written` below.
    """
    result = run_plan(planning, L5_STUB_WRITE=session_writing(titled(written)))
    assert result.returncode == 0, result.stdout + result.stderr

    assert subject_line(PLANNED_ID, printed) in printed_lines(result.stdout)
    bounded = messages_this_story_bounds(result.stdout, PLANNED_ID)
    assert bounded, result.stdout
    for line in bounded:
        assert printed in line, line
        assert written not in line, line


def test_the_bounded_lines_carry_a_short_title_as_written(planning: Planning):
    """The control the absence above needs.

    The same selection over the same fixture, given a title that needs no
    bounding: every line it holds carries the title exactly as it was written,
    so the absences above are the bound at work rather than a selection that
    has stopped seeing the lines it selects.
    """
    result = run_plan(planning, L5_STUB_WRITE=session_writing(titled(TITLE)))
    assert result.returncode == 0, result.stdout + result.stderr

    bounded = messages_this_story_bounds(result.stdout, PLANNED_ID)
    assert bounded, result.stdout
    for line in bounded:
        assert TITLE in line, line


# ==========================================================================
# 4. An artifact whose title cannot be read is named by its id alone
# ==========================================================================


#: An artifact with the title line taken out, and one no parser accepts. Both
#: are refused by the validation that runs after the approval, which is the
#: second half of what this pair asserts: the prompt names what it can, and the
#: fault is still reported by the check that owns it.
UNTITLED = "".join(line for line in titled(TITLE).splitlines(keepends=True)
                   if not line.startswith("  title:"))
UNPARSEABLE = titled(TITLE) + "\n\tan indented line no parser accepts: ][\n"


def test_the_two_faulty_artifacts_this_module_writes_are_faulty():
    """Load-bearing: the case below says nothing if these parse and validate."""
    assert "title:" not in UNTITLED
    assert story_coordinator.read_story(UNTITLED).problems
    assert story_coordinator.read_story(UNPARSEABLE).parsed is None


@pytest.mark.parametrize("body, fault", [
    pytest.param(UNTITLED, "$.story.title", id="an artifact carrying no title"),
    pytest.param(UNPARSEABLE, "tab", id="an artifact that does not parse"),
])
def test_an_unreadable_title_leaves_the_id_alone_and_the_fault_reported(
        planning: Planning, body: str, fault: str):
    """The prompt names the artifact by its id, the question is still asked,
    and the validation that follows still refuses it.

    The control is the test below: the same fixture carrying a readable title
    prints that same line with the title on it, so "named by its id alone" is
    a fact about this artifact rather than about a line that never carries one.
    """
    result = run_plan(planning, L5_STUB_WRITE=session_writing(body))

    assert subject_line(PLANNED_ID) in printed_lines(result.stdout)
    assert "approve this plan?" in result.stdout
    assert result.returncode == 1
    assert fault in result.stdout + result.stderr


def test_the_same_prompt_carries_a_title_when_the_artifact_has_one(
        planning: Planning):
    """The control for the two cases above."""
    result = run_plan(planning, L5_STUB_WRITE=session_writing(titled(TITLE)))

    lines = printed_lines(result.stdout)
    assert subject_line(PLANNED_ID, TITLE) in lines
    assert subject_line(PLANNED_ID) not in lines


# ==========================================================================
# 5. The run offer and the skip line
# ==========================================================================


def run_command_as_the_script_types_it(story_id: str, cwd: Path) -> str:
    """What the skip line hands over, composed by the module that composes it.

    `plan_run_offer.run_command` types the executable relative to the current
    directory, and the directory the script has is the target repository rather
    than this one — so it is called from there, and what comes back is what the
    script had to print.
    """
    previous = os.getcwd()
    os.chdir(cwd)
    try:
        return plan_run_offer.run_command(HARNESS_ROOT, story_id)
    finally:
        os.chdir(previous)


def test_the_run_offer_names_the_id_and_the_title(planning: Planning):
    """The prompt a developer answers before pressing Enter.

    `run_plan` answers the approval and then declines the offer, so both the
    prompt and the skip line the decline produces are in this one output. The
    prompt is written without a newline — it is a question waiting for a reply
    — so it is found as text rather than as a whole line.
    """
    result = run_plan(planning, L5_STUB_WRITE=session_writing(titled(TITLE)))
    assert result.returncode == 0, result.stdout + result.stderr

    assert result.stdout.count(f"l5-plan: run {PLANNED_ID}: {TITLE} now?") == 1
    assert f"run {PLANNED_ID} now?" not in result.stdout


def test_the_declined_offer_s_skip_line_names_the_title_and_still_hands_over_the_command(
        planning: Planning):
    """The title is added beside the id; the command is untouched.

    The command is composed by `plan_run_offer` here rather than spelled, so
    this asserts the line hands over what the run path would have started
    rather than that it hands over some text.
    """
    result = run_plan(planning, L5_STUB_WRITE=session_writing(titled(TITLE)))
    assert result.returncode == 0, result.stdout + result.stderr

    command = run_command_as_the_script_types_it(PLANNED_ID, planning.root)
    assert result.stdout.count(
        f"l5-plan: run {PLANNED_ID}: {TITLE} with: {command}\n") == 1


def test_more_than_one_artifact_skips_each_one_by_id_and_title(
        planning: Planning):
    """Two artifacts make the offer moot, and each gets its own skip line.

    Nothing is asked — the offer prompts only where there is one story to run —
    so what a developer is left with is these lines, and each has to say which
    story it runs as well as how.
    """
    second = "story-901"
    second_title = "Zarquon the second planned story"
    result = run_plan(planning, L5_STUB_WRITE=writes(
        (PLANNED_REL, titled(TITLE)),
        (f".harness/stories/{second}.yaml", titled(second_title, second))))
    assert result.returncode == 0, result.stdout + result.stderr

    assert "now?" not in result.stdout
    for story_id, title in ((PLANNED_ID, TITLE), (second, second_title)):
        command = run_command_as_the_script_types_it(story_id, planning.root)
        assert result.stdout.count(
            f"l5-plan: run {story_id}: {title} with: {command}\n") == 1


# ==========================================================================
# 6. What a mandate records and how it is conferred is unchanged
# ==========================================================================


def test_one_question_one_stamp_and_one_commit(planning: Planning):
    """The constraint the story sets on itself: the prompt gained a subject and
    the act behind it did not move.

    Two artifacts, one approval question, one block on each artifact, and one
    commit holding both — so a subject line printed per artifact cannot have
    become a question, a stamp or a commit per artifact.
    """
    second = "story-901"
    result = run_plan(planning, L5_STUB_WRITE=writes(
        (PLANNED_REL, titled(TITLE)),
        (f".harness/stories/{second}.yaml", titled("Another one", second))))
    assert result.returncode == 0, result.stdout + result.stderr

    assert result.stdout.count("approve this plan?") == 1
    paths = [PLANNED_REL, f".harness/stories/{second}.yaml"]
    for relative in paths:
        text = (planning.root / relative).read_text(encoding="utf-8")
        assert text.count(f"\n{plan_mandate.MANDATE_KEY}:\n") == 1
    committed = subprocess.run(
        ["git", "-C", str(planning.root), "log", "--format=%H",
         "--diff-filter=A", "--", *paths],
        capture_output=True, text=True, check=True).stdout.split()
    assert len(committed) == 1
    assert committed_paths(planning.root, committed[0]) == sorted(paths)


# ==========================================================================
# 7. The fresh run's announcement
#
# The target is built under `tmp_path` and the definition it runs is the one
# `tests/test_run_announces_its_workflow.py` assembles: the announcement's
# other half is that module's subject, and the story's title is this one's.
# ==========================================================================


STORY = """\
story:
  id: {story_id}
  title: {title}
  description: |
    A stand-in story used to drive the coordinator deterministically against
    a fake runner.
  workflow: {workflow}

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


def story_with_title(title: str, tail: str = "") -> str:
    """The artifact a run is given, carrying this title and a mandate block.

    A story handed straight to the coordinator carries one: since story-087 a
    run whose mandate does not resolve to a human is refused before anything is
    created, and this module's subject is what the run says once it starts.
    """
    return STORY.format(story_id=STORY_ID, title=title_line(title),
                        workflow=EXECUTED["name"]) + conftest.MANDATE_BLOCK \
        + tail


@pytest.fixture
def environment(tmp_path):
    """A builder for (target, harness) pairs, one target per title.

    A factory rather than a fixture, because the control below holds two
    targets side by side — the same story titled two ways — and each needs a
    repository of its own.
    """
    made = set()

    def make(title: str, *, name: str) -> tuple[Path, Path]:
        assert name not in made, f"two environments named {name}"
        made.add(name)
        harness = build_harness(tmp_path / f"harness-{name}", (EXECUTED,))
        target = build_target(tmp_path / f"target-{name}", EXECUTED["name"],
                              EXECUTED["name"])
        retitle(target, title)
        return target, harness

    return make


def retitle(target: Path, title: str, tail: str = "") -> None:
    """Give the built target's story this title, and commit it.

    The builder writes a story of its own and commits the repository; the title
    is the one thing every case here varies, so it is written over and
    committed rather than threaded through a builder whose subject is
    something else.
    """
    write(target / ".harness" / "stories" / f"{STORY_ID}.yaml",
          story_with_title(title, tail))
    git(target, "add", "-A")
    git(target, "commit", "-q", "-m", "give the story its title")


def announcement(target: Path, harness: Path, capsys) -> str:
    """Run the story and return the message of its first console line."""
    code = story_coordinator.run_story(STORY_ID, harness, target,
                                       Runner(target, EXECUTED))
    assert code == 0
    return message_of(first_line(capsys.readouterr().out))


def test_a_fresh_runs_announcement_names_the_title_after_the_story_id(
        environment, capsys):
    """The first line on the console and the first line of `events.log`.

    The workflow the run is executing is still named, and the title comes after
    the id rather than instead of it — both, because this story adds to that
    line rather than replacing what story-072 put there.
    """
    target, harness = environment(TITLE, name="fresh")

    announced = announcement(target, harness, capsys)

    assert EXECUTED["name"] in announced
    assert STORY_ID in announced
    assert TITLE in announced
    assert announced.index(STORY_ID) < announced.index(TITLE)
    assert message_of(events_of(target)[0]) == announced


def test_the_same_story_titled_otherwise_announces_that_title(environment,
                                                              capsys):
    """The control the assertion above needs: "the title is in the line" is
    not something a line that names every title, or none, could satisfy."""
    other = "Grunthos the story that also exists"
    target, harness = environment(other, name="fresh-other")

    announced = announcement(target, harness, capsys)

    assert other in announced
    assert TITLE not in announced


@pytest.mark.parametrize("written, printed", [
    pytest.param(LONG_TITLE, BOUNDED_TITLE, id="longer than the bound"),
    pytest.param(WRAPPED_TITLE, UNWRAPPED_TITLE, id="wrapped across lines"),
])
def test_the_announcement_is_one_bounded_line(environment, capsys,
                                              written: str, printed: str):
    """A run's first line stays one line at a conventional terminal width.

    Read off `events.log` as well as the console, because a message carrying a
    newline would be two lines there and every reader of that file counts
    lines.
    """
    target, harness = environment(written, name="bounded")

    announced = announcement(target, harness, capsys)

    assert printed in announced
    assert written not in announced
    assert "\n" not in announced
    assert message_of(events_of(target)[0]) == announced


def test_the_announcement_is_still_one_workflow_started_entry(environment,
                                                              capsys):
    """The constraint: no new kind, no second line, and `events.log` and
    `execution-history.json` still two renderings of one write."""
    target, harness = environment(TITLE, name="one-entry")

    announced = announcement(target, harness, capsys)

    started = entries_of_kind(history_of(target), STARTED)
    assert len(started) == 1
    assert started[0]["message"] == announced
    assert started[0]["sequence"] == 1


def test_a_second_announcement_would_be_reported_by_the_same_counter(
        environment, capsys):
    """The control for the count above: the same reading over the same run
    directory with a second entry of that kind appended reports two."""
    target, harness = environment(TITLE, name="one-entry-control")
    announcement(target, harness, capsys)
    assert len(entries_of_kind(history_of(target), STARTED)) == 1

    story_coordinator.append_event(run_dir_of(target), "and again",
                                   kind=STARTED)

    assert len(entries_of_kind(history_of(target), STARTED)) == 2


HISTORY_SCHEMA = schema_validator.load_schema("execution-history")


def test_the_history_gains_no_field_and_validates_against_the_shipped_schema(
        environment, capsys):
    """A message is not a field: the entry carries what it carried.

    The control is beside the subset check — the same comparison over that
    entry with one more key reports it — so an entry that had gained a field
    could not pass this.
    """
    target, harness = environment(TITLE, name="history")
    announcement(target, harness, capsys)

    history = history_of(target)
    assert schema_validator.validate(history, HISTORY_SCHEMA) == []
    (entry,) = entries_of_kind(history, STARTED)
    declared = set(HISTORY_SCHEMA["items"]["properties"])
    assert set(entry) <= declared
    assert not (set(entry) | {"story_title"}) <= declared


# ==========================================================================
# 8. A resumed run says what it always said
# ==========================================================================


def test_a_resumed_runs_announcement_is_unchanged(environment, capsys):
    """The story extends the fresh run's first line and leaves the resumed
    one alone.

    The control is in the same run's own stream: the fresh announcement above
    the resumed one carries the title, so the resumed one's silence about it is
    this branch's rather than a title that was never readable here.
    """
    target, harness = environment(TITLE, name="resume")
    failing = Runner(target, EXECUTED, verdicts=[FAIL])
    assert story_coordinator.run_story(STORY_ID, harness, target, failing) == 2
    capsys.readouterr()
    stage = state_of(target)["current_stage"]
    # An amendment the run can see is what clears the resume guard; a comment
    # is the smallest one the dialect allows and leaves the title alone.
    retitle(target, TITLE, tail="\n# amended to clear the resume guard\n")

    story_coordinator.run_story(STORY_ID, harness, target,
                                Runner(target, EXECUTED))

    announced = message_of(first_line(capsys.readouterr().out))
    (resumed,) = entries_of_kind(history_of(target), RESUMED)
    assert resumed["message"] == announced
    assert stage in announced
    assert EXECUTED["name"] in announced
    assert TITLE not in announced
    (started,) = entries_of_kind(history_of(target), STARTED)
    assert TITLE in started["message"]


# ==========================================================================
# 9. Where the title comes from
# ==========================================================================


#: Ways of reading a story artifact that are not the shipped reader: a YAML
#: library under either of the names it is imported by, and the parser the
#: reader itself is built from, reached around it.
OTHER_READERS = ("yaml.", "safe_load", "story_parser.")


def other_readers(source: str) -> list[str]:
    return [named for named in OTHER_READERS if named in source]


def report_source() -> str:
    return conftest.function_source(
        L5_PLAN.read_text(encoding="utf-8"), "report")


def test_the_titles_are_read_through_the_coordinators_own_reader():
    """The one reader of a story artifact this repository has.

    The read that derives a title is the coordinator's, and its result reaches
    the coordinator's own bounded-title function — so a title obtained some
    other way, or printed unbounded, fails here.
    """
    source = report_source()
    assert source.count("read_story") == 1
    assert "story_coordinator.read_story" in source
    assert "story_coordinator.story_title" in source


def test_no_call_site_added_by_this_story_names_a_yaml_parser():
    """The story dialect is not YAML, and a second reader of an artifact would
    be a second answer to what the artifact says.

    The control is below: the same scanner over a source that does name one
    reports it, so this is a fact about the source rather than about a scanner
    that has stopped seeing anything.
    """
    assert other_readers(report_source()) == []
    assert other_readers(conftest.function_source(
        Path(story_coordinator.__file__).read_text(encoding="utf-8"),
        "story_title")) == []


def test_the_scanner_reports_the_readers_it_exists_to_catch():
    """The control for both absences above."""
    for named in OTHER_READERS:
        planted = report_source().replace(
            "story_coordinator.read_story", f"{named}load", 1)
        assert other_readers(planted) == [named], named


def test_the_bounded_title_function_reads_no_artifact_of_its_own():
    """It is handed a parse, so it cannot become a second reader.

    Stated as what its source does not do — open a file, read text, or parse
    one — with the control immediately above showing this scanner sees what it
    looks for.
    """
    source = conftest.function_source(
        Path(story_coordinator.__file__).read_text(encoding="utf-8"),
        "story_title")
    for reading in ("read_text", "open(", "Path("):
        assert reading not in source, reading
