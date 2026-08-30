"""Independent validation for the Inspector: reading a scope of a target's
own code and filing a story brief for each defect found in it.

Written from the story's acceptance criteria rather than from the
implementation. The subjects are kept apart deliberately:

  * **the unit of invocation.** One scope is one agent invocation, observed
    at a fake runner that records what it was handed rather than inferred
    from the arguments that were parsed. Two named paths make two
    invocations; no arguments makes one per `source_dirs` entry plus one for
    `tests_dir`; neither key declared makes one over the tracked tree.

  * **what a scope resolves to.** What git tracks beneath it, minus every
    path under a blocked path, with the blocked set read off the execution
    rules — shown by a mirrored harness root whose rules carry a prefix this
    repository does not, and whose file then reaches neither the query nor
    the prompt.

  * **the findings as an artifact.** A file the invocation wrote, validated
    against the envelope schema, with nothing read out of what it printed. An
    invocation that printed well-formed findings and wrote nothing files
    nothing and says which of the three ways it was.

  * **every way of dropping a finding, each on its own.** Malformed, an
    unknown workflow, already filed, past the cap, lost by the queue. Each is
    driven separately, because a repair can get one right while getting
    another wrong, and each is required to name what it excluded.

  * **the identity.** Kind, category, sorted bare paths and slug — driven by
    filing one finding twice with its title rewritten and its severity
    changed, and reading the queue, and pinned at the digest a fixed finding
    is filed under, because a key already on disk is matched only by an
    identity constructed exactly as the one that produced it.

  * **the local queue as the other dedupe source.** Driven with no filed-query
    command configured, which is the configuration this repository is in: a
    landed entry drops a finding naming that source, a pending one drops it
    under a reason of its own, and a failed one drops nothing at all. The two
    sources are shown to be a union in both directions in one inspection, and
    the three already-filed reasons are shown to be counted apart and printed
    apart.

  * **the bounds.** The cap across the whole inspection, the per-invocation
    cost allowance observed at the runner, and the two refusals `l5-inspect`
    makes before anything is invoked.

  * **the shapes.** `schemas/story-brief.schema.json` and
    `schemas/inspection-findings.schema.json` are live harness artifacts and
    are the subjects of the assertions that name them: a conforming document
    is accepted and each way of malforming it is refused. The generic sweeps
    in `tests/test_artifact_schemas.py` already cover them as shipped schemas
    — that they are registered, that every required name is a declared
    property, that they constrain nothing the validator cannot check — so
    what is here is the coverage particular to these two shapes, beside the
    module whose answer they describe.

  * **the prose.** `prompts/inspector.md` and `prompts/assist.md` are live
    harness artifacts and are read as they ship.

Every absence asserted here carries a demonstration that it can fail:

  * "a blocked path reaches neither the query nor the prompt" sits beside the
    same target under a mirrored harness root whose rules omit the prefix,
    where the same file does reach both;
  * "nothing is parsed out of what the invocation printed" sits beside the
    same envelope written to the artifact, which is filed;
  * "no filed path carries a line number" sits beside the body of the same
    finding, which does carry one;
  * "the module derives no digest of its own" sits beside the same search
    over `orchestration/outbox.py`, which does derive one;
  * "the queue is reached only through `enqueue`" and "`l5-inspect` names
    only the two entry points" each sit beside a planted source the same
    scans report;
  * "the shipped prompt names no workflow" sits beside the prompt rendered
    against a harness root, which names both of that root's workflows;
  * "`prompts/assist.md` no longer asks for a story request" sits beside a
    rendering of that file with the superseded sentence restored in the
    hard-wrapped form the file carried it in, which the same whitespace-
    collapsing comparison reports;
  * "every enqueue in this suite went to a queue the test owns" sits beside
    the same predicate pointed at this repository, which it reports;
  * "a failed entry suppresses nothing" sits beside the same finding planted
    landed, which is dropped, and rests on a fixture that reads every entry it
    plants back through the queue's own reader — an entry the queue called
    poisoned would contribute no key and pass for the wrong reason;
  * "an unlistable queue costs tier one and nothing else" sits beside a
    control that the directory really refuses to be listed, which skips by
    name where the process can read it anyway;
  * "the poisoned file is noted" sits beside the same queue without it, where
    the note is absent and the index holds the same key;
  * "reading the local index does not make dedupe complete" sits beside the
    same index under a query that answers, where dedupe is reported as run;
  * "`local_index` spawns nothing and reads no query command" sits beside the
    two functions in that module that legitimately do, which the same scans
    report;
  * "nothing here reaches the hand-written briefs" sits beside a planted
    source naming that directory, which the same search reports.

Nothing here invokes a model: every invocation goes through a fake runner
this module wrote, and `test_no_inspection_in_this_module_runs_without_a_fake
_runner` holds that to a scan of this module's own source rather than to a
habit. Nothing here reaches a network or a tracker: every command driven as a
filed-query command is a file this module wrote, outside this repository,
which `fixture_command_problems` makes a checked property.
"""
from __future__ import annotations

import ast
import contextlib
import inspect as introspection
import io
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

import agent_runner
import conftest
import filed_query
import harness_config
import harness_source
import inspection
import outbox
import schema_validator
import test_no_target_stack_in_harness_source as stack_module
import workflow_selection
from agent_runner import AgentResult

REPO_ROOT = Path(inspection.__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
PROMPTS = REPO_ROOT / "prompts"
MODULE_NAME = Path(__file__).name

#: What the module says about itself, read off it so this file names no key,
#: reason, kind or artifact of its own.
KIND = inspection.KIND
SOURCE = inspection.SOURCE
TESTS = inspection.TESTS
SOURCE_DIRS_KEY = inspection.SOURCE_DIRS_KEY
TESTS_DIR_KEY = inspection.TESTS_DIR_KEY
MAX_FINDINGS_KEY = inspection.MAX_FINDINGS_KEY
MAX_COST_KEY = inspection.MAX_COST_KEY
DEFAULT_MAX_FINDINGS = inspection.DEFAULT_MAX_FINDINGS
DEFAULT_MAX_COST_USD = inspection.DEFAULT_MAX_COST_USD
DELIVERY_TOOL = inspection.DELIVERY_TOOL
INSPECTOR_PROMPT = inspection.INSPECTOR_PROMPT

ALREADY_FILED = inspection.ALREADY_FILED
ALREADY_FILED_LOCALLY = inspection.ALREADY_FILED_LOCALLY
ALREADY_QUEUED = inspection.ALREADY_QUEUED
MALFORMED = inspection.MALFORMED
UNKNOWN_WORKFLOW = inspection.UNKNOWN_WORKFLOW
PAST_THE_CAP = inspection.PAST_THE_CAP
LOST_BY_THE_QUEUE = inspection.LOST_BY_THE_QUEUE
NO_ARTIFACT = inspection.NO_ARTIFACT

#: The three states an entry can be in, read off the queue's own module so no
#: state name is spelled here beside the definition that decides them.
PENDING = outbox.PENDING
LANDED = outbox.LANDED
FAILED = outbox.FAILED

#: The two shapes, loaded as they ship. They are the subject of the schema
#: assertions below and the definition every fixture finding is built to
#: satisfy, so the fixtures derive their enum members from them rather than
#: spelling members of their own beside them.
BRIEF_SCHEMA = schema_validator.load_schema(inspection.BRIEF_SCHEMA)
FINDINGS_SCHEMA = schema_validator.load_schema(inspection.FINDINGS_SCHEMA)


def brief_enum(field_name: str) -> list:
    return BRIEF_SCHEMA["properties"][field_name]["enum"]


BRIEF_REQUIRED = tuple(BRIEF_SCHEMA["required"])
CATEGORIES = brief_enum("category")
SEVERITIES = sorted(brief_enum("severity"))
CONFIDENCES = brief_enum("confidence")
EFFORTS = brief_enum("effort")

LOWEST, MIDDLE, HIGHEST = SEVERITIES[0], SEVERITIES[1], SEVERITIES[-1]


# --------------------------------------------------------------------------
# A harness root this repository does not ship
#
# The blocked set, the workflow names and the prompt template are *inputs* to
# every assertion about what the module decides, rather than its subjects: an
# assertion about how a finding naming an undefined workflow is dropped needs
# *a* set of definitions, not the ones this repository deploys, and reading
# the deployed ones there would turn shipping a third workflow into something
# this suite reddens. So they come from a mirrored harness root built here,
# which defines the names once and lets every assertion derive them from it
# exactly as it would have derived them from the shipped tree.
#
# `schemas/` is the exception and is linked at the shipped directory, because
# the shape a finding is held to *is* the subject there: a fixture finding
# validated against a schema this module wrote would say nothing about what
# the harness accepts.
# --------------------------------------------------------------------------

#: Workflows the mirror defines and this repository does not, so a finding
#: naming one is accepted because a *definition* carries it rather than
#: because the harness happens to deploy a workflow by that name.
MIRROR_WORKFLOWS = {
    "zzz-mirrored-workflow": "the mirrored definition is the one to plan a "
                             "defect like this one under",
    "zzz-second-mirrored-workflow": "the mirrored definition is the one to "
                                    "plan the other kind of work under",
}
MIRROR_WORKFLOW = sorted(MIRROR_WORKFLOWS)[0]
OTHER_MIRROR_WORKFLOW = sorted(MIRROR_WORKFLOWS)[1]

#: A workflow no definition anywhere carries, for the finding that names one.
UNDEFINED_WORKFLOW = "zzz-no-definition-carries-this-name"

#: A blocked prefix the mirror's rules declare and this repository's do not,
#: which is what makes "a rule added there is excluded here" a fact about the
#: reading rather than about the prefixes that happen to be deployed.
MIRROR_BLOCKED = "zzz-vault/"

#: The context fields the fixture prompt renders, named here — in the fixture
#: — for the reason `conftest.BUILT_PROMPT_FIELDS` is: an assertion that some
#: value reached the prompt finds it by the label it derived from this tuple
#: rather than from what the shipped template happens to say today.
FIXTURE_PROMPT_FIELDS = (
    "scope", "scope_kind", "scope_paths", "repository_standards",
    "already_filed", "findings_path", "workflow_candidates",
    "inspection_findings_schema", "story_brief_schema",
)


def fixture_prompt() -> str:
    """The template the mirrored harness root carries for the Inspector.

    Every field on its own line with the placeholder on the line below it, so
    a multi-line value renders as a block and an assertion can find the span
    by its label.
    """
    lines = ["# a template this module wrote", ""]
    for name in FIXTURE_PROMPT_FIELDS:
        lines += [f"{name}:", f"{{{{{name}}}}}", ""]
    return "\n".join(lines)


def harness_mirror(tmp_path: Path, *, blocked=(MIRROR_BLOCKED,),
                   workflows: dict | None = None,
                   name: str = "mirrored-harness") -> Path:
    """A harness root carrying rules, workflows and a prompt this module wrote.

    `schemas/` is linked at the shipped directory for the reason stated above
    the constants: the shapes are the subject, the rest are inputs.
    """
    root = Path(tmp_path) / name
    (root / "rules").mkdir(parents=True, exist_ok=True)
    (root / "rules" / "execution-rules.json").write_text(
        json.dumps({"blocked_paths": list(blocked)}, indent=2) + "\n",
        encoding="utf-8")

    (root / "workflows").mkdir(exist_ok=True)
    for workflow, applies_when in (workflows or MIRROR_WORKFLOWS).items():
        (root / "workflows" / f"{workflow}.json").write_text(
            json.dumps({"name": workflow, "applies_when": applies_when,
                        "stages": []}, indent=2) + "\n", encoding="utf-8")

    (root / "prompts").mkdir(exist_ok=True)
    (root / "prompts" / INSPECTOR_PROMPT).write_text(fixture_prompt(),
                                                     encoding="utf-8")
    if not (root / "schemas").exists():
        (root / "schemas").symlink_to(REPO_ROOT / "schemas")
    return root


# --------------------------------------------------------------------------
# A target repository this module owns
# --------------------------------------------------------------------------

#: What the fixture target tracks. Two source directories and a tests
#: directory, so a scope can be shown to carry its own files and not its
#: neighbour's, and one file under the prefix the mirror blocks.
SOURCE_DIR = "src/"
OTHER_SOURCE_DIR = "docs/"
TESTS_DIR = "checks/"
SOURCE_FILE = f"{SOURCE_DIR}app.py"
OTHER_SOURCE_FILE = f"{OTHER_SOURCE_DIR}guide.md"
TESTS_FILE = f"{TESTS_DIR}check_app.py"
BLOCKED_FILE = f"{MIRROR_BLOCKED}secret.txt"

TRACKED_FILES = {
    SOURCE_FILE: "def add(a, b):\n    return a + b\n",
    OTHER_SOURCE_FILE: "# how the thing works\n",
    TESTS_FILE: "def check_add():\n    assert True\n",
    BLOCKED_FILE: "nothing an inspection may read\n",
}

STANDARDS_DIR = ".harness/zzz-house-rules"

#: A standards document whose name nothing in the harness could have guessed,
#: which is what makes "the standards are globbed and no filename is
#: interpreted" a fact rather than a claim.
STANDARDS_FILE = "zzz-whatever-we-called-it.md"
STANDARDS_MARKER = "zzz-standard: the marker that proves the body was read"

LOGS_DIR = ".harness/zzz-logs"


def target_repository(tmp_path: Path, *, files: dict | None = None,
                      standards: dict | None = None,
                      name: str = "inspected-target") -> Path:
    """A git repository this module built, holding what the caller asked for.

    Committed, because a scope resolves through `git ls-files` and an
    uncommitted file is not tracked.
    """
    root = Path(tmp_path) / name
    for relative, text in (TRACKED_FILES if files is None else files).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for relative, text in (standards or {}).items():
        path = root / STANDARDS_DIR / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "the tree to be inspected"],
                   cwd=root, check=True)
    return root


PERMISSION_MODE = "zzz-acceptEdits"
MODEL = "zzz-model"
GRANTED_TOOL = "Bash(zzz-search:*)"
MAX_COST_USD = 0.41


def configuration(**overrides) -> dict:
    """The target's configuration for one inspection, with departures applied."""
    config = {
        "logs_dir": LOGS_DIR,
        "standards_dir": STANDARDS_DIR,
        "permission_mode": PERMISSION_MODE,
        "model": MODEL,
        "allowed_tools": [GRANTED_TOOL],
        MAX_COST_KEY: str(MAX_COST_USD),
    }
    config.update(overrides)
    return config


# --------------------------------------------------------------------------
# The findings a fake invocation writes
# --------------------------------------------------------------------------


def brief(**overrides) -> dict:
    """One conforming finding, with the caller's departures applied."""
    finding = {
        "title": "The blocked path list is stated twice and the two disagree",
        "slug": "duplicated-blocked-path-list",
        "body": f"{SOURCE_FILE}:12 states it and {OTHER_SOURCE_FILE}:4 "
                f"states it differently",
        "category": CATEGORIES[0],
        "severity": MIDDLE,
        "confidence": CONFIDENCES[-1],
        "effort": EFFORTS[0],
        "workflow": MIRROR_WORKFLOW,
        "paths": [SOURCE_FILE],
    }
    finding.update(overrides)
    return finding


def envelope(*findings: dict) -> dict:
    return {"findings": list(findings)}


def writes(*findings: dict):
    """An invocation that writes an envelope holding `findings`."""
    def act(artifact: Path, index: int) -> None:
        artifact.write_text(json.dumps(envelope(*findings)), encoding="utf-8")
    return act


def writes_per_invocation(*per_call: tuple):
    """An invocation that writes the findings for its own position."""
    def act(artifact: Path, index: int) -> None:
        chosen = per_call[index] if index < len(per_call) else ()
        artifact.write_text(json.dumps(envelope(*chosen)), encoding="utf-8")
    return act


def writes_text(text: str):
    def act(artifact: Path, index: int) -> None:
        artifact.write_text(text, encoding="utf-8")
    return act


def writes_nothing(artifact: Path, index: int) -> None:
    """An invocation that wrote no file, whatever it said on the way there."""


@dataclass
class Invocation:
    """One thing the fake runner was handed, recorded as it was handed it."""

    prompt: str
    stage: str
    cwd: Path
    log_path: Path
    permission_mode: str
    model: object
    allowed_tools: object
    max_budget_usd: object


class FakeRunner:
    """Stands in for `agent_runner.run_agent`, recording every invocation.

    It reaches no model, no network and no tracker: it records what it was
    given, performs whatever the caller asked this invocation to do to the
    findings artifact, and answers.
    """

    def __init__(self, artifact: Path, act=writes_nothing, prints: str = ""):
        self.artifact = Path(artifact)
        self.act = act
        self.prints = prints
        self.invocations: list[Invocation] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode,
                 model, allowed_tools=None, max_budget_usd=None,
                 suite_command=None):
        index = len(self.invocations)
        self.invocations.append(Invocation(
            prompt=prompt, stage=stage, cwd=Path(cwd),
            log_path=Path(log_path), permission_mode=permission_mode,
            model=model, allowed_tools=allowed_tools,
            max_budget_usd=max_budget_usd))
        self.act(self.artifact, index)
        return AgentResult(ok=True, result_text=self.prints)


# --------------------------------------------------------------------------
# Driving one inspection
# --------------------------------------------------------------------------


@dataclass
class Inspected:
    """One inspection, and everything an assertion reads off it."""

    report: object
    runner: FakeRunner
    target: Path
    config: dict
    harness: Path

    @property
    def queue(self) -> Path:
        return outbox.queue_dir(self.target)

    @property
    def entries(self) -> list[dict]:
        return [json.loads(path.read_text(encoding="utf-8"))
                for path in outbox.entry_files(self.queue)]

    @property
    def filed_slugs(self) -> list[str]:
        return [one.slug for one in self.report.filed]

    @property
    def invocations(self) -> list[Invocation]:
        return self.runner.invocations

    @property
    def scope_labels(self) -> list[str]:
        return [scope.label for scope in self.report.scopes]

    @property
    def scope_kinds(self) -> list[str]:
        return [scope.kind for scope in self.report.scopes]

    def prompt(self, index: int = 0) -> str:
        return self.runner.invocations[index].prompt

    def dropped(self, reason: str) -> tuple:
        return self.report.dropped_for(reason)

    def detail(self, reason: str) -> str:
        return " ".join(drop.detail for drop in self.dropped(reason))


def inspecting(tmp_path: Path, *, act=writes_nothing, prints: str = "",
               arguments=(), dry_run: bool = False, config: dict | None = None,
               target: Path | None = None, harness: Path | None = None,
               files: dict | None = None, standards: dict | None = None,
               name: str = "inspected-target") -> Inspected:
    """One whole inspection, against a fake runner and a target this test owns.

    The runner is passed explicitly at every call site in this module rather
    than defaulted, which the scan at the foot of this file holds the module
    to: a call that fell back to the default would invoke a model.
    """
    harness = harness or harness_mirror(tmp_path)
    target = target or target_repository(tmp_path, files=files,
                                         standards=standards, name=name)
    config = configuration() if config is None else config
    artifact, _ = inspection.findings_paths(target, config)
    runner = FakeRunner(artifact, act=act, prints=prints)
    report = inspection.inspect(target, config, harness, arguments=arguments,
                                dry_run=dry_run, runner=runner)
    return Inspected(report=report, runner=runner, target=target,
                     config=config, harness=harness)


# --------------------------------------------------------------------------
# Every filed-query command this module drives is a file it wrote
# --------------------------------------------------------------------------


def fixture_command_problems(path: Path) -> list[str]:
    """What would stop `path` from being a command this module wrote itself.

    A predicate rather than a pair of inline assertions, so it can be *shown*
    reporting a violation rather than only observed to be silent. A path that
    already exists is one somebody else wrote, and a path inside this
    repository is a shipped artifact.
    """
    problems = []
    if path.exists():
        problems.append(f"{path} already exists, so this module did not write it")
    if REPO_ROOT in path.parents:
        problems.append(f"{path} is inside {REPO_ROOT}, so it is a shipped file "
                        f"rather than one this module wrote")
    return problems


def fixture_command(tmp_path: Path, name: str, body: str) -> Path:
    directory = Path(tmp_path) / "fake-commands"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    problems = fixture_command_problems(path)
    assert problems == [], problems
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_the_fixture_check_reports_a_command_this_module_did_not_write():
    """The control for the property every fixture command rests on.

    Silence from the check means nothing until it has been shown to speak, so
    it is pointed at a shipped entry point — which exists, and lives inside
    this repository — and must report both.
    """
    problems = fixture_command_problems(SCRIPTS / "l5-inspect")
    assert len(problems) == 2, problems
    assert any("already exists" in problem for problem in problems)
    assert any(str(REPO_ROOT) in problem for problem in problems)


def answering_query(tmp_path: Path, *items: dict, name: str = "answers.sh"
                    ) -> str:
    """A command that answers with `items` and exits zero."""
    document = Path(tmp_path) / "fake-commands" / f"{name}.document"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(json.dumps({"items": list(items)}), encoding="utf-8")
    return str(fixture_command(tmp_path, name, f'cat "{document}"\n'))


def recording_query(tmp_path: Path, transcript: Path, *items: dict) -> str:
    """A command that writes what it read on stdin, then answers."""
    document = Path(tmp_path) / "fake-commands" / "recorded.document"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(json.dumps({"items": list(items)}), encoding="utf-8")
    return str(fixture_command(tmp_path, "records-the-question.sh",
                               f'cat > "{transcript}"\ncat "{document}"\n'))


def failing_query(tmp_path: Path, code: int = 3) -> str:
    return str(fixture_command(
        tmp_path, "cannot-answer.sh",
        f'echo "the tracker refused the search" >&2\nexit {code}\n'))


# --------------------------------------------------------------------------
# One scope is one agent invocation
# --------------------------------------------------------------------------


def test_two_named_paths_make_two_invocations(tmp_path):
    """Observed at the runner rather than inferred from the arguments.

    Each invocation is handed its own scope's files and not its neighbour's,
    which is what makes them two scopes rather than one invocation made twice.
    """
    found = inspecting(tmp_path, arguments=(SOURCE_DIR, OTHER_SOURCE_DIR))

    assert len(found.invocations) == 2
    assert found.scope_labels == [SOURCE_DIR, OTHER_SOURCE_DIR]
    assert found.report.invocations == 2

    assert SOURCE_FILE in found.prompt(0)
    assert OTHER_SOURCE_FILE not in found.prompt(0)
    assert OTHER_SOURCE_FILE in found.prompt(1)
    assert SOURCE_FILE not in found.prompt(1)


def test_no_arguments_makes_one_invocation_per_source_dir_plus_one_for_tests(
        tmp_path):
    found = inspecting(tmp_path, config=configuration(**{
        SOURCE_DIRS_KEY: [SOURCE_DIR, OTHER_SOURCE_DIR],
        TESTS_DIR_KEY: TESTS_DIR,
    }))

    assert len(found.invocations) == 3
    assert found.scope_labels == [SOURCE_DIR, OTHER_SOURCE_DIR, TESTS_DIR]
    assert found.scope_kinds == [SOURCE, SOURCE, TESTS]
    assert TESTS_FILE in found.prompt(2)
    assert TESTS_FILE not in found.prompt(0)


def test_with_no_source_dirs_the_tracked_tree_is_the_scope_and_tests_is_its_own(
        tmp_path):
    """The everything-scope keeps the tests directory out of itself.

    Not by merging the two — the tests directory is a scope of its own, with
    its own invocation and its own kind — but by leaving it to that scope, so
    a target that declares only `tests_dir` still has its tests inspected.
    """
    found = inspecting(tmp_path,
                       config=configuration(**{TESTS_DIR_KEY: TESTS_DIR}))

    assert len(found.invocations) == 2
    assert found.scope_kinds == [SOURCE, TESTS]

    whole_tree = found.prompt(0)
    assert SOURCE_FILE in whole_tree
    assert OTHER_SOURCE_FILE in whole_tree
    assert TESTS_FILE not in whole_tree
    assert TESTS_FILE in found.prompt(1)


def test_a_target_declaring_neither_key_is_inspected_over_its_tracked_tree(
        tmp_path):
    """Inspected rather than refused, which is the whole of the claim."""
    found = inspecting(tmp_path, config=configuration())

    assert len(found.invocations) == 1
    assert found.scope_kinds == [SOURCE]
    for tracked in (SOURCE_FILE, OTHER_SOURCE_FILE, TESTS_FILE):
        assert tracked in found.prompt(0), tracked


def test_source_and_tests_are_a_union_and_not_a_merge(tmp_path):
    """Three things at once, because they are one claim.

    `tests_dir` is not folded into `source_dirs`; the Inspector is told which
    of the two halves it is looking at; and what the workflow's create
    restriction resolves to is untouched by declaring source directories,
    because that restriction reads `tests_dir` and nothing else.
    """
    without = configuration(**{TESTS_DIR_KEY: TESTS_DIR})
    with_source = configuration(**{TESTS_DIR_KEY: TESTS_DIR,
                                   SOURCE_DIRS_KEY: [SOURCE_DIR]})

    scopes = inspection.scopes((), with_source)
    assert [scope.path for scope in scopes] == [SOURCE_DIR, TESTS_DIR]
    assert [scope.kind for scope in scopes] == [SOURCE, TESTS]
    assert with_source[SOURCE_DIRS_KEY] == [SOURCE_DIR]

    found = inspecting(tmp_path, config=with_source)
    assert f"{TESTS}\n" in found.prompt(1), found.prompt(1)
    assert f"{SOURCE}\n" in found.prompt(0), found.prompt(0)

    assert harness_config.workflow_token_values(with_source) == \
        harness_config.workflow_token_values(without)


def test_a_named_scope_beneath_the_tests_directory_is_a_tests_scope(tmp_path):
    """What the Inspector is told does not depend on how the developer got
    there: naming the tests directory on the command line is the tests half."""
    found = inspecting(tmp_path, arguments=(TESTS_DIR,),
                       config=configuration(**{TESTS_DIR_KEY: TESTS_DIR}))
    assert found.scope_kinds == [TESTS]


# --------------------------------------------------------------------------
# What a scope resolves to
# --------------------------------------------------------------------------


def question_paths(transcript: Path) -> list[str]:
    """The paths one query was asked about, read off what the command read."""
    read = transcript.read_text(encoding="utf-8")
    question, consumed = json.JSONDecoder().raw_decode(read)
    assert read[consumed:].strip() == "", read
    return question["paths"]


def test_a_path_under_a_blocked_path_reaches_neither_the_query_nor_the_prompt(
        tmp_path):
    """The blocked set is read off the execution rules rather than restated.

    The prefix excluded here is one the mirrored harness root's rules declare
    and this repository's do not, so what is being observed is a rule being
    read rather than a list that happens to agree. The control below runs the
    same target against a mirror whose rules omit the prefix, where the same
    file does reach both.
    """
    transcript = tmp_path / "the-question"
    found = inspecting(
        tmp_path,
        config=configuration(
            **{filed_query.COMMAND_KEY: recording_query(tmp_path, transcript)}))

    assert MIRROR_BLOCKED in inspection.blocked_prefixes(found.harness)
    asked = question_paths(transcript)
    assert BLOCKED_FILE not in asked
    assert SOURCE_FILE in asked
    assert BLOCKED_FILE not in found.prompt(0)
    assert SOURCE_FILE in found.prompt(0)


def test_the_same_file_reaches_both_when_no_rule_blocks_it(tmp_path):
    """The control for the absence above.

    Nothing changes but the rules file, which is what makes the exclusion a
    property of the rule rather than of the path's name — and what makes a
    rule added there excluded here with no edit to the module.
    """
    transcript = tmp_path / "the-question"
    found = inspecting(
        tmp_path,
        harness=harness_mirror(tmp_path, blocked=(), name="unblocked-harness"),
        config=configuration(
            **{filed_query.COMMAND_KEY: recording_query(tmp_path, transcript)}))

    assert inspection.blocked_prefixes(found.harness) == ()
    assert BLOCKED_FILE in question_paths(transcript)
    assert BLOCKED_FILE in found.prompt(0)


def test_a_scope_carries_what_git_tracks_beneath_it_and_nothing_else(tmp_path):
    """An untracked file is not a path a scope carries."""
    target = target_repository(tmp_path)
    (target / SOURCE_DIR / "never-added.py").write_text("pass\n",
                                                        encoding="utf-8")
    blocked = inspection.blocked_prefixes(harness_mirror(tmp_path))
    paths = inspection.scope_paths(target,
                                   inspection.Scope(path=SOURCE_DIR,
                                                    kind=SOURCE),
                                   blocked)
    assert paths == (SOURCE_FILE,)


def test_a_scope_prefix_does_not_match_a_sibling_it_merely_prefixes(tmp_path):
    """`src` names `src/` and not `src-notes.py`."""
    files = {SOURCE_FILE: "pass\n", "src-notes.py": "# beside it\n"}
    target = target_repository(tmp_path, files=files)
    paths = inspection.scope_paths(
        target, inspection.Scope(path=SOURCE_DIR.rstrip("/"), kind=SOURCE), ())
    assert paths == (SOURCE_FILE,)


# --------------------------------------------------------------------------
# The findings are an artifact, not parsed output
# --------------------------------------------------------------------------


def test_an_invocation_that_printed_findings_and_wrote_no_file_files_nothing(
        tmp_path):
    """Nothing is read out of what the invocation printed.

    The runner prints a well-formed envelope carrying a conforming finding, so
    an inspection that dug it out of the output would file it. That it files
    nothing instead, and says which of the three ways it was, is what makes
    the findings an artifact rather than a format enforced by nothing. The
    control is every other test here, where the same envelope written to the
    artifact is filed.
    """
    found = inspecting(tmp_path, act=writes_nothing,
                       prints=json.dumps(envelope(brief())))

    assert found.report.filed == ()
    assert found.entries == []
    detail = found.detail(NO_ARTIFACT)
    assert "wrote no findings file" in detail, detail
    assert brief()["slug"] not in detail


def test_a_findings_file_that_is_not_json_yields_no_findings_and_says_so(
        tmp_path):
    found = inspecting(tmp_path, act=writes_text("the findings, in prose"))
    assert found.report.filed == ()
    assert "not JSON" in found.detail(NO_ARTIFACT)


def test_a_findings_file_that_misses_the_envelope_schema_says_that(tmp_path):
    found = inspecting(tmp_path,
                       act=writes_text(json.dumps({"defects": [brief()]})))
    assert found.report.filed == ()
    assert inspection.FINDINGS_SCHEMA in found.detail(NO_ARTIFACT)


def test_each_way_of_having_no_findings_says_which_way_it_was(tmp_path):
    """"No findings" is not the whole answer; which way it was is.

    The reasons are required to differ from one another, so a repair that
    funnelled two of them into one string is reported here even though every
    assertion about them individually would still hold.
    """
    ways = {
        "wrote nothing": inspecting(tmp_path / "nothing", act=writes_nothing),
        "not json": inspecting(tmp_path / "prose",
                               act=writes_text("not a document")),
        "wrong shape": inspecting(tmp_path / "shape",
                                  act=writes_text(json.dumps({"items": []}))),
    }
    details = [found.detail(NO_ARTIFACT) for found in ways.values()]
    assert all(details), ways
    assert len(set(details)) == len(details), details


def test_an_envelope_carrying_no_findings_is_an_answer_rather_than_a_failure(
        tmp_path):
    found = inspecting(tmp_path, act=writes())
    assert found.report.filed == ()
    assert found.report.dropped == ()


def test_an_artifact_left_by_an_earlier_invocation_is_not_read_as_this_ones(
        tmp_path):
    """Removed before the invocation, so a stale file cannot become findings.

    The runner writes nothing, and a file carrying a conforming finding is
    sitting where the invocation would have written one. The control is the
    same finding written *by* the invocation, which is filed.
    """
    target = target_repository(tmp_path)
    config = configuration()
    artifact, _ = inspection.findings_paths(target, config)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(envelope(brief())), encoding="utf-8")

    found = inspecting(tmp_path, target=target, config=config,
                       act=writes_nothing)
    assert found.report.filed == ()
    assert "wrote no findings file" in found.detail(NO_ARTIFACT)
    assert not artifact.exists()

    filed = inspecting(tmp_path, target=target, config=config,
                       act=writes(brief()))
    assert filed.filed_slugs == [brief()["slug"]]


# --------------------------------------------------------------------------
# One malformed finding costs only itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize("missing", sorted(BRIEF_REQUIRED))
def test_a_finding_missing_a_required_field_is_dropped_and_named(missing,
                                                                 tmp_path):
    """Named with the field that failed, and the finding beside it is filed.

    Parametrized over every required field rather than over one of them,
    because a validator that checked eight of nine would pass a sampled
    assertion.
    """
    conforming = brief(slug="the-one-that-conforms")
    broken = {key: value for key, value in brief().items() if key != missing}

    found = inspecting(tmp_path, act=writes(conforming, broken),
                       name=f"target-missing-{missing}")

    assert found.filed_slugs == [conforming["slug"]]
    detail = found.detail(MALFORMED)
    assert missing in detail, detail
    assert len(found.entries) == 1


def test_a_finding_whose_field_is_out_of_its_enum_is_dropped_and_named(
        tmp_path):
    found = inspecting(tmp_path, act=writes(
        brief(slug="the-one-that-conforms"),
        brief(slug="out-of-the-enum", category="zzz-not-a-category")))

    assert found.filed_slugs == ["the-one-that-conforms"]
    assert "category" in found.detail(MALFORMED)


def test_a_finding_naming_an_undefined_workflow_is_dropped_and_named(tmp_path):
    """And the acceptable names come from the definitions the harness holds.

    The finding beside it names the mirror's *second* workflow — a name this
    repository does not ship — and is filed, so what makes a name acceptable
    is a definition carrying it rather than anything written in the module or
    in the schema.
    """
    found = inspecting(tmp_path, act=writes(
        brief(slug="names-the-second-definition", workflow=OTHER_MIRROR_WORKFLOW),
        brief(slug="names-nothing-defined", workflow=UNDEFINED_WORKFLOW)))

    assert found.filed_slugs == ["names-the-second-definition"]
    detail = found.detail(UNKNOWN_WORKFLOW)
    assert UNDEFINED_WORKFLOW in detail, detail
    for defined in MIRROR_WORKFLOWS:
        assert defined in detail, detail
    assert UNDEFINED_WORKFLOW not in json.dumps(BRIEF_SCHEMA)


# --------------------------------------------------------------------------
# What a brief is filed under
# --------------------------------------------------------------------------


def test_no_filed_path_carries_a_line_number(tmp_path):
    """Read off the entry the queue holds, for a finding that cited a line.

    The reason is mechanical: the reference sync command writes one searchable
    marker per path a payload carries and the query command searches for the
    marker of a bare path, so a path filed with a line number is invisible to
    every scoped query that follows. The control is the same finding's body,
    which does carry the line — the citation is not lost, it is where a reader
    looks for it.
    """
    cited = brief(paths=[f"{SOURCE_FILE}:12", f"{SOURCE_FILE}:12:7",
                         OTHER_SOURCE_FILE],
                  body=f"{SOURCE_FILE}:12 is where it is")
    found = inspecting(tmp_path, act=writes(cited))

    entry = found.entries[0]
    assert entry["payload"]["paths"] == sorted([SOURCE_FILE, OTHER_SOURCE_FILE])
    assert entry["identity"]["paths"] == sorted([SOURCE_FILE, OTHER_SOURCE_FILE])
    for path in entry["payload"]["paths"] + entry["identity"]["paths"]:
        assert ":" not in path, path
    assert f"{SOURCE_FILE}:12" in entry["payload"]["body"]


def test_a_bare_path_is_left_exactly_as_it_is(tmp_path):
    """The control for the stripping above: it takes off what is there and
    invents nothing, so a path with no line number is untouched."""
    assert inspection.bare_path(SOURCE_FILE) == SOURCE_FILE
    assert inspection.bare_path(f"{SOURCE_FILE}:12") == SOURCE_FILE
    assert inspection.bare_path(f"{SOURCE_FILE}:12:7") == SOURCE_FILE


def test_the_identity_carries_the_stable_parts_and_none_of_the_prose():
    """Kind, category, sorted paths and slug, and nothing else at all."""
    finding = brief()
    identity = inspection.identity(finding)

    assert set(identity) == {"kind", "category", "paths", "slug"}
    assert identity["kind"] == KIND
    rendered = json.dumps(identity)
    for prose in (finding["title"], finding["body"], finding["confidence"]):
        assert prose not in rendered, prose
    assert str(finding["severity"]) not in rendered


def test_the_paths_in_an_identity_are_sorted_and_deduplicated():
    """An identity that depended on the order a model happened to write two
    paths in would file one defect twice."""
    one = inspection.identity(brief(paths=[OTHER_SOURCE_FILE, SOURCE_FILE]))
    other = inspection.identity(brief(paths=[SOURCE_FILE, OTHER_SOURCE_FILE,
                                             f"{SOURCE_FILE}:3"]))
    assert one == other
    assert one["paths"] == sorted([SOURCE_FILE, OTHER_SOURCE_FILE])


def test_the_same_finding_rewritten_and_rerated_files_one_entry_under_one_key(
        tmp_path):
    """Two inspections of one defect, and one entry on disk.

    The second inspection's finding differs in exactly the parts a model
    rephrases between runs — the title and the severity — and is recognised as
    the same finding under the same key.

    Since story-095 read the local queue as a dedupe index, that recognition is
    where the identity's stability is observed: the first inspection's entry is
    still pending, so the second drops the rewrite as already queued rather
    than refiling it, and the queue still holds exactly one entry under the key
    the first filed it as. A drifting identity would produce a second key, a
    second entry and no drop, so the claim is unchanged and only what makes it
    visible moved.
    """
    target = target_repository(tmp_path)
    config = configuration()
    first = inspecting(tmp_path, target=target, config=config,
                       act=writes(brief()))
    rewritten = brief(title="A quite different sentence about the same defect",
                      severity=HIGHEST, confidence=CONFIDENCES[-1])
    second = inspecting(tmp_path, target=target, config=config,
                        act=writes(rewritten))

    assert len(second.entries) == 1
    assert second.entries[0]["key"] == first.report.filed[0].key
    assert second.report.filed == ()
    assert len(second.report.dropped_for(inspection.ALREADY_QUEUED)) == 1


def test_the_key_an_entry_is_filed_under_is_the_outboxs_own_derivation(
        tmp_path):
    found = inspecting(tmp_path, act=writes(brief()))
    expected = outbox.identity_key(inspection.identity(brief()))
    assert found.report.filed[0].key == expected
    assert found.entries[0]["key"] == expected


def test_everything_a_brief_is_filed_with_renders_as_json(tmp_path):
    """The outbox coerces nothing, so a value it cannot render is an item it
    drops silently by design.

    The control is beneath: the same rendering over a payload carrying a
    `Path`, which raises — so the success above is a fact about what this
    producer constructs rather than about a renderer that accepts anything.
    """
    scope = inspection.Scope(path=SOURCE_DIR, kind=SOURCE)
    payload = inspection.payload(brief(), scope)
    assert json.loads(json.dumps(payload)) == payload
    identity = inspection.identity(brief())
    assert json.loads(json.dumps(identity)) == identity

    with pytest.raises(TypeError):
        json.dumps({**payload, "paths": [Path(SOURCE_FILE)]})

    found = inspecting(tmp_path, act=writes(brief()))
    assert found.entries[0]["payload"]["kind"] == KIND
    assert found.entries[0]["payload"]["scope"] == ""


# --------------------------------------------------------------------------
# The queue is reached one way, and a loss is not a filing
# --------------------------------------------------------------------------


def test_an_enqueue_that_answered_with_the_empty_string_is_reported_as_a_loss(
        tmp_path):
    """The queue's own contract: the empty string is the item having been lost.

    The loss is forced by leaving a *file* where the queue directory belongs,
    so `enqueue` cannot write and answers with nothing. Nothing is named after
    it and nothing is reported as filed; the report says an item was dropped.
    The control is the same inspection against a writable queue, below.
    """
    target = target_repository(tmp_path)
    queue = outbox.queue_dir(target)
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text("a file standing where the queue directory belongs\n",
                     encoding="utf-8")

    found = inspecting(tmp_path, target=target, act=writes(brief()))

    assert found.report.filed == ()
    detail = found.detail(LOST_BY_THE_QUEUE)
    assert brief()["slug"] in detail, detail
    assert found.dropped(LOST_BY_THE_QUEUE)[0].severity == brief()["severity"]


def test_the_same_finding_is_filed_when_the_queue_can_be_written(tmp_path):
    """The control for the loss above."""
    found = inspecting(tmp_path, act=writes(brief()))
    assert found.filed_slugs == [brief()["slug"]]
    assert found.dropped(LOST_BY_THE_QUEUE) == ()


def outbox_attributes(source: str) -> set[str]:
    """Every attribute of the `outbox` module a source names.

    An AST read rather than a substring search, so a mention in a docstring is
    not read as a call — which is the whole difference between a module that
    documents the queue and one that reaches into it.
    """
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "outbox":
            found.add(node.attr)
    return found


def test_the_module_reaches_the_queue_only_through_enqueue_and_hashes_nothing():
    """Two halves of one claim: the outbox computes the key.

    The module derives no digest of its own, and the only queue operations it
    names are the key derivation, the write, where the queue lives, and — since
    story-095 read the queue as a dedupe index — the two reads `l5-status`
    already makes plus the two states it sorts entries by. Nothing here builds
    a transport or spawns a subprocess for the queue. The controls are below.
    """
    source = (REPO_ROOT / "orchestration" / "inspection.py").read_text(
        encoding="utf-8")
    assert outbox_attributes(source) == {
        "identity_key", "enqueue", "queue_dir",
        "entry_files", "read_entry", "LANDED", "PENDING",
    }
    assert "hashlib" not in source
    assert "sha256" not in source


def test_the_same_searches_report_a_module_that_hashes_and_a_second_queue_call():
    """The controls for the two absences above.

    The digest search is pointed at the module that legitimately derives one,
    and the attribute scan at a source that names a second queue operation, so
    neither absence above is a search that has stopped seeing anything.
    """
    outbox_source = (REPO_ROOT / "orchestration" / "outbox.py").read_text(
        encoding="utf-8")
    assert "hashlib" in outbox_source
    assert "sha256" in outbox_source

    planted = ("import outbox\n\n\n"
               "def drain(queue):\n"
               "    return outbox.sync(queue)\n")
    assert outbox_attributes(planted) == {"sync"}


# --------------------------------------------------------------------------
# Dedupe: what is already filed is asked for before the model is
# --------------------------------------------------------------------------


def test_the_query_is_asked_about_the_scopes_paths_and_its_answer_is_injected(
        tmp_path):
    """Asked before the invocation, and injected into the prompt as data.

    The question is observed at the command — the fake writes what it read on
    stdin — and the answer is observed in the rendered prompt, so neither is
    inferred from the call.
    """
    transcript = tmp_path / "the-question"
    item = {"key": "zzz-already-filed-1",
            "title": "something a tracker already carries",
            "summary": "reported once before", "paths": [SOURCE_FILE]}
    found = inspecting(tmp_path, act=writes(brief()), config=configuration(
        **{filed_query.COMMAND_KEY: recording_query(tmp_path, transcript,
                                                    item)}))

    assert question_paths(transcript) == list(
        inspection.scope_paths(found.target,
                               inspection.Scope(path="", kind=SOURCE),
                               inspection.blocked_prefixes(found.harness)))
    prompt = found.prompt(0)
    assert item["key"] in prompt
    assert item["title"] in prompt
    assert found.report.dedupe[0].ran is True
    assert found.report.dedupe[0].known == 1


def test_a_finding_the_query_already_reported_is_dropped_without_asking_again(
        tmp_path):
    """The drop is deterministic, on the key, and costs no second invocation.

    The finding beside it is filed, so the drop is about the match rather than
    about the inspection having stopped filing.
    """
    known = brief(slug="the-one-already-filed")
    fresh = brief(slug="the-one-nobody-has-filed")
    item = {"key": outbox.identity_key(inspection.identity(known)),
            "title": "what the tracker already carries"}

    found = inspecting(tmp_path, act=writes(known, fresh), config=configuration(
        **{filed_query.COMMAND_KEY: answering_query(tmp_path, item)}))

    assert found.filed_slugs == [fresh["slug"]]
    assert known["slug"] in found.detail(ALREADY_FILED)
    assert len(found.invocations) == 1
    assert [entry["payload"]["slug"] for entry in found.entries] == \
        [fresh["slug"]]


def test_a_query_that_could_not_answer_is_reported_as_dedupe_not_having_run(
        tmp_path):
    """And the findings are filed anyway.

    Losing the check is not a reason to lose the findings, so both halves are
    asserted together: the report says dedupe did not run for that scope, in
    those terms, and the queue holds what was found.
    """
    found = inspecting(tmp_path, act=writes(brief()), config=configuration(
        **{filed_query.COMMAND_KEY: failing_query(tmp_path)}))

    assert found.report.dedupe_ran is False
    dedupe = found.report.dedupe[0]
    assert dedupe.ran is False
    assert dedupe.reason
    assert found.filed_slugs == [brief()["slug"]]
    assert len(found.entries) == 1

    printed = report_text(found)
    assert "dedupe did NOT run" in printed, printed


def test_a_query_that_answered_is_reported_as_having_run(tmp_path):
    """The control for the report above: the same wording distinguishes the
    two, so "did not run" is a fact about the query rather than what is always
    printed."""
    found = inspecting(tmp_path, act=writes(brief()), config=configuration(
        **{filed_query.COMMAND_KEY: answering_query(tmp_path)}))
    printed = report_text(found)
    assert "dedupe did NOT run" not in printed, printed
    assert "dedupe ran" in printed, printed


# --------------------------------------------------------------------------
# Dedupe: the local queue is the other source, and needs no configuration
#
# Every inspection below is driven with no filed-query command unless it names
# one, which is the configuration this repository is in: the tier being
# validated here is the one that answers anyway. Nothing here reaches a
# network or a tracker — an entry is written into a queue beneath the test's
# own target through the queue's own writer, and moved to the state the
# assertion is about.
# --------------------------------------------------------------------------


def planted(target: Path, finding: dict, state: str) -> str:
    """One entry in a target's own queue, in `state`, under `finding`'s key.

    Written through the queue's own writer and then moved into the state the
    assertion is about, in the shape a sync leaves each one: a landed entry
    records the provider's reference and drops the payload it no longer needs,
    a failed one records what refused it and the attempt it cost.

    It is read back through `outbox.read_entry` before it is returned, and the
    state it reads back as is asserted here. That is the control every
    assertion below rests on: an entry this fixture wrote in a shape the queue
    calls poisoned would contribute no key at all, and then "a failed entry
    suppresses nothing" would pass for the wrong reason.
    """
    queue = outbox.queue_dir(target)
    key = outbox.enqueue(queue, {"slug": finding["slug"]},
                         inspection.identity(finding))
    assert key, "the fixture's own enqueue lost the entry it meant to plant"

    path = outbox.entry_path(queue, key)
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["state"] = state
    if state == LANDED:
        entry.pop("payload", None)
        entry["reference"] = f"zzz-tracker#{finding['slug']}"
    elif state == FAILED:
        entry["attempts"] = 1
        entry["last_error"] = "the tracker refused it on its own terms"
    path.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")

    written, problems = outbox.read_entry(path)
    assert problems == [], problems
    assert written["state"] == state
    return key


def filed_payload_slugs(found: Inspected) -> list[str]:
    """The slugs the queue holds a payload for, sorted.

    A landed entry has dropped its payload, so the entries a test planted are
    not counted among the ones an inspection wrote.
    """
    return sorted(entry["payload"]["slug"] for entry in found.entries
                  if "payload" in entry)


def test_a_landed_entry_in_the_local_queue_drops_a_finding_and_says_which_source(
        tmp_path):
    """Tier one, with no filed-query command configured at all.

    That configuration is asserted rather than assumed, because it is the
    point: this repository configures no query, and the tier being driven here
    is the one that answers regardless. The finding beside the match is filed,
    so the drop is about the match rather than about an inspection that stopped
    filing, and the reason names the local queue rather than the query — which
    did not run.
    """
    config = configuration()
    assert filed_query.COMMAND_KEY not in config

    target = target_repository(tmp_path)
    known = brief(slug="the-one-this-harness-already-filed")
    fresh = brief(slug="the-one-nobody-has-filed")
    key = planted(target, known, LANDED)

    found = inspecting(tmp_path, target=target, config=config,
                       act=writes(known, fresh))

    assert found.filed_slugs == [fresh["slug"]]
    assert known["slug"] in found.detail(ALREADY_FILED_LOCALLY)
    assert found.dropped(ALREADY_FILED) == ()
    assert found.report.local_index.landed == frozenset({key})
    assert filed_payload_slugs(found) == [fresh["slug"]]
    assert found.report.dedupe_ran is False


def test_a_failed_entry_suppresses_nothing_and_the_finding_is_filed_again(
        tmp_path):
    """The rule a tempting simpler one gets wrong.

    A failed entry is terminal: no later sync files it, so the finding it
    carries reached nobody, and dropping on it would lose that finding
    permanently with no signal. The finding is filed again and replaces the
    failed entry at the same key with a pending one — another chance rather
    than a duplicate, because the key is derived from the identity alone.

    The control is below in the same test: the same finding, against the same
    fixture, planted landed instead — which *is* dropped. So "not dropped" here
    is a fact about the state the entry is in rather than about a queue nothing
    read.
    """
    target = target_repository(tmp_path)
    finding = brief(slug="the-one-that-reached-nobody")
    key = planted(target, finding, FAILED)

    found = inspecting(tmp_path, target=target, act=writes(finding))

    assert found.filed_slugs == [finding["slug"]]
    assert found.report.dropped == ()
    assert [entry["key"] for entry in found.entries] == [key]
    assert found.entries[0]["state"] == PENDING
    assert found.entries[0]["payload"]["slug"] == finding["slug"]
    assert found.report.local_index.landed == frozenset()
    assert found.report.local_index.queued == frozenset()

    landed = target_repository(tmp_path, name="the-same-finding-landed")
    planted(landed, finding, LANDED)
    control = inspecting(tmp_path, target=landed, act=writes(finding))
    assert control.report.filed == ()
    assert finding["slug"] in control.detail(ALREADY_FILED_LOCALLY)


def test_a_pending_entry_is_dropped_as_queued_rather_than_as_already_filed(
        tmp_path):
    """Two states, two reasons, driven side by side so the two can be compared.

    A pending entry is written down here and seen by no tracker, so it is not
    the evidence a landed one is: reporting it as filed would claim the harness
    filed something nothing external has seen. It is dropped, the report does
    not count it as newly filed, and the reason differs from the one the same
    finding produces when its entry has landed.
    """
    assert len({ALREADY_FILED, ALREADY_FILED_LOCALLY, ALREADY_QUEUED}) == 3

    finding = brief(slug="the-one-waiting-in-the-queue")
    queued_target = target_repository(tmp_path, name="holds-it-pending")
    key = planted(queued_target, finding, PENDING)
    landed_target = target_repository(tmp_path, name="holds-it-landed")
    planted(landed_target, finding, LANDED)

    queued = inspecting(tmp_path, target=queued_target, act=writes(finding))
    landed = inspecting(tmp_path, target=landed_target, act=writes(finding))

    assert queued.report.filed == ()
    assert len(queued.dropped(ALREADY_QUEUED)) == 1
    assert finding["slug"] in queued.detail(ALREADY_QUEUED)
    assert queued.dropped(ALREADY_FILED_LOCALLY) == ()
    assert queued.report.local_index.queued == frozenset({key})
    assert [entry["key"] for entry in queued.entries] == [key]
    assert queued.entries[0]["state"] == PENDING

    assert len(landed.dropped(ALREADY_FILED_LOCALLY)) == 1
    assert landed.dropped(ALREADY_QUEUED) == ()
    assert queued.dropped(ALREADY_QUEUED)[0].reason != \
        landed.dropped(ALREADY_FILED_LOCALLY)[0].reason


def test_the_two_sources_are_a_union_and_each_drop_names_the_source_that_knew(
        tmp_path):
    """Both directions in one inspection, with a third finding neither knows.

    One finding the query reported and the queue does not hold, one the queue
    holds landed and the query did not report, and one nobody has seen. Each
    slug is asserted present in its own source's drops and absent from the
    other's, so neither absence is a search that has stopped seeing anything:
    the same comparison finds each slug once.
    """
    target = target_repository(tmp_path)
    by_query = brief(slug="the-one-only-the-tracker-knows")
    by_queue = brief(slug="the-one-only-this-machine-filed")
    fresh = brief(slug="the-one-neither-source-knows")
    planted(target, by_queue, LANDED)

    item = {"key": outbox.identity_key(inspection.identity(by_query)),
            "title": "what the tracker already carries"}
    found = inspecting(
        tmp_path, target=target, act=writes(by_query, by_queue, fresh),
        config=configuration(**{
            filed_query.COMMAND_KEY: answering_query(tmp_path, item)}))

    assert found.report.dedupe[0].ran is True
    assert found.filed_slugs == [fresh["slug"]]

    assert by_query["slug"] in found.detail(ALREADY_FILED)
    assert by_queue["slug"] not in found.detail(ALREADY_FILED)
    assert by_queue["slug"] in found.detail(ALREADY_FILED_LOCALLY)
    assert by_query["slug"] not in found.detail(ALREADY_FILED_LOCALLY)


def reported_lines(printed: str, reason: str) -> list[str]:
    """Every line of the report that is a drop for exactly `reason`.

    Matched at the start of the line rather than by substring, because one
    reason is a prefix of another — and whether the two are told apart is the
    thing being asserted, so a comparison that could not tell them apart would
    answer the wrong question.
    """
    return [line.strip() for line in printed.splitlines()
            if line.strip().startswith(f"{reason}: ")]


def test_the_three_already_filed_reasons_are_counted_and_printed_apart(
        tmp_path):
    """One inspection carrying a drop from each source, told apart twice.

    Once through `dropped_for`, which must return each reason's drop without
    the others', and once through what a developer reads, where the three are
    three lines rather than one merged into another.
    """
    target = target_repository(tmp_path)
    by_query = brief(slug="the-one-the-tracker-reported")
    landed = brief(slug="the-one-the-queue-holds-landed")
    pending = brief(slug="the-one-the-queue-holds-pending")
    planted(target, landed, LANDED)
    planted(target, pending, PENDING)

    item = {"key": outbox.identity_key(inspection.identity(by_query)),
            "title": "what the tracker already carries"}
    found = inspecting(
        tmp_path, target=target, act=writes(by_query, landed, pending),
        config=configuration(**{
            filed_query.COMMAND_KEY: answering_query(tmp_path, item)}))

    for reason, finding in ((ALREADY_FILED, by_query),
                            (ALREADY_FILED_LOCALLY, landed),
                            (ALREADY_QUEUED, pending)):
        drops = found.dropped(reason)
        assert len(drops) == 1, (reason, drops)
        assert finding["slug"] in drops[0].detail

    printed = report_text(found)
    for reason, finding in ((ALREADY_FILED, by_query),
                            (ALREADY_FILED_LOCALLY, landed),
                            (ALREADY_QUEUED, pending)):
        lines = reported_lines(printed, reason)
        assert len(lines) == 1, (reason, printed)
        assert finding["slug"] in lines[0]


@pytest.fixture
def unlistable_queue_target(tmp_path: Path) -> Path:
    """A target whose queue can be written to and cannot be listed.

    Written to, deliberately: a queue that could not be written either would
    make "the inspection still files what it found" unobservable, and the claim
    is that losing tier one costs tier one and nothing else.
    """
    target = target_repository(tmp_path, name="an-unlistable-queue")
    queue = outbox.queue_dir(target)
    queue.mkdir(parents=True, exist_ok=True)
    queue.chmod(0o300)
    yield target
    queue.chmod(0o700)


def refuses_to_be_listed(target: Path) -> None:
    """Skip unless the queue beneath `target` really cannot be listed.

    A developer running the suite as root can read a directory with no read
    bit, which would make the assertions resting on it vacuous rather than
    false — so that case is skipped by name rather than passed silently.
    """
    try:
        list(outbox.queue_dir(target).iterdir())
    except OSError:
        return
    pytest.skip("this process can list a directory with no read permission")


def test_the_unlistable_queue_really_cannot_be_listed(unlistable_queue_target):
    """The control for the assertion below: the directory refuses to be listed,
    so a report saying the index could not be read is the guard working rather
    than a path that quietly succeeded."""
    refuses_to_be_listed(unlistable_queue_target)
    with pytest.raises(OSError):
        outbox.entry_files(outbox.queue_dir(unlistable_queue_target))


def test_a_queue_that_cannot_be_listed_costs_tier_one_and_nothing_else(
        unlistable_queue_target, tmp_path):
    """The inspection runs, invokes, files what it found, and says what it lost.

    Nothing is raised out of the module: a queue that cannot be read is nothing
    known rather than an error, in the one-directional bias every other total
    path there takes. What was filed is read off the queue afterwards, with the
    directory made listable again for the reading alone.
    """
    refuses_to_be_listed(unlistable_queue_target)
    queue = outbox.queue_dir(unlistable_queue_target)

    found = inspecting(tmp_path, target=unlistable_queue_target,
                       act=writes(brief()))

    index = found.report.local_index
    assert index.read is False
    assert str(queue) in index.reason
    assert index.landed == frozenset()
    assert len(found.invocations) == 1
    assert found.filed_slugs == [brief()["slug"]]
    assert found.report.dropped == ()
    assert index.reason in report_text(found)

    queue.chmod(0o700)
    assert filed_payload_slugs(found) == [brief()["slug"]]


def test_a_poisoned_entry_contributes_no_key_and_stops_nothing(tmp_path):
    """The entry beside the poison is indexed exactly as it would have been.

    The poisoned file is counted and named as a count in what a developer
    reads. The control is the same inspection over the same queue without the
    poisoned file, where the count is zero and the note is absent — so the note
    is a fact about the file rather than something always printed.
    """
    known = brief(slug="the-one-beside-the-poison")
    fresh = brief(slug="the-one-nobody-has-filed")
    note = "could not be read as entries"

    target = target_repository(tmp_path, name="holds-a-poisoned-file")
    planted(target, known, LANDED)
    poison = outbox.queue_dir(target) / f"zzz-not-an-entry{outbox.ENTRY_SUFFIX}"
    poison.write_text("{ this file is not an entry", encoding="utf-8")

    found = inspecting(tmp_path, target=target, act=writes(known, fresh))

    assert found.report.local_index.unreadable == 1
    assert found.report.local_index.landed == frozenset(
        {outbox.identity_key(inspection.identity(known))})
    assert known["slug"] in found.detail(ALREADY_FILED_LOCALLY)
    assert found.filed_slugs == [fresh["slug"]]
    assert note in report_text(found)

    clean = target_repository(tmp_path, name="holds-no-poisoned-file")
    planted(clean, known, LANDED)
    control = inspecting(tmp_path, target=clean, act=writes(known, fresh))
    assert control.report.local_index.unreadable == 0
    assert control.report.local_index.landed == \
        found.report.local_index.landed
    assert note not in report_text(control)


def local_index_lines(printed: str) -> list[str]:
    """Every line of the report that speaks about the local queue."""
    return [line.strip() for line in printed.splitlines()
            if "local queue" in line]


def test_the_report_says_what_the_local_index_held_even_when_it_held_nothing(
        tmp_path):
    """A source that is silent when it found nothing cannot be told from one
    that did not run.

    So the line is asserted on an inspection whose queue was empty, and beside
    it on one whose queue held a landed entry and a pending one — where the
    same line says something different. Two lines that could not differ would
    be a report that says nothing.
    """
    empty = inspecting(tmp_path, act=writes(brief()), name="an-empty-queue")
    index = empty.report.local_index
    assert index.read is True
    assert (index.landed, index.queued, index.unreadable) == \
        (frozenset(), frozenset(), 0)

    nothing = local_index_lines(report_text(empty))
    assert len(nothing) == 1, nothing
    assert "0" in nothing[0]

    target = target_repository(tmp_path, name="an-index-holding-two")
    planted(target, brief(slug="one-that-landed"), LANDED)
    planted(target, brief(slug="one-that-is-queued"), PENDING)
    held = inspecting(tmp_path, target=target, act=writes(brief()))

    assert len(held.report.local_index.landed) == 1
    assert len(held.report.local_index.queued) == 1
    said = local_index_lines(report_text(held))
    assert len(said) == 1, said
    assert said != nothing


def test_dedupe_ran_stays_a_statement_about_the_filed_query_alone(tmp_path):
    """A read local index does not make dedupe complete.

    It knows only what this machine filed, so a duplicate filed elsewhere or by
    hand is invisible to it. An inspection whose query could not answer reports
    that dedupe did not run even where the index was read and matched
    something. The control is the same index under a query that answers, where
    the same inspection reports dedupe as having run.
    """
    known = brief(slug="the-one-this-machine-filed")
    fresh = brief(slug="the-one-nobody-has-filed")

    target = target_repository(tmp_path, name="an-index-and-a-failing-query")
    planted(target, known, LANDED)
    found = inspecting(
        tmp_path, target=target, act=writes(known, fresh),
        config=configuration(**{
            filed_query.COMMAND_KEY: failing_query(tmp_path)}))

    assert found.report.local_index.read is True
    assert found.report.local_index.landed
    assert found.report.dedupe_ran is False
    assert known["slug"] in found.detail(ALREADY_FILED_LOCALLY)
    assert "dedupe did NOT run" in report_text(found)

    other = target_repository(tmp_path, name="an-index-and-an-answering-query")
    planted(other, known, LANDED)
    control = inspecting(
        tmp_path, target=other, act=writes(known, fresh),
        config=configuration(**{
            filed_query.COMMAND_KEY: answering_query(tmp_path)}))

    assert control.report.local_index.landed == found.report.local_index.landed
    assert control.report.dedupe_ran is True
    assert known["slug"] in control.detail(ALREADY_FILED_LOCALLY)


def test_the_local_index_is_read_through_the_queues_two_reads_and_nothing_else():
    """No transport, no subprocess, and no filed-query command.

    Read off the function this harness ships, which is the subject of the
    claim. The controls are the two functions beside it in the same module: one
    that legitimately spawns a subprocess and one that legitimately asks the
    filed query, both of which the same three scans report — so the silences
    above are facts about this function rather than scans that see nothing.
    """
    source = introspection.getsource(inspection.local_index)
    assert outbox_attributes(source) == {
        "queue_dir", "entry_files", "read_entry", "LANDED", "PENDING",
    }
    assert script_attributes(source, "subprocess") == set()
    assert script_attributes(source, "filed_query") == set()
    assert filed_query.COMMAND_KEY not in source

    spawns = introspection.getsource(inspection._tracked)
    assert script_attributes(spawns, "subprocess")

    asks = introspection.getsource(inspection.inspect_scope)
    assert script_attributes(asks, "filed_query")

    whole = (REPO_ROOT / "orchestration" / "inspection.py").read_text(
        encoding="utf-8")
    assert filed_query.COMMAND_KEY not in whole


#: A finding written out here rather than derived from the fixtures, and the
#: key the harness files it under. The digest is the pin: every key already on
#: disk was derived from this construction, and a change to what the identity
#: carries, to the order it carries it in, or to the kind, invalidates all of
#: them at once. That must be a deliberate act rather than a side effect of
#: something else, which is what this pin makes it.
PINNED_FINDING = {
    "slug": "zzz-a-finding-pinned-so-the-key-cannot-drift",
    "category": "structural-duplication",
    "paths": ["zzz-src/second.py", "zzz-src/first.py:12"],
    "title": "prose the identity does not carry",
    "body": "more prose the identity does not carry",
    "severity": 3,
    "confidence": "high",
}
PINNED_KEY = "719582c79825521a691a8b3e7187ba539c4c3821ece953965a02869a7681cbce"


def test_the_identity_a_brief_is_filed_under_is_byte_for_byte_what_it_was():
    """The identity, and the key derived from it, pinned against drift.

    Not a restatement of what the identity carries — the test above already
    says that — but the digest itself, because a key already on disk is only
    matched by an identity constructed exactly as the one that produced it.
    """
    identity = inspection.identity(PINNED_FINDING)
    assert identity == {
        "kind": KIND,
        "category": PINNED_FINDING["category"],
        "paths": ["zzz-src/first.py", "zzz-src/second.py"],
        "slug": PINNED_FINDING["slug"],
    }
    assert outbox.identity_key(identity) == PINNED_KEY


#: The directory of hand-written briefs this story records as considered and
#: rejected: it is gitignored and local, its briefs carry no identity, and it
#: answers a different question from the one a tracker answers.
REQUESTS_DIR = ".harness/requests"


def test_nothing_the_inspector_ships_reaches_the_hand_written_briefs():
    """Neither the module nor the entry point reads, writes or names it.

    The control is a planted source naming the directory, which the same search
    reports — so the silence over the two shipped files is a fact about them
    rather than about a search that has stopped seeing anything.
    """
    for path in (REPO_ROOT / "orchestration" / "inspection.py",
                 SCRIPTS / "l5-inspect"):
        assert REQUESTS_DIR not in path.read_text(encoding="utf-8"), path

    planted_source = f'BRIEFS = "{REQUESTS_DIR}"\n'
    assert REQUESTS_DIR in planted_source


# --------------------------------------------------------------------------
# The cap
# --------------------------------------------------------------------------


def test_the_cap_keeps_the_highest_severities_and_names_what_it_dropped(
        tmp_path):
    """Written in an order that makes first-written and highest-severity
    different answers, so a cap on writing order fails here."""
    findings = (
        brief(slug="written-first-and-lowest", severity=LOWEST),
        brief(slug="written-second-and-highest", severity=HIGHEST,
              confidence=CONFIDENCES[-1]),
        brief(slug="written-third-and-middling", severity=MIDDLE),
    )
    found = inspecting(tmp_path, act=writes(*findings), config=configuration(
        **{MAX_FINDINGS_KEY: "2"}))

    assert found.filed_slugs == ["written-second-and-highest",
                                 "written-third-and-middling"]
    dropped = found.dropped(PAST_THE_CAP)
    assert len(dropped) == 1
    assert "written-first-and-lowest" in dropped[0].detail
    assert str(LOWEST) in dropped[0].detail
    assert dropped[0].severity == LOWEST
    assert len(found.entries) == 2


def test_the_cap_is_applied_across_the_whole_inspection_rather_than_per_scope(
        tmp_path):
    """Two scopes, two findings each, and a bound of two briefs in total."""
    first = (brief(slug="first-scope-high", severity=HIGHEST,
                   confidence=CONFIDENCES[-1]),
             brief(slug="first-scope-low", severity=LOWEST))
    second = (brief(slug="second-scope-high", severity=HIGHEST,
                    confidence=CONFIDENCES[-1]),
              brief(slug="second-scope-low", severity=LOWEST))

    found = inspecting(tmp_path, act=writes_per_invocation(first, second),
                       arguments=(SOURCE_DIR, OTHER_SOURCE_DIR),
                       config=configuration(**{MAX_FINDINGS_KEY: "2"}))

    assert len(found.invocations) == 2
    assert sorted(found.filed_slugs) == ["first-scope-high",
                                         "second-scope-high"]
    assert len(found.dropped(PAST_THE_CAP)) == 2
    assert len(found.entries) == 2


def test_nothing_is_dropped_by_a_cap_the_inspection_stayed_inside(tmp_path):
    """The control for the cap: it excludes what exceeds it and nothing else."""
    found = inspecting(tmp_path, act=writes(brief()), config=configuration(
        **{MAX_FINDINGS_KEY: "2"}))
    assert found.filed_slugs == [brief()["slug"]]
    assert found.dropped(PAST_THE_CAP) == ()


# --------------------------------------------------------------------------
# Every invocation is bounded in cost, and the bound is handed to it
# --------------------------------------------------------------------------


def test_the_cost_ceiling_reaches_the_runner_as_this_invocations_allowance(
        tmp_path):
    """Observed at the runner, and per scope rather than per inspection.

    The report says how many invocations were made, which is what lets a
    reader see that the ceiling multiplies with the scopes.
    """
    found = inspecting(tmp_path, arguments=(SOURCE_DIR, OTHER_SOURCE_DIR))

    assert [one.max_budget_usd for one in found.invocations] == \
        [MAX_COST_USD, MAX_COST_USD]
    assert found.report.invocations == 2
    assert "2 invocation(s)" in report_text(found)


def test_an_invocation_is_granted_the_targets_tools_plus_the_one_it_delivers_with(
        tmp_path):
    found = inspecting(tmp_path)
    granted = found.invocations[0].allowed_tools
    assert GRANTED_TOOL in granted
    assert DELIVERY_TOOL in granted
    assert found.invocations[0].permission_mode == PERMISSION_MODE
    assert found.invocations[0].model == MODEL
    assert found.invocations[0].cwd == found.target


def test_the_invocation_writes_its_findings_inside_the_workspace(tmp_path):
    """A turn asked to write outside the workspace cannot deliver, so the
    artifact and the log both sit under the configured logs directory."""
    target = target_repository(tmp_path)
    config = configuration()
    artifact, log_path = inspection.findings_paths(target, config)
    assert artifact.parent == target / LOGS_DIR
    assert log_path.parent == target / LOGS_DIR

    found = inspecting(tmp_path, target=target, config=config)
    assert str(artifact) in found.prompt(0)
    assert found.invocations[0].log_path == log_path


# --------------------------------------------------------------------------
# The dry run
# --------------------------------------------------------------------------


def queue_listing(target: Path) -> list[str]:
    queue = outbox.queue_dir(target)
    if not queue.exists():
        return []
    return sorted(path.name for path in queue.iterdir())


def test_a_dry_run_enqueues_nothing_and_reports_what_an_ordinary_run_files(
        tmp_path):
    """Both halves of the claim, against one target.

    The queue directory is compared before and after the dry run, and the
    equivalent ordinary run is then required to file exactly what the dry run
    said it would.
    """
    findings = (brief(slug="the-first-defect"),
                brief(slug="the-second-defect", severity=HIGHEST,
                      confidence=CONFIDENCES[-1]))
    target = target_repository(tmp_path)
    config = configuration()

    before = queue_listing(target)
    dry = inspecting(tmp_path, target=target, config=config,
                     act=writes(*findings), dry_run=True)
    assert queue_listing(target) == before == []
    assert dry.report.dry_run is True
    assert sorted(dry.filed_slugs) == sorted(one["slug"] for one in findings)
    assert [one.key for one in dry.report.filed] == ["", ""]
    assert "nothing was enqueued" in report_text(dry)

    ordinary = inspecting(tmp_path, target=target, config=config,
                          act=writes(*findings))
    assert sorted(ordinary.filed_slugs) == sorted(dry.filed_slugs)
    assert sorted(entry["payload"]["slug"] for entry in ordinary.entries) == \
        sorted(dry.filed_slugs)


# --------------------------------------------------------------------------
# The bounds, and the refusals that precede every invocation
# --------------------------------------------------------------------------


def test_the_two_bounds_default_in_source():
    """A positive integer and a positive number, asserted at the resolution
    rather than at the constants alone, so what an inspection with neither key
    configured actually runs under is what is pinned."""
    assert isinstance(DEFAULT_MAX_FINDINGS, int)
    assert DEFAULT_MAX_FINDINGS > 0
    assert isinstance(DEFAULT_MAX_COST_USD, (int, float))
    assert DEFAULT_MAX_COST_USD > 0

    bounds, problem = inspection.bounds({})
    assert problem == ""
    assert bounds.max_findings == DEFAULT_MAX_FINDINGS
    assert bounds.max_cost_usd == DEFAULT_MAX_COST_USD


@pytest.mark.parametrize("key,value", [
    pytest.param(MAX_FINDINGS_KEY, "0", id="findings-zero"),
    pytest.param(MAX_FINDINGS_KEY, "-3", id="findings-negative"),
    pytest.param(MAX_FINDINGS_KEY, "several", id="findings-not-an-integer"),
    pytest.param(MAX_COST_KEY, "0", id="cost-zero"),
    pytest.param(MAX_COST_KEY, "-1.5", id="cost-negative"),
    pytest.param(MAX_COST_KEY, "cheap", id="cost-not-a-number"),
])
def test_a_bound_that_is_not_one_is_refused_naming_the_key_and_the_value(
        key, value):
    bounds, problem = inspection.bounds({key: value})
    assert bounds is None
    assert key in problem
    assert value in problem


def test_both_bounds_being_wrong_reports_both():
    _, problem = inspection.bounds({MAX_FINDINGS_KEY: "several",
                                    MAX_COST_KEY: "cheap"})
    assert MAX_FINDINGS_KEY in problem
    assert MAX_COST_KEY in problem


def test_an_inspection_given_a_bound_it_cannot_read_invokes_nothing(tmp_path):
    """The module's own half of the refusal, for a caller that did not refuse.

    Rather than quietly obeying a default nobody wrote: no invocation is made
    and nothing is filed.
    """
    found = inspecting(tmp_path, act=writes(brief()),
                       config=configuration(**{MAX_FINDINGS_KEY: "several"}))
    assert found.invocations == []
    assert found.report.filed == ()
    assert found.entries == []
    assert MAX_FINDINGS_KEY in found.detail(MALFORMED)


# --------------------------------------------------------------------------
# scripts/l5-inspect: the arguments, the wording, and no decision of its own
# --------------------------------------------------------------------------


def l5_inspect():
    return conftest.load_script("l5-inspect")


def report_text(found: Inspected) -> str:
    """What a developer is shown for one inspection, through the real script.

    The module decides all of it and prints none of it, so the wording is read
    where it lives rather than restated here.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = l5_inspect().report(found.report, found.report.dry_run)
    assert code == 0
    return buffer.getvalue()


def test_the_script_parses_scopes_and_the_dry_run_flag():
    parse = l5_inspect().parse
    assert parse([]) == ([], False)
    assert parse([SOURCE_DIR, OTHER_SOURCE_DIR]) == \
        ([SOURCE_DIR, OTHER_SOURCE_DIR], False)
    assert parse(["--dry-run", SOURCE_DIR]) == ([SOURCE_DIR], True)
    assert parse(["--not-a-flag"]) is None


def test_the_report_says_what_was_filed_and_that_nothing_reached_a_tracker(
        tmp_path):
    found = inspecting(tmp_path, act=writes(brief()))
    printed = report_text(found)
    assert brief()["slug"] in printed
    assert brief()["title"] in printed
    assert found.report.filed[0].key in printed
    assert "outbox" in printed
    assert "l5-sync" in printed


def test_the_report_names_every_way_a_finding_was_dropped(tmp_path):
    found = inspecting(tmp_path, act=writes(
        brief(slug="the-one-that-conforms"),
        brief(slug="names-nothing-defined", workflow=UNDEFINED_WORKFLOW),
        {key: value for key, value in brief(slug="malformed").items()
         if key != "severity"}))
    printed = report_text(found)
    assert MALFORMED in printed
    assert UNKNOWN_WORKFLOW in printed
    assert "names-nothing-defined" in printed


def script_attributes(source: str, module: str) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == module:
            found.add(node.attr)
    return found


def test_the_script_holds_no_decision_of_its_own(tmp_path):
    """Every judgement about scope, dedupe, bounds, identity and filing is the
    module's: the script names the refusal and the inspection and nothing else.

    The control is a planted script naming a third entry point, which the same
    scan reports.
    """
    source = (SCRIPTS / "l5-inspect").read_text(encoding="utf-8")
    assert script_attributes(source, "inspection") == {"bounds", "inspect"}

    planted = ("import inspection\n\n\n"
               "def main():\n"
               "    return inspection.identity({})\n")
    assert script_attributes(planted, "inspection") == {"identity"}


def run_script(target: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / "l5-inspect"),
                           *arguments],
                          cwd=target, capture_output=True, text=True,
                          timeout=120)


def configured_target(tmp_path: Path, lines: str, name: str) -> Path:
    """A target whose own configuration file carries `lines`.

    Built for the refusals alone, which are decided before anything is
    invoked — so no path through these tests reaches a model.
    """
    root = target_repository(tmp_path, name=name)
    (root / ".harness").mkdir(parents=True, exist_ok=True)
    (root / ".harness" / "config.yaml").write_text(lines, encoding="utf-8")
    return root


@pytest.mark.parametrize("key,value", [
    pytest.param(MAX_FINDINGS_KEY, "several", id="findings-not-an-integer"),
    pytest.param(MAX_COST_KEY, "cheap", id="cost-not-a-number"),
])
def test_the_script_refuses_a_bound_it_cannot_read_before_invoking_anything(
        key, value, tmp_path):
    """Named at the key and the value, and before anything is enqueued.

    That nothing was invoked is observed rather than argued: the run leaves no
    logs directory, which is where an invocation's findings artifact and log
    are written, and no queue.
    """
    target = configured_target(tmp_path, f"{key}: {value}\n", f"refuses-{key}")
    result = run_script(target)

    assert result.returncode == 1, result.stdout
    assert key in result.stderr
    assert value in result.stderr
    assert result.stdout == ""
    assert not (target / LOGS_DIR).exists()
    assert queue_listing(target) == []


def test_the_script_refuses_an_argument_that_is_not_a_scope(tmp_path):
    target = configured_target(tmp_path, "\n", "refuses-a-flag")
    result = run_script(target, "--not-a-flag")
    assert result.returncode == 1
    assert "Usage" in result.stderr or "usage" in result.stderr
    assert result.stdout == ""


# --------------------------------------------------------------------------
# The model is reached through one module, and this suite reaches none
# --------------------------------------------------------------------------


def test_the_runner_is_injected_and_defaults_to_the_one_module_that_runs_agents(
        tmp_path):
    """The architecture standard that puts model calls in one module.

    The default is `agent_runner.run_agent` itself, and the parameter is what
    every test here substitutes a fake at.
    """
    default = introspection.signature(inspection.inspect).parameters[
        "runner"].default
    assert default is agent_runner.run_agent

    source = (REPO_ROOT / "orchestration" / "inspection.py").read_text(
        encoding="utf-8")
    assert script_attributes(source, "agent_runner") == {"run_agent"}


def inspections_without_a_fake_runner(source: str) -> list[int]:
    """Line numbers of calls to `inspection.inspect` with no runner named.

    A scan rather than a habit: a call that fell back to the default would
    invoke a model, and the whole of this module's claim to reach none rests
    on there being no such call.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not (isinstance(target, ast.Attribute) and target.attr == "inspect"
                and isinstance(target.value, ast.Name)
                and target.value.id == "inspection"):
            continue
        if not any(keyword.arg == "runner" for keyword in node.keywords):
            found.append(node.lineno)
    return found


def test_no_inspection_in_this_module_runs_without_a_fake_runner():
    assert inspections_without_a_fake_runner(
        Path(__file__).read_text(encoding="utf-8")) == []


def test_that_scan_reports_a_call_that_would_have_invoked_a_model():
    """The control for the absence above."""
    planted = ("import inspection\n\n\n"
               "def go(target, config, harness):\n"
               "    return inspection.inspect(target, config, harness)\n")
    assert inspections_without_a_fake_runner(planted) == [5]


def test_every_entry_this_suite_filed_went_to_a_queue_the_test_owns(tmp_path):
    """Nothing here touches a tracker, and nothing here writes into this
    repository's own queue.

    The control is the same predicate pointed at this repository, which it
    reports — so the silence for the fixture target is a fact about where the
    queue is rather than about a comparison that cannot fail.
    """
    found = inspecting(tmp_path, act=writes(brief()))
    assert found.entries
    assert REPO_ROOT not in found.queue.parents
    assert Path(tmp_path) in found.queue.parents
    assert REPO_ROOT in outbox.queue_dir(REPO_ROOT).parents


# --------------------------------------------------------------------------
# The two shapes
# --------------------------------------------------------------------------


def test_a_conforming_brief_is_accepted():
    assert schema_validator.validate(brief(), BRIEF_SCHEMA) == []
    without_optional = {key: value for key, value in brief().items()
                        if key in BRIEF_REQUIRED}
    assert schema_validator.validate(without_optional, BRIEF_SCHEMA) == []
    assert schema_validator.validate(
        brief(not_in_scope=["the sibling module"]), BRIEF_SCHEMA) == []


@pytest.mark.parametrize("field_name", sorted(BRIEF_REQUIRED))
def test_a_brief_missing_any_required_field_is_refused(field_name):
    instance = {key: value for key, value in brief().items()
                if key != field_name}
    problems = schema_validator.validate(instance, BRIEF_SCHEMA)
    assert problems, field_name
    assert any(f"$.{field_name}" in problem for problem in problems), problems


@pytest.mark.parametrize("field_name,value", [
    pytest.param("category", "zzz-not-a-category", id="category-out-of-enum"),
    pytest.param("severity", max(SEVERITIES) + 1, id="severity-out-of-enum"),
    pytest.param("severity", "2", id="severity-not-an-integer"),
    pytest.param("confidence", "certain", id="confidence-out-of-enum"),
    pytest.param("effort", "XL", id="effort-out-of-enum"),
    pytest.param("title", 17, id="title-not-a-string"),
    pytest.param("workflow", 17, id="workflow-not-a-string"),
    pytest.param("paths", SOURCE_FILE, id="paths-not-an-array"),
    pytest.param("paths", [17], id="path-not-a-string"),
])
def test_each_way_of_malforming_a_brief_is_refused(field_name, value):
    problems = schema_validator.validate(brief(**{field_name: value}),
                                         BRIEF_SCHEMA)
    assert problems, (field_name, value)
    assert any(f"$.{field_name}" in problem for problem in problems), problems


def test_the_workflow_field_is_a_plain_string_rather_than_an_enum():
    """The acceptable names are the definitions the harness holds, so a third
    workflow becomes selectable by shipping one — which the drop above shows
    the module deciding, and which this shows the schema declining to."""
    assert BRIEF_SCHEMA["properties"]["workflow"]["type"] == "string"
    assert "enum" not in BRIEF_SCHEMA["properties"]["workflow"]


def test_a_conforming_envelope_is_accepted():
    assert schema_validator.validate(envelope(brief()), FINDINGS_SCHEMA) == []
    assert schema_validator.validate(envelope(), FINDINGS_SCHEMA) == []


@pytest.mark.parametrize("instance,path", [
    pytest.param({}, "$.findings", id="no-findings"),
    pytest.param({"findings": {}}, "$.findings", id="findings-not-an-array"),
    pytest.param({"findings": ["a defect"]}, "$.findings[0]",
                 id="finding-not-an-object"),
])
def test_each_way_of_malforming_the_envelope_is_refused(instance, path):
    problems = schema_validator.validate(instance, FINDINGS_SCHEMA)
    assert problems, instance
    assert any(path in problem for problem in problems), problems


def test_the_envelope_types_its_items_as_objects_and_nothing_more():
    """Which is what lets one malformed finding be dropped without costing the
    rest, and what keeps the brief's shape defined once."""
    items = FINDINGS_SCHEMA["properties"]["findings"]["items"]
    assert items == {"type": "object"}
    assert schema_validator.validate(envelope({"anything": True}),
                                     FINDINGS_SCHEMA) == []


def test_the_brief_schema_states_why_its_paths_are_bare_and_what_it_is_not():
    """A live harness artifact, read as it ships: where a reader meets the
    rules the code enforces and the reason for them."""
    described = json.dumps(BRIEF_SCHEMA).lower()
    assert "line number" in described
    assert "marker" in described
    assert "not a story" in described
    assert "filed under" in described


def test_the_envelope_schema_says_why_it_restates_nothing():
    described = json.dumps(FINDINGS_SCHEMA)
    assert inspection.BRIEF_SCHEMA in described
    assert "reference keyword" in described


def test_both_schemas_are_registered_in_the_shipped_inventory():
    shipped = schema_validator.shipped_schemas()
    assert inspection.BRIEF_SCHEMA in shipped
    assert inspection.FINDINGS_SCHEMA in shipped


# --------------------------------------------------------------------------
# prompts/inspector.md, read as it ships
# --------------------------------------------------------------------------


def inspector_prompt() -> str:
    return (PROMPTS / INSPECTOR_PROMPT).read_text(encoding="utf-8")


def flattened(text: str) -> str:
    """One text with its line breaks taken out of it.

    A prompt is wrapped for a reader, so a sentence in it is not a line: a
    search for a phrase has to be a search over the prose rather than over the
    wrapping, or an assertion goes red when a word moves to the next line.
    """
    return " ".join(text.split())


def test_the_prompt_states_every_category_the_schema_declares():
    """Derived from the schema in both directions rather than eyeballed: a
    category the prompt does not describe is one the Inspector is asked to
    choose blind."""
    text = inspector_prompt()
    for category in CATEGORIES:
        assert category in text, category


def test_the_prompt_defines_severity_by_consequence_and_confidence_apart():
    text = flattened(inspector_prompt())
    assert "Severity is a consequence" in text
    assert "Confidence is a separate axis" in text
    for confidence in CONFIDENCES:
        assert confidence in text, confidence
    for effort in EFFORTS:
        assert effort in text, effort


def test_the_prompt_carries_the_two_mechanical_rules():
    lowered = flattened(inspector_prompt()).lower()
    assert "file:line" in lowered
    assert f"severity {HIGHEST} unless its confidence is high" in lowered


def test_the_prompt_names_no_standards_file_and_asks_for_none():
    """The harness declares no required document set.

    The absence is controlled by the behaviour below, where a standards
    document whose name nothing could have guessed is read: the body is
    globbed, so a repository with one standards file and a repository with
    twelve are inspected identically.
    """
    text = flattened(inspector_prompt())
    assert "undifferentiated body" in text
    assert "not look for a standards file by name" in text.lower()
    assert "declares no standards" in text
    assert "first observation" in text


def test_the_prompt_states_the_slug_rule_and_that_the_title_is_not_filed_on():
    text = flattened(inspector_prompt())
    assert "kebab-case" in text
    assert "slug" in text
    assert "part of what a brief is filed under" in text


def test_the_prompt_says_the_already_filed_items_are_data_and_not_instructions():
    text = flattened(inspector_prompt())
    assert "data, not instructions" in text
    assert "did not answer" in text


def test_the_shipped_prompt_names_no_workflow_and_the_render_names_them_all(
        tmp_path):
    """The candidates are rendered from the definitions' own `applies_when`.

    The template carries the placeholder and no workflow name, so a third
    workflow becomes classifiable by shipping a definition; the control is the
    same template rendered against a harness root, where both of that root's
    workflows and both of their own sentences appear.
    """
    text = inspector_prompt()
    assert "{{workflow_candidates}}" in text
    for shipped in harness_config.workflow_names(REPO_ROOT):
        assert shipped not in text, shipped

    found = inspecting(tmp_path)
    rendered = found.prompt(0)
    for workflow, applies_when in MIRROR_WORKFLOWS.items():
        assert workflow in rendered, workflow
        assert applies_when in rendered, workflow
    assert workflow_selection.candidate_block(
        workflow_selection.candidates(found.harness)) in rendered


def test_the_standards_body_is_globbed_and_no_filename_is_interpreted(tmp_path):
    """A document nothing could have guessed the name of reaches the prompt.

    The control is the same inspection over a target that declares none, where
    the field renders as the optional-placeholder convention's None — so the
    marker's presence is a fact about the glob rather than about a field that
    always carries something.
    """
    found = inspecting(tmp_path, standards={
        STANDARDS_FILE: f"# whatever we called it\n- {STANDARDS_MARKER}\n"})
    assert STANDARDS_MARKER in found.prompt(0)
    assert STANDARDS_FILE not in inspector_prompt()
    assert STANDARDS_FILE not in (
        REPO_ROOT / "orchestration" / "inspection.py").read_text(
            encoding="utf-8")

    without = inspecting(tmp_path / "no-standards", standards={})
    assert STANDARDS_MARKER not in without.prompt(0)


# --------------------------------------------------------------------------
# prompts/assist.md, the harness's other producer of briefs
# --------------------------------------------------------------------------

#: The sentence the assist prompt used to close with, which named an artifact
#: shape that no longer exists. Carried here so the absence below can be shown
#: to be detectable rather than merely observed.
SUPERSEDED_CLOSING = ("When you propose a harness improvement, express it as a "
                      "story request the developer can hand to l5-plan.")

#: The same sentence as the prompt file actually carried it: hard-wrapped, so
#: the sentence spans a line break. A search for the one-line form over the raw
#: file would miss this, which is why the comparison below collapses runs of
#: whitespace on both sides before asking.
SUPERSEDED_CLOSING_AS_WRAPPED = (
    "When you propose a harness improvement, express it as a story request the\n"
    "developer can hand to l5-plan.\n")


def collapsed(text: str) -> str:
    """`text` with every run of whitespace reduced to one space.

    Prose in these prompts is hard-wrapped, so a sentence is a sentence
    regardless of where the wrapping put its line breaks.
    """
    return " ".join(text.split())


def assist_prompt() -> str:
    return (PROMPTS / "assist.md").read_text(encoding="utf-8")


def test_the_assist_prompt_says_what_a_brief_contains():
    """The harness's two producers of briefs are told the same thing."""
    text = assist_prompt()
    assert "brief" in text
    assert "file:line" in text
    assert "slug" in text
    assert "severity" in text
    assert "confidence" in text
    assert f"schemas/{inspection.BRIEF_SCHEMA}.schema.json" in text
    assert "mandate" in text


def test_the_assist_prompt_no_longer_asks_for_a_story_request():
    """The absence, with the same search shown reporting the sentence.

    The control is the shipped prompt with the superseded closing restored in
    the hard-wrapped form the file actually carried it in — not the one-line
    constant appended to itself — so what is demonstrated is that this
    comparison finds the sentence as it was written, line break and all. The
    silence above is then a fact about the text rather than about a search
    looking for something the file could never have matched.
    """
    text = assist_prompt()
    assert collapsed(SUPERSEDED_CLOSING) not in collapsed(text)

    restored = text + "\n" + SUPERSEDED_CLOSING_AS_WRAPPED
    assert collapsed(SUPERSEDED_CLOSING) in collapsed(restored)


# --------------------------------------------------------------------------
# Who reaches this module, and what this story left alone
# --------------------------------------------------------------------------


def harness_sources(root: Path) -> list[Path]:
    """Every file a caller could live in: `orchestration/*.py` and `scripts/`."""
    sources = sorted((root / "orchestration").glob("*.py"))
    sources += sorted(path for path in (root / "scripts").iterdir()
                      if path.is_file())
    return sources


def sources_importing(module: str, root: Path) -> set[str]:
    found = set()
    for path in harness_sources(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == module for alias in node.names):
                    found.add(str(path.relative_to(root)))
            elif isinstance(node, ast.ImportFrom) and node.module == module:
                found.add(str(path.relative_to(root)))
    return found


def planted_root(tmp_path: Path, name: str, source: str) -> Path:
    root = Path(tmp_path) / "planted"
    (root / "orchestration").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "orchestration" / name).write_text(source, encoding="utf-8")
    (root / "scripts" / "l5-planted").write_text("pass\n", encoding="utf-8")
    return root


def test_the_inspection_module_is_reached_from_the_entry_point_and_nowhere_else():
    """Nothing in a run, a resume or a sweep invokes an inspection.

    Broad mode is a terminal command a developer asked for, so the only
    importer in the harness is its entry point — the suite reaches it too, and
    the suite is not scanned here because a test importing the module under
    test is what a test does.
    """
    assert sources_importing("inspection", REPO_ROOT) == \
        {str(Path("scripts") / "l5-inspect")}


def test_the_import_scan_reports_a_second_caller_when_there_is_one(tmp_path):
    """The control for the absence above, in both import forms."""
    root = planted_root(tmp_path, "a_caller.py",
                        "import inspection\n\n\ndef go(target):\n"
                        "    return inspection.inspect(target, {}, None)\n")
    assert sources_importing("inspection", root) == \
        {str(Path("orchestration") / "a_caller.py")}

    other = planted_root(tmp_path / "from-form", "another_caller.py",
                         "from inspection import inspect\n")
    assert sources_importing("inspection", other) == \
        {str(Path("orchestration") / "another_caller.py")}


#: What this story states it widens none of, and the request directory it
#: states it leaves exactly as it is.
UNCHANGED = (
    "orchestration/outbox.py",
    "orchestration/outbox_sweep.py",
    "orchestration/command_transport.py",
    "orchestration/filed_query.py",
    "orchestration/agent_runner.py",
    "orchestration/story_coordinator.py",
    ".harness/requests/",
)


@pytest.mark.parametrize("relative", UNCHANGED)
def test_this_story_left_these_paths_alone(relative, tmp_path):
    """Restated over a story this test builds rather than recalled out of this
    repository's own commit graph.

    The claim is the story's: it is a caller of four seams and widens none of
    them, it reaches the coordinator not at all, and it leaves the request
    directory exactly as it is. The predicate is the shared resolution's, and
    the control beside it shows the same call reporting the violation — so an
    empty diff here is a fact about a story that respected the path rather
    than about a comparison bounded at commits where nothing could differ.
    """
    respecting = conftest.constructed_story(tmp_path, respected=[relative],
                                            name="scope-respected")
    assert conftest.constructed_story_diff(respecting, [relative]) == ""
    violating = conftest.constructed_story(tmp_path, violated=[relative],
                                           name="scope-violated")
    assert conftest.constructed_story_diff(violating, [relative]) != ""


def test_nothing_in_this_story_reads_the_request_directory():
    """The briefs under it are worked examples and historical records.

    An absence over the two sources this story adds, controlled by the same
    search over a planted source that does name the directory.
    """
    requests = ".harness/requests"
    for relative in ("orchestration/inspection.py", "scripts/l5-inspect"):
        assert requests not in (REPO_ROOT / relative).read_text(
            encoding="utf-8"), relative
    assert requests in f"a source that reads {requests}/README.md"


# --------------------------------------------------------------------------
# No target stack entered the harness with this story
# --------------------------------------------------------------------------


def test_the_scan_that_holds_harness_source_free_of_target_literals_covers_it():
    """The existing scan, run rather than cited.

    Its subject is what this repository ships, so it is pointed at this
    repository and required to report nothing in the new module. The control
    below is a copy of the module with a provider named in it, which the same
    scan must report.
    """
    relative = "orchestration/inspection.py"
    assert [finding for finding in harness_source.scan(REPO_ROOT)
            if finding.path == relative] == []
    assert (REPO_ROOT / relative).is_file()


def test_the_only_literal_the_new_entry_point_carries_is_a_declared_mention():
    """The entry point says to the kernel that it is a Python program.

    Which is a tie the scan reports and the other module's own list declares,
    with a reason — so this asserts the entry through *that* list rather than
    writing an exemption of its own beside it, and a tie the list does not
    carry would be reported here.
    """
    reported = [finding for finding in harness_source.scan(REPO_ROOT)
                if finding.path == "scripts/l5-inspect"]
    assert reported, "the scan sees nothing in the entry point it is pointed at"
    for finding in reported:
        entry = (finding.path, finding.line)
        assert entry in stack_module.PERMANENT_MENTIONS, entry
        assert stack_module.PERMANENT_MENTIONS[entry].strip(), entry


def test_that_scan_reports_a_provider_named_in_the_new_module(tmp_path):
    """The control, built against a throwaway root rather than by editing this
    repository."""
    root = Path(tmp_path) / "scanned"
    (root / "orchestration").mkdir(parents=True)
    source = (REPO_ROOT / "orchestration" / "inspection.py").read_text(
        encoding="utf-8")
    (root / "orchestration" / "inspection.py").write_text(
        source + '\n\nPLANTED = "pytest -q"\n', encoding="utf-8")
    reported = [finding for finding in harness_source.scan(root)
                if finding.path.endswith("inspection.py")]
    assert reported, "the scan sees nothing in the module it is pointed at"


# --------------------------------------------------------------------------
# The three configured keys are declared and read
# --------------------------------------------------------------------------


def test_the_three_keys_are_declared_and_this_repository_declares_its_own():
    """Declared in the schema, and the one that is not a bound is set here.

    What each key *governs* is proven in `tests/test_config_keys_are_obeyed.py`,
    where every declared key is varied and the harness observed obeying it;
    what is asserted here is the declaration and this repository's own use of
    it.
    """
    declared = harness_config.declared_config_keys()
    for key in (SOURCE_DIRS_KEY, MAX_FINDINGS_KEY, MAX_COST_KEY):
        assert key in declared, key

    own = harness_config.load_config(REPO_ROOT)
    assert own[SOURCE_DIRS_KEY], "this repository declares no source dirs"
    assert all(isinstance(one, str) and one for one in own[SOURCE_DIRS_KEY])
    # Its own tests are a scope beside them rather than one of them.
    assert own["tests_dir"] not in own[SOURCE_DIRS_KEY]
