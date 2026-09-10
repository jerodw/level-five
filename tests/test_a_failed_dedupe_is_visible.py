"""story-131 validation: a failed dedupe is visible as a failure.

An inspection whose filed query could not answer files what it found anyway —
a query that could not answer costs dedupe and costs nothing else — and until
this story the only sign of it was a trailing clause on the end of one long
summary line. That is how the tracker tier stayed broken from story-101 to
story-130 without anybody reading it as the standing failure it was. This story
gives it a line of its own in the run's events.log, a field on the durable
record, and a block of its own in what `l5-inspect` prints.

The subjects are kept apart deliberately:

  * **the run's events.log.** A run driven with a filed-query command that
    refuses, whose own words this module wrote, so the reason on the line is
    traceable to the command that produced it. The line names the scope and
    the reason, and the summary line beside it still carries its counts and its
    own trailing clause — this is an addition rather than a move.

  * **the durable record, from both modes.** The narrow mode's record is
    written by a whole run and the broad mode's by a whole `l5-inspect`
    inspection, and both are read back out of the log the cross-run history
    declaration routes this kind to, found by that declaration rather than by a
    filename written here.

  * **what the declaration says the field is.** `schemas/cross-run-history.
    schema.json` is a live harness artifact and is the subject of the
    assertions that name it: the field is declared, it is declared a boolean,
    and it says what it means.

  * **what a developer is shown.** Read through `scripts/l5-inspect` itself
    against reports this module constructs, so the wording is read where it
    lives. A failed dedupe is said outside the per-scope lines, and the command
    still exits zero, because the findings were filed.

Every absence asserted here carries a demonstration that it can fail:

  * "the run's events.log carries a line of its own for the failed dedupe" sits
    beside the same run against a command that answers, where that line is not
    there and the summary is;
  * "the record says dedupe did not run" sits beside the same mode against the
    answering command, where the same field on the same record says it did;
  * "the declaration accepts the record" sits beside the same record with a
    dedupe_ran that is not a boolean, which the same declaration reports;
  * "the report says dedupe failed outside the per-scope lines" sits beside the
    same report with the same scope answered, which prints no such block.

Nothing here reaches a model: the fake runner both modes are driven against is
`tests/test_an_inspection_records_what_it_cost.py`'s, whose autouse guard fails
any test in this module that reaches the real one. Nothing here reaches a
tracker either: the filed-query commands are two files this module wrote.
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path

import conftest
import filed_query
import inspection
import story_inspection

from test_an_inspection_records_what_it_cost import (  # noqa: F401 - shared
    ROOMY_CAP,                                        # idioms and fixtures
    STORY_ID,
    broad_inspection,
    build_target,
    finding,
    harness,
    messages,
    narrow_run,
    no_model,
    problems_with,
    records,
    routed_logs,
)

#: What the two filed-query commands this module writes say and where they are
#: written. The refusing one's words are this module's own, so a reason quoted
#: back on an events.log line is traceable to the command that produced it
#: rather than to anything the harness might have supplied.
THE_TRACKER_REFUSED = "the tracker refused to answer"
QUERY_COMMAND = "queries/what-is-filed.sh"

REFUSES = f"#!/bin/sh\necho '{THE_TRACKER_REFUSED}' >&2\nexit 3\n"
ANSWERS = "#!/bin/sh\nprintf '%s\\n' '{\"items\":[]}'\n"

#: What a post-story inspection calls the scope it covers, built the way the
#: producer builds it rather than written out, so a scope renamed there is a
#: resolution that follows rather than an assertion left behind.
SCOPE_LABEL = story_inspection.ORIGIN.format(story_id=STORY_ID)

#: The field this story added to the durable record, named once here and
#: derived from nothing: the whole claim is that a record carries it, and a
#: name read out of the record being asserted about would be satisfied by
#: whatever the record happened to hold.
DEDUPE_RAN = "dedupe_ran"


def target_asking(tmp_path: Path, name: str, command: str) -> Path:
    """A target whose inspections ask `command` what is already filed.

    The command is written before the target is built, so it is committed with
    everything else and is there when the run reaches it — the query an
    inspection actually makes rather than one installed underneath a run.
    """
    root = tmp_path / name
    script = root / QUERY_COMMAND
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(command, encoding="utf-8")
    script.chmod(0o755)
    return build_target(root, **{story_inspection.MAX_FILES_KEY: ROOMY_CAP,
                                 filed_query.COMMAND_KEY: QUERY_COMMAND})


def inspection_lines(target: Path) -> list[str]:
    """Every line this run's events.log carries about its inspection."""
    return [line for line in messages(target)
            if line.startswith(f"post-story inspection of {STORY_ID}")]


def summary_of(target: Path) -> str:
    """The one line carrying the counts, which is the summary."""
    said = [line for line in inspection_lines(target) if "finding(s)" in line]
    assert len(said) == 1, messages(target)
    return said[0]


def notes_in(target: Path) -> list[str]:
    """Every inspection line that is not the summary."""
    summary = summary_of(target)
    return [line for line in inspection_lines(target) if line != summary]


def one_record(target: Path) -> dict:
    written = records(target)
    assert len(written) == 1, written
    return written[0]


# ==========================================================================
# The run's events.log
# ==========================================================================


def test_a_query_that_could_not_answer_gets_its_own_line_naming_the_scope(
        tmp_path, harness, monkeypatch):
    """The line this story exists to add, beside the summary rather than
    instead of it.

    The reason it quotes is the refusing command's own words, so what is on the
    line came from the query that failed. The summary is required to still
    carry its counts and its own trailing clause: a reader who was reading that
    clause is reading exactly what they read before.
    """
    target = target_asking(tmp_path, "with-a-refusing-query", REFUSES)
    code, inspector, _runner = narrow_run(target, harness, monkeypatch,
                                          findings=[finding()])

    assert code == 0
    assert inspector.invocations, "the inspection was never attempted"

    notes = notes_in(target)
    assert len(notes) == 1, inspection_lines(target)
    assert SCOPE_LABEL in notes[0], notes[0]
    assert THE_TRACKER_REFUSED in notes[0], notes[0]

    summary = summary_of(target)
    assert "dedupe did not run" in summary, summary
    assert "1 finding(s)" in summary, summary


def test_the_same_run_against_a_query_that_answers_carries_no_such_line(
        tmp_path, harness, monkeypatch):
    """The control for the absence: the same fixture, the same inspection, a
    command that answers instead of refusing.

    Without it, "a line of its own" would be satisfied by a run that says
    nothing at all, and the assertion above could not tell a working query from
    a reader looking in the wrong file.
    """
    target = target_asking(tmp_path, "with-an-answering-query", ANSWERS)
    code, inspector, _runner = narrow_run(target, harness, monkeypatch,
                                          findings=[finding()])

    assert code == 0
    assert inspector.invocations, "the inspection was never attempted"
    assert notes_in(target) == []

    summary = summary_of(target)
    assert "dedupe did not run" not in summary, summary
    assert "1 finding(s)" in summary, summary


# ==========================================================================
# The durable record, from both modes
# ==========================================================================


def test_a_narrow_mode_record_says_whether_dedupe_ran(
        tmp_path, harness, monkeypatch):
    """One read of one tracked file answers it, for a run that has ended.

    The two runs differ in nothing but the command they ask, so the field is
    the query deciding rather than a constant written on every record.
    """
    refused = target_asking(tmp_path, "narrow-refused", REFUSES)
    answered = target_asking(tmp_path, "narrow-answered", ANSWERS)

    assert narrow_run(refused, harness, monkeypatch,
                      findings=[finding()])[0] == 0
    assert narrow_run(answered, harness, monkeypatch,
                      findings=[finding()])[0] == 0

    assert one_record(refused)[DEDUPE_RAN] is False
    assert one_record(answered)[DEDUPE_RAN] is True
    for record in (one_record(refused), one_record(answered)):
        assert record["mode"] == inspection.MODE_NARROW
        assert problems_with(record) == [], record


def test_a_broad_mode_record_says_whether_dedupe_ran(tmp_path, harness):
    """The same field on the same log from the other mode, so which mode wrote
    a line is not something a reader has to know before reading it."""
    refused = target_asking(tmp_path, "broad-refused", REFUSES)
    answered = target_asking(tmp_path, "broad-answered", ANSWERS)

    broad_inspection(refused, harness, findings=[finding()])
    broad_inspection(answered, harness, findings=[finding()])

    assert one_record(refused)[DEDUPE_RAN] is False
    assert one_record(answered)[DEDUPE_RAN] is True
    for record in (one_record(refused), one_record(answered)):
        assert record["mode"] == inspection.MODE_BROAD
        assert problems_with(record) == [], record


def test_an_inspection_that_made_no_invocation_records_that_dedupe_did_not_run(
        tmp_path, harness, monkeypatch):
    """No query answered for it, so false is what its record says.

    The cap is unusable, which the mechanism reports and refuses nothing over —
    so the run completes, nothing is invoked, and a line is still appended. A
    field left off that line would be indistinguishable from a line written
    before the field existed; the control for the false here is the true the
    answering runs above record on the same field.
    """
    target = build_target(tmp_path / "with-an-unusable-cap",
                          **{story_inspection.MAX_FILES_KEY: "lots"})
    code, inspector, _runner = narrow_run(target, harness, monkeypatch,
                                          findings=[finding()])

    assert code == 0
    assert inspector.invocations == []
    record = one_record(target)
    assert record["invocations"] == 0
    assert record[DEDUPE_RAN] is False
    assert problems_with(record) == [], record


def test_the_declaration_declares_the_field_a_boolean_and_says_what_it_means():
    """A shipped schema and the legitimate subject: the claim is about what
    this harness declares a record to be.

    The description is required to say the thing a reader needs and cannot get
    from the value — that a false is an inspection made without the only tier
    that sees what another checkout filed, so what it filed may already be
    filed. A boolean nobody can interpret is a column of true and false.
    """
    declared = []
    for declaration in routed_logs().values():
        properties = declaration.get("properties", {})
        if DEDUPE_RAN in properties:
            declared.append(properties[DEDUPE_RAN])

    assert declared, "no log this record reaches declares the field"
    for field in declared:
        assert field["type"] == "boolean"
        assert field.get("description", "").strip()


def test_the_declaration_reports_a_record_whose_field_is_not_a_boolean(
        tmp_path, harness, monkeypatch):
    """The control for the acceptance above: the same record, the same
    declaration, the field carrying a string.

    Built from a record a real run wrote rather than from a literal, so what is
    shown to be refused differs from what is accepted in this one field alone.
    """
    target = target_asking(tmp_path, "narrow-for-the-control", REFUSES)
    assert narrow_run(target, harness, monkeypatch,
                      findings=[finding()])[0] == 0

    record = one_record(target)
    assert problems_with(record) == [], record

    reported = problems_with({**record, DEDUPE_RAN: "no"})
    assert reported, "the declaration accepts a field of the wrong type"
    assert any(DEDUPE_RAN in problem for problem in reported), reported


# ==========================================================================
# What a developer is shown, through scripts/l5-inspect itself
# ==========================================================================


A_SCOPE = "src/"
A_REASON = "the command exited 3"


def report_of(*dedupe: inspection.Dedupe) -> tuple[str, int]:
    """What `l5-inspect` prints for an inspection carrying `dedupe`, and what
    it returns.

    The report is constructed here rather than inspected out of a target: what
    is under test is the words the script prints for a dedupe that did or did
    not answer, and the report is the input it prints them from.
    """
    found = inspection.Report(dedupe=tuple(dedupe))
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = conftest.load_script("l5-inspect").report(found, found.dry_run)
    return buffer.getvalue(), code


def top_level(printed: str) -> list[str]:
    """The lines a report says at the left margin, which is where it says
    things about the inspection rather than about one of its scopes."""
    return [line for line in printed.splitlines()
            if line.strip() and not line.startswith(" ")]


def test_a_failed_dedupe_is_reported_outside_the_per_scope_lines(tmp_path):
    """The failure said as a failure, and the command still exiting zero.

    The two reports differ in one field — whether the scope's query answered —
    so the block is that field deciding. It is required to be at the left
    margin and to be a line the answering report does not have: one line
    indented among a dozen scope lines is exactly the visibility this story
    exists to fix.
    """
    failed, failed_code = report_of(
        inspection.Dedupe(scope=A_SCOPE, ran=False, reason=A_REASON))
    ran, ran_code = report_of(
        inspection.Dedupe(scope=A_SCOPE, ran=True, known=0))

    # A failed dedupe costs dedupe and costs nothing else: an inspection that
    # ran is reported as having run.
    assert failed_code == ran_code == 0

    added = [line for line in top_level(failed) if line not in top_level(ran)]
    assert added, printed_for_comparison(failed, ran)
    assert len(added) == 1, added
    assert "dedupe" in added[0].lower()

    # The scope and its reason are still said per scope, and are said again
    # under the block, so a reader who reached the failure knows which scope it
    # was without going back up.
    assert failed.count(A_SCOPE) >= 2, failed
    assert A_REASON in failed
    assert f"  {A_SCOPE}: {A_REASON}" in failed.splitlines()


def printed_for_comparison(failed: str, ran: str) -> str:
    """Both reports, for the message of a comparison that came back empty."""
    return f"failed:\n{failed}\nran:\n{ran}"
