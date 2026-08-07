"""story-013: the schema inventory moves out of tests/ into schemas/manifest.json.

The story's claim is that the inventory can live beside the schemas it
declares without losing what made it valuable: adding an artifact shape to
the harness still cannot happen unnoticed. These tests hold that claim to
its two halves.

The declaration half is checked directly - the manifest names exactly the
six shipped schemas, the accessor reads it, and every degradation path
raises instead of handing back an empty inventory that would make the
per-schema parametrized checks silently vacuous.

The enforcement half is checked by mutation rather than by reading the
assertions and reasoning about them. A copy of the harness is made in a
temporary directory, one thing is changed in it - a schema file with no
manifest entry, a manifest entry with no schema file, a stray non-schema
file - and the repository's own inventory tests are run against that copy
to show they fail. The same machinery demonstrates the story's central
property: adding a schema together with its manifest line turns the suite
green again while every file under tests/ stays byte-identical, so the
paths a new schema requires editing are all under schemas/ and none are
under tests/.
"""
import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import context_assembler
import schema_validator

REPO_ROOT = Path(schema_validator.__file__).resolve().parents[1]

# Named here, not read from the manifest: this is the story's own statement
# of what the harness ships today, and it is the thing the manifest is
# checked against. Reading it from the manifest would check nothing.
EXPECTED_SCHEMAS = (
    "changed-files",
    "execution-history",
    "retry-guidance",
    "story",
    "test-results",
    "verification-result",
)

# The two tests that hold the directory to the declared inventory. Both are
# run against mutated copies of the harness below.
INVENTORY_TESTS = (
    "tests/test_schema_validator.py::test_shipped_schemas_are_exactly_the_named_ones",
    "tests/test_story_004_validation.py::test_schemas_directory_holds_exactly_the_named_schemas",
)

INVENTORY_TEST_FILES = ("tests/test_schema_validator.py",
                        "tests/test_story_004_validation.py")

# A well-formed schema in the subset the validator supports, so a mutation
# test fails because of the inventory and never because the file it added is
# a bad schema.
THROWAWAY_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "throwaway",
    "description": "A schema added by a test to exercise the inventory.",
    "type": "object",
    "required": ["note"],
    "properties": {"note": {"type": "string", "description": "anything"}},
}

COPIED = ("orchestration", "schemas", "tests", "workflows", "prompts",
          "scripts", "rules")


# --------------------------------------------------------------------------
# The manifest is the declaration
# --------------------------------------------------------------------------


def test_manifest_names_exactly_the_six_shipped_schemas():
    """No schema added and none removed by this story."""
    manifest = json.loads(
        (REPO_ROOT / "schemas" / "manifest.json").read_text(encoding="utf-8"))
    assert sorted(manifest[schema_validator.MANIFEST_KEY]) == sorted(EXPECTED_SCHEMAS)


def test_every_named_schema_has_a_file_and_every_file_is_named():
    directory = REPO_ROOT / "schemas"
    assert {p.name for p in directory.glob("*.schema.json")} == \
        {f"{name}.schema.json" for name in EXPECTED_SCHEMAS}


def test_shipped_schemas_returns_the_declared_names():
    assert sorted(schema_validator.shipped_schemas()) == sorted(EXPECTED_SCHEMAS)
    assert isinstance(schema_validator.shipped_schemas(), tuple)


def test_shipped_schemas_accepts_a_harness_root_override(tmp_path: Path):
    """The override behaves like load_schema's and schemas_dir's: the caller's
    root, not the module's."""
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "manifest.json").write_text(
        json.dumps({schema_validator.MANIFEST_KEY: ["only-this-one"]}),
        encoding="utf-8")
    assert schema_validator.shipped_schemas(tmp_path) == ("only-this-one",)
    # And the default root is untouched by the override.
    assert sorted(schema_validator.shipped_schemas()) == sorted(EXPECTED_SCHEMAS)


def test_shipped_schemas_carries_type_hints():
    annotations = schema_validator.shipped_schemas.__annotations__
    assert "harness_root" in annotations
    assert "return" in annotations
    source = ast.parse((REPO_ROOT / "orchestration" / "schema_validator.py")
                       .read_text(encoding="utf-8"))
    function = next(node for node in source.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "shipped_schemas")
    assert ast.unparse(function.returns) == "tuple[str, ...]"
    argument = function.args.args[0]
    assert argument.arg == "harness_root" and argument.annotation is not None


# --------------------------------------------------------------------------
# A degraded manifest raises rather than emptying the inventory
# --------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [
    pytest.param(None, id="missing-file"),
    pytest.param("{ not json", id="unparseable"),
    pytest.param('["changed-files"]', id="top-level-array"),
    pytest.param('"changed-files"', id="top-level-string"),
    pytest.param("{}", id="no-schemas-key"),
    pytest.param('{"schemas": "changed-files"}', id="schemas-not-a-list"),
    pytest.param('{"schemas": ["changed-files", 7]}', id="entry-not-a-string"),
    pytest.param('{"schemas": ["changed-files", ""]}', id="empty-entry"),
])
def test_a_degraded_manifest_raises(tmp_path: Path, payload):
    """An empty or partial inventory would make the per-schema parametrized
    checks expand over nothing and pass vacuously, which is worse than the
    loud failure."""
    (tmp_path / "schemas").mkdir()
    if payload is not None:
        (tmp_path / "schemas" / "manifest.json").write_text(payload, encoding="utf-8")
    with pytest.raises(Exception) as raised:
        schema_validator.shipped_schemas(tmp_path)
    assert not isinstance(raised.value, AssertionError)


def test_an_empty_inventory_is_never_returned_for_a_degraded_manifest(tmp_path: Path):
    """The failure mode this guards is silence, so state it as its own fact."""
    (tmp_path / "schemas").mkdir()
    try:
        result = schema_validator.shipped_schemas(tmp_path)
    except Exception:
        return
    raise AssertionError(f"missing manifest yielded {result!r} instead of raising")


# --------------------------------------------------------------------------
# Exactly one declared inventory survives, established by search
# --------------------------------------------------------------------------


# A collection naming this many of the six is claiming to be the inventory.
# Not two: tests legitimately name the pair of schemas one prompt injects,
# or the pair of artifacts one stage writes, and those are references to
# particular schemas rather than a statement of what the harness ships.
INVENTORY_THRESHOLD = 4

SCHEMA_NAME_LITERALS = set(EXPECTED_SCHEMAS) | {
    f"{name}.schema.json" for name in EXPECTED_SCHEMAS}


def _python_inventories(tree: ast.AST) -> list[tuple[int, list[str]]]:
    """Every collection literal in the tree that reads as a schema inventory."""
    found = []
    for node in ast.walk(tree):
        elements = None
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            elements = node.elts
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in {"set", "frozenset", "tuple", "list"} \
                and len(node.args) == 1 \
                and isinstance(node.args[0], (ast.List, ast.Tuple, ast.Set)):
            elements = node.args[0].elts
        if elements is None:
            continue
        literals = [e.value for e in elements
                    if isinstance(e, ast.Constant) and e.value in SCHEMA_NAME_LITERALS]
        if len(literals) >= INVENTORY_THRESHOLD:
            found.append((node.lineno, literals))
    return found


def _json_inventories(payload) -> list[list[str]]:
    found = []
    if isinstance(payload, list):
        literals = [v for v in payload
                    if isinstance(v, str) and v in SCHEMA_NAME_LITERALS]
        if len(literals) >= INVENTORY_THRESHOLD:
            found.append(literals)
        for item in payload:
            found.extend(_json_inventories(item))
    elif isinstance(payload, dict):
        keys = [k for k in payload if k in SCHEMA_NAME_LITERALS]
        if len(keys) >= INVENTORY_THRESHOLD:
            found.append(keys)
        for value in payload.values():
            found.extend(_json_inventories(value))
    return found


def test_no_literal_inventory_of_schema_names_remains_in_tests_or_orchestration():
    """By search, not by inspection: nowhere under tests/ or orchestration/
    does a collection of schema names still exist."""
    offenders = {}
    for directory in ("tests", "orchestration"):
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            if path.name == Path(__file__).name:
                continue    # this module names them on purpose, as the check
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for lineno, literals in _python_inventories(tree):
                offenders[f"{path.relative_to(REPO_ROOT)}:{lineno}"] = literals
    assert offenders == {}


def test_the_two_inventory_constants_are_gone_by_name():
    """The exact statement of the deletion, alongside the fuzzier search
    above: no module anywhere still binds a collection literal to a name
    that reads as a shipped-schema inventory."""
    survivors = {}
    for directory in ("tests", "orchestration"):
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            if path.name == Path(__file__).name:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if not any("SHIPPED" in name for name in targets):
                    continue
                if isinstance(node.value, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
                    survivors[str(path.relative_to(REPO_ROOT))] = targets
    assert survivors == {}


def test_the_manifest_is_the_only_file_declaring_the_full_inventory():
    """Search the whole shipped tree rather than the two files the story
    expected: a third copy anywhere is the thing being ruled out."""
    carriers = []
    for directory in COPIED:
        for path in sorted((REPO_ROOT / directory).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.name == Path(__file__).name:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if path.suffix == ".py":
                if _python_inventories(ast.parse(text)):
                    carriers.append(str(path.relative_to(REPO_ROOT)))
            elif path.suffix == ".json":
                if _json_inventories(json.loads(text)):
                    carriers.append(str(path.relative_to(REPO_ROOT)))
    assert carriers == ["schemas/manifest.json"]


def test_both_inventory_modules_read_the_shared_accessor():
    for relative in INVENTORY_TEST_FILES:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "shipped_schemas()" in text, relative


# --------------------------------------------------------------------------
# The surviving assertions are exact equality, not a subset
# --------------------------------------------------------------------------


def _assert_ops(path: Path, test_name: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == test_name)
    operators = []
    for node in ast.walk(function):
        if isinstance(node, ast.Compare):
            operators.extend(type(op).__name__ for op in node.ops)
    return operators


@pytest.mark.parametrize("node_id", INVENTORY_TESTS)
def test_the_inventory_assertions_are_still_exact_equality(node_id: str):
    """Not relaxed to a subset or a containment check to make the move work."""
    relative, test_name = node_id.split("::")
    operators = _assert_ops(REPO_ROOT / relative, test_name)
    assert operators, node_id
    assert set(operators) == {"Eq"}, (node_id, operators)


# --------------------------------------------------------------------------
# Mutation: the inventory tests actually bite, in both directions
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pristine_harness(tmp_path_factory) -> Path:
    """A copy of the shipped harness the mutation tests each clone."""
    root = tmp_path_factory.mktemp("harness")
    for name in COPIED:
        shutil.copytree(REPO_ROOT / name, root / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    return root


@pytest.fixture
def harness_copy(pristine_harness: Path, tmp_path: Path) -> Path:
    root = tmp_path / "harness"
    shutil.copytree(pristine_harness, root)
    return root


def run_inventory_tests(root: Path, node_ids=INVENTORY_TESTS) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *node_ids],
        cwd=root, capture_output=True, text=True)


def read_manifest(root: Path) -> dict:
    return json.loads((root / "schemas" / "manifest.json").read_text(encoding="utf-8"))


def write_manifest(root: Path, names) -> None:
    (root / "schemas" / "manifest.json").write_text(
        json.dumps({schema_validator.MANIFEST_KEY: sorted(names)}, indent=2) + "\n",
        encoding="utf-8")


def test_the_unmutated_copy_passes_the_inventory_tests(harness_copy: Path):
    """The control. Without it, every failure below could be an artifact of
    the copy rather than of the mutation."""
    result = run_inventory_tests(harness_copy)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_schema_file_with_no_manifest_entry_fails_a_test(harness_copy: Path):
    (harness_copy / "schemas" / "throwaway.schema.json").write_text(
        json.dumps(THROWAWAY_SCHEMA, indent=2) + "\n", encoding="utf-8")
    result = run_inventory_tests(harness_copy)
    assert result.returncode != 0, result.stdout


def test_a_manifest_entry_with_no_schema_file_fails_a_test(harness_copy: Path):
    write_manifest(harness_copy,
                   read_manifest(harness_copy)[schema_validator.MANIFEST_KEY]
                   + ["throwaway"])
    result = run_inventory_tests(harness_copy)
    assert result.returncode != 0, result.stdout


def test_removing_a_schema_file_but_not_its_manifest_entry_fails_a_test(
        harness_copy: Path):
    (harness_copy / "schemas" / "retry-guidance.schema.json").unlink()
    result = run_inventory_tests(harness_copy)
    assert result.returncode != 0, result.stdout


def test_removing_a_manifest_entry_but_not_its_schema_file_fails_a_test(
        harness_copy: Path):
    names = [n for n in read_manifest(harness_copy)[schema_validator.MANIFEST_KEY]
             if n != "retry-guidance"]
    write_manifest(harness_copy, names)
    result = run_inventory_tests(harness_copy)
    assert result.returncode != 0, result.stdout


@pytest.mark.parametrize("stray", ["README.md", "notes.json", "draft.schema.yaml"])
def test_a_stray_non_schema_file_in_schemas_still_fails_a_test(
        harness_copy: Path, stray: str):
    """Narrowing the glob to *.schema.json must not open the hole glob("*")
    closed."""
    (harness_copy / "schemas" / stray).write_text("stray\n", encoding="utf-8")
    result = run_inventory_tests(harness_copy)
    assert result.returncode != 0, result.stdout


def test_a_bogus_manifest_entry_fails_the_per_schema_checks_too(harness_copy: Path):
    """A name in the manifest whose file is not a valid schema must fail
    loudly rather than silently widening the inventory."""
    (harness_copy / "schemas" / "throwaway.schema.json").write_text(
        json.dumps({"type": "object", "additionalProperties": False}) + "\n",
        encoding="utf-8")
    write_manifest(harness_copy,
                   read_manifest(harness_copy)[schema_validator.MANIFEST_KEY]
                   + ["throwaway"])
    result = run_inventory_tests(
        harness_copy,
        node_ids=(*INVENTORY_TESTS,
                  "tests/test_story_004_validation.py"
                  "::test_no_schema_constrains_a_field_the_validator_cannot_check"))
    assert result.returncode != 0, result.stdout


# --------------------------------------------------------------------------
# The central property, demonstrated rather than argued
# --------------------------------------------------------------------------


def test_adding_a_schema_requires_edits_only_under_schemas(harness_copy: Path):
    """Add a throwaway schema and its manifest line, touch nothing else, and
    show every inventory check passes - including the per-schema parametrized
    ones, which now expand over it. Then show tests/ is byte-identical to the
    shipped tree, so no path under tests/ was among the edits."""
    (harness_copy / "schemas" / "throwaway.schema.json").write_text(
        json.dumps(THROWAWAY_SCHEMA, indent=2) + "\n", encoding="utf-8")
    write_manifest(harness_copy,
                   read_manifest(harness_copy)[schema_validator.MANIFEST_KEY]
                   + ["throwaway"])

    result = run_inventory_tests(harness_copy, node_ids=INVENTORY_TEST_FILES)
    assert result.returncode == 0, result.stdout + result.stderr
    # The per-schema cases parametrize over the manifest, so the new schema
    # was actually checked rather than merely tolerated.
    parametrized = run_inventory_tests(
        harness_copy,
        node_ids=("tests/test_schema_validator.py", "tests/test_story_004_validation.py",
                  "-k", "throwaway"))
    assert parametrized.returncode == 0, parametrized.stdout
    assert "no tests ran" not in parametrized.stdout, parametrized.stdout

    differences = subprocess.run(
        ["diff", "-r", "-x", "__pycache__", str(REPO_ROOT / "tests"),
         str(harness_copy / "tests")],
        capture_output=True, text=True)
    assert differences.stdout == "", differences.stdout
    assert differences.returncode == 0


# --------------------------------------------------------------------------
# Nothing else moved
# --------------------------------------------------------------------------


def test_the_injected_schema_placeholder_set_is_unchanged():
    """schema_context globs *.schema.json, so the manifest becomes no
    placeholder."""
    context = context_assembler.schema_context(REPO_ROOT)
    assert set(context) == {name.replace("-", "_") + "_schema"
                            for name in EXPECTED_SCHEMAS}
    assert "manifest_schema" not in context
    assert "manifest" not in " ".join(context)


def test_no_prompt_template_references_a_manifest_placeholder():
    for path in sorted((REPO_ROOT / "prompts").glob("*.md")):
        assert "{{manifest" not in path.read_text(encoding="utf-8"), path.name


def test_nothing_in_orchestration_routes_on_the_manifest():
    """shipped_schemas has no caller inside orchestration/, and no module
    other than schema_validator names the manifest at all."""
    for path in sorted((REPO_ROOT / "orchestration").glob("*.py")):
        if path.name == "schema_validator.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "manifest" not in text, path.name
        assert "shipped_schemas" not in text, path.name


def test_the_coordinator_is_untouched_by_this_story():
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "HEAD", "--",
         "orchestration/story_coordinator.py", "workflows/", "prompts/",
         "rules/", "scripts/"],
        capture_output=True, text=True, check=True)
    assert result.stdout.strip() == ""


def test_no_shipped_schema_was_edited_by_this_story():
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "HEAD", "--", "schemas/"],
        capture_output=True, text=True, check=True)
    assert result.stdout.strip() == ""
    assert schema_validator.SUPPORTED_KEYWORDS == frozenset(
        {"type", "required", "properties", "items", "enum"})


def test_the_implementer_added_no_test_file_and_no_test_function():
    """Its edits under tests/ are the two deletions and their repointing."""
    status = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--", "tests/"],
        capture_output=True, text=True, check=True).stdout.splitlines()
    added = sorted(line[3:] for line in status if line[:2] in {"??", "A "})
    assert added == [f"tests/{Path(__file__).name}"], added

    diff = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "HEAD", "--", *INVENTORY_TEST_FILES],
        capture_output=True, text=True, check=True).stdout
    added_lines = [line[1:] for line in diff.splitlines()
                   if line.startswith("+") and not line.startswith("+++")]
    assert not [line for line in added_lines if line.startswith("def test")], added_lines
