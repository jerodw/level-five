"""Validation for story-005: the schema-directed story parser.

Every fixture here is inline except the corpus test, which deliberately
discovers the committed story artifacts under .harness/stories/ rather than
naming them. That directory is committed and present in CI (unlike
.harness/runs/, which no test may read).
"""
import re
from pathlib import Path

import pytest

import schema_validator
import story_parser
from story_parser import StoryParseError

HARNESS_ROOT = Path(__file__).resolve().parents[1]
STORIES_DIR = HARNESS_ROOT.joinpath(".harness", "stories")

STRING_ITEMS = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {"type": "string"}}},
}
OBJECT_ITEMS = {
    "type": "object",
    "properties": {
        "items": {
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


def story_schema() -> dict:
    return schema_validator.load_schema("story")


# --------------------------------------------------------------------------
# The schema decides, not the syntax
# --------------------------------------------------------------------------


AMBIGUOUS = "items:\n  - file: orchestration/story_parser.py\n"


def test_a_sequence_item_with_a_colon_is_one_string_under_a_string_node():
    parsed = story_parser.parse(AMBIGUOUS, STRING_ITEMS)
    assert parsed == {"items": ["file: orchestration/story_parser.py"]}


def test_the_same_line_is_key_and_value_under_an_object_node():
    parsed = story_parser.parse(AMBIGUOUS, OBJECT_ITEMS)
    assert parsed == {"items": [{"file": "orchestration/story_parser.py"}]}


def test_the_colon_in_an_acceptance_criterion_survives_verbatim():
    """The exact shape a conforming YAML parser would read as a mapping."""
    text = (
        "acceptance_criteria:\n"
        "  - A test demonstrates the one-file-edit property: editing only\n"
        "    prompts/harness-layer.md changes the harness layer.\n"
    )
    criteria = story_parser.parse(text, story_schema())["acceptance_criteria"]
    assert criteria == [
        "A test demonstrates the one-file-edit property: editing only "
        "prompts/harness-layer.md changes the harness layer."
    ]
    assert all(isinstance(item, str) for item in criteria)


# --------------------------------------------------------------------------
# Supported constructs
# --------------------------------------------------------------------------


DOCUMENT = """\
# a full-line comment before anything
story:
  id: story-042
  title: A story with every construct
  description: |
    First paragraph of the block scalar.

      An indented line inside the block, and a # that is not a comment.

    Last paragraph.

tasks:
  - a plain item
  - an item hand-wrapped across
    two lines

  # a full-line comment inside a sequence
  - "a quoted item, with a comma"

acceptance_criteria:
  - the parser works

technical_plan:
  implementation_steps:
    - write it
  likely_file_changes:
    - file: orchestration/story_parser.py
      reason: New module.
    - file: orchestration/story_coordinator.py
      reason: Parse then validate.

scope:
  modify:
    - orchestration/
  do_not_modify:
    - rules/

verification_requirements:
  - confirm it

constraints:
  - stdlib only
"""


@pytest.fixture
def document() -> dict:
    return story_parser.parse(DOCUMENT, story_schema())


def test_nested_mappings_parse(document):
    assert document["story"]["id"] == "story-042"
    assert document["story"]["title"] == "A story with every construct"


def test_a_pipe_block_scalar_keeps_its_shape(document):
    assert document["story"]["description"] == (
        "First paragraph of the block scalar.\n"
        "\n"
        "  An indented line inside the block, and a # that is not a comment.\n"
        "\n"
        "Last paragraph.\n"
    )


def test_a_sequence_of_strings_parses(document):
    assert document["acceptance_criteria"] == ["the parser works"]
    assert document["scope"]["modify"] == ["orchestration/"]
    assert document["scope"]["do_not_modify"] == ["rules/"]


def test_a_sequence_of_mappings_parses(document):
    assert document["technical_plan"]["likely_file_changes"] == [
        {"file": "orchestration/story_parser.py", "reason": "New module."},
        {"file": "orchestration/story_coordinator.py", "reason": "Parse then validate."},
    ]


def test_wrapped_lines_join_with_a_single_space(document):
    assert document["tasks"][1] == "an item hand-wrapped across two lines"


def test_a_quoted_scalar_loses_its_quotes(document):
    assert document["tasks"][2] == "a quoted item, with a comma"


def test_comments_and_blank_lines_are_dropped(document):
    assert document["tasks"][0] == "a plain item"
    assert len(document["tasks"]) == 3
    assert "comment" not in str(document["tasks"])


def test_the_whole_document_validates(document):
    assert schema_validator.validate(document, story_schema()) == []


def test_the_parser_coerces_nothing():
    """Numbers and booleans stay strings; the harness routes on text."""
    text = "items:\n  - 42\n  - true\n  - null\n  - 3.14\n"
    assert story_parser.parse(text, STRING_ITEMS) == {
        "items": ["42", "true", "null", "3.14"]
    }


def test_a_scalar_value_is_a_string_even_when_it_looks_numeric():
    schema = {"type": "object", "properties": {"count": {"type": "string"}}}
    parsed = story_parser.parse("count: 7\n", schema)
    assert parsed["count"] == "7"
    assert isinstance(parsed["count"], str)


def test_a_key_the_schema_does_not_name_still_parses_structurally():
    parsed = story_parser.parse("extra:\n  - one\n  - two\n", {"type": "object"})
    assert parsed == {"extra": ["one", "two"]}


# --------------------------------------------------------------------------
# Rejections, every one carrying a line number
# --------------------------------------------------------------------------


def test_a_tab_indented_line_is_rejected_naming_its_line():
    text = "story:\n  id: story-042\n\ttitle: tabbed\n"
    with pytest.raises(StoryParseError) as caught:
        story_parser.parse(text, story_schema())
    assert caught.value.line == 3
    assert "tab" in str(caught.value)
    assert "line 3" in str(caught.value)


REJECTIONS = [
    ("a tab in the indentation", "story:\n\tid: x\n", 2),
    ("a line that is not a key", "story:\n  a line with no key\n", 2),
    ("a sequence item where a mapping belongs", "story:\n  - id: x\n", 2),
    ("a key with nothing beneath it", "story:\n", 1),
    ("an empty document", "\n# only a comment\n", 1),
    ("a document that does not start at column 0", "  story:\n    id: x\n", 1),
    ("a sequence where the schema requires one", "tasks:\n  a: b\n", 2),
]


@pytest.mark.parametrize("label,text,line", REJECTIONS, ids=[r[0] for r in REJECTIONS])
def test_every_parse_error_names_the_line_the_expectation_and_the_finding(
    label, text, line
):
    with pytest.raises(StoryParseError) as caught:
        story_parser.parse(text, story_schema())
    message = str(caught.value)
    assert re.match(r"^line \d+: expected .+, found .+$", message), message
    assert caught.value.line == line
    assert message.startswith(f"line {line}: ")
    assert caught.value.expected and caught.value.found


def test_an_error_carries_its_parts_separately_for_the_coordinator():
    with pytest.raises(StoryParseError) as caught:
        story_parser.parse("story:\n  no key here\n", story_schema())
    error = caught.value
    assert error.line == 2
    assert "key: value" in error.expected
    assert "no key here" in error.found


# --------------------------------------------------------------------------
# The committed corpus
# --------------------------------------------------------------------------


def committed_stories() -> list[Path]:
    return sorted(STORIES_DIR.glob("*.yaml"))


def test_the_stories_directory_is_present_and_not_empty():
    """Guards the corpus test below against silently discovering nothing."""
    assert committed_stories(), f"no story artifacts found under {STORIES_DIR}"


def test_every_committed_story_parses_and_validates():
    schema = story_schema()
    failures = []
    for path in committed_stories():
        try:
            parsed = story_parser.parse(path.read_text(encoding="utf-8"), schema)
        except StoryParseError as error:
            failures.append(f"{path.name}: {error}")
            continue
        for problem in schema_validator.validate(parsed, schema):
            failures.append(f"{path.name}: {problem}")
    assert not failures, "\n".join(failures)


def test_story_003_parses_as_written_with_no_mapping_among_its_criteria():
    """The artifact this story refused to edit, read as its author wrote it."""
    path = STORIES_DIR / "story-003.yaml"
    text = path.read_text(encoding="utf-8")
    assert (
        "  - A test demonstrates the one-file-edit property: editing only "
        in text
    ), "story-003.yaml was edited; this story must parse it as written"

    parsed = story_parser.parse(text, story_schema())
    assert schema_validator.validate(parsed, story_schema()) == []
    criteria = parsed["acceptance_criteria"]
    assert all(isinstance(item, str) for item in criteria), criteria
    assert not any(isinstance(item, dict) for item in criteria)
    assert any(
        item.startswith("A test demonstrates the one-file-edit property: editing only")
        for item in criteria
    ), criteria
