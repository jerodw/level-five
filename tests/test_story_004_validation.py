"""Independent validation for story-004: machine-readable artifact schemas.

Validates the acceptance criteria from the artifacts outward — the shipped
schema files, the workflow declaration, the validator's error strings, and
the coordinator's escalation path — using its own fake runner rather than
the ones in test_story_coordinator.py or test_story_002_validation.py.

Every fixture here is inline. Nothing reads the repository's .harness/runs/,
which is gitignored and absent in CI.
"""
import inspect
import json
import re
from pathlib import Path

import context_assembler
import schema_validator
import story_coordinator
import story_parser
from agent_runner import AgentResult

# story-013: the inventory is declared once, in schemas/manifest.json. The
# assertions below check the declaration against the directory; they no
# longer hold a second copy of it.
SHIPPED_SCHEMAS = set(schema_validator.shipped_schemas())

ORIGINAL_STORY_SECTIONS = (
    "story",
    "tasks",
    "acceptance_criteria",
    "scope",
    "verification_requirements",
    "constraints",
)

PLACEHOLDER = re.compile(r"\{\{[a-z_]+\}\}")


def _write(path: Path, payload) -> None:
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _workflow(harness_root: Path) -> dict:
    text = (harness_root / "workflows" / "story-workflow.json").read_text()
    return {stage["name"]: stage for stage in json.loads(text)["stages"]}


# --------------------------------------------------------------------------
# The workflow definition owns the artifact-to-schema mapping
# --------------------------------------------------------------------------


def test_writing_stages_declare_a_schema_for_every_json_artifact(harness_root):
    stages = _workflow(harness_root)
    for name in ("implementer", "tester", "verifier"):
        declared = stages[name].get("schemas")
        assert declared, f"{name} declares no schemas map"
        for artifact, schema_name in declared.items():
            assert artifact.endswith(".json"), (name, artifact)
            assert schema_name in SHIPPED_SCHEMAS, (name, schema_name)
        json_outputs = [o for o in stages[name].get("outputs", []) if o.endswith(".json")]
        assert set(json_outputs) <= set(declared), name


def test_both_changed_files_records_share_one_schema_definition(harness_root):
    stages = _workflow(harness_root)
    implementer = stages["implementer"]["schemas"]["changed-files.json"]
    tester = stages["tester"]["schemas"]["tester-changed-files.json"]
    assert implementer == tester == "changed-files"


def test_verifier_declares_its_conditional_artifact(harness_root):
    verifier = _workflow(harness_root)["verifier"]
    assert verifier["schemas"]["verification-result.json"] == "verification-result"
    assert verifier["schemas"]["retry-guidance.json"] == "retry-guidance"
    # The conditional artifact is declared for validation but never required.
    assert "retry-guidance.json" not in verifier["outputs"]


def test_declared_schema_names_all_resolve_to_shipped_files(harness_root):
    for stage in _workflow(harness_root).values():
        for schema_name in stage.get("schemas", {}).values():
            path = harness_root / "schemas" / f"{schema_name}.schema.json"
            assert path.is_file(), schema_name
            assert json.loads(path.read_text())["title"] == schema_name


def test_the_validation_step_hard_codes_no_artifact_or_schema_name():
    """The mapping lives in the workflow, so the coordinator names nothing."""
    source = inspect.getsource(story_coordinator._schema_violation)
    # Prose is free to name examples; only executable lines are constrained.
    lines, in_docstring = [], False
    for line in source.splitlines():
        if line.lstrip().startswith('"""'):
            in_docstring = not in_docstring
            continue
        if not in_docstring and not line.lstrip().startswith("#"):
            lines.append(line)
    body = "\n".join(lines)
    assert 'stage.get("schemas"' in body    # the stripping left the real code
    assert ".json" not in body
    for schema_name in SHIPPED_SCHEMAS:
        assert schema_name not in body


# --------------------------------------------------------------------------
# The shipped schemas
# --------------------------------------------------------------------------


def test_schemas_directory_holds_exactly_the_named_schemas(harness_root):
    directory = harness_root / "schemas"
    schemas = {p.name for p in directory.glob("*.schema.json")}
    assert schemas == {f"{name}.schema.json" for name in SHIPPED_SCHEMAS}
    # And nothing else is in there: the narrowed glob must not let a stray
    # file past that the previous iterdir() comparison would have caught.
    present = {p.name for p in directory.iterdir()}
    assert present == schemas | {schema_validator.MANIFEST_NAME}


def test_every_required_field_is_also_a_declared_property(harness_root):
    """A required name absent from properties would be an unenforceable typo."""

    def check(schema: dict, where: str) -> None:
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            assert name in properties, f"{where}: required {name!r} is not a property"
        for name, subschema in properties.items():
            check(subschema, f"{where}.{name}")
        if "items" in schema:
            check(schema["items"], f"{where}[]")

    for name in sorted(SHIPPED_SCHEMAS):
        check(schema_validator.load_schema(name, harness_root), name)


def test_no_schema_constrains_a_field_the_validator_cannot_check(harness_root):
    for name in sorted(SHIPPED_SCHEMAS):
        schema = schema_validator.load_schema(name, harness_root)
        assert schema_validator.unsupported_keywords(schema) == [], name
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_story_schema_types_its_nested_shape(harness_root):
    schema = schema_validator.load_schema("story", harness_root)
    assert tuple(schema["required"]) == ORIGINAL_STORY_SECTIONS
    assert "technical_plan" in schema["properties"]
    assert "technical_plan" not in schema["required"]
    plan = schema["properties"]["technical_plan"]["properties"]
    changes = plan["likely_file_changes"]["items"]
    assert set(changes["required"]) == {"file", "reason", "stage"}
    assert schema["properties"]["scope"]["properties"]["modify"]["items"]["type"] == "string"


# --------------------------------------------------------------------------
# Error strings: path, expectation, and found value
# --------------------------------------------------------------------------


def test_deeply_nested_error_path_names_every_step(harness_root):
    schema = schema_validator.load_schema("story", harness_root)
    instance = {
        "story": {"id": "story-x", "title": "t", "description": "d"},
        "tasks": ["a"],
        "acceptance_criteria": ["b"],
        "scope": {"modify": [], "do_not_modify": []},
        "verification_requirements": ["c"],
        "constraints": ["d"],
        "technical_plan": {
            "likely_file_changes": [
                {"file": "a.py", "stage": "implementer", "reason": "ok"},
                {"file": "b.py", "stage": "implementer"},
            ]
        },
    }
    errors = schema_validator.validate(instance, schema)
    assert len(errors) == 1
    assert "$.technical_plan.likely_file_changes[1].reason" in errors[0]


def test_an_error_names_the_expectation_and_the_found_value(harness_root):
    schema = schema_validator.load_schema("verification-result", harness_root)
    errors = schema_validator.validate(
        {"status": "inconclusive", "retry_recommended": "yes"}, schema
    )
    joined = "\n".join(errors)
    assert "$.status" in joined and '"passed"' in joined and "inconclusive" in joined
    assert "$.retry_recommended" in joined and "expected type boolean" in joined


def test_every_violation_is_reported_not_only_the_first(harness_root):
    schema = schema_validator.load_schema("changed-files", harness_root)
    errors = schema_validator.validate({}, schema)
    assert len(errors) == 3
    for field in ("$.modified", "$.created", "$.deleted"):
        assert any(field in error for error in errors)


def test_numeric_types_are_distinguished():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    assert schema_validator.validate({"n": 1.5}, schema) != []
    assert schema_validator.validate({"n": 1}, schema) == []
    assert schema_validator.validate({"n": 1}, {"type": "object", "properties": {"n": {"type": "number"}}}) == []


def test_an_extra_key_anywhere_validates(harness_root):
    schema = schema_validator.load_schema("verification-result", harness_root)
    instance = {
        "status": "failed",
        "retry_recommended": True,
        "confidence": 0.9,
        "blocking_issues": [
            {
                "severity": "high",
                "issue": "i",
                "location": "l",
                "required_behavior": "r",
                "evidence": ["extra"],
            }
        ],
    }
    assert schema_validator.validate(instance, schema) == []


# --------------------------------------------------------------------------
# Coordinator escalation
# --------------------------------------------------------------------------

VALID_CHANGED_FILES = {"modified": ["src/app.py"], "created": [], "deleted": []}
VALID_TESTER_RECORD = {"modified": [], "created": ["tests/test_app.py"], "deleted": []}
VALID_TEST_RESULTS = {"status": "passed", "tests_run": 1, "tests_passed": 1}
VALID_VERDICT = {
    "status": "passed",
    "blocking_issues": [],
    "unverified": [],
    "retry_recommended": False,
}


class ArtifactRunner:
    """Writes valid artifacts unless an override replaces one of them."""

    def __init__(self, target_root: Path, story_id: str = "story-001", **overrides):
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.overrides = overrides
        self.calls: list[str] = []

    def _payload(self, artifact: str, default):
        return self.overrides.get(artifact, default)

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None):
        self.calls.append(stage)
        if stage == "implementer":
            _write(self.run_dir / "changed-files.json",
                   self._payload("changed-files.json", VALID_CHANGED_FILES))
            (self.run_dir / "implementation-summary.md").write_text("done\n")
        elif stage == "tester":
            _write(self.run_dir / "test-results.json",
                   self._payload("test-results.json", VALID_TEST_RESULTS))
            _write(self.run_dir / "tester-changed-files.json",
                   self._payload("tester-changed-files.json", VALID_TESTER_RECORD))
        elif stage == "verifier":
            _write(self.run_dir / "verification-result.json",
                   self._payload("verification-result.json", VALID_VERDICT))
        elif stage == "documenter":
            (self.run_dir / "documentation-report.md").write_text("n/a\n")
        return AgentResult(ok=True, result_text=f"{stage} ok")


def _escalation_texts(run_dir: Path) -> tuple[str, str]:
    return (
        (run_dir / "events.log").read_text(),
        (run_dir / "escalation-summary.md").read_text(),
    )


def test_verdict_missing_a_routed_field_escalates_instead_of_routing(
    target_root, harness_root
):
    """The coordinator routes on retry_recommended; without it the run stops."""
    runner = ArtifactRunner(
        target_root,
        **{"verification-result.json": {"status": "passed", "blocking_issues": []}},
    )
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    assert runner.calls == ["implementer", "tester", "verifier"]

    state = json.loads((runner.run_dir / "state.json").read_text())
    assert state["status"] == "escalated"
    assert state["retry_count"] == 0
    assert not (runner.run_dir / "completion-report.md").exists()

    for text in _escalation_texts(runner.run_dir):
        assert "verification-result.json" in text
        assert "$.retry_recommended" in text


def test_out_of_enum_status_escalates_naming_the_allowed_values(
    target_root, harness_root
):
    runner = ArtifactRunner(
        target_root,
        **{"verification-result.json": {"status": "flaky", "retry_recommended": False}},
    )
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 2
    _, summary = _escalation_texts(runner.run_dir)
    assert "$.status" in summary
    assert '"passed"' in summary and '"failed"' in summary
    assert "flaky" in summary


def test_unparseable_changed_files_escalates_before_the_blocked_paths_check(
    target_root, harness_root
):
    """_blocked_violation json.loads the same file; validation must run first."""
    runner = ArtifactRunner(target_root, **{"changed-files.json": "not json at all"})
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    assert runner.calls == ["implementer"]
    _, summary = _escalation_texts(runner.run_dir)
    assert "changed-files.json" in summary
    assert "not parseable as JSON" in summary
    assert "Traceback" not in summary


def test_a_tester_record_of_the_wrong_shape_escalates_with_its_path(
    target_root, harness_root
):
    runner = ArtifactRunner(
        target_root,
        **{"tester-changed-files.json": {"modified": [], "created": [2], "deleted": []}},
    )
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    assert runner.calls == ["implementer", "tester"]
    assert json.loads((runner.run_dir / "state.json").read_text())["retry_count"] == 0
    for text in _escalation_texts(runner.run_dir):
        assert "tester-changed-files.json" in text
        assert "$.created[0]" in text
        assert "expected type string" in text


def test_extra_keys_on_every_artifact_do_not_stop_a_run(target_root, harness_root):
    runner = ArtifactRunner(
        target_root,
        **{
            "changed-files.json": {**VALID_CHANGED_FILES, "notes": "extra"},
            "test-results.json": {**VALID_TEST_RESULTS, "duration_s": 2.9},
            "tester-changed-files.json": {**VALID_TESTER_RECORD, "notes": ["extra"]},
            "verification-result.json": {**VALID_VERDICT, "confidence": "high"},
        },
    )
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 0
    assert runner.calls == ["implementer", "tester", "verifier", "documenter"]
    assert not (runner.run_dir / "escalation-summary.md").exists()


def test_valid_artifacts_route_exactly_as_before(target_root, harness_root):
    runner = ArtifactRunner(target_root)
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 0
    assert (runner.run_dir / "completion-report.md").is_file()
    assert not (runner.run_dir / "retry-guidance.json").exists()


# --------------------------------------------------------------------------
# Story sections derived from the schema
# --------------------------------------------------------------------------


def test_enforced_story_sections_are_unchanged_and_schema_derived(harness_root):
    """story-005 replaced the line-prefix check with parse-then-validate; the
    set of top-level sections enforced is still the schema's required list."""
    required = tuple(
        json.loads((harness_root / "schemas" / "story.schema.json").read_text())["required"]
    )
    assert required == ORIGINAL_STORY_SECTIONS
    problems = story_coordinator.read_story("story:\n  id: x\n").problems
    missing = {
        problem.split(":")[0].removeprefix("$.")
        for problem in problems
        if "found it missing" in problem
    }
    missing = {section for section in missing if "." not in section}
    assert missing == set(ORIGINAL_STORY_SECTIONS) - {"story"}


def test_the_section_list_tracks_the_schema_file_rather_than_a_constant(tmp_path):
    """Editing the schema changes what is enforced — proof it is not hard-coded."""
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    _write(schemas / "story.schema.json",
           {"type": "object", "required": ["story", "sentinel_section"],
            "properties": {"story": {"type": "object"}}})
    assert story_coordinator.read_story("story:\n  id: x\n", tmp_path).problems == [
        "$.sentinel_section: expected a required property, found it missing"
    ]


# --------------------------------------------------------------------------
# Prompts carry the schema, not a copy of it
# --------------------------------------------------------------------------


def test_the_three_rewritten_prompts_contain_no_inline_json_body(harness_root):
    """Once placeholders are stripped, no JSON object punctuation survives."""
    for name in ("implementer.md", "tester.md", "verifier.md"):
        text = (harness_root / "prompts" / name).read_text()
        stripped = PLACEHOLDER.sub("", text)
        assert not set(stripped) & set("{}"), name


def test_each_prompt_injects_the_schema_for_every_artifact_it_must_write(harness_root):
    stages = _workflow(harness_root)
    for stage_name in ("implementer", "tester", "verifier"):
        stage = stages[stage_name]
        text = (harness_root / "prompts" / stage["prompt"]).read_text()
        for artifact, schema_name in stage["schemas"].items():
            placeholder = "{{" + schema_name.replace("-", "_") + "_schema}}"
            assert placeholder in text, (stage_name, artifact)
            assert artifact in text, (stage_name, artifact)


def test_prompts_this_story_leaves_alone_carry_no_schema_placeholders(harness_root):
    # planner.md left this list in story-008, which injects {{story_schema}}
    # there deliberately; tests/test_story_008_validation.py holds that
    # property now. The assertion is unchanged for the prompts still without
    # schema injection.
    for name in ("assist.md", "documenter.md", "harness-layer.md"):
        text = (harness_root / "prompts" / name).read_text()
        assert "_schema}}" not in text, name


def test_rendered_prompts_carry_the_schema_file_verbatim(
    target_root, harness_root
):
    import harness_config

    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    context = context_assembler.build_context(
        story_text=story_text,
        story=story_parser.parse(story_text, schema_validator.load_schema("story")),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=harness_config.load_config(target_root),
        rules=harness_config.load_rules(harness_root),
        retry_count=0,
    )
    stages = _workflow(harness_root)
    for stage_name in ("implementer", "tester", "verifier"):
        stage = stages[stage_name]
        rendered = context_assembler.render(
            context_assembler.load_template(harness_root, stage["prompt"]), context
        )
        assert "{{" not in rendered, stage_name
        for schema_name in set(stage["schemas"].values()):
            body = (harness_root / "schemas" / f"{schema_name}.schema.json").read_text()
            assert body in rendered, (stage_name, schema_name)
        # The injected text is a usable schema, not a truncated fragment.
        assert '"$schema"' in rendered, stage_name


def test_a_missing_schemas_directory_does_not_break_context_assembly(
    target_root, tmp_path
):
    """build_context tolerates an absent schemas/ exactly as it tolerates an
    absent harness-layer.md, so a partial harness copy still assembles."""
    import harness_config

    fake_root = tmp_path / "harness"
    (fake_root / "prompts").mkdir(parents=True)
    (fake_root / "prompts" / "stage.md").write_text("Story:\n{{story}}\n")
    context = context_assembler.build_context(
        story_text="story:\n  id: story-001\n",
        story={"story": {"id": "story-001"}},
        run_dir=target_root / ".harness" / "runs" / "story-001",
        target_root=target_root,
        harness_root=fake_root,
        config=harness_config.load_config(target_root),
        rules={"blocked_paths": [], "max_retries": 2},
        retry_count=0,
    )
    assert not any(key.endswith("_schema") for key in context)


# --------------------------------------------------------------------------
# Test-suite hygiene
# --------------------------------------------------------------------------


def test_no_test_reads_the_repository_run_directory(harness_root):
    """.harness/runs/ is gitignored and absent in CI; the only way a test could
    reach it is through the harness root, so no test may join the two."""
    this_file = Path(__file__).name
    for path in sorted((harness_root / "tests").glob("*.py")):
        if path.name == this_file:      # this guard quotes the patterns it forbids
            continue
        text = path.read_text()
        for forbidden in ('harness_root / ".harness"', 'HARNESS_ROOT / ".harness"'):
            assert forbidden not in text, f"{path.name} reads {forbidden}"
