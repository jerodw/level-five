"""Independent validation for story-051: the documenter may not assert what
nothing can check.

An architecture document is tracked and permanent; the run directories the
documenter writes it from are not. So a stage can write a factual claim into
the repository whose only support is a file local to one machine. story-051's
answer is narrow and mechanical: text this run added to a configured
architecture document, naming a story with no completion commit reachable from
the run's base, in a block that also carries a quantity, is reported to the
verifier — and nothing routes on it.

Written from the story's acceptance criteria. The subjects are asserted at the
altitude each lives at:

  * **the reference and quantity decision** is a function of a repository and a
    diff, so it is driven directly — `claim_support_check` against target
    repositories built under `tmp_path`, one per case, with the merged/unmerged
    difference made by a real completion commit rather than by a stub;
  * **the routing, the injection and the declaration** are properties of a run,
    so they are driven by running the coordinator over a workflow built by
    `tests/conftest.py`'s builder. The declaration this story adds is an
    *input* to those questions, so the workflow carrying it is one this module
    builds; what this repository happens to deploy is not the subject and is
    not read for them;
  * **the prompts** are shipped artifacts and are read as shipped — but
    *rendered*, because the criteria are about what reaches the agent, and a
    template with an unresolved placeholder in it satisfies no criterion.

Every absence asserted here carries a demonstration that it can fail, written
beside the assertion it protects:

  * "a forward reference naming no story is not reported" sits beside the same
    paragraph with a story number written into it, which the same call
    reports;
  * "a claim about the story the run is landing is not reported" sits beside
    the same text checked for a different run, where it is reported;
  * "a claim about a merged story is not reported" sits beside the same
    document in a repository lacking that story's completion commit, and
    beside a commit that wears the completion subject without the marker —
    which `completion_commits` does not accept and which is therefore
    reported;
  * "text already at the base is not reported" sits beside the same sentence
    added by the run, which is;
  * "no retry is consumed and nothing is routed" sits beside a run of the same
    fixture that does consume one, so the counters are shown to be able to
    move;
  * "a workflow with no declaration writes no record" sits beside the same
    fixture under the declaration, which writes one;
  * "the check that could not run reports nothing" is a missing `reports` key
    asserted beside a run of the same repository where the key is present;
  * "the prompts said none of this before" is the same rendering of the same
    templates at this story's own baseline, resolved through the shared
    baseline resolution rather than against HEAD.

Nothing here invokes a model: every run goes through a fake agent runner, and
`no_model` turns the single call that would reach one into a failure.
"""
import json
import subprocess
from pathlib import Path

import pytest

import agent_runner
import context_assembler
import harness_config
import schema_validator
import story_coordinator
from agent_runner import AgentResult
from conftest import (BASELINE, ENDPOINT, repository_file_at,
                      revision_carrying)
import conftest

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]

STORY_ID = "story-001"
DEFAULT_BRANCH = "main"

#: The documents this fixture's target configures. Deliberately not the path
#: this repository configures for itself: what is scanned has to come from the
#: target's own `architecture_docs`, and a check that read a path written into
#: harness source would look green against a fixture that used the same path.
#: Two of them, so "every configured document, in the order configured" is a
#: statement with something behind it.
PRIMARY_DOC = "docs/DESIGN.md"
SECOND_DOC = "docs/HISTORY.md"
#: A document the target does not configure, for the same reason.
UNCONFIGURED_DOC = "docs/NOTES.md"

#: The workflow these runs execute, assembled by the builder in
#: `tests/conftest.py`. The declaration this story adds is what turns the check
#: on, so it is declared here — on the third stage, which is a documenting
#: stage because that is the shape the check exists for, not because this
#: repository deploys it there. The variants below move it and drop it.
#:
#: The artifact it names comes from the fixture rather than from this module:
#: `context_assembler` reads the record off the run directory by that fixed
#: name to fill a placeholder, which makes it a fact about the harness, and
#: `tests/conftest.py` is where the fixture keeps such names.
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
        claim_support={"result": conftest.CLAIM_SUPPORT_RESULT},
        schemas={conftest.DOCUMENTER_CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        clean_clone={"result": conftest.CLEAN_CLONE_RESULT,
                     "retry_stage": conftest.StageRef(0)},
        retry_routing={
            "documentation-defect": {
                "stage": conftest.StageRef(2),
                "when": "the defect is in the document itself"},
        }),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="claim-support-workflow",
)

STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, DOCUMENTING, VERIFYING = STAGE_NAMES

#: The stage that declares the check, found *by the declaration* rather than by
#: name, and the artifact read off that declaration — so this module writes
#: neither, exactly as the coordinator may write neither.
DECLARING_STAGE = next(stage for stage in WORKFLOW["stages"]
                       if "claim_support" in stage)
DECLARING_STAGE_NAME = DECLARING_STAGE["name"]
ARTIFACT = DECLARING_STAGE["claim_support"]["result"]

DOC_CATEGORY, DOC_ROUTE = next(
    (category, route) for category, route
    in next(s for s in WORKFLOW["stages"]
            if "on_failure" in s)["on_failure"]["retry_routing"].items())

SCHEMA_STEM = "claim-support-result"
SCHEMA = schema_validator.load_schema(SCHEMA_STEM)
HISTORY_SCHEMA = schema_validator.load_schema("execution-history")

RULES = harness_config.load_rules(REPO_ROOT)
MAX_RETRIES = RULES["max_retries"]

#: A string no template prints and no fixture document holds, so finding it in
#: a record or a rendered prompt is finding content that travelled there.
PLANTED = "PLANTED_BY_THE_DOCUMENTER"

#: The story this repository's own story-049 wrote an unsupportable figure
#: about, and the figure itself. Used only to locate the sentence in the
#: history below — the sentence is read, never retyped.
STORY_049_FIGURE = "converted 8 of 22 modules"
STORY_049_SUBJECT = "story-048"
CORRECTED_DOC = ".harness/docs/ARCHITECTURE.md"

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


def failing(category: str) -> dict:
    return {
        "status": "failed",
        "blocking_issues": [{
            "severity": "high",
            "issue": "the document describes something else",
            "location": f"{PRIMARY_DOC}:1",
            "required_behavior": "the sample behavior exists",
        }],
        "unverified": [],
        "retry_recommended": True,
        "retry_target": category,
    }


STORY = f"""\
story:
  id: {STORY_ID}
  title: Sample story for coordinator tests
  description: |
    A stand-in story used to exercise the workflow deterministically.

tasks:
  - do the sample work

acceptance_criteria:
  - the sample behavior exists
  - existing behavior is preserved

scope:
  modify:
    - src/
  do_not_modify:
    - rules/

verification_requirements:
  - confirm the sample behavior

constraints:
  - preserve existing behavior
"""

CONFIG = """\
workflow: {workflow}
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
test_command: {test_command}
tests_dir: tests/
{documents}"""


def configured_documents(documents) -> str:
    """The `architecture_docs` block a target's configuration carries.

    An empty sequence produces no key at all, which is the target the
    could-not-run branch exists for — as distinct from a key with an empty
    list, which is a different thing to write and not what this fixture means.
    """
    if not documents:
        return ""
    listed = "".join(f"  - {document}\n" for document in documents)
    return f"architecture_docs:\n{listed}"


# --------------------------------------------------------------------------
# No model, for every test in this file
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    """Turn the one call that would reach a model into a failure."""
    real = agent_runner.subprocess.Popen

    def guarded(command, *args, **kwargs):
        first = command[0] if isinstance(command, (list, tuple)) else command
        if str(first).endswith("claude"):
            raise AssertionError("a model was invoked")
        return real(command, *args, **kwargs)

    monkeypatch.setattr(agent_runner.subprocess, "Popen", guarded)


def test_the_no_model_guard_fires_when_a_model_is_invoked(tmp_path):
    """The control for the guard every other test in this file runs under."""
    with pytest.raises(AssertionError, match="a model was invoked"):
        agent_runner.run_agent("prompt", stage=WRITING, cwd=tmp_path,
                               log_path=tmp_path / "agent.log")


# --------------------------------------------------------------------------
# A target repository whose base and whose history a test decides
# --------------------------------------------------------------------------


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=check)


def build_target(root: Path, *, workflow: str | None = None,
                 test_command: str = "echo tests-ok",
                 documents=(PRIMARY_DOC,),
                 at_base: dict[str, str] | None = None,
                 completed: dict[str, str] | None = None) -> Path:
    """A target repository, its base holding `at_base` and `completed`.

    `at_base` is what each document already says at the branch point, so a
    test can distinguish text a run added from text that was already there.
    `completed` maps a story id to a title and gives that story a completion
    commit reachable from the base — composed through the coordinator's own
    `completion_commit_subject` and marker rather than through a second
    spelling of the shape, since whether such a commit counts is exactly what
    is under test.
    """
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", "docs", "src", "tests"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    write(root / ".gitignore", ".harness/runs/\n.harness/logs/\n")
    write(root / ".harness" / "config.yaml",
          CONFIG.format(workflow=workflow or WORKFLOW["name"],
                        test_command=test_command,
                        documents=configured_documents(documents)))
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    for document in (PRIMARY_DOC, SECOND_DOC, UNCONFIGURED_DOC):
        write(root / document,
              (at_base or {}).get(document, "# A document\n\nIt describes.\n"))
    write(root / "src" / "app.py", "print('hello')\n")
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    git(root, "branch", "-M", DEFAULT_BRANCH)
    for story_id, title in (completed or {}).items():
        git(root, "commit", "-q", "--allow-empty", "-m",
            f"{story_coordinator.completion_commit_subject(story_id, title)}\n"
            f"\n{story_coordinator.COMPLETION_COMMIT_MARKER}")
    return root


def add_to_document(root: Path, text: str, document: str = PRIMARY_DOC) -> None:
    """Text a run added: appended to the working tree, left uncommitted.

    Which is what the check sees during a run — the stage has written the
    document and the run has not committed anything yet.
    """
    path = root / document
    path.write_text(path.read_text(encoding="utf-8") + "\n" + text + "\n",
                    encoding="utf-8")


def check(root: Path, *, base: str = DEFAULT_BRANCH, story_id: str = STORY_ID,
          run_dir: Path | None = None):
    """Run the check the way the coordinator runs it, and read back its record.

    Returns the result object and the record on disk, because the criteria are
    about both: the coordinator computes a result and the run directory is
    where the verifier meets it.
    """
    run_dir = run_dir or (root / ".harness" / "runs" / story_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    result = story_coordinator.claim_support_check(
        run_dir, root, harness_config.load_config(root), ARTIFACT, base,
        story_id)
    record = json.loads((run_dir / ARTIFACT).read_text(encoding="utf-8"))
    return result, record


def reports_of(root: Path, **kwargs) -> list[dict]:
    _, record = check(root, **kwargs)
    return record["reports"]


# --------------------------------------------------------------------------
# The harness roots and the fake runner
# --------------------------------------------------------------------------


def variant(workflow: dict, name: str) -> dict:
    """A mutable copy of the built definition, renamed."""
    copy = json.loads(json.dumps(workflow))
    copy["name"] = name
    return copy


def without_declaration(workflow: dict) -> dict:
    """The same definition with the declaration this story adds removed.

    Derived from the definition above rather than written out a second time,
    so the two differ in exactly the declaration and in nothing else.
    """
    copy = variant(workflow, "no-claim-support")
    for stage in copy["stages"]:
        stage.pop("claim_support", None)
    return copy


def declared_on(workflow: dict, stage_name: str, name: str) -> dict:
    """The same definition with the declaration moved to `stage_name`."""
    copy = variant(workflow, name)
    declaration = None
    for stage in copy["stages"]:
        declaration = stage.pop("claim_support", declaration)
    for stage in copy["stages"]:
        if stage["name"] == stage_name:
            stage["claim_support"] = declaration
    return copy


NO_DECLARATION = without_declaration(WORKFLOW)
DECLARED_EARLIER = declared_on(WORKFLOW, VALIDATING, "claim-support-earlier")

#: The verifying stage's template, plus the one field the fixture's default
#: template does not list. `tests/conftest.py` says in as many words that a
#: module needing a field it does not declare supplies its own template, and
#: the placeholder is spelled here because a token a template must carry is
#: not derivable from anything: it *is* the name the assembler fills.
VERIFYING_TEMPLATE = (
    conftest.built_stage_prompt(conftest.VERIFYING_STAGE)
    + "claim_support_result:\n{{claim_support_result}}\n"
)


def materialize(workflow: dict, root: Path) -> Path:
    return conftest.materialize_workflow(
        workflow, root,
        prompts={conftest.VERIFYING_STAGE: VERIFYING_TEMPLATE})


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    return materialize(WORKFLOW, tmp_path / "claim-support-harness")


class Runner:
    """A fake agent runner: each stage writes the artifacts it declares.

    The documenting stage appends `documented` to the primary document, so a
    test chooses what claim this run's document gains; `second` does the same
    for the second configured document when a test needs one.
    """

    def __init__(self, target_root: Path, verdicts: list | None = None, *,
                 documented: str = "The harness runs four stages.",
                 second: str | None = None):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.verdicts = list(verdicts or [PASS])
        self.documented = documented
        self.second = second
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None):
        self.calls.append(stage)
        if stage == WRITING:
            write(self.target_root / "src" / "app.py",
                  "print('hello')\n# the story's change\n")
            write_json(self.run_dir / conftest.CHANGED_FILES,
                       {"modified": ["src/app.py"], "created": [], "deleted": []})
            write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY, "Did the work.\n")
        elif stage == VALIDATING:
            write_json(self.run_dir / conftest.TEST_RESULTS, {
                "status": "passed", "tests_written": 1, "tests_run": 1,
                "tests_passed": 1, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES, {
                "modified": [], "created": ["tests/test_app.py"], "deleted": [],
            })
        elif stage == DOCUMENTING:
            touched = [PRIMARY_DOC]
            add_to_document(self.target_root, self.documented)
            if self.second is not None:
                add_to_document(self.target_root, self.second, SECOND_DOC)
                touched.append(SECOND_DOC)
            write(self.run_dir / conftest.DOCUMENTATION_REPORT,
                  "# Documentation report\n\nWrote it.\n")
            write_json(self.run_dir / conftest.DOCUMENTER_CHANGED_FILES, {
                "modified": touched, "created": [], "deleted": [],
            })
        elif stage == VERIFYING:
            verdict = self.verdicts.pop(0) if len(self.verdicts) > 1 \
                else self.verdicts[0]
            verdict = conftest.answering_guidance(verdict, self.run_dir)
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, verdict)
            if verdict["status"] == "failed":
                write_json(self.run_dir / conftest.RETRY_GUIDANCE, {
                    "current_focus": [{
                        "focus": "fix what the verdict named",
                        "satisfied_when": "what the verdict named is fixed",
                    }],
                    "preserve_behavior": ["existing behavior"],
                    "retry_scope": [PRIMARY_DOC],
                })
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path) -> Path:
    return target_root / ".harness" / "runs" / STORY_ID


def record_of(target_root: Path) -> dict:
    return json.loads((run_dir_of(target_root) / ARTIFACT).read_text(
        encoding="utf-8"))


def history_of(target_root: Path) -> list[dict]:
    return json.loads((run_dir_of(target_root) / "execution-history.json")
                      .read_text(encoding="utf-8"))


def stream_of(target_root: Path) -> list[tuple[str, str | None]]:
    return [(entry["event"], entry.get("stage"))
            for entry in history_of(target_root)]


def claim_entries(target_root: Path) -> list[dict]:
    """The history entries this check appended, found by the artifact they name.

    By the artifact rather than by the event kind, so this module writes no
    event name of its own — the artifact is the one the declaration named.
    """
    return [entry for entry in history_of(target_root)
            if ARTIFACT in (entry.get("artifacts") or [])]


def prompt_of(target_root: Path, stage: str, attempt: int) -> str:
    return (run_dir_of(target_root) /
            story_coordinator.prompt_file(stage, attempt)).read_text(
                encoding="utf-8")


def state_of(target_root: Path) -> dict:
    return json.loads((run_dir_of(target_root) / "state.json").read_text(
        encoding="utf-8"))


# --------------------------------------------------------------------------
# The schema
#
# Shipped-artifact readings: the criterion is that *this repository* ships the
# schema and lists it, which only what it ships can answer.
# --------------------------------------------------------------------------


def test_the_schema_is_shipped_and_listed_in_the_manifest():
    assert (REPO_ROOT / "schemas" / f"{SCHEMA_STEM}.schema.json").is_file()
    assert SCHEMA_STEM in schema_validator.shipped_schemas(REPO_ROOT)
    manifest = json.loads((REPO_ROOT / "schemas" / "manifest.json").read_text(
        encoding="utf-8"))
    assert SCHEMA_STEM in manifest["schemas"]


def test_the_schema_uses_only_keywords_the_shipped_validator_supports():
    """A schema written in keywords the validator ignores would validate
    everything, which is the failure mode this repository's own validator
    reports rather than hides."""
    assert schema_validator.unsupported_keywords(SCHEMA) == []


def test_the_schema_requires_the_field_that_says_whether_the_check_ran():
    """`ran` is the field the whole distinction rests on, so a record without
    it is not a record. The control is the same call with it present."""
    assert schema_validator.validate({}, SCHEMA) != []
    assert schema_validator.validate({"ran": True, "reports": []}, SCHEMA) == []


# --------------------------------------------------------------------------
# What the check reports: the reference, the quantity, and the base
# --------------------------------------------------------------------------


def test_an_added_claim_about_a_story_with_no_merged_work_is_reported(tmp_path):
    """The report names the document, the story with no merged work, and the
    added text the report is about."""
    root = build_target(tmp_path / "reported")
    add_to_document(root, f"{PLANTED}: story-048 converted 5 of 22 modules.")

    result, record = check(root)

    assert record["ran"] is True
    assert record["base"] == DEFAULT_BRANCH
    assert record["story_id"] == STORY_ID
    assert record["documents"] == [PRIMARY_DOC]
    assert len(record["reports"]) == 1
    report = record["reports"][0]
    assert report["document"] == PRIMARY_DOC
    assert report["stories"] == ["story-048"]
    assert PLANTED in report["text"] and "converted 5 of 22" in report["text"]
    assert schema_validator.validate(record, SCHEMA) == []
    assert result.ran is True


def test_story_049s_own_sentence_is_reported_when_a_run_adds_it(tmp_path):
    """The case this story exists for, read out of this repository's history
    rather than retyped: the sentence as `.harness/docs/ARCHITECTURE.md`
    carried it before the commit that corrected the figure — located by
    searching the document's history for the figure rather than by a pinned
    sha, which a rebase leaves unreachable in a clean clone.

    The figure and its subject are searched for *together on one line*, which
    is what makes the sentence rather than the phrase the thing found: the
    document now describes this very check and quotes the figure while writing
    about other stories, so a search for the figure alone answers with today's
    revision and a sentence this test is not about.
    """
    revision = revision_carrying(CORRECTED_DOC, STORY_049_FIGURE,
                                 STORY_049_SUBJECT, repo=REPO_ROOT)
    document = repository_file_at(CORRECTED_DOC, revision=revision,
                                  repo=REPO_ROOT)
    sentences = [line for line in document.splitlines()
                 if STORY_049_FIGURE in line and STORY_049_SUBJECT in line]
    assert len(sentences) == 1, "the figure this story is about moved"
    sentence = sentences[0]

    root = build_target(tmp_path / "story-049")
    add_to_document(root, sentence)

    reports = reports_of(root)

    assert len(reports) == 1
    assert reports[0]["document"] == PRIMARY_DOC
    assert STORY_049_SUBJECT in reports[0]["stories"]
    assert STORY_049_FIGURE in reports[0]["text"]
    assert sentence in reports[0]["text"]


def test_a_forward_reference_naming_no_story_is_not_reported(tmp_path):
    """The distinction the story lives or dies on, run rather than read.

    A forward reference is invisible because a story number does not exist
    until the story is planned — so the control is the same paragraph with a
    number in it, which the same call reports. Without the control, "not
    reported" would hold equally against a check that reported nothing.
    """
    forward = "The next story in this line will carry 3 more modules."
    numbered = "story-052 will carry 3 more modules."

    quiet = build_target(tmp_path / "forward")
    add_to_document(quiet, forward)
    loud = build_target(tmp_path / "numbered")
    add_to_document(loud, numbered)

    assert reports_of(quiet) == []
    # The control: the same quantity, the same tense, one story number.
    assert [report["stories"] for report in reports_of(loud)] == [["story-052"]]


def test_a_claim_about_the_story_the_run_is_landing_is_not_reported(tmp_path):
    """Exempt outright, whatever quantities it carries — its work is unmerged
    by definition while it is landing.

    The control is the same text checked for a *different* run: one story id
    apart, and the same sentence is reported. So the exemption is the run's own
    id rather than something about the sentence.
    """
    root = build_target(tmp_path / "landing")
    add_to_document(root, f"{STORY_ID} converts 5 of 22 modules and adds 3 checks.")

    assert reports_of(root) == []
    assert reports_of(root, story_id="story-999")[0]["stories"] == [STORY_ID]


def test_a_claim_about_a_story_with_a_completion_commit_is_not_reported(tmp_path):
    """git can support it, so nothing is reported.

    The control is the same document in a repository whose history lacks that
    completion commit, where the same sentence is reported.
    """
    sentence = "story-048 converted 5 of 22 modules."
    merged = build_target(tmp_path / "merged",
                          completed={"story-048": "Convert the modules"})
    add_to_document(merged, sentence)
    unmerged = build_target(tmp_path / "unmerged")
    add_to_document(unmerged, sentence)

    assert reports_of(merged) == []
    assert [report["stories"] for report in reports_of(unmerged)] == \
        [["story-048"]]


def test_the_merged_decision_is_the_one_completion_commits_makes(tmp_path):
    """Not a second spelling of merged: a commit wearing the completion
    subject *without* the marker is not finished work, `completion_commits`
    says so, and the claim is still reported."""
    sentence = "story-048 converted 5 of 22 modules."
    root = build_target(tmp_path / "subject-only")
    git(root, "commit", "-q", "--allow-empty", "-m",
        story_coordinator.completion_commit_subject("story-048",
                                                    "Convert the modules"))
    add_to_document(root, sentence)

    assert story_coordinator.completion_commits(
        root, DEFAULT_BRANCH, "story-048") == []
    assert [report["stories"] for report in reports_of(root)] == [["story-048"]]

    # And where that reader does report finished work, the claim is not.
    proper = build_target(tmp_path / "proper",
                          completed={"story-048": "Convert the modules"})
    add_to_document(proper, sentence)
    assert story_coordinator.completion_commits(
        proper, DEFAULT_BRANCH, "story-048") != []
    assert reports_of(proper) == []


def test_text_this_run_did_not_add_is_not_reported(tmp_path):
    """The claims already in the document at the base are not re-reported by
    every run.

    The control is the same sentence added by the run, which is reported — so
    "not reported" is about *when* the text arrived rather than about the
    sentence being invisible to the check.

    The quiet run adds a line of its own beside the claim rather than leaving
    the document untouched: an untouched document has no diff at all, and the
    absence would then hold for a check that could not tell added text from
    the text it sits next to.
    """
    sentence = "story-048 converted 5 of 22 modules."
    already = build_target(tmp_path / "already",
                           at_base={PRIMARY_DOC: f"# A document\n\n{sentence}\n"})
    add_to_document(already, "This run says nothing about another story.")
    added = build_target(tmp_path / "added")
    add_to_document(added, sentence)

    assert reports_of(already) == []
    assert len(reports_of(added)) == 1


def test_only_the_added_text_of_a_document_that_already_makes_one_is_reported(
    tmp_path,
):
    """The two halves in one document and one run: an unsupportable claim at
    the base and another added on top. Exactly the added one is reported."""
    at_base = ("# A document\n\nstory-040 converted 9 of 22 modules.\n")
    root = build_target(tmp_path / "mixed", at_base={PRIMARY_DOC: at_base})
    add_to_document(root, "story-048 converted 5 of 22 modules.")

    reports = reports_of(root)

    assert [report["stories"] for report in reports] == [["story-048"]]
    assert "story-040" not in reports[0]["text"]


def test_a_reference_with_no_quantity_beside_it_is_not_reported(tmp_path):
    """A quantity is half the shape. The control is the same sentence with a
    figure in it."""
    quiet = build_target(tmp_path / "no-quantity")
    add_to_document(quiet, "story-048 was the story that motivated this.")
    loud = build_target(tmp_path / "with-quantity")
    add_to_document(loud, "story-048 was the story that motivated 2 of these.")

    assert reports_of(quiet) == []
    assert len(reports_of(loud)) == 1


def test_the_digits_of_the_story_number_are_not_themselves_a_quantity(tmp_path):
    """`story-048` is a name, and the digits in it are part of that name. The
    control is the same sentence with one figure added outside the name."""
    named = build_target(tmp_path / "just-the-name")
    add_to_document(named, "The lesson of story-048 and story-049 is attribution.")
    counted = build_target(tmp_path / "a-real-count")
    add_to_document(counted,
                    "The lesson of story-048 and story-049 is 1 of attribution.")

    assert reports_of(named) == []
    assert len(reports_of(counted)) == 1


def test_a_reference_and_a_quantity_in_different_paragraphs_are_not_one_claim(
    tmp_path,
):
    """A block is a paragraph of added prose: two paragraphs apart are two
    things said, not one claim. The control is the same two sentences in one
    paragraph."""
    apart = build_target(tmp_path / "apart")
    add_to_document(apart, "story-048 is the motivating case.\n\nIt took 3 tries.")
    together = build_target(tmp_path / "together")
    add_to_document(together, "story-048 is the motivating case. It took 3 tries.")

    assert reports_of(apart) == []
    assert len(reports_of(together)) == 1


def test_every_configured_document_is_scanned_and_no_other(tmp_path):
    """The documents come from the target's configuration: both configured
    ones are scanned, in the order configured, and a document the target does
    not configure is not — even carrying the same sentence."""
    sentence = "story-048 converted 5 of 22 modules."
    root = build_target(tmp_path / "two-docs",
                        documents=(PRIMARY_DOC, SECOND_DOC))
    for document in (PRIMARY_DOC, SECOND_DOC, UNCONFIGURED_DOC):
        add_to_document(root, sentence, document)

    _, record = check(root)

    assert record["documents"] == [PRIMARY_DOC, SECOND_DOC]
    assert [report["document"] for report in record["reports"]] == \
        [PRIMARY_DOC, SECOND_DOC]
    # The control for the absence: the unconfigured document holds the same
    # sentence and appears nowhere, so this is a statement about configuration
    # rather than about a sentence the check cannot see.
    assert UNCONFIGURED_DOC not in json.dumps(record)
    assert sentence in (root / UNCONFIGURED_DOC).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The check reports; it never judges
# --------------------------------------------------------------------------


@pytest.mark.parametrize("figure", ["8", "5", "12"])
def test_varying_the_reported_figure_leaves_the_report_unchanged(tmp_path, figure):
    """The right value, the value story-049 wrote, and a third wrong one: the
    check never asks which is correct, only whether anything tracked could
    say. The report differs in the figure and in nothing else."""
    sentence = f"story-048 converted {figure} of 22 modules."
    root = build_target(tmp_path / f"figure-{figure}")
    add_to_document(root, sentence)

    reports = reports_of(root)

    assert len(reports) == 1
    assert reports[0]["document"] == PRIMARY_DOC
    assert reports[0]["stories"] == ["story-048"]
    assert reports[0]["text"] == sentence


def test_two_texts_of_opposite_meaning_and_the_same_shape_report_alike(tmp_path):
    """No branch depends on what the added text says beyond the reference and
    the presence of a quantity — an assertion and its denial are reported
    identically."""
    asserted = build_target(tmp_path / "asserted")
    add_to_document(asserted, "story-048 converted 5 of 22 modules.")
    denied = build_target(tmp_path / "denied")
    add_to_document(denied, "story-048 did not convert 5 of 22 modules.")

    for reports in (reports_of(asserted), reports_of(denied)):
        assert len(reports) == 1
        assert reports[0]["stories"] == ["story-048"]


# --------------------------------------------------------------------------
# A check that could not run says so, with the reason
# --------------------------------------------------------------------------


def test_a_target_configuring_no_documents_is_a_check_that_could_not_run(tmp_path):
    """Not a document with no unsupportable claims: `ran` is false, a reason
    says why, and there is no `reports` key to read as "nothing to report".

    The control is the same repository with one document configured, where the
    key is present and the check has actually looked.
    """
    silent = build_target(tmp_path / "no-docs", documents=())
    add_to_document(silent, "story-048 converted 5 of 22 modules.")

    _, record = check(silent)

    assert record["ran"] is False
    assert "reports" not in record
    assert record["reason"].strip()
    assert schema_validator.validate(record, SCHEMA) == []

    configured = build_target(tmp_path / "one-doc")
    add_to_document(configured, "story-048 converted 5 of 22 modules.")
    _, present = check(configured)
    assert present["ran"] is True and present["reports"] != []


def test_a_base_that_does_not_resolve_is_a_check_that_could_not_run(tmp_path):
    """There is no horizon to ask what was added or what has merged, so the
    record says that rather than reporting nothing.

    The control is the same repository at a base that does resolve.
    """
    root = build_target(tmp_path / "no-base")
    add_to_document(root, "story-048 converted 5 of 22 modules.")

    _, record = check(root, base="no-such-branch")

    assert record["ran"] is False
    assert "reports" not in record
    assert "no-such-branch" in record["reason"]
    assert schema_validator.validate(record, SCHEMA) == []

    _, resolved = check(root)
    assert resolved["ran"] is True and resolved["reports"] != []


def test_a_git_diff_that_fails_is_a_check_that_could_not_run(tmp_path, monkeypatch):
    """The third stopping condition, and the same treatment.

    The control is the same repository with the same call unpatched.
    """
    root = build_target(tmp_path / "diff-fails")
    add_to_document(root, "story-048 converted 5 of 22 modules.")

    real = story_coordinator._git

    def failing_diff(target_root, *args):
        if args and args[0] == "diff":
            return subprocess.CompletedProcess(
                args=list(args), returncode=128, stdout="",
                stderr="fatal: this diff could not be taken")
        return real(target_root, *args)

    monkeypatch.setattr(story_coordinator, "_git", failing_diff)
    _, record = check(root)

    assert record["ran"] is False
    assert "reports" not in record
    assert "fatal: this diff could not be taken" in record["reason"]
    assert schema_validator.validate(record, SCHEMA) == []

    monkeypatch.undo()
    _, unpatched = check(root)
    assert unpatched["ran"] is True and unpatched["reports"] != []


# --------------------------------------------------------------------------
# The declaration is what drives the check
# --------------------------------------------------------------------------


def test_a_run_records_the_check_after_the_stage_that_declares_it(
    tmp_path, harness_root,
):
    root = build_target(tmp_path / "declared")
    runner = Runner(root, documented=f"{PLANTED}: story-048 converted 5 of 22.")

    assert story_coordinator.run_story(STORY_ID, harness_root, root, runner) == 0

    entries = claim_entries(root)
    assert len(entries) == 1
    assert entries[0]["stage"] == DECLARING_STAGE_NAME

    events = stream_of(root)
    position = [index for index, entry in enumerate(history_of(root))
                if ARTIFACT in (entry.get("artifacts") or [])][0]
    assert events.index(("stage-started", DECLARING_STAGE_NAME)) < position
    assert position < events.index(("stage-completed", DECLARING_STAGE_NAME))
    assert (run_dir_of(root) / ARTIFACT).is_file()
    assert schema_validator.validate(history_of(root), HISTORY_SCHEMA) == []


def test_a_workflow_that_moves_the_declaration_runs_the_check_after_that_stage(
    tmp_path,
):
    """The stage the check runs after is read off the workflow: the same
    coordinator, the same fixture, the declaration one stage earlier."""
    harness = materialize(DECLARED_EARLIER, tmp_path / "earlier-harness")
    root = build_target(tmp_path / "earlier-target",
                        workflow=DECLARED_EARLIER["name"])

    assert story_coordinator.run_story(
        STORY_ID, harness, root, Runner(root)) == 0

    moved = next(stage["name"] for stage in DECLARED_EARLIER["stages"]
                 if "claim_support" in stage)
    assert moved != DECLARING_STAGE_NAME
    entries = claim_entries(root)
    assert len(entries) == 1 and entries[0]["stage"] == moved

    events = stream_of(root)
    position = [index for index, entry in enumerate(history_of(root))
                if ARTIFACT in (entry.get("artifacts") or [])][0]
    assert events.index(("stage-started", moved)) < position
    assert position < events.index(("stage-started", DECLARING_STAGE_NAME))


def test_a_workflow_with_no_declaration_writes_no_record_and_runs_the_same(
    tmp_path, harness_root,
):
    """The control for the two above, and the criterion in its own right: the
    check exists because a stage declares it.

    "No record" is asserted beside the same fixture under the declaration,
    which writes one — and the two event streams are compared with this
    check's own entry removed, so "otherwise unchanged" is a comparison rather
    than an assumption.
    """
    harness = materialize(NO_DECLARATION, tmp_path / "undeclared-harness")
    plain = build_target(tmp_path / "undeclared-target",
                         workflow=NO_DECLARATION["name"])
    declared = build_target(tmp_path / "declared-target")
    documented = f"{PLANTED}: story-048 converted 5 of 22."

    assert story_coordinator.run_story(
        STORY_ID, harness, plain, Runner(plain, documented=documented)) == 0
    assert story_coordinator.run_story(
        STORY_ID, harness_root, declared,
        Runner(declared, documented=documented)) == 0

    assert not (run_dir_of(plain) / ARTIFACT).exists()
    assert claim_entries(plain) == []
    assert (run_dir_of(declared) / ARTIFACT).is_file()
    assert claim_entries(declared) != []

    without = [entry for entry in history_of(declared)
               if ARTIFACT not in (entry.get("artifacts") or [])]
    assert [(entry["event"], entry.get("stage")) for entry in without] == \
        stream_of(plain)
    assert state_of(plain)["status"] == state_of(declared)["status"]


# --------------------------------------------------------------------------
# The record reaches the verifier, and nothing routes on it
# --------------------------------------------------------------------------


def test_the_record_reaches_the_verifiers_rendered_prompt(tmp_path, harness_root):
    """A distinctive string planted in the reported text, found in the record
    on disk and in the prompt the run rendered for the stage that judges.

    The planted string alone does not settle it, and asserting only that would
    be looking in the wrong place: the documenter also *wrote* that string into
    the document, and the diff of the documenter's own work reaches the same
    prompt by a route that has nothing to do with this record — so the
    assertion would hold with the injection removed entirely. What only the
    injection can put in the prompt is the record's own body, so that is what
    is asserted, with the planted string as the statement that the body reached
    it whole rather than truncated.
    """
    root = build_target(tmp_path / "injected")
    runner = Runner(root,
                    documented=f"{PLANTED}: story-048 converted 5 of 22 modules.")

    assert story_coordinator.run_story(STORY_ID, harness_root, root, runner) == 0

    record = record_of(root)
    assert record["ran"] is True
    assert PLANTED in record["reports"][0]["text"]
    assert schema_validator.validate(record, SCHEMA) == []

    prompt = prompt_of(root, VERIFYING, 1)
    on_disk = (run_dir_of(root) / ARTIFACT).read_text(encoding="utf-8")
    assert on_disk.strip() in prompt
    assert PLANTED in prompt
    assert context_assembler.PLACEHOLDER.search(prompt) is None


def test_a_run_with_nothing_to_report_carries_a_record_that_reports_nothing(
    tmp_path, harness_root,
):
    """The control for the injection above: the same fixture whose documenter
    writes a sentence naming no story. The record still reaches the prompt,
    the planted string does not, and the record says the check ran."""
    root = build_target(tmp_path / "quiet")
    assert story_coordinator.run_story(
        STORY_ID, harness_root, root,
        Runner(root, documented="The next story in this line adds 1 check.")) == 0

    record = record_of(root)
    assert record["ran"] is True and record["reports"] == []

    prompt = prompt_of(root, VERIFYING, 1)
    assert PLANTED not in prompt
    assert '"ran": true' in prompt
    assert context_assembler.PLACEHOLDER.search(prompt) is None


def test_a_reported_claim_routes_exactly_as_a_run_without_one(
    tmp_path, harness_root,
):
    """The check routes nothing and escalates nothing.

    Two runs of the same fixture differing only in what the documenter wrote:
    same exit code, same stages invoked, same event stream, same retry count,
    same self-route count, and no retry history in either. That the two runs
    really differ is asserted first, so this is not two identical runs
    compared with each other.
    """
    reported = build_target(tmp_path / "routes-reported")
    quiet = build_target(tmp_path / "routes-quiet")
    loud_runner = Runner(reported,
                         documented="story-048 converted 5 of 22 modules.")
    quiet_runner = Runner(quiet, documented="The harness runs four stages.")

    assert story_coordinator.run_story(
        STORY_ID, harness_root, reported, loud_runner) == 0
    assert story_coordinator.run_story(
        STORY_ID, harness_root, quiet, quiet_runner) == 0

    # The premise: one run reported a claim and the other did not.
    assert record_of(reported)["reports"] != []
    assert record_of(quiet)["reports"] == []

    assert loud_runner.calls == quiet_runner.calls == STAGE_NAMES
    assert stream_of(reported) == stream_of(quiet)
    for root in (reported, quiet):
        state = state_of(root)
        assert state["status"] == "completed"
        assert state["retry_count"] == 0
        assert state.get("self_route_count", 0) == 0
        assert not (run_dir_of(root) / "retry-history.json").exists()


def test_a_retry_and_a_retry_history_entry_are_things_this_run_could_have_had(
    tmp_path, harness_root,
):
    """The control for the assertion above: the same fixture, routed by a
    verdict rather than by a report, moves the counter the report left at zero
    and writes the file the report left absent."""
    root = build_target(tmp_path / "routed-by-verdict")
    runner = Runner(root, documented="story-048 converted 5 of 22 modules.",
                    verdicts=[failing(DOC_CATEGORY), PASS])

    assert story_coordinator.run_story(STORY_ID, harness_root, root, runner) == 0

    assert record_of(root)["reports"] != []
    assert state_of(root)["retry_count"] == 1
    assert (run_dir_of(root) / "retry-history.json").is_file()
    assert runner.calls.count(DOC_ROUTE["stage"]) == 2


def test_the_record_of_the_second_attempt_is_the_second_attempts(
    tmp_path, harness_root,
):
    """A retried documenting stage is checked again, and the record the
    verifier reads is the one that stage just earned rather than the previous
    attempt's."""
    root = build_target(tmp_path / "second-attempt")

    class Retrying(Runner):
        def __call__(self, prompt, *, stage, **kwargs):
            if stage == DOCUMENTING:
                self.documented = ("story-048 converted 5 of 22 modules."
                                   if DOCUMENTING in self.calls
                                   else "The harness runs four stages.")
            return super().__call__(prompt, stage=stage, **kwargs)

    runner = Retrying(root, verdicts=[failing(DOC_CATEGORY), PASS])
    assert story_coordinator.run_story(STORY_ID, harness_root, root, runner) == 0

    assert len(claim_entries(root)) == 2
    assert record_of(root)["reports"] != []
    assert "story-048" in prompt_of(root, VERIFYING, 2)
    assert "story-048" not in prompt_of(root, VERIFYING, 1)


# --------------------------------------------------------------------------
# The prompts, as rendered
#
# Shipped-artifact readings, deliberately: the criteria are about what this
# repository's own templates tell the two stages. Each is asserted against the
# *rendered* prompt, because a template with an unresolved placeholder tells an
# agent nothing — and each carries as its control the same rendering of the
# same template at this story's own baseline, where the sentences are absent.
# --------------------------------------------------------------------------


#: What the verifier's prompt must say a report means and what settles it.
VERIFIER_CRITERIA = (
    "no support that travels",
    "attribution",
    "quotation",
    "restatement",
    "defect in the documentation",
)
#: What the documenter's prompt must say about the untracked directories.
DOCUMENTER_CRITERIA = (
    "untracked by design",
    "not citable authority",
    "restate it in the document",
    "attribute it as a quotation",
)


@pytest.fixture
def shipped_context(tmp_path, harness_root):
    """A context assembled from a real run of this fixture, for rendering the
    templates this repository ships against this repository's own workflow."""
    root = build_target(tmp_path / "prompt-context")
    runner = Runner(root,
                    documented=f"{PLANTED}: story-048 converted 5 of 22 modules.")
    assert story_coordinator.run_story(STORY_ID, harness_root, root, runner) == 0

    return context_assembler.build_context(
        story_text=STORY,
        story=story_coordinator.read_story(STORY).parsed,
        run_dir=run_dir_of(root),
        target_root=root,
        harness_root=REPO_ROOT,
        config=harness_config.load_config(root),
        rules=RULES,
        workflow=conftest.shipped_workflow(REPO_ROOT),
        retry_count=0,
    )


def shipped_template(name: str, *, bound: str) -> str:
    """One shipped prompt template, at one end of this story's own range.

    Resolved through the shared baseline resolution rather than against HEAD:
    the coordinator commits the working tree at the end of a successful run, so
    a HEAD comparison would go vacuously green the moment this story commits.
    """
    return repository_file_at(f"prompts/{name}", validation_file=Path(__file__),
                              bound=bound, repo=REPO_ROOT)


def rendered(name: str, context: dict, *, bound: str = ENDPOINT) -> str:
    return " ".join(context_assembler.render(
        shipped_template(name, bound=bound), context).lower().split())


def test_the_rendered_verifier_prompt_carries_the_record(shipped_context):
    assert shipped_context["claim_support_result"] is not None
    assert PLANTED in shipped_context["claim_support_result"]

    prompt = context_assembler.render(
        shipped_template("verifier.md", bound=ENDPOINT), shipped_context)

    assert shipped_context["claim_support_result"].strip() in prompt
    assert PLANTED in prompt
    assert context_assembler.PLACEHOLDER.search(prompt) is None


def test_the_rendered_verifier_prompt_says_what_a_report_means_and_settles_it(
    shipped_context,
):
    """And that a claim left asserted is a documentation defect belonging to
    the retry category this deployment gives documentation defects.

    The control is the same context rendered through the template as it stood
    at this story's baseline, which says none of it — so a check looking at the
    wrong text would report these missing from both.
    """
    today = rendered("verifier.md", shipped_context)
    before = rendered("verifier.md", shipped_context, bound=BASELINE)

    for phrase in VERIFIER_CRITERIA:
        assert phrase in today, phrase
        assert phrase not in before, phrase

    # The category is reached by injection rather than restated, so the
    # rendered prompt names this deployment's documentation category beside
    # the sentence that assigns a left-asserted claim to it.
    routes = next(stage for stage in conftest.shipped_workflow(REPO_ROOT)["stages"]
                  if "on_failure" in stage)["on_failure"]["retry_routing"]
    category = next(name for name, route in routes.items()
                    if "documentation" in name)
    assert category in today
    assert "retry category" in today


def test_the_rendered_documenter_prompt_says_the_run_directories_are_not_citable(
    shipped_context,
):
    """What the documenter is told instead, asserted the same way and with the
    same control."""
    today = rendered("documenter.md", shipped_context)
    before = rendered("documenter.md", shipped_context, bound=BASELINE)

    for phrase in DOCUMENTER_CRITERIA:
        assert phrase in today, phrase
        assert phrase not in before, phrase

    # The three directories are named as what they are, rather than left as
    # "some files somewhere".
    for named in ("run directories", "logs", "requests"):
        assert named in today, named


def test_the_rendered_documenter_prompt_resolves_every_placeholder(
    shipped_context,
):
    prompt = context_assembler.render(
        shipped_template("documenter.md", bound=ENDPOINT), shipped_context)
    assert context_assembler.PLACEHOLDER.search(prompt) is None


def test_a_stage_running_before_the_check_reads_no_record(tmp_path, harness_root):
    """The record is absent rather than empty for a stage that runs before the
    check, and an absent record renders as the optional-placeholder None.

    The control is the context above, where it is present — so this is a
    statement about when the record exists rather than about the key being
    unreadable.
    """
    root = build_target(tmp_path / "before-the-check")
    empty = tmp_path / "empty-run-dir"
    (empty / "verification").mkdir(parents=True)

    context = context_assembler.build_context(
        story_text=STORY,
        story=story_coordinator.read_story(STORY).parsed,
        run_dir=empty,
        target_root=root,
        harness_root=REPO_ROOT,
        config=harness_config.load_config(root),
        rules=RULES,
        workflow=conftest.shipped_workflow(REPO_ROOT),
        retry_count=0,
    )
    prompt = context_assembler.render(
        shipped_template("verifier.md", bound=ENDPOINT), context)

    assert context["claim_support_result"] is None
    assert PLANTED not in prompt
    assert context_assembler.PLACEHOLDER.search(prompt) is None
