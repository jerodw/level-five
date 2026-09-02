"""story-100 validation: a completed story is inspected.

The Inspector was a capability a developer invoked. This story makes it
standing: when a run completes, the coordinator inspects what that story
touched and files briefs for what it finds. Everything about *what a good
finding is* stays where it was; what is new is the run integration, and that is
the whole of what this module is about.

The subjects are kept apart deliberately:

  * **the guarantee.** A run whose inspection fails is driven first, because it
    is what the whole story is for. Every part of the inspection is broken in
    turn — the agent unavailable, the filed query failing, the enqueue dropping
    every item, the record commit unable to stage — and each run must still
    commit its work, still complete, and exit with the status the same fixture
    has with the feature unconfigured. That status is asserted by *running* the
    unconfigured fixture and comparing, rather than by writing a zero here.

  * **the ordering.** Observed rather than read off the source. The fake
    inspector records the subject of the commit at the target's HEAD at the
    moment it is invoked, and the target's configured sync command records
    every entry it is asked to file, both into one journal. So the journal
    itself says the inspection ran on a HEAD the completion commit had already
    moved, and that the brief it enqueued was filed by the sweep *after* it.

  * **the expansion and the cap.** Computed with no model at all: the two are
    driven as functions, against changed-files records this module wrote and a
    repository it built, and the autouse guard below means an invocation
    anywhere in those tests would fail the test rather than reach a provider.

  * **the record and its commit.** What an inspection did outlives the run
    directory, so the record and the commit that makes it durable are asserted
    on the repository rather than on the run.

  * **the runs that inspect nothing.** An escalated run, a run paused in place
    for capacity and a run that stopped rather than wait out a capacity reset,
    each against the same fixture that inspects when it completes.

Every absence asserted here carries a demonstration that it can fail:

  * "the unconfigured run appended nothing" sits beside the configured run
    under the same fixture, where the same reading finds the line;
  * "no invocation was made" sits beside the completing run, where the same
    fake records one;
  * "the subdirectory is not in scope" sits beside the sibling that is;
  * "the record commit stages nothing else" sits beside a file the fake
    inspector really did change, which must be left in the working tree;
  * "completion_commits does not match the record commit" sits beside the
    completion commit, which the same call must match;
  * "the framing does not render as None" sits beside the same extraction over
    a rendering with the value removed, which must report it.

Nothing here reaches a model: `agent_runner.run_agent` is replaced for every
test in this module by a fake that fails the test if it is called without
having been installed deliberately. Nothing here resolves a baseline out of
git; the two repositories it reads are ones it built.
"""
from __future__ import annotations

import ast
import inspect as inspect_module
import json
import subprocess
from pathlib import Path

import pytest

import agent_runner
import conftest
import harness_config
import inspection
import outbox
import schema_validator
import story_brief
import story_coordinator
import story_inspection
from agent_runner import AgentResult, CapacityStop

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
ORCHESTRATION = REPO_ROOT / "orchestration"

STORY_ID = "story-001"

PASSED = {"status": "passed", "blocking_issues": [], "unverified": [],
          "retry_recommended": False}

#: One capacity signal the agent runner holds, read off the constant so this
#: module names no signal of its own.
A_CAPACITY_SIGNAL = agent_runner.CAPACITY_SIGNALS[0]


# ==========================================================================
# The workflow, the rules and the target
# ==========================================================================


#: The definition these runs execute. Built rather than resolved: whether a
#: completed run inspects what it changed is a property of the coordinator, and
#: the definition it walks is an input to it. Two writing stages, so "one
#: invocation whatever mix of source and tests files the run changed" is a
#: statement about two records rather than about one.
WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        outputs=(conftest.TEST_RESULTS, conftest.TESTER_CHANGED_FILES),
        changed_files=conftest.TESTER_CHANGED_FILES,
        schemas={conftest.TEST_RESULTS: "test-results",
                 conftest.TESTER_CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result"},
        retry_routing={"the-code": {"stage": conftest.StageRef(0),
                                    "when": "the behaviour is missing"}}),
    name="post-story-inspection-workflow",
)

WRITING, VALIDATING, VERIFYING = [stage["name"] for stage in WORKFLOW["stages"]]

#: A prefix the fixture's own rules block and this repository's do not, which
#: is what makes "a blocked path is left out of the scope" a fact about the
#: reading rather than about the prefixes that happen to be deployed.
VAULT = "src/zzz-vault/"

#: The rule set these runs execute under. The fixture's own, for the reason
#: every converted module states: what a run does with a blocked path is the
#: subject, and which paths this repository blocks is an input to it.
FIXTURE_RULES = {
    "max_retries": 2,
    "require_verifier_pass": True,
    "blocked_paths": [".git/", ".harness/runs/", "rules/", VAULT],
}

#: The parts of the tree the fixture target declares, and the files in them.
#: `SUBDIRECTORY_FILE` is what makes "one level and not recursively" assertable,
#: `OUT_OF_SCOPE_FILE` is what makes "outside both scope keys" assertable, and
#: `DELETED_FILE` is tracked at the start and removed by the story.
SOURCE_DIR = "src/"
TESTS_DIR = "tests/"
CHANGED_SOURCE = f"{SOURCE_DIR}a.py"
SIBLING_SOURCE = f"{SOURCE_DIR}b.py"
SUBDIRECTORY_FILE = f"{SOURCE_DIR}sub/deep.py"
BLOCKED_FILE = f"{VAULT}secret.py"
BLOCKED_SIBLING = f"{VAULT}beside-the-secret.py"
DELETED_FILE = f"{SOURCE_DIR}gone.py"
CHANGED_TEST = f"{TESTS_DIR}t_a.py"
SIBLING_TEST = f"{TESTS_DIR}t_b.py"
OUT_OF_SCOPE_FILE = "docs/x.md"

#: A path under a scope key that the repository does not track, for a run whose
#: writing stage records a file that is not there to read. It is deliberately
#: not in `TRACKED`: the expansion takes its paths from the tracked listing
#: rather than from the record, which is how a deleted file and a
#: never-existed one come out the same way.
UNTRACKED_CHANGE = f"{SOURCE_DIR}removed.py"

TRACKED = {
    CHANGED_SOURCE: "def a():\n    return 1\n",
    SIBLING_SOURCE: "def b():\n    return 2\n",
    SUBDIRECTORY_FILE: "def deep():\n    return 3\n",
    BLOCKED_FILE: "nothing an inspection may read\n",
    BLOCKED_SIBLING: "nor this\n",
    DELETED_FILE: "def gone():\n    return 4\n",
    CHANGED_TEST: "def check_a():\n    assert True\n",
    SIBLING_TEST: "def check_b():\n    assert True\n",
    OUT_OF_SCOPE_FILE: "# how the thing works\n",
}

#: What the fixture allows one inspection to take into scope. Larger than the
#: whole expansion, so the runs below are not silently trimmed; the cap's own
#: assertions configure their own.
ROOMY_CAP = 60

#: Where the target's sync command lives. It records every entry it is handed
#: and then answers with the transport's own code for a transient failure, so
#: the entry stays pending and keeps the payload an assertion below reads —
#: while the record of the attempt is what makes the sweep's position in the
#: journal observable.
SYNC_COMMAND_REL = "sync/records-and-fails.sh"

#: The subject of the commit the target is built on, so the journal can be read.
SETUP_SUBJECT = "the tree this run starts from"


def transient_exit_code() -> int:
    """The transport's own code for a transient failure, imported where it is
    used so this module carries no second spelling of it."""
    import command_transport

    return command_transport.TRANSIENT_EXIT_CODE


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


@pytest.fixture
def harness(tmp_path) -> Path:
    """A harness root carrying the built workflow, the fixture rules and an
    Inspector template this module wrote."""
    root = conftest.materialize_workflow(
        WORKFLOW, tmp_path / "inspection-harness", rules=FIXTURE_RULES)
    (root / "prompts" / inspection.INSPECTOR_PROMPT).write_text(
        inspector_template(), encoding="utf-8")
    return root


#: The context fields the fixture's Inspector template renders, named here for
#: the reason `conftest.BUILT_PROMPT_FIELDS` is: an assertion that a value
#: reached the prompt finds it by the label it derived from this tuple rather
#: than from what the shipped template happens to say today.
INSPECTOR_FIELDS = ("framing", "scope", "scope_kind", "scope_paths",
                    "repository_standards", "already_filed", "findings_path")


def inspector_template() -> str:
    """Every field on its own line with the placeholder on the line below it,
    so an assertion can find a value by its label."""
    lines = ["# a template this module wrote", ""]
    for name in INSPECTOR_FIELDS:
        lines += [f"{name}:", f"{{{{{name}}}}}", ""]
    return "\n".join(lines)


def rendered_field(prompt: str, name: str) -> str:
    """The value the fixture template rendered under one label."""
    lines = prompt.splitlines()
    where = lines.index(f"{name}:")
    return lines[where + 1]


def build_target(root: Path, journal: Path, *, ignore_history: bool = False,
                 **config_keys) -> Path:
    """A target repository a run can execute in, with a recording sync command.

    The same shape `conftest.target_root` builds — its config and its story,
    read off conftest so neither is spelled twice — with what this module needs
    added: the source layout above, a sync command at the configured path, the
    run directory, the log directory and the queue ignored, and whatever
    configuration the caller departs from.

    The run directory is ignored for the reason this harness ignores its own:
    an inspection appends its line to that run's events.log *after* the
    completion commit, so a target tracking its run directory is left holding a
    modified events.log by the story's own requirement that the counts reach
    it. What the story promises about a clean tree is promised for the
    deployment shape this builds.

    `journal` is deliberately outside the target: a file written inside it
    would be work no stage produced.
    """
    for sub in (".harness/standards", ".harness/stories", ".harness/docs",
                "sync"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    config = conftest.CONFIG.format(workflow=WORKFLOW["name"])
    config += f"source_dirs:\n  - {SOURCE_DIR}\n"
    config += f"sync_command: {SYNC_COMMAND_REL}\n"
    config += "".join(f"{key}: {value}\n"
                      for key, value in sorted(config_keys.items()))
    (root / ".harness" / "config.yaml").write_text(config, encoding="utf-8")
    (root / ".harness" / "stories" / f"{STORY_ID}.yaml").write_text(
        conftest.STORY, encoding="utf-8")
    (root / ".harness" / "standards" / "coding.md").write_text(
        "# Coding Standards\n- keep it simple\n", encoding="utf-8")
    (root / ".harness" / "standards" / "testing.md").write_text(
        "# Testing Standards\n- test everything\n", encoding="utf-8")
    (root / ".harness" / "docs" / "ARCHITECTURE.md").write_text(
        "# Sample Architecture\n", encoding="utf-8")
    for relative, text in TRACKED.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    ignored = [".harness/runs/", ".harness/logs/", "/".join(outbox.QUEUE_DIR)]
    if ignore_history:
        ignored.append(
            harness_config.history_dir(root, {}).relative_to(root).as_posix())
    (root / ".gitignore").write_text(
        "".join(f"{one}\n" for one in ignored), encoding="utf-8")

    command = root / SYNC_COMMAND_REL
    command.write_text(
        "#!/bin/sh\n"
        f'printf "filed %s\\n" "$L5_SYNC_KEY" >> "{journal}"\n'
        f"exit {transient_exit_code()}\n",
        encoding="utf-8")
    command.chmod(0o755)

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", SETUP_SUBJECT)
    return root


# ==========================================================================
# The fake inspector, installed in place of the agent runner
# ==========================================================================


def finding(ordinal: int = 1, **overrides) -> dict:
    """One conforming finding, named for the workflow this fixture defines."""
    schema = schema_validator.load_schema(inspection.BRIEF_SCHEMA)
    found = {
        "title": f"zzz: the {ordinal}th thing this change left behind",
        "slug": f"zzz-finding-{ordinal}",
        "body": f"{CHANGED_SOURCE}:1 disagrees with {SIBLING_SOURCE}:1",
        "category": schema["properties"]["category"]["enum"][0],
        "severity": min(schema["properties"]["severity"]["enum"]),
        "confidence": schema["properties"]["confidence"]["enum"][0],
        "effort": schema["properties"]["effort"]["enum"][0],
        "workflow": WORKFLOW["name"],
        "paths": [CHANGED_SOURCE],
    }
    found.update(overrides)
    return found


class Inspector:
    """Stands in for `agent_runner.run_agent` for the post-story inspection.

    It reaches no model: it records what it was handed, notes the subject of
    the commit the target's HEAD stands on at that moment — which is the whole
    of how the ordering below is observed — performs whatever the caller asked
    this invocation to do, and writes the findings the caller supplied.
    """

    def __init__(self, target: Path, config: dict, journal: Path, *,
                 findings=(), act=None, raises: str = ""):
        self.target = Path(target)
        self.artifact = inspection.findings_paths(self.target, config)[0]
        self.journal = Path(journal)
        self.findings = list(findings)
        self.act = act
        self.raises = raises
        self.invocations: list[dict] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode,
                 model, allowed_tools=None, max_budget_usd=None,
                 suite_command=None):
        self.invocations.append({
            "prompt": prompt, "stage": stage, "cwd": Path(cwd),
            "permission_mode": permission_mode, "model": model,
            "allowed_tools": allowed_tools, "max_budget_usd": max_budget_usd,
        })
        with self.journal.open("a", encoding="utf-8") as handle:
            handle.write(f"inspected at {head_subject(self.target)}\n")
        if self.act is not None:
            self.act(self.target)
        if self.raises:
            raise RuntimeError(self.raises)
        self.artifact.parent.mkdir(parents=True, exist_ok=True)
        self.artifact.write_text(json.dumps({"findings": self.findings}),
                                 encoding="utf-8")
        return AgentResult(ok=True, result_text="inspected")

    @property
    def prompt(self) -> str:
        assert self.invocations, "no inspection invocation was made"
        return self.invocations[0]["prompt"]


class NoInvocationExpected:
    """The default in place of the agent runner: being called at all is the
    failure. Every test that wants an invocation installs its own fake."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **keywords):
        self.calls += 1
        raise AssertionError(
            "the post-story inspection reached agent_runner.run_agent, which "
            "this module replaces so that nothing here can invoke a model")


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    """Substituted for every test in this module, so a path that reaches for
    the real runner fails here rather than talking to a provider."""
    guard = NoInvocationExpected()
    monkeypatch.setattr(agent_runner, "run_agent", guard)
    return guard


def install(monkeypatch, inspector: Inspector) -> Inspector:
    monkeypatch.setattr(agent_runner, "run_agent", inspector)
    return inspector


# ==========================================================================
# Driving one run
# ==========================================================================


class Runner:
    """The fake agent runner the coordinator is handed for the run's stages.

    `fails_at` names a stage whose invocation comes back not-ok, with
    `capacity` deciding whether that failure is a capacity stop — which is how
    the escalated run and the two stopped runs below come from one runner.

    `extra_changed` names paths the writing stage records beside the file it
    actually edits, so a run can record a change the expansion leaves out —
    which is the only way to observe what the run's own record says about one.
    """

    def __init__(self, target_root: Path, journal: Path, *,
                 fails_at: str | None = None, capacity=None,
                 extra_changed=()):
        self.target_root = Path(target_root)
        self.run_dir = self.target_root / ".harness" / "runs" / STORY_ID
        self.journal = Path(journal)
        self.fails_at = fails_at
        self.capacity = capacity
        self.extra_changed = tuple(extra_changed)
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 **declared):
        self.calls.append(stage)
        with self.journal.open("a", encoding="utf-8") as handle:
            handle.write(f"stage {stage}\n")
        if stage == self.fails_at:
            return AgentResult(ok=False, result_text=f"{stage} stopped",
                               capacity=self.capacity)
        if stage == WRITING:
            _write(self.run_dir / conftest.CHANGED_FILES,
                   {"modified": [CHANGED_SOURCE, *self.extra_changed],
                    "created": [], "deleted": []})
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Did the work.\n", encoding="utf-8")
            (self.target_root / CHANGED_SOURCE).write_text(
                "def a():\n    return 11\n", encoding="utf-8")
        elif stage == VALIDATING:
            _write(self.run_dir / conftest.TEST_RESULTS, {"tests_written": 1})
            _write(self.run_dir / conftest.TESTER_CHANGED_FILES,
                   {"modified": [CHANGED_TEST], "created": [], "deleted": []})
            (self.target_root / CHANGED_TEST).write_text(
                "def check_a():\n    assert True  # and again\n",
                encoding="utf-8")
        elif stage == VERIFYING:
            _write(self.run_dir / conftest.VERIFICATION_RESULT, PASSED)
        return AgentResult(ok=True, result_text=f"{stage} done")


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run(target: Path, harness: Path, runner: Runner) -> int:
    return story_coordinator.run_story(
        STORY_ID, harness, target, runner, sleep=lambda _seconds: None)


def run_dir_of(target: Path) -> Path:
    return target / ".harness" / "runs" / STORY_ID


def state_of(target: Path) -> dict:
    return json.loads(
        (run_dir_of(target) / "state.json").read_text(encoding="utf-8"))


def events(target: Path) -> list[str]:
    path = run_dir_of(target) / "events.log"
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def messages(target: Path) -> list[str]:
    """Each events.log line with its timestamp removed, so two runs can be
    compared for what they said rather than for when they said it."""
    return [line.split("] ", 1)[-1] for line in events(target)]


def journal_lines(journal: Path) -> list[str]:
    if not journal.is_file():
        return []
    return [line for line in journal.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def head_subject(root: Path) -> str:
    return _git(root, "log", "-1", "--format=%s").stdout.strip()


def subjects(root: Path) -> list[str]:
    return _git(root, "log", "--format=%s").stdout.splitlines()


def committed_paths(root: Path, revision: str = "HEAD") -> list[str]:
    listed = _git(root, "show", "--name-only", "--format=", revision)
    return [line for line in listed.stdout.splitlines() if line.strip()]


def history_records(target: Path) -> list[dict]:
    """The inspection log's records, found by the declaration that routes this
    kind rather than by a filename written here."""
    directory = harness_config.history_dir(target, {})
    found: list[dict] = []
    for relative in story_inspection.record_paths(target, {}):
        path = target / relative
        assert path.parent == directory, (path, directory)
        if path.is_file():
            found += [json.loads(line)
                      for line in path.read_text(encoding="utf-8").splitlines()
                      if line.strip()]
    return found


def queue_entries(target: Path) -> list[dict]:
    queue = outbox.queue_dir(target)
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in outbox.entry_files(queue)]


def completing_run(tmp_path: Path, harness: Path, monkeypatch, *,
                   name: str = "inspected", findings=(), act=None,
                   raises: str = "", ignore_history: bool = False,
                   extra_changed=(), **config_keys):
    """One completing run of the fixture, with the inspector installed.

    Returns `(target, journal, code, inspector, runner)`. The cap is supplied
    unless the caller departs from it, so a run built by this helper inspects.
    `extra_changed` reaches the writing stage's changed-files record, so a
    caller can give the run a change the expansion will leave out.
    """
    config_keys.setdefault(story_inspection.MAX_FILES_KEY, ROOMY_CAP)
    journal = tmp_path / f"{name}-journal.txt"
    target = build_target(tmp_path / name, journal,
                          ignore_history=ignore_history, **config_keys)
    config = harness_config.load_config(target)
    inspector = install(monkeypatch, Inspector(
        target, config, journal, findings=findings, act=act, raises=raises))
    runner = Runner(target, journal, extra_changed=extra_changed)
    code = run(target, harness, runner)
    return target, journal, code, inspector, runner


# ==========================================================================
# The guarantee: an inspection that fails costs the run nothing
# ==========================================================================


def break_the_filed_query(target: Path) -> None:
    """A filed-query command the harness will run and that cannot answer.

    Configured before the run rather than during it, so it is the query the
    inspection actually makes that fails.
    """
    path = target / "sync" / "cannot-answer.sh"
    path.write_text("#!/bin/sh\necho 'the tracker refused' >&2\nexit 3\n",
                    encoding="utf-8")
    path.chmod(0o755)


def lock_the_index(target: Path) -> None:
    """The record commit's staging made to fail, constructed rather than
    described: a lock file git will refuse to take the index past."""
    (target / ".git" / "index.lock").write_text("held", encoding="utf-8")


def unlock_the_index(target: Path) -> None:
    lock = target / ".git" / "index.lock"
    if lock.is_file():
        lock.unlink()


#: The two parts of the inspection whose failure is constructed at the
#: invocation itself, each keyed by what a failure message should say. The
#: filed query and the enqueue are broken in their own tests below, because
#: each is broken at a different seam.
FAILURES = {
    "the agent is unavailable": {"raises": "no provider answered"},
    "the record commit cannot stage": {"act": lock_the_index},
}


@pytest.mark.parametrize("name", sorted(FAILURES))
def test_a_run_whose_inspection_fails_still_commits_completes_and_exits_as_it_would(
        name, tmp_path, harness, monkeypatch):
    """The guarantee the whole story is for.

    The failure is constructed rather than assumed, and the run must be
    indifferent to it: the work is committed, the run completes, and the exit
    status is the one the same fixture has with the feature unconfigured — a
    comparison, not a literal, because what the story promises is that the
    inspection makes no difference.
    """
    target, _journal, code, inspector, runner = completing_run(
        tmp_path, harness, monkeypatch, name="broken",
        findings=[finding()], **FAILURES[name])
    unlock_the_index(target)

    assert runner.calls == [WRITING, VALIDATING, VERIFYING]
    assert state_of(target)["status"] == "completed"
    assert inspector.invocations, "the inspection was never attempted"
    # The work is committed — asked of the reader that decides whether a run of
    # this story finished, rather than of HEAD, because what HEAD is depends on
    # whether the failure happened above or below the record commit.
    assert story_coordinator.completion_commits(
        target, state_of(target)["branch"], STORY_ID)
    assert code == unconfigured_exit_status(tmp_path, harness)


def unconfigured_exit_status(tmp_path: Path, harness: Path) -> int:
    """The status the same fixture returns with the key unset.

    Run rather than written down, so the comparison above is against what this
    fixture actually does rather than against a zero somebody typed.
    """
    journal = tmp_path / "control-journal.txt"
    target = build_target(tmp_path / "unconfigured-control", journal)
    return run(target, harness, Runner(target, journal))


def test_a_query_that_cannot_answer_costs_the_run_nothing(
        tmp_path, harness, monkeypatch):
    """The filed query broken at the seam the inspection asks through.

    The dedupe not having run is reported and the findings are filed anyway,
    which is the inspection's own rule; what matters here is that the run
    completed and the brief still reached the queue.
    """
    journal = tmp_path / "broken-query-journal.txt"
    target = build_target(tmp_path / "with-a-broken-query", journal,
                          filed_query_command="sync/cannot-answer.sh",
                          **{story_inspection.MAX_FILES_KEY: ROOMY_CAP})
    break_the_filed_query(target)
    _git(target, "add", "-A")
    _git(target, "commit", "-q", "-m", "the query command")
    config = harness_config.load_config(target)
    install(monkeypatch, Inspector(target, config, journal,
                                   findings=[finding()]))

    assert run(target, harness, Runner(target, journal)) == 0
    assert state_of(target)["status"] == "completed"
    assert len(queue_entries(target)) == 1


def test_an_enqueue_that_drops_every_item_costs_the_run_nothing(
        tmp_path, harness, monkeypatch):
    """story-090's contract, driven: the empty string is the item having been
    lost. Every brief is lost, and the run completes with nothing filed."""
    monkeypatch.setattr(outbox, "enqueue", lambda *args, **keywords: "")
    target, _journal, code, inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="lost", findings=[finding()])

    assert code == 0
    assert state_of(target)["status"] == "completed"
    assert inspector.invocations
    assert queue_entries(target) == []
    assert any(inspection.LOST_BY_THE_QUEUE in line
               for line in messages(target)), messages(target)


def test_the_entry_point_offers_no_way_to_stop_a_run(tmp_path, harness,
                                                     monkeypatch):
    """Its whole signature and its return, in the terms the queue sweep's own
    equivalent assertion uses.

    A parameter added later that a call site could set to make the inspection
    refuse is reported rather than absorbed, and the value it answers with is
    None on a path that did everything it could do.
    """
    parameters = inspect_module.signature(
        story_inspection.inspect_after_story).parameters
    assert list(parameters) == [
        "run_dir", "target_root", "config", "harness_root", "story_id",
        "stages", "runner"]

    target, journal, _code, _inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="total", findings=[finding()])
    config = harness_config.load_config(target)
    answered = story_inspection.inspect_after_story(
        run_dir_of(target), target, config, harness, STORY_ID,
        WORKFLOW["stages"],
        runner=Inspector(target, config, journal, findings=[finding(2)]))
    assert answered is None


def inspection_calls_in(source: str) -> list[ast.AST]:
    """Every call to the entry point in a source, as the node enclosing it.

    The enclosing statement rather than the call, because what the rule is
    about is not that the inspection is called but what is done with what it
    answers: a bare expression statement discards it, and anything else is a
    call site that could act on it.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.Call):
                continue
            target = child.func
            if (isinstance(target, ast.Attribute)
                    and target.attr ==
                    story_inspection.inspect_after_story.__name__
                    and isinstance(target.value, ast.Name)
                    and target.value.id == story_inspection.__name__):
                found.append(node)
    return found


COORDINATOR_SOURCE = (
    ORCHESTRATION / "story_coordinator.py").read_text(encoding="utf-8")


def test_the_coordinator_calls_the_inspection_once_and_reads_nothing_from_it():
    """One call site, and it cannot turn an inspection into a decision."""
    calls = inspection_calls_in(COORDINATOR_SOURCE)
    assert len(calls) == 1
    assert [node for node in calls if not isinstance(node, ast.Expr)] == []


@pytest.mark.parametrize("planted", [
    "    outcome = story_inspection.inspect_after_story(run_dir)\n",
    "    if story_inspection.inspect_after_story(run_dir).blocked:\n"
    "        return 1\n",
    "    return story_inspection.inspect_after_story(run_dir)\n",
])
def test_the_scan_reports_a_call_site_that_reads_what_the_inspection_answered(
        planted):
    """Control: the empty list above is a fact about the coordinator's one call
    site rather than about a scan that has stopped seeing a use."""
    source = f"def a_function(run_dir):\n{planted}"
    found = inspection_calls_in(source)
    assert len(found) == 1
    assert [node for node in found if not isinstance(node, ast.Expr)] != []


# ==========================================================================
# The ordering, observed rather than inferred
# ==========================================================================


def test_the_inspection_runs_after_the_completion_commit_and_before_the_sweep(
        tmp_path, harness, monkeypatch):
    """Observed from the order the calls were made.

    The fake inspector records the subject of the commit HEAD stood on when it
    was invoked, and the target's sync command records every entry it was asked
    to file. So the journal says the inspection saw a HEAD the completion
    commit had already moved, and that the brief it enqueued was filed
    afterwards — by this run's own completion sweep rather than by the next
    run.
    """
    target, journal, code, inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="ordered", findings=[finding()])
    assert code == 0

    lines = journal_lines(journal)
    inspected = [index for index, line in enumerate(lines)
                 if line.startswith("inspected at ")]
    filed = [index for index, line in enumerate(lines)
             if line.startswith("filed ")]
    assert len(inspected) == 1, lines
    assert filed, lines
    assert inspected[0] < filed[0], lines

    # The HEAD the inspection saw was the completion commit and not the tree
    # the run started from, so it ran below the commit rather than above it.
    seen = lines[inspected[0]].split("inspected at ", 1)[1]
    assert seen != SETUP_SUBJECT, lines
    assert seen.startswith(f"{STORY_ID}: "), lines

    # And what the sweep was asked to file is the brief the inspection
    # enqueued, so the sweep really did run after it rather than merely later
    # in the file.
    assert len(inspector.invocations) == 1
    keys = [line.split("filed ", 1)[1] for line in lines
            if line.startswith("filed ")]
    entries = queue_entries(target)
    assert keys == [entry["key"] for entry in entries]
    assert [entry["attempts"] for entry in entries] == [1]


def test_the_sweep_is_still_the_last_thing_the_completion_does(
        tmp_path, harness, monkeypatch):
    """story-092's criterion, restated over a run this story's mechanism sits
    inside: the completion sweep still runs after the completion commit, and
    the inspection inserted between them moved neither."""
    target, journal, code, _inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="swept", findings=[finding()])
    assert code == 0
    lines = journal_lines(journal)
    assert lines[-1].startswith("filed "), lines
    assert head_subject(target) != SETUP_SUBJECT


# ==========================================================================
# The runs that inspect nothing
# ==========================================================================


#: The three ways a run stops short of completing, each with the runner that
#: produces it and the status the run records.
STOPPED = {
    "escalated": (None, "escalated"),
    "paused in place": (CapacityStop(signal=A_CAPACITY_SIGNAL), "paused"),
    "stopped rather than wait": (
        CapacityStop(signal=A_CAPACITY_SIGNAL, reset_at=2e10), "paused"),
}


@pytest.mark.parametrize("name", sorted(STOPPED))
def test_a_run_that_does_not_complete_inspects_nothing(
        name, tmp_path, harness, no_model):
    """No invocation, no inspection line, no record and no record commit.

    The guard installed for every test in this module is what makes "no
    invocation" observable: a run that inspected would have reached it and
    failed here.
    """
    capacity, status = STOPPED[name]
    journal = tmp_path / f"{name}-journal.txt"
    target = build_target(tmp_path / name.replace(" ", "-"), journal,
                          **{story_inspection.MAX_FILES_KEY: ROOMY_CAP})
    runner = Runner(target, journal, fails_at=WRITING, capacity=capacity)

    code = run(target, harness, runner)

    assert code != 0
    assert state_of(target)["status"] == status
    assert no_model.calls == 0
    assert history_records(target) == []
    assert [line for line in journal_lines(journal)
            if line.startswith("inspected at ")] == []
    assert not any(story_inspection.COMMIT_SUBJECT.format(story_id=STORY_ID)
                   == subject for subject in subjects(target))


def test_the_completing_run_is_the_control_for_all_three(
        tmp_path, harness, monkeypatch):
    """The control for the absences above: the same fixture, the same cap and
    the same reading, in a run that completes — where the invocation is made,
    the record is written and the commit is there."""
    target, journal, code, inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="control", findings=[finding()])

    assert code == 0
    assert len(inspector.invocations) == 1
    assert len(history_records(target)) == 1
    assert [line for line in journal_lines(journal)
            if line.startswith("inspected at ")] != []
    assert head_subject(target) == \
        story_inspection.COMMIT_SUBJECT.format(story_id=STORY_ID)


# ==========================================================================
# The unconfigured run is the run it was
# ==========================================================================


def test_a_run_with_the_key_unset_inspects_nothing_and_says_nothing(
        tmp_path, harness, no_model):
    """Unset is off: no invocation, no event, no record, no commit."""
    journal = tmp_path / "unset-journal.txt"
    target = build_target(tmp_path / "unset", journal)

    assert run(target, harness, Runner(target, journal)) == 0
    assert no_model.calls == 0
    assert history_records(target) == []
    assert [line for line in messages(target)
            if line.startswith("post-story inspection")] == [], messages(target)
    assert head_subject(target).startswith(f"{STORY_ID}: ")


def test_the_key_adds_the_inspection_line_to_events_log_and_nothing_else(
        tmp_path, harness, monkeypatch, no_model):
    """"Byte-for-byte what the same run leaves today", as a comparison between
    two runs of one fixture differing in that key alone.

    The unconfigured run's lines must be exactly the configured run's with the
    inspection's own line removed — so the key adds that line and moves
    nothing else in the stream.
    """
    journal = tmp_path / "unset-journal.txt"
    unset = build_target(tmp_path / "unset-half", journal)
    assert run(unset, harness, Runner(unset, journal)) == 0
    assert no_model.calls == 0

    configured, _journal, code, inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="set-half", findings=[finding()])
    assert code == 0
    assert inspector.invocations

    said = messages(configured)
    inspected = [line for line in said if line.startswith(
        f"post-story inspection of {STORY_ID}")]
    assert len(inspected) == 1, said
    assert [line for line in said if line not in inspected] == messages(unset)


# ==========================================================================
# The expansion, computed with no model
# ==========================================================================


def changed_set(tmp_path: Path, records: dict) -> set[str]:
    """What the stages' changed-files records name, through the coordinator's
    one derivation of it.

    `records` is keyed by stage name, so this module writes no artifact name of
    its own: each stage's record is the one the workflow declares for it.
    """
    run_dir = tmp_path / "recorded"
    run_dir.mkdir(parents=True, exist_ok=True)
    for stage in WORKFLOW["stages"]:
        name = stage.get("changed_files")
        if name and stage["name"] in records:
            _write(run_dir / name, records[stage["name"]])
    return story_coordinator.recorded_by_all_stages(run_dir, WORKFLOW["stages"])


def expanded(tmp_path: Path, harness: Path, records: dict,
             name: str = "expanded"):
    """The expansion over a known collection of records, with no model.

    Nothing here invokes anything: the whole computation is `git ls-files` and
    two set operations, which is what makes its cost known before an invocation
    is made.
    """
    journal = tmp_path / f"{name}-journal.txt"
    target = build_target(tmp_path / name, journal)
    config = harness_config.load_config(target)
    (target / DELETED_FILE).unlink()
    _git(target, "add", "-A")
    _git(target, "commit", "-q", "-m", "the story removed a file")
    return story_inspection.expansion(
        target, config, harness, changed_set(tmp_path, records))


RECORDS_NAMING_EVERY_CASE = {
    WRITING: {"modified": [CHANGED_SOURCE], "created": [],
              "deleted": [DELETED_FILE]},
    VALIDATING: {"modified": [CHANGED_TEST, OUT_OF_SCOPE_FILE],
                 "created": [BLOCKED_FILE], "deleted": []},
}


def test_the_expansion_is_the_changed_files_and_the_files_beside_them(
        tmp_path, harness):
    """The whole expansion for one known collection of records.

    Changed files kept apart from the files beside them, because the cap trims
    the second before the first, and both asserted exactly rather than by
    membership: a scope that grew a file nobody asked for is as wrong as one
    that lost a file.
    """
    found = expanded(tmp_path, harness, RECORDS_NAMING_EVERY_CASE)

    assert found.changed == (CHANGED_SOURCE, CHANGED_TEST)
    assert found.siblings == (SIBLING_SOURCE, SIBLING_TEST)
    assert found.paths == (CHANGED_SOURCE, CHANGED_TEST,
                           SIBLING_SOURCE, SIBLING_TEST)


def test_the_expansion_takes_no_runner_and_the_scope_is_known_before_one(
        tmp_path, harness):
    """No model is involved: the function has no parameter one could arrive
    through, and the guard installed for this module would have failed the test
    if the expansion had reached for the real one anyway."""
    parameters = inspect_module.signature(story_inspection.expansion).parameters
    assert "runner" not in parameters
    assert list(parameters) == ["target_root", "config", "harness_root",
                                "changed"]


def test_a_changed_file_outside_both_scope_keys_is_left_out_and_named(
        tmp_path, harness):
    """And its containing directory is not pulled in either: what the harness
    will not inspect it does not inspect the neighbours of."""
    found = expanded(tmp_path, harness, RECORDS_NAMING_EVERY_CASE)

    assert OUT_OF_SCOPE_FILE not in found.paths
    assert not any(path.startswith("docs/") for path in found.paths)
    assert any(OUT_OF_SCOPE_FILE in one for one in found.excluded), found.excluded
    # The control for both absences: a file in the same shape that *is* under a
    # scope key is in scope, so the exclusion is the prefix deciding.
    assert CHANGED_TEST in found.paths


def test_a_changed_file_under_a_blocked_prefix_is_left_out_with_its_directory(
        tmp_path, harness):
    """The rules' blocked prefixes, read off the rule set this run executes
    under rather than off the ones this repository deploys."""
    found = expanded(tmp_path, harness, RECORDS_NAMING_EVERY_CASE)

    assert BLOCKED_FILE not in found.paths
    assert BLOCKED_SIBLING not in found.paths
    assert any(BLOCKED_FILE in one for one in found.excluded), found.excluded
    assert VAULT in " ".join(FIXTURE_RULES["blocked_paths"])


def test_a_deleted_file_contributes_its_directory_and_not_itself(
        tmp_path, harness):
    """What sits beside a removal is exactly what a removal can have broken, so
    the directory is in scope and the path is not."""
    found = expanded(tmp_path, harness, {
        WRITING: {"modified": [], "created": [], "deleted": [DELETED_FILE]}})

    assert DELETED_FILE not in found.paths
    assert SIBLING_SOURCE in found.paths
    assert CHANGED_SOURCE in found.paths
    assert any(DELETED_FILE in one for one in found.excluded), found.excluded


def test_only_the_files_git_tracks_directly_beside_a_change_are_in_scope(
        tmp_path, harness):
    """One level and not recursively.

    The subdirectory beneath the changed file's directory is populated and
    tracked, and its file is not in scope; the file *beside* the change is, so
    this is the level deciding rather than the listing finding nothing.
    """
    found = expanded(tmp_path, harness, {
        WRITING: {"modified": [CHANGED_SOURCE], "created": [], "deleted": []}})

    assert SUBDIRECTORY_FILE not in found.paths
    assert SIBLING_SOURCE in found.paths
    assert SUBDIRECTORY_FILE in TRACKED


def test_an_untracked_path_the_records_name_contributes_nothing_but_its_directory(
        tmp_path, harness):
    """The same derivation seen from the other side: the paths come from what
    git tracks rather than from the record, so a record naming a file that is
    not there cannot put it in a scope."""
    found = expanded(tmp_path, harness, {
        WRITING: {"modified": [f"{SOURCE_DIR}never-existed.py"],
                  "created": [], "deleted": []}})

    assert f"{SOURCE_DIR}never-existed.py" not in found.paths
    assert SIBLING_SOURCE in found.paths


# ==========================================================================
# The cap
# ==========================================================================


def test_the_cap_trims_the_files_beside_the_change_before_the_change(
        tmp_path, harness):
    """Ordered so the cap bites on the neighbours first.

    What it left out comes back beside what it kept rather than being dropped
    in silence; that it reaches the run's own record is asserted below, against
    a run.
    """
    found = expanded(tmp_path, harness, RECORDS_NAMING_EVERY_CASE)
    kept, trimmed = story_inspection.cap_paths(found, 3)

    assert kept == (CHANGED_SOURCE, CHANGED_TEST, SIBLING_SOURCE)
    assert trimmed == (SIBLING_TEST,)
    assert set(found.changed) <= set(kept)


def test_the_cap_trims_changed_files_too_where_they_alone_exceed_it(
        tmp_path, harness):
    """A cap that silently kept the whole change would be a cap on nothing in
    the case that most needs bounding."""
    found = expanded(tmp_path, harness, RECORDS_NAMING_EVERY_CASE)
    kept, trimmed = story_inspection.cap_paths(found, 1)

    assert kept == (CHANGED_SOURCE,)
    assert CHANGED_TEST in trimmed
    assert len(kept) + len(trimmed) == len(found.paths)


def test_an_uncapped_expansion_is_the_control_for_both(tmp_path, harness):
    """The same expansion under a cap larger than it keeps everything and trims
    nothing, so what the two assertions above report is the cap rather than an
    expansion that was short to begin with."""
    found = expanded(tmp_path, harness, RECORDS_NAMING_EVERY_CASE)
    kept, trimmed = story_inspection.cap_paths(found, ROOMY_CAP)

    assert kept == found.paths
    assert trimmed == ()


#: The two labels the inspection's line introduces its dropped paths with, and
#: the cap this run inspects under. A path named under one label may not
#: satisfy an assertion about the other, which is what `segment` is for.
TRIMMED_LABEL = "trimmed to the file cap: "
LEFT_OUT_LABEL = "left out of scope: "
CAP_THE_RUN_EXCEEDS = 3


def inspection_line(target: Path) -> str:
    """The one line this run's events.log carries about its inspection."""
    said = [line for line in messages(target)
            if line.startswith(f"post-story inspection of {STORY_ID}")]
    assert len(said) == 1, messages(target)
    return said[0]


def segment(line: str, label: str) -> str:
    """The part of an inspection line one label introduces.

    It runs to the *next label* rather than to the next `; `, because the
    entries under `left out of scope` carry their own reasons and are joined
    with `; ` themselves. Reading it this way is what keeps an assertion about
    the trimmed paths from being satisfied by a path named as out of scope.
    """
    assert label in line, (label, line)
    after = line.split(label, 1)[1]
    for other in (TRIMMED_LABEL, LEFT_OUT_LABEL):
        if other != label:
            after = after.split("; " + other, 1)[0]
    return after


def test_what_the_cap_excluded_reaches_the_runs_events_log(
        tmp_path, harness, monkeypatch):
    """Every trimmed path named in the record, and the invocation handed
    exactly the cap.

    Named rather than counted: a count tells a reader the scope was smaller
    than the expansion and leaves them no way to find out which file the
    inspection did not read. The control for the naming is the other side of
    the same run — no path the invocation *was* handed appears in the trimmed
    segment, so the segment is reporting the cap rather than listing the scope.
    """
    target, _journal, code, inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="capped", findings=[finding()],
        **{story_inspection.MAX_FILES_KEY: CAP_THE_RUN_EXCEEDS})

    assert code == 0
    listed = rendered_paths(inspector.prompt)
    assert len(listed) == CAP_THE_RUN_EXCEEDS, listed

    trimmed = segment(inspection_line(target), TRIMMED_LABEL)
    assert DELETED_FILE in trimmed, trimmed
    assert SIBLING_TEST in trimmed, trimmed
    for path in listed:
        assert path not in trimmed, (path, trimmed)


def test_the_paths_the_expansion_left_out_are_named_in_the_runs_events_log(
        tmp_path, harness, monkeypatch):
    """A run whose writing stage records a path outside both scope keys and one
    the repository does not track names both, with the reason each was left
    out.

    The control is the change the same record names that *is* in scope: it is
    handed to the invocation and is not in the segment, so what the segment
    reports is the exclusion rather than the changed set.
    """
    target, _journal, code, inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="left-out", findings=[finding()],
        extra_changed=(OUT_OF_SCOPE_FILE, UNTRACKED_CHANGE))

    assert code == 0
    left_out = segment(inspection_line(target), LEFT_OUT_LABEL)
    assert OUT_OF_SCOPE_FILE in left_out, left_out
    assert UNTRACKED_CHANGE in left_out, left_out
    assert CHANGED_SOURCE not in left_out, left_out
    assert CHANGED_SOURCE in rendered_paths(inspector.prompt)


def rendered_paths(prompt: str) -> list[str]:
    """The paths the fixture template rendered under its scope-paths label.

    A multi-line value renders as a block, so the span runs to the next label.
    """
    lines = prompt.splitlines()
    where = lines.index("scope_paths:") + 1
    found = []
    while where < len(lines) and lines[where].strip():
        found.append(lines[where].strip())
        where += 1
    return found


# ==========================================================================
# A cap that is not a positive integer refuses nothing
# ==========================================================================


@pytest.mark.parametrize("value", ["lots", "0", "-1", "1.5"])
def test_a_cap_that_is_not_a_positive_integer_disables_and_names_itself(
        value, tmp_path, harness, no_model):
    """It refuses nothing, because a total function has no way to refuse.

    The key and the value are both named in the record, the run completes, and
    the status is the one the same fixture has with the key unset.
    """
    journal = tmp_path / f"bad-{value}-journal.txt"
    target = build_target(tmp_path / f"bad-cap-{value}", journal,
                          **{story_inspection.MAX_FILES_KEY: value})

    code = run(target, harness, Runner(target, journal))

    assert code == unconfigured_exit_status(tmp_path, harness)
    assert state_of(target)["status"] == "completed"
    assert no_model.calls == 0
    named = [line for line in messages(target)
             if story_inspection.MAX_FILES_KEY in line]
    assert len(named) == 1, messages(target)
    assert value in named[0], named
    assert len(history_records(target)) == 1


def test_the_bound_reader_tells_an_absent_key_from_an_unusable_one():
    """The two answers are different, and only one of them is a problem.

    Absent is the mechanism switched off and reports nothing at all; unusable
    is named. Driven at the reader so both are stated once, against values this
    test supplies rather than against a run.
    """
    cap, problem = story_inspection.max_files({})
    assert (cap, problem) == (None, "")
    cap, problem = story_inspection.max_files(
        {story_inspection.MAX_FILES_KEY: "3"})
    assert (cap, problem) == (3, "")
    cap, problem = story_inspection.max_files(
        {story_inspection.MAX_FILES_KEY: "not-a-count"})
    assert cap is None
    assert story_inspection.MAX_FILES_KEY in problem
    assert "not-a-count" in problem


# ==========================================================================
# One invocation, and what it is told it is looking at
# ==========================================================================


def test_one_invocation_is_made_whatever_mix_of_files_the_run_changed(
        tmp_path, harness, monkeypatch):
    """Two writing stages record between them a source file and a tests file,
    and the change is one subject: one invocation, carrying both."""
    _target, _journal, code, inspector, runner = completing_run(
        tmp_path, harness, monkeypatch, name="one-call", findings=[finding()])

    assert code == 0
    assert runner.calls == [WRITING, VALIDATING, VERIFYING]
    assert len(inspector.invocations) == 1
    listed = rendered_paths(inspector.prompt)
    assert CHANGED_SOURCE in listed
    assert CHANGED_TEST in listed
    assert SUBDIRECTORY_FILE not in listed


def test_the_invocation_is_told_it_is_looking_at_a_change(
        tmp_path, harness, monkeypatch):
    """Not at the source half or the tests half: a third kind, and a scope
    labelled by where it came from rather than by a part of the tree."""
    _target, _journal, _code, inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="framed", findings=[finding()])

    assert rendered_field(inspector.prompt, "scope_kind") == inspection.CHANGE
    assert inspection.CHANGE not in (inspection.SOURCE, inspection.TESTS)
    assert rendered_field(inspector.prompt, "scope") == \
        story_inspection.ORIGIN.format(story_id=STORY_ID)
    assert STORY_ID in inspector.invocations[0]["stage"]


def test_the_post_story_prompt_asks_whether_the_change_left_a_defect(
        tmp_path, harness, monkeypatch):
    """The framing is this caller's question, rendered where the template asks
    it, and it is not the question broad mode asks."""
    _target, _journal, _code, inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="asked", findings=[finding()])

    framing = rendered_field(inspector.prompt, "framing")
    assert framing == story_inspection.POST_STORY_FRAMING
    assert "defect" in framing
    assert framing != inspection.BROAD_FRAMING


def test_a_broad_mode_invocation_asks_its_own_question_and_neither_is_none(
        tmp_path, harness, monkeypatch):
    """The other half, and the control for both.

    The same template, rendered for a broad-mode inspection of the same
    repository, carries broad mode's framing — so "the post-story framing was
    rendered" is a fact about the value this caller supplies rather than about
    a template that renders one thing whatever it is given. Neither renders as
    the literal None, and the extraction is shown able to report one.
    """
    journal = tmp_path / "broad-journal.txt"
    target = build_target(tmp_path / "broad", journal)
    config = harness_config.load_config(target)
    broad = Inspector(target, config, journal, findings=[])
    inspection.inspect(target, config, harness, runner=broad)

    assert broad.invocations, "broad mode made no invocation"
    for one in broad.invocations:
        assert rendered_field(one["prompt"], "framing") == \
            inspection.BROAD_FRAMING

    # The control for "renders as None": the same extraction over a rendering
    # whose value really is the literal must report it.
    planted = "\n".join(["framing:", "None", ""])
    assert rendered_field(planted, "framing") == "None"
    assert rendered_field(broad.invocations[0]["prompt"], "framing") != "None"


def test_the_invocation_runs_under_the_existing_cost_ceiling(
        tmp_path, harness, monkeypatch):
    """No second ceiling: what this invocation may spend is the allowance the
    existing key already declares."""
    allowance = "0.37"
    _target, _journal, _code, inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="bounded", findings=[finding()],
        **{inspection.MAX_COST_KEY: allowance})

    assert inspector.invocations[0]["max_budget_usd"] == float(allowance)


def test_it_files_under_the_existing_brief_cap(tmp_path, harness, monkeypatch):
    """And no second brief cap: of two findings under a brief cap of one, one
    is filed and what the cap excluded is named."""
    target, _journal, code, _inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="brief-capped",
        findings=[finding(1), finding(2)],
        **{inspection.MAX_FINDINGS_KEY: "1"})

    assert code == 0
    assert len(queue_entries(target)) == 1
    assert any(inspection.PAST_THE_CAP in line for line in messages(target))


def test_no_second_ceiling_and_no_second_brief_cap_are_declared():
    """As a fact of the declaration rather than as prose: the inspection keys
    the schema declares are the two that already bounded an inspection, plus
    the file cap this story adds."""
    declared = {key for key in harness_config.declared_config_keys()
                if key.startswith("inspect")}
    assert declared == {inspection.MAX_COST_KEY, inspection.MAX_FINDINGS_KEY,
                        story_inspection.MAX_FILES_KEY}


# ==========================================================================
# One key for one finding, whichever producer found it
# ==========================================================================


def test_a_brief_this_mode_files_lands_on_the_key_an_l5_inspect_run_derives(
        tmp_path, harness, monkeypatch):
    """The scope a brief carries does not divide the dedupe.

    The key the entry was filed under is compared against the one the shared
    identity derives for the same finding — the derivation broad mode files
    under — so a finding this mode files and the same finding rediscovered by
    an inspection of the whole tree collapse onto one entry.
    """
    found = finding()
    target, _journal, code, _inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="keyed", findings=[found])
    assert code == 0

    entries = queue_entries(target)
    assert len(entries) == 1
    assert entries[0]["key"] == outbox.identity_key(story_brief.identity(found))
    # The provenance rides as payload and never as identity, so it is on the
    # entry and absent from what the key was derived from.
    assert entries[0]["payload"]["scope"] == \
        story_inspection.ORIGIN.format(story_id=STORY_ID)
    assert "scope" not in story_brief.identity(found)


def test_the_same_finding_under_a_different_scope_derives_the_same_key():
    """The control, constructed rather than argued: two scopes, one identity.

    Without it the assertion above passes just as happily against a key
    derivation that never sees a scope in the first place — which is what it
    claims, and so is what has to be shown by feeding it two.
    """
    found = finding()
    change = inspection.Scope(
        path="", kind=inspection.CHANGE, paths=(CHANGED_SOURCE,),
        origin=story_inspection.ORIGIN.format(story_id=STORY_ID),
        framing=story_inspection.POST_STORY_FRAMING)
    broad = inspection.Scope(path=SOURCE_DIR, kind=inspection.SOURCE)

    from_the_change = inspection.payload(found, change)
    from_the_tree = inspection.payload(found, broad)

    # The two really are filed carrying different provenance...
    assert from_the_change["scope"] != from_the_tree["scope"]
    # ...and the key each is filed under is the same one, so the queue holds
    # one entry however many producers found it.
    assert outbox.identity_key(inspection.identity(from_the_change)) == \
        outbox.identity_key(inspection.identity(from_the_tree))
    assert from_the_change["paths"] == from_the_tree["paths"]


# ==========================================================================
# What the inspection did reaches the run's events.log
# ==========================================================================


def test_the_counts_and_every_way_a_finding_was_dropped_reach_events_log(
        tmp_path, harness, monkeypatch):
    """How many were found, how many filed, and how many went each way.

    Three findings, one of them malformed and one of them past the brief cap,
    so more than one drop reason is in play at once and each is named with how
    many went that way.
    """
    malformed = finding(3)
    malformed.pop("severity")
    target, _journal, code, _inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="counted",
        findings=[finding(1), finding(2), malformed],
        **{inspection.MAX_FINDINGS_KEY: "1"})

    assert code == 0
    said = [line for line in messages(target)
            if line.startswith(f"post-story inspection of {STORY_ID}")]
    assert len(said) == 1, messages(target)
    line = said[0]
    assert "3 finding(s)" in line, line
    assert "1 filed" in line, line
    assert inspection.PAST_THE_CAP in line, line
    assert inspection.MALFORMED in line, line


def test_the_counts_are_on_the_runs_structured_history_too(
        tmp_path, harness, monkeypatch):
    """The three flat fields, in the idiom the verifier outcome already uses:
    present on an inspection's entry even where a count is zero."""
    target, _journal, _code, _inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="structured",
        findings=[finding()])

    entries = [entry for entry in
               story_coordinator.load_history(run_dir_of(target))
               if entry["event"] == story_inspection.INSPECTION_EVENT]
    assert len(entries) == 1
    assert entries[0]["findings"] == 1
    assert entries[0]["filed"] == 1
    assert entries[0]["dropped"] == 0


# ==========================================================================
# The record, and the commit that makes it durable
# ==========================================================================


def test_a_completed_inspection_records_the_story_the_time_and_the_counts(
        tmp_path, harness, monkeypatch):
    """One line in the declared log, carrying what the declaration declares."""
    target, _journal, code, _inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="recorded", findings=[finding()])
    assert code == 0

    written = history_records(target)
    assert len(written) == 1
    record = written[0]
    assert record["story_id"] == STORY_ID
    assert record["timestamp"]
    assert (record["findings"], record["filed"], record["dropped"]) == (1, 1, 0)


def test_the_record_is_inside_a_commit_rather_than_left_in_the_working_tree(
        tmp_path, harness, monkeypatch):
    """The run directory reaches no clone, so a record that stayed there would
    be no evidence at all."""
    target, _journal, _code, _inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="committed", findings=[finding()])

    paths = list(story_inspection.record_paths(target, {}))
    assert paths, "the declaration routes this kind to no log"
    assert head_subject(target) == \
        story_inspection.COMMIT_SUBJECT.format(story_id=STORY_ID)
    assert committed_paths(target) == paths
    assert story_coordinator.dirty_paths(target) == []


def edits_a_file_elsewhere(target: Path) -> None:
    """What an inspection agent that ignored its instructions would leave: an
    edit somewhere in the repository that is nothing to do with the record."""
    (target / SIBLING_SOURCE).write_text("def b():\n    return 22\n",
                                         encoding="utf-8")


def test_the_record_commit_stages_the_record_paths_and_nothing_else(
        tmp_path, harness, monkeypatch):
    """Constructed rather than assumed: the fake inspector really does change a
    file elsewhere in the repository, and that file must be left in the working
    tree rather than folded into a commit this story made."""
    target, _journal, code, _inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="staged", findings=[finding()],
        act=edits_a_file_elsewhere)

    assert code == 0
    assert committed_paths(target) == list(
        story_inspection.record_paths(target, {}))
    assert SIBLING_SOURCE in story_coordinator.dirty_paths(target)


def test_the_record_commit_is_skipped_where_it_would_stage_nothing(
        tmp_path, harness, monkeypatch):
    """A target that tracks no record path gains no commit.

    The target's own ignore rules keep the history directory out of version
    control, so staging the record leaves the index empty — and an empty commit
    about a record nothing tracks is a commit about nothing.
    """
    target, _journal, code, inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="untracked-record",
        findings=[finding()], ignore_history=True)

    assert code == 0
    assert inspector.invocations, "the inspection did not run"
    # The record was written — so this is the commit being skipped rather than
    # the inspection not having happened — and no commit followed it.
    assert len(history_records(target)) == 1
    assert head_subject(target).startswith(f"{STORY_ID}: ")
    assert story_inspection.COMMIT_SUBJECT.format(story_id=STORY_ID) not in \
        subjects(target)


def test_nothing_reads_the_record_commit_as_a_completion_or_an_escalation(
        tmp_path, harness, monkeypatch):
    """The subject carries no completion marker and wears neither the
    escalation nor the pause shape, so the readers that decide a run on one of
    those are unaffected by it."""
    target, _journal, _code, _inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="unrecognised",
        findings=[finding()])
    branch = state_of(target)["branch"]

    matched = story_coordinator.completion_commits(target, branch, STORY_ID)
    assert len(matched) == 1
    assert story_inspection.COMMIT_SUBJECT.format(story_id=STORY_ID) not in \
        " ".join(matched)
    # The control: the commit the matcher *does* match is on this branch, so an
    # unmatched record commit is the shape deciding rather than a reader that
    # matches nothing.
    assert f"{STORY_ID}: " in matched[0], matched
    assert story_coordinator._head_escalated(target) is None
    assert story_coordinator.paused_story(head_subject(target)) is None
    assert story_coordinator.COMPLETION_COMMIT_MARKER not in \
        _git(target, "log", "-1", "--format=%B").stdout


def test_the_tree_a_run_left_clean_is_still_clean_after_the_inspection(
        tmp_path, harness, monkeypatch):
    """So the next run's clean-tree pre-flight is not refused by anything this
    story added: the pre-flight's whole evidence is `dirty_paths`, and after
    the inspection and its commit that reader reports nothing."""
    target, _journal, code, _inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="clean", findings=[finding()])

    assert code == 0
    assert story_coordinator.dirty_paths(target) == []
    # The control for that emptiness: a file no stage produced is reported by
    # the same reader, so the clean tree is the reader looking rather than a
    # reader that has stopped seeing.
    (target / SIBLING_SOURCE).write_text("left behind\n", encoding="utf-8")
    assert story_coordinator.dirty_paths(target) == [SIBLING_SOURCE]


# ==========================================================================
# The deployment: this repository turns it on for itself
# ==========================================================================


def test_this_repository_declares_the_key_and_the_template_carries_it():
    """A deployment fact about shipped files, read out of the files.

    This one legitimately reads what the harness ships: the claim *is* about
    what this repository configures and what the template offers a new target.
    """
    configured = harness_config.load_config(REPO_ROOT)
    assert story_inspection.MAX_FILES_KEY in configured
    assert int(str(configured[story_inspection.MAX_FILES_KEY])) > 0

    template = (REPO_ROOT / "templates" / "config.yaml").read_text(
        encoding="utf-8")
    commented = [line for line in template.splitlines()
                 if line.startswith(f"# {story_inspection.MAX_FILES_KEY}:")]
    assert len(commented) == 1, commented
