"""Independent validation for story-005: schema-directed story parsing.

Written from the story's acceptance criteria outward rather than from the
implementation: the parser is driven through its public ``parse``, the
coordinator through ``run_story`` and ``scripts/l5-run``, and the shipped
prose (module docstring, schema description) is read from the files.

Fixtures here are inline apart from ``target_root``/``harness_root`` from
conftest. The committed corpus under .harness/stories/ is read by discovery,
never by name; nothing here reads .harness/runs/, which is gitignored.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import schema_validator
import story_coordinator
import story_parser
from agent_runner import AgentResult

HARNESS_ROOT = Path(__file__).resolve().parents[1]
STORIES_DIR = HARNESS_ROOT.joinpath(".harness", "stories")

ERROR_SHAPE = re.compile(r"^line (\d+): expected .+, found .+$")

STRING_ITEMS = {
    "type": "object",
    "properties": {"entries": {"type": "array", "items": {"type": "string"}}},
}

OBJECT_ITEMS = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["file", "reason"],
                "properties": {
                    "file": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}

# The exact shape of the committed criterion this story exists to support.
COLON_CRITERION = (
    "A test demonstrates the one-file-edit property: editing only "
    "prompts/harness-layer.md changes the harness layer."
)


def story_schema() -> dict:
    return schema_validator.load_schema("story")


def parse_story(text: str) -> dict:
    return story_parser.parse(text, story_schema())


VALID_STORY = """\
story:
  id: story-999
  title: An inline story used only by this file
  description: |
    Body text.

tasks:
  - do the work

acceptance_criteria:
  - the behavior exists

technical_plan:
  implementation_steps:
    - write the code
  likely_file_changes:
    - file: src/app.py
      reason: it holds the behavior

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


# --------------------------------------------------------------------------
# The schema, not the syntax, decides what a sequence item is
# --------------------------------------------------------------------------


def test_parse_takes_text_and_a_schema_and_returns_a_dict():
    assert callable(story_parser.parse)
    parsed = parse_story(VALID_STORY)
    assert isinstance(parsed, dict)
    assert parsed["story"]["id"] == "story-999"


def test_one_syntax_two_readings_chosen_by_the_schema():
    """The same three characters of syntax; the schema node decides."""
    text = "entries:\n  - file: src/app.py\n"
    assert story_parser.parse(text, STRING_ITEMS) == {"entries": ["file: src/app.py"]}
    assert story_parser.parse(text, OBJECT_ITEMS) == {
        "entries": [{"file": "src/app.py"}]
    }


def test_a_sequence_of_mappings_collects_the_keys_indented_under_the_dash():
    text = (
        "entries:\n"
        "  - file: a.py\n"
        "    reason: first\n"
        "  - file: b.py\n"
        "    reason: second\n"
    )
    assert story_parser.parse(text, OBJECT_ITEMS)["entries"] == [
        {"file": "a.py", "reason": "first"},
        {"file": "b.py", "reason": "second"},
    ]


def test_a_criterion_with_a_colon_parses_to_one_string_not_a_mapping():
    text = f"entries:\n  - {COLON_CRITERION}\n"
    entries = story_parser.parse(text, STRING_ITEMS)["entries"]
    assert entries == [COLON_CRITERION]
    assert not any(isinstance(entry, dict) for entry in entries)
    assert ":" in entries[0]


def test_a_hand_wrapped_criterion_with_a_colon_is_still_one_string():
    text = (
        "entries:\n"
        "  - A test demonstrates the one-file-edit property: editing only\n"
        "    prompts/harness-layer.md changes the harness layer.\n"
    )
    assert story_parser.parse(text, STRING_ITEMS)["entries"] == [COLON_CRITERION]


# --------------------------------------------------------------------------
# The committed corpus parses and validates as written
# --------------------------------------------------------------------------


# Artifacts written before schemas/ existed are execution records, not inputs.
# Holding them to a contract written later can only ever force the schema
# weaker, so the corpus contract starts where the current convention does.
FIRST_SCHEMA_ERA_STORY = "story-003"


def all_committed_stories() -> list[Path]:
    return sorted(STORIES_DIR.glob("*.yaml"))


def committed_stories() -> list[Path]:
    return [p for p in all_committed_stories() if p.stem >= FIRST_SCHEMA_ERA_STORY]


def test_the_corpus_discovery_finds_files_so_the_corpus_test_cannot_pass_on_zero():
    assert committed_stories(), f"no story artifacts discovered under {STORIES_DIR}"


def test_the_schema_is_not_weakened_to_accommodate_pre_schema_artifacts():
    """The reason the corpus is scoped: technical_plan stays typed.

    story-001 and story-002 wrote technical_plan as a free-form block
    scalar. If they were held to this schema, the only way to pass would be
    to drop the type constraint, permanently costing every future story its
    structural checking on that field.
    """
    technical_plan = story_schema()["properties"]["technical_plan"]
    assert technical_plan["type"] == "object"
    entry = technical_plan["properties"]["likely_file_changes"]["items"]
    assert set(entry["required"]) == {"file", "reason"}


def test_every_committed_story_artifact_parses_and_validates():
    schema = story_schema()
    failures = []
    for path in committed_stories():
        try:
            parsed = story_parser.parse(path.read_text(encoding="utf-8"), schema)
        except story_parser.StoryParseError as error:
            failures.append(f"{path.name}: {error}")
            continue
        for problem in schema_validator.validate(parsed, schema):
            failures.append(f"{path.name}: {problem}")
    assert not failures, "\n".join(failures)


def test_the_committed_story_that_motivated_this_change_reads_as_strings():
    """story-003.yaml carries a criterion a YAML parser would call a mapping."""
    path = STORIES_DIR / "story-003.yaml"
    criteria = parse_story(path.read_text(encoding="utf-8"))["acceptance_criteria"]
    assert criteria
    assert all(isinstance(item, str) for item in criteria), criteria
    colon_criteria = [item for item in criteria if ": " in item]
    assert colon_criteria, "story-003 no longer exercises the colon case"


# --------------------------------------------------------------------------
# Supported constructs
# --------------------------------------------------------------------------


def test_nested_mappings_nest():
    parsed = story_parser.parse("a:\n  b:\n    c: deep\n  d: shallow\n", {})
    assert parsed == {"a": {"b": {"c": "deep"}, "d": "shallow"}}


def test_a_pipe_block_scalar_keeps_its_blank_and_comment_shaped_lines():
    text = "description: |\n  first\n\n  # not a comment in here\n  last\nafter: x\n"
    parsed = story_parser.parse(text, {})
    assert parsed["description"] == "first\n\n# not a comment in here\nlast\n"
    assert parsed["after"] == "x"


def test_a_sequence_of_strings_is_a_list_of_strings():
    parsed = story_parser.parse("entries:\n  - one\n  - two\n", STRING_ITEMS)
    assert parsed["entries"] == ["one", "two"]


def test_full_line_comments_and_blank_lines_are_ignored():
    text = "# leading comment\n\na: one\n\n  # indented full-line comment\nb: two\n"
    assert story_parser.parse(text, {}) == {"a": "one", "b": "two"}


def test_a_quoted_scalar_loses_its_quotes():
    assert story_parser.parse('a: "quoted value"\n', {}) == {"a": "quoted value"}


def test_the_parser_coerces_nothing():
    parsed = story_parser.parse("n: 42\nf: 3.5\nb: true\nz: null\n", {})
    assert parsed == {"n": "42", "f": "3.5", "b": "true", "z": "null"}
    assert all(isinstance(value, str) for value in parsed.values())


def test_no_scalar_anywhere_in_the_corpus_is_a_number_or_a_boolean():
    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        else:
            assert isinstance(node, str), f"{node!r} is {type(node).__name__}"

    for path in committed_stories():
        walk(parse_story(path.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------
# Rejections carry a line number, an expectation, and what was found
# --------------------------------------------------------------------------


REJECTIONS = [
    ("tab indentation", "tasks:\n\t- one\n", 2, STRING_ITEMS),
    ("tab deeper in the file", "a: one\nb: two\nc:\n\td: three\n", 4, {}),
    ("a line that is not an entry", "a: one\nnot an entry here\n", 2, {}),
    ("a key with nothing beneath it", "a:\n", {"type": "object"}, None),
    ("a sequence where a mapping is required", "entries:\n  - x\n", 2,
     {"type": "object", "properties": {"entries": {"type": "object"}}}),
    ("a mapping where a sequence is required", "entries:\n  b: c\n", 2, STRING_ITEMS),
    ("an empty document", "\n# only a comment\n", 1, {}),
    ("a document that starts indented", "  a: one\n", 1, {}),
]


@pytest.mark.parametrize("label,text,line,schema", REJECTIONS)
def test_every_rejection_names_a_line_an_expectation_and_what_was_found(
    label, text, line, schema
):
    schema = schema if schema is not None else {"type": "object"}
    expected_line = line if isinstance(line, int) else 1
    with pytest.raises(story_parser.StoryParseError) as caught:
        story_parser.parse(text, schema)
    message = str(caught.value)
    match = ERROR_SHAPE.match(message)
    assert match, f"{label}: {message!r} is not 'line N: expected ..., found ...'"
    assert int(match.group(1)) == expected_line, f"{label}: {message}"
    assert caught.value.line == expected_line
    assert caught.value.expected and caught.value.found


def test_a_tab_indented_line_is_rejected_naming_its_line_number():
    story = VALID_STORY.replace("  - do the work", "\t- do the work")
    with pytest.raises(story_parser.StoryParseError) as caught:
        parse_story(story)
    assert caught.value.line == story.splitlines().index("\t- do the work") + 1
    assert "tab" in str(caught.value)


def test_a_tab_inside_a_block_scalar_is_rejected_too():
    with pytest.raises(story_parser.StoryParseError) as caught:
        story_parser.parse("a: |\n\tbody\n", {})
    assert caught.value.line == 2


# --------------------------------------------------------------------------
# Pre-flight: parse-then-validate before any run state exists
# --------------------------------------------------------------------------


class ExplodingRunner:
    """Any agent invocation during a rejected story is a test failure."""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, **kwargs):
        self.calls.append(stage)
        raise AssertionError(f"agent invoked for stage {stage} on a rejected story")
        return AgentResult(ok=False, result_text="")


def story_file(target_root: Path) -> Path:
    return target_root / ".harness" / "stories" / "story-001.yaml"


def branch_names(target_root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(target_root), "branch", "--format=%(refname:short)"],
        capture_output=True, text=True, check=True,
    )
    return set(result.stdout.split())


def reject(target_root: Path, harness_root: Path, story_text: str) -> int:
    """Run a story that should be refused; assert it left nothing behind."""
    before = branch_names(target_root)
    story_file(target_root).write_text(story_text, encoding="utf-8")
    runner = ExplodingRunner()
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert runner.calls == []
    assert not (target_root / ".harness" / "runs" / "story-001").exists()
    assert not (target_root / ".harness" / "runs" / "story-001" / "state.json").exists()
    assert not (target_root / ".harness" / "logs" / "story-001.log").exists()
    assert branch_names(target_root) == before
    assert "story/story-001" not in branch_names(target_root)
    return code


def test_a_valid_story_reports_no_problems_and_still_runs(target_root, harness_root):
    assert story_coordinator.read_story(VALID_STORY).problems == []
    story_file(target_root).write_text(VALID_STORY, encoding="utf-8")
    calls: list[str] = []

    def runner(prompt, *, stage, **kwargs):
        calls.append(stage)
        return AgentResult(ok=False, result_text="stopped after the first stage")

    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert calls == ["implementer"], "pre-flight must not refuse a valid story"
    assert code == 2


@pytest.mark.parametrize("section", [
    "tasks", "acceptance_criteria", "scope", "verification_requirements", "constraints",
])
def test_a_missing_top_level_section_is_rejected_and_named(target_root, harness_root,
                                                            capsys, section):
    text = VALID_STORY.replace(f"\n{section}:", f"\n{section}_renamed:")
    assert reject(target_root, harness_root, text) == 1
    err = capsys.readouterr().err
    assert section in err
    assert "missing" in err


def test_several_missing_sections_are_all_named(target_root, harness_root, capsys):
    text = VALID_STORY.replace("\nscope:", "\nboundary:").replace(
        "\nconstraints:", "\nlimits:")
    assert reject(target_root, harness_root, text) == 1
    err = capsys.readouterr().err
    assert "scope" in err and "constraints" in err


def test_a_scope_without_do_not_modify_is_now_rejected(target_root, harness_root,
                                                        capsys):
    """The case this story exists to catch: valid at the top level, wrong below."""
    text = VALID_STORY.replace("  do_not_modify:\n    - rules/\n", "")
    assert "scope:" in text and "do_not_modify" not in text
    assert reject(target_root, harness_root, text) == 1
    assert "$.scope.do_not_modify" in capsys.readouterr().err


def test_a_likely_file_changes_entry_missing_file_is_rejected(target_root,
                                                              harness_root, capsys):
    text = VALID_STORY.replace(
        "    - file: src/app.py\n      reason: it holds the behavior\n",
        "    - reason: it holds the behavior\n",
    )
    assert reject(target_root, harness_root, text) == 1
    err = capsys.readouterr().err
    assert "$.technical_plan.likely_file_changes[0].file" in err
    assert "missing" in err


def test_an_unparseable_story_is_rejected_with_a_line_numbered_message(
    target_root, harness_root, capsys
):
    text = VALID_STORY.replace("  - do the work", "\t- do the work")
    assert reject(target_root, harness_root, text) == 1
    err = capsys.readouterr().err
    assert ERROR_SHAPE.search(err.replace("  - ", "")) or "line " in err
    assert "tab" in err


def test_l5_run_exits_1_and_creates_nothing_for_a_rejected_story(target_root):
    story_file(target_root).write_text(
        VALID_STORY.replace("\ntasks:", "\nwork_items:"), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(HARNESS_ROOT / "scripts" / "l5-run"), "story-001"],
        cwd=target_root, capture_output=True, text=True,
    )
    assert result.returncode == 1, result.stderr
    assert "tasks" in result.stderr
    assert not (target_root / ".harness" / "runs" / "story-001").exists()
    assert not (target_root / ".harness" / "logs" / "story-001.log").exists()
    assert "story/story-001" not in branch_names(target_root)


def test_pre_flight_validates_against_the_schema_file_on_disk(tmp_path):
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "story.schema.json").write_text(json.dumps({
        "type": "object",
        "required": ["story", "invented_section"],
        "properties": {"story": {"type": "object"}},
    }), encoding="utf-8")
    problems = story_coordinator.read_story("story:\n  id: x\n", tmp_path).problems
    assert any("invented_section" in problem for problem in problems)


# --------------------------------------------------------------------------
# One mechanism, no YAML library, and the divergence written down
# --------------------------------------------------------------------------


def test_the_line_prefix_helpers_are_gone_from_the_coordinator():
    source = Path(story_coordinator.__file__).read_text(encoding="utf-8")
    for obsolete in ("REQUIRED_STORY_SECTIONS", "load_required_story_sections",
                     "missing_story_sections"):
        assert not hasattr(story_coordinator, obsolete), obsolete
        assert obsolete not in source, obsolete


def test_no_module_or_test_still_calls_the_removed_helpers():
    """A guard may quote the names; nothing may still call them."""
    for directory in ("orchestration", "tests", "scripts"):
        for path in sorted(HARNESS_ROOT.joinpath(directory).iterdir()):
            if path.is_dir():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for obsolete in ("REQUIRED_STORY_SECTIONS", "load_required_story_sections",
                             "missing_story_sections"):
                call = f"story_coordinator.{obsolete}"
                assert call not in text, f"{path.name} calls {call}"
                if directory == "orchestration":
                    assert obsolete not in text, f"{path.name} references {obsolete}"


def test_the_harness_imports_no_yaml_library():
    pattern = re.compile(r"^\s*(import|from)\s+(yaml|ruamel)", re.MULTILINE)
    for directory in ("orchestration", "scripts"):
        for path in sorted(HARNESS_ROOT.joinpath(directory).iterdir()):
            if path.is_dir():
                continue
            assert not pattern.search(path.read_text(encoding="utf-8", errors="ignore")), path


def test_the_parser_docstring_warns_that_the_dialect_is_not_yaml():
    doc = story_parser.__doc__ or ""
    lowered = doc.lower()
    assert "not yaml" in lowered
    assert "yaml.safe_load" in doc
    assert "plain scalar" in lowered
    assert ": " in doc and "colon" in lowered


def test_the_story_schema_description_records_the_divergence():
    description = story_schema()["description"].lower()
    assert "parse" in description
    assert "not yaml" in description
    assert "yaml.safe_load" in description


def test_the_parser_reuses_schema_validator_rather_than_reimplementing_it():
    parser_source = Path(story_parser.__file__).read_text(encoding="utf-8")
    assert "def validate(" not in parser_source
    coordinator_source = Path(story_coordinator.__file__).read_text(encoding="utf-8")
    assert "schema_validator.validate(" in coordinator_source
    assert "schema_validator.load_schema(" in coordinator_source
