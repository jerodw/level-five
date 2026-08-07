# story-013 — implementation summary

Moved the schema inventory out of `tests/` and beside the schemas it declares.

## What changed

**`schemas/manifest.json` (created)** — the single declared inventory. A JSON
object with one key, `schemas`, holding the sorted list of the six shipped
schema names as kebab-case stems without the `.schema.json` suffix, matching
how `load_schema` already names them: `changed-files`, `execution-history`,
`retry-guidance`, `story`, `test-results`, `verification-result`. No schema was
added, removed, or edited by this story.

**`orchestration/schema_validator.py` (modified)** — added
`shipped_schemas(harness_root: Path | None = None) -> tuple[str, ...]` beside
`schemas_dir` and `load_schema`. It resolves the manifest through
`schemas_dir(harness_root)`, so the `harness_root` override behaves identically
to the other two. Two module constants back it: `MANIFEST_NAME`
(`"manifest.json"`) and `MANIFEST_KEY` (`"schemas"`), so no test needs to spell
the manifest filename itself. The function raises `ValueError` on every
degradation path rather than returning an empty or partial inventory — missing
or unreadable file, unparseable JSON, a payload that is not an object with a
`schemas` array, or a non-string/empty entry in that array. That matters
because the per-schema parametrized checks expand over its return value: an
empty inventory would make them silently vacuous rather than failing.

**`tests/test_schema_validator.py` (modified)** — deleted the `SHIPPED` tuple,
replaced with a module-level `schema_validator.shipped_schemas()` call so the
two `@pytest.mark.parametrize` cases still expand at collection time. Narrowed
the directory glob in `test_shipped_schemas_are_exactly_the_named_ones` to
`*.schema.json` and added the companion assertion in the same test that
`iterdir()` yields exactly those schemas plus `MANIFEST_NAME` — the coverage
the old `glob("*")` gave for free.

**`tests/test_story_004_validation.py` (modified)** — deleted the
`SHIPPED_SCHEMAS` set the same way, keeping all four of its uses reading from
the one manifest: the workflow schema-map check, the required-is-a-property
walk, the unsupported-keyword walk, and the directory equality assertion. That
last one was narrowed to `glob("*.schema.json")` and given the same companion
`iterdir()` assertion.

## Decisions

- The manifest key is `schemas` rather than something like `shipped`, and the
  list is flat strings rather than objects. The value of the inventory is that
  adding a schema is a deliberate, noticed act; a one-line diff of a filename
  stem is the cheapest shape that still forces that act.
- Both directory equality assertions stayed *exact set equality*. Neither was
  relaxed to a subset or a containment check, and the narrowed glob is paired
  with an `iterdir()` assertion in the same test so the stray-file hole the
  narrowing would otherwise open is closed in the same place.
- The edits under `tests/` are confined to deleting the two inventory
  definitions and repointing their assertions. No test file was created, no
  test function added, and no assertion changed other than the two bound to the
  inventory (both directory-equality checks, which gained the companion
  `iterdir()` line and the narrowed glob).
- `context_assembler.schema_context` already globs `*.schema.json`
  (`orchestration/context_assembler.py:45`), so `manifest.json` becomes no
  placeholder and no `{{manifest_schema}}` can appear in a rendered prompt. No
  change was needed there.
- `orchestration/story_coordinator.py` was not edited. Nothing in orchestration
  routes on the manifest; `shipped_schemas` has no caller inside
  `orchestration/`, only the two test modules.
- `FIRST_SCHEMA_ERA_STORY` was left where it is, per the story's constraint.

## Test suite

`.venv/bin/python -m pytest tests/ -q` — **424 passed** in 25.61s. No test was
weakened, skipped, or deleted.
