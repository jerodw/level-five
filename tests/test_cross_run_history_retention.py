"""story-081 and story-168 validation: the cross-run history is bounded, never
repaired, and never rewritten by a run.

The prune is the only thing that reads a log and the only thing that rewrites
one. Since story-168 a run makes the read only: at run-directory creation and
above that run's first append it parses every file of every declared log,
refuses on a line it cannot parse, and — under a configured bound — counts
what is past the bound and reports the count at its end, naming the files and
the command that prunes. The rewrite happens only through `l5-prune` on the
default branch, whose cases are `tests/test_l5_prune.py`'s subject, so two
story branches never both rewrite the same file.

What this module validates: that a run under a configured
`history_retention_days` leaves every record — older than the bound or not —
byte for byte where it found it and ends with exactly one note naming the
bound, each file with its count past the bound and the command to run, on
completion and on escalation and inside the commit each leaves; that an unset
bound, or one nothing is older than, leaves a run appending no such note and a
completion report identical to a run's today; that this repository's own
configuration leaves the bound unset; that a record whose timestamp cannot be
read is kept by the prune rather than dropped; that the run announces no
drop, because it makes none; and — the property Chapter 19's silent-repair
hazard is about — that a line which is not valid JSON stops the read and
refuses the run, naming the file and the line, with the malformed line and
every other line still present and unaltered afterwards.

The records themselves, the projection that writes them and the blocked-path
and versioning decisions around them are the subject of
`tests/test_cross_run_history.py`. The two configuration keys are proven
configurable in `tests/test_config_keys_are_obeyed.py`.

The workflow and the execution rules these runs execute under are the
fixture's own, built by `tests/conftest.py` rather than resolved out of what
this repository deploys: retention is a property of the harness, and the stage
list is an input to it. Every absence asserted here carries a control that
constructs the violation. Nothing invokes a model.
"""
import ast
import json
import shutil
import time
from pathlib import Path

import pytest

import conftest
import harness_config
import schema_validator
import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]

SCHEMA = schema_validator.load_schema(story_coordinator.CROSS_RUN_HISTORY_SCHEMA)
DECLARATIONS = {name: log["items"] for name, log in SCHEMA["properties"].items()}
EVENT = story_coordinator.HISTORY_EVENT_PROPERTY

RETENTION_KEY = story_coordinator.HISTORY_RETENTION_KEY
TIMESTAMP_FORMAT = story_coordinator.HISTORY_TIMESTAMP_FORMAT
HISTORY_DIR = harness_config.DEFAULT_HISTORY_DIR

#: The bound these runs are configured with, and two ages either side of it. A
#: record older than the bound is counted and reported and one newer is not,
#: so the number itself is what is being obeyed rather than merely something
#: non-zero.
RETENTION_DAYS = 30
OLDER_THAN_THE_BOUND = RETENTION_DAYS + 11
NEWER_THAN_THE_BOUND = RETENTION_DAYS - 11

#: The command the end-of-run note tells a reader to run, resolved from the
#: one place the harness spells it.
PRUNE_COMMAND = story_coordinator.PRUNE_COMMAND

STORY_ID = "story-001"
PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}
#: A verdict the verifier asks an escalation for, which takes no retry.
ESCALATE = {
    "status": "failed",
    "blocking_issues": [{
        "severity": "high",
        "issue": "the sample behavior is missing",
        "location": "src/app.py",
        "required_behavior": "the sample behavior exists",
    }],
    "unverified": [],
    "retry_recommended": False,
}


# --------------------------------------------------------------------------
# The fixture workflow and the runs
# --------------------------------------------------------------------------

WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result"}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="cross-run-history-retention-workflow",
)
WRITING, VERIFYING = [stage["name"] for stage in WORKFLOW["stages"]]

FIXTURE_RULES = {
    "max_retries": 2,
    "require_verifier_pass": True,
    "blocked_paths": [".git/", ".harness/runs/", "rules/",
                      HISTORY_DIR.rstrip("/") + "/"],
}


@pytest.fixture
def configured_workflow() -> str:
    return WORKFLOW["name"]


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    return conftest.materialize_workflow(
        WORKFLOW, tmp_path / "retention-harness", rules=FIXTURE_RULES)


class Runner:
    """A fake agent runner that carries each stage's declared artifacts.

    `verdict` is what the verifying stage says, so the same run can be made to
    complete or to escalate and the history it leaves read either way.
    """

    def __init__(self, target_root: Path, verdict: dict = PASS):
        self.run_dir = conftest.run_dir_for(target_root, STORY_ID)
        self.verdict = verdict
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None, max_budget_usd=None, suite_command=None, run_dir=None):
        self.calls.append(stage)
        if stage == WRITING:
            _write(self.run_dir / conftest.CHANGED_FILES,
                   {"modified": ["src/app.py"], "created": [], "deleted": []})
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Did the work.\n", encoding="utf-8")
        elif stage == VERIFYING:
            _write(self.run_dir / conftest.VERIFICATION_RESULT, self.verdict)
        return AgentResult(ok=True, result_text=f"{stage} done")


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Seeding a history a run did not produce
# --------------------------------------------------------------------------


def stamp(days_ago: float) -> str:
    """A timestamp in the form the harness writes, `days_ago` days back."""
    return time.strftime(TIMESTAMP_FORMAT,
                         time.localtime(time.time() - days_ago * 86400))


def seeded_record(log: str, marker: str, timestamp: str) -> str:
    """One line for `log`, carrying exactly the fields its declaration names.

    The marker goes into the story id, so a surviving line is identifiable by
    which record it is rather than by its position in the file.
    """
    record = {}
    for name in DECLARATIONS[log]["properties"]:
        if name == EVENT:
            continue
        if name == "story_id":
            record[name] = marker
        elif name == "timestamp":
            record[name] = timestamp
        elif name == "retry_count":
            record[name] = 0
        elif name == "status":
            record[name] = DECLARATIONS[log]["properties"][name]["enum"][0]
        elif "enum" in DECLARATIONS[log]["properties"][name]:
            record[name] = DECLARATIONS[log]["properties"][name]["enum"][0]
        else:
            record[name] = f"{marker}-{name}"
    return json.dumps(record)


def history_dir_of(target_root: Path) -> Path:
    """Where the cross-run history is written, which since story-117 is the
    tree the run works in rather than the checkout it was invoked from."""
    return conftest.run_root_for(Path(target_root), STORY_ID) / HISTORY_DIR


def seed_history(target_root: Path, lines: dict[str, list[str]]) -> None:
    """Put a history in place for the next run to read.

    In the tree that run will work in — and creating that tree as the run
    would, because a plain directory at its path is exactly what the run's
    `git worktree add` then refuses to write into.
    """
    conftest.worktree_a_run_left(Path(target_root), STORY_ID)
    directory = history_dir_of(target_root)
    directory.mkdir(parents=True, exist_ok=True)
    for log, log_lines in lines.items():
        (directory / log).write_text("".join(f"{line}\n" for line in log_lines),
                                     encoding="utf-8")


def aged_lines(log: str) -> list[str]:
    """One record either side of the bound, in the order they were appended."""
    return [seeded_record(log, "old-record", stamp(OLDER_THAN_THE_BOUND)),
            seeded_record(log, "new-record", stamp(NEWER_THAN_THE_BOUND))]


def configure(target_root: Path, **values: object) -> None:
    """Add configuration keys to a target and commit what the test set up."""
    path = target_root / ".harness" / "config.yaml"
    text = path.read_text(encoding="utf-8")
    for key, value in values.items():
        text += f"{key}: {value}\n"
    path.write_text(text, encoding="utf-8")


def log_text(target_root: Path, log: str) -> str:
    path = history_dir_of(target_root) / log
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def markers(target_root: Path, log: str) -> list[str]:
    return [json.loads(line)["story_id"]
            for line in log_text(target_root, log).splitlines() if line]


def prepared(target_root: Path, *, retention: object | None,
             lines: dict[str, list[str]] | None = None) -> Path:
    """A target carrying a seeded history and, when asked for, a bound.

    Everything the test wrote is committed, because a run commits the tree it
    ends on and refuses to start from one it cannot account for — so the seeded
    history is part of the repository the run starts *from*.

    Each half is committed in the tree it was written in, which since story-117
    are two trees. The bound goes in the checkout the run is invoked from,
    which is where the configuration a run loads is read. The history goes in
    the worktree the run works in, which is the tree the clean-tree check reads
    and the tree the run commits — seeded and left uncommitted there, it would
    refuse the very run the seeding exists to feed. The bound is committed
    first so the worktree, cut from that HEAD, carries it too.
    """
    if retention is not None:
        configure(target_root, **{RETENTION_KEY: retention})
    conftest.commit_setup(target_root, "a history this run did not produce")
    if lines:
        seed_history(target_root, lines)
        worked_in = conftest.run_root_for(Path(target_root), STORY_ID)
        if worked_in != Path(target_root):
            conftest.commit_setup(
                worked_in, "a history this run did not produce")
    return target_root


def run(target_root: Path, harness_root: Path,
        verdict: dict = PASS) -> tuple[int, Runner]:
    runner = Runner(target_root, verdict)
    return (story_coordinator.run_story(
        STORY_ID, harness_root, target_root, runner), runner)


def run_dir_of(target_root: Path) -> Path:
    return conftest.run_dir_for(target_root, STORY_ID)


def events_of(target_root: Path) -> str:
    return (run_dir_of(target_root) / "events.log").read_text(encoding="utf-8")


def messages_of(events: str) -> list[str]:
    """The events.log lines with their timestamps taken off, so two runs' logs
    compare on what they said rather than on when."""
    return [line.split("] ", 1)[1] for line in events.splitlines() if line]


def notes_in(events: str) -> list[str]:
    """The lines of an events.log that name the retention bound: the end-of-run
    note when the run counted anything past it, and nothing otherwise."""
    return [line for line in events.splitlines() if RETENTION_KEY in line]


def committed_events_of(target_root: Path) -> str:
    """The events.log as the commit the run left carries it, so a note the run
    appended after its commit — untracked, and lost to the branch — is told
    apart from one inside it."""
    tree = conftest.run_root_for(Path(target_root), STORY_ID)
    relative = (run_dir_of(target_root) / "events.log").relative_to(tree)
    return conftest._git(tree, "show", f"HEAD:{relative.as_posix()}").stdout


def seeded_files(target_root: Path) -> dict[str, bytes]:
    """Every root-level file of the history directory, byte for byte: the
    files a seeded history is written to, and the ones a run leaves alone
    because it writes only under its own segment."""
    directory = history_dir_of(target_root)
    return {path.name: path.read_bytes()
            for path in sorted(directory.iterdir()) if path.is_file()}


def assert_reported_once(events: str, counts: dict[str, int]) -> str:
    """Exactly one note, naming the bound's value, each named log with the
    count past the bound in it, and the command to run; returns the note."""
    notes = notes_in(events)
    assert len(notes) == 1, events
    note = notes[0]
    assert f"{RETENTION_KEY}={RETENTION_DAYS}" in note
    assert PRUNE_COMMAND in note
    for log, older in counts.items():
        assert f"{log}: {older}" in note, note
    return note


# --------------------------------------------------------------------------
# A configured bound, and an unset one
# --------------------------------------------------------------------------


def test_a_configured_bound_leaves_every_record_and_reports_the_older_ones(
    target_root, harness_root,
):
    """The story's whole subject on the run's side.

    A record older than the bound and one newer, in every declared log, and
    the run completes with both still there byte for byte — no file the
    history was seeded into is rewritten — and ends with exactly one note
    naming the bound's value, each file with its count past the bound and the
    command that prunes. The control is the unset bound below: the same
    history and the same run append no such note.
    """
    seeded = {log: aged_lines(log) for log in DECLARATIONS}
    prepared(target_root, retention=RETENTION_DAYS, lines=seeded)
    before = seeded_files(target_root)
    code, runner = run(target_root, harness_root)

    assert code == 0 and runner.calls == [WRITING, VERIFYING]
    for log in DECLARATIONS:
        surviving = markers(target_root, log)
        assert "old-record" in surviving, log
        assert "new-record" in surviving, log
    # Every seeded file is the file that was written, byte for byte: the run
    # rewrites nothing whatever the bound says, and appends only under its
    # own segment.
    assert seeded_files(target_root) == before
    assert_reported_once(events_of(target_root),
                         {log: 1 for log in DECLARATIONS})


def test_an_unset_bound_leaves_every_record_and_reports_nothing(
    target_root, harness_root,
):
    """The control for the note above, and the criterion in its own right.

    The same seeded history, the same run, and only the configured bound
    differs: with it set the run ends with the note, and with it unset it
    appends no line naming the bound or the command. Both records are still
    there either way, which is what separates this from the story-081 run
    that dropped the older one.
    """
    seeded = {log: aged_lines(log) for log in DECLARATIONS}
    prepared(target_root, retention=None, lines=seeded)
    before = seeded_files(target_root)
    code, _ = run(target_root, harness_root)

    assert code == 0
    for log in DECLARATIONS:
        surviving = markers(target_root, log)
        assert "old-record" in surviving, log
        assert "new-record" in surviving, log
    assert seeded_files(target_root) == before
    assert notes_in(events_of(target_root)) == []
    assert PRUNE_COMMAND not in events_of(target_root)


def test_a_bound_far_enough_back_drops_nothing_it_was_not_asked_to(
    target_root, harness_root,
):
    """The bound pinned from the other side.

    The same two records and a bound older than both: neither is counted and
    no note is appended, so the note above was the *number* being obeyed
    rather than merely the key being present.
    """
    seeded = {log: aged_lines(log) for log in DECLARATIONS}
    prepared(target_root, retention=OLDER_THAN_THE_BOUND + 100, lines=seeded)
    code, _ = run(target_root, harness_root)

    assert code == 0
    for log in DECLARATIONS:
        # The seeded lines, byte for byte and in order, ahead of whatever the
        # run itself went on to append after the read had finished.
        assert log_text(target_root, log).startswith(
            "".join(f"{line}\n" for line in seeded[log])), log
        assert markers(target_root, log)[:len(seeded[log])] == [
            "old-record", "new-record"], log
    assert notes_in(events_of(target_root)) == []


def test_this_repositorys_own_configuration_leaves_the_retention_unset():
    """The shipped decision, read off the file that carries it.

    Unset keeps the whole of this deployment's history, which is the evidence
    the records exist to preserve. The control is the assertion beside it: the
    same loaded configuration carries other keys, so the absence is this key
    being absent rather than the loader returning nothing.
    """
    config = harness_config.load_config(REPO_ROOT)
    assert RETENTION_KEY not in config
    assert config.get("test_command")
    assert RETENTION_KEY in harness_config.declared_config_keys()


def test_a_record_whose_timestamp_cannot_be_read_is_kept(tmp_path):
    """The store never discards what it cannot judge.

    Dropping a line because its timestamp could not be read would be exactly
    the silent repair the prune exists to refuse. The control is in the same
    file: a line whose timestamp *can* be read, and is older than the bound, is
    dropped by the same call.
    """
    directory = tmp_path / "history"
    directory.mkdir()
    log = next(iter(DECLARATIONS))
    unreadable = json.dumps({**json.loads(seeded_record(log, "unreadable", "x")),
                             "timestamp": "not-a-timestamp"})
    absent = json.dumps({"story_id": "no-timestamp-at-all"})
    (directory / log).write_text(
        "".join(f"{line}\n" for line in (
            seeded_record(log, "old-record", stamp(OLDER_THAN_THE_BOUND)),
            unreadable, absent)),
        encoding="utf-8")

    result = story_coordinator.prune_history(
        directory, {RETENTION_KEY: str(RETENTION_DAYS)})
    assert result.problems == []
    assert result.dropped == {log: 1}
    kept = [json.loads(line)["story_id"]
            for line in (directory / log).read_text(encoding="utf-8").splitlines()]
    assert kept == ["unreadable", "no-timestamp-at-all"]


def test_the_read_counts_exactly_what_the_prune_drops_and_rewrites_nothing(
    tmp_path,
):
    """The split the story made, held on the two functions directly.

    The read a run makes counts, per file, what the prune would drop — judged
    by the same rule, so a record whose timestamp cannot be read is not
    counted — and leaves every file byte for byte. The prune then drops
    exactly that, and the same read afterwards counts nothing, which is the
    control: a read that counted nothing before the prune would be
    indistinguishable from one that cannot see the older record.
    """
    directory = tmp_path / "history"
    directory.mkdir()
    config = {RETENTION_KEY: RETENTION_DAYS}
    logs = list(DECLARATIONS)
    first, second = logs[0], logs[1]
    unreadable = json.dumps({**json.loads(seeded_record(first, "unreadable", "x")),
                             "timestamp": "not-a-timestamp"})
    (directory / first).write_text(
        "".join(f"{line}\n" for line in (*aged_lines(first), unreadable)),
        encoding="utf-8")
    (directory / second).write_text(
        seeded_record(second, "new-record", stamp(NEWER_THAN_THE_BOUND)) + "\n",
        encoding="utf-8")
    before = {name: (directory / name).read_bytes() for name in (first, second)}

    reading = story_coordinator.read_history(directory, config)
    assert reading.problems == []
    assert reading.past_bound == {first: 1}
    assert {name: (directory / name).read_bytes() for name in (first, second)} \
        == before

    pruned = story_coordinator.prune_history(directory, config)
    assert pruned.dropped == reading.past_bound
    assert (directory / second).read_bytes() == before[second]
    assert [json.loads(line)["story_id"] for line in
            (directory / first).read_text(encoding="utf-8").splitlines()] == [
        "new-record", "unreadable"]
    assert story_coordinator.read_history(directory, config).past_bound == {}
    # And with the bound unset the same read parses and counts nothing, so
    # the count is the bound deciding.
    (directory / first).write_bytes(before[first])
    assert story_coordinator.read_history(directory, {}).past_bound == {}
    assert story_coordinator.read_history(directory, {}).problems == []


# --------------------------------------------------------------------------
# What is past the bound is reported at the run's end, and only then
# --------------------------------------------------------------------------


def test_the_note_is_appended_at_the_runs_end_and_inside_the_completion_commit(
    target_root, harness_root,
):
    """The note is an end-of-run note and not a startup announcement.

    It comes after every stage has run and immediately before the completion
    entry, so the history still ends with that entry; it is inside the commit
    completion leaves rather than appended after it, as every record a
    terminal path writes is; and the completion report carries the same
    sentence under Next Step. The in-run announcement of a drop is gone: the
    only line naming the bound is this one, and it is at the end.
    """
    seeded = {log: aged_lines(log) for log in DECLARATIONS}
    prepared(target_root, retention=RETENTION_DAYS, lines=seeded)
    code, _ = run(target_root, harness_root)

    assert code == 0
    events = events_of(target_root)
    note = assert_reported_once(events, {log: 1 for log in DECLARATIONS})
    lines = events.splitlines()
    position = lines.index(note)
    assert position == len(lines) - 2, lines
    assert "story completed" in lines[-1]
    stage_lines = [i for i, line in enumerate(lines)
                   if WRITING in line or VERIFYING in line]
    assert stage_lines and max(stage_lines) < position, lines

    assert note in committed_events_of(target_root)

    report = (run_dir_of(target_root) / "completion-report.md").read_text(
        encoding="utf-8")
    sentence = note.split("] ", 1)[1]
    next_step = report.split("## Next Step\n", 1)[1]
    assert sentence in next_step, report
    assert PRUNE_COMMAND in next_step


def test_an_escalating_run_appends_the_same_note_inside_the_escalation_commit(
    target_root, harness_root,
):
    """An escalation is an end too, and the note goes before the commit it
    leaves. The control is the escalation below with the bound unset."""
    seeded = {log: aged_lines(log) for log in DECLARATIONS}
    prepared(target_root, retention=RETENTION_DAYS, lines=seeded)
    before = seeded_files(target_root)
    code, runner = run(target_root, harness_root, ESCALATE)

    assert code == 2 and runner.calls == [WRITING, VERIFYING]
    assert seeded_files(target_root) == before
    events = events_of(target_root)
    note = assert_reported_once(events, {log: 1 for log in DECLARATIONS})
    lines = events.splitlines()
    assert lines.index(note) == len(lines) - 2, lines
    assert lines[-1].split("] ", 1)[1].startswith("escalated")
    assert note in committed_events_of(target_root)


def test_an_escalating_run_with_the_bound_unset_appends_no_note(
    target_root, harness_root,
):
    seeded = {log: aged_lines(log) for log in DECLARATIONS}
    prepared(target_root, retention=None, lines=seeded)
    code, _ = run(target_root, harness_root, ESCALATE)

    assert code == 2
    assert notes_in(events_of(target_root)) == []
    assert PRUNE_COMMAND not in events_of(target_root)
    assert "escalated" in events_of(target_root).splitlines()[-1]


def test_a_run_with_nothing_past_the_bound_appends_no_note(
    target_root, harness_root,
):
    """The control for the note, and the reason it is conditional.

    Every deployment leaving the bound unset would otherwise gain a new event
    on every run saying nothing happened. The bound here is set and the history
    holds only a record newer than it, so the read ran and counted nothing.
    """
    seeded = {log: [seeded_record(log, "new-record", stamp(NEWER_THAN_THE_BOUND))]
              for log in DECLARATIONS}
    prepared(target_root, retention=RETENTION_DAYS, lines=seeded)
    code, _ = run(target_root, harness_root)

    assert code == 0
    assert RETENTION_KEY not in events_of(target_root)
    assert PRUNE_COMMAND not in events_of(target_root)
    for log in DECLARATIONS:
        # The seeded line still leading, byte for byte, ahead of whatever the
        # run itself appended after the read.
        assert log_text(target_root, log).startswith(seeded[log][0] + "\n"), log
        assert markers(target_root, log)[:1] == ["new-record"], log


def test_a_run_that_reports_nothing_leaves_the_log_and_report_a_run_leaves_today(
    tmp_path, target_template, harness_root,
):
    """A deployment leaving the bound unset — this one — gets no new event and
    no new sentence.

    Two runs from two copies of the same target: one with the bound unset and
    old records seeded, one under the bound with nothing older than it. Their
    events.logs say the same things, timestamps aside, and their completion
    reports are byte for byte the same file — and neither says anything a run
    under a bound with records past it says, which is the control: the run
    above shows the same reader finding the note when there is one.
    """
    logs: dict[str, str] = {}
    reports: dict[str, str] = {}
    for name, retention, lines in (
        ("unset", None, {log: aged_lines(log) for log in DECLARATIONS}),
        ("nothing-older", RETENTION_DAYS,
         {log: [seeded_record(log, "new-record", stamp(NEWER_THAN_THE_BOUND))]
          for log in DECLARATIONS}),
    ):
        target = tmp_path / name / "sample-target"
        shutil.copytree(target_template, target, symlinks=True)
        prepared(target, retention=retention, lines=lines)
        assert run(target, harness_root)[0] == 0
        logs[name] = events_of(target)
        reports[name] = (run_dir_of(target) / "completion-report.md").read_text(
            encoding="utf-8")

    assert messages_of(logs["unset"]) == messages_of(logs["nothing-older"])
    assert reports["unset"] == reports["nothing-older"]
    for name in logs:
        assert RETENTION_KEY not in logs[name], name
        assert PRUNE_COMMAND not in logs[name], name
        assert PRUNE_COMMAND not in reports[name], name
        assert reports[name].endswith(
            "## Next Step\nReview the branch and merge it when you accept "
            "the story.\n"), reports[name]


# --------------------------------------------------------------------------
# The prune refuses rather than repairs
# --------------------------------------------------------------------------

MALFORMED = "{this line is not valid JSON"


def malformed_lines(log: str) -> list[str]:
    """A log whose second line cannot be parsed, with valid lines either side."""
    return [seeded_record(log, "first-record", stamp(OLDER_THAN_THE_BOUND)),
            MALFORMED,
            seeded_record(log, "third-record", stamp(NEWER_THAN_THE_BOUND))]


def test_a_malformed_line_refuses_the_run_naming_the_file_and_the_line(
    target_root, harness_root, capsys,
):
    log = next(iter(DECLARATIONS))
    prepared(target_root, retention=RETENTION_DAYS,
             lines={log: malformed_lines(log)})
    capsys.readouterr()
    code, runner = run(target_root, harness_root)
    refusal = capsys.readouterr().err

    assert code == 1
    assert runner.calls == []
    assert str(history_dir_of(target_root) / log) in refusal
    assert "line 2" in refusal
    # The refusal says what it did *not* do, because a store that repairs what
    # it cannot parse is not append-only.
    assert "append-only" in refusal


def test_the_malformed_line_and_every_other_line_survive_the_refusal(
    target_root, harness_root,
):
    """Nothing discarded, nothing rewritten around.

    The whole file is compared byte for byte against what was seeded: a prune
    that had dropped the older record — which the configured bound would
    otherwise have dropped — or rewritten the file around the line it could not
    read would fail this even though the malformed line itself survived.
    """
    log = next(iter(DECLARATIONS))
    seeded = malformed_lines(log)
    prepared(target_root, retention=RETENTION_DAYS, lines={log: seeded})
    before = log_text(target_root, log)
    code, _ = run(target_root, harness_root)

    assert code == 1
    assert log_text(target_root, log) == before
    assert MALFORMED in log_text(target_root, log)
    assert markers_including_malformed(target_root, log) == [
        "first-record", MALFORMED, "third-record"]


def markers_including_malformed(target_root: Path, log: str) -> list[str]:
    found = []
    for line in log_text(target_root, log).splitlines():
        try:
            found.append(json.loads(line)["story_id"])
        except json.JSONDecodeError:
            found.append(line)
    return found


def test_the_same_history_without_the_malformed_line_runs_to_completion(
    target_root, harness_root,
):
    """The control for the two refusals above.

    The same seeded records, the same bound, the same run — with the one
    unparseable line removed — and the run completes, keeps both records and
    reports the older one. So the refusal is that line deciding rather than
    anything else about a seeded history.
    """
    log = next(iter(DECLARATIONS))
    seeded = [line for line in malformed_lines(log) if line != MALFORMED]
    prepared(target_root, retention=RETENTION_DAYS, lines={log: seeded})
    code, runner = run(target_root, harness_root)

    assert code == 0
    assert runner.calls == [WRITING, VERIFYING]
    surviving = markers(target_root, log)
    assert "third-record" in surviving
    assert "first-record" in surviving
    assert log_text(target_root, log).startswith(
        "".join(f"{line}\n" for line in seeded))
    assert_reported_once(events_of(target_root), {log: 1})


def test_a_malformed_line_in_one_log_leaves_every_other_log_unrewritten(
    target_root, harness_root,
):
    """Every log is parsed before any is rewritten.

    The first log holds records the bound would drop and the second holds a
    line that cannot be parsed. A prune that rewrote as it went would have
    already truncated the first before it reached the second, and the record
    it dropped would be gone with nothing announcing it.
    """
    logs = list(DECLARATIONS)
    assert len(logs) > 1, "this assertion needs two declared logs"
    first, second = logs[0], logs[1]
    seeded = {first: aged_lines(first), second: malformed_lines(second)}
    prepared(target_root, retention=RETENTION_DAYS, lines=seeded)
    before = {log: log_text(target_root, log) for log in seeded}
    code, _ = run(target_root, harness_root)

    assert code == 1
    for log in seeded:
        assert log_text(target_root, log) == before[log], log
    assert markers(target_root, first) == ["old-record", "new-record"]


def test_the_prune_reports_the_problem_rather_than_returning_a_repaired_file(
    tmp_path,
):
    """The predicate the refusals above go through, exercised in both
    directions against a directory this test owns."""
    directory = tmp_path / "history"
    directory.mkdir()
    log = next(iter(DECLARATIONS))
    (directory / log).write_text(
        "".join(f"{line}\n" for line in malformed_lines(log)), encoding="utf-8")
    before = (directory / log).read_text(encoding="utf-8")

    refused = story_coordinator.prune_history(
        directory, {RETENTION_KEY: str(RETENTION_DAYS)})
    assert len(refused.problems) == 1
    assert str(directory / log) in refused.problems[0]
    assert "line 2" in refused.problems[0]
    assert refused.dropped == {}
    assert (directory / log).read_text(encoding="utf-8") == before

    # The control: the same call over the same file with the one unparseable
    # line removed prunes, so the refusal above is that line and not the shape
    # of the call.
    (directory / log).write_text(
        "".join(f"{line}\n" for line in malformed_lines(log)
                if line != MALFORMED), encoding="utf-8")
    accepted = story_coordinator.prune_history(
        directory, {RETENTION_KEY: str(RETENTION_DAYS)})
    assert accepted.problems == []
    assert accepted.dropped == {log: 1}


def test_a_bound_that_is_not_a_number_refuses_the_run_rather_than_ignoring_it(
    target_root, harness_root, capsys,
):
    """The declaration says the value is parsed to a non-negative number and a
    value that is not one refuses the run.

    The control is every other run in this module: the same key carrying a
    number completes.
    """
    prepared(target_root, retention="not-a-number")
    capsys.readouterr()
    code, runner = run(target_root, harness_root)
    refusal = capsys.readouterr().err

    assert code == 1
    assert runner.calls == []
    assert RETENTION_KEY in refusal
    assert "not-a-number" in refusal


# --------------------------------------------------------------------------
# The rewrite is invoked from l5-prune alone
# --------------------------------------------------------------------------

ORCHESTRATION = REPO_ROOT / "orchestration"


def callers_of(source: str, callee: str) -> set[str]:
    """The top-level functions in `source` whose body calls `callee`, read off
    the syntax tree so a comment that names the call is not a call."""
    found = set()
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) \
                    and inner.func.id == callee:
                found.add(node.name)
    return found


def test_prune_history_is_called_from_the_store_prune_alone_and_never_by_a_run():
    """A run no longer drops anything, so nothing on a run's path calls the
    rewrite: across every module under orchestration/ the only caller of
    `prune_history` is the function `l5-prune` hands control to, and
    `run_story` — which called it at run-directory creation until story-168 —
    reads through `read_history` instead. The control plants a call in a copy
    of `run_story` and shows the same scan reporting it."""
    callers: dict[str, set[str]] = {}
    for module in sorted(ORCHESTRATION.glob("*.py")):
        found = callers_of(module.read_text(encoding="utf-8"), "prune_history")
        if found:
            callers[module.name] = found
    coordinator = Path(story_coordinator.__file__).name
    assert callers == {coordinator: {story_coordinator.prune_store.__name__}}

    source = Path(story_coordinator.__file__).read_text(encoding="utf-8")
    run_story = conftest.function_source(source, "run_story")
    assert "read_history(" in run_story
    planted = run_story.replace(
        "read_history(history_directory, config)",
        "prune_history(history_directory, config)", 1)
    assert planted != run_story
    assert callers_of(planted, "prune_history") == {"run_story"}
