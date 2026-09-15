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

Since story-147 this is one of two positions the inspection can run in, and
this module is the one that stays. A workflow whose stages declare an
inspection is inspected *before* the declaring stage, so that a finding about
the change reaches the stage that can still answer it; a workflow declaring
none is inspected from `_complete` exactly as it always was, with the same
scope, the same filing and the same record. The workflow this module builds
declares none — asserted below rather than assumed — so everything here is the
second case, and the first is
`tests/test_inspection_findings_reach_the_story.py`'s subject.

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

#: The kind the coordinator appends before the inspection is invoked. An event
#: kind is the coordinator's own vocabulary rather than a name a workflow
#: declares, so it is spelled here as the modules reading the other
#: announcements spell theirs. What the announcement *says* is
#: `tests/test_a_run_announces_what_it_is_waiting_on.py`'s subject; this module
#: needs only to tell its line from the rest of the stream.
ANNOUNCEMENT = "inspection-started"


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

#: The last stage this definition declares, derived rather than spelled: the
#: seam between a run that has done its work and the inspection that follows it
#: is wherever the definition's stages end, and a module that named one of them
#: would stop finding the seam the moment a stage was added below it.
LAST_STAGE = WORKFLOW["stages"][-1]["name"]

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

#: A path under a scope key that the tree does not hold, for a run whose
#: writing stage records a file that is not there to read. It is deliberately
#: not in `TRACKED`: the expansion takes its paths from the listing of what the
#: tree holds rather than from the record, which is how a deleted file and a
#: never-existed one come out the same way.
MISSING_CHANGE = f"{SOURCE_DIR}removed.py"

#: A file a stage writes into the tree and nothing stages, which is the
#: condition every file a story creates is in while the stage loop is still
#: running: on disk, under a scope key, and in no index. It is not in `TRACKED`
#: and the fixture never writes it — the cases that want it write it themselves,
#: so every other expansion here is computed over a tree without it.
CREATED_FILE = f"{SOURCE_DIR}created.py"

#: A path under the same scope key that the fixture's own .gitignore matches, so
#: "the listing admits what the tree holds" can be told from "the listing admits
#: everything". Written by the cases that want it, on the same terms as above.
IGNORED_FILE = f"{SOURCE_DIR}ignored.py"

#: A path under a scope key that has never existed in this fixture in any form.
NEVER_EXISTED = f"{SOURCE_DIR}never-existed.py"

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

    ignored = [".harness/runs/", ".harness/logs/", "/".join(outbox.QUEUE_DIR),
               IGNORED_FILE]
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


SEVERITY_ENUM = schema_validator.load_schema(
    inspection.BRIEF_SCHEMA)["properties"]["severity"]["enum"]


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
        self.config = config
        # The tree this stand-in reads and writes: the working directory the
        # inspection hands it, which since story-117 is the worktree the run
        # works in rather than the checkout the run was invoked from. Taken from
        # that hand-off at invocation rather than resolved here, because it is
        # what a real inspection agent would be given and what the findings
        # artifact is read back out of. Until the first invocation there is
        # nothing to have been handed, so it starts as the root it was built
        # for — which is what an invocation driven directly, outside a run, then
        # confirms it to be.
        self.tree = self.target
        self.journal = Path(journal)
        self.findings = list(findings)
        self.act = act
        self.raises = raises
        self.invocations: list[dict] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode,
                 model, allowed_tools=None, max_budget_usd=None,
                 suite_command=None, run_dir=None):
        self.invocations.append({
            "prompt": prompt, "stage": stage, "cwd": Path(cwd),
            "permission_mode": permission_mode, "model": model,
            "allowed_tools": allowed_tools, "max_budget_usd": max_budget_usd,
        })
        self.tree = Path(cwd)
        with self.journal.open("a", encoding="utf-8") as handle:
            handle.write(f"inspected at {head_subject(self.tree)}\n")
        if self.act is not None:
            self.act(self.tree)
        if self.raises:
            raise RuntimeError(self.raises)
        self.artifact.parent.mkdir(parents=True, exist_ok=True)
        self.artifact.write_text(json.dumps({"findings": self.findings}),
                                 encoding="utf-8")
        return AgentResult(ok=True, result_text="inspected")

    @property
    def artifact(self) -> Path:
        """Where the findings go, in whichever tree this was last handed."""
        return inspection.findings_paths(self.tree, self.config)[0]

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

    `at_last_stage` is called with the tree the run works in as that tree's
    final stage is invoked, which is the one seam between a run that has done
    its work and the inspection that follows it. It exists so a test can break
    something the inspection will reach without breaking the run that reaches
    it: anything done to the tree before the run starts would be done to a
    checkout the run never works in, and anything done after the run returns
    would be done after the inspection had already finished.
    """

    def __init__(self, target_root: Path, journal: Path, *,
                 fails_at: str | None = None, capacity=None,
                 extra_changed=(), at_last_stage=None):
        self.target_root = Path(target_root)
        self.run_dir = conftest.run_dir_for(self.target_root, STORY_ID)
        self.journal = Path(journal)
        self.fails_at = fails_at
        self.capacity = capacity
        self.extra_changed = tuple(extra_changed)
        self.at_last_stage = at_last_stage
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
        # The source edits go in the tree the coordinator handed this stage,
        # which since story-117 is the worktree the run works in rather than
        # the checkout it was invoked from — and it is the tree the run's own
        # completion commit is taken over.
        tree = Path(cwd) if cwd else self.target_root
        if stage == WRITING:
            _write(self.run_dir / conftest.CHANGED_FILES,
                   {"modified": [CHANGED_SOURCE, *self.extra_changed],
                    "created": [], "deleted": []})
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Did the work.\n", encoding="utf-8")
            (tree / CHANGED_SOURCE).write_text(
                "def a():\n    return 11\n", encoding="utf-8")
        elif stage == VALIDATING:
            _write(self.run_dir / conftest.TEST_RESULTS, {"tests_written": 1})
            _write(self.run_dir / conftest.TESTER_CHANGED_FILES,
                   {"modified": [CHANGED_TEST], "created": [], "deleted": []})
            (tree / CHANGED_TEST).write_text(
                "def check_a():\n    assert True  # and again\n",
                encoding="utf-8")
        elif stage == VERIFYING:
            _write(self.run_dir / conftest.VERIFICATION_RESULT, PASSED)
        if self.at_last_stage is not None and stage == LAST_STAGE:
            self.at_last_stage(tree)
        return AgentResult(ok=True, result_text=f"{stage} done")


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run(target: Path, harness: Path, runner: Runner) -> int:
    return story_coordinator.run_story(
        STORY_ID, harness, target, runner, sleep=lambda _seconds: None)


def run_dir_of(target: Path) -> Path:
    return conftest.run_dir_for(target, STORY_ID)


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


def kinds_and_messages(target: Path) -> list[tuple[str, str]]:
    """Each event this run recorded, as its kind beside what it said.

    The two renderings are one write, so the kind is how a line is told from
    another line that reads like it — which is what the announcement needs: it
    carries no prefix the record's own lines carry, and selecting it by wording
    would be this module spelling a line the coordinator composes.
    """
    history = json.loads(
        (run_dir_of(target) / "execution-history.json").read_text(
            encoding="utf-8"))
    return [(entry["event"], entry["message"]) for entry in history]


def detail_log_of(root: Path) -> Path:
    """The run's own log under `root`'s configured logs directory.

    Derived through the same helper the run that creates the file and the
    inspection that appends to it both read it from, so this module cannot
    disagree with either about which file it is reading. It takes the tree
    rather than the checkout, because an inspection appends where the run
    worked.
    """
    root = Path(root)
    return harness_config.run_log_path(
        root, harness_config.load_config(root), STORY_ID)


def detail_under(root: Path, label: str) -> list[str]:
    """Every fact the run's own log carries under one label.

    One fact per line, which is what keeps an assertion about the trimmed paths
    from being satisfied by a path the expansion left out — and what keeps
    either of them from being satisfied by the agent stream the same log
    carries.
    """
    path = detail_log_of(root)
    if not path.is_file():
        return []
    return [line[len(label):]
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith(label)]


def in_the_runs_tree(root: Path) -> Path:
    """`root` resolved to the tree a run of this story works in.

    Since story-117 a run commits in a worktree of its own rather than in the
    checkout it was invoked from, so every question below about what a run
    committed is asked of that tree. Idempotent: handed the worktree itself it
    answers the worktree, because a tree already standing on the story branch
    is its own run root. A root the harness cannot read a configuration out of
    — the bare tree the broad-mode tests below build — is answered as itself,
    since there is no run working anywhere else for it. An invocation that
    drives the inspection directly rather than through a run has no such tree
    standing anywhere, and is likewise answered as itself.
    """
    root = Path(root)
    try:
        resolved = conftest.run_root_for(root, STORY_ID)
    except Exception:
        return root
    return resolved if resolved.is_dir() else root


def head_subject(root: Path) -> str:
    return _git(in_the_runs_tree(root), "log", "-1", "--format=%s").stdout.strip()


def subjects(root: Path) -> list[str]:
    return _git(in_the_runs_tree(root), "log", "--format=%s").stdout.splitlines()


def committed_paths(root: Path, revision: str = "HEAD") -> list[str]:
    listed = _git(in_the_runs_tree(root), "show", "--name-only", "--format=",
                  revision)
    return [line for line in listed.stdout.splitlines() if line.strip()]


def history_records(target: Path) -> list[dict]:
    """The inspection log's records, found by the declaration that routes this
    kind rather than by a filename written here."""
    target = in_the_runs_tree(target)
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
    """Every entry the run's own outbox queue holds.

    Read in the tree the run works in rather than in the checkout it was
    invoked from: since story-117 a run's inspection files into the worktree's
    queue, which is where the sweep that ships it looks.
    """
    queue = outbox.queue_dir(conftest.run_root_for(target, STORY_ID))
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in outbox.entry_files(queue)]


def completing_run(tmp_path: Path, harness: Path, monkeypatch, *,
                   name: str = "inspected", findings=(), act=None,
                   raises: str = "", ignore_history: bool = False,
                   extra_changed=(), at_last_stage=None, **config_keys):
    """One completing run of the fixture, with the inspector installed.

    Returns `(target, journal, code, inspector, runner)`. The cap is supplied
    unless the caller departs from it, so a run built by this helper inspects.
    `extra_changed` reaches the writing stage's changed-files record, so a
    caller can give the run a change the expansion will leave out.

    The severity floor is declared at the lowest severity the scale defines —
    which is no floor at all — unless the caller departs from it. The findings
    this module builds carry that severity, and this module's subject is the
    post-story inspection's mechanics rather than which findings the floor
    files: with the default floor in force every one of them would be dropped
    before it reached the queue, which is the floor's own module's question.
    """
    config_keys.setdefault(story_inspection.MAX_FILES_KEY, ROOMY_CAP)
    config_keys.setdefault(inspection.MIN_SEVERITY_KEY,
                           str(min(SEVERITY_ENUM)))
    journal = tmp_path / f"{name}-journal.txt"
    target = build_target(tmp_path / name, journal,
                          ignore_history=ignore_history, **config_keys)
    config = harness_config.load_config(target)
    inspector = install(monkeypatch, Inspector(
        target, config, journal, findings=findings, act=act, raises=raises))
    runner = Runner(target, journal, extra_changed=extra_changed,
                    at_last_stage=at_last_stage)
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
                          # No floor, for the reason `completing_run` states:
                          # this target is built directly rather than through
                          # that helper, and the subject here is the query.
                          **{story_inspection.MAX_FILES_KEY: ROOMY_CAP,
                             inspection.MIN_SEVERITY_KEY:
                                 str(min(SEVERITY_ENUM))})
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


def test_the_fixture_declares_no_inspection_so_this_module_is_the_second_case():
    """The premise every run below rests on, since story-147 gave the harness a
    second position to inspect in.

    A workflow declaring an inspection is inspected before the declaring stage
    and `_complete` does not inspect at all. This fixture declares none, so
    every run here reaches the post-story position — and an assertion here
    about what a completed run does is a statement about the workflows that
    declare none rather than about a position the coordinator still holds for
    everything. The control is the shipped definition, which does declare one,
    read through the same reader.
    """
    assert story_coordinator.inspection_declaration(WORKFLOW["stages"]) == {}
    assert story_coordinator.inspection_declaration(
        conftest.shipped_workflow(REPO_ROOT, "story-workflow")["stages"])


def test_the_coordinator_calls_the_inspection_once_and_reads_nothing_from_it():
    """One call site, and it cannot turn an inspection into a decision.

    Still one since story-147, which made it conditional rather than moving it:
    the post-story call happens where no stage of the loaded workflow declares
    an inspection of its own, so the two positions cannot both fire and no run
    inspects the same diff twice. A bare expression statement inside an `if` is
    still a bare expression statement, which is what this reading is about.
    """
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
    assert [kind for kind, _ in kinds_and_messages(target)
            if kind == ANNOUNCEMENT] == [], messages(target)
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
    kinds = [kind for kind, _message in kinds_and_messages(configured)]
    assert len(kinds) == len(said), "the two renderings disagree in length"
    # The inspection's own lines: its announcement, told by its kind, and its
    # record, told by the prefix every line of it carries. The announcement
    # carries no such prefix — it is written before there is anything to
    # report — so a reading by prefix alone would call it something the key
    # moved rather than something the key added.
    inspected = [line for kind, line in zip(kinds, said)
                 if kind == ANNOUNCEMENT
                 or line.startswith(f"post-story inspection of {STORY_ID}")]
    # The summary line, and beside it however many lines say a scope's dedupe
    # did not answer — this target configures no filed_query_command, so there
    # is one of those. The subject is that every line the key added is one of
    # the inspection's and that nothing else in the log moved, so the summary
    # is what is counted rather than the block.
    assert len([line for line in inspected if "finding(s)" in line]) == 1, said
    assert kinds.count(ANNOUNCEMENT) == 1, said
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


def expanded_against(tmp_path: Path, harness: Path, records: dict,
                     name: str = "expanded", prepare=None):
    """The target the expansion was computed over, and the expansion.

    Nothing here invokes anything: the whole computation is one `git ls-files`
    and two set operations, which is what makes its cost known before an
    invocation is made.

    `prepare` is called with the target *after* the commit that removes a file,
    so whatever it does to the tree is uncommitted and unstaged — which is the
    condition the stage loop leaves a tree in, and the only one in which a file
    the story created can be told from a file the index already knew about.
    """
    journal = tmp_path / f"{name}-journal.txt"
    target = build_target(tmp_path / name, journal)
    config = harness_config.load_config(target)
    (target / DELETED_FILE).unlink()
    _git(target, "add", "-A")
    _git(target, "commit", "-q", "-m", "the story removed a file")
    if prepare is not None:
        prepare(target)
    return target, story_inspection.expansion(
        target, config, harness, changed_set(tmp_path, records))


def expanded(tmp_path: Path, harness: Path, records: dict,
             name: str = "expanded", prepare=None):
    """The expansion alone, for a case with nothing to ask of the target."""
    return expanded_against(tmp_path, harness, records, name, prepare)[1]


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


def test_only_the_files_held_directly_beside_a_change_are_in_scope(
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


def test_a_path_the_tree_does_not_hold_contributes_nothing_but_its_directory(
        tmp_path, harness):
    """The same derivation seen from the other side: the paths come from the
    listing of what the tree holds rather than from the record, so a record
    naming a path that is not there cannot put it in a scope."""
    found = expanded(tmp_path, harness, {
        WRITING: {"modified": [f"{SOURCE_DIR}never-existed.py"],
                  "created": [], "deleted": []}})

    assert f"{SOURCE_DIR}never-existed.py" not in found.paths
    assert SIBLING_SOURCE in found.paths


# ==========================================================================
# story-148: the scope is what the tree holds, not what the index tracks
#
# The expansion above is computed over a tree whose every file is committed, so
# the index and the tree agree and nothing there can tell which of the two the
# scope came from. Each case below departs from that tree in exactly one way and
# asks the same expansion the same question.
# ==========================================================================


def wrote(root: Path, relative: str, text: str = "def written():\n    pass\n"):
    """One file written into the target tree and staged by nothing.

    The condition a stage leaves a file it created in: on disk, and in no index
    until something commits it. Returns the path so a caller can write two.
    """
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def tracked_paths(target: Path) -> list[str]:
    """What the index holds, which is what the scope used to be taken from."""
    return _git(target, "ls-files").stdout.splitlines()


def excluding(found, path: str) -> list[str]:
    """Every entry in the expansion's exclusions that names one path."""
    return [one for one in found.excluded if one.startswith(f"{path}:")]


def test_a_file_a_stage_created_and_nothing_staged_is_in_the_scope(
        tmp_path, harness):
    """The case this story exists for.

    A file the story wrote during the stage loop is on disk and in no index, and
    the inspection whose subject it is has to read it. The premise is asserted
    rather than assumed: the index really does not hold it, so what puts it in
    the scope is the tree.
    """
    target, found = expanded_against(
        tmp_path, harness,
        {WRITING: {"modified": [], "created": [CREATED_FILE], "deleted": []}},
        name="created-unstaged",
        prepare=lambda root: wrote(root, CREATED_FILE))

    assert CREATED_FILE not in tracked_paths(target), "the premise is gone"
    assert CREATED_FILE in found.changed
    assert CREATED_FILE in found.paths
    assert excluding(found, CREATED_FILE) == [], found.excluded


def test_a_file_git_ignores_is_left_out_beside_the_created_file_that_is_not(
        tmp_path, harness):
    """The control the case above needs.

    Both files are written into the tree the same way and recorded the same
    way, and only the one the fixture's .gitignore matches is left out — so the
    listing admits what the tree holds rather than admitting everything. The
    premise is read off git itself: it reports the one as untracked and says
    nothing about the other.
    """
    def prepare(root: Path) -> None:
        wrote(root, CREATED_FILE)
        wrote(root, IGNORED_FILE)

    target, found = expanded_against(
        tmp_path, harness,
        {WRITING: {"modified": [], "created": [CREATED_FILE, IGNORED_FILE],
                   "deleted": []}},
        name="created-and-ignored", prepare=prepare)

    untracked = _git(target, "status", "--porcelain",
                     "--untracked-files=all").stdout
    assert CREATED_FILE in untracked and IGNORED_FILE not in untracked

    assert CREATED_FILE in found.paths
    assert IGNORED_FILE not in found.paths
    assert IGNORED_FILE not in found.changed
    assert excluding(found, IGNORED_FILE), found.excluded


def test_a_file_the_working_tree_lost_is_left_out_though_the_index_holds_it(
        tmp_path, harness):
    """The deletion the new listing would otherwise hand the Inspector to read.

    The file is removed from the working tree with its index entry untouched, so
    a listing of the index alone would offer a path there is nothing at. It is
    excluded on exactly the terms a committed deletion is, and contributes its
    containing directory just the same.
    """
    target, found = expanded_against(
        tmp_path, harness,
        {WRITING: {"modified": [], "created": [], "deleted": [CHANGED_SOURCE]}},
        name="deleted-in-the-tree",
        prepare=lambda root: (Path(root) / CHANGED_SOURCE).unlink())

    assert CHANGED_SOURCE in tracked_paths(target), "the premise is gone"
    assert CHANGED_SOURCE not in found.paths
    assert excluding(found, CHANGED_SOURCE), found.excluded
    # Its directory is in scope, which is the whole of what a removal
    # contributes: what sits beside it is what it can have broken.
    assert SIBLING_SOURCE in found.paths


def asserts_the_repository_stopped_tracking(text: str) -> bool:
    """Whether one exclusion says the repository no longer tracks a path.

    The claim the wording may not make: it is false of a file the story has just
    written, and false of a path that was never there at all.
    """
    return "track" in text.lower()


def test_no_exclusion_for_a_path_the_tree_does_not_hold_asserts_a_removal(
        tmp_path, harness):
    """One sentence for all three forms, and it claims nothing of the index.

    A committed deletion, a working-tree deletion whose index entry remains and
    a path that never existed are excluded in one expansion, and the text after
    the path is the same for all three — which is what makes it read true of a
    path that was never there as well as of one that was removed.
    """
    target, found = expanded_against(
        tmp_path, harness,
        {WRITING: {"modified": [NEVER_EXISTED], "created": [],
                   "deleted": [DELETED_FILE, CHANGED_SOURCE]}},
        name="every-form-of-absence",
        prepare=lambda root: (Path(root) / CHANGED_SOURCE).unlink())

    wordings = {}
    for path in (DELETED_FILE, CHANGED_SOURCE, NEVER_EXISTED):
        entries = excluding(found, path)
        assert len(entries) == 1, (path, found.excluded)
        wordings[path] = entries[0].split(": ", 1)[1]
        assert not asserts_the_repository_stopped_tracking(wordings[path]), \
            entries[0]
    assert len(set(wordings.values())) == 1, wordings

    # The control for all three absences: the wording this replaced is reported
    # by the same reading, so it is a check that can see the claim rather than
    # one that has stopped looking.
    assert asserts_the_repository_stopped_tracking(
        "the repository no longer tracks it")


def asserts_the_tree_holds_no_file_there(text: str) -> bool:
    """Whether one exclusion claims the working tree has no file at the path.

    A sentence that names the *listing* the scope was taken from claims only
    that the listing left the path out, which stays true of a file git ignores
    while that file sits on disk. A sentence that names the tree itself makes
    the stronger claim, and of an ignored path that claim is false: it reports
    a file the reader of the run's detail log can open as one that is not
    there.
    """
    lowered = text.lower()
    return "tree" in lowered and "listing" not in lowered


def test_the_exclusion_for_an_ignored_file_claims_nothing_the_file_disproves(
        tmp_path, harness):
    """The form of absence the other exclusion case cannot reach.

    An ignored path is the one excluded path that is on disk: the listing
    leaves it out, so the expansion drops it, but a reader who follows the run's
    detail log to it finds the file. So the sentence has to be true of a path
    that is there as well as of one that is not, and here the same sentence is
    written for an ignored file and for a path that never existed — which is
    only possible while it claims no more than that the listing left the path
    out.
    """
    target, found = expanded_against(
        tmp_path, harness,
        {WRITING: {"modified": [NEVER_EXISTED], "created": [IGNORED_FILE],
                   "deleted": []}},
        name="ignored-and-never-existed",
        prepare=lambda root: wrote(root, IGNORED_FILE))

    assert (target / IGNORED_FILE).exists(), "the premise is gone"

    wordings = {}
    for path in (IGNORED_FILE, NEVER_EXISTED):
        entries = excluding(found, path)
        assert len(entries) == 1, (path, found.excluded)
        wordings[path] = entries[0].split(": ", 1)[1]
        assert not asserts_the_repository_stopped_tracking(wordings[path]), \
            entries[0]
        assert not asserts_the_tree_holds_no_file_there(wordings[path]), \
            entries[0]
    assert len(set(wordings.values())) == 1, wordings

    # The control for the absence above: each reading reports the wording it
    # exists to keep out, so neither is a check that has stopped looking. The
    # first of these is what the module said before this story, and the second
    # is what it said between this story's implementation and the correction
    # that followed it — each true of one form of absence and false of another.
    assert asserts_the_repository_stopped_tracking(
        "the repository no longer tracks it")
    assert asserts_the_tree_holds_no_file_there(
        "the working tree holds no such file")


def test_an_expansion_over_a_committed_tree_reaches_nothing_the_index_lacks(
        tmp_path, harness):
    """The other half: the change is invisible to the caller that runs after the
    completion commit.

    Over a tree where every file is committed, the scope is inside what the
    index holds — so the post-story inspection covers what it covered before
    this story, which the exact-tuple assertion above states file by file.
    """
    target, found = expanded_against(tmp_path, harness,
                                     RECORDS_NAMING_EVERY_CASE,
                                     name="committed-tree")

    assert found.paths, "an empty scope would satisfy any containment"
    assert set(found.paths) <= set(tracked_paths(target))


# ==========================================================================
# story-148: nothing in the module explains the scope as the index
# ==========================================================================


def module_tree(module) -> ast.Module:
    return ast.parse(Path(module.__file__).read_text(encoding="utf-8"))


def listing_helper(module) -> ast.FunctionDef:
    """The function in a module that runs the listing, found by the git
    subcommand it spells rather than by a name written here — so this keeps
    finding it after the rename the story makes."""
    found = []
    for node in ast.walk(module_tree(module)):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = node.body[1:] if ast.get_docstring(node) else node.body
        if any(isinstance(one, ast.Constant) and isinstance(one.value, str)
               and "ls-files" in one.value
               for statement in body for one in ast.walk(statement)):
            found.append(node)
    assert len(found) == 1, [node.name for node in found]
    return found[0]


def names_the_index(name: str) -> bool:
    """Whether an identifier explains itself as the index rather than the tree."""
    return "track" in name.lower() or "index" in name.lower()


def test_the_listing_helpers_name_no_longer_explains_the_scope_as_the_index():
    """The helper answers what the tree holds, and a reader meeting its name
    has to be told that rather than told it is the index."""
    assert not names_the_index(listing_helper(story_inspection).name)

    # The control: the same reading over the helper in `inspection`, whose
    # listing really is of what git tracks, reports it — so the absence above
    # is a reading that can see the claim.
    assert names_the_index(listing_helper(inspection).name)


def sentences(text: str) -> list[str]:
    return [one.strip() for one in (text or "").split(".") if one.strip()]


def unqualified_claims(text: str) -> list[str]:
    """Every sentence that reaches for the index without saying the scope is
    the tree.

    A sentence may name the index — the whole of why one listing serves both
    callers is a statement about it — as long as it is the contrast being drawn
    rather than the description being given.
    """
    return [one for one in sentences(text)
            if names_the_index(one)
            and "tree" not in one.lower() and "hold" not in one.lower()]


def test_neither_docstring_describes_the_inspection_scope_as_the_index():
    """`Expansion.changed`'s docstring and `expansion`'s, which are where a
    reader goes to find out what a scope contains."""
    for subject in (story_inspection.Expansion, story_inspection.expansion):
        text = subject.__doc__
        assert "tree" in text.lower(), subject
        assert unqualified_claims(text) == [], subject

    # The control: the wording these replaced is reported by the same reading.
    assert unqualified_claims(
        "The files git tracks, as repository-relative paths") == [
            "The files git tracks, as repository-relative paths"]


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


#: The two labels the inspection introduces its dropped paths with, and the cap
#: this run inspects under. A path named under one label may not satisfy an
#: assertion about the other, which is what `segment` and `detail_under` are
#: for. The same two labels appear in both places the inspection writes: the
#: run's own log, which carries one name per line under them, and the summary
#: line, which carries a count under each.
TRIMMED_LABEL = "trimmed to the file cap: "
LEFT_OUT_LABEL = "left out of scope: "
CAP_THE_RUN_EXCEEDS = 3


def inspection_line(target: Path) -> str:
    """The one summary line this run's events.log carries about its inspection.

    A failed dedupe now gets a line of its own beside it, so the summary is
    selected by the counts it carries rather than by the whole block being one
    line. Still exactly one: the summary is what these assertions are about.
    """
    said = [line for line in messages(target)
            if line.startswith(f"post-story inspection of {STORY_ID}")
            and "finding(s)" in line]
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


def test_what_the_cap_excluded_reaches_the_runs_own_log(
        tmp_path, harness, monkeypatch):
    """Every trimmed path named in the run's own log, and the invocation handed
    exactly the cap.

    Named rather than counted: a count tells a reader the scope was smaller
    than the expansion and leaves them no way to find out which file the
    inspection did not read. Where the names live changed with story-140 — the
    summary line was simultaneously the notice and the whole report, and on one
    run it named about 130 files — but nothing bounds how many are written to
    the log, because a bound there would be exactly the silent drop the naming
    exists against.

    The control for the naming is the other side of the same run: no path the
    invocation *was* handed appears under the trimmed label, so what is under
    it is the cap's doing rather than a copy of the scope.
    """
    target, _journal, code, inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="capped", findings=[finding()],
        **{story_inspection.MAX_FILES_KEY: CAP_THE_RUN_EXCEEDS})

    assert code == 0
    listed = rendered_paths(inspector.prompt)
    assert len(listed) == CAP_THE_RUN_EXCEEDS, listed

    trimmed = detail_under(in_the_runs_tree(target), TRIMMED_LABEL)
    assert DELETED_FILE in trimmed, trimmed
    assert SIBLING_TEST in trimmed, trimmed
    for path in listed:
        assert path not in trimmed, (path, trimmed)


def test_the_paths_the_expansion_left_out_are_named_in_the_runs_own_log(
        tmp_path, harness, monkeypatch):
    """A run whose writing stage records a path outside both scope keys and one
    the tree does not hold names both in that same log, with the reason each was
    left out.

    The control is the change the same record names that *is* in scope: it is
    handed to the invocation and is named nowhere under this label, so what is
    under it is the exclusion rather than the changed set.
    """
    target, _journal, code, inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="left-out", findings=[finding()],
        extra_changed=(OUT_OF_SCOPE_FILE, MISSING_CHANGE))

    assert code == 0
    left_out = "\n".join(
        detail_under(in_the_runs_tree(target), LEFT_OUT_LABEL))
    assert OUT_OF_SCOPE_FILE in left_out, left_out
    assert MISSING_CHANGE in left_out, left_out
    assert CHANGED_SOURCE not in left_out, left_out
    assert CHANGED_SOURCE in rendered_paths(inspector.prompt)


def test_the_summary_counts_what_it_no_longer_names_and_names_the_log(
        tmp_path, harness, monkeypatch):
    """The line is the size a reader can take in, and it says where the rest is.

    Both counts, the repository-relative path of the log holding the names, and
    neither list. The control for "neither list" is the log beside it: every
    name the line no longer carries is in that file, so the absence is the move
    this story made rather than a run that had nothing to name.
    """
    target, _journal, code, _inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="counted", findings=[finding()],
        extra_changed=(OUT_OF_SCOPE_FILE, MISSING_CHANGE),
        **{story_inspection.MAX_FILES_KEY: CAP_THE_RUN_EXCEEDS})

    assert code == 0
    tree = in_the_runs_tree(target)
    line = inspection_line(target)
    trimmed = detail_under(tree, TRIMMED_LABEL)
    left_out = detail_under(tree, LEFT_OUT_LABEL)
    assert trimmed and left_out, (trimmed, left_out)

    assert segment(line, TRIMMED_LABEL).split(";")[0] == str(len(trimmed)), line
    assert segment(line, LEFT_OUT_LABEL).split(";")[0] == str(len(left_out)), line

    assert str(detail_log_of(tree).relative_to(tree)) in line, line

    for named in trimmed + left_out:
        assert named not in line, (named, line)


def occupy_the_run_log(tree: Path) -> None:
    """The run's own log made unwritable, constructed rather than described.

    A directory stands where the log file goes, so the append the inspection
    makes raises rather than writing. It is done to the tree the run works in,
    at the seam between the run's last stage and the inspection, because that
    is the only moment at which the file is the inspection's to write: the
    coordinator creates the directory holding it as the run starts, so a tree
    prepared before that would have the preparation undone.

    What it takes away is the detail and nothing else. The stage logs that
    share this file are the agent runner's, and this module replaces the agent
    runner, so no part of the run other than the inspection writes here.
    """
    path = detail_log_of(tree)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()


def test_an_inspection_whose_log_cannot_be_written_still_announces_and_reports(
        tmp_path, harness, monkeypatch):
    """A log the inspection cannot write costs the detail and never the run.

    Every other call this mechanism makes is guarded on those terms, and the
    detail append is guarded on them too: an inspection that could not write
    the names still says it started, still says what it did, and still leaves
    the run completing with the status it would have had.

    The control is the same fixture with the log writable, run beside it: there
    the names *are* in the log and the run exits the same way. Without it the
    absence below would be satisfied by an inspection that had nothing to write
    and by a check that had stopped looking, neither of which is the guard
    holding. The counts on the two summary lines are compared for the same
    reason: they say the occupied run still knew what it could not write down.
    """
    capped = {story_inspection.MAX_FILES_KEY: CAP_THE_RUN_EXCEEDS}
    left_out = (OUT_OF_SCOPE_FILE, MISSING_CHANGE)

    control, _control_journal, control_code, _ci, _cr = completing_run(
        tmp_path, harness, monkeypatch, name="log-writable",
        findings=[finding()], extra_changed=left_out, **capped)
    target, _journal, code, _inspector, _runner = completing_run(
        tmp_path, harness, monkeypatch, name="log-occupied",
        findings=[finding()], extra_changed=left_out,
        at_last_stage=occupy_the_run_log, **capped)

    control_tree = in_the_runs_tree(control)
    tree = in_the_runs_tree(target)

    # The control: with the log writable, both lists reach it.
    assert detail_under(control_tree, TRIMMED_LABEL), control_code
    assert detail_under(control_tree, LEFT_OUT_LABEL), control_code

    # The occupied run: the detail is what was lost, and all of it.
    assert not detail_log_of(tree).is_file()
    assert detail_under(tree, TRIMMED_LABEL) == []
    assert detail_under(tree, LEFT_OUT_LABEL) == []

    # It still said it had started.
    kinds = [kind for kind, _ in kinds_and_messages(target)]
    assert ANNOUNCEMENT in kinds, kinds

    # It still said what it did, still named the log, and still counted what
    # that log does not hold.
    line = inspection_line(target)
    assert str(detail_log_of(tree).relative_to(tree)) in line, line
    for label in (TRIMMED_LABEL, LEFT_OUT_LABEL):
        assert (segment(line, label).split(";")[0]
                == segment(inspection_line(control), label).split(";")[0]), line

    # And the run completed with the status it would have had.
    assert code == control_code == 0


def test_an_inspection_with_nothing_in_scope_still_writes_what_it_left_out(
        tmp_path, harness, no_model):
    """The path that returns before any invocation still says what it left out.

    Every path the stages recorded is outside both scope keys, so there is
    nothing to inspect and the run's line is a sentence rather than a report.
    That is the path on which the exclusions matter most: nothing else in the
    run says why the inspection did not happen, so a move that dropped them
    here would take away the only account of it.

    Driven at the inspection rather than through a run, because the run is not
    the subject: no invocation is made on this path, and what is asserted is
    where the exclusions are written. The changed-files records are the
    workflow's own declared artifacts, so this names none of its own.

    The name is in the log and not on the line, and each half is the other's
    control: the line is short because the names moved, rather than because
    there were none.
    """
    journal = tmp_path / "nothing-journal.txt"
    target = build_target(tmp_path / "nothing-in-scope", journal,
                          **{story_inspection.MAX_FILES_KEY: ROOMY_CAP})
    run_dir = tmp_path / "nothing-run"
    run_dir.mkdir()
    for stage in WORKFLOW["stages"]:
        name = stage.get("changed_files")
        if name:
            _write(run_dir / name, {"modified": [OUT_OF_SCOPE_FILE],
                                    "created": [], "deleted": []})

    story_inspection.inspect_after_story(
        run_dir, target, harness_config.load_config(target), harness,
        STORY_ID, WORKFLOW["stages"])

    assert no_model.calls == 0
    left_out = "\n".join(detail_under(target, LEFT_OUT_LABEL))
    assert OUT_OF_SCOPE_FILE in left_out, left_out

    said = [line.split("] ", 1)[-1] for line
            in (run_dir / "events.log").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    assert len(said) == 1, said
    assert OUT_OF_SCOPE_FILE not in said[0], said


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
    the file cap this story adds.

    `inspect_min_severity` joined them in story-114 and is neither of the two
    things this test is about: it is a floor on what is filed, not a second
    ceiling on what an invocation may spend and not a second cap on how many
    briefs an inspection may file. The comparison stays exact so that a key
    that *is* one of those two cannot arrive unnoticed.
    """
    declared = {key for key in harness_config.declared_config_keys()
                if key.startswith("inspect")}
    assert declared == {inspection.MAX_COST_KEY, inspection.MAX_FINDINGS_KEY,
                        inspection.MIN_SEVERITY_KEY,
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
    # The summary line, selected by the counts it carries: a failed dedupe has
    # a line of its own beside it, and this assertion is about the summary.
    said = [line for line in messages(target)
            if line.startswith(f"post-story inspection of {STORY_ID}")
            and "finding(s)" in line]
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
    assert story_coordinator.dirty_paths(in_the_runs_tree(target)) == []


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
    assert SIBLING_SOURCE in story_coordinator.dirty_paths(in_the_runs_tree(target))


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
    assert story_coordinator.dirty_paths(in_the_runs_tree(target)) == []
    # The control for that emptiness: a file no stage produced is reported by
    # the same reader, so the clean tree is the reader looking rather than a
    # reader that has stopped seeing.
    (in_the_runs_tree(target) / SIBLING_SOURCE).write_text(
        "left behind\n", encoding="utf-8")
    assert story_coordinator.dirty_paths(in_the_runs_tree(target)) == [SIBLING_SOURCE]


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
