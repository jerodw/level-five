"""story-081 validation: the cross-run history is bounded, and never repaired.

Pruning is the only thing that reads a log and the only thing that rewrites
one. It runs once per run, at run-directory creation and above that run's first
append, which confines the only rewrite of a committed record to a single
announced point and leaves the append path itself append-only.

What this module validates: that a configured `history_retention_days` drops
records older than the bound and leaves newer ones, that an unset bound prunes
nothing, that this repository's own configuration leaves it unset, that a
record whose timestamp cannot be read is kept rather than dropped, that a prune
which drops something announces itself and one which drops nothing says
nothing, and — the property Chapter 19's silent-repair hazard is about — that a
line which is not valid JSON stops the prune and refuses the run, naming the
file and the line, with the malformed line and every other line still present
and unaltered afterwards.

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
import json
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
#: record older than the bound is dropped and one newer is kept, so the number
#: itself is what is being obeyed rather than merely something non-zero.
RETENTION_DAYS = 30
OLDER_THAN_THE_BOUND = RETENTION_DAYS + 11
NEWER_THAN_THE_BOUND = RETENTION_DAYS - 11

STORY_ID = "story-001"
PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


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
    """A fake agent runner that carries each stage's declared artifacts."""

    def __init__(self, target_root: Path):
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None, max_budget_usd=None, suite_command=None):
        self.calls.append(stage)
        if stage == WRITING:
            _write(self.run_dir / conftest.CHANGED_FILES,
                   {"modified": ["src/app.py"], "created": [], "deleted": []})
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Did the work.\n", encoding="utf-8")
        elif stage == VERIFYING:
            _write(self.run_dir / conftest.VERIFICATION_RESULT, PASS)
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
    return target_root / HISTORY_DIR


def seed_history(target_root: Path, lines: dict[str, list[str]]) -> None:
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
    """
    if retention is not None:
        configure(target_root, **{RETENTION_KEY: retention})
    if lines:
        seed_history(target_root, lines)
    conftest.commit_setup(target_root, "a history this run did not produce")
    return target_root


def run(target_root: Path, harness_root: Path) -> tuple[int, Runner]:
    runner = Runner(target_root)
    return (story_coordinator.run_story(
        STORY_ID, harness_root, target_root, runner), runner)


def events_of(target_root: Path) -> str:
    return (target_root / ".harness" / "runs" / STORY_ID / "events.log").read_text(
        encoding="utf-8")


# --------------------------------------------------------------------------
# A configured bound, and an unset one
# --------------------------------------------------------------------------


def test_a_configured_bound_drops_older_records_and_leaves_newer_ones(
    target_root, harness_root,
):
    seeded = {log: aged_lines(log) for log in DECLARATIONS}
    prepared(target_root, retention=RETENTION_DAYS, lines=seeded)
    code, runner = run(target_root, harness_root)

    assert code == 0 and runner.calls == [WRITING, VERIFYING]
    for log in DECLARATIONS:
        surviving = markers(target_root, log)
        assert "old-record" not in surviving, log
        assert "new-record" in surviving, log
        # The surviving line is the line that was written, byte for byte: a
        # prune that re-serialized what it kept would be rewriting records it
        # was not asked to touch.
        assert seeded[log][1] + "\n" in log_text(target_root, log), log


def test_an_unset_bound_prunes_nothing(target_root, harness_root):
    """The control for the assertion above, and the criterion in its own right.

    The same seeded history, the same run, and only the configured bound
    differs: with it set the older record is dropped, and with it unset both
    records are still there. So the drop is the bound deciding rather than
    anything else about a run.
    """
    seeded = {log: aged_lines(log) for log in DECLARATIONS}
    prepared(target_root, retention=None, lines=seeded)
    code, _ = run(target_root, harness_root)

    assert code == 0
    for log in DECLARATIONS:
        surviving = markers(target_root, log)
        assert "old-record" in surviving, log
        assert "new-record" in surviving, log
        assert log_text(target_root, log).startswith(
            "".join(f"{line}\n" for line in seeded[log])), log


def test_a_bound_far_enough_back_drops_nothing_it_was_not_asked_to(
    target_root, harness_root,
):
    """The bound pinned from the other side.

    The same two records and a bound older than both: neither is dropped, so
    the earlier drop was the *number* being obeyed rather than merely the key
    being present.
    """
    seeded = {log: aged_lines(log) for log in DECLARATIONS}
    prepared(target_root, retention=OLDER_THAN_THE_BOUND + 100, lines=seeded)
    code, _ = run(target_root, harness_root)

    assert code == 0
    for log in DECLARATIONS:
        # The seeded lines, byte for byte and in order, ahead of whatever the
        # run itself went on to append after the prune had finished.
        assert log_text(target_root, log).startswith(
            "".join(f"{line}\n" for line in seeded[log])), log
        assert markers(target_root, log)[:len(seeded[log])] == [
            "old-record", "new-record"], log


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


# --------------------------------------------------------------------------
# A rewrite of a committed record is never silent
# --------------------------------------------------------------------------


def test_a_prune_that_dropped_records_announces_what_it_dropped(
    target_root, harness_root,
):
    prepared(target_root, retention=RETENTION_DAYS,
             lines={log: aged_lines(log) for log in DECLARATIONS})
    code, _ = run(target_root, harness_root)

    assert code == 0
    announced = events_of(target_root)
    assert f"{RETENTION_KEY}={RETENTION_DAYS}" in announced
    for log in DECLARATIONS:
        assert log in announced, log


def test_a_prune_that_dropped_nothing_announces_nothing(
    target_root, harness_root,
):
    """The control for the announcement, and the reason it is conditional.

    Every deployment leaving the bound unset would otherwise gain a new event
    on every run saying nothing happened. The bound here is set and the history
    holds only a record newer than it, so the prune ran and dropped nothing.
    """
    seeded = {log: [seeded_record(log, "new-record", stamp(NEWER_THAN_THE_BOUND))]
              for log in DECLARATIONS}
    prepared(target_root, retention=RETENTION_DAYS, lines=seeded)
    code, _ = run(target_root, harness_root)

    assert code == 0
    assert RETENTION_KEY not in events_of(target_root)
    for log in DECLARATIONS:
        # The seeded line still leading, byte for byte, ahead of whatever the
        # run itself appended after the prune.
        assert log_text(target_root, log).startswith(seeded[log][0] + "\n"), log
        assert markers(target_root, log)[:1] == ["new-record"], log


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
    unparseable line removed — and the run completes and prunes. So the refusal
    is that line deciding rather than anything else about a seeded history.
    """
    log = next(iter(DECLARATIONS))
    seeded = [line for line in malformed_lines(log) if line != MALFORMED]
    prepared(target_root, retention=RETENTION_DAYS, lines={log: seeded})
    code, runner = run(target_root, harness_root)

    assert code == 0
    assert runner.calls == [WRITING, VERIFYING]
    surviving = markers(target_root, log)
    assert "third-record" in surviving
    assert "first-record" not in surviving


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
