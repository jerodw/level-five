"""Unit tests for the schema subset the harness enforces.

Fixtures are inline: nothing here reads .harness/runs/, which is gitignored
and absent in CI.
"""
import json

import pytest

import schema_validator

SHIPPED = (
    "changed-files",
    "test-results",
    "verification-result",
    "retry-guidance",
    "story",
    # Coordinator-written rather than stage-written, so no workflow stage maps
    # to it; it is shipped and validator-checked like every other schema.
    "execution-history",
    # story-014: the coordinator's clean-clone record, coordinator-written for
    # the same reason. The inventory below stays exact.
    "clean-clone-result",
)

OBJECT_SCHEMA = {
    "type": "object",
    "required": ["name", "count"],
    "properties": {
        "name": {"type": "string"},
        "count": {"type": "integer"},
        "mode": {"type": "string", "enum": ["fast", "slow"]},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}


def test_valid_instance_produces_no_errors():
    instance = {"name": "a", "count": 1, "mode": "fast", "tags": ["x"]}
    assert schema_validator.validate(instance, OBJECT_SCHEMA) == []


def test_missing_required_field_names_the_path():
    errors = schema_validator.validate({"name": "a"}, OBJECT_SCHEMA)
    assert len(errors) == 1
    assert "$.count" in errors[0]
    assert "required" in errors[0]
    assert "missing" in errors[0]


def test_wrong_type_reports_expected_and_found():
    errors = schema_validator.validate({"name": "a", "count": "many"}, OBJECT_SCHEMA)
    assert len(errors) == 1
    assert "$.count" in errors[0]
    assert "expected type integer" in errors[0]
    assert "many" in errors[0]


def test_out_of_enum_value_reports_allowed_options_and_found():
    errors = schema_validator.validate(
        {"name": "a", "count": 1, "mode": "sideways"}, OBJECT_SCHEMA
    )
    assert len(errors) == 1
    assert "$.mode" in errors[0]
    assert '"fast"' in errors[0] and '"slow"' in errors[0]
    assert "sideways" in errors[0]


def test_extra_unlisted_key_is_accepted():
    instance = {"name": "a", "count": 1, "harmless": {"anything": True}}
    assert schema_validator.validate(instance, OBJECT_SCHEMA) == []


def test_booleans_are_not_integers():
    errors = schema_validator.validate({"name": "a", "count": True}, OBJECT_SCHEMA)
    assert "expected type integer" in errors[0]


def test_items_errors_carry_array_index_paths():
    errors = schema_validator.validate(
        {"name": "a", "count": 1, "tags": ["ok", 7]}, OBJECT_SCHEMA
    )
    assert len(errors) == 1
    assert "$.tags[1]" in errors[0]


def test_nested_object_in_array_path_formatting():
    schema = json.loads(
        (schema_validator.schemas_dir() / "verification-result.schema.json").read_text()
    )
    instance = {
        "status": "failed",
        "retry_recommended": True,
        "blocking_issues": [
            {
                "severity": "critical",
                "issue": "x",
                "location": "y",
                "required_behavior": "z",
            }
        ],
    }
    errors = schema_validator.validate(instance, schema)
    assert any("$.blocking_issues[0].severity" in error for error in errors)


def test_missing_routed_field_names_its_path():
    schema = schema_validator.load_schema("verification-result")
    errors = schema_validator.validate({"status": "passed"}, schema)
    assert any("$.retry_recommended" in error for error in errors)


def test_top_level_type_mismatch_short_circuits():
    errors = schema_validator.validate(["not", "an", "object"], OBJECT_SCHEMA)
    assert len(errors) == 1
    assert errors[0].startswith("$: expected type object")


def test_unsupported_keyword_raises_rather_than_being_ignored():
    schema = {"type": "object", "additionalProperties": False}
    with pytest.raises(ValueError) as excinfo:
        schema_validator.validate({}, schema)
    assert "additionalProperties" in str(excinfo.value)


def test_unsupported_keyword_nested_in_properties_raises():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "array", "items": {"minLength": 3}}},
    }
    with pytest.raises(ValueError) as excinfo:
        schema_validator.validate({"a": ["xy"]}, schema)
    assert "properties.a.items.minLength" in str(excinfo.value)


def test_unknown_type_name_raises():
    with pytest.raises(ValueError):
        schema_validator.validate("x", {"type": "text"})


def test_shipped_schemas_are_exactly_the_named_ones():
    names = sorted(p.name for p in schema_validator.schemas_dir().glob("*"))
    assert names == sorted(f"{name}.schema.json" for name in SHIPPED)


@pytest.mark.parametrize("name", SHIPPED)
def test_shipped_schema_is_valid_json_draft_2020_12_and_supported(name):
    schema = schema_validator.load_schema(name)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema_validator.unsupported_keywords(schema) == []


@pytest.mark.parametrize("name", SHIPPED)
def test_no_shipped_schema_forbids_additional_properties(name):
    text = (schema_validator.schemas_dir() / f"{name}.schema.json").read_text()
    assert "additionalProperties" not in text


def test_shipped_schemas_require_the_fields_the_coordinator_routes_on():
    assert set(schema_validator.load_schema("verification-result")["required"]) == {
        "status",
        "retry_recommended",
    }
    assert "status" in schema_validator.load_schema("test-results")["required"]
    assert set(schema_validator.load_schema("changed-files")["required"]) == {
        "modified",
        "created",
        "deleted",
    }


def test_story_schema_describes_the_full_shape():
    schema = schema_validator.load_schema("story")
    assert schema["required"] == [
        "story",
        "tasks",
        "acceptance_criteria",
        "scope",
        "verification_requirements",
        "constraints",
    ]
    assert "technical_plan" in schema["properties"]
    assert "technical_plan" not in schema["required"]
    story_props = schema["properties"]["story"]["properties"]
    assert {"id", "title", "description"} <= set(story_props)
    assert schema["properties"]["tasks"]["items"]["type"] == "string"
