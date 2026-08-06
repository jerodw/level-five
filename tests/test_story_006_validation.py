"""Validation for story-006: one reader of a story artifact.

Before this story three mechanisms read a story: the schema-directed parse,
a line-prefix slice of the acceptance_criteria block, and a `title:` line
scan. The verifier — the stage that decides whether a story passes — was
evaluating against the output of the weakest of the three.

These tests are written against what the verifier now receives, with
particular attention to the cases where the two readers could diverge: a
criterion containing a colon, a hand-wrapped criterion, and a comment
written inside the criteria block.
"""
import json
import re
from pathlib import Path

import context_assembler
import harness_config
import schema_validator
import story_coordinator
import story_parser
from agent_runner import AgentResult

HARNESS_ROOT = Path(__file__).resolve().parents[1]
# Discovered, not named, and joined the way the other corpus tests join it.
STORIES_DIR = HARNESS_ROOT.joinpath(".harness", "stories")

# A story exercising every divergence case at once. The colon criterion is
# quoted from .harness/stories/story-003.yaml, the artifact that motivated
# schema-directed parsing in the first place.
DIVERGENT_STORY = """\
story:
  id: story-001
  title: Divergence cases the old reader could get wrong
  description: |
    A story whose acceptance criteria exercise every way the line-prefix
    reader and the parse could disagree.

tasks:
  - do the work

acceptance_criteria:
  # This full-line comment is not a criterion and must not reach the prompt.
  - A test demonstrates the one-file-edit property: editing only
    prompts/harness-layer.md changes all three rendered stage prompts.
  - a plain criterion
  - a hand-wrapped criterion that runs past the end of its source line and
    continues on the next one

scope:
  modify:
    - src/
  do_not_modify:
    - rules/

verification_requirements:
  - confirm the behavior

constraints:
  - preserve existing behavior
"""

COLON_CRITERION = (
    "A test demonstrates the one-file-edit property: editing only "
    "prompts/harness-layer.md changes all three rendered stage prompts."
)
WRAPPED_CRITERION = (
    "a hand-wrapped criterion that runs past the end of its source line and "
    "continues on the next one"
)


def parse(story_text: str) -> dict:
    return story_parser.parse(story_text, schema_validator.load_schema("story"))


def build(target_root, harness_root, story_text: str) -> dict:
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


def verifier_prompt(target_root, harness_root, story_text: str) -> str:
    context = build(target_root, harness_root, story_text)
    return context_assembler.render(
        context_assembler.load_template(harness_root, "verifier.md"), context
    )


# --------------------------------------------------------------------------
# read_story returns the parse rather than discarding it
# --------------------------------------------------------------------------


def test_read_story_returns_a_story_reading_with_the_parse_and_the_problems():
    reading = story_coordinator.read_story(DIVERGENT_STORY)
    assert isinstance(reading, story_coordinator.StoryReading)
    assert reading.problems == []
    assert reading.parsed["story"]["id"] == "story-001"
    assert reading.parsed["acceptance_criteria"][0] == COLON_CRITERION


def test_a_parse_failure_yields_no_parse_and_one_line_numbered_message():
    reading = story_coordinator.read_story(
        DIVERGENT_STORY.replace("  - do the work", "\t- do the work")
    )
    assert reading.parsed is None
    assert len(reading.problems) == 1
    assert re.match(r"line \d+: expected .*, found ", reading.problems[0])
    assert "tab" in reading.problems[0]


def test_a_structural_failure_yields_one_message_per_offending_path():
    reading = story_coordinator.read_story(
        DIVERGENT_STORY.replace("\nscope:", "\nboundary:").replace(
            "\nconstraints:", "\nlimits:"
        )
    )
    assert reading.parsed is not None      # it parsed; it is the shape that is wrong
    joined = "\n".join(reading.problems)
    assert "$.scope" in joined and "$.constraints" in joined


def test_story_problems_no_longer_exists_on_the_module():
    assert not hasattr(story_coordinator, "story_problems")


# --------------------------------------------------------------------------
# What the verifier now receives
# --------------------------------------------------------------------------


def test_the_criteria_block_is_dash_prefixed_lines_with_no_yaml_syntax(
    target_root, harness_root
):
    context = build(target_root, harness_root, DIVERGENT_STORY)
    block = context["acceptance_criteria"]
    lines = block.splitlines()
    assert lines == [
        f"- {COLON_CRITERION}",
        "- a plain criterion",
        f"- {WRAPPED_CRITERION}",
    ]
    for line in lines:
        assert not line.startswith(" ")     # no YAML indentation survives
        assert not line[2:].startswith(('"', "'"))


def test_a_colon_criterion_reaches_the_verifier_prompt_whole(
    target_root, harness_root
):
    """The case a conforming YAML reader would have split into a mapping."""
    rendered = verifier_prompt(target_root, harness_root, DIVERGENT_STORY)
    assert f"- {COLON_CRITERION}" in rendered


def test_a_hand_wrapped_criterion_reaches_the_prompt_as_one_joined_line(
    target_root, harness_root
):
    rendered = verifier_prompt(target_root, harness_root, DIVERGENT_STORY)
    assert f"- {WRAPPED_CRITERION}" in rendered
    # Joined onto one line in the criteria block, where the raw slice would
    # have carried the author's line break through.
    block = build(target_root, harness_root, DIVERGENT_STORY)["acceptance_criteria"]
    assert f"- {WRAPPED_CRITERION}" in block.splitlines()
    assert "source line and\n" not in block


def test_an_in_block_comment_does_not_reach_the_verifier_prompt(
    target_root, harness_root
):
    """The previous line-prefix reader carried this comment into the criteria
    block verbatim. It survives only inside the raw {{story}} artifact now,
    where it belongs; the criteria the verifier evaluates are just criteria."""
    context = build(target_root, harness_root, DIVERGENT_STORY)
    assert "must not reach the prompt" not in context["acceptance_criteria"]
    rendered = verifier_prompt(target_root, harness_root, DIVERGENT_STORY)
    assert rendered.count("must not reach the prompt") == 1   # the raw story only
    assert not any(
        line.lstrip("- ").startswith("#")
        for line in context["acceptance_criteria"].splitlines()
    )


def test_every_criterion_of_the_story_reaches_the_rendered_prompt(
    target_root, harness_root
):
    """The verifier loses no information relative to the previous reader."""
    rendered = verifier_prompt(target_root, harness_root, DIVERGENT_STORY)
    for criterion in parse(DIVERGENT_STORY)["acceptance_criteria"]:
        assert f"- {criterion}" in rendered, criterion


def test_every_committed_story_renders_all_of_its_criteria(
    target_root, harness_root
):
    """Corpus check: for each real artifact, no criterion goes missing."""
    stories = sorted(STORIES_DIR.glob("*.yaml"))
    assert stories, "the story corpus is empty"
    for path in stories:
        story_text = path.read_text(encoding="utf-8")
        rendered = verifier_prompt(target_root, harness_root, story_text)
        criteria = parse(story_text)["acceptance_criteria"]
        assert criteria, path.name
        for criterion in criteria:
            assert f"- {criterion}" in rendered, (path.name, criterion)


def test_absent_acceptance_criteria_renders_as_none_rather_than_raising(
    target_root, harness_root
):
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
    assert context_assembler.render("x {{acceptance_criteria}} y", context) == "x None y"


def test_the_story_placeholder_is_the_raw_artifact_byte_for_byte(
    target_root, harness_root
):
    """{{story}} stays unparsed and unreformatted: agents read it as authored."""
    story_path = target_root / ".harness" / "stories" / "story-001.yaml"
    story_path.write_text(DIVERGENT_STORY, encoding="utf-8")
    on_disk = story_path.read_text(encoding="utf-8")

    context = build(target_root, harness_root, on_disk)
    assert context["story"] == on_disk

    rendered = verifier_prompt(target_root, harness_root, on_disk)
    assert on_disk in rendered
    # The raw artifact still carries its YAML shape, comment and all.
    assert "acceptance_criteria:" in rendered
    assert "must not reach the prompt" in rendered.split("[Runtime State")[-1]


# --------------------------------------------------------------------------
# The completion report reads the parsed title
# --------------------------------------------------------------------------


class PassingRunner:
    """Writes each stage's artifacts so the workflow reaches completion."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    def __call__(self, prompt, *, stage, **kwargs):
        changed = {"modified": [], "created": [], "deleted": []}
        if stage == "implementer":
            _write(self.run_dir / "implementation-summary.md", "done\n")
            _write(self.run_dir / "changed-files.json", changed)
        elif stage == "tester":
            _write(self.run_dir / "test-results.json",
                   {"status": "passed", "tests_written": 1, "tests_run": 1,
                    "tests_passed": 1, "tests_failed": 0, "failures": []})
            _write(self.run_dir / "tester-changed-files.json", changed)
        elif stage == "verifier":
            _write(self.run_dir / "verification-result.json",
                   {"status": "passed", "retry_recommended": False,
                    "blocking_issues": [], "summary": "all good"})
        elif stage == "documenter":
            _write(self.run_dir / "documentation-report.md", "documented\n")
        return AgentResult(ok=True, result_text=f"{stage} done")


def _write(path: Path, payload) -> None:
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_completion_report_and_commit_take_the_title_from_the_parse(
    target_root, harness_root
):
    (target_root / ".harness" / "stories" / "story-001.yaml").write_text(
        DIVERGENT_STORY, encoding="utf-8"
    )
    run_dir = target_root / ".harness" / "runs" / "story-001"
    code = story_coordinator.run_story(
        "story-001", harness_root, target_root, PassingRunner(run_dir)
    )
    assert code == 0
    title = parse(DIVERGENT_STORY)["story"]["title"]
    report = (run_dir / "completion-report.md").read_text()
    assert f"## Story\n{title}\n" in report
    assert f"story-001: {title}" in _last_commit_subject(target_root)


def test_the_title_source_is_the_parse_not_the_first_title_shaped_line(
    target_root, harness_root
):
    """A `title:`-shaped line inside the description used to win the line scan.

    The old reader took the first line whose strip() started with `title:`,
    wherever it appeared; the parse takes story.title.
    """
    story_text = DIVERGENT_STORY.replace(
        "  id: story-001\n",
        "  id: story-001\n  # a decoy the line scan would have found first\n",
    ).replace(
        "  description: |\n",
        "  description: |\n    title: not the real title\n",
    )
    (target_root / ".harness" / "stories" / "story-001.yaml").write_text(
        story_text, encoding="utf-8"
    )
    run_dir = target_root / ".harness" / "runs" / "story-001"
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, PassingRunner(run_dir)
    ) == 0
    report = (run_dir / "completion-report.md").read_text()
    assert "Divergence cases the old reader could get wrong" in report
    assert "not the real title" not in report


def _last_commit_subject(target_root: Path) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(target_root), "log", "-1", "--pretty=%s"],
        capture_output=True, text=True,
    ).stdout


# --------------------------------------------------------------------------
# Pre-flight refusal is unchanged, and read_story is called once
# --------------------------------------------------------------------------


def test_read_story_is_called_once_above_the_run_directory_and_the_branch():
    source = Path(story_coordinator.__file__).read_text(encoding="utf-8")
    assert source.count("read_story(") == 2      # the definition and its one call
    call = source.index("reading = read_story(")
    assert call < source.index("run_dir.mkdir(")
    # The checkout *call* in run_story, not the helper's definition above it.
    assert call < source.rindex("_checkout_story_branch(target_root")


def test_a_rejected_story_still_leaves_no_run_directory_state_log_or_branch(
    target_root, harness_root, capsys
):
    import subprocess

    def exploding(prompt, *, stage, **kwargs):
        raise AssertionError("no agent may be invoked for a rejected story")

    (target_root / ".harness" / "stories" / "story-001.yaml").write_text(
        DIVERGENT_STORY.replace("\ntasks:", "\nwork_items:"), encoding="utf-8"
    )
    before = subprocess.run(
        ["git", "-C", str(target_root), "branch", "--format=%(refname:short)"],
        capture_output=True, text=True,
    ).stdout

    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, exploding
    ) == 1
    assert "tasks" in capsys.readouterr().err

    run_dir = target_root / ".harness" / "runs" / "story-001"
    assert not run_dir.exists()
    assert not (run_dir / "state.json").exists()
    assert not (target_root / ".harness" / "logs" / "story-001.log").exists()
    after = subprocess.run(
        ["git", "-C", str(target_root), "branch", "--format=%(refname:short)"],
        capture_output=True, text=True,
    ).stdout
    assert after == before
    assert "story/story-001" not in after


# --------------------------------------------------------------------------
# No second reader survives anywhere in orchestration/
# --------------------------------------------------------------------------


def test_no_line_prefix_scan_of_story_text_remains_in_orchestration():
    for path in sorted((HARNESS_ROOT / "orchestration").glob("*.py")):
        if path.name == "story_parser.py":       # the one reader, by design
            continue
        source = path.read_text(encoding="utf-8")
        assert "extract_section" not in source, path.name
        assert "story_text.splitlines()" not in source, path.name
        assert "title:" not in source, path.name


def test_build_context_requires_the_parsed_story(target_root, harness_root):
    import inspect

    signature = inspect.signature(context_assembler.build_context)
    story = signature.parameters["story"]
    assert story.kind is inspect.Parameter.KEYWORD_ONLY
    assert story.default is inspect.Parameter.empty
    assert "story_text" in signature.parameters


def test_no_conforming_yaml_library_was_introduced():
    for path in sorted((HARNESS_ROOT / "orchestration").glob("*.py")):
        if path.name == "story_parser.py":   # its docstring names what it forbids
            continue
        source = path.read_text(encoding="utf-8")
        assert "yaml.safe_load" not in source, path.name
        assert not re.search(r"^import yaml", source, re.M), path.name
