"""A record commit that fails is checked, reported, and leaves nothing staged.

When an inspection finishes it writes one line into the versioned cross-run
history and commits that line, so the tree is left clean for the next run.
Before story-188 a commit that failed — a pre-commit hook that rejected it, a
git with no identity to commit as — was checked by nothing and said by
nothing: the record was left staged and never committed, the developer read a
normal-looking report, and the next run refused to start on a dirty tree with
no message connecting the refusal to the inspection that caused it.

What this module holds the two producers to:

  * in broad mode, `inspection.record` returns a result saying whether the
    record was written and whether it was committed, with git's own words in
    the detail where git had any, and `scripts/l5-inspect` prints one line
    about it on every real inspection — committed, or NOT committed and why —
    and none on a dry run;
  * in narrow mode, `story_inspection.commit_record` writes one line into the
    run's events.log whenever the record was not committed — a refused
    commit, a raise, nothing staged — and no line when it was;
  * a commit that fails leaves the tree as the commit found it: the record
    modified in the working tree, nothing of it staged, and anything else
    that was staged left exactly as it was;
  * neither producer raises to its caller, and `inspect_after_story` still
    returns None on a refused commit;
  * nothing retries, amends, resets past the staged paths or pushes.

The failure is constructed rather than described: a pre-commit hook this
module wrote, reached through the target's own `core.hooksPath` so a
developer's global hooks directory cannot stand in for it, that refuses the
commit and says so on stderr once it has been armed. In narrow mode it is
armed by the fake inspector at the moment of the invocation, after the run's
own commits have landed and before the record's is attempted, so the run
completes as it would and only the record commit is refused.

Every absence asserted here has its control beside it: the check that
nothing of the record is staged is shown seeing a sibling file that is, the
dry run's silence about the record is shown against the real run's line, the
"no line was added" claim is a count against the refused run's, and each
source scan is shown reporting a planted violation.

The fixtures are the ones `test_an_inspection_records_what_it_cost` already
builds — a target whose history directory is tracked, standing on the story
branch so a run works in it, a fake inspector that reaches no model — because
what is asked here is asked of the same record that module reads back.
"""
from __future__ import annotations

import ast
import contextlib
import inspect as introspection
import io
import json
import subprocess
from pathlib import Path

import pytest

import agent_runner
import conftest
import harness_config
import inspection
import story_coordinator
import story_inspection
from test_an_inspection_records_what_it_cost import (  # noqa: F401 - shared
    # fixtures and idioms, used by name
    ROOMY_CAP,
    SIBLING_SOURCE,
    STORY_ID,
    WORKFLOW,
    Inspector,
    broad_inspection,
    broad_segment,
    build_target,
    committed_paths,
    harness,
    head_subject,
    messages,
    narrow_run,
    no_model,
    run_dir_of,
    target,
)

#: What the fixture hook says on stderr when it refuses a commit. A token no
#: harness message carries, so finding it in a detail or an events.log line
#: is finding what git relayed rather than a sentence the harness composed.
HOOK_SAID = "zzz-the-fixture-hook-refused-this-commit"

#: What a planted failure says, for the paths where the producer's own code
#: raised rather than git having refused anything.
RAISED = "zzz-the-commit-raised-inside-the-producer"

#: A path outside both scope keys the fixture target declares, so a run whose
#: stages changed only this has nothing to inspect and reaches the record
#: commit without an invocation.
OUT_OF_SCOPE_FILE = "docs/x.md"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


# ==========================================================================
# The refusing hook, and reading the index it leaves
# ==========================================================================


def rejecting_hook(root: Path, arm: Path) -> None:
    """A pre-commit hook that refuses every commit once `arm` exists.

    Installed through the repository's own `core.hooksPath` rather than into
    `.git/hooks`, because a developer whose global git configuration points
    hooks elsewhere would never run a hook written there — and a hook that
    is not run is a failure that was never constructed.
    """
    hooks = Path(root) / ".fixture-hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-commit").write_text(
        "#!/bin/sh\n"
        f"if [ -e '{arm}' ]; then\n"
        f"  echo '{HOOK_SAID}' >&2\n"
        "  exit 1\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8")
    (hooks / "pre-commit").chmod(0o755)
    _git(root, "config", "core.hooksPath", str(hooks))
    # Ignored, so the hooks directory is neither dirt the pre-flight would
    # refuse on nor something a commit here could fold in.
    exclude = Path(root) / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as handle:
        handle.write(".fixture-hooks/\n")


@pytest.fixture
def arm(tmp_path: Path) -> Path:
    """The file whose existence turns the hook on. Outside the target, so
    arming it dirties nothing."""
    return tmp_path / "arm-the-hook"


def arming(arm: Path):
    """An inspector action that arms the hook at the moment of the
    invocation — after the run's own commits, before the record's."""
    def act(_target: Path) -> None:
        arm.write_text("armed\n", encoding="utf-8")
    return act


def staged(root: Path, *paths: str) -> bool:
    """Whether anything under `paths` — or anything at all, given none — is
    staged. Read the way the producers read it, so the two cannot disagree
    about what "nothing staged" means."""
    completed = subprocess.run(
        ["git", "-C", str(root), "diff", "--cached", "--quiet", "--", *paths],
        capture_output=True, text=True)
    return completed.returncode != 0


def stage_the_sibling(root: Path) -> None:
    """Something staged that is not the record, so an unstage that reached
    past the record's paths would be seen taking it."""
    (Path(root) / SIBLING_SOURCE).write_text("def b():\n    return 22\n",
                                             encoding="utf-8")
    _git(root, "add", "--", SIBLING_SOURCE)


def left_in_the_working_tree(root: Path, path: str) -> bool:
    """Whether the pre-flight's own reader would refuse on `path`.

    `dirty_paths` reports what `git status --porcelain` reports, and for a
    file inside a directory nothing tracks yet that is the directory — which
    is what a broad-mode record's moment segment is — so the path is matched
    under an entry rather than only against one.
    """
    return any(path == entry or path.startswith(entry)
               for entry in story_coordinator.dirty_paths(root))


def broad_record_paths(root: Path, config: dict) -> list[str]:
    """The paths a broad-mode record commit stages, derived as the producer
    derives them, off the segment the record was written to."""
    return list(inspection.record_paths(root, config,
                                        segment=broad_segment(root)))


def narrow_record_paths(root: Path, config: dict) -> list[str]:
    return list(story_inspection.record_paths(root, config, STORY_ID))


def printed_report(report, dry_run: bool) -> tuple[str, int]:
    """What `scripts/l5-inspect` prints for one report, and what it returns,
    through the script itself — the wording lives there and is read there."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = conftest.load_script("l5-inspect").report(report, dry_run)
    return buffer.getvalue(), code


def record_lines(printed: str) -> list[str]:
    """The report's lines about the record commit: the ones that say whether
    it was committed. Every other line the report prints is about scopes,
    briefs, drops and the queue, none of which is committed."""
    return [line for line in printed.splitlines() if "committed" in line]


def events_log_lines(run_dir: Path) -> list[str]:
    path = Path(run_dir) / "events.log"
    if not path.is_file():
        return []
    return [line.split("] ", 1)[-1]
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


# ==========================================================================
# Broad mode: inspection.record says what it did
# ==========================================================================


def test_the_hook_refuses_a_commit_and_the_check_sees_a_staged_file(
        target, arm):
    """The controls for everything below, shown before anything rests on
    them: the armed hook really refuses a commit and really says so, and
    `staged` really reports a staged file."""
    rejecting_hook(target, arm)
    stage_the_sibling(target)
    assert staged(target)
    assert staged(target, SIBLING_SOURCE)

    arm.write_text("armed\n", encoding="utf-8")
    refused = subprocess.run(
        ["git", "-C", str(target), "commit", "-m", "the hook sees this"],
        capture_output=True, text=True)
    assert refused.returncode != 0
    assert HOOK_SAID in refused.stderr
    assert staged(target, SIBLING_SOURCE), "the refusal itself unstages nothing"


def test_a_broad_mode_record_the_hook_refuses_is_reported_and_left_unstaged(
        target, arm):
    """Written, not committed, git's words in the detail, and the tree left as
    the commit found it: the record modified with nothing of it staged, and
    the sibling that was staged before still staged after."""
    rejecting_hook(target, arm)
    stage_the_sibling(target)
    arm.write_text("armed\n", encoding="utf-8")
    config = harness_config.load_config(target)
    head_before = _git(target, "rev-parse", "HEAD").stdout.strip()

    recorded = inspection.record(target, config, inspection.Report())

    assert recorded.written
    assert not recorded.committed
    assert HOOK_SAID in recorded.detail, recorded
    assert _git(target, "rev-parse", "HEAD").stdout.strip() == head_before

    paths = broad_record_paths(target, config)
    assert paths
    for path in paths:
        assert (target / path).is_file()
    assert not staged(target, *paths)
    # The unstage named the record's paths and nothing else: the sibling is
    # still staged, which is also what shows the check above can see a
    # staged file at all.
    assert staged(target, SIBLING_SOURCE)
    assert staged(target)
    # Left in the working tree, which is what the next run's pre-flight
    # refuses on — and what the detail above is for.
    for path in paths:
        assert left_in_the_working_tree(target, path), \
            (path, story_coordinator.dirty_paths(target))


def test_a_broad_mode_record_that_commits_is_reported_as_committed(target):
    """The success path is unchanged in what it stages and what it says about
    itself, and the result says so."""
    config = harness_config.load_config(target)
    recorded = inspection.record(target, config, inspection.Report())

    assert recorded == inspection.Recorded(written=True, committed=True)
    assert head_subject(target) == inspection.COMMIT_SUBJECT
    assert committed_paths(target) == broad_record_paths(target, config)
    assert story_coordinator.dirty_paths(target) == []


def test_a_record_that_could_not_be_written_is_reported_on_the_same_terms(
        target):
    """A history directory that cannot be written costs the record and
    nothing else, and the result says the record was not written rather than
    passing for one that was."""
    config = harness_config.load_config(target)
    history = harness_config.history_dir(target, config)
    assert not history.exists(), history
    history.write_text("a file where the directory should be\n",
                       encoding="utf-8")
    head_before = _git(target, "rev-parse", "HEAD").stdout.strip()

    recorded = inspection.record(target, config, inspection.Report())

    assert not recorded.written
    assert not recorded.committed
    assert recorded.detail, recorded
    assert _git(target, "rev-parse", "HEAD").stdout.strip() == head_before
    assert not staged(target)


def test_a_raise_inside_the_broad_commit_becomes_a_not_committed_result(
        target, monkeypatch):
    """No path in `inspection.record` raises to its caller: the exception is
    in the detail, and the record it was about is still on disk."""
    def raises(*_args, **_keywords):
        raise RuntimeError(RAISED)
    monkeypatch.setattr(inspection, "commit_record_paths", raises)
    config = harness_config.load_config(target)

    recorded = inspection.record(target, config, inspection.Report())

    assert recorded.written
    assert not recorded.committed
    assert RAISED in recorded.detail, recorded
    assert broad_record_paths(target, config)


# ==========================================================================
# Broad mode: the report a developer reads says it
# ==========================================================================


def test_the_report_says_the_record_was_not_committed_and_why(
        target, harness, arm):
    """Through the script's own `report`, which is what the command prints
    and returns: one line naming the record as NOT committed, carrying the
    hook's words, and an exit status of zero."""
    rejecting_hook(target, arm)
    arm.write_text("armed\n", encoding="utf-8")
    report, _inspector = broad_inspection(target, harness)

    assert report.record.written
    assert not report.record.committed
    printed, code = printed_report(report, report.dry_run)

    assert code == 0
    lines = record_lines(printed)
    assert len(lines) == 1, printed
    assert "NOT committed" in lines[0]
    assert HOOK_SAID in lines[0]
    assert not staged(target, *broad_record_paths(
        target, harness_config.load_config(target)))


def test_the_report_says_the_record_was_committed_where_it_was(
        target, harness):
    """The line is printed on every real inspection, not only on a failure —
    the report's own no-silent-bound idiom — and the commit it reports is the
    branch tip under the subject it has today."""
    report, _inspector = broad_inspection(target, harness)
    assert report.record.committed
    printed, code = printed_report(report, report.dry_run)

    assert code == 0
    lines = record_lines(printed)
    assert len(lines) == 1, printed
    assert "NOT" not in lines[0]
    assert head_subject(target) == inspection.COMMIT_SUBJECT


def test_a_dry_run_prints_no_record_line_and_makes_no_commit(target, harness):
    """A dry run attempts no record, and says nothing about one. The control
    is the real run above, whose report the same predicate finds the line in."""
    head_before = _git(target, "rev-parse", "HEAD").stdout.strip()
    config = harness_config.load_config(target)
    inspector = Inspector(target, config)
    report = inspection.inspect(target, config, harness, dry_run=True,
                                runner=inspector)

    assert report.dry_run
    assert report.record == inspection.Recorded()
    assert not report.record.written
    printed, code = printed_report(report, report.dry_run)
    assert code == 0
    assert record_lines(printed) == [], printed
    assert _git(target, "rev-parse", "HEAD").stdout.strip() == head_before
    assert not harness_config.history_dir(target, config).exists()


def test_a_report_built_without_a_record_says_none_was_attempted():
    """The field is defaulted, so every construction that predates it — and
    every path that never reached `record` — carries the same answer."""
    assert inspection.Report().record == inspection.Recorded()
    assert inspection.Recorded() == inspection.Recorded(
        written=False, committed=False, detail="")
    with pytest.raises(Exception):
        inspection.Recorded().committed = True  # type: ignore[misc]


# ==========================================================================
# Narrow mode: the run's own events.log says it
# ==========================================================================


def test_a_run_whose_record_commit_is_refused_completes_and_says_so(
        target, harness, monkeypatch, arm):
    """The whole run, with the hook armed by the inspector at the moment of
    the invocation: the run's own commits land, the record's is refused, the
    run still completes, and events.log carries one line saying the record
    was not committed and why, with nothing of it staged afterwards."""
    rejecting_hook(target, arm)
    code, inspector, _runner = narrow_run(target, harness, monkeypatch,
                                          act=arming(arm))

    assert code == 0
    assert inspector.invocations, "the inspection did not run"
    said = [line for line in messages(target) if HOOK_SAID in line]
    assert len(said) == 1, messages(target)
    assert STORY_ID in said[0]
    assert "NOT committed" in said[0]

    config = harness_config.load_config(target)
    paths = narrow_record_paths(target, config)
    assert paths
    assert not staged(target, *paths)
    for path in paths:
        assert left_in_the_working_tree(target, path), \
            (path, story_coordinator.dirty_paths(target))
    # The commit that was refused is on no branch, and the run's own
    # completion commit is the tip it left.
    assert story_inspection.COMMIT_SUBJECT.format(story_id=STORY_ID) not in \
        _git(target, "log", "--format=%s").stdout


def test_a_narrow_mode_commit_that_lands_adds_no_line_to_events_log(
        target, harness, monkeypatch, tmp_path, arm):
    """A commit that landed adds nothing: events.log already carries the
    record line the inspection wrote. The control is the refused run, driven
    identically in a second target, whose events.log is one line longer —
    the note — and nothing else."""
    code, _inspector, _runner = narrow_run(target, harness, monkeypatch)
    assert code == 0
    assert head_subject(target) == \
        story_inspection.COMMIT_SUBJECT.format(story_id=STORY_ID)
    landed = messages(target)
    assert not any(HOOK_SAID in line for line in landed)
    assert not any("NOT committed" in line for line in landed), landed

    refused_target = build_target(tmp_path / "refused",
                                  **{story_inspection.MAX_FILES_KEY: ROOMY_CAP})
    rejecting_hook(refused_target, arm)
    code, _inspector, _runner = narrow_run(refused_target, harness,
                                           monkeypatch, act=arming(arm))
    assert code == 0
    refused = messages(refused_target)
    assert len(refused) == len(landed) + 1, (landed, refused)
    assert [line for line in refused if "NOT committed" not in line] == \
        [line for line in landed if "NOT committed" not in line]


@pytest.fixture
def history_of(target):
    """The run-side history directory established for a direct call, as
    `run_story` establishes it for a run, and taken down afterwards."""
    directory = harness_config.history_dir(
        target, harness_config.load_config(target))
    story_coordinator.set_history_dir(directory)
    try:
        yield directory
    finally:
        story_coordinator.set_history_dir(None)


def out_of_scope_run_dir(tmp_path: Path) -> Path:
    """A run directory named for the story — which is how the writer-side
    resolver picks the story's segment — whose stages changed only a path
    outside both scope keys, so the inspection has nothing to invoke on."""
    run_dir = tmp_path / STORY_ID
    run_dir.mkdir()
    for stage in WORKFLOW["stages"]:
        name = stage.get("changed_files")
        if name:
            (run_dir / name).write_text(json.dumps(
                {"modified": [OUT_OF_SCOPE_FILE], "created": [],
                 "deleted": []}), encoding="utf-8")
    return run_dir


def test_inspect_after_story_returns_none_and_raises_nothing_on_a_refusal(
        target, harness, arm, tmp_path, history_of, no_model):
    """The producer's totality, asked directly: a refused record commit is
    returned as nothing and raised as nothing, noted once in the run's
    events.log with the hook's words, and leaves nothing staged."""
    rejecting_hook(target, arm)
    arm.write_text("armed\n", encoding="utf-8")
    run_dir = out_of_scope_run_dir(tmp_path)
    config = harness_config.load_config(target)

    result = story_inspection.inspect_after_story(
        run_dir, target, config, harness, STORY_ID, WORKFLOW["stages"])

    assert result is None
    assert no_model.calls == 0
    said = [line for line in events_log_lines(run_dir) if HOOK_SAID in line]
    assert len(said) == 1, events_log_lines(run_dir)
    assert STORY_ID in said[0]
    paths = narrow_record_paths(target, config)
    assert paths, "no record was written, so the commit was never attempted"
    assert not staged(target, *paths)
    assert story_inspection.COMMIT_SUBJECT.format(story_id=STORY_ID) not in \
        _git(target, "log", "--format=%s").stdout


def test_a_commit_that_stages_nothing_says_so_in_one_line(target, tmp_path):
    """Nothing written for the story, so nothing to stage: the result says
    the record was not committed, and events.log says so in exactly one line
    carrying the result's own detail."""
    run_dir = tmp_path / STORY_ID
    run_dir.mkdir()
    config = harness_config.load_config(target)
    assert narrow_record_paths(target, config) == []
    head_before = _git(target, "rev-parse", "HEAD").stdout.strip()

    recorded = story_inspection.commit_record(run_dir, target, config,
                                              STORY_ID)

    assert not recorded.committed
    assert recorded.detail, recorded
    lines = events_log_lines(run_dir)
    assert len(lines) == 1, lines
    assert STORY_ID in lines[0]
    assert recorded.detail in lines[0]
    assert _git(target, "rev-parse", "HEAD").stdout.strip() == head_before
    assert not staged(target)


def test_a_raise_inside_commit_record_becomes_a_noted_result(
        target, tmp_path, monkeypatch):
    """No path in `commit_record` raises to its caller: the exception is in
    the detail and in the one line events.log gains."""
    def raises(*_args, **_keywords):
        raise RuntimeError(RAISED)
    monkeypatch.setattr(inspection, "commit_record_paths", raises)
    run_dir = tmp_path / STORY_ID
    run_dir.mkdir()
    config = harness_config.load_config(target)

    recorded = story_inspection.commit_record(run_dir, target, config,
                                              STORY_ID)

    assert not recorded.committed
    assert RAISED in recorded.detail, recorded
    lines = events_log_lines(run_dir)
    assert len(lines) == 1, lines
    assert RAISED in lines[0]


# ==========================================================================
# What the commit is made of: no retry, no amend, no reset past the paths,
# no push — and every narrow-mode call site names the run directory
# ==========================================================================


#: Git words neither producer may use. `reset` would reach past the staged
#: paths where `restore --staged -- <paths>` cannot; `-A` would stage what
#: was never the record; the rest are the repairs the story leaves alone.
FORBIDDEN_GIT_WORDS = frozenset({"push", "reset", "--amend", "-A", "--hard",
                                 "--force", "-f"})


def string_constants(source: str) -> set[str]:
    return {node.value for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def git_words_of(function) -> set[str]:
    return string_constants(introspection.getsource(function))


def test_the_commit_restores_the_index_and_neither_retries_amends_resets_nor_pushes():
    """The one commit both modes make names `restore --staged`, and none of
    the forbidden words. Both producers are scanned: the shared commit, and
    each producer's own body around it."""
    for function in (inspection.commit_record_paths, inspection.record,
                     story_inspection.commit_record):
        words = git_words_of(function)
        assert words & FORBIDDEN_GIT_WORDS == set(), (function.__name__, words)
    shared = git_words_of(inspection.commit_record_paths)
    assert {"restore", "--staged", "commit", "add"} <= shared, shared


def test_the_same_scan_reports_a_planted_amend_and_a_planted_push():
    """The control: a source carrying either forbidden word is reported."""
    planted = ("def commit(argv, paths):\n"
               "    run([*argv, 'commit', '--amend', '--', *paths])\n"
               "    run([*argv, 'push'])\n")
    assert string_constants(planted) & FORBIDDEN_GIT_WORDS == {"--amend",
                                                               "push"}


def calls_to_commit_record(source: str) -> list[ast.Call]:
    return [node for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "commit_record"]


def calls_not_passing_the_run_dir(source: str) -> list[int]:
    """Line numbers of `commit_record` calls whose first argument is not the
    run directory by name."""
    return [call.lineno for call in calls_to_commit_record(source)
            if not (call.args and isinstance(call.args[0], ast.Name)
                    and call.args[0].id == "run_dir")]


def test_every_commit_record_call_site_passes_the_run_directory_first():
    """`run_dir` is the first parameter, and every call in the module hands
    it over — including the ones committing under the completion filing's
    own subject, which the brief did not name and the story took in."""
    parameters = list(introspection.signature(
        story_inspection.commit_record).parameters)
    assert parameters[0] == "run_dir"
    source = Path(story_inspection.__file__).read_text(encoding="utf-8")
    assert calls_to_commit_record(source), "the scan found no call at all"
    assert calls_not_passing_the_run_dir(source) == []
    assert any(
        isinstance(arg, ast.Name) and arg.id == "FILED_COMMIT_SUBJECT"
        for call in calls_to_commit_record(source) for arg in call.args
    ), "no call site commits under the filing subject"


def test_the_same_scan_reports_a_call_that_omits_the_run_directory():
    planted = ("def after(target_root, config, story_id):\n"
               "    commit_record(target_root, config, story_id)\n")
    assert calls_not_passing_the_run_dir(planted) == [2]
