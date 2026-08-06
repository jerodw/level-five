"""Schema-directed parser for story artifacts.

WARNING: THIS DIALECT IS NOT YAML. Do not load a story artifact with
yaml.safe_load, ruamel, or any other conforming YAML library. This parser
and a conforming YAML library will disagree about files already committed
under .harness/stories/, and the YAML reading is the wrong one.

The divergence
--------------
A conforming YAML parser terminates a plain scalar at the first ": ", so
this committed acceptance criterion (.harness/stories/story-003.yaml, line
38) reads as a *mapping* rather than a string::

    - A test demonstrates the one-file-edit property: editing only
      prompts/harness-layer.md changes the harness layer of all three
      rendered stage prompts.

"property: explanation" is the natural way to phrase an acceptance
criterion, so that is the normal case to support, not an outlier to
correct. Local syntax cannot decide whether "- name: value" is a string or
a mapping, so this parser does not ask it to: the *schema* decides.

Parsing is therefore schema-directed. Under a schema node whose
``items.type`` is ``"string"`` (acceptance_criteria, tasks, constraints),
every ``- `` line is taken as the verbatim remainder of its line, colons
included. Under a node whose ``items.type`` is ``"object"``
(technical_plan.likely_file_changes), the same syntax parses into key/value
pairs. The ambiguity YAML cannot resolve from local syntax, the schema
resolves from context.

Other deliberate divergences from YAML
--------------------------------------
- No type coercion. Every scalar parses to a Python ``str``; ``42`` and
  ``true`` are the strings ``"42"`` and ``"true"``.
- A trailing ``# comment`` on a value line is part of the value, because a
  string item is taken verbatim. Only a *full-line* comment is dropped.
- Flow syntax (``[]``, ``{}``), anchors, aliases, tags, multiple documents,
  and the ``>`` folded scalar are not supported.
- A line more indented than its parent that begins neither a sequence item
  nor a new key continues the preceding scalar, joined with one space, so a
  hand-wrapped criterion parses as a single string.

Supported constructs: nested mappings, ``key: |`` block scalars, sequences
of strings, sequences of mappings, quoted scalars, full-line comments, and
blank lines. Tab indentation is rejected.

Every error is a :class:`StoryParseError` carrying the line number, what
was expected, and what was found, so the coordinator can print it directly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# A sequence item is read as a mapping entry only when the schema is silent
# about the item type; a schema that says "string" always wins.
_INFERRED_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*:(\s|$)")

_BLOCK_SCALAR = re.compile(r"^(?P<key>[^:]+):[ \t]*\|[ \t]*$")

_QUOTES = ("\"", "'")


class StoryParseError(ValueError):
    """A story artifact the parser refuses, located at a line."""

    def __init__(self, line: int, expected: str, found: str) -> None:
        self.line = line
        self.expected = expected
        self.found = found
        super().__init__(f"line {line}: expected {expected}, found {found}")


@dataclass(frozen=True)
class Line:
    """One significant line: where it is, how deep it sits, what it says."""

    number: int
    indent: int
    text: str
    block: str | None = None


def parse(story_text: str, schema: dict) -> dict:
    """Parse a story artifact into a dict, guided by ``schema``.

    ``schema`` is a story schema in the subset ``schema_validator`` supports;
    only ``type``, ``properties``, and ``items`` steer parsing. Raises
    :class:`StoryParseError` on anything it cannot read.
    """
    lines = lex(story_text)
    if not lines:
        raise StoryParseError(1, "a story mapping", "an empty document")
    if lines[0].indent != 0:
        raise StoryParseError(
            lines[0].number,
            "a top-level entry starting at column 0",
            f"a line indented {lines[0].indent} space(s)",
        )
    reader = _Reader(lines)
    parsed = reader.mapping(schema, 0)
    if reader.pos < len(lines):
        stray = lines[reader.pos]
        raise StoryParseError(
            stray.number,
            "a top-level 'key: value' entry or the end of the document",
            f"a line indented {stray.indent} space(s) ({stray.text!r})",
        )
    return parsed


def lex(story_text: str) -> list[Line]:
    """Turn story text into significant lines, consuming block scalars whole.

    Blank lines and full-line comments are dropped. A ``key: |`` line carries
    its whole block body, so blank and comment-shaped lines *inside* a block
    scalar survive untouched.
    """
    raw_lines = story_text.splitlines()
    lines: list[Line] = []
    index = 0
    while index < len(raw_lines):
        raw = raw_lines[index]
        number = index + 1
        if not raw.strip():
            index += 1
            continue
        indent = _indent_of(raw, number)
        text = raw.strip()
        if text.startswith("#"):
            index += 1
            continue
        block_key = None if text.startswith("- ") else _BLOCK_SCALAR.match(text)
        if block_key:
            body, index = _read_block(raw_lines, index + 1, indent)
            lines.append(Line(number, indent, f"{block_key.group('key')}:", body))
            continue
        lines.append(Line(number, indent, text))
        index += 1
    return lines


def _indent_of(raw: str, number: int) -> int:
    leading = raw[: len(raw) - len(raw.lstrip())]
    if "\t" in leading:
        raise StoryParseError(
            number, "indentation made of spaces", "a tab character"
        )
    return len(leading)


def _read_block(raw_lines: list[str], start: int, key_indent: int) -> tuple[str, int]:
    """Collect the body of a block scalar introduced on the preceding line."""
    body: list[str] = []
    index = start
    while index < len(raw_lines):
        raw = raw_lines[index]
        if not raw.strip():
            body.append("")
            index += 1
            continue
        if _indent_of(raw, index + 1) <= key_indent:
            break
        body.append(raw)
        index += 1
    while body and not body[-1].strip():
        body.pop()
    if not body:
        return "", index
    margin = min(len(line) - len(line.lstrip()) for line in body if line.strip())
    return "\n".join(line[margin:] if line.strip() else "" for line in body) + "\n", index


def _unquote(text: str) -> str:
    """Strip a matching pair of surrounding quotes, if that is unambiguous."""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in _QUOTES:
        if text[0] not in text[1:-1]:
            return text[1:-1]
    return text


class _Reader:
    """Walks the lexed lines alongside the schema node it is interpreting."""

    def __init__(self, lines: list[Line]) -> None:
        self.lines = lines
        self.pos = 0

    def _peek(self) -> Line | None:
        return self.lines[self.pos] if self.pos < len(self.lines) else None

    # -- mappings ---------------------------------------------------------

    def mapping(self, schema: dict, indent: int, first: Line | None = None) -> dict:
        """Read ``key: value`` entries at ``indent`` into a dict.

        ``first`` is the entry carried on a ``- `` sequence-item line, which
        belongs to this mapping but was already taken from the stream.
        """
        properties = schema.get("properties") or {}
        result: dict[str, object] = {}
        pending = first
        while True:
            if pending is not None:
                line, pending = pending, None
            else:
                line = self._peek()
                if line is None or line.indent != indent:
                    break
                self.pos += 1
            key, value = self._entry(line, indent, properties)
            result[key] = value
        return result

    def _entry(self, line: Line, indent: int, properties: dict) -> tuple[str, object]:
        if line.text.startswith("- "):
            raise StoryParseError(
                line.number,
                "a 'key: value' entry",
                f"a sequence item ({line.text!r})",
            )
        key, separator, rest = line.text.partition(":")
        key = key.strip()
        if not separator or not key:
            raise StoryParseError(
                line.number,
                "a 'key: value' entry",
                f"a line with no key ({line.text!r})",
            )
        subschema = properties.get(key) or {}
        if line.block is not None:
            return key, line.block
        if rest.strip():
            return key, self.scalar(rest.strip(), indent)
        return key, self._nested(subschema, indent, line, key)

    def _nested(self, schema: dict, indent: int, line: Line, key: str) -> object:
        """Read the block indented beneath a bare ``key:`` line."""
        child = self._peek()
        if child is None or child.indent <= indent:
            raise StoryParseError(
                line.number,
                f"a value or an indented block for {key!r}",
                "nothing indented beneath it",
            )
        kind = _kind(schema, child)
        if kind == "array":
            return self.sequence(schema, child.indent, line)
        if kind == "object":
            return self.mapping(schema, child.indent)
        self.pos += 1
        return self.scalar(child.text, indent)

    # -- sequences --------------------------------------------------------

    def sequence(self, schema: dict, indent: int, opener: Line) -> list:
        items_schema = schema.get("items") or {}
        result: list[object] = []
        while True:
            line = self._peek()
            if line is None or line.indent != indent or not line.text.startswith("- "):
                break
            self.pos += 1
            remainder = line.text[2:].strip()
            if items_schema.get("type") == "object" or (
                not items_schema.get("type") and _INFERRED_KEY.match(remainder)
            ):
                result.append(self._sequence_item_mapping(items_schema, indent, line, remainder))
            else:
                result.append(self.scalar(remainder, indent))
        if not result:
            found = self._peek()
            raise StoryParseError(
                found.number if found else opener.number,
                "a sequence item beginning with '- '",
                f"{found.text!r}" if found else "nothing",
            )
        return result

    def _sequence_item_mapping(
        self, items_schema: dict, indent: int, line: Line, remainder: str
    ) -> dict:
        """Read ``- key: value`` plus the keys indented under it."""
        following = self._peek()
        inner = (
            following.indent
            if following is not None and following.indent > indent
            else indent + 2
        )
        first = Line(line.number, inner, remainder, line.block)
        return self.mapping(items_schema, inner, first=first)

    # -- scalars ----------------------------------------------------------

    def scalar(self, first: str, indent: int) -> str:
        """Read a scalar, joining hand-wrapped continuation lines with a space."""
        parts = [first]
        while True:
            line = self._peek()
            if line is None or line.indent <= indent:
                break
            parts.append(line.text)
            self.pos += 1
        return _unquote(" ".join(parts))


def _kind(schema: dict, child: Line) -> str:
    """What to read next: the schema decides, structure only fills a silence."""
    declared = schema.get("type")
    if declared in ("array", "object", "string"):
        return declared
    if child.text.startswith("- "):
        return "array"
    if "properties" in schema or ":" in child.text:
        return "object"
    return "string"
