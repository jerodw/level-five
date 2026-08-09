"""Independent validation for story-006: one reader of a story artifact.

Written from the story's acceptance criteria rather than from the
implementation. Where the story's other validation file asserts against
module source text, these tests prefer observable behavior: what a real run
actually writes into the verifier prompt on disk, how many times the
coordinator reads the artifact during that run, and what the completion
report and commit subject end up saying.
"""
import inspect
import json
import re
import subprocess
from pathlib import Path

import pytest

import context_assembler
import harness_config
import schema_validator
import story_coordinator
import story_parser
from agent_runner import AgentResult
from conftest import commit_setup

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT.joinpath(".harness", "stories")

# Every way the two readers could disagree, in one artifact: a criterion
# carrying a colon, a criterion the author hand-wrapped across source lines,
# a full-line comment inside the block, and a quoted criterion.
AWKWARD_STORY = """\
story:
  id: story-001
  title: Criteria the line-prefix reader read wrong
  description: |
    The description mentions acceptance_criteria: and a stray
    title: decoy line, neither of which is structure.

tasks:
  - do the awkward work

acceptance_criteria:
  # A note to the author. Not a criterion, and not the verifier's business.
  - Retry state is bounded: the third failure escalates instead of retrying.
  - a criterion the author wrapped by hand because it ran past the right
    margin of the file
  - "a quoted criterion"
  - a plain criterion

scope:
  modify:
    - src/
  do_not_modify:
    - rules/

verification_requirements:
  - confirm the awkward behavior

constraints:
  - preserve existing behavior
"""

EXPECTED_CRITERIA = [
    "Retry state is bounded: the third failure escalates instead of retrying.",
    "a criterion the author wrapped by hand because it ran past the right "
    "margin of the file",
    "a quoted criterion",
    "a plain criterion",
]


def parse(story_text: str) -> dict:
    return story_parser.parse(story_text, schema_validator.load_schema("story"))


def context_for(target_root: Path, harness_root: Path, story_text: str) -> dict:
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    return context_assembler.build_context(
        story_text=story_text,
        story=parse(story_text),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=harness_config.load_config(target_root),
        rules=harness_config.load_rules(harness_root),
        retry_count=0,
    )


def install(target_root: Path, story_text: str) -> Path:
    path = target_root / ".harness" / "stories" / "story-001.yaml"
    path.write_text(story_text, encoding="utf-8")
    # The artifact is what the run reads, not what it produces, so it is
    # committed: story-021's clean-tree pre-flight refuses a run whose target
    # tree holds anything uncommitted, the story artifact included.
    commit_setup(target_root, "the story artifact this test runs")
    return path


class StageRunner:
    """A fake runner that writes each stage's artifacts so a run completes."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.stages: list[str] = []

    def __call__(self, prompt, *, stage, **kwargs):
        self.stages.append(stage)
        empty = {"modified": [], "created": [], "deleted": []}
        if stage == "implementer":
            self._write("implementation-summary.md", "Did the work.\n")
            self._write("changed-files.json", empty)
        elif stage == "tester":
            self._write("test-results.json", {
                "status": "passed", "tests_written": 1, "tests_run": 1,
                "tests_passed": 1, "tests_failed": 0, "failures": [],
            })
            self._write("tester-changed-files.json", empty)
        elif stage == "verifier":
            self._write("verification-result.json", {
                "status": "passed", "blocking_issues": [], "unverified": [],
                "retry_recommended": False,
            })
        elif stage == "documenter":
            self._write("documentation-report.md", "Nothing to document.\n")
        return AgentResult(ok=True, result_text=f"{stage} done")

    def _write(self, name: str, payload) -> None:
        path = self.run_dir / name
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def complete_run(target_root: Path, harness_root: Path) -> Path:
    """Run the whole workflow with fake agents and return the run directory."""
    run_dir = target_root / ".harness" / "runs" / "story-001"
    runner = StageRunner(run_dir)
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner
    ) == 0
    assert runner.stages == ["implementer", "tester", "verifier", "documenter"]
    return run_dir


def criteria_block(prompt: str) -> list[str]:
    """The lines the verifier prompt renders under 'Acceptance criteria:'."""
    body = prompt.split("Acceptance criteria:\n", 1)[1]
    return body.split("\n\n", 1)[0].splitlines()


# ---------------------------------------------------------------------------
# read_story: the parse is returned, not discarded
# ---------------------------------------------------------------------------


def test_read_story_has_the_documented_signature_and_returns_a_dataclass():
    signature = inspect.signature(story_coordinator.read_story)
    assert list(signature.parameters) == ["story_text", "harness_root"]
    assert signature.parameters["harness_root"].default is None

    reading = story_coordinator.read_story(AWKWARD_STORY)
    assert isinstance(reading, story_coordinator.StoryReading)
    assert reading.problems == []
    assert reading.parsed["acceptance_criteria"] == EXPECTED_CRITERIA


def test_the_story_reading_is_frozen_and_carries_only_parsed_and_problems():
    reading = story_coordinator.read_story(AWKWARD_STORY)
    assert set(vars(reading)) == {"parsed", "problems"}
    with pytest.raises(Exception):
        reading.parsed = {}


def test_a_parse_failure_carries_no_parse_and_exactly_one_message():
    reading = story_coordinator.read_story(
        AWKWARD_STORY.replace("  - do the awkward work", "\t- do the awkward work")
    )
    assert reading.parsed is None
    assert len(reading.problems) == 1
    assert reading.problems[0].startswith("line 9:")


def test_a_structural_failure_carries_the_parse_and_one_message_per_path():
    reading = story_coordinator.read_story(
        AWKWARD_STORY.replace("\ntasks:", "\nwork_items:")
                     .replace("\nconstraints:", "\nlimits:")
    )
    assert reading.parsed is not None
    assert len(reading.problems) == 2
    assert {p.split(":")[0] for p in reading.problems} == {"$.tasks", "$.constraints"}


def test_read_story_loads_the_schema_from_the_given_harness_root(tmp_path):
    """The same parse-then-validate work, steered by a schema file on disk."""
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "story.schema.json").write_text(json.dumps({
        "type": "object",
        "required": ["story", "acceptance_criteria"],
        "properties": {"acceptance_criteria": {"type": "array",
                                               "items": {"type": "string"}}},
    }), encoding="utf-8")
    reading = story_coordinator.read_story(AWKWARD_STORY, tmp_path)
    assert reading.problems == []
    assert reading.parsed["acceptance_criteria"] == EXPECTED_CRITERIA


def test_story_problems_is_gone_from_the_module():
    assert not hasattr(story_coordinator, "story_problems")


# ---------------------------------------------------------------------------
# The run reads the artifact once, before anything exists
# ---------------------------------------------------------------------------


def test_a_run_calls_read_story_exactly_once(target_root, harness_root, monkeypatch):
    """Behavioral, not textual: count the calls a real run actually makes."""
    install(target_root, AWKWARD_STORY)
    calls: list[str] = []
    real = story_coordinator.read_story

    def counting(story_text, harness_root=None):
        calls.append(story_text)
        return real(story_text, harness_root)

    monkeypatch.setattr(story_coordinator, "read_story", counting)
    complete_run(target_root, harness_root)
    assert len(calls) == 1
    assert calls[0] == AWKWARD_STORY


def test_the_read_is_written_above_the_run_directory_and_the_checkout():
    """The ordering held textually, so a later edit cannot quietly invert it.

    The runtime test below proves nothing exists *when* the read happens. This
    one pins where the call sits in the source, so moving it below the
    run-directory creation or the branch checkout fails here rather than only
    in a run that happens to be rejected.
    """
    source = Path(story_coordinator.__file__).read_text(encoding="utf-8")
    call = source.index("reading = read_story(")
    assert call < source.index("run_dir.mkdir(")
    # The checkout *call* in run_story, not the helper's definition above it.
    assert call < source.rindex("_checkout_story_branch(target_root")


def test_the_one_read_happens_before_any_run_state_or_branch_exists(
    target_root, harness_root, monkeypatch
):
    """At the moment the artifact is read, nothing has been created yet."""
    install(target_root, AWKWARD_STORY)
    run_dir = target_root / ".harness" / "runs" / "story-001"
    observed: dict[str, object] = {}
    real = story_coordinator.read_story

    def observing(story_text, harness_root=None):
        observed["run_dir"] = run_dir.exists()
        observed["log"] = (target_root / ".harness" / "logs" / "story-001.log").exists()
        observed["branches"] = branches(target_root)
        return real(story_text, harness_root)

    monkeypatch.setattr(story_coordinator, "read_story", observing)
    before = branches(target_root)
    complete_run(target_root, harness_root)
    assert observed["run_dir"] is False
    assert observed["log"] is False
    assert observed["branches"] == before
    assert "story/story-001" not in observed["branches"]


def branches(target_root: Path) -> list[str]:
    return subprocess.run(
        ["git", "-C", str(target_root), "branch", "--format=%(refname:short)"],
        capture_output=True, text=True, check=True,
    ).stdout.split()


def test_a_rejected_story_still_leaves_nothing_behind(target_root, harness_root, capsys):
    install(target_root, AWKWARD_STORY.replace("\nscope:", "\nboundaries:"))
    before = branches(target_root)

    def no_agent(prompt, **kwargs):
        raise AssertionError("a rejected story must not reach an agent")

    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, no_agent
    ) == 1
    err = capsys.readouterr().err
    assert "$.scope" in err

    run_dir = target_root / ".harness" / "runs" / "story-001"
    assert not run_dir.exists()
    assert not (run_dir / "state.json").exists()
    assert not (target_root / ".harness" / "logs" / "story-001.log").exists()
    assert branches(target_root) == before


def test_every_problem_is_printed_one_per_line(target_root, harness_root, capsys):
    install(
        target_root,
        AWKWARD_STORY.replace("\ntasks:", "\nwork_items:")
                     .replace("\nconstraints:", "\nlimits:"),
    )
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, lambda *a, **k: None
    ) == 1
    err = capsys.readouterr().err
    assert "  - $.tasks" in err
    assert "  - $.constraints" in err


# ---------------------------------------------------------------------------
# build_context takes the parse
# ---------------------------------------------------------------------------


def test_build_context_requires_a_keyword_only_story_argument(target_root, harness_root):
    signature = inspect.signature(context_assembler.build_context)
    parameter = signature.parameters["story"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty

    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(TypeError):
        context_assembler.build_context(
            story_text=AWKWARD_STORY,
            run_dir=run_dir,
            target_root=target_root,
            harness_root=harness_root,
            config=harness_config.load_config(target_root),
            rules=harness_config.load_rules(harness_root),
            retry_count=0,
        )


def test_the_criteria_value_is_exactly_one_dash_line_per_parsed_criterion(
    target_root, harness_root
):
    context = context_for(target_root, harness_root, AWKWARD_STORY)
    assert context["acceptance_criteria"].splitlines() == [
        f"- {c}" for c in EXPECTED_CRITERIA
    ]


def test_the_criteria_value_carries_no_yaml_indentation_or_quoting(
    target_root, harness_root
):
    lines = context_for(
        target_root, harness_root, AWKWARD_STORY
    )["acceptance_criteria"].splitlines()
    for line in lines:
        assert line.startswith("- ")
        body = line[2:]
        assert body == body.strip()
        assert not body.startswith(('"', "'"))
        assert not body.endswith(('"', "'"))
    assert "- \"a quoted criterion\"" not in lines
    assert "- a quoted criterion" in lines


def test_absent_acceptance_criteria_renders_as_none(target_root, harness_root):
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    context = context_assembler.build_context(
        story_text="story:\n  id: story-001\n",
        story={"story": {"id": "story-001"}},
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=harness_config.load_config(target_root),
        rules=harness_config.load_rules(harness_root),
        retry_count=0,
    )
    assert context["acceptance_criteria"] is None
    rendered = context_assembler.render(
        context_assembler.load_template(harness_root, "verifier.md"), context
    )
    assert "Acceptance criteria:\nNone" in rendered
    assert "{{" not in rendered


def test_the_story_value_is_the_raw_text_it_was_given(target_root, harness_root):
    context = context_for(target_root, harness_root, AWKWARD_STORY)
    assert context["story"] == AWKWARD_STORY


# ---------------------------------------------------------------------------
# What a real run puts in front of the verifier
# ---------------------------------------------------------------------------


def test_the_prompt_a_run_writes_carries_every_criterion_and_nothing_else(
    target_root, harness_root
):
    install(target_root, AWKWARD_STORY)
    run_dir = complete_run(target_root, harness_root)
    prompt = (run_dir / "prompt-verifier-attempt-1.md").read_text(encoding="utf-8")
    assert criteria_block(prompt) == [f"- {c}" for c in EXPECTED_CRITERIA]


def test_a_colon_criterion_reaches_the_verifier_prompt_whole(target_root, harness_root):
    install(target_root, AWKWARD_STORY)
    run_dir = complete_run(target_root, harness_root)
    prompt = (run_dir / "prompt-verifier-attempt-1.md").read_text(encoding="utf-8")
    assert f"- {EXPECTED_CRITERIA[0]}" in criteria_block(prompt)


def test_a_hand_wrapped_criterion_reaches_the_prompt_on_one_line(
    target_root, harness_root
):
    install(target_root, AWKWARD_STORY)
    run_dir = complete_run(target_root, harness_root)
    block = criteria_block(
        (run_dir / "prompt-verifier-attempt-1.md").read_text(encoding="utf-8")
    )
    assert f"- {EXPECTED_CRITERIA[1]}" in block
    assert not any(line.startswith("margin of the file") for line in block)


def test_an_in_block_comment_does_not_reach_the_criteria_the_verifier_evaluates(
    target_root, harness_root
):
    install(target_root, AWKWARD_STORY)
    run_dir = complete_run(target_root, harness_root)
    prompt = (run_dir / "prompt-verifier-attempt-1.md").read_text(encoding="utf-8")
    block = criteria_block(prompt)
    assert not any("not the verifier's business" in line.lower() for line in block)
    assert not any(line.lstrip("- ").startswith("#") for line in block)


def test_the_prompt_carries_the_story_file_byte_for_byte(target_root, harness_root):
    """{{story}} is the artifact as authored: comments, indentation and all."""
    story_path = install(target_root, AWKWARD_STORY)
    run_dir = complete_run(target_root, harness_root)
    on_disk = story_path.read_text(encoding="utf-8")
    for name in ("implementer", "tester", "verifier", "documenter"):
        prompt = (run_dir / f"prompt-{name}-attempt-1.md").read_text(encoding="utf-8")
        assert on_disk in prompt, name


def test_no_criterion_of_any_committed_story_goes_missing(target_root, harness_root):
    """The corpus check: the verifier loses nothing the old reader carried."""
    stories = sorted(CORPUS.glob("*.yaml"))
    assert stories, "no committed story artifacts to check"
    for path in stories:
        story_text = path.read_text(encoding="utf-8")
        criteria = parse(story_text)["acceptance_criteria"]
        assert criteria, path.name
        context = context_for(target_root, harness_root, story_text)
        rendered = context_assembler.render(
            context_assembler.load_template(harness_root, "verifier.md"), context
        )
        assert criteria_block(rendered) == [f"- {c}" for c in criteria], path.name
        assert story_text in rendered, path.name


# ---------------------------------------------------------------------------
# The completion report and commit subject come from the parse
# ---------------------------------------------------------------------------


def test_the_completion_report_and_commit_take_the_parsed_title(
    target_root, harness_root
):
    install(target_root, AWKWARD_STORY)
    run_dir = complete_run(target_root, harness_root)
    title = parse(AWKWARD_STORY)["story"]["title"]

    report = (run_dir / "completion-report.md").read_text(encoding="utf-8")
    assert report.startswith("# story-001 Completion Report\n\n## Story\n" + title)
    assert "## Outcome\nCompleted on branch story/story-001 after 0 retries." in report
    assert "- test-results.json" in report
    assert "verification/iteration-1.json (passed)" in report

    subject = subprocess.run(
        ["git", "-C", str(target_root), "log", "-1", "--pretty=%s"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert subject == f"story-001: {title}"


def test_a_title_shaped_line_in_the_description_does_not_become_the_title(
    target_root, harness_root
):
    """The old `title:` line scan took the first matching line anywhere."""
    install(target_root, AWKWARD_STORY)
    run_dir = complete_run(target_root, harness_root)
    report = (run_dir / "completion-report.md").read_text(encoding="utf-8")
    assert "Criteria the line-prefix reader read wrong" in report
    assert "decoy" not in report


# ---------------------------------------------------------------------------
# Nothing reads a story artifact a second way
# ---------------------------------------------------------------------------


def test_extract_section_is_gone_from_context_assembler():
    assert not hasattr(context_assembler, "extract_section")
    source = Path(context_assembler.__file__).read_text(encoding="utf-8")
    assert "extract_section" not in source


def test_no_module_in_orchestration_scans_story_text_by_line(harness_root):
    """Look for the scanning *patterns*, not for the words being scanned for.

    A bare `"title:" not in source` would also fail on a docstring that
    mentions the field, which is prose about the design rather than a second
    reader of it. What makes a line-prefix scan is splitting story text into
    lines and testing those lines against a key prefix.
    """
    scans = (
        "story_text.splitlines",
        'startswith("title:")',
        "startswith('title:')",
        'startswith("acceptance_criteria:")',
        "startswith('acceptance_criteria:')",
        'startswith(f"{key}:")',
    )
    for path in sorted((harness_root / "orchestration").glob("*.py")):
        if path.name == "story_parser.py":      # the one reader
            continue
        source = path.read_text(encoding="utf-8")
        for scan in scans:
            assert scan not in source, (path.name, scan)


def test_story_parser_parse_is_the_only_entry_point_into_a_story(harness_root):
    callers = []
    for path in sorted((harness_root / "orchestration").glob("*.py")):
        if path.name == "story_parser.py":
            continue
        if "story_parser.parse(" in path.read_text(encoding="utf-8"):
            callers.append(path.name)
    assert callers == ["story_coordinator.py"]
    source = Path(story_coordinator.__file__).read_text(encoding="utf-8")
    assert source.count("story_parser.parse(") == 1


def test_the_single_mechanism_test_was_retargeted_not_removed(harness_root):
    """The story requires the existing assertion to survive, retargeted."""
    source = (harness_root / "tests" / "test_story_coordinator.py").read_text(
        encoding="utf-8"
    )
    marker = "def test_exactly_one_mechanism_reads_a_story_artifact("
    assert marker in source, "the single-mechanism test is missing"
    body = source[source.index(marker):]
    body = body[: body.index("\nclass ") if "\nclass " in body else len(body)]
    assert 'source.count("read_story(") == 2' in body
    assert "extract_section" in body
    assert 'startswith("title:")' in body


def test_no_conforming_yaml_library_reads_a_story(harness_root):
    """An *import* of a YAML library, not the word appearing in prose."""
    for path in sorted((harness_root / "orchestration").glob("*.py")):
        if path.name == "story_parser.py":      # its docstring names what it bans
            continue
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|from) (yaml|ruamel)", source, re.M), path.name
        assert "yaml.safe_load(" not in source, path.name
