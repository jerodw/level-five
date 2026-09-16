"""The documenter is governed by the check that records what it wrote.

Before story-154 the documenter could write implementation code and the run
recorded nothing about it: it carried neither `may_only_change` nor
`revert_check`, so there was no path on which the coordinator asked what it
wrote. It now declares both, in both shipped workflows, on the terms the tester
declares its own — and because the documenter's subject is a set of documents
rather than a directory, the confinement's prefix is a workflow token,
`{{architecture_docs}}`, resolved from the target's configuration.

The subject here is what this repository ships, which is why the runs below go
through the shipped story workflow rather than a built one: the claim is that
*this deployment's* documenter is governed, and that cannot be asked of a
fixture. Every name is still derived — the documenter stage is found by the
token its confinement carries, the artifact names come off its declaration,
the correction-pass entry off the verifier's — and the target underneath is a
repository this module builds, with a real suite the revert check's verdict
comes from.

The confinement collides with the correction pass, and the resolution is
asserted as runs: the verifier's verdict is the grant. A correctable finding
names a `path`, and at the documenter's post-stage checks the exempt list is
the story's own grants plus that path, for the stage and attempt the pass
record names and no other.

Every absence asserted here carries a demonstration that it can fail:

  * "a documenter naming only the configured documents runs no revert check"
    sits beside the same run naming a module outside them, which runs one;
  * "an edit the suite is green without is undone" sits beside the identical
    edit with the suite red without it, which is kept and permitted;
  * "a granted path is not put to the revert check on the pass" sits beside a
    second file the same documenter edited that no finding named, which is;
  * "a later attempt inherits no grant" sits beside the pass itself, where the
    same file is exempt and the grant is in events.log;
  * "an unset key resolves the confinement out" sits beside the same load with
    one and with two documents configured;
  * "the verifier slot no longer calls the record documentation files" sits
    beside the old wording, constructed here, which the same check reports.

Nothing here invokes a model: every run goes through the fake runner below.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
import context_assembler
import harness_config
import schema_validator
import story_coordinator
import story_parser
from agent_runner import AgentResult

REPO_ROOT = conftest.HARNESS_ROOT
STORY_ID = "story-001"

#: The token a documenter's confinement carries in a shipped definition, and
#: the configuration key it resolves from. The one place the token is spelled;
#: the documenter stage is *found* by it below rather than named.
DOCS_KEY = "architecture_docs"
DOCS_TOKEN = f"{{{{{DOCS_KEY}}}}}"

#: The workflow the runs below execute. Its name is a name `load_workflow`
#: takes, spelled here once because the subject of the runs is this
#: deployment's own definition.
RUN_WORKFLOW = "story-workflow"

#: Both definitions this harness ships, read off the directory rather than
#: listed, so a third would join these assertions without an edit here.
SHIPPED = harness_config.workflow_names(REPO_ROOT)

CONFINEMENT = story_coordinator.CONFINEMENT


def raw_definition(name: str) -> dict:
    """A shipped definition as it ships — token and all."""
    return json.loads(
        (REPO_ROOT / "workflows" / f"{name}.json").read_text(encoding="utf-8"))


def stage_confined_to_the_documents(definition: dict) -> dict:
    """The one stage whose confinement is the architecture-documents token."""
    stages = [stage for stage in definition["stages"]
              if DOCS_TOKEN in stage.get(CONFINEMENT, [])]
    assert len(stages) == 1, [stage["name"] for stage in stages]
    return stages[0]


RAW = raw_definition(RUN_WORKFLOW)
DOCUMENTING_STAGE = stage_confined_to_the_documents(RAW)
DOCUMENTING = DOCUMENTING_STAGE["name"]
DECLARATION = DOCUMENTING_STAGE["revert_check"]
ARTIFACT = DECLARATION["result"]
RECORD = DOCUMENTING_STAGE["changed_files"]
VERIFIER_STAGE = next(stage for stage in RAW["stages"]
                      if "correction_pass" in stage)
VERIFYING = VERIFIER_STAGE["name"]
CORRECTION = VERIFIER_STAGE["correction_pass"]
ROUTES = VERIFIER_STAGE["on_failure"]["retry_routing"]

#: The retry category whose route re-enters the documenter, derived from the
#: table rather than spelled, so the "later attempt" run below is a real
#: backward retry to the stage under test.
(DOCUMENTING_CATEGORY,) = [category for category, route in ROUTES.items()
                           if route["stage"] == DOCUMENTING]

#: The events the grant and the two revert dispositions append, as the
#: execution-history schema enumerates them.
GRANT_EVENT = "correction-pass-grant-applied"
STORY_GRANT_EVENT = "stage-exception-applied"
REVERTED_EVENT = "revert-check-reverted"
PERMITTED_EVENT = "revert-check-permitted"

VERDICT_SCHEMA = schema_validator.load_schema("verification-result")
CORRECTION_SCHEMA = schema_validator.load_schema("correction-pass")
HISTORY_SCHEMA = schema_validator.load_schema("execution-history")
RULES = harness_config.load_rules(REPO_ROOT)


# --------------------------------------------------------------------------
# The target: configured documents, a module outside them, a suite over it
# --------------------------------------------------------------------------

#: The fixture's own names, each stated once. The document is where the
#: documenter may write; the modules are outside it and so are where every
#: governed case lands; the suite is where the revert check's verdict comes
#: from.
DOCUMENT = ".harness/docs/ARCHITECTURE.md"
SECOND_DOCUMENT = ".harness/docs/DECISIONS.md"
SOURCE_DIR = "src/"
SUITE_DIR = "tests/"
APP = f"{SOURCE_DIR}app.py"
EXAMPLE = f"{SOURCE_DIR}example.py"
OTHER = f"{SOURCE_DIR}other.py"
NEW_MODULE = f"{SOURCE_DIR}helper.py"
NEW_TEST = f"{SUITE_DIR}test_shout.py"

TEST_COMMAND = shlex.join([sys.executable, "-m", "pytest",
                           SUITE_DIR.rstrip("/"), "-q", "-p", "no:cacheprovider"])

APP_AT_HEAD = '''\
def greet(name):
    return f"hello, {name}"
'''

#: The helper the suite needs once the test below exists: revert it and the
#: new test cannot import what it calls.
APP_WITH_HELPER = APP_AT_HEAD + '''

def shout(name):
    return greet(name).upper()
'''

TEST_APP = '''\
from app import greet


def test_greet():
    assert greet("world") == "hello, world"
'''

TEST_SHOUT = '''\
from app import shout


def test_shout():
    assert shout("world") == "HELLO, WORLD"
'''

EXAMPLE_AT_HEAD = '"""An example module. Its docstring say three things."""\n'
EXAMPLE_CORRECTED = '"""An example module. Its docstring says three things."""\n'
OTHER_AT_HEAD = '"""Another module, which no finding names."""\n'
OTHER_REWORDED = '"""Another module, reworded by a pass that was not granted it."""\n'
EXAMPLE_REWORDED_AGAIN = '"""An example module, reworded again on a retry."""\n'
DOCUMENT_AT_HEAD = "# Architecture\n"
DOCUMENT_UPDATED = "# Architecture\n\nThe documenter wrote this.\n"

ROOT_CONFTEST = '''\
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
'''


def config_text(documents: list[str]) -> str:
    listed = "".join(f"  - {document}\n" for document in documents)
    return (
        f"workflow: {RUN_WORKFLOW}\n"
        "branch_prefix: story/\n"
        "permission_mode: acceptEdits\n"
        "stories_dir: .harness/stories\n"
        "runs_dir: .harness/runs\n"
        "logs_dir: .harness/logs\n"
        "standards_dir: .harness/standards\n"
        f"{DOCS_KEY}:\n{listed}"
        f"test_command: {TEST_COMMAND}\n"
        f"tests_dir: {SUITE_DIR}\n"
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def build_target(root: Path) -> Path:
    """A repository configured to run the shipped workflow, with a real suite."""
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml", config_text([DOCUMENT]))
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", conftest.STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / DOCUMENT, DOCUMENT_AT_HEAD)
    write(root / "conftest.py", ROOT_CONFTEST)
    write(root / APP, APP_AT_HEAD)
    write(root / EXAMPLE, EXAMPLE_AT_HEAD)
    write(root / OTHER, OTHER_AT_HEAD)
    write(root / SUITE_DIR / "test_app.py", TEST_APP)
    write(root / ".gitignore", ".pytest_cache/\n__pycache__/\n")
    conftest.init_repository(root)
    return root


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return build_target(tmp_path / "governed-documenter-target")


def append_to_story(target_root: Path, text: str) -> None:
    path = target_root / ".harness" / "stories" / f"{STORY_ID}.yaml"
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")
    conftest.commit_setup(target_root, "the story this test runs")


def story_grant(path: str) -> str:
    return (
        "\nstage_exceptions:\n"
        f"  - stage: {DOCUMENTING}\n"
        f"    create: {path}\n"
        "    reason: this story's own deliverable is that file\n"
    )


# --------------------------------------------------------------------------
# The documenter's working-tree changes, each with the record for it
# --------------------------------------------------------------------------


def only_the_document(tree: Path) -> dict:
    write(tree / DOCUMENT, DOCUMENT_UPDATED)
    return {"modified": [DOCUMENT], "created": [], "deleted": []}


def unforced_module_edit(tree: Path) -> dict:
    """The document, and a module outside it that nothing in the suite needs."""
    write(tree / DOCUMENT, DOCUMENT_UPDATED)
    write(tree / APP, APP_WITH_HELPER)
    return {"modified": [DOCUMENT, APP], "created": [], "deleted": []}


def forced_module_edit(tree: Path) -> dict:
    """The same module edit, with a test that needs it.

    The test is outside the confinement too, so the story running this case
    grants it; the module edit is not granted, and reverting it alone is what
    turns the suite red.
    """
    write(tree / APP, APP_WITH_HELPER)
    write(tree / NEW_TEST, TEST_SHOUT)
    return {"modified": [APP], "created": [NEW_TEST], "deleted": []}


def creation_outside(tree: Path) -> dict:
    write(tree / NEW_MODULE, "VALUE = 1\n")
    return {"modified": [], "created": [NEW_MODULE], "deleted": []}


def the_named_file(tree: Path) -> dict:
    """The correction the pass asked for, in the file the finding named."""
    write(tree / EXAMPLE, EXAMPLE_CORRECTED)
    return {"modified": [EXAMPLE], "created": [], "deleted": []}


def the_named_file_again(tree: Path) -> dict:
    """A later edit to the file the pass was granted, with different words, so
    undoing it is observable beside the pass's edit standing."""
    write(tree / EXAMPLE, EXAMPLE_REWORDED_AGAIN)
    return {"modified": [EXAMPLE], "created": [], "deleted": []}


def the_named_file_and_another(tree: Path) -> dict:
    """The same correction, and a second file no finding named."""
    write(tree / EXAMPLE, EXAMPLE_CORRECTED)
    write(tree / OTHER, OTHER_REWORDED)
    return {"modified": [EXAMPLE, OTHER], "created": [], "deleted": []}


# --------------------------------------------------------------------------
# Driving a run of the shipped workflow
# --------------------------------------------------------------------------

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}
EMPTY = {"modified": [], "created": [], "deleted": []}

#: The finding a pass is routed on. Its `path` is the grant; its category is
#: one the shipped table declares, read off the table rather than spelled.
FINDING = {
    "path": EXAMPLE,
    "location": f"{EXAMPLE} - the module docstring",
    "finding": "MARKER-FINDING the docstring says 'say' for 'says'",
    "correction": "MARKER-FINDING-FIX write 'says'",
    "category": DOCUMENTING_CATEGORY,
}


def passing_with(*findings: dict) -> dict:
    return {**PASS, "correctable_findings": [dict(one) for one in findings]}


def failing_into(category: str) -> dict:
    return {
        "status": "failed",
        "blocking_issues": [{
            "severity": "high", "issue": "the document is stale",
            "location": DOCUMENT,
            "required_behavior": "the document describes what shipped",
        }],
        "unverified": [], "retry_recommended": True, "retry_target": category,
    }


class Runner:
    """A fake agent runner that writes each stage's declared artifacts.

    Every artifact comes off the stage's declaration in the loaded workflow.
    The documenter's working-tree change is the case's: `edits` is the list of
    edit functions its invocations make, in order — None for an invocation
    that changes nothing — so a run that re-enters the documenter can have it
    do something different the second time. A pass runs in the attempt its
    first entry ran in, and a record in one attempt accounts for everything
    the stage changed in it, so the runs below that take a pass have the
    first entry change nothing and the pass's record name the pass's edits
    alone. Each
    invocation's prompt is kept, so what a stage was told on a pass can be
    read back beside what it was told on its first entry.
    """

    def __init__(self, target_root: Path, verdicts: list[dict],
                 edits: list | None = None):
        self.target_root = Path(target_root)
        self.run_dir = conftest.run_dir_for(self.target_root, STORY_ID)
        self.stages = harness_config.load_workflow(
            REPO_ROOT, RUN_WORKFLOW, harness_config.load_config(target_root)
        )["stages"]
        self.verdicts = list(verdicts)
        self.edits = list(edits or [])
        self.calls: list[str] = []
        self.prompts: dict[str, list[str]] = {}

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None, suite_command=None, run_dir=None):
        self.calls.append(stage)
        self.prompts.setdefault(stage, []).append(prompt)
        declaration = next(s for s in self.stages if s["name"] == stage)
        edit = self.edits.pop(0) if (stage == DOCUMENTING and self.edits) else None
        record = edit(Path(cwd)) if edit else dict(EMPTY)
        for artifact in story_coordinator.required_artifacts(declaration):
            self._write(artifact, declaration, record)
        return AgentResult(ok=True, result_text=f"{stage} done")

    def _write(self, artifact: str, declaration: dict, record: dict) -> None:
        path = self.run_dir / artifact
        if artifact == conftest.VERIFICATION_RESULT:
            verdict = conftest.answering_guidance(self.verdicts.pop(0),
                                                  self.run_dir)
            write_json(path, verdict)
            if verdict.get("retry_recommended"):
                write_json(self.run_dir / conftest.RETRY_GUIDANCE, {
                    "current_focus": [{
                        "focus": "bring the document up to date",
                        "satisfied_when": "it describes what shipped",
                    }],
                    "preserve_behavior": ["the existing behavior"],
                    "retry_scope": [DOCUMENT],
                })
        elif artifact == declaration.get("changed_files"):
            write_json(path, record)
        elif artifact == conftest.TEST_RESULTS:
            write_json(path, {"tests_written": 1})
        else:
            write(path, f"{artifact} written.\n")


def drive(target_root: Path, verdicts: list[dict], edits: list | None = None):
    """One run of the shipped workflow: exit code, runner, run directory."""
    runner = Runner(target_root, verdicts, edits)
    code = story_coordinator.run_story(STORY_ID, REPO_ROOT, target_root, runner)
    return code, runner, runner.run_dir


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def history_of(run_dir: Path) -> list[dict]:
    return read_json(run_dir / "execution-history.json")


def events_of_kind(run_dir: Path, kind: str) -> list[dict]:
    """History entries of one kind; the schema keys the kind as `event` and
    the wording as `message`."""
    return [entry for entry in history_of(run_dir) if entry.get("event") == kind]


def documenter_events(run_dir: Path, kind: str) -> list[dict]:
    return [entry for entry in events_of_kind(run_dir, kind)
            if entry.get("stage") == DOCUMENTING]


def events_log(run_dir: Path) -> str:
    return (run_dir / "events.log").read_text(encoding="utf-8")


def pass_record(run_dir: Path, number: int = 1) -> dict:
    return read_json(run_dir / story_coordinator.correction_pass_result_file(
        CORRECTION["result"], number))


def content(run_dir: Path, relative: str) -> str:
    """A file in the tree the run worked in, which holds its run directory
    under the configured runs_dir — so the tree is two levels above it."""
    return (run_dir.parents[2] / relative).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The fixture is what it claims to be
# --------------------------------------------------------------------------


def restriction_declarations(definition: dict) -> list[str]:
    """Every entry of every restriction declaration across a definition's
    stages — the lists a literal path would have to be in to govern anything.
    The prose beside them is not read: a `budget_reason` that names the
    document by way of explanation predates this story and confines no one."""
    return [entry
            for stage in definition["stages"]
            for key in (CONFINEMENT, story_coordinator.CREATE_RESTRICTION)
            for entry in stage.get(key, [])]


def test_the_documenter_is_found_by_its_token_in_both_shipped_workflows():
    """The premise: each shipped definition confines exactly one stage to the
    documents, by the token and not by a literal path, and declares the revert
    check beside it keyed as the tester's is."""
    for name in SHIPPED:
        stage = stage_confined_to_the_documents(raw_definition(name))
        assert stage[CONFINEMENT] == [DOCS_TOKEN]
        assert set(stage["revert_check"]) == {"result", "baseline", "discarded"}
        assert DOCUMENT not in restriction_declarations(raw_definition(name))


def test_a_literal_path_in_a_restriction_declaration_would_be_seen():
    """The control for the absence above: the same reader over a copy of the
    shipped definition with the literal path planted in a restriction list
    reports it, so "no literal path" is the declarations and not a reader
    that stopped seeing them."""
    planted = raw_definition(RUN_WORKFLOW)
    stage = stage_confined_to_the_documents(planted)
    stage[CONFINEMENT] = [DOCUMENT]
    assert DOCUMENT in restriction_declarations(planted)
    # And the other sense is read too: planted there instead, still seen.
    also = raw_definition(RUN_WORKFLOW)
    implementer = next(stage for stage in also["stages"]
                       if story_coordinator.CREATE_RESTRICTION in stage)
    implementer[story_coordinator.CREATE_RESTRICTION].append(DOCUMENT)
    assert DOCUMENT in restriction_declarations(also)


def test_the_correction_pass_enters_at_the_documenter():
    """The grant cases below are about the stage a pass enters at, and the
    documenter is that stage in the shipped definition."""
    assert story_coordinator.correction_pass_declaration(
        RAW["stages"])["stage"] == DOCUMENTING


def test_the_target_suite_is_green_as_built_and_red_without_the_helper(tmp_path):
    """What the revert check's two verdicts rest on: the suite as built passes,
    and with the new test present and the helper absent it fails."""
    root = build_target(tmp_path / "probe")
    assert subprocess.run(shlex.split(TEST_COMMAND), cwd=root).returncode == 0
    write(root / NEW_TEST, TEST_SHOUT)
    assert subprocess.run(shlex.split(TEST_COMMAND), cwd=root).returncode != 0
    write(root / APP, APP_WITH_HELPER)
    assert subprocess.run(shlex.split(TEST_COMMAND), cwd=root).returncode == 0


# --------------------------------------------------------------------------
# The token resolves the confinement out of configuration
# --------------------------------------------------------------------------


def config_with(documents) -> dict:
    """This repository's config with the documents set, or the key removed."""
    config = dict(conftest.repository_config())
    if documents is None:
        config.pop(DOCS_KEY, None)
    else:
        config[DOCS_KEY] = list(documents)
    return config


def documenter_under(name: str, documents) -> dict:
    workflow = harness_config.load_workflow(REPO_ROOT, name,
                                            config_with(documents))
    return next(stage for stage in workflow["stages"]
                if stage["name"] == DOCUMENTING)


def documenter_restrictions_under(name: str, documents) -> list:
    workflow = harness_config.load_workflow(REPO_ROOT, name,
                                            config_with(documents))
    return [restriction for restriction
            in story_coordinator.stage_restrictions(workflow["stages"])
            if restriction.stage == DOCUMENTING]


@pytest.mark.parametrize("name", SHIPPED)
def test_one_configured_document_resolves_to_exactly_that_path(name):
    assert documenter_under(name, [DOCUMENT])[CONFINEMENT] == [DOCUMENT]
    assert [r.prefix for r in documenter_restrictions_under(name, [DOCUMENT])] \
        == [DOCUMENT]


@pytest.mark.parametrize("name", SHIPPED)
def test_two_configured_documents_resolve_to_both_in_configured_order(name):
    """A list-valued token splices its entries in place of the token entry."""
    both = [DOCUMENT, SECOND_DOCUMENT]
    assert documenter_under(name, both)[CONFINEMENT] == both
    assert documenter_under(name, list(reversed(both)))[CONFINEMENT] \
        == list(reversed(both))
    assert [r.prefix for r in documenter_restrictions_under(name, both)] == both


@pytest.mark.parametrize("name", SHIPPED)
@pytest.mark.parametrize("documents", [None, []], ids=["unset", "empty"])
def test_an_unset_or_empty_key_resolves_the_confinement_out(name, documents):
    """No entries at all rather than an empty string, and no restriction for
    the documenter. Its control is the two loads above, identical but for the
    key, which report one."""
    assert documenter_under(name, documents).get(CONFINEMENT, []) == []
    assert documenter_restrictions_under(name, documents) == []
    assert documenter_restrictions_under(name, [DOCUMENT]) != []


def test_this_repository_resolves_the_shipped_confinement_to_its_own_document():
    for name in SHIPPED:
        shipped = conftest.shipped_workflow(name=name)
        documenter = next(s for s in shipped["stages"] if s["name"] == DOCUMENTING)
        assert documenter[CONFINEMENT] == conftest.repository_config()[DOCS_KEY]
        assert "{{" not in json.dumps(shipped)


def test_a_key_outside_the_referable_set_is_still_refused_naming_both(tmp_path):
    """`branch_prefix` is a declared, set key and stays unreferable; the
    refusal lists exactly the two tokens a declaration may carry."""
    definition = raw_definition(RUN_WORKFLOW)
    stage_confined_to_the_documents(definition)[CONFINEMENT] = ["{{branch_prefix}}"]
    harness = conftest.materialize_workflow(definition, tmp_path / "harness")
    config = config_with([DOCUMENT])
    assert config.get("branch_prefix")

    with pytest.raises(harness_config.UnresolvedWorkflowToken) as raised:
        harness_config.load_workflow(harness, RUN_WORKFLOW, config)

    assert raised.value.tokens == ["branch_prefix"]
    (problem,) = raised.value.problems
    assert "{{tests_dir}}" in problem
    assert DOCS_TOKEN in problem
    assert set(harness_config.workflow_token_values({})) == {"tests_dir",
                                                              DOCS_KEY}


def test_the_planner_injection_lists_the_documenters_confinement():
    """In the workflow's own wording, beside the other stages' restrictions."""
    rendered = context_assembler.workflow_context(
        conftest.shipped_workflow(), RULES)["stage_path_restrictions"]
    wordings = [restriction.wording for restriction in
                story_coordinator.stage_restrictions(
                    conftest.shipped_workflow()["stages"])]
    documenters = [w for w in wordings if w.startswith(f"{DOCUMENTING} ")]
    assert documenters
    for wording in wordings:
        assert wording in rendered
    assert len({w.split(" ")[0] for w in wordings}) >= 3


# --------------------------------------------------------------------------
# A slash-less confinement prefix is one file
# --------------------------------------------------------------------------


def confinement(*prefixes: str):
    (first, *_) = story_coordinator.restrictions_on(
        {"name": DOCUMENTING, CONFINEMENT: list(prefixes)})
    return first


def test_a_file_prefix_governs_a_path_that_merely_extends_its_name():
    restriction = confinement(DOCUMENT)
    assert restriction.governs(f"{DOCUMENT}.bak")
    assert not restriction.governs(DOCUMENT)
    assert restriction.governs(APP)


def test_a_directory_prefix_keeps_its_directory_meaning():
    restriction = confinement(SUITE_DIR)
    assert not restriction.governs(f"{SUITE_DIR}test_app.py")
    assert not restriction.governs(f"{SUITE_DIR}unit/test_deep.py")
    assert restriction.governs(APP)
    assert restriction.governs(SUITE_DIR.rstrip("/") + "-more/test.py")


def test_the_two_senses_agree_with_grant_covers():
    """What a slash-less confinement admits is what a slash-less grant covers,
    so a plan-time grant and a run-time confinement cannot read one value two
    ways."""
    for value, path in ((DOCUMENT, DOCUMENT), (DOCUMENT, f"{DOCUMENT}.bak"),
                        (SUITE_DIR, f"{SUITE_DIR}x.py"), (SUITE_DIR, APP)):
        assert (not confinement(value).governs(path)) == \
            story_coordinator.grant_covers([value], path)


def test_a_baseline_pathspec_reads_a_file_prefix_as_that_file(tmp_path):
    """The capture side of the same reading: a pathspec built from the file
    prefix excludes the file and nothing sharing its name as a prefix, and one
    built from a directory prefix excludes what is beneath it."""
    specs = story_coordinator.baseline_pathspecs(
        story_coordinator.restrictions_on(
            {"name": DOCUMENTING, CONFINEMENT: [DOCUMENT]}))
    root = tmp_path / "pathspec-probe"
    write(root / DOCUMENT, "doc\n")
    write(root / f"{DOCUMENT}.bak", "bak\n")
    write(root / APP, "app\n")
    conftest.init_repository(root)
    listed = subprocess.run(["git", "ls-files", "--", *specs], cwd=root,
                            capture_output=True, text=True, check=True
                            ).stdout.split()
    assert f"{DOCUMENT}.bak" in listed
    assert APP in listed
    assert DOCUMENT not in listed


# --------------------------------------------------------------------------
# A documenter edit outside the documents is decided by the revert check
# --------------------------------------------------------------------------


def test_a_module_edit_the_suite_is_green_without_is_undone_and_recorded(target):
    code, runner, run_dir = drive(target, [PASS], [unforced_module_edit])

    assert code == 0, runner.calls
    record = read_json(run_dir / ARTIFACT)
    assert record["ran"] is True
    assert record["paths"] == [APP]
    assert record["permitted"] is False
    # The tree holds the baseline content, the stage's version is kept under
    # the discarded directory, and the document edit stands.
    assert content(run_dir, APP) == APP_AT_HEAD
    assert (run_dir / DECLARATION["discarded"] / APP).read_text(
        encoding="utf-8") == APP_WITH_HELPER
    assert content(run_dir, DOCUMENT) == DOCUMENT_UPDATED
    (event,) = documenter_events(run_dir, REVERTED_EVENT)
    assert APP in event["message"]
    assert documenter_events(run_dir, PERMITTED_EVENT) == []
    assert runner.calls[-1] == VERIFYING


def test_a_module_edit_the_suite_is_red_without_is_kept_and_permitted(target):
    """The control for the refusal above: the identical edit to the identical
    path, with a test that needs it. The test is granted by the story so it
    is not what the check decides; the module edit is, and reverting it alone
    turns the suite red."""
    append_to_story(target, story_grant(NEW_TEST))

    code, runner, run_dir = drive(target, [PASS], [forced_module_edit])

    assert code == 0, runner.calls
    record = read_json(run_dir / ARTIFACT)
    assert record["ran"] is True
    assert record["paths"] == [APP]
    assert record["permitted"] is True
    assert record["exit_code"] != 0
    assert content(run_dir, APP) == APP_WITH_HELPER
    (event,) = documenter_events(run_dir, PERMITTED_EVENT)
    assert APP in event["message"]
    assert documenter_events(run_dir, REVERTED_EVENT) == []
    (granted,) = documenter_events(run_dir, STORY_GRANT_EVENT)
    assert NEW_TEST in granted["message"]


def test_a_documenter_naming_only_the_documents_is_unaffected(target):
    """No revert check, no artifact, no event, and the tree as the stage left
    it. The two runs above are the control: the same machinery with a module
    beside the document decides it."""
    code, runner, run_dir = drive(target, [PASS], [only_the_document])

    assert code == 0, runner.calls
    assert not (run_dir / ARTIFACT).exists()
    assert documenter_events(run_dir, REVERTED_EVENT) == []
    assert documenter_events(run_dir, PERMITTED_EVENT) == []
    assert content(run_dir, DOCUMENT) == DOCUMENT_UPDATED
    assert runner.calls[-1] == VERIFYING


def test_a_documenter_creating_outside_the_documents_escalates(target):
    code, runner, run_dir = drive(target, [PASS], [creation_outside])

    assert code == 2
    assert runner.calls[-1] == DOCUMENTING
    (wording,) = [restriction.wording for restriction
                  in story_coordinator.stage_restrictions(runner.stages)
                  if restriction.stage == DOCUMENTING]
    summary = (run_dir / "escalation-summary.md").read_text(encoding="utf-8")
    for text in (events_log(run_dir), summary):
        assert DOCUMENTING in text
        assert NEW_MODULE in text
        assert DOCUMENT in text
        assert wording in text


def test_the_documenter_is_governed_by_its_declaration_alone():
    """Removing the declaration disables the check with no code change, and no
    stage name or repository path is written into the enforcement path."""
    source = Path(story_coordinator.__file__).read_text(encoding="utf-8")
    assert f'"{DOCUMENTING}"' not in source
    assert f"'{DOCUMENTING}'" not in source
    undeclared = {**DOCUMENTING_STAGE}
    undeclared.pop(CONFINEMENT)
    undeclared.pop("revert_check")
    assert story_coordinator.restrictions_on(undeclared) == []
    assert story_coordinator.restrictions_on(
        {**DOCUMENTING_STAGE, CONFINEMENT: [DOCUMENT]}) != []


# --------------------------------------------------------------------------
# The correction pass is granted the files its findings name
# --------------------------------------------------------------------------


@pytest.fixture
def pass_run(target):
    """A passing verdict carrying one finding naming EXAMPLE, then a pass on
    which the documenter edits that file and one no finding named."""
    return drive(target, [passing_with(FINDING), PASS],
                 [None, the_named_file_and_another])


def test_the_pass_record_names_the_documenter_and_the_attempt(pass_run):
    _, runner, run_dir = pass_run
    record = pass_record(run_dir)
    assert record["stage"] == DOCUMENTING
    assert record["attempt"] == 1
    assert [finding["path"] for finding in record["findings"]] == [EXAMPLE]
    assert runner.calls.count(DOCUMENTING) == 2


def test_the_named_file_is_exempt_and_the_other_is_decided(pass_run):
    code, _, run_dir = pass_run

    assert code == 0
    record = read_json(run_dir / ARTIFACT)
    assert record["paths"] == [OTHER]
    assert record["permitted"] is False
    (reverted,) = documenter_events(run_dir, REVERTED_EVENT)
    assert OTHER in reverted["message"]
    assert EXAMPLE not in reverted["message"]


def test_the_granted_edit_lands_and_the_other_is_undone(pass_run):
    _, _, run_dir = pass_run
    assert content(run_dir, EXAMPLE) == EXAMPLE_CORRECTED
    assert content(run_dir, OTHER) == OTHER_AT_HEAD
    assert (run_dir / DECLARATION["discarded"] / OTHER).read_text(
        encoding="utf-8") == OTHER_REWORDED


def test_the_grant_is_an_event_before_the_checks_and_reads_like_a_story_grant(
    pass_run,
):
    _, _, run_dir = pass_run
    history = history_of(run_dir)
    kinds = [entry.get("event") for entry in history]
    (grant,) = documenter_events(run_dir, GRANT_EVENT)
    assert EXAMPLE in grant["message"]
    assert DOCUMENTING in grant["message"]
    assert kinds.index(GRANT_EVENT) < kinds.index(REVERTED_EVENT)
    assert f"may change {EXAMPLE}" in events_log(run_dir)
    assert schema_validator.validate(history, HISTORY_SCHEMA) == []


def test_the_grant_is_one_file_and_not_its_directory(pass_run):
    """A finding grants the file it names: OTHER sits in the same directory
    and was decided, so the grant did not widen to `src/`."""
    _, _, run_dir = pass_run
    grants = story_coordinator.correction_pass_grants(
        run_dir, RAW["stages"], story_coordinator.load_state(run_dir),
        DOCUMENTING, 1)
    assert grants == [EXAMPLE]
    assert not story_coordinator.grant_covers(grants, OTHER)
    assert story_coordinator.grant_covers(grants, EXAMPLE)


def test_a_first_entry_before_the_pass_is_granted_nothing(target):
    """The control for the grant: the documenter's first invocation, before
    any pass, edits the same file and is decided on it."""
    code, _, run_dir = drive(target, [PASS], [the_named_file])

    assert code == 0
    record = read_json(run_dir / ARTIFACT)
    assert record["paths"] == [EXAMPLE]
    assert record["permitted"] is False
    assert documenter_events(run_dir, GRANT_EVENT) == []


def test_a_record_for_another_stage_or_attempt_grants_nothing(pass_run):
    _, _, run_dir = pass_run
    state = story_coordinator.load_state(run_dir)
    assert story_coordinator.correction_pass_grants(
        run_dir, RAW["stages"], state, DOCUMENTING, 1) == [EXAMPLE]
    assert story_coordinator.correction_pass_grants(
        run_dir, RAW["stages"], state, DOCUMENTING, 2) == []
    assert story_coordinator.correction_pass_grants(
        run_dir, RAW["stages"], state, VERIFYING, 1) == []


@pytest.fixture
def retried_after_pass(target):
    """A pass, then a failing verdict routing a retry that re-enters the
    documenter on attempt 2, where it edits the file the pass record named."""
    return drive(
        target,
        [passing_with(FINDING), failing_into(DOCUMENTING_CATEGORY), PASS],
        [None, the_named_file, the_named_file_again])


def test_a_documenter_re_entered_on_a_later_attempt_inherits_no_grant(
    retried_after_pass,
):
    code, runner, run_dir = retried_after_pass

    assert code == 0, runner.calls
    assert runner.calls.count(DOCUMENTING) == 3
    state = story_coordinator.load_state(run_dir)
    assert state.retry_count == 1
    assert pass_record(run_dir)["attempt"] == 1
    # One grant, on the pass; none on the retry.
    (grant,) = documenter_events(run_dir, GRANT_EVENT)
    assert EXAMPLE in grant["message"]
    # The retry's edit to the same file was decided and undone, and the
    # retry's own words are under the discarded directory. What the tree
    # holds afterwards is what the documenter's *first* invocation found: the
    # stage baseline is keyed by stage and first-seen content wins, so a
    # revert restores the tree from before any of the stage's edits, the
    # pass's included. That the pass's words do not survive a reverted retry
    # is the baseline rule and not this story's grant, which is bounded to the
    # pass it was conferred on.
    record = read_json(run_dir / ARTIFACT)
    assert record["paths"] == [EXAMPLE]
    assert record["permitted"] is False
    (reverted,) = documenter_events(run_dir, REVERTED_EVENT)
    assert EXAMPLE in reverted["message"]
    assert content(run_dir, EXAMPLE) == EXAMPLE_AT_HEAD
    assert (story_coordinator.stage_baseline_dir(
        run_dir, DECLARATION["baseline"], DOCUMENTING) / EXAMPLE).read_text(
            encoding="utf-8") == EXAMPLE_AT_HEAD
    assert (run_dir / DECLARATION["discarded"] / EXAMPLE).read_text(
        encoding="utf-8") == EXAMPLE_REWORDED_AGAIN


def test_the_pass_itself_did_grant_that_file(retried_after_pass):
    """The control for the inheritance above: three documenter invocations,
    and the documenter's events read grant, then reverted — the pass's edit
    to the file was exempt and the retry's was decided. Read in order off the
    history, so the one reverted event is shown to be the retry's rather than
    the pass's.

    The tree afterwards cannot be the witness, because the retry's revert
    restores the stage's first-seen baseline and the pass's words go with it.
    What shows the grant was acted on is that the pass's attempt wrote no
    revert-check record at all — its archive holds none, while the retry's
    attempt holds the live one — and that what was discarded is the retry's
    words rather than the pass's."""
    _, runner, run_dir = retried_after_pass
    assert runner.calls.count(DOCUMENTING) == 3
    ordered = [entry["event"] for entry in history_of(run_dir)
               if entry.get("stage") == DOCUMENTING
               and entry.get("event") in (GRANT_EVENT, REVERTED_EVENT,
                                          PERMITTED_EVENT)]
    assert ordered == [GRANT_EVENT, REVERTED_EVENT]
    assert pass_record(run_dir)["attempt"] == 1
    archived = story_coordinator.attempt_dir(run_dir, 1)
    assert archived.is_dir()
    assert not (archived / ARTIFACT).exists()
    assert (run_dir / ARTIFACT).is_file()
    assert (run_dir / DECLARATION["discarded"] / EXAMPLE).read_text(
        encoding="utf-8") == EXAMPLE_REWORDED_AGAIN
    assert content(run_dir, EXAMPLE) != EXAMPLE_REWORDED_AGAIN


# --------------------------------------------------------------------------
# A finding without a path is refused by both schemas
# --------------------------------------------------------------------------


def without_path(finding: dict) -> dict:
    return {key: value for key, value in finding.items() if key != "path"}


def test_a_verdict_whose_finding_names_no_path_fails_the_schema():
    assert schema_validator.validate(passing_with(FINDING), VERDICT_SCHEMA) == []
    problems = schema_validator.validate(passing_with(without_path(FINDING)),
                                         VERDICT_SCHEMA)
    assert problems
    assert any("path" in problem for problem in problems)


def test_a_pass_record_whose_finding_names_no_path_fails_the_schema(pass_run):
    _, _, run_dir = pass_run
    record = pass_record(run_dir)
    assert schema_validator.validate(record, CORRECTION_SCHEMA) == []
    broken = {**record, "findings": [without_path(f) for f in record["findings"]]}
    problems = schema_validator.validate(broken, CORRECTION_SCHEMA)
    assert problems
    assert any("path" in problem for problem in problems)


@pytest.mark.parametrize("schema, field", [
    (VERDICT_SCHEMA, "correctable_findings"),
    (CORRECTION_SCHEMA, "findings"),
], ids=["verdict", "pass record"])
def test_each_schema_says_what_naming_the_path_grants(schema, field):
    item = schema["properties"][field]["items"]
    assert "path" in item["required"]
    description = item["properties"]["path"]["description"].lower()
    assert "grant" in description
    assert "revert check" in description


# --------------------------------------------------------------------------
# The prompts
# --------------------------------------------------------------------------


def flowed(text: str) -> str:
    return " ".join(text.split())


def rendered_template(target_root: Path, template: str, workflow: str) -> str:
    """A shipped template rendered against the fixture target."""
    story_text = (target_root / ".harness" / "stories"
                  / f"{STORY_ID}.yaml").read_text(encoding="utf-8")
    run_dir = target_root / ".harness" / "runs" / STORY_ID
    run_dir.mkdir(parents=True, exist_ok=True)
    config = harness_config.load_config(target_root)
    context = context_assembler.build_context(
        story_text=story_text,
        story=story_parser.parse(story_text,
                                 schema_validator.load_schema("story")),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=REPO_ROOT,
        config=config,
        rules=RULES,
        workflow=harness_config.load_workflow(REPO_ROOT, workflow, config),
        retry_count=0,
    )
    return context_assembler.render(
        context_assembler.load_template(REPO_ROOT, template), context)


def test_the_documenter_prompt_on_a_pass_states_the_grant_and_the_check(pass_run):
    _, runner, _ = pass_run
    first, on_pass = runner.prompts[DOCUMENTING]
    paragraph = flowed(on_pass)
    assert "files the findings name are yours to change for this pass" in paragraph
    assert "architecture documents always are" in paragraph
    assert "put to the revert check" in paragraph
    assert "undone if the suite is green without it" in paragraph
    assert "approved story artifact is never edited" in paragraph
    # The same template carried the same words on the first entry: the
    # paragraph is the prompt's, and what the pass changes is the record
    # injected beneath it.
    assert "put to the revert check" in flowed(first)
    assert FINDING["finding"] in on_pass
    assert FINDING["finding"] not in first


def verifier_of(name: str) -> str:
    """The verifier template a shipped workflow names."""
    return next(stage for stage in raw_definition(name)["stages"]
                if "correction_pass" in stage)["prompt"]


def documenter_record_label(prompt: str) -> str:
    """The label introducing the documenter changed-files slot."""
    marker = "Documenter changed files"
    assert marker in prompt
    start = prompt.index(marker)
    return prompt[start:prompt.index(":", start + len(marker))]


#: What the slot used to say, written here so the check below is shown to
#: report it.
THE_OLD_LABEL = ("Documenter changed files (documenter's record — documentation "
                 "files created or modified by the documenter stage)")


def calls_them_documentation(label: str) -> bool:
    return "documentation" in flowed(label).lower()


def test_the_check_for_the_old_wording_reports_it():
    assert calls_them_documentation(THE_OLD_LABEL)


@pytest.mark.parametrize("name", SHIPPED)
def test_each_rendered_verifier_prompt_asks_for_path_and_names_every_file(
    name, target,
):
    rendered = flowed(rendered_template(target, verifier_of(name), name))
    assert "Give each finding a `path`" in rendered
    assert "the stage the pass enters at change that one file" in rendered
    assert "finding that names no path is refused by the schema" in rendered
    label = documenter_record_label(rendered)
    assert not calls_them_documentation(label)
    assert "every repository file" in flowed(label)
    assert "{{" not in rendered
