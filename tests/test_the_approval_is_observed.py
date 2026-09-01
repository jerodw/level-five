"""story-088 validation: the approval is observed, not inferred.

The subject is the question `l5-plan` asks between the end of a planning
session and the first thing it stamps. Before this story the mandate was
stamped on one condition — a terminal was attached and the session created a
file — so a present developer who said no was indistinguishable from one who
said yes. What is validated here is that the block is now the record of an
answer the process read.

What is asserted, and at what altitude:

* **The decision** — `plan_mandate.approved` — is driven directly against
  streams this module constructs, because it is a function over a stream and
  needs no repository, no session and no clock. The affirmative replies are
  read off `plan_mandate.APPROVALS` rather than spelled, so a change to what
  answers yes moves these cases with it.

* **The strip** — `plan_mandate.strip_mandate` — is driven directly against
  each block extent the story names, with the artifact around the block
  compared byte for byte in every case. This is the assertion the story stands
  on, so the cases are a table rather than prose: a block at end of file, one
  followed by another top-level key, one carrying a blank line, one carrying a
  comment line, the nested `source` mapping, and the block `plan_mandate`
  itself composes.

* **The script** is driven end to end as a subprocess, through the throwaway
  planning repository and stub `claude` that `tests/test_plan_commit.py`
  builds and `tests/test_story_mandate.py` already reuses. Those fixtures are
  imported rather than rebuilt: what this module adds is the answer given to
  the question and what is asked of the repository afterwards.

* **The shipped prompt** is read off the file this repository ships, through
  the template loader and normative-sentence detector
  `tests/test_planner_injection.py` already owns. The assertions sit here
  rather than beside that detector because that module is one of the four
  `tests/test_shared_baseline_resolution.py` holds to exactly the test set its
  origin shipped, so a story adding a test to it turns that guard red.

Every absence asserted here carries a demonstration that it can fail:

* "a rejection stamped, committed and pushed nothing" is a byte comparison of
  the artifact and an unmoved HEAD and unmoved remote refs, and sits beside
  the same fixture answered yes, where all three move;
* "a rejection removed nothing, including a session-written block" sits beside
  the same fixture approved, where that block is what gets discarded;
* "a stream that is not a terminal is never read" sits beside the same stream
  claiming to be one, where the read happens and raises;
* "`confer` reads no stream" is driven with `sys.stdin` replaced by an object
  that raises on any use, and sits beside `approved` under the same
  replacement, where it raises;
* "the strip removed the block and nothing else" is an equality against the
  artifact the case was built from, so a strip that removed too much or too
  little fails as itself;
* "the strip never reconstructs the artifact from a parse" is driven against
  an artifact no parser accepts, which a reconstructing implementation could
  not return at all;
* "the added prose states nothing normative" sits beside the same paragraph
  rewritten into the normative spelling it was written to avoid, where the
  same detector call reports it.

Two guarantees this story restates are held by scans that already exist and
are not duplicated here: that `report` writes nothing and removes nothing
(`tests/test_plan_time_validation.py`), and that the run offer's own reply is
still read by the offer (`tests/test_plan_run_offer.py`).

No model is invoked anywhere in this file, and nothing here resolves a
baseline out of this repository's commit graph.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import harness_config
import plan_mandate
import plan_run_offer
import story_coordinator

import conftest
from test_plan_commit import (
    Planning,
    artifact,
    bare_remote,
    committed_paths,
    conferring_paths,
    drain,
    make_planning,
    remote_refs,
    run_plan,
    run_plan_on_a_pty,
    writes,
)
#: The shipped template, the schema it injects and the normative-sentence
#: detector, read through the module whose subject they are rather than
#: resolved a second time here.
from test_planner_injection import (
    PLACEHOLDER,
    normative_sentences,
    planner_template,
    rendered_planner_prompt,
    story_schema,
)
from test_story_mandate import (
    CONFERRED_LOG,
    PLANNED_ID,
    PLANNED_REL,
    conferring_records,
    plan_without_a_terminal,
    session_writing,
)

L5_PLAN = Path(story_coordinator.__file__).resolve().parents[1] / "scripts" / "l5-plan"


@pytest.fixture
def planning(tmp_path: Path) -> Planning:
    """The planning repository with a bare origin to push to.

    The same fixture `tests/test_story_mandate.py` uses, for the same reason:
    a session that is refused, approved or rejected has to be able to get as
    far as the push before the difference between those three is observable.
    """
    made = make_planning(tmp_path)
    made.remote = bare_remote(tmp_path, made, upstream=True)
    return made


# --------------------------------------------------------------------------
# The decision: what answers yes, what answers no, and what is never read
# --------------------------------------------------------------------------


class Answering:
    """A stream that claims to be a terminal and answers with `reply`.

    Constructed rather than driven through a pty because the two cases that
    matter most cannot be produced on one: end of input on a terminal means
    closing the master, which makes the slave's read fail rather than return
    an empty string, and "never read" needs a read that is observable when it
    happens. Both are properties of `approved`'s own two lines, and a stream
    is all either of them takes.
    """

    def __init__(self, reply: str, *, terminal: bool = True):
        self.reply = reply
        self.terminal = terminal
        self.reads = 0

    def isatty(self) -> bool:
        return self.terminal

    def readline(self) -> str:
        self.reads += 1
        return self.reply


@pytest.mark.parametrize("reply", sorted(plan_mandate.APPROVALS))
def test_every_reply_the_module_calls_an_approval_approves(reply: str):
    """Read off `APPROVALS` rather than spelled, in both spellings a developer
    types: the reply as it stands and the reply as a terminal delivers it,
    with a newline and whatever case they used."""
    assert plan_mandate.approved(Answering(reply))
    assert plan_mandate.approved(Answering(f"{reply.upper()}\n"))
    assert plan_mandate.approved(Answering(f"  {reply}  \n"))


@pytest.mark.parametrize("reply", ["\n", "n\n", "no\n", "later\n", "yep\n",
                                   "y n\n"])
def test_everything_else_rejects_including_the_empty_line(reply: str):
    """Enter alone is a rejection, which is the opposite of the run offer.

    The run offer risks a run nobody wanted and reads Enter as yes; this risks
    a mandate nobody conferred, so the silence a developer leaves by pressing
    Enter is not an approval.
    """
    assert not plan_mandate.approved(Answering(reply))


def test_the_two_questions_read_one_line_the_opposite_way():
    """Stated as the difference it is, so neither default can drift into the
    other unnoticed: the same empty line runs the story at the offer and
    confers nothing at the approval."""
    assert plan_run_offer.should_run(Answering("\n"))
    assert not plan_mandate.approved(Answering("\n"))


def test_end_of_input_at_the_approval_prompt_is_a_rejection():
    """The same one-directional bias `can_prompt` already takes.

    An input that ended is an input with nobody behind it, and answering yes
    to that is exactly the inference this story removes.
    """
    ended = Answering("")
    assert not plan_mandate.approved(ended)
    assert ended.reads == 1, "end of input must be read once, not re-asked"


def test_a_stream_that_is_not_a_terminal_is_never_read():
    """Nothing is read, rather than read and found wanting.

    The control is immediately below: the same stream carrying the same reply,
    claiming to be a terminal, is read — so the silence here is the terminal
    test's and not the stream's.
    """
    piped = Answering(plan_mandate.APPROVALS[0], terminal=False)
    assert not plan_mandate.approved(piped)
    assert piped.reads == 0

    terminal = Answering(plan_mandate.APPROVALS[0], terminal=True)
    assert plan_mandate.approved(terminal)
    assert terminal.reads == 1


def test_the_terminal_test_is_the_offer_s_own_rather_than_a_second_spelling():
    """One question, one answer. A second `isatty` reading here could disagree
    with the offer's, and the two would then differ on whether a developer is
    present."""
    source = conftest.function_source(
        (Path(plan_mandate.__file__)).read_text(encoding="utf-8"), "approved")
    assert "plan_run_offer.can_prompt" in source
    assert "isatty" not in source


# --------------------------------------------------------------------------
# The strip: the block removed, and every other byte where it was
# --------------------------------------------------------------------------


#: What a session writes: the artifact with no block at all.
BODY = artifact(PLANNED_ID)

#: The title `artifact` gives that story. Since story-097 the run offer names it
#: after the id, so the question these assertions look for is no longer the id
#: followed immediately by the word `now`.
PLANNED_TITLE = "Stub planned story"

#: The run offer's question as the script now writes it.
RUN_OFFER = f"run {PLANNED_ID}: {PLANNED_TITLE} now?"

#: The blank line separating the artifact from whatever was appended after it.
#: It is not part of the block — the block's extent starts at its key line — so
#: it is one of the bytes the strip has to leave behind, and putting it on this
#: side of the seam is what states that.
SEPARATOR = "\n"

#: What every case below carries before the block: the artifact and that
#: separator. So what each case asks the strip to give back is something this
#: module already holds.
PRECEDING = BODY + SEPARATOR

#: A top-level key after the block, with its own blank line ahead of it. That
#: blank line is the case the extent rule turns on: it is met while the block
#: is still open, and must come back out, because it belongs to what follows
#: rather than to what was removed.
FOLLOWING_KEY = "\nnotes:\n  - a top-level key the session wrote after it\n"


def key_line_onwards(block: str) -> str:
    """A composed block from its key line, with the separator ahead of it cut.

    `plan_mandate.block` and `conftest.MANDATE_BLOCK` both open with the blank
    line that separates the block from the artifact, because both are written
    to be appended. A case that is about the extent puts that separator in
    `PRECEDING` instead, where the assertion can say it survives.
    """
    assert block.startswith(SEPARATOR)
    return block[len(SEPARATOR):]


#: Each extent the story names, as (the block, what follows it). What precedes
#: the block is `PRECEDING` in every row, so a row states only what it varies.
EXTENTS = {
    "at end of file": (key_line_onwards(conftest.MANDATE_BLOCK), ""),
    "followed by another top-level key": (
        key_line_onwards(conftest.MANDATE_BLOCK), FOLLOWING_KEY),
    "carrying a blank line": (
        "mandate:\n"
        "  source:\n"
        "    kind: human\n"
        "\n"
        "  conferred_at: 2026-08-29 07:00:00\n"
        "  conferred_by: A Developer <developer@example.com>\n"
        "  recorded_by: l5-plan\n",
        FOLLOWING_KEY,
    ),
    "carrying a comment line": (
        "mandate:\n"
        "  source:\n"
        "    kind: human\n"
        "  # the session even explained itself here\n"
        "  conferred_at: 2026-08-29 07:00:00\n"
        "  conferred_by: A Developer <developer@example.com>\n"
        "  recorded_by: l5-plan\n",
        FOLLOWING_KEY,
    ),
    "with a nested source mapping": (
        "mandate:\n"
        "  source:\n"
        "    kind: xyzzy-record\n"
        "    id: xyzzy-source-1\n"
        "    origin:\n"
        "      recorded_by: something further down\n"
        "  conferred_at: 2026-08-29 07:00:00\n"
        "  conferred_by: ''\n"
        "  recorded_by: l5-plan\n",
        FOLLOWING_KEY,
    ),
    "as this process composes it": (
        key_line_onwards(plan_mandate.block(
            "A Developer <developer@example.com>", "2026-08-29 07:00:00")),
        "",
    ),
}


@pytest.mark.parametrize("extent", sorted(EXTENTS))
def test_the_strip_removes_the_block_and_leaves_every_other_byte(extent: str):
    """The assertion this story stands on, for each extent it names.

    The comparison is an equality against the text the case was assembled
    from, so a strip that swallowed a blank line either side of the block,
    stopped short at a comment inside it, or ran on past a following top-level
    key fails as itself rather than as a difference somebody has to spot.
    """
    block, tail = EXTENTS[extent]
    carrying = PRECEDING + block + tail
    assert plan_mandate.carries_a_mandate(carrying), extent

    stripped = plan_mandate.strip_mandate(carrying)
    assert stripped == PRECEDING + tail, extent
    assert not plan_mandate.carries_a_mandate(stripped), extent


@pytest.mark.parametrize("wrong", [
    pytest.param(lambda text: text.replace(SEPARATOR + FOLLOWING_KEY,
                                           FOLLOWING_KEY),
                 id="the separator ahead of the block taken too"),
    pytest.param(lambda text: text + "  recorded_by: l5-plan\n",
                 id="the last line of the block left behind"),
])
def test_the_comparison_above_reports_a_strip_of_the_wrong_extent(wrong):
    """The control for every equality in the table.

    An equality passes as readily against a subject that cannot differ as
    against one that does, so the same comparison is shown reporting both ways
    the extent can be wrong: one byte too many taken from the front, and one
    line too few taken from the back.
    """
    block, tail = EXTENTS["followed by another top-level key"]
    stripped = plan_mandate.strip_mandate(PRECEDING + block + tail)
    assert stripped == PRECEDING + tail
    assert stripped != wrong(PRECEDING + tail)


#: An artifact whose formatting nothing that parses and re-serialises could
#: give back: a comment, a run of blank lines, trailing whitespace, a tab, and
#: a quoted scalar that needs no quoting. It is not the story dialect and is
#: not meant to be — the strip is a line scan, and what it is being shown here
#: is that it neither needs nor consults a parse.
UNPARSEABLE = (
    "# what the session wrote, formatted its own way\n"
    "story:\n"
    "  id: 'story-900'   \n"
    "\n"
    "\n"
    "\tan indented line no parser accepts: ][\n"
    "  title: \"Stub planned story\"\n"
)


def test_the_strip_never_reconstructs_the_artifact_from_a_parse():
    """An artifact no parser accepts comes back whole, block removed.

    An implementation that loaded the artifact to remove one key could not
    return this text at all, and one that dumped what it loaded would return
    it reformatted — the comment gone, the blank run collapsed, the quoting
    normalised. Byte equality is what says neither happened.
    """
    preceding = UNPARSEABLE + SEPARATOR
    carrying = preceding + key_line_onwards(conftest.MANDATE_BLOCK) \
        + FOLLOWING_KEY
    assert plan_mandate.carries_a_mandate(carrying)
    assert plan_mandate.strip_mandate(carrying) == preceding + FOLLOWING_KEY


def test_a_text_carrying_no_block_comes_back_unchanged():
    """The other half of "removes the block and nothing else": with no block
    to remove, nothing is."""
    assert plan_mandate.strip_mandate(BODY) == BODY
    assert plan_mandate.strip_mandate(UNPARSEABLE) == UNPARSEABLE


def test_an_indented_mandate_key_is_not_the_block():
    """`mandate:` nested under something else is a key of that thing.

    The block the declaration puts at the top level is what the strip is
    about, and a line that is indented is not it.
    """
    nested = BODY + "\nnotes:\n  mandate:\n    kind: something else\n"
    assert not plan_mandate.carries_a_mandate(nested)
    assert plan_mandate.strip_mandate(nested) == nested


# --------------------------------------------------------------------------
# `confer` observes nothing itself: it is handed a decision already taken
# --------------------------------------------------------------------------


class Unusable:
    """A stand-in for `sys.stdin` that fails on any use at all."""

    def __getattr__(self, name):
        raise AssertionError(f"a stream was consulted for {name!r}")


def a_repository_with_an_identity(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir(parents=True)
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.name", "Xyzzy Approver"],
        ["git", "config", "user.email", "xyzzy@example.com"],
    ):
        subprocess.run(command, cwd=root, check=True)
    return root


def test_confer_neither_prompts_nor_reads_any_stream(tmp_path, monkeypatch):
    """The seam a process holding an approval of some other kind confers on.

    `sys.stdin` is replaced by something that raises on any use, and the
    conferral happens anyway. The control is immediately below: under the same
    replacement, the function that *does* ask raises.
    """
    monkeypatch.setattr(sys, "stdin", Unusable())
    root = a_repository_with_an_identity(tmp_path, "conferring")
    path = root / f"{PLANNED_ID}.yaml"
    path.write_text(BODY, encoding="utf-8")

    conferred = plan_mandate.confer(path, root, now=0)
    assert conferred.stamped
    assert conferred.conferred_by == "Xyzzy Approver <xyzzy@example.com>"
    assert not conferred.discarded_block

    with pytest.raises(AssertionError):
        plan_mandate.approved(sys.stdin)


def test_confer_told_to_discard_strips_the_block_before_appending_its_own(
        tmp_path):
    """The developer authorized the discard, so the refusal is not triggered.

    What is left is the session's own bytes with exactly one block after them,
    and that block is the one this process composed.
    """
    root = a_repository_with_an_identity(tmp_path, "discarding")
    path = root / f"{PLANNED_ID}.yaml"
    path.write_text(BODY + conftest.MANDATE_BLOCK, encoding="utf-8")

    conferred = plan_mandate.confer(path, root, now=0, discarding=True)
    assert conferred.stamped
    assert conferred.discarded_block
    # The session's own bytes, the separator that was always between them and
    # what followed, and one block — this process's.
    assert path.read_text(encoding="utf-8") == BODY + SEPARATOR + \
        plan_mandate.block(conferred.conferred_by, conferred.conferred_at)


def test_confer_not_told_to_discard_still_refuses_and_touches_nothing(tmp_path):
    """The control for the discard above, and story-087's refusal unchanged.

    `discarding` is the whole of the difference: the same artifact, the same
    repository, the same call without it, and nothing is written.
    """
    root = a_repository_with_an_identity(tmp_path, "refusing")
    path = root / f"{PLANNED_ID}.yaml"
    carrying = BODY + conftest.MANDATE_BLOCK
    path.write_text(carrying, encoding="utf-8")

    conferred = plan_mandate.confer(path, root, now=0)
    assert not conferred.stamped
    assert not conferred.discarded_block
    assert "already carries a mandate block" in conferred.detail
    assert path.read_text(encoding="utf-8") == carrying


# --------------------------------------------------------------------------
# The script: what an approval does, and what a rejection leaves
# --------------------------------------------------------------------------


#: A reply that rejects. Anything that is not an approval does, so it is
#: derived from what approves rather than picked: a spelling that quietly
#: became affirmative would otherwise turn every rejection test below into an
#: approval test that still passed.
REJECTS = "no\n"
assert REJECTS.strip().lower() not in plan_mandate.APPROVALS


def reject_plan(planning: Planning, **stub) -> subprocess.CompletedProcess:
    """The real script on a terminal, answered with a rejection.

    The shared fixture's reply approves, because almost every module driving
    the script needs to get past this question to reach its own subject. This
    module's subject *is* the question, so the reply is stated here.
    """
    with conftest.a_terminal_for_stdin(reply=REJECTS) as stdin:
        return subprocess.run(
            [sys.executable, str(L5_PLAN), "--workflow", "story-workflow",
             "a story request"],
            cwd=planning.root, env=planning.env(**stub), stdin=stdin,
            capture_output=True, text=True)


def history_log(planning: Planning) -> Path:
    return planning.root / harness_config.DEFAULT_HISTORY_DIR / CONFERRED_LOG


def test_a_rejection_stamps_commits_and_pushes_nothing(planning: Planning):
    """Compared by the bytes rather than read off the message.

    A message saying nothing happened is not evidence that nothing happened,
    so what is asserted is the artifact's own bytes, an unmoved HEAD, unmoved
    remote refs and an absent conferring log. The control is the test below,
    which is this fixture answered yes.
    """
    head, refs = planning.head(), remote_refs(planning.remote)
    result = reject_plan(planning, L5_STUB_WRITE=session_writing(BODY))

    assert result.returncode == 1
    assert planning.head() == head
    assert remote_refs(planning.remote) == refs
    assert (planning.root / PLANNED_REL).read_bytes() == BODY.encode("utf-8")
    assert PLANNED_REL in planning.status()
    assert not history_log(planning).exists()


def test_the_same_fixture_approved_stamps_commits_and_pushes(planning: Planning):
    """The control for the rejection above.

    Nothing about the invocation changes but the answer, so the answer is what
    decided it.
    """
    head, refs = planning.head(), remote_refs(planning.remote)
    assert run_plan(planning, L5_STUB_WRITE=session_writing(BODY)).returncode == 0

    assert planning.head() != head
    assert remote_refs(planning.remote) != refs
    assert plan_mandate.carries_a_mandate(
        (planning.root / PLANNED_REL).read_text(encoding="utf-8"))
    assert history_log(planning).exists()


def test_a_rejection_says_where_the_artifacts_are_and_what_removes_them(
        planning: Planning):
    """What a developer is owed by a refusal that leaves work behind.

    Each path, that they are unstamped and uncommitted, what leaving them
    costs at the next run's clean-tree pre-flight, and the command that
    removes them — handed over rather than run, which the byte comparison
    above is what proves.
    """
    result = reject_plan(planning, L5_STUB_WRITE=session_writing(BODY))
    printed = result.stdout

    assert PLANNED_REL in printed or str(planning.root / PLANNED_REL) in printed
    assert "unstamped and uncommitted" in printed
    assert "clean-tree" in printed and "pre-flight" in printed
    assert f"{plan_mandate.REMOVE_COMMAND} " in printed


def test_a_rejection_removes_nothing_including_a_block_the_session_wrote(
        planning: Planning):
    """Leave-and-warn leaves everything.

    The block is the one thing on this path a developer might expect to be
    tidied away, and it is exactly the thing that must not be: a rejection is
    not an authorization to change the artifact in any direction.
    """
    forged = BODY + conftest.MANDATE_BLOCK
    result = reject_plan(planning, L5_STUB_WRITE=session_writing(forged))

    assert result.returncode == 1
    assert (planning.root / PLANNED_REL).read_bytes() == forged.encode("utf-8")
    assert plan_mandate.carries_a_mandate(
        (planning.root / PLANNED_REL).read_text(encoding="utf-8"))
    assert not history_log(planning).exists()


def test_the_approval_is_all_or_nothing_across_the_session_s_artifacts(
        planning: Planning):
    """One answer for the session, because there is one plan to approve.

    A partial commit would invent a split the session did not have, which is
    the rule the artifact commit already follows.
    """
    second = artifact("story-901", title="A second planned story")
    head = planning.head()
    result = reject_plan(planning, L5_STUB_WRITE=writes(
        (PLANNED_REL, BODY),
        (".harness/stories/story-901.yaml", second)))

    assert result.returncode == 1
    assert planning.head() == head
    assert (planning.root / PLANNED_REL).read_bytes() == BODY.encode("utf-8")
    assert (planning.root / ".harness" / "stories" / "story-901.yaml"
            ).read_bytes() == second.encode("utf-8")
    assert not history_log(planning).exists()


def expected_steps(planning: Planning) -> list[str]:
    """What an approved session prints, in the order the script does it.

    Derived where the wording is composed elsewhere — the story id, the log a
    conferring record reaches, the artifact commit subject — so a change to any
    of those moves this with it rather than being restated here.
    """
    return [
        f"{PLANNED_ID} runs on a mandate conferred by",
        f"committed {', '.join(conferring_paths(planning))} as ",
        f"committed {PLANNED_REL} as Plan {PLANNED_ID}:",
        "pushed main to",
        RUN_OFFER,
    ]


def test_an_approval_produces_the_same_steps_in_the_same_order(
        planning: Planning):
    """The common path is not a new path.

    Stamp, conferring-record commit, artifact commit, push and run offer, each
    said once and in that order, with the approval question the only thing
    between the session's end and the first of them. The base check and the
    validation sit in that sequence too and print nothing when they pass;
    their own refusals are the subject of `tests/test_branch_base.py` and
    `tests/test_plan_time_validation.py`.
    """
    result = run_plan(planning, L5_STUB_WRITE=session_writing(BODY))
    assert result.returncode == 0, result.stdout + result.stderr
    printed = result.stdout

    positions = []
    for step in expected_steps(planning):
        assert printed.count(step) == 1, step
        positions.append(printed.index(step))
    assert positions == sorted(positions), printed

    question = "approve this plan?"
    assert printed.count(question) == 1
    assert printed.index(question) < positions[0]


def test_the_conferred_time_is_when_the_approval_was_read(planning: Planning):
    """Not when the session ended, which is the whole difference.

    The reply is held back after the session has already written its artifact
    and exited, so the two moments are separated by a measurable gap. The hold
    begins when the session's end is observed rather than when the process was
    spawned — the artifact appearing on disk is what the script itself takes
    for the end of the session, and waiting for it keeps the gap the length of
    the hold instead of the hold minus however long the interpreter took to
    start.

    The slack allowed against the hold is what the measurement loses on its
    own: a conferring timestamp carries whole seconds, so up to one is lost to
    truncation, and the moment the end was observed is at or after the moment
    the artifact was written.
    """
    held = 4
    written = planning.root / PLANNED_REL
    process, master = run_plan_on_a_pty(planning,
                                        L5_STUB_WRITE=session_writing(BODY))
    deadline = time.monotonic() + 60
    while not written.exists():
        assert time.monotonic() < deadline, "the session never wrote it"
        time.sleep(0.05)
    session_ended = time.time()
    time.sleep(held)
    os.write(master, (conftest.APPROVES + conftest.DECLINES).encode())
    status, output = drain(process, master)
    assert status == 0, output

    (record,) = conferring_records(planning.root,
                                   harness_config.DEFAULT_HISTORY_DIR)
    conferred_at = time.mktime(
        time.strptime(record["timestamp"], plan_mandate.TIMESTAMP_FORMAT))
    assert conferred_at - session_ended >= held - 2, (record, session_ended)
    # The block and the record are one act, so the block moved with it.
    assert f"conferred_at: {record['timestamp']}" in (
        planning.root / PLANNED_REL).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# A block the session wrote: found before the question, folded into it
# --------------------------------------------------------------------------


def test_a_session_written_block_is_put_to_the_developer_as_a_decision(
        planning: Planning):
    """Neither repaired quietly nor refused outright where there is somebody
    to ask.

    The developer is told the block is there and that approving discards it,
    and both are said before the question — an agent that ignored the one
    instruction about the one field it must not write is an agent whose
    remaining output deserves a closer read.
    """
    result = run_plan(planning,
                      L5_STUB_WRITE=session_writing(BODY + conftest.MANDATE_BLOCK))
    assert result.returncode == 0, result.stdout + result.stderr
    printed = result.stdout

    told = printed.index(f"carries a {plan_mandate.MANDATE_KEY} block")
    discards = printed.index("approving discards that block")
    assert told < discards < printed.index("approve this plan?")


def test_approving_a_carried_block_discards_it_and_stamps_exactly_one(
        planning: Planning):
    """One block, this process's, over the session's own bytes.

    What is compared is the whole artifact: the session's text with its block
    removed, followed by the block `plan_mandate` composed. So neither the
    session's block surviving nor a second block being appended can pass.
    """
    assert run_plan(planning, L5_STUB_WRITE=session_writing(
        BODY + conftest.MANDATE_BLOCK)).returncode == 0

    committed = subprocess.run(
        ["git", "-C", str(planning.root), "show", f"HEAD:{PLANNED_REL}"],
        capture_output=True, text=True, check=True).stdout
    (record,) = conferring_records(planning.root,
                                   harness_config.DEFAULT_HISTORY_DIR)
    assert committed == BODY + SEPARATOR + plan_mandate.block(
        record["conferred_by"], record["timestamp"])
    assert committed_paths(planning.root) == [PLANNED_REL]


def test_the_conferring_record_says_the_conferral_discarded_a_block(
        planning: Planning):
    """True where one was discarded, false where none was.

    Both halves, because a field written only on the true case would leave a
    reader unable to tell a false from a record written before the field
    existed — which is what the declaration says and what is asserted below it.
    """
    assert run_plan(planning, L5_STUB_WRITE=session_writing(
        BODY + conftest.MANDATE_BLOCK)).returncode == 0
    (discarding,) = conferring_records(planning.root,
                                       harness_config.DEFAULT_HISTORY_DIR)
    assert discarding["discarded_session_block"] is True
    assert discarding["story_id"] == PLANNED_ID


def test_a_conferral_onto_a_clean_artifact_says_it_discarded_nothing(
        planning: Planning):
    """The control for the record above, from the other side."""
    assert run_plan(planning, L5_STUB_WRITE=session_writing(BODY)).returncode == 0
    (clean,) = conferring_records(planning.root,
                                  harness_config.DEFAULT_HISTORY_DIR)
    assert clean["discarded_session_block"] is False


def conferring_declaration() -> dict:
    """The declaration of the log a conferring record reaches.

    Read off the schema through the same routing the append uses, so the field
    is asserted where it is declared rather than where a test remembers it
    being.
    """
    return story_coordinator.history_log_declarations()[CONFERRED_LOG]


def test_the_field_the_record_carries_is_declared_by_the_schema():
    """A record carrying a field no declaration names would be a record whose
    reader has nowhere to learn what it means."""
    declared = conferring_declaration()["properties"]
    assert "discarded_session_block" in declared
    assert declared["discarded_session_block"]["type"] == "boolean"


def test_the_declaration_says_a_rejection_writes_no_record_at_all():
    """So the absence of a line is read as the mechanism working.

    A log holding conferrals has nothing to say about a plan nobody conferred,
    and a reader who did not know that would read a gap.
    """
    description = conferring_declaration()["properties"][
        "discarded_session_block"]["description"].lower()
    assert "rejection writes no record" in description


# --------------------------------------------------------------------------
# The headless paths: what this story closes, and what it leaves as it was
# --------------------------------------------------------------------------


def test_a_headless_session_written_block_commits_nothing(planning: Planning):
    """The path this story closes.

    With no terminal there is no developer to put the block to, so it is
    refused above the stamp rather than resolved beneath it — which is what a
    headless l5-plan committing a block no process observed would have been.
    The detection sits above the terminal branch, which is what makes this
    reachable at all.
    """
    forged = BODY + conftest.MANDATE_BLOCK
    head = planning.head()
    result = plan_without_a_terminal(planning,
                                     L5_STUB_WRITE=session_writing(forged))

    assert result.returncode == 1
    assert planning.head() == head
    assert (planning.root / PLANNED_REL).read_bytes() == forged.encode("utf-8")
    assert not history_log(planning).exists()
    assert "stamped nothing and committed nothing" in result.stdout


def test_a_headless_session_with_no_block_behaves_as_story_087_left_it(
        planning: Planning):
    """Nothing stamped, the artifact refused at validation, the human named.

    Unchanged by this story, and asserted here because the detection moved
    above the terminal branch: an invocation carrying no block must fall
    through that detection to exactly where it fell through before.
    """
    head = planning.head()
    result = plan_without_a_terminal(planning,
                                     L5_STUB_WRITE=session_writing(BODY))

    assert result.returncode == 1
    assert planning.head() == head
    assert (planning.root / PLANNED_REL).read_bytes() == BODY.encode("utf-8")
    assert plan_mandate.MANDATE_KEY in result.stderr
    assert "no human present" in result.stdout
    assert "no terminal" in result.stdout
    # The refusal it gets is the schema's, which is the outcome story-087
    # chose: the block it does not carry is what refuses it.
    assert "approve this plan?" not in result.stdout


def test_the_headless_invocation_reads_nothing_at_all(planning: Planning,
                                                      tmp_path: Path):
    """Neither question is asked where there is no terminal.

    The control is the same session in a second repository with a terminal,
    where both are asked. It is a second repository rather than a second run in
    this one because what a session produced is decided by what *appeared*
    under the stories directory: an artifact the first invocation left behind
    is not new to the second, which would then have no session output to
    approve and would ask nothing for that reason instead.
    """
    result = plan_without_a_terminal(planning,
                                     L5_STUB_WRITE=session_writing(BODY))
    assert "approve this plan?" not in result.stdout
    assert RUN_OFFER not in result.stdout

    second = make_planning(tmp_path / "with-a-terminal")
    second.remote = bare_remote(tmp_path / "with-a-terminal", second,
                                upstream=True)
    approving = run_plan(second, L5_STUB_WRITE=session_writing(BODY))
    assert approving.returncode == 0, approving.stdout + approving.stderr
    assert "approve this plan?" in approving.stdout
    assert RUN_OFFER in approving.stdout


# --------------------------------------------------------------------------
# The shipped prompt: the one part of the contract the planner does not write
#
# The schema is injected whole, and it declares the mandate block like every
# other part of the contract. A planner that dutifully writes the field it is
# shown has its whole session's output refused for a reason nothing it was
# given could tell it, so the prompt says why the block is not its to write.
#
# That prose is added *beside* the injection rather than by editing what is
# injected, which is what the assertions here are about in both directions:
# the instruction is in the shipped template, and the schema reaching the
# planner is still the schema file itself, still declaring the field.
#
# The template, the schema and the detector are read through
# `tests/test_planner_injection.py`, whose subject they are, rather than
# resolved a second time here — and the assertions live here rather than there
# because that module is one of the four `tests/test_shared_baseline_
# resolution.py` holds to the test set its origin shipped.
# --------------------------------------------------------------------------


#: The block's key, from the module that writes it, so the prose is searched
#: for under the name the process actually stamps.
MANDATE_KEY = plan_mandate.MANDATE_KEY


def mandate_paragraphs(text: str | None = None) -> list[str]:
    """The template's own paragraphs about the block, injection excluded.

    Split on blank lines and taken from the prose, so what is read is what the
    template says rather than what the schema says inside it: the injected
    schema is one long line and mentions the field repeatedly, and a search
    over the whole rendered prompt would find those instead.
    """
    prose = PLACEHOLDER.sub("", planner_template() if text is None else text)
    return [" ".join(paragraph.split())
            for paragraph in prose.split("\n\n")
            if MANDATE_KEY in paragraph.lower()]


def test_the_template_tells_the_planner_the_block_is_not_its_to_write():
    """Read off the shipped file, so the fix cannot have been made elsewhere.

    Three things a planner needs and cannot derive: who writes the block, what
    happens to an artifact that arrives carrying one, and that its absence from
    what the session writes is the correct outcome rather than an omission.
    """
    (paragraph,) = mandate_paragraphs()
    lowered = paragraph.lower()
    assert "written by the harness process that observed" in lowered
    assert "refused" in lowered
    assert "correct rather than an omission" in lowered


def test_the_instruction_sits_beside_the_injection_rather_than_inside_it():
    """Added after the schema, and nothing removed from what is injected.

    The schema is injected so the prompt cannot hold a copy that drifts, so a
    paragraph explaining one of its fields has to sit outside it — and the
    field it explains must still be declared in the schema the planner is
    handed.
    """
    head, marker, tail = planner_template().partition("{{story_schema}}")
    assert marker
    assert mandate_paragraphs(head) == []
    assert mandate_paragraphs(tail) == mandate_paragraphs()

    schema = story_schema()
    assert MANDATE_KEY in schema["properties"]
    assert MANDATE_KEY in schema["required"]
    assert MANDATE_KEY in rendered_planner_prompt()


def test_the_added_prose_states_nothing_normative_of_its_own():
    """The same detector the whole template is held to, over the paragraph.

    `tests/test_planner_injection.py` already runs this detector over the whole
    template and would go red for a sentence added anywhere; this narrows the
    subject to the prose this story added, so a failure says which paragraph
    rather than only that one exists.
    """
    for paragraph in mandate_paragraphs():
        assert normative_sentences(paragraph) == [], paragraph


def test_the_detector_reports_a_normative_spelling_of_the_same_paragraph():
    """Control for the empty result above.

    A detector that had stopped seeing anything — because the paragraph was
    resolved to nothing, or because the words it looks for changed — would
    return the same empty list. So the added paragraph is rewritten into the
    normative spelling it was written to avoid, and the same call reports it.
    """
    (paragraph,) = mandate_paragraphs()
    normative = f"The {MANDATE_KEY} block is a required top-level key. " \
                + paragraph
    flagged = normative_sentences(normative)
    assert flagged, normative
    assert any(MANDATE_KEY in sentence.lower() for sentence in flagged)
