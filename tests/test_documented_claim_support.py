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

story-054 widened the quantity half of that decision: a quantity is now a
digit *or* one word of a bounded set, so story-047's "a fourth standing rule"
is a claim this reports where the shipped check saw nothing. Its assertions
are driven through the same `claim_support_check` as the rest, and the control
each of them carries is the check with its quantity decision monkeypatched
back to the digits-only one — a predicate written in this module, because a
one-line predicate written here is the same predicate with no revision under
it to be squashed, renamed or rebased away. The boundary is asserted in the
two places this repository states it and that exist when this module runs: the
comment beside the pattern and the schema description.

Nothing here invokes a model: every run goes through a fake agent runner, and
`no_model` turns the single call that would reach one into a failure.
"""
import ast
import inspect
import json
import re
import subprocess
from pathlib import Path

import pytest

import agent_runner
import context_assembler
import harness_config
import schema_validator
import story_coordinator
from agent_runner import AgentResult
from conftest import BASELINE, ENDPOINT
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

#: The paragraph a run adds to the document under test. Written here rather
#: than recovered out of this repository's history, and written with the
#: messiness of prose somebody wrote for a reader: several sentences, an
#: em-dashed aside, a parenthesis, a backticked path, a colon, and the claim
#: itself finishing on a line of its own. The unsupportable claim it carries —
#: a quantity attributed to a story whose work is not merged — is the shape
#: story-049 wrote into `.harness/docs/ARCHITECTURE.md` and the shape this
#: check exists to report.
ADDED_CLAIM = f"""\
{PLANTED}: the conversion has been under way for some time now, and it is
worth saying where it stands — the mechanical part (the scan, its
classification vocabulary, and the ceiling in `tests/test_baseline_honesty.py`)
landed first, and the modules followed. As of this writing story-048
converted 5 of 22 modules; the rest are on the work list, in no particular
order, and none of them is hard.
"""

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
    added text the report is about.

    The added text is a paragraph rather than a one-liner, and deliberately as
    messy as the prose a documenter actually writes: several sentences, an
    em-dashed aside, a parenthesis, a backticked path, a colon and a trailing
    clause on a line of its own. A synthetic one-liner would pass against a
    check that only ever matched a whole line, and this is what the real
    documents look like.

    It replaced a test that recovered one of this repository's own sentences
    out of the commit graph to make the same point. That test asserted less
    than this one and cost story-051 its entire retry budget: a pinned revision
    rebased away by a squash merge, then a content search that collided with
    the document's own description of this very check. The messiness was its
    one genuine contribution, and it is written here instead.
    """
    root = build_target(tmp_path / "reported")
    add_to_document(root, ADDED_CLAIM)

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
    # Every line of the added paragraph reaches the report, so what is reported
    # is the added text rather than the one line the quantity happened to sit on.
    for line in ADDED_CLAIM.splitlines():
        assert line in report["text"], line
    assert schema_validator.validate(record, SCHEMA) == []
    assert result.ran is True


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
# A spelled-out quantity is a quantity too
#
# story-054 widened the quantity test from a digit to a digit *or* one word of
# a bounded set. Every assertion here is driven through `claim_support_check`
# rather than through the pattern, because what the story is about is which
# blocks get reported.
#
# The control every one of them carries is the check with its quantity
# decision reverted to the one this story widened — `a_digit_and_nothing_
# cleverer` below, monkeypatched over the coordinator's own. That is the
# demonstration that these cases were invisible before rather than a claim
# about it, and it is a written predicate rather than a revision resolved out
# of this repository's commit graph: the predicate is one line, and a line
# written here is the same line with nothing under it to be squashed, renamed
# or rebased away.
# --------------------------------------------------------------------------


def a_digit_and_nothing_cleverer(text: str) -> bool:
    """`carries_a_quantity` exactly as it stood before story-054.

    The reference stripping is the coordinator's, because the stripping is not
    what this story changed and reverting it too would make the control differ
    from the shipped check in two ways rather than one.
    """
    return bool(re.search(r"\d", story_coordinator.STORY_REFERENCE.sub(" ", text)))


def revert_the_quantity_test(monkeypatch) -> None:
    """Put the digits-only decision back, leaving the rest of the check whole.

    The seam is the coordinator's own `carries_a_quantity`, which is where the
    story required the widening to land — so a widening written as a second
    decision point somewhere else would leave this control green and this
    module would be looking in the wrong place. The test below asserts the
    seam is real before relying on it.
    """
    monkeypatch.setattr(story_coordinator, "carries_a_quantity",
                        a_digit_and_nothing_cleverer)


def test_the_reverted_control_is_wired_to_the_decision_it_claims_to_revert(
    tmp_path, monkeypatch,
):
    """The control for the controls: reverting the quantity test really does
    change what the check reports.

    Without this, every "and it reported nothing before" below would hold
    equally against a monkeypatch that landed on a name nothing calls.
    """
    root = build_target(tmp_path / "seam")
    add_to_document(root, "story-048 converted eight of twenty-two modules.")

    assert reports_of(root) != []
    revert_the_quantity_test(monkeypatch)
    assert reports_of(root) == []
    monkeypatch.undo()
    assert reports_of(root) != []


@pytest.mark.parametrize("sentence", [
    pytest.param("story-048 introduced a fourth standing rule for the planner.",
                 id="story-047s-own-wording"),
    pytest.param("story-048 left the three standing rules already in force "
                 "untouched.", id="the-other-half-of-story-047s-wording"),
    pytest.param("story-048 converted eight of twenty-two modules.",
                 id="the-case-the-architecture-document-named-as-unseen"),
])
def test_a_spelled_out_quantity_is_reported(tmp_path, monkeypatch, sentence):
    """The three wordings this story exists for.

    Two are story-047's actual prose — the claim that motivated the check and
    which the check as shipped could not see — and the third is the worked
    example `.harness/docs/ARCHITECTURE.md` offered as a quantity this could
    never catch.

    Each is asserted reported, and then the same repository is checked again
    with the quantity decision reverted, where nothing is reported. So the
    report is owed to the widening rather than to something the shipped check
    already did.
    """
    root = build_target(tmp_path / "spelled-out")
    add_to_document(root, sentence)

    assert [report["stories"] for report in reports_of(root)] == [["story-048"]]

    revert_the_quantity_test(monkeypatch)
    assert reports_of(root) == []


@pytest.mark.parametrize("text,reported", [
    pytest.param(ADDED_CLAIM, True, id="the-paragraph-this-module-reports-on"),
    pytest.param("story-048 converted 5 of 22 modules.", True,
                 id="the-sentence-most-of-this-module-uses"),
    pytest.param("story-048 was the story that motivated 2 of these.", True,
                 id="a-reference-with-a-figure-beside-it"),
    pytest.param("The lesson of story-048 and story-049 is attribution.", False,
                 id="references-whose-only-digits-are-their-own"),
    pytest.param("story-048 is the motivating case.\n\nIt took 3 tries.", False,
                 id="a-reference-and-a-figure-in-paragraphs-apart"),
    pytest.param("The next story in this line will carry 3 more modules.", False,
                 id="a-forward-reference-naming-no-story"),
])
def test_the_digit_cases_report_identically_to_before_the_widening(
    tmp_path, monkeypatch, text, reported,
):
    """Widening added a case rather than changed one.

    The same documents, the same stories and the same text, checked twice: once
    by the widened decision and once by the digits-only one it replaced. The
    two records are compared whole — document, stories and reported text — so a
    widening that had quietly moved a block boundary or swallowed a reference
    would differ here even though both runs still reported something.

    The `reported` flag is carried so the equality cannot pass by both sides
    reporting nothing: the cases that reported before are asserted to still
    report, and the cases that did not are asserted to still not.
    """
    root = build_target(tmp_path / "unchanged")
    add_to_document(root, text)

    widened = reports_of(root)
    assert (widened != []) is reported

    revert_the_quantity_test(monkeypatch)
    assert reports_of(root) == widened


def test_the_excluded_number_words_are_excluded_on_purpose(tmp_path):
    """`one`, `a`, `an` and `first` carry no enumeration and are left out.

    The control differs in exactly one word — `first` becomes `second` — so
    this is a statement about which words are in the set rather than about a
    sentence the check cannot see at all.
    """
    excluded = ("story-048 added a rule: one of the standing rules, an obvious "
                "one, and the first of its kind.")
    included = excluded.replace("the first of its kind", "the second of its kind")

    quiet = build_target(tmp_path / "only-excluded-words")
    add_to_document(quiet, excluded)
    loud = build_target(tmp_path / "one-included-word")
    add_to_document(loud, included)

    assert reports_of(quiet) == []
    assert [report["stories"] for report in reports_of(loud)] == [["story-048"]]


def test_a_number_word_inside_a_longer_word_is_not_a_quantity(tmp_path):
    """A word the set holds, buried in a longer word, is not a quantity:
    "often" is not "ten" and "someone" is not "one".

    The control is the same sentence with a bare set word in it, which is
    reported — so the absence is about the letters around the word rather than
    about the check having stopped looking. `hone` and `someone` are in both
    sentences, which makes the control differ in the bare word alone.
    """
    inside = ("story-048 is often revisited, and someone will hone those rules "
              "again.")
    bare = inside.replace("is often revisited", "is revisited ten times")

    quiet = build_target(tmp_path / "inside-a-longer-word")
    add_to_document(quiet, inside)
    loud = build_target(tmp_path / "the-bare-word")
    add_to_document(loud, bare)

    assert reports_of(quiet) == []
    assert [report["stories"] for report in reports_of(loud)] == [["story-048"]]


def test_the_quantity_words_are_matched_whatever_their_case(tmp_path):
    """A sentence starting "Three" is reported.

    The control is the same sentence with a capitalised word that enumerates
    nothing mechanically — "Several" — which is not reported, so this is a
    statement about case rather than about any capitalised word reporting.
    """
    shouted = "story-048 changed the planner. Three standing rules survived it."
    vaguer = shouted.replace("Three", "Several")

    loud = build_target(tmp_path / "sentence-initial-three")
    add_to_document(loud, shouted)
    quiet = build_target(tmp_path / "sentence-initial-several")
    add_to_document(quiet, vaguer)

    assert [report["stories"] for report in reports_of(loud)] == [["story-048"]]
    assert reports_of(quiet) == []


def test_a_hyphenated_compound_is_reported_without_a_rule_of_its_own(
    tmp_path, monkeypatch,
):
    """A compound reports on the strength of the bounded set alone:
    "twenty-two" is `twenty` with a non-letter after it.

    Asserted beside the digits-only control, which reports nothing, and beside
    the same sentence carrying `twenty` unhyphenated — the compound is not a
    case of its own, it is the set word with a non-letter after it.
    """
    compound = build_target(tmp_path / "hyphenated")
    add_to_document(compound, "story-048 converted twenty-two of the modules.")
    plain = build_target(tmp_path / "unhyphenated")
    add_to_document(plain, "story-048 converted twenty of the modules.")

    assert [report["stories"] for report in reports_of(compound)] == \
        [["story-048"]]
    assert [report["stories"] for report in reports_of(plain)] == [["story-048"]]

    revert_the_quantity_test(monkeypatch)
    assert reports_of(compound) == []


def test_the_widened_test_still_looks_outside_the_story_reference(tmp_path):
    """The digits of `story-048` are still part of a name.

    The reference stripping is shared by both alternatives rather than by the
    digit one alone, so the control is the same sentence with a set word added
    outside the names, which is reported.
    """
    named = build_target(tmp_path / "widened-just-the-name")
    add_to_document(named, "The lesson of story-048 and story-049 is attribution.")
    counted = build_target(tmp_path / "widened-a-real-count")
    add_to_document(counted, "The lesson of story-048 and story-049 is "
                             "attribution, and it took a second attempt.")

    assert reports_of(named) == []
    assert len(reports_of(counted)) == 1


def test_the_bounded_set_is_what_decides_and_the_regex_is_built_from_it():
    """The set is written once: the alternation is derived from it.

    Every word in the set is a quantity when it stands alone, no word outside
    the set is, and the four excluded words are absent from the set — the
    exclusion is a decision about membership rather than a special case bolted
    on somewhere else. Run through `carries_a_quantity`, which is the decision
    the check makes, rather than through the pattern that implements it.
    """
    for word in story_coordinator.NUMBER_WORDS:
        assert story_coordinator.carries_a_quantity(f"it took {word} attempts"), \
            word

    for outside in ("quadrillion", "several", "many", "few", "umpteen",
                    "one", "a", "an", "first"):
        assert not story_coordinator.carries_a_quantity(
            f"it took {outside} attempts"), outside

    assert {"one", "a", "an", "first"}.isdisjoint(story_coordinator.NUMBER_WORDS)


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


def test_a_reported_spelled_out_claim_routes_exactly_as_a_run_without_one(
    tmp_path, harness_root,
):
    """Widening what is seen did not widen what is done about it.

    The same comparison as the run above, with the reported claim carrying a
    spelled-out quantity instead of a figure: same exit code, same stages
    invoked, same event stream, same retry count, same self-route count and no
    retry history in either. The premise is asserted first, and asserted twice
    over — one run reported and the other did not, *and* the reported text
    carries no digit at all, so the report travelled the path this story added
    rather than the one that already existed.

    `test_a_retry_and_a_retry_history_entry_are_things_this_run_could_have_had`
    below is the control for the zeroes here: the same fixture routed by a
    verdict moves every counter this run leaves still.
    """
    reported = build_target(tmp_path / "spelled-routes-reported")
    quiet = build_target(tmp_path / "spelled-routes-quiet")
    loud_runner = Runner(
        reported, documented="story-048 introduced a fourth standing rule.")
    quiet_runner = Runner(
        quiet, documented="The harness runs its stages in the declared order.")

    assert story_coordinator.run_story(
        STORY_ID, harness_root, reported, loud_runner) == 0
    assert story_coordinator.run_story(
        STORY_ID, harness_root, quiet, quiet_runner) == 0

    assert record_of(reported)["reports"] != []
    assert record_of(quiet)["reports"] == []
    assert not a_digit_and_nothing_cleverer(
        record_of(reported)["reports"][0]["text"])

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

    The endpoint is the template this repository ships, read from the tree.
    That is the subject of every assertion below: what a stage's rendered
    prompt says *here* is the claim, and asserting it at a frozen past endpoint
    would say nothing about whether the criteria are still in the file an agent
    is handed today.

    The baseline is a frozen past text and is carried as a committed fixture.
    Resolving it through the range made a control depend on this repository's
    commit graph rather than on the template: a squash makes the range
    unresolvable in a clone, and a rename empties it silently. The text is the
    same text, lifted from exactly that baseline.
    """
    if bound == BASELINE:
        return conftest.history_fixture(
            f"prompts-{name.removesuffix('.md')}.at-this-storys-baseline.md.txt")
    assert bound == ENDPOINT, bound
    return (REPO_ROOT / "prompts" / name).read_text(encoding="utf-8")


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


# --------------------------------------------------------------------------
# The stated limit, in the places it is stated
#
# Shipped-artifact readings, deliberately: the claim is that *this repository*
# tells a reader where the check's boundary now falls. The schema this
# repository ships and the comment beside the pattern in the coordinator are
# the subjects, so they are read as shipped.
#
# Each absence — that no one of them still asserts the digits-only limit — is
# asserted beside the text it replaced, written here as a constant. The same
# predicate reports that text, so a predicate that had stopped recognising the
# old sentence would fail rather than pass quietly.
#
# `.harness/docs/ARCHITECTURE.md` states the same limit in its scope-limits
# paragraph and is deliberately not asserted here: that document is the
# documenting stage's to write and it has not run yet when this module does.
# --------------------------------------------------------------------------


#: The two sentences that stated the digits-only limit before story-054,
#: quoted from the artifacts they were removed from. They are the controls for
#: the absences below, not a second copy of anything live.
PRE_STORY_QUANTITY_COMMENT = (
    "What counts as a quantity: a digit, and nothing cleverer. A spelled-out "
    'number ("eight of twenty-two") is a quantity this does not see, and that '
    "limit is stated rather than papered over."
)
PRE_STORY_SCHEMA_SENTENCE = (
    "the whole decision is the story-0NN reference shape, whether that story "
    "has a completion commit reachable from the base, and whether a digit "
    "appears outside the reference itself."
)


def normalized(text: str) -> str:
    """One line, lowercased, with the markup a comment or a schema wraps words
    in removed — so a needle matches the words rather than their backticks."""
    return " ".join(text.lower().replace("`", "").replace('"', "").split())


def states_the_digits_only_limit(text: str) -> bool:
    """Whether `text` still says a quantity is a digit and nothing else."""
    lowered = normalized(text)
    return any(needle in lowered for needle in (
        "whether a digit appears outside the reference",
        "a digit, and nothing cleverer",
        "a quantity is a digit and nothing cleverer",
    ))


def states_the_widened_limit(text: str) -> bool:
    """Whether `text` states story-054's boundary: what is matched, how, and
    which words are kept out of it."""
    lowered = normalized(text)
    return all(needle in lowered for needle in (
        "digit",
        "one, a, an and first",
        "case-insensitiv",
        "whole word",
    ))


def comment_above(source: str, assignment: str) -> str:
    """The `#:` block immediately above a module-level assignment."""
    lines = source.splitlines()
    index = next(number for number, line in enumerate(lines)
                 if line.startswith(assignment))
    collected = []
    while index and lines[index - 1].startswith("#:"):
        index -= 1
        collected.append(lines[index].removeprefix("#:").strip())
    return " ".join(reversed(collected))


COORDINATOR_SOURCE = inspect.getsource(story_coordinator)


def test_the_comment_beside_the_pattern_states_the_new_boundary():
    """The limit is written where the pattern is, and it is the new one.

    The comment is located by the assignment it sits above rather than by a
    line number, and the control for "it no longer asserts digits-only" is the
    sentence it replaced, which the same predicate reports.
    """
    comment = comment_above(COORDINATOR_SOURCE, "QUANTITY = ")

    assert comment, "no comment block was found above the pattern"
    assert states_the_widened_limit(comment)
    assert not states_the_digits_only_limit(comment)
    # The control: the sentence that stood there before is still recognised.
    assert states_the_digits_only_limit(PRE_STORY_QUANTITY_COMMENT)
    assert not states_the_widened_limit(PRE_STORY_QUANTITY_COMMENT)


def test_the_exclusion_is_reasoned_beside_the_set_rather_than_left_to_be_found():
    """A reader can tell an omission from a decision.

    The four excluded words are named beside the set and a reason is given for
    leaving them out. Both halves are required, and both controls show it: a
    comment naming them without a reason, and one giving neither, are rejected
    by the same predicate that accepts what is shipped.
    """
    comment = comment_above(COORDINATOR_SOURCE, "NUMBER_WORDS = ")

    def reasons_the_exclusion(text: str) -> bool:
        lowered = normalized(text)
        return ("one, a, an and first" in lowered
                and "on purpose" in lowered
                and "carry no enumeration" in lowered)

    assert reasons_the_exclusion(comment)
    assert not reasons_the_exclusion(
        "The bounded set of words that count as a quantity. one, a, an and "
        "first are not in it.")
    assert not reasons_the_exclusion(
        "The bounded set. one, a, an and first are excluded on purpose.")


def test_the_shipped_schema_description_states_the_new_boundary():
    """The record's own description tells the reader where the boundary falls.

    Asserted of the schema this repository ships, with the sentence it
    replaced as the control for the absence — and the shape asserted unchanged
    beside it, since only the description sentence was this story's to move.
    """
    description = SCHEMA["description"]

    assert states_the_widened_limit(description)
    assert not states_the_digits_only_limit(description)
    # The control: the sentence that stood there before is still recognised.
    assert states_the_digits_only_limit(PRE_STORY_SCHEMA_SENTENCE)
    assert not states_the_widened_limit(PRE_STORY_SCHEMA_SENTENCE)

    # And the record is still the same record: the description moved, the
    # shape did not.
    assert list(SCHEMA["properties"]) == [
        "ran", "base", "story_id", "documents", "reports", "reason"]
    assert SCHEMA["required"] == ["ran"]
    assert schema_validator.validate({"ran": True, "reports": []}, SCHEMA) == []


# --------------------------------------------------------------------------
# What story-053 left, still standing
#
# This story edits this module, so the two properties story-053 established
# here are carried as guards rather than trusted: exactly one test drives the
# report-a-claim paragraph, and that paragraph is prose somebody could have
# written rather than a synthetic one-liner. The other half — that no test
# here resolves a fixture out of this repository's commit graph — is held by
# `tests/test_baseline_honesty.py`, which scans every module for exactly that
# and names this one, with its own planted control.
# --------------------------------------------------------------------------


MODULE_SOURCE = Path(__file__).read_text(encoding="utf-8")

#: The test that drives the paragraph, named — so a second one added beside it
#: fails here rather than being noticed a story later.
THE_REPORT_A_CLAIM_TEST = \
    "test_an_added_claim_about_a_story_with_no_merged_work_is_reported"


#: The calls that put text through the check. A test of the report-a-claim
#: behaviour is one whose body drives the paragraph through one of these —
#: which is what makes it a test *of that behaviour* rather than a test that
#: merely mentions the paragraph. The parity test above hands it to the same
#: calls as a parametrization, so the paragraph is one input among six there
#: rather than the subject, and the guard below reads it only to ask whether
#: it is prose.
PARAGRAPH_DRIVERS = frozenset({"check", "reports_of", "add_to_document"})


def _callee(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else \
        getattr(func, "id", "")


def report_a_claim_tests(source: str) -> list[str]:
    """Every test in `source` that drives this module's claim paragraph
    through the check."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        # The body only: a decorator naming the paragraph is a parametrization
        # handing it in as data, not a test whose subject it is.
        inner = [descendant for statement in node.body
                 for descendant in ast.walk(statement)]
        names = {leaf.id for leaf in inner if isinstance(leaf, ast.Name)}
        calls = {_callee(leaf) for leaf in inner if isinstance(leaf, ast.Call)}
        if "ADDED_CLAIM" in names and calls & PARAGRAPH_DRIVERS:
            found.append(node.name)
    return sorted(found)


def is_multi_clause_prose(text: str) -> bool:
    """Whether `text` reads like a paragraph a documenter wrote.

    Several sentences, more than one line, and at least one of the marks prose
    uses to join clauses — a dash, a parenthesis, a semicolon or a colon.
    """
    return (text.count(".") >= 3
            and len(text.strip().splitlines()) > 1
            and any(mark in text for mark in ("—", "(", ";", ":")))


def test_this_module_holds_exactly_one_test_of_the_report_a_claim_behaviour():
    """One test, carrying realistic prose — the state story-053 left.

    Both halves carry their control: the counting is shown to report two when
    a second such test is planted in a copy of this module's source, and the
    prose predicate is shown to reject the one-liner the paragraph replaced.
    """
    assert report_a_claim_tests(MODULE_SOURCE) == [THE_REPORT_A_CLAIM_TEST]
    assert is_multi_clause_prose(ADDED_CLAIM)

    # The controls, both by construction rather than by observation.
    planted = MODULE_SOURCE + (
        "\n\ndef test_a_second_reader_of_the_paragraph(tmp_path):\n"
        "    root = build_target(tmp_path / 'planted')\n"
        "    add_to_document(root, ADDED_CLAIM)\n"
        "    assert reports_of(root) != []\n")
    assert len(report_a_claim_tests(planted)) == 2
    assert not is_multi_clause_prose("story-048 converted 5 of 22 modules.")
