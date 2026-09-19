"""story-168 validation: `l5-prune` is where the cross-run history is rewritten,
on the default branch and outside any run.

A story run reads the history and reports what a configured
`history_retention_days` would drop; it rewrites nothing, because two story
branches that both rewrote the same file would conflict on merge in exactly
the shape per-writer segments removed for the append. The rewrite is this
command's: run by a person on the repository's default branch, it drops every
record older than the bound from whichever file holds it, keeps every newer
record and every record whose timestamp cannot be read byte for byte, prints
each rewritten file with the count dropped from it, and commits exactly the
rewritten files with a message naming the bound. It does not push.

What this module validates:

  * on the default branch with a configured bound it drops, prints, and
    leaves one commit whose changed files are exactly the rewritten ones,
    with the tree clean afterwards;
  * on a branch that is not the default, and on a detached HEAD, it refuses
    naming where it stands and the default branch, and rewrites and commits
    nothing;
  * on a malformed line it refuses through the bad-history refusal naming the
    file and the line, with nothing rewritten and nothing committed; on a
    bound that is not a non-negative number it refuses naming the key and the
    value;
  * with the bound unset, or with nothing past it, it says so, exits 0 and
    commits nothing;
  * it does not push, read off a remote that a push would have moved;
  * the entry point walks up from the current directory to the nearest
    `.harness/config.yaml`, hands control to orchestration, and takes no
    arguments.

The run's side — that a run rewrites nothing and reports at its end — is
`tests/test_cross_run_history_retention.py`'s subject, and the merge two such
runs make is `tests/test_two_runs_merge_without_contending.py`'s. Every
repository here is built by this module under a temporary directory; nothing
resolves a baseline out of the history the harness lives in, and nothing
invokes a model. Every absence asserted — nothing rewritten, nothing
committed, nothing pushed — sits beside a demonstration that the same reader
sees the thing when it is there.
"""
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

import conftest
import harness_config
import schema_validator
import story_coordinator

SCHEMA = schema_validator.load_schema(story_coordinator.CROSS_RUN_HISTORY_SCHEMA)
DECLARATIONS = {name: log["items"] for name, log in SCHEMA["properties"].items()}

#: The log told apart by what its declaration carries rather than by name.
OUTCOME_LOG = next(name for name, shape in DECLARATIONS.items()
                   if "status" in shape["properties"])

RETENTION_KEY = story_coordinator.HISTORY_RETENTION_KEY
PRUNE_COMMAND = story_coordinator.PRUNE_COMMAND
HISTORY_DIR = harness_config.DEFAULT_HISTORY_DIR

#: A record older than the bound every store here is configured with, and the
#: bound itself.
OLD_STAMP = "2020-01-01 00:00:00"
RETENTION_DAYS = 1

#: The segments a planted history carries: an earlier story's, holding a
#: record past the bound, and a later one holding only a fresh record.
OLDER_STORY = "story-001"
NEWER_STORY = "story-002"

#: A record whose timestamp cannot be read, which the prune keeps.
UNREADABLE = json.dumps({"story_id": "unreadable", "status": "completed",
                         "timestamp": "not-a-timestamp"}) + "\n"


def now_stamp() -> str:
    return time.strftime(story_coordinator.HISTORY_TIMESTAMP_FORMAT)


def outcome_line(story_id: str, stamp: str) -> str:
    return json.dumps({"story_id": story_id, "status": "completed",
                       "timestamp": stamp}) + "\n"


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True)


def git_out(root: Path, *args: str) -> str:
    finished = git(root, *args)
    assert finished.returncode == 0, finished.stderr
    return finished.stdout


def snapshot(directory: Path) -> dict[str, bytes]:
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def parse_records(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# --------------------------------------------------------------------------
# A store to prune: a target repository standing on its default branch
# --------------------------------------------------------------------------


@dataclass
class Store:
    target: Path
    directory: Path
    before: dict[str, bytes]
    head: str
    default: str

    @property
    def rewritten(self) -> dict[str, int]:
        """What a prune of this store drops, keyed as the prune keys it."""
        return {OUTCOME_LOG: 1, f"{OLDER_STORY}/{OUTCOME_LOG}": 1}

    @property
    def untouched(self) -> set[str]:
        return set(self.before) - set(self.rewritten)


def configure(target: Path, **values: object) -> None:
    path = target / ".harness" / "config.yaml"
    text = path.read_text(encoding="utf-8")
    for key, value in values.items():
        text += f"{key}: {value}\n"
    path.write_text(text, encoding="utf-8")


def build_store(target: Path, *, retention: object = RETENTION_DAYS,
                lines: dict[str, str] | None = None) -> Store:
    """Plant a history and a bound in `target`, committed on the default branch.

    The root-level file and the older story's segment each hold one record
    past the bound and one not; the older segment also holds a record whose
    timestamp cannot be read; the newer segment holds only a fresh record.
    The target is put on the branch the harness resolves as the default when
    nothing configures one, so the base is a branch that exists whatever
    `init.defaultBranch` the machine carries.
    """
    git_out(target, "branch", "-M", conftest.BASE_BRANCH_FALLBACK)
    if retention is not None:
        configure(target, **{RETENTION_KEY: retention})
    fresh = now_stamp()
    files = lines if lines is not None else {
        OUTCOME_LOG: outcome_line("story-000", OLD_STAMP)
                     + outcome_line("story-000", fresh),
        f"{OLDER_STORY}/{OUTCOME_LOG}": outcome_line(OLDER_STORY, OLD_STAMP)
                                        + outcome_line(OLDER_STORY, fresh)
                                        + UNREADABLE,
        f"{NEWER_STORY}/{OUTCOME_LOG}": outcome_line(NEWER_STORY, fresh),
    }
    config = harness_config.load_config(target)
    directory = harness_config.history_dir(target, config)
    for relative, text in files.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    conftest.commit_setup(target, "a history with records past the bound")
    return Store(
        target=target,
        directory=directory,
        before=snapshot(directory),
        head=git_out(target, "rev-parse", "HEAD").strip(),
        default=story_coordinator.resolve_base(target, config, None),
    )


@pytest.fixture
def store(target_root: Path) -> Store:
    return build_store(target_root)


@pytest.fixture
def script():
    return conftest.load_script("l5-prune")


def prune(script, monkeypatch, capsys, cwd: Path,
          argv: list[str] = ()) -> tuple[int, str, str]:
    """Run the entry point as a person would: from `cwd`, with `argv`."""
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(sys, "argv", [PRUNE_COMMAND, *argv])
    capsys.readouterr()
    code = script.main()
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def assert_nothing_rewritten_or_committed(store: Store) -> None:
    assert snapshot(store.directory) == store.before
    assert git_out(store.target, "rev-parse", "HEAD").strip() == store.head
    assert git_out(store.target, "status", "--porcelain") == ""


# --------------------------------------------------------------------------
# On the default branch it drops, prints and commits exactly what it rewrote
# --------------------------------------------------------------------------


def test_on_the_default_branch_it_drops_the_older_records_and_commits_the_rewritten_files(
        store, script, monkeypatch, capsys):
    """The command's whole subject, driven from a subdirectory so the walk up
    to `.harness/config.yaml` is exercised too."""
    assert git_out(store.target, "rev-parse", "--abbrev-ref", "HEAD").strip() \
        == store.default
    code, out, err = prune(script, monkeypatch, capsys, store.target / "src")

    assert code == 0, err
    after = snapshot(store.directory)
    assert set(after) == set(store.before)
    for relative, count in store.rewritten.items():
        assert f"{relative}: dropped {count}" in out, out
        records = parse_records(after[relative].decode("utf-8"))
        assert all(r["timestamp"] != OLD_STAMP for r in records), relative
        # Every kept line is the line that was written, byte for byte, in
        # order: the file is the old file with the dropped lines taken out.
        kept = [line for line in store.before[relative].decode("utf-8")
                .splitlines(keepends=True)
                if json.loads(line)["timestamp"] != OLD_STAMP]
        assert after[relative] == "".join(kept).encode("utf-8"), relative
    assert UNREADABLE.encode("utf-8") in after[f"{OLDER_STORY}/{OUTCOME_LOG}"]
    for relative in store.untouched:
        assert after[relative] == store.before[relative], relative
        assert relative not in out

    head = git_out(store.target, "rev-parse", "HEAD").strip()
    assert head != store.head
    assert git_out(store.target, "rev-parse", "HEAD~1").strip() == store.head
    assert git_out(store.target, "rev-parse", "--abbrev-ref", "HEAD").strip() \
        == store.default
    changed = git_out(store.target, "show", "--name-only", "--format=",
                      "HEAD").split()
    relative_dir = store.directory.relative_to(store.target).as_posix()
    assert sorted(changed) == sorted(
        f"{relative_dir}/{name}" for name in store.rewritten)
    subject = git_out(store.target, "log", "-1", "--format=%s").strip()
    assert f"{RETENTION_KEY}={RETENTION_DAYS}" in subject
    assert git_out(store.target, "status", "--porcelain") == ""
    assert "not pushed" in out


def test_a_second_prune_finds_nothing_and_commits_nothing(
        store, script, monkeypatch, capsys):
    """The prune is idempotent: run again over the store it just rewrote it
    reports nothing past the bound, exits 0 and leaves the branch where the
    first prune left it."""
    assert prune(script, monkeypatch, capsys, store.target)[0] == 0
    head = git_out(store.target, "rev-parse", "HEAD").strip()
    after = snapshot(store.directory)

    code, out, err = prune(script, monkeypatch, capsys, store.target)

    assert code == 0, err
    assert "nothing to prune" in out.lower()
    assert f"{RETENTION_KEY}={RETENTION_DAYS}" in out
    assert snapshot(store.directory) == after
    assert git_out(store.target, "rev-parse", "HEAD").strip() == head


# --------------------------------------------------------------------------
# It refuses off the default branch, and on a history it cannot read
# --------------------------------------------------------------------------


def test_off_the_default_branch_it_refuses_naming_both_branches_and_touches_nothing(
        store, script, monkeypatch, capsys):
    """The control is the test above: the same store on the default branch
    is pruned and committed, so the refusal is the branch deciding."""
    elsewhere = "feature/not-the-default"
    git_out(store.target, "checkout", "-q", "-b", elsewhere)

    code, out, err = prune(script, monkeypatch, capsys, store.target)

    assert code == 1
    assert elsewhere in err
    assert store.default in err
    assert PRUNE_COMMAND in err
    assert out == ""
    assert_nothing_rewritten_or_committed(store)
    assert git_out(store.target, "rev-parse", "--abbrev-ref", "HEAD").strip() \
        == elsewhere


def test_on_a_detached_head_it_refuses_naming_the_default_branch_and_touches_nothing(
        store, script, monkeypatch, capsys):
    git_out(store.target, "checkout", "-q", "--detach", "HEAD")

    code, out, err = prune(script, monkeypatch, capsys, store.target)

    assert code == 1
    assert "detached" in err
    assert store.default in err
    assert out == ""
    assert snapshot(store.directory) == store.before
    assert git_out(store.target, "rev-parse", "HEAD").strip() == store.head
    assert git_out(store.target, "status", "--porcelain") == ""


def test_a_malformed_line_refuses_naming_the_file_and_the_line_with_nothing_rewritten(
        target_root, script, monkeypatch, capsys):
    """Through the bad-history refusal a run makes, and with nothing dropped
    around it: the root-level file still holds the record the bound would
    have dropped. The control is the default-branch test above, where the
    same store without the malformed line is pruned."""
    fresh = now_stamp()
    store = build_store(target_root, lines={
        OUTCOME_LOG: outcome_line("story-000", OLD_STAMP)
                     + outcome_line("story-000", fresh),
        f"{NEWER_STORY}/{OUTCOME_LOG}": outcome_line(NEWER_STORY, fresh)
                                        + "{this line is not valid JSON\n",
    })
    broken = store.directory / NEWER_STORY / OUTCOME_LOG

    code, out, err = prune(script, monkeypatch, capsys, store.target)

    assert code == 1
    assert str(broken) in err
    assert "line 2" in err
    assert "append-only" in err
    assert f"run {PRUNE_COMMAND} again" in err
    assert out == ""
    assert_nothing_rewritten_or_committed(store)
    assert OLD_STAMP in (store.directory / OUTCOME_LOG).read_text(encoding="utf-8")


def test_a_bound_that_is_not_a_number_refuses_naming_the_key_and_the_value(
        target_root, script, monkeypatch, capsys):
    store = build_store(target_root, retention="not-a-number")

    code, out, err = prune(script, monkeypatch, capsys, store.target)

    assert code == 1
    assert RETENTION_KEY in err
    assert "not-a-number" in err
    assert out == ""
    assert_nothing_rewritten_or_committed(store)


# --------------------------------------------------------------------------
# With nothing to do it says so and exits 0
# --------------------------------------------------------------------------


def test_with_the_bound_unset_it_says_so_exits_zero_and_commits_nothing(
        target_root, script, monkeypatch, capsys):
    """The same records the default-branch test drops, and no bound: read,
    kept, and nothing committed — so the drop above is the bound deciding."""
    store = build_store(target_root, retention=None)

    code, out, err = prune(script, monkeypatch, capsys, store.target)

    assert code == 0, err
    assert RETENTION_KEY in out
    assert "unset" in out
    assert "nothing to prune" in out.lower()
    assert_nothing_rewritten_or_committed(store)


def test_with_nothing_past_the_bound_it_says_so_exits_zero_and_commits_nothing(
        target_root, script, monkeypatch, capsys):
    fresh = now_stamp()
    store = build_store(target_root, lines={
        OUTCOME_LOG: outcome_line("story-000", fresh),
        f"{OLDER_STORY}/{OUTCOME_LOG}": outcome_line(OLDER_STORY, fresh) + UNREADABLE,
    })

    code, out, err = prune(script, monkeypatch, capsys, store.target)

    assert code == 0, err
    assert f"{RETENTION_KEY}={RETENTION_DAYS}" in out
    assert "nothing to prune" in out.lower()
    assert_nothing_rewritten_or_committed(store)


# --------------------------------------------------------------------------
# It does not push
# --------------------------------------------------------------------------


def test_it_commits_on_the_default_branch_and_does_not_push(
        store, script, monkeypatch, capsys, tmp_path):
    """A bare remote the default branch is pushed to before the prune stays
    exactly where it was after it, while the local branch moved on by the
    prune's commit. The control pushes by hand afterwards and shows the same
    reader seeing the remote move, so an unmoved remote is a push that was
    not made rather than a reader that cannot see one."""
    remote = tmp_path / "remote.git"
    git_out(tmp_path, "init", "-q", "--bare", str(remote))
    git_out(store.target, "remote", "add", "origin", str(remote))
    git_out(store.target, "push", "-q", "origin", store.default)
    ref = f"refs/heads/{store.default}"
    assert git_out(remote, "rev-parse", ref).strip() == store.head

    code, out, err = prune(script, monkeypatch, capsys, store.target)

    assert code == 0, err
    local = git_out(store.target, "rev-parse", "HEAD").strip()
    assert local != store.head
    assert git_out(remote, "rev-parse", ref).strip() == store.head
    assert git_out(store.target, "rev-parse", f"origin/{store.default}").strip() \
        == store.head

    git_out(store.target, "push", "-q", "origin", store.default)
    assert git_out(remote, "rev-parse", ref).strip() == local


# --------------------------------------------------------------------------
# The entry point is thin
# --------------------------------------------------------------------------


def test_the_entry_point_hands_the_found_target_to_orchestration_and_takes_no_arguments(
        store, script, monkeypatch, capsys):
    """The script finds the target and calls `prune_store` with it, deciding
    nothing itself; handed any argument it prints its usage and exits 1
    without reaching orchestration."""
    seen: list[Path] = []

    def spy(target_root: Path) -> int:
        seen.append(target_root)
        return 0

    monkeypatch.setattr(script.story_coordinator, "prune_store", spy)
    code, _, err = prune(script, monkeypatch, capsys, store.target / "src")
    assert code == 0 and err == ""
    assert [path.resolve() for path in seen] == [store.target.resolve()]

    seen.clear()
    code, _, err = prune(script, monkeypatch, capsys, store.target,
                         argv=["--force"])
    assert code == 1
    assert seen == []
    assert "Usage" in err
    assert "default branch" in err


def test_outside_any_target_the_entry_point_exits_naming_the_missing_config(
        script, monkeypatch, capsys, tmp_path):
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    monkeypatch.chdir(nowhere)
    monkeypatch.setattr(sys, "argv", [PRUNE_COMMAND])
    with pytest.raises(SystemExit) as stopped:
        script.main()
    assert ".harness/config.yaml" in str(stopped.value)


def test_the_script_names_why_the_prune_belongs_on_the_default_branch():
    """The docstring a person reads before running it says what a run does
    instead and why the rewrite is here, on the default branch."""
    text = (conftest.HARNESS_ROOT / "scripts" / PRUNE_COMMAND).read_text(
        encoding="utf-8")
    assert text.startswith("#!")
    doc = text.split('"""', 2)[1]
    assert "default branch" in doc
    assert RETENTION_KEY in doc
    assert "does not\npush" in doc or "does not push" in doc
    assert "merg" in doc
