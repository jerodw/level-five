"""story-081 validation: the cross-run history outlives the runs it records.

A run directory is execution state and is not versioned, so everything the
harness knew about an execution goes with the directory when it is deleted.
This story adds a separate collection of append-only records under a
configured history directory, written through `append_event` as events occur.
What reaches which log, and what a record there carries, is declared in
`schemas/cross-run-history.schema.json` and nowhere else.

What this module validates: the declaration itself, the record a completed run
leaves, the record an escalated run leaves, the two records a run that retried
twice leaves, that a record carries only the fields its log declares and
reproduces no run artifact, that a kind named in no declaration writes nothing,
that the records survive deletion of the run directory, that each log is valid
JSONL a later run appends to rather than rewrites, that no routing decision
reads either log, that a stage naming a path beneath a blocked history
directory escalates the run, and that nothing in this repository's `.gitignore`
keeps the logs out of version control.

What it does not validate, and why, is written where the assertion would have
gone: this repository's own rule set does not yet block its history directory,
and the entry that would make it so is outside every stage's reach.

Pruning, retention and the prune's refusal are the subject of
`tests/test_cross_run_history_retention.py`; the two configuration keys are
proven configurable in `tests/test_config_keys_are_obeyed.py`, where every
declared key's proof lives.

The workflow these runs execute is built by the fixture in `tests/conftest.py`
rather than resolved out of what this repository deploys: the subject here is
what a record says about an execution, and the stage list is an input to that.
The execution rules are the fixture's own for the same reason — a run's
blocked-path enforcement needs *a* rule set, not the shipped one. The
assertions whose subject genuinely is what this repository ships — the schema,
the manifest, the sources under `orchestration/`, the ignore rules — read the
shipped files and say so.

Every absence asserted here is paired with a demonstration that the same check
reports the violation it exists to catch. Nothing invokes a model; nothing
resolves a baseline out of git.
"""
import ast
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import agent_runner
import conftest
import harness_config
import inspection
import schema_validator
import story_coordinator
import story_inspection
from agent_runner import AgentResult

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# The declaration: read here, from the schema, so nothing below derives the
# logs or their fields from the code that projects into them
# --------------------------------------------------------------------------

SCHEMA = schema_validator.load_schema(story_coordinator.CROSS_RUN_HISTORY_SCHEMA)

#: Each declared log's filename mapped to the shape of a record in it. Read
#: straight off the schema rather than through `history_log_declarations`, so
#: the reader is checked against the declaration below rather than standing in
#: for it.
DECLARATIONS = {name: log["items"] for name, log in SCHEMA["properties"].items()}

#: The one declared property that is a selection rather than a projected field.
EVENT = story_coordinator.HISTORY_EVENT_PROPERTY

#: The logs this harness has no producer for. Written here because they are
#: this assertion's subject: the story's whole treatment of them is that the
#: schema names them and the harness ships none of them.
#:
#: The inspection log was one of them until story-100, which gave the harness
#: the producer it was reserved against: a completed run inspects what its
#: story changed and appends a record. So it moved out of this tuple and into
#: the declaration, which is what the reservation always said would happen.
RESERVED = ("adjudication-log.jsonl",)

#: The two logs, told apart by what their own declarations carry rather than by
#: their filenames: the outcome log is the one declaring how an execution
#: ended, and the routing log is the one declaring the retry decision an entry
#: recorded. Derived, so this module writes neither filename.
OUTCOME_LOG = next(name for name, shape in DECLARATIONS.items()
                   if "status" in shape["properties"])
ROUTING_LOG = next(name for name, shape in DECLARATIONS.items()
                   if "retry_decision" in shape["properties"])

#: The third log, told apart the same way: it is the one declaring the identity
#: an authorizing act was observed from. Derived, so this module writes no
#: filename here either.
CONFERRAL_LOG = next(name for name, shape in DECLARATIONS.items()
                     if "conferred_by" in shape["properties"])

#: The fourth, told apart by the count only an inspection records. Derived for
#: the reason the three above are, so this module still writes no log filename.
INSPECTION_LOG = next(name for name, shape in DECLARATIONS.items()
                      if "findings" in shape["properties"])



def projected(log: str) -> set[str]:
    """The fields a record in `log` carries: everything declared but the
    selection."""
    return set(DECLARATIONS[log]["properties"]) - {EVENT}


def kinds_routed_to(log: str) -> list[str]:
    return DECLARATIONS[log]["properties"][EVENT]["enum"]


def routed_kinds() -> set[str]:
    """Every kind any declaration routes somewhere.

    Over all the declared logs rather than a named pair of them: a kind the
    fourth log claims is a routed kind, and a union that named only two would
    call it unrouted and then assert it projects nowhere.
    """
    return {kind for log in DECLARATIONS for kind in kinds_routed_to(log)}

#: The kinds a run can put through the projection at all: the event kinds the
#: run's own structured history declares. Read off that schema rather than
#: listed here, so a kind added there is covered without this module being
#: edited.
RUN_KINDS = set(
    schema_validator.load_schema("execution-history")["items"]["properties"]
    [EVENT]["enum"]
)

#: The declared logs a *run* can reach, and the declared logs it cannot. A log
#: whose enum names no kind the run's own history declares has no producer
#: inside a run: what reaches it is a caller outside one, using the same
#: per-log append with an explicit history directory. Derived from the two
#: declarations rather than written down, so neither set is this module's
#: opinion about which log is which.
RUN_PRODUCED_LOGS = {
    log for log in DECLARATIONS if set(kinds_routed_to(log)) & RUN_KINDS
}
RUNLESS_LOGS = set(DECLARATIONS) - RUN_PRODUCED_LOGS


# --------------------------------------------------------------------------
# The fixture workflow, the fixture rules, and the runs
# --------------------------------------------------------------------------

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
        outputs=(conftest.DOCUMENTATION_REPORT,
                 conftest.DOCUMENTER_CHANGED_FILES),
        changed_files=conftest.DOCUMENTER_CHANGED_FILES,
        schemas={conftest.DOCUMENTER_CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        retry_routing={"implementation-defect": {
            "stage": conftest.StageRef(0),
            "when": "the behaviour the story asked for is missing"}}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="cross-run-history-workflow",
)

STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, DOCUMENTING, VERIFYING = STAGE_NAMES
RETRY_CATEGORY, RETRY_STAGE = conftest.first_retry_route(WORKFLOW)

#: The default this repository's own deployment leaves in place, resolved from
#: the harness rather than written down, and the prefix the fixture rules below
#: block. A test derives the name from the fixture; the fixture derives it from
#: the one place the harness spells it.
HISTORY_DIR = harness_config.DEFAULT_HISTORY_DIR
BLOCKED_HISTORY_PREFIX = HISTORY_DIR.rstrip("/") + "/"

#: The rule set these runs execute under. The fixture's own rather than the
#: shipped one: what a run does with a blocked path is the subject, and which
#: paths this repository happens to block is an input to it. The one assertion
#: about what this repository ships reads the shipped file and is marked as
#: doing so.
FIXTURE_RULES = {
    "max_retries": 2,
    "require_verifier_pass": True,
    "blocked_paths": [".git/", ".harness/runs/", "rules/",
                      BLOCKED_HISTORY_PREFIX],
}
MAX_RETRIES = FIXTURE_RULES["max_retries"]

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


#: What the fixture allows one post-story inspection to take into scope. The
#: key is configured at all because since story-100 a run *produces* the fourth
#: declared log: leaving it unset would leave that log with no producer inside a
#: run, and the derived sets below would then describe a deployment rather than
#: the harness.
INSPECTION_FILE_CAP = 20


@pytest.fixture
def configured_workflow() -> str:
    return WORKFLOW["name"]


@pytest.fixture
def target_root(target_root: Path) -> Path:
    """The shared target with the post-story inspection turned on.

    Overrides the fixture in `tests/conftest.py` and requests it, so what these
    runs execute in is the same repository every other module's runs execute
    in plus the one key this module needs. The addition is committed, because a
    test's own setup is part of the repository the run starts *from*.
    """
    config = target_root / ".harness" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + f"{inspection.SOURCE_DIRS_KEY}:\n  - src/\n"
        + f"{story_inspection.MAX_FILES_KEY}: {INSPECTION_FILE_CAP}\n",
        encoding="utf-8")
    conftest.commit_setup(target_root, "turn the post-story inspection on")
    return target_root


class NoFindings:
    """Stands in for `agent_runner.run_agent` for the post-story inspection.

    It reaches no model and finds nothing: what this module is about is the
    record an inspection leaves, and a finding would only add a queue entry to
    every fixture here. `tests/test_a_completed_story_is_inspected.py` is where
    what an inspection finds is the subject.
    """

    def __init__(self):
        self.calls = 0

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None, max_budget_usd=None, suite_command=None):
        self.calls += 1
        artifact, _ = inspection.findings_paths(
            Path(cwd), harness_config.load_config(Path(cwd)))
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps({"findings": []}), encoding="utf-8")
        return AgentResult(ok=True, result_text="inspected")


@pytest.fixture(autouse=True)
def no_model(monkeypatch) -> NoFindings:
    """Substituted for every test in this module, so the inspection a completed
    run makes reaches this fake rather than a provider."""
    inspector = NoFindings()
    monkeypatch.setattr(agent_runner, "run_agent", inspector)
    return inspector


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    root = conftest.materialize_workflow(
        WORKFLOW, tmp_path / "cross-run-history-harness", rules=FIXTURE_RULES)
    # The template the post-story inspection renders. The builder writes one
    # prompt per workflow stage and an inspection is not a stage, so this one
    # is the fixture's own — and it is deliberately bare: what an Inspector is
    # asked is `tests/test_a_completed_story_is_inspected.py`'s subject, and
    # all this module needs is a rendering that succeeds.
    (root / "prompts" / inspection.INSPECTOR_PROMPT).write_text(
        "# the template this fixture renders\n{{scope_paths}}\n",
        encoding="utf-8")
    return root


def failing_verdict(attempt: int) -> dict:
    return {
        "status": "failed",
        "blocking_issues": [{
            "severity": "high",
            "issue": f"attempt {attempt} did not implement the sample behavior",
            "location": f"src/attempt_{attempt}.py",
            "required_behavior": f"the sample behavior exists after attempt {attempt}",
        }],
        "unverified": [],
        "retry_recommended": True,
        "retry_target": RETRY_CATEGORY,
    }


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class Runner:
    """A fake agent runner that writes each stage's declared artifacts.

    `tester_record` is a seam for the blocked-path case: a stage whose
    changed-files record names a path beneath the blocked history directory
    must escalate the run rather than be allowed to edit the record of its own
    execution.
    """

    def __init__(self, target_root: Path, verdicts: list[dict],
                 story_id: str = "story-001",
                 tester_record: dict | None = None):
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.verdicts = list(verdicts)
        self.tester_record = tester_record or {
            "modified": [], "created": ["tests/test_app.py"], "deleted": []}
        self.calls: list[str] = []
        self.attempt = 1

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None, max_budget_usd=None, suite_command=None):
        self.calls.append(stage)
        self.attempt = max(1, self.calls.count(RETRY_STAGE))
        if stage == WRITING:
            write_json(self.run_dir / conftest.CHANGED_FILES, {
                "modified": ["src/app.py"],
                "created": [f"src/attempt_{self.attempt}.py"],
                "deleted": [],
            })
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                f"Implemented on attempt {self.attempt}.\n", encoding="utf-8")
            (Path(cwd) / "src" / f"attempt_{self.attempt}.py").write_text(
                f"# attempt {self.attempt}\n", encoding="utf-8")
        elif stage == VALIDATING:
            write_json(self.run_dir / conftest.TEST_RESULTS, {"tests_written": 1})
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES,
                       self.tester_record)
        elif stage == VERIFYING:
            verdict = conftest.answering_guidance(
                self.verdicts.pop(0), self.run_dir)
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, verdict)
            if verdict["status"] == "failed":
                write_json(self.run_dir / conftest.RETRY_GUIDANCE, {
                    "current_focus": [{
                        "focus": f"guidance issued after attempt {self.attempt}",
                        "satisfied_when": "the next attempt closes what it names",
                    }],
                    "preserve_behavior": ["existing behavior"],
                    "retry_scope": [f"src/attempt_{self.attempt}.py"],
                })
        elif stage == DOCUMENTING:
            (self.run_dir / conftest.DOCUMENTATION_REPORT).write_text(
                "Documented.\n", encoding="utf-8")
            write_json(self.run_dir / conftest.DOCUMENTER_CHANGED_FILES,
                       {"modified": [], "created": [], "deleted": []})
        return AgentResult(ok=True, result_text=f"{stage} done")


def history_dir_of(target_root: Path) -> Path:
    return target_root / HISTORY_DIR


def run_dir_of(target_root: Path, story_id: str = "story-001") -> Path:
    return target_root / ".harness" / "runs" / story_id


def state_of(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text(encoding="utf-8"))


def log_text(target_root: Path, log: str) -> str:
    path = history_dir_of(target_root) / log
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def records(target_root: Path, log: str) -> list[dict]:
    text = log_text(target_root, log)
    return [json.loads(line) for line in text.splitlines() if line]


def routed_entries(run_dir: Path, log: str) -> list[dict]:
    """The run's own history entries whose kind the log's declaration names.

    Selection read off the declaration, so this counts what *should* reach a
    log without re-implementing the projection that puts it there.
    """
    kinds = kinds_routed_to(log)
    return [entry for entry in story_coordinator.load_history(run_dir)
            if entry.get(EVENT) in kinds]


@pytest.fixture
def completed(target_root, harness_root):
    runner = Runner(target_root, [PASS])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 0
    return target_root, run_dir_of(target_root)


@pytest.fixture
def escalated(target_root, harness_root):
    """An escalation the verifier asked for, which takes no retry."""
    runner = Runner(target_root, [{**failing_verdict(1), "retry_recommended": False}])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 2
    return target_root, run_dir_of(target_root)


@pytest.fixture
def retried_then_completed(target_root, harness_root):
    """One retry taken and then a pass, which is the one shape that reaches
    every log a run can reach: the retry log, the outcome log, and — because
    only a completing run inspects what it changed — the inspection log."""
    runner = Runner(target_root, [failing_verdict(1), PASS])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 0
    return target_root, run_dir_of(target_root)


@pytest.fixture
def retried_twice(target_root, harness_root):
    """Two retries taken, then the ceiling escalates the third attempt."""
    runner = Runner(target_root, [failing_verdict(n) for n in (1, 2, 3)])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 2
    return target_root, run_dir_of(target_root)


# --------------------------------------------------------------------------
# The declaration itself
# --------------------------------------------------------------------------


def test_the_schema_is_shipped_valid_and_named_in_the_manifest():
    name = story_coordinator.CROSS_RUN_HISTORY_SCHEMA
    assert name in schema_validator.shipped_schemas()
    assert (REPO_ROOT / "schemas" / f"{name}.schema.json").is_file()
    assert SCHEMA["title"] == name
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    # Checkable by the validator this harness actually ships, which is what
    # every other shipped schema is held to.
    assert schema_validator.unsupported_keywords(SCHEMA) == []


def test_each_declared_log_names_the_kinds_it_records_and_the_fields_it_carries():
    derived = {OUTCOME_LOG, ROUTING_LOG, CONFERRAL_LOG, INSPECTION_LOG}
    assert set(DECLARATIONS) == derived
    # Each was told apart by a field of its own, so four derivations that
    # collapsed onto one log would be reported here rather than silently
    # asserting the same log four times.
    assert len(derived) == len(DECLARATIONS)
    for log, shape in DECLARATIONS.items():
        assert log.endswith(".jsonl"), log
        assert shape["type"] == "object"
        assert kinds_routed_to(log), log
        assert projected(log), log
        # Every required field is one the record carries, so a log cannot
        # require the property that only selects into it.
        assert set(shape["required"]) <= projected(log), log
    # The enums are pairwise disjoint, so no entry is a record in two logs by
    # declaration — which is what makes "exactly one record" a statement about
    # the run rather than about which log was looked at first.
    for one in DECLARATIONS:
        for other in DECLARATIONS:
            if one != other:
                assert not set(kinds_routed_to(one)) & set(kinds_routed_to(other))


def test_the_reader_returns_exactly_what_the_schema_declares():
    """`history_log_declarations` is the projection's only view of the
    declaration; nothing else below goes through it."""
    assert story_coordinator.history_log_declarations() == DECLARATIONS


def declares_a_log(name: str) -> bool:
    """Whether the declaration routes anything to a log called `name`.

    A predicate rather than an inline membership test, so the control below
    drives exactly the code the absence assertion does.
    """
    return name in DECLARATIONS


def test_the_reserved_logs_are_named_as_reserved_and_declared_nowhere():
    description = SCHEMA["description"]
    assert "reserved" in description.lower()
    for name in RESERVED:
        assert name in description, name
        assert not declares_a_log(name), name
    # The control for the absence: the same predicate over the logs that *are*
    # declared answers True. So "declared nowhere" is the declaration being
    # read rather than a mapping that could not have held either name.
    assert DECLARATIONS
    assert all(declares_a_log(name) for name in DECLARATIONS)


def test_the_harness_creates_no_log_the_declaration_does_not_name(
    retried_then_completed,
):
    """The absence, with the declared logs as its control.

    A directory listing that found nothing would satisfy "no reserved log was
    created" just as happily as one looking in the right place, so the listing
    is required to equal the run-produced set exactly — which it can only do by
    having seen the files that are there. The run shape is the one that reaches
    every log a run can reach, so none is absent for want of anything to record.

    Run-produced rather than declared, because since story-087 a declared log
    can have no producer inside a run: a conferring record is written by the
    process that observes an authorizing act, which has no run directory to
    resolve a history directory from. The distinction is derived from the two
    declarations rather than named here, and is asserted to be a real one — a
    listing equal to the whole declared set would mean a run had produced
    something no run can produce.

    The shape is the one that retried *and* completed, because a run that ends
    any other way inspects nothing: an escalated run reaches every log but the
    inspection's, and requiring it to hold one would require a run to write
    what a stopped run must not.
    """
    target_root, _ = retried_then_completed
    present = {path.name for path in history_dir_of(target_root).iterdir()}
    assert RUN_PRODUCED_LOGS and RUNLESS_LOGS
    assert present == RUN_PRODUCED_LOGS
    for name in RESERVED:
        assert name not in present


# --------------------------------------------------------------------------
# One record per outcome, one per retry
# --------------------------------------------------------------------------


def test_a_completed_run_appends_exactly_one_record_saying_it_completed(completed):
    target_root, run_dir = completed
    written = records(target_root, OUTCOME_LOG)
    assert len(written) == 1
    # The status is compared against the run's own state rather than against a
    # word written here, so the record and the run have to agree about how the
    # execution ended.
    assert written[0]["status"] == state_of(run_dir)["status"] == "completed"
    assert written[0]["story_id"] == run_dir.name
    assert records(target_root, ROUTING_LOG) == []


def test_a_completed_run_appends_one_inspection_record_and_a_stopped_run_none(
    completed, no_model,
):
    """The fourth log's producer, and the one shape that has one.

    A completed run inspects what its story changed, so it leaves one record
    carrying the story it inspected and the three counts the declaration names.
    The record is a summary and not a copy: what it says about the findings is
    three numbers, and which finding went which way is in the run's own
    events.log.
    """
    target_root, run_dir = completed
    written = records(target_root, INSPECTION_LOG)

    assert no_model.calls == 1, "the inspection made no invocation"
    assert len(written) == 1
    assert written[0]["story_id"] == run_dir.name
    assert set(written[0]) <= projected(INSPECTION_LOG)
    assert (written[0]["findings"], written[0]["filed"],
            written[0]["dropped"]) == (0, 0, 0)


def test_a_run_that_escalated_leaves_the_inspection_log_untouched(
    escalated, no_model,
):
    """The control for the record above: the same fixture, the same key, a run
    that stopped — and no invocation, no record."""
    target_root, _ = escalated
    assert no_model.calls == 0
    assert records(target_root, INSPECTION_LOG) == []


def test_an_escalated_run_appends_exactly_one_record_saying_it_escalated(escalated):
    target_root, run_dir = escalated
    written = records(target_root, OUTCOME_LOG)
    assert len(written) == 1
    assert written[0]["status"] == state_of(run_dir)["status"] == "escalated"
    assert written[0]["story_id"] == run_dir.name
    # An escalation the verifier asked for takes no retry, so nothing routed
    # to the retry log.
    assert state_of(run_dir)["retry_count"] == 0
    assert records(target_root, ROUTING_LOG) == []


def test_a_run_that_retried_twice_appends_two_retry_records_and_one_outcome(
    retried_twice,
):
    target_root, run_dir = retried_twice
    retries = records(target_root, ROUTING_LOG)
    outcomes = records(target_root, OUTCOME_LOG)
    assert len(retries) == MAX_RETRIES == state_of(run_dir)["retry_count"]
    assert len(outcomes) == 1
    assert outcomes[0]["retry_count"] == state_of(run_dir)["retry_count"]
    assert [record["retry_stage"] for record in retries] == [RETRY_STAGE] * MAX_RETRIES
    assert {record["retry_category"] for record in retries} == {RETRY_CATEGORY}
    assert all(record["story_id"] == run_dir.name for record in retries)


@pytest.mark.parametrize("shape", ["completed", "escalated", "retried_twice"])
def test_each_log_holds_exactly_the_entries_its_declaration_routes_there(
    shape, request,
):
    """The count in every shape, stated once against the declaration.

    The run's own execution history is filtered by the log's declared enum and
    nothing else, so this says the projection put a record there for each entry
    the declaration selects — no more, and none missed.
    """
    target_root, run_dir = request.getfixturevalue(shape)
    for log in DECLARATIONS:
        assert len(records(target_root, log)) == len(routed_entries(run_dir, log))


# --------------------------------------------------------------------------
# A record carries what its log declares and nothing else
# --------------------------------------------------------------------------


def undeclared_fields(record: dict, log: str) -> set[str]:
    """The one predicate every "only the declared fields" assertion goes
    through, so the controls drive the same code the assertions do."""
    return set(record) - projected(log)


@pytest.mark.parametrize("shape", ["completed", "escalated", "retried_twice"])
def test_no_record_carries_a_field_its_declaration_does_not_name(shape, request):
    target_root, _ = request.getfixturevalue(shape)
    for log in DECLARATIONS:
        for record in records(target_root, log):
            assert undeclared_fields(record, log) == set()
            assert set(DECLARATIONS[log]["required"]) <= set(record)
            # The selection is not a field: what routed a record is read off
            # the enum that named its kind rather than repeated on every line.
            assert EVENT not in record


def test_the_field_check_reports_a_record_carrying_an_undeclared_field():
    """The control, constructed rather than argued: a record with a field no
    declaration names is reported by the same predicate."""
    for log in DECLARATIONS:
        planted = {name: "value" for name in projected(log)}
        planted["xyzzy_undeclared"] = "something no declaration names"
        assert undeclared_fields(planted, log) == {"xyzzy_undeclared"}


#: The length below which a shared string is a field value both a record and an
#: artifact legitimately carry rather than one reproducing the other. Longer
#: than any status word, story id or stage name the harness writes, and far
#: shorter than the summary the control below plants.
SHORTER_THAN_AN_ARTIFACT = 20


def artifacts_reproduced(target_root: Path, run_dir: Path) -> list[str]:
    """Which run artifacts' contents appear verbatim in a log.

    A record is a summary, never a copy: the per-run artifacts hold the detail,
    and a log that duplicated them would inherit their size and lose its
    queryability.

    Only an artifact longer than a record's own field values is compared. A
    handful of characters appearing in both — a status word, a story id, a
    stage name — is the summary doing its job rather than a copy, and comparing
    those would report a duplication that is not one.
    """
    logs = "".join(log_text(target_root, log) for log in DECLARATIONS)
    found = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except UnicodeDecodeError:  # pragma: no cover - no binary artifact today
            continue
        if len(text) > SHORTER_THAN_AN_ARTIFACT and text in logs:
            found.append(path.relative_to(run_dir).as_posix())
    return found


@pytest.mark.parametrize("shape", ["completed", "retried_twice"])
def test_no_record_reproduces_the_content_of_a_run_artifact(shape, request):
    target_root, run_dir = request.getfixturevalue(shape)
    # The run really did produce artifacts, so the scan below had something to
    # find and its emptiness is a fact about the logs rather than about an
    # empty run directory.
    assert [p for p in run_dir.rglob("*") if p.is_file()]
    assert artifacts_reproduced(target_root, run_dir) == []


def test_the_reproduction_check_reports_an_artifact_copied_into_a_log(
    completed, tmp_path,
):
    """The control: the same scan over a target whose log really does carry an
    artifact's content must name it."""
    target_root, run_dir = completed
    copied = tmp_path / "copied-target"
    shutil.copytree(target_root, copied)
    artifact = run_dir_of(copied) / conftest.IMPLEMENTATION_SUMMARY
    with open(history_dir_of(copied) / OUTCOME_LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(
            {"story_id": "story-001",
             "status": artifact.read_text(encoding="utf-8").strip()}) + "\n")
    assert artifacts_reproduced(copied, run_dir_of(copied)) == \
        [conftest.IMPLEMENTATION_SUMMARY]


# --------------------------------------------------------------------------
# The selection is decided by the declaration alone
# --------------------------------------------------------------------------


def test_an_entry_whose_kind_no_declaration_names_projects_nowhere():
    """`history_record` against constructed entries, so nothing has to be run.

    The unrouted kind is taken from the shipped execution-history declaration's
    own enum minus everything the two logs claim, so this cannot pass by naming
    a kind that does not exist.
    """
    declared_kinds = schema_validator.load_schema(
        "execution-history")["items"]["properties"][EVENT]["enum"]
    unrouted = sorted(set(declared_kinds) - routed_kinds())
    assert unrouted, "every declared event kind is routed; there is nothing to check"

    for kind in unrouted:
        entry = {"sequence": 1, "timestamp": "2026-08-28 12:00:00",
                 EVENT: kind, "message": "an entry no log declares"}
        for log in DECLARATIONS:
            assert story_coordinator.history_record(
                entry, [], "story-001", DECLARATIONS[log]) is None, (kind, log)

    # The control: the same call with a kind the declaration *does* name
    # returns a record, so the None above is the selection deciding rather
    # than the projection being unable to produce anything.
    for log in DECLARATIONS:
        entry = {"sequence": 1, "timestamp": "2026-08-28 12:00:00",
                 EVENT: kinds_routed_to(log)[0], "message": "a routed entry"}
        record = story_coordinator.history_record(
            entry, [], "story-001", DECLARATIONS[log])
        assert record is not None and record["story_id"] == "story-001"


@pytest.mark.parametrize("shape", ["completed", "retried_twice"])
def test_a_run_emits_kinds_no_log_declares_and_they_leave_no_record(shape, request):
    """The same claim as a fact about a real run rather than a constructed
    entry: every run emits stage events, and none of them reach a log."""
    target_root, run_dir = request.getfixturevalue(shape)
    emitted = {entry[EVENT] for entry in story_coordinator.load_history(run_dir)}
    assert emitted - routed_kinds(), "the run emitted only routed kinds"
    total = sum(len(records(target_root, log)) for log in DECLARATIONS)
    assert total == sum(len(routed_entries(run_dir, log)) for log in DECLARATIONS)


# --------------------------------------------------------------------------
# The records outlive the run directory
# --------------------------------------------------------------------------


def test_the_records_survive_deletion_of_the_run_directory(
    retried_then_completed,
):
    """The property the story exists for.

    The run directory is deleted outright — the state, the events log, the
    per-run execution history, every artifact — and the logs still hold that
    run's records.
    """
    target_root, run_dir = retried_then_completed
    # Over the logs a run can reach, for the reason
    # `test_the_harness_creates_no_log_the_declaration_does_not_name` gives: a
    # log with no producer inside a run holds nothing after one, so requiring
    # every declared log to be non-empty here would be requiring a run to write
    # what no run writes. The set is derived, and asserted non-empty, so this
    # is still every log a run touches.
    assert RUN_PRODUCED_LOGS
    before = {log: records(target_root, log) for log in RUN_PRODUCED_LOGS}
    assert all(before.values()), before

    shutil.rmtree(run_dir)
    assert not run_dir.exists()

    after = {log: records(target_root, log) for log in RUN_PRODUCED_LOGS}
    assert after == before
    assert all(record["story_id"] == run_dir.name
               for written in after.values() for record in written)


# --------------------------------------------------------------------------
# Valid JSONL, appended to rather than rewritten
# --------------------------------------------------------------------------


def jsonl_problems(text: str) -> list[str]:
    """What stops `text` from being one JSON object per line.

    A predicate rather than a run of inline assertions, so the control below
    can show it reporting each shape it exists to catch.
    """
    problems = []
    if text and not text.endswith("\n"):
        problems.append("the file ends in a partial line")
    for number, line in enumerate(text.split("\n")[:-1] if text else [], start=1):
        if not line.strip():
            problems.append(f"line {number} is blank")
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {number} is not valid JSON")
            continue
        if not isinstance(parsed, dict):
            problems.append(f"line {number} is not an object")
    return problems


@pytest.mark.parametrize("shape", ["completed", "escalated", "retried_twice"])
def test_each_log_is_valid_jsonl(shape, request):
    target_root, _ = request.getfixturevalue(shape)
    for log in DECLARATIONS:
        text = log_text(target_root, log)
        assert jsonl_problems(text) == [], log


def test_the_jsonl_check_reports_each_shape_it_exists_to_catch():
    """The control for the absence above, one constructed violation per shape."""
    assert jsonl_problems('{"a": 1}\n{"b": 2}\n') == []
    assert jsonl_problems('{"a": 1}\n{"b": 2}') == ["the file ends in a partial line"]
    assert jsonl_problems('{"a": 1}\n\n') == ["line 2 is blank"]
    assert jsonl_problems('{"a": 1}\nnot json\n') == ["line 2 is not valid JSON"]
    assert jsonl_problems('{"a": 1}\n[1, 2]\n') == ["line 2 is not an object"]


SECOND_STORY_ID = "story-002"


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


def land(root: Path, story_id: str, base: str) -> None:
    """Take a finished story's branch onto the base it was cut from.

    A second run starts where the first one did — on the base, with the first
    story's commit in it — because that is where a story is run from and
    because the coordinator refuses to cut a new story branch from a previous
    story's branch. Fast-forward, since the base has not moved meanwhile.

    The commit first is what carries whatever the run left onto the base, and
    it is deliberately not conditioned on there being anything. Before
    story-082 a completion appended its `story-completed` event *after* its
    commit, so the last thing a successful run did was write files the commit
    had not swept in — leaving the tree dirty in tracked files, which a
    checkout away from the branch refuses rather than discards. That ordering
    is now the other way round and this target's tree is clean here, so the
    commit is usually an empty one. It stays because what this helper is for is
    landing a finished branch whatever it left behind, and because an empty
    commit costs the assertion after this call nothing.
    """
    conftest.commit_setup(root, f"Land {story_id}")
    git(root, "checkout", "-q", base)
    git(root, "merge", "-q", "--ff-only", f"story/{story_id}")


def test_a_later_run_appends_to_the_logs_rather_than_rewriting_them(
    target_root, harness_root,
):
    """Two runs in one repository, and the first run's bytes still leading.

    Byte-for-byte prefix equality rather than a membership test: a log the
    append path had opened for writing would hold only the second run's
    records, and a log it had rewritten around would hold the first run's
    records in some other form. Only appending leaves the earlier text exactly
    where it was.
    """
    base = git(target_root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    first = Runner(target_root, [failing_verdict(1), PASS])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, first) == 0
    # The logs a run can reach, for the reason the listing assertion above
    # gives: a log with no producer inside a run is empty after one, and
    # requiring it to hold bytes would require a run to write what no run
    # writes. The set is derived and asserted non-empty.
    assert RUN_PRODUCED_LOGS
    after_first = {log: log_text(target_root, log) for log in RUN_PRODUCED_LOGS}
    assert all(after_first.values()), after_first

    land(target_root, "story-001", base)
    # The landing carried the first run's records onto the base, so what the
    # second run appends to is the file the first run left.
    assert {log: log_text(target_root, log)
            for log in RUN_PRODUCED_LOGS} == after_first

    story = (target_root / ".harness" / "stories" / "story-001.yaml").read_text(
        encoding="utf-8")
    (target_root / ".harness" / "stories" / f"{SECOND_STORY_ID}.yaml").write_text(
        story.replace("id: story-001", f"id: {SECOND_STORY_ID}"), encoding="utf-8")
    conftest.commit_setup(target_root, f"Plan {SECOND_STORY_ID}")

    second = Runner(target_root, [failing_verdict(1), PASS],
                    story_id=SECOND_STORY_ID)
    assert story_coordinator.run_story(
        SECOND_STORY_ID, harness_root, target_root, second) == 0

    for log in RUN_PRODUCED_LOGS:
        after_second = log_text(target_root, log)
        assert after_second.startswith(after_first[log]), log
        assert len(after_second) > len(after_first[log]), log
        assert jsonl_problems(after_second) == [], log
        ids = [record["story_id"] for record in records(target_root, log)]
        assert ids[:len(ids) // 2] == ["story-001"] * (len(ids) // 2)
        assert SECOND_STORY_ID in ids


def opened_modes(source: str) -> set[str]:
    """Every mode the `open` calls in a piece of source ask for.

    The append path's whole claim is that it never opens a log for writing, and
    that is a property of the call rather than of what the file ends up
    holding — a rewrite that happened to produce the same bytes would pass a
    content check and still be a second rewrite point.
    """
    modes = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                modes.add(node.args[1].value)
            for keyword in node.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    modes.add(keyword.value.value)
    return modes


def coordinator_function(name: str) -> str:
    return conftest.function_source(
        (REPO_ROOT / "orchestration" / "story_coordinator.py").read_text(
            encoding="utf-8"), name)


def test_the_append_path_opens_every_log_for_appending_and_never_for_writing():
    """The absence, with the same function mutated as its control.

    A reader that found no `open` call at all — because the function moved, or
    because the extraction returned the wrong text — would report an empty set
    and satisfy "no write mode" just as happily. So the same reader is run over
    the same source with the mode changed, and must report the write.
    """
    # Since story-087 the open lives in the per-log append that was factored
    # out so a caller outside a run can reach it, and the projection above it
    # is one of its callers. Both are read here, so neither can start opening
    # a log for writing without this failing.
    seam = coordinator_function("append_history_records")
    projection = coordinator_function("_append_history_records")
    assert opened_modes(seam) == {"a"}
    assert opened_modes(seam.replace('"a"', '"w"')) == {"w"}
    assert opened_modes(projection) == set()
    # The control for that emptiness: the same reader over the same text with
    # an open planted in it reports the mode, so the empty set above is the
    # function opening nothing rather than the reader seeing nothing.
    assert opened_modes(projection + '\nopen(path, "w")\n') == {"w"}
    # And neither rewrites by another route: `write_text` replaces a file
    # whole, which is the prune's job and only the prune's.
    for source in (seam, projection):
        assert "write_text" not in source
    assert "write_text" in coordinator_function("prune_history")


# --------------------------------------------------------------------------
# History is evidence, never state
# --------------------------------------------------------------------------


def test_no_routing_decision_changes_when_the_logs_already_hold_records(
    target_root, harness_root, tmp_path,
):
    """Two identical runs, one against a repository whose logs already claim a
    long and eventful past.

    If any routing decision read a log, a history claiming prior escalations
    and prior retries for this very story would move something: the stage
    sequence, the retry count, the exit code, the recorded status. Nothing
    moves, because state.json is the coordinator's only routing source.
    """
    seeded = tmp_path / "seeded-target"
    shutil.copytree(target_root, seeded)
    history = history_dir_of(seeded)
    history.mkdir(parents=True, exist_ok=True)
    for log in DECLARATIONS:
        lines = []
        for index in range(5):
            record = {name: "story-001" if name == "story_id" else f"seeded-{index}"
                      for name in DECLARATIONS[log]["required"]}
            record["timestamp"] = "2020-01-01 00:00:00"
            lines.append(json.dumps(record))
        (history / log).write_text("\n".join(lines) + "\n", encoding="utf-8")
    conftest.commit_setup(seeded, "a history this run did not produce")

    plain_runner = Runner(target_root, [failing_verdict(1), PASS])
    plain_code = story_coordinator.run_story(
        "story-001", harness_root, target_root, plain_runner)
    seeded_runner = Runner(seeded, [failing_verdict(1), PASS])
    seeded_code = story_coordinator.run_story(
        "story-001", harness_root, seeded, seeded_runner)

    assert plain_code == seeded_code == 0
    assert plain_runner.calls == seeded_runner.calls
    plain_state = state_of(run_dir_of(target_root))
    seeded_state = state_of(run_dir_of(seeded))
    for key in ("status", "retry_count", "verification_iterations", "current_stage"):
        assert plain_state[key] == seeded_state[key], key
    # And the seeded records are still there, untouched, beneath the new ones:
    # nothing read them and nothing rewrote them either.
    for log in DECLARATIONS:
        written = records(seeded, log)
        assert [r for r in written if r.get("timestamp") == "2020-01-01 00:00:00"]


def log_names_written_under(paths) -> dict[str, list[str]]:
    """Which source files spell a log's filename, and which log.

    The names live in the declaration, so a module reading a log by name is a
    module that has grown a second place where "which events matter" is
    settled — the thing the schema-driven selection exists to prevent.
    """
    found: dict[str, list[str]] = {}
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for log in DECLARATIONS:
            if log in text:
                found.setdefault(log, []).append(path.name)
    return found


def test_no_module_under_orchestration_names_a_log(tmp_path):
    sources = sorted((REPO_ROOT / "orchestration").glob("*.py"))
    assert sources, "the scan found no sources to read"
    assert log_names_written_under(sources) == {}

    # The control: the same scan over a planted module that does name one must
    # report it, so the emptiness above is the scan looking rather than the
    # scan being unable to see.
    planted = tmp_path / "reads_a_log.py"
    planted.write_text(
        f'def read(directory):\n'
        f'    return (directory / "{OUTCOME_LOG}").read_text()\n',
        encoding="utf-8")
    assert log_names_written_under([planted]) == {OUTCOME_LOG: [planted.name]}


# --------------------------------------------------------------------------
# Committed, and blocked to every stage
# --------------------------------------------------------------------------


def test_a_stage_naming_a_path_beneath_the_history_escalates_the_run(
    target_root, harness_root,
):
    """The enforcement, against the fixture's own rules rather than the
    shipped ones.

    The prefix comes off the rule set this run executes under, so what is
    asserted is that a stage record naming a path beneath a blocked history
    directory escalates — not that this repository happens to block one.
    """
    beneath = BLOCKED_HISTORY_PREFIX + OUTCOME_LOG
    runner = Runner(target_root, [PASS], tester_record={
        "modified": [beneath], "created": [], "deleted": []})
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 2

    run_dir = run_dir_of(target_root)
    summary = (run_dir / "escalation-summary.md").read_text(encoding="utf-8")
    assert f"blocked path: {beneath}" in summary
    assert state_of(run_dir)["status"] == "escalated"
    assert runner.calls == [WRITING, VALIDATING]


def test_the_blocked_check_permits_the_same_record_without_the_history_prefix(
    target_root, harness_root,
):
    """The control for the escalation above.

    The same stage, the same record shape, the same rules — only the path
    differs — and the run completes. So the escalation is the prefix deciding
    rather than the record shape being rejected for some other reason.
    """
    runner = Runner(target_root, [PASS], tester_record={
        "modified": ["src/app.py"], "created": [], "deleted": []})
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 0
    assert state_of(run_dir_of(target_root))["status"] == "completed"


def test_the_blocked_prefix_is_the_one_the_fixture_rules_declare():
    """The predicate the escalation above rests on, exercised in both
    directions against the fixture's declaration."""
    blocked = FIXTURE_RULES["blocked_paths"]
    assert BLOCKED_HISTORY_PREFIX in blocked
    assert any((BLOCKED_HISTORY_PREFIX + OUTCOME_LOG).startswith(prefix)
               for prefix in blocked)
    assert not any("src/app.py".startswith(prefix) for prefix in blocked)


def test_this_repository_blocks_the_history_directory_to_every_stage():
    """The deployment fact, read out of the shipped rule set.

    The tests above hold the *behaviour* — a stage record naming a path
    beneath a blocked history directory escalates the run, and the same
    record without the prefix completes — against the fixture's own rules.
    This one holds the half those cannot: that this repository's own
    `rules/execution-rules.json` carries the entry, so a stage of a real run
    cannot edit the record of its own execution. It is a positive assertion
    about a shipped file: drop the entry and it fails here.

    No stage may write that file — `rules/` is blocked for every stage of
    every story — so the entry is made by hand and asserted here.
    """
    shipped = json.loads(
        (REPO_ROOT / "rules" / "execution-rules.json").read_text(
            encoding="utf-8"))
    assert BLOCKED_HISTORY_PREFIX in shipped["blocked_paths"]


def git_ignores(relative: str) -> bool:
    """Whether this repository's ignore rules match a path.

    `git check-ignore` rather than a read of `.gitignore`: the question is
    whether *any* rule matches, and the rules can live in several files.
    """
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", relative],
        capture_output=True, text=True)
    return result.returncode == 0


def test_the_history_directory_is_matched_by_no_ignore_rule():
    """The absence, with a directory that *is* ignored as its control.

    Both go through `git check-ignore`, and the run directory answers True
    while the history directory answers False — so the check is looking at
    something that can differ, and the logs are versioned.
    """
    for log in DECLARATIONS:
        assert not git_ignores(f"{BLOCKED_HISTORY_PREFIX}{log}"), log
    assert not git_ignores(BLOCKED_HISTORY_PREFIX)
    assert git_ignores(".harness/runs/story-001/state.json")
    assert git_ignores(".harness/logs/story-001.log")
