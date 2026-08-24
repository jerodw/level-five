"""story-013 validation: the schema inventory moved out of `tests/`.

The story's deliverable is a relocation, not a behavior. So almost nothing
here reads the new code and agrees with it. Instead:

* The single-inventory claim is established by **search** — an AST scan for
  literal collections of schema names across `tests/` and `orchestration/` —
  and the scan is controlled by feeding it the pre-story text of
  `tests/test_schema_validator.py`, recovered from git, which really did hold
  one. A scan that finds nothing is worth nothing until it has found
  something.

* The inventory checks are exercised by **mutation**. A throwaway harness
  root is built from real copies of `orchestration/`, `schemas/` and the two
  inventory test files, and the checks are run there by a real pytest. A
  pristine copy must go green; each of the four violations the story names —
  a schema file with no manifest entry, a manifest entry with no schema file,
  a stray non-schema file in `schemas/`, a malformed manifest — must turn it
  red. Every "still fails" claim below is a run, not a reading.

* The story's central property is **demonstrated**: a throwaway schema is
  added to that copy along with its manifest line, every inventory and
  per-schema check passes, and every file under `tests/` in the copy is
  asserted byte-identical to the one in this repository. Adding a schema
  touched only `schemas/`.

Every absence asserted here carries a control that constructs the violation
and shows the same check reporting it. Baselines come from
`tests/conftest.py`; none is resolved as HEAD against this repository.
"""
import ast
import inspect
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
import context_assembler
import schema_validator
from conftest import NothingToCompareAgainst
from test_shared_baseline_resolution import committed_story

REPO_ROOT = Path(schema_validator.__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"
SCHEMAS_DIR = REPO_ROOT / "schemas"
MANIFEST = SCHEMAS_DIR / "manifest.json"

SHIPPED = schema_validator.shipped_schemas()

#: The two assertions that hold the directory to the manifest. Named as node
#: ids because they are *run*, in a mutated copy of this repository, rather
#: than read.
INVENTORY_NODES = (
    "tests/test_schema_validator.py::test_shipped_schemas_are_exactly_the_named_ones",
    "tests/test_artifact_schemas.py::test_schemas_directory_holds_exactly_the_named_schemas",
)

#: The per-schema checks parametrized over the manifest. A manifest entry with
#: no file has to fail one of these loudly rather than quietly shrink the
#: parametrization.
PARAMETRIZED_NODES = (
    "tests/test_schema_validator.py::test_shipped_schema_is_valid_json_draft_2020_12_and_supported",
    "tests/test_schema_validator.py::test_no_shipped_schema_forbids_additional_properties",
)

WALK_NODES = (
    "tests/test_artifact_schemas.py::test_every_required_field_is_also_a_declared_property",
    "tests/test_artifact_schemas.py::test_no_schema_constrains_a_field_the_validator_cannot_check",
)

ALL_NODES = INVENTORY_NODES + PARAMETRIZED_NODES + WALK_NODES

#: The test files copied into the throwaway harness. Copying only these keeps
#: collection there to the inventory checks and their dependencies.
COPIED_TESTS = ("conftest.py", "test_schema_validator.py",
                "test_artifact_schemas.py")

#: The files the implementer touched under `tests/`, and the one function in
#: each whose body the story is allowed to have changed.
IMPLEMENTER_TEST_EDITS = {
    "tests/test_schema_validator.py":
        {"test_shipped_schemas_are_exactly_the_named_ones"},
    "tests/test_story_004_validation.py":
        {"test_schemas_directory_holds_exactly_the_named_schemas"},
    "tests/test_story_014_validation.py":
        {"test_the_new_schema_ships_and_both_inventories_still_assert_equality"},
}

#: What the tester stage adds under `tests/`: this file, and the repair to
#: story-015's archived-copy assertion that this file's own existence forced.
TESTER_TEST_EDITS = ("tests/test_story_013_validation.py",
                     "tests/test_story_015_validation.py")

THROWAWAY = "throwaway-artifact"
THROWAWAY_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": THROWAWAY,
    "description": "A schema that exists only inside one test's temp directory.",
    "type": "object",
    "required": ["status"],
    "properties": {"status": {"type": "string"}},
}


# --------------------------------------------------------------------------
# This story's own two ends, carried rather than resolved
#
# Every comparison below is between two frozen past states of this repository:
# what `schemas/` and `tests/` held before this story's run commit, and what
# they held at it. Both were resolved out of this repository's commit graph
# until story-053, and that made every one of them depend on facts about the
# graph rather than about what the story did. A rename gives a path a new
# add-commit and empties the range silently; a squash makes the range
# unresolvable in a clone; CI carried `fetch-depth: 0` for no other reason.
# None of that is a property of which files story-013 touched.
#
# So the two ends are committed under `tests/history-fixtures/`, lifted from
# exactly the bounds this used to resolve:
#
#   * the text of each file an assertion below actually reads, at the bound it
#     reads it — a fixture holds what an assertion reads and no more;
#   * and one tree fixture, `story-013-tree.json`, giving the name and content
#     digest of every file in `schemas/` and `tests/` at each end. The
#     whole-tree comparisons are *recomputed* from it rather than restated as a
#     list, so a file that differed at the two ends is still found by comparing
#     two states rather than by being named here.
# --------------------------------------------------------------------------


BASELINE_BOUND = "baseline"
ENDPOINT_BOUND = "endpoint"


def _tree() -> dict:
    """Both ends of `schemas/` and `tests/`, as name-to-digest maps."""
    return json.loads(conftest.history_fixture("story-013-tree.json"))


def _listing_at(bound: str, directory: str) -> set[str]:
    """The file names one directory held at one end of this story's range."""
    return set(_tree()[bound][directory])


def _digests_at(bound: str, directory: str) -> dict[str, str]:
    return dict(_tree()[bound][directory])


def _text_at(bound: str, rel: str) -> str:
    """One file's text at one end of this story's range.

    Raises `NothingToCompareAgainst` for a path this story's range does not
    carry a fixture for, which is what the reader raised before and is what the
    accounting sweep below relies on to report a file that appeared.
    """
    name = f"{Path(rel).name}.at-story-013-{bound}.py.txt"
    if not (conftest.HISTORY_FIXTURES / name).is_file():
        raise NothingToCompareAgainst(
            f"{rel} is not carried at this story's {bound}")
    return conftest.history_fixture(name)


def _schema_stems(names: set[str]) -> set[str]:
    suffix = ".schema.json"
    return {name[: -len(suffix)] for name in names if name.endswith(suffix)}


# --------------------------------------------------------------------------
# The throwaway harness root
# --------------------------------------------------------------------------


def harness_copy(tmp_path: Path, name: str = "harness") -> Path:
    """A real, runnable copy of the parts of this harness the inventory needs.

    `orchestration/` is copied rather than symlinked on purpose:
    `schema_validator.HARNESS_ROOT` is `Path(__file__).resolve().parents[1]`,
    and `resolve()` follows a symlink straight back to this repository, which
    would make every mutation below invisible.
    """
    root = tmp_path / name
    ignore = shutil.ignore_patterns("__pycache__")
    shutil.copytree(REPO_ROOT / "orchestration", root / "orchestration",
                    ignore=ignore)
    shutil.copytree(SCHEMAS_DIR, root / "schemas", ignore=ignore)
    (root / "tests").mkdir()
    for name_ in COPIED_TESTS:
        shutil.copy2(TESTS_DIR / name_, root / "tests" / name_)
    return root


def run_checks(root: Path, *nodes: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         *(nodes or ALL_NODES)],
        cwd=root, capture_output=True, text=True,
    )


def manifest_of(root: Path) -> dict:
    return json.loads((root / "schemas" / "manifest.json").read_text(encoding="utf-8"))


def write_manifest(root: Path, names) -> None:
    (root / "schemas" / "manifest.json").write_text(
        json.dumps({"schemas": sorted(names)}, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# The control every mutation below depends on
# --------------------------------------------------------------------------


def test_the_pristine_copy_runs_the_real_checks_green(tmp_path):
    """The positive control for this whole file.

    Each mutation test asserts a copy goes red. That means nothing unless an
    unmutated copy goes green for the right reason — so this also pins the
    parametrized case count to the manifest's length, which is what makes
    "the per-schema checks still run over every shipped schema" a fact rather
    than a hope.
    """
    root = harness_copy(tmp_path)
    result = run_checks(root)
    assert result.returncode == 0, result.stdout + result.stderr

    expanded = run_checks(root, *PARAMETRIZED_NODES)
    assert expanded.returncode == 0, expanded.stdout + expanded.stderr
    assert f"{2 * len(SHIPPED)} passed" in expanded.stdout, expanded.stdout


# --------------------------------------------------------------------------
# One declared inventory, established by search
# --------------------------------------------------------------------------


#: A literal collection of two or more shipped schema names. Two, not three,
#: so no threshold is quietly hiding a small inventory; the two collections
#: that legitimately match are declared below by name rather than excused by
#: the cutoff.
MIN_NAMES = 2

def _per_prompt_values(tree: ast.Module) -> set[int]:
    """Collections that are a dict value under a prompt-filename key.

    The one structure in the suite that legitimately lists schema names:
    `{"story-tester.md": ["test-results", "changed-files"]}` in
    `tests/test_context_assembler.py` says which schemas *that prompt*
    injects. It is not an inventory — adding a seventh schema would not
    change the line, because it never claimed to enumerate what the harness
    ships. Recognised by shape rather than excused by name or line number, so
    the exemption cannot be borrowed by a real inventory.
    """
    exempt = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (isinstance(key, ast.Constant) and isinstance(key.value, str)
                    and key.value.endswith(".md")):
                exempt.add(id(value))
    return exempt


def literal_inventories(source: str, module: str) -> set[tuple[str, tuple[str, ...]]]:
    """Every literal list/tuple/set holding `MIN_NAMES` or more schema names."""
    tree = ast.parse(source)
    exempt = _per_prompt_values(tree)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            continue
        if id(node) in exempt:
            continue
        names = tuple(sorted(
            element.value for element in node.elts
            if isinstance(element, ast.Constant)
            and isinstance(element.value, str)
            and element.value in SHIPPED
        ))
        if len(names) >= MIN_NAMES:
            found.add((module, names))
    return found


def _scanned() -> set[tuple[str, tuple[str, ...]]]:
    found = set()
    for directory in ("tests", "orchestration"):
        for path in sorted((REPO_ROOT / directory).glob("*.py")):
            found |= literal_inventories(path.read_text(encoding="utf-8"), path.name)
    return found


def test_no_literal_inventory_of_schema_names_survives_under_tests_or_orchestration():
    """The story's headline claim, by search rather than by inspection."""
    assert _scanned() == set()


def test_the_only_exempted_shape_is_the_per_prompt_injection_map():
    """The exemption is real, narrow, and cannot be borrowed.

    `tests/test_context_assembler.py` holds the only collections the scan
    passes over. Lifted out of their dict, the identical lists are flagged —
    so what exempts them is the structure that makes them per-prompt
    expectations, not the fact that they live in that file.
    """
    source = (TESTS_DIR / "test_context_assembler.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    exempted = [ast.get_source_segment(source, node) for node in ast.walk(tree)
                if isinstance(node, (ast.List, ast.Tuple, ast.Set))
                and id(node) in _per_prompt_values(tree)
                and sum(isinstance(e, ast.Constant) and e.value in SHIPPED
                        for e in node.elts) >= MIN_NAMES]
    assert len(exempted) == 2, exempted

    for segment in exempted:
        assert literal_inventories(f"NAMES = {segment}\n", "probe.py"), segment
        assert literal_inventories(f'D = {{"x.md": {segment}}}\n', "probe.py") == set()


def test_the_search_finds_the_inventory_it_is_looking_for(tmp_path):
    """The control. Two subjects, one synthetic and one real.

    The real one matters more: it is the pre-story text of
    `tests/test_schema_validator.py`, recovered from git, which held the
    `SHIPPED` tuple this story deleted. The same scan that reports nothing
    today reports it — so today's silence is an absence, not a blind spot.
    """
    synthetic = 'INVENTORY = [%s]\n' % ", ".join(repr(name) for name in SHIPPED)
    assert literal_inventories(synthetic, "probe.py") == {
        ("probe.py", tuple(sorted(SHIPPED)))}

    before = _text_at(BASELINE_BOUND, "tests/test_schema_validator.py")
    hits = literal_inventories(before, "test_schema_validator.py")
    assert hits, "the pre-story file was expected to hold a literal inventory"
    # The inventory it held was the one that revision shipped, resolved from
    # the same revision rather than from today's `SHIPPED` — a later story
    # that adds a schema does not make this file's old copy wrong.
    shipped_then = _schema_stems(_listing_at(BASELINE_BOUND, "schemas"))
    assert shipped_then
    assert any(set(names) == shipped_then for _, names in hits), hits

    # And the file as it stands now holds none.
    after = (TESTS_DIR / "test_schema_validator.py").read_text(encoding="utf-8")
    assert literal_inventories(after, "test_schema_validator.py") == set()

    before_004 = _text_at(BASELINE_BOUND, "tests/test_story_004_validation.py")
    assert literal_inventories(before_004, "test_story_004_validation.py")
    after_004 = (TESTS_DIR / "test_artifact_schemas.py").read_text(encoding="utf-8")
    assert literal_inventories(after_004, "test_story_004_validation.py") == set()


def test_the_manifest_is_the_declaration_and_names_what_is_on_disk():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert list(manifest) == ["schemas"]
    assert manifest["schemas"] == sorted(manifest["schemas"])
    on_disk = sorted(p.name[: -len(".schema.json")]
                     for p in SCHEMAS_DIR.glob("*.schema.json"))
    assert manifest["schemas"] == on_disk
    assert schema_validator.shipped_schemas() == tuple(manifest["schemas"])


# --------------------------------------------------------------------------
# shipped_schemas(): signature, override, and refusal to degrade
# --------------------------------------------------------------------------


def test_shipped_schemas_carries_type_hints_and_the_same_override_as_its_neighbours():
    signature = inspect.signature(schema_validator.shipped_schemas)
    parameter = signature.parameters["harness_root"]
    assert parameter.default is None
    assert parameter.annotation is not inspect.Parameter.empty
    assert signature.return_annotation is not inspect.Parameter.empty
    assert "tuple" in str(signature.return_annotation)

    # Identical in shape to the two functions it sits beside.
    for neighbour in (schema_validator.schemas_dir, schema_validator.load_schema):
        other = inspect.signature(neighbour).parameters["harness_root"]
        assert (other.default, str(other.annotation)) == (
            parameter.default, str(parameter.annotation))


def test_the_harness_root_override_really_redirects(tmp_path):
    """Positive and negative in one: a foreign root yields foreign names, and
    the real root is unmoved by the call."""
    (tmp_path / "schemas").mkdir()
    write_manifest(tmp_path, ["alpha", "beta"])
    assert schema_validator.shipped_schemas(tmp_path) == ("alpha", "beta")
    assert schema_validator.shipped_schemas() == SHIPPED


MALFORMED = {
    "missing-file": None,
    "not-json": "{ this is not json",
    "top-level-array": '["alpha", "beta"]\n',
    "top-level-string": '"alpha"\n',
    "no-schemas-key": '{"names": ["alpha"]}\n',
    "schemas-not-a-list": '{"schemas": {"alpha": true}}\n',
    "schemas-empty": '{"schemas": []}\n',
    "entry-not-a-string": '{"schemas": ["alpha", 7]}\n',
    "entry-empty-string": '{"schemas": ["alpha", ""]}\n',
}


@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_a_missing_or_malformed_manifest_raises_rather_than_degrading(case, tmp_path):
    """The failure that matters is not the exception; it is the alternative.

    An empty or partial return would leave every parametrized per-schema check
    with nothing to iterate and every equality assertion comparing two empty
    collections — green, and meaningless.
    """
    root = tmp_path / case
    (root / "schemas").mkdir(parents=True)
    payload = MALFORMED[case]
    if payload is not None:
        (root / "schemas" / "manifest.json").write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError):
        schema_validator.shipped_schemas(root)

    # The control: the same root with a well-formed manifest returns names, so
    # the raise above is about the payload and not about the directory.
    write_manifest(root, ["alpha"])
    assert schema_validator.shipped_schemas(root) == ("alpha",)


def test_a_malformed_manifest_turns_the_parametrized_checks_red_not_vacuous(tmp_path):
    """Run for real: the per-schema checks must not silently shrink to zero."""
    root = harness_copy(tmp_path)
    (root / "schemas" / "manifest.json").write_text("{ broken", encoding="utf-8")
    result = run_checks(root, *PARAMETRIZED_NODES)
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "ValueError" in output, output
    assert "0 passed" not in output or "error" in output.lower(), output


# --------------------------------------------------------------------------
# Both failure directions, and the hole the narrowed glob could have opened
# --------------------------------------------------------------------------


def test_a_schema_file_with_no_manifest_entry_fails(tmp_path):
    root = harness_copy(tmp_path)
    (root / "schemas" / "undeclared.schema.json").write_text(
        json.dumps(THROWAWAY_SCHEMA) + "\n", encoding="utf-8")
    result = run_checks(root, *INVENTORY_NODES)
    assert result.returncode != 0, result.stdout
    assert "undeclared" in result.stdout, result.stdout


def test_a_manifest_entry_with_no_schema_file_fails(tmp_path):
    root = harness_copy(tmp_path)
    write_manifest(root, list(manifest_of(root)["schemas"]) + ["phantom"])
    result = run_checks(root, *INVENTORY_NODES)
    assert result.returncode != 0, result.stdout
    assert "phantom" in result.stdout, result.stdout

    # And the per-schema checks are where it fails loudest: a name with no
    # file cannot be loaded, so the parametrization cannot widen quietly.
    parametrized = run_checks(root, *PARAMETRIZED_NODES)
    assert parametrized.returncode != 0, parametrized.stdout


def test_a_stray_non_schema_file_in_schemas_still_fails(tmp_path):
    """The narrowed `*.schema.json` glob would walk straight past this file.
    The companion `iterdir()` assertion is what still catches it."""
    root = harness_copy(tmp_path)
    (root / "schemas" / "notes.txt").write_text("a stray file\n", encoding="utf-8")
    result = run_checks(root, *INVENTORY_NODES)
    assert result.returncode != 0, result.stdout
    assert "notes.txt" in result.stdout, result.stdout


def test_both_inventory_tests_compare_by_equality_and_neither_was_relaxed():
    """The two directions above are the real proof — a subset check cannot
    fail in both. This adds the static half: no containment operator sits in
    either function, and each ends in an `==`."""
    for rel, function in (("tests/test_schema_validator.py",
                           "test_shipped_schemas_are_exactly_the_named_ones"),
                          ("tests/test_artifact_schemas.py",
                           "test_schemas_directory_holds_exactly_the_named_schemas")):
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        node = next(n for n in ast.parse(source).body
                    if isinstance(n, ast.FunctionDef) and n.name == function)
        segment = ast.get_source_segment(source, node)
        for banned in ("issubset", "issuperset", "<=", ">=", "in present", "in names"):
            assert banned not in segment, (rel, banned)
        comparisons = [n for n in ast.walk(node)
                       if isinstance(n, ast.Compare) and len(n.ops) == 1]
        assert comparisons, rel
        assert all(isinstance(c.ops[0], ast.Eq) for c in comparisons), rel


# --------------------------------------------------------------------------
# The central property, demonstrated
# --------------------------------------------------------------------------


def test_adding_a_schema_requires_editing_only_schemas_and_nothing_under_tests(tmp_path):
    """The story's whole reason for existing, run rather than argued.

    A throwaway schema and its manifest line are the only edits. Every
    inventory and per-schema check passes, the parametrization grows by one,
    and every file under `tests/` in the copy is still byte-identical to the
    one in this repository — so no path under `tests/` was on the critical
    path for adding a schema.
    """
    root = harness_copy(tmp_path)
    (root / "schemas" / f"{THROWAWAY}.schema.json").write_text(
        json.dumps(THROWAWAY_SCHEMA, indent=2) + "\n", encoding="utf-8")
    write_manifest(root, list(manifest_of(root)["schemas"]) + [THROWAWAY])

    result = run_checks(root)
    assert result.returncode == 0, result.stdout + result.stderr

    expanded = run_checks(root, *PARAMETRIZED_NODES)
    assert f"{2 * (len(SHIPPED) + 1)} passed" in expanded.stdout, expanded.stdout

    for name in COPIED_TESTS:
        assert (root / "tests" / name).read_bytes() == \
            (TESTS_DIR / name).read_bytes(), name


def test_the_demonstration_above_would_have_noticed_a_tests_edit(tmp_path):
    """Its control. The byte-comparison is an absence assertion — it says no
    test file differs — so here is the same comparison over a copy where one
    does, reporting it."""
    root = harness_copy(tmp_path, name="edited")
    edited = root / "tests" / "test_schema_validator.py"
    edited.write_text(edited.read_text(encoding="utf-8") + "\n# an edit\n",
                      encoding="utf-8")
    differing = [name for name in COPIED_TESTS
                 if (root / "tests" / name).read_bytes()
                 != (TESTS_DIR / name).read_bytes()]
    assert differing == ["test_schema_validator.py"]


# --------------------------------------------------------------------------
# Nothing else moved
# --------------------------------------------------------------------------


UNTOUCHED = ("orchestration/story_coordinator.py",
             "orchestration/context_assembler.py",
             "orchestration/story_parser.py",
             "orchestration/harness_config.py",
             "orchestration/agent_runner.py",
             "orchestration/run_status.py",
             "workflows/", "prompts/", "scripts/", "rules/",
             ".harness/stories/", ".harness/requests/")


@pytest.mark.parametrize("rel", UNTOUCHED)
def test_this_story_changed_nothing_outside_its_scope(rel, tmp_path):
    """Restated over a story this test builds, with the control beside it.

    Asked of this repository's own commit graph the assertion re-stated a
    frozen past fact and drew its evidence from a history that moves under it.
    The claim, the paths and the predicate are unchanged.
    """
    respecting = conftest.constructed_story(tmp_path, respected=[rel],
                                            name="in-scope")
    assert conftest.constructed_story_diff(respecting, [rel]).strip() == ""


def test_the_scope_assertion_above_can_fail(tmp_path):
    """Its control: over a synthetic history where the story does touch
    `orchestration/`, the identical call reports it."""
    rel = "tests/test_story_013_validation.py"
    root = committed_story(tmp_path, rel, "orchestration/", violate="modify")
    assert conftest.story_diff(["orchestration/"], validation_file=root / rel,
                               repo=root).strip() != ""


def test_no_schema_was_added_removed_or_edited_and_only_the_manifest_appeared():
    """Resolved by listing rather than by diff, so it holds both before and
    after the coordinator commits: a diff cannot see an untracked addition."""
    before = _listing_at(BASELINE_BOUND, "schemas")
    after = _listing_at(ENDPOINT_BOUND, "schemas")
    assert {name for name in before if name.endswith(".schema.json")} == \
        {name for name in after if name.endswith(".schema.json")}
    assert after - before == {"manifest.json"}
    assert before - after == set()

    # No schema *body* changed either, which the digests carried beside the
    # names already say: every schema present at both ends hashes the same.
    before_digests = _digests_at(BASELINE_BOUND, "schemas")
    after_digests = _digests_at(ENDPOINT_BOUND, "schemas")
    changed = sorted(name for name in before_digests
                     if after_digests.get(name) != before_digests[name])
    assert changed == [], changed
    # And the digests can differ: the endpoint carries a name the baseline did
    # not, so this is a comparison of two states rather than one read twice.
    assert set(after_digests) - set(before_digests) == {"manifest.json"}


def test_the_supported_keyword_subset_is_unchanged():
    before = conftest.history_fixture(
        "schema_validator.at-story-013-baseline.py.txt")
    assert _constant(before, "SUPPORTED_KEYWORDS") == sorted(
        schema_validator.SUPPORTED_KEYWORDS)
    assert _constant(before, "ANNOTATION_KEYWORDS") == sorted(
        schema_validator.ANNOTATION_KEYWORDS)


def _constant(source: str, name: str) -> list[str]:
    """The sorted string members of a module-level frozenset literal."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return sorted(
                element.value
                for element in ast.walk(node)
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
    raise AssertionError(f"{name} not found")


def test_nothing_in_orchestration_routes_on_the_manifest():
    """Only the accessor knows the manifest exists; no other module reads it,
    and nothing branches on it."""
    readers = []
    for path in sorted((REPO_ROOT / "orchestration").glob("*.py")):
        if "manifest" in path.read_text(encoding="utf-8").lower():
            readers.append(path.name)
    assert readers == ["schema_validator.py"]

    # The control: the search is looking in the right place and can see the
    # string it is asserting the absence of.
    assert "manifest" in (
        REPO_ROOT / "orchestration" / "schema_validator.py"
    ).read_text(encoding="utf-8")

    # And the accessor is called from tests only — no coordinator code path
    # reaches it.
    callers = [path.name for path in sorted((REPO_ROOT / "orchestration").glob("*.py"))
               if "shipped_schemas(" in path.read_text(encoding="utf-8")
               and path.name != "schema_validator.py"]
    assert callers == []


# --------------------------------------------------------------------------
# The injected placeholder set
# --------------------------------------------------------------------------


def test_the_injected_placeholder_set_is_exactly_what_it_was():
    context = context_assembler.schema_context(REPO_ROOT)
    expected = {f"{name.replace('-', '_')}_schema" for name in SHIPPED}
    assert set(context) == expected
    assert "manifest_schema" not in context

    # The set is unchanged *by this story*: same schema files at both ends of
    # its commit range, so same placeholders. Resolved from history rather
    # than restated here, and bounded at the story's own endpoint so a later
    # story that ships a schema is not reported as this story's doing.
    def placeholders(names: set[str]) -> set[str]:
        return {stem.replace("-", "_") + "_schema" for stem in _schema_stems(names)}

    before = placeholders(_listing_at(BASELINE_BOUND, "schemas"))
    assert before
    assert placeholders(_listing_at(ENDPOINT_BOUND, "schemas")) == before


def test_the_manifest_is_no_placeholder_because_of_its_name_not_by_luck(tmp_path):
    """The control for the absence above.

    `schema_context` is not blind to the file — rename it so the glob matches
    and the placeholder appears. Its absence today is the `*.schema.json`
    glob doing its job, which is the only thing keeping `{{manifest_schema}}`
    out of every rendered prompt.
    """
    root = tmp_path / "renamed"
    shutil.copytree(SCHEMAS_DIR, root / "schemas")
    (root / "schemas" / "manifest.json").rename(
        root / "schemas" / "manifest.schema.json")
    assert "manifest_schema" in context_assembler.schema_context(root)


def test_no_prompt_template_asks_for_a_manifest_placeholder():
    templates = sorted((REPO_ROOT / "prompts").glob("*.md"))
    assert templates
    for path in templates:
        assert "{{manifest_schema}}" not in path.read_text(encoding="utf-8"), path.name

    # The control: the placeholders that *are* injected do appear, so the scan
    # reads the files the renderer reads.
    implementer = (REPO_ROOT / "prompts" / "story-implementer.md").read_text(encoding="utf-8")
    assert "{{changed_files_schema}}" in implementer


# --------------------------------------------------------------------------
# What the implementer was allowed to do under tests/
# --------------------------------------------------------------------------


def _test_names(source: str) -> set[str]:
    return {node.name for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


def _functions(source: str) -> dict[str, str]:
    tree = ast.parse(source)
    return {node.name: ast.get_source_segment(source, node)
            for node in tree.body if isinstance(node, ast.FunctionDef)}


def test_the_implementer_created_no_file_under_tests():
    """The only file this story adds under `tests/` is this one, written by
    the tester stage. Listing, not diff, so an untracked addition is seen.

    The "after" side is this story's own endpoint rather than today's working
    tree: a later story that legitimately adds its own validation file under
    `tests/` moves the working tree and not this story's commit, and this
    assertion is about what *this* story added.
    """
    before = _python_names(_listing_at(BASELINE_BOUND, "tests"))
    after = _python_names(_listing_at(ENDPOINT_BOUND, "tests"))
    assert before, "the pre-story revision was expected to hold test modules"
    assert after - before == {"test_story_013_validation.py"}
    assert before - after == set()


def _python_names(names: set[str]) -> set[str]:
    return {name for name in names if name.endswith(".py")}


@pytest.mark.parametrize("rel", sorted(IMPLEMENTER_TEST_EDITS))
def test_the_implementer_added_no_test_function(rel):
    """The "after" side is this story's own endpoint, for the reason its
    sibling below already gives: read against today's working tree it counts
    every test any *later* story added to these files, which is not what its
    name asks. story-038 is the story that made it concrete — it merged
    story-033's validation into `tests/test_story_014_validation.py`'s
    successor, so today's file carries tests story-013 has nothing to say
    about. Subject, strictness and the paths compared are unchanged; only the
    upper bound moved, onto the same `_endpoint_text` the sibling uses.
    """
    before = _text_at(BASELINE_BOUND, rel)
    after = _text_at(ENDPOINT_BOUND, rel)
    assert _test_names(before) == _test_names(after), rel
    assert "@pytest.mark.skip" not in after, rel
    assert "pytest.skip(" not in after, rel


def test_the_added_no_test_function_check_can_fail():
    """Its control: the same comparison over a source with one more test."""
    before = "def test_a():\n    assert True\n"
    after = before + "\n\ndef test_b():\n    assert True\n"
    assert _test_names(before) != _test_names(after)
    assert _test_names(after) - _test_names(before) == {"test_b"}


@pytest.mark.parametrize("rel", sorted(IMPLEMENTER_TEST_EDITS))
def test_the_implementer_changed_only_the_inventory_bound_assertions(rel):
    """The "after" side is this story's own endpoint, for the same reason
    `_endpoint_listing` and `_endpoint_text` exist beside it: read against
    today's working tree it reports every function any later story touches in
    these files, which is not what its name asks. story-021 is the story that
    made that concrete — it edited `configure` in
    `tests/test_story_014_validation.py` because its clean-tree pre-flight
    refuses a run whose target tree the test left dirty. The subject and the
    expected set are unchanged."""
    before = _text_at(BASELINE_BOUND, rel)
    after = _text_at(ENDPOINT_BOUND, rel)
    before_functions, after_functions = _functions(before), _functions(after)
    changed = {name for name in after_functions
               if after_functions[name] != before_functions.get(name)}
    assert changed == IMPLEMENTER_TEST_EDITS[rel], (rel, changed)


def test_every_file_differing_under_tests_is_accounted_for():
    """Three files the implementer edited, two the tester wrote. Anything
    else changing under `tests/` is unexplained and this says so.

    Both ends are this story's own: the pre-story revision against this
    story's endpoint. Read against today's working tree it would instead
    report every file any later story touches, which is not what its name
    asks.
    """
    before_digests = _digests_at(BASELINE_BOUND, "tests")
    after_digests = _digests_at(ENDPOINT_BOUND, "tests")
    names = _python_names(set(after_digests))
    assert names, "the endpoint was expected to hold test modules"
    differing = set()
    for name in sorted(names):
        rel = f"tests/{name}"
        if rel in TESTER_TEST_EDITS:
            continue
        if before_digests.get(name) != after_digests[name]:
            differing.add(rel)
    assert differing == set(IMPLEMENTER_TEST_EDITS)

    # The comparison is over two genuinely different states: the endpoint
    # carries a file the baseline did not, so a digest map read twice would
    # fail here rather than report an empty difference.
    assert set(after_digests) - set(before_digests)


# --------------------------------------------------------------------------
# story-015's archived vacuous copy
# --------------------------------------------------------------------------


ARCHIVED = (REPO_ROOT / ".harness" / "runs-archive" / "story-013-vacuous-tests"
            / "pre-reset-test_story_013_validation.py")


def test_the_archived_vacuous_copy_was_not_restored_as_this_story_validation():
    """story-015 asserted this by the path's non-existence, which this file's
    own arrival necessarily ends. The property it was protecting is about
    content: the reset run's validation must not come back. Asserted here on
    content, which survives the re-run."""
    archived = ARCHIVED.read_text(encoding="utf-8")
    for path in sorted(TESTS_DIR.glob("*.py")):
        assert path.read_text(encoding="utf-8") != archived, path.name

    # The archive is what it is claimed to be: the mechanical check still
    # flags it, so the comparison above is against a genuinely vacuous file.
    import test_baseline_honesty as check
    assert check.flagged_calls(archived, ARCHIVED.name)


def test_the_restoration_check_can_fail(tmp_path):
    """Its control: a directory that does hold the archived copy is reported."""
    (tmp_path / "tests").mkdir()
    shutil.copy2(ARCHIVED, tmp_path / "tests" / "test_story_013_validation.py")
    archived = ARCHIVED.read_text(encoding="utf-8")
    restored = [path.name for path in sorted((tmp_path / "tests").glob("*.py"))
                if path.read_text(encoding="utf-8") == archived]
    assert restored == ["test_story_013_validation.py"]


def test_the_archive_itself_was_not_edited_by_this_story(tmp_path):
    """Restated over a story this test builds, with the control beside it."""
    archive = ".harness/runs-archive/"
    respecting = conftest.constructed_story(tmp_path, respected=[archive],
                                            name="archive-left-alone")
    assert conftest.constructed_story_diff(respecting,
                                           [archive]).strip() == ""
    violating = conftest.constructed_story(tmp_path, violated=[archive],
                                           name="archive-edited")
    assert conftest.constructed_story_diff(violating, [archive]).strip() != ""


def test_the_shared_baseline_resolution_is_what_this_file_uses():
    """No assertion above resolves its own baseline.

    Since story-053 that is a stronger statement than it was: this module
    resolves nothing out of this repository's commit graph at all. Its two
    ends are committed fixtures, read through `conftest.history_fixture`, and
    the one comparison that still needs a story range builds the story it asks
    about and resolves it through `conftest`'s own entry point.

    Asserted of this module's source rather than claimed, through the very
    scans `tests/test_baseline_honesty.py` holds the suite to — with the
    control beside it, so an empty report is this module rather than a scan
    that has stopped looking.
    """
    import test_baseline_honesty as rules

    source = Path(__file__).read_text(encoding="utf-8")
    assert rules.history_reads(source, Path(__file__).name) == []
    assert rules.flagged_calls(source, Path(__file__).name) == []
    assert rules.git_text_reads(source, Path(__file__).name) == []

    # The control: the same scan over the same source with one history read
    # planted in it reports, so the emptiness above is a property of this
    # module rather than of a scan that reads nothing.
    planted = source + (
        "\n\ndef _probe():\n"
        "    return conftest.story_diff(['schemas/'],\n"
        "                               validation_file=Path(__file__))\n")
    assert rules.history_reads(planted, "probe.py")

    # And the shared resolution really is where the entry points live.
    assert conftest.story_diff.__module__ == "conftest"
    assert conftest.story_commit_range.__module__ == "conftest"
