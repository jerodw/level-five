"""A suite run records what it was narrowed to, and nothing decides on it.

Every suite run the coordinator records said what command ran, what executable
ran it and what it exited with. It did not say what the run *covered*: a run of
the configured `test_command` and a run narrowed to a single nominated test both
exit zero, and nothing downstream could tell them apart. `CleanCloneResult` now
carries a `scope` — the selections the coordinator substituted into the
configured command, verbatim — and the three records that shape carries it into
serialize it always.

What `scope` claims is narrow and this module holds it to that. An empty scope
says *the coordinator narrowed the configured command by nothing*. It is not the
claim that the configured command covers the whole of the target's tests, which
is the target's business and is nowhere in this record; and it is not the same
statement as an absent field, which is why the field is required in all three
schemas and written even when it is empty.

The subjects here are the dataclass, the source of `orchestration/`, the three
shipped schemas and the records a real run writes. The run is driven through
`tests/test_coordinator_runs_the_suite.py`'s built workflow — the one that
declares all three coordinator suite runs at once — so the artifact names come
off a definition a test built rather than off what this repository deploys. The
schemas are read as shipped, deliberately: an assertion about what this harness
ships has to read what it ships. Which shipped schema describes each of those
records is the derivation that module already makes and exports, reused here
rather than made a second time.

Every absence asserted below carries a demonstration that it can fail:

  * "no `CleanCloneResult` construction under `orchestration/` omits the field"
    sits beside the same scan over that source with a construction that omits it
    appended, which the scan reports;
  * "no decision in `orchestration/` reads the field" sits beside the same scan
    over that source with a branch on it planted, which the scan reports;
  * "a record that omits the field fails validation" is itself the control for
    the field being required, run beside the same instance carrying it, which
    validates;
  * "the schema's description makes no claim about coverage" sits beside the
    same reading of a description that does make one, which it reports;
  * "the nomination block carries no scope" sits beside the same lookup in the
    record around it, which finds one;
  * "the census record and its schema carry no scope" sits beside the same three
    lookups against the clean-clone record and schema, which find one;
  * "the field has no default" sits beside the fields of that same dataclass
    that do have one.
"""
import ast
import dataclasses
import shlex
import sys
from pathlib import Path

import pytest

import schema_validator
import story_coordinator

# The all-three-suite-runs fixture, its target builder and its readers. Reused
# rather than rebuilt: one run there already makes the revert check, the
# declared suite run and the clean-clone check, which is exactly the set of
# records this field has to appear in.
from test_coordinator_runs_the_suite import (  # noqa: F401
    ALL_THREE,
    COORDINATOR_RECORD_SCHEMAS,
    PROMPTS,
    SUITE_DECLARATION_KEYS,
    VERIFIER_PROMPT,
    VERIFYING,
    WORKFLOW,
    all_three_run,
    drive,
    make_target,
    materialize,
    record_of,
    rendered_prompt,
    suite_declarations_in,
)

#: The field this module is about. Written here once, because it *is* the
#: subject: the story asks for a field of this name in these records.
SCOPE = "scope"

ORCHESTRATION_DIR = Path(story_coordinator.__file__).resolve().parent
ORCHESTRATION_MODULES = sorted(ORCHESTRATION_DIR.glob("*.py"))
COORDINATOR_SOURCE = Path(story_coordinator.__file__).read_text(encoding="utf-8")

RESULT = story_coordinator.CleanCloneResult

#: The arguments a `CleanCloneResult` needs apart from the scope, so that the
#: construction below differs from a working one in the scope alone.
MINIMAL = {"ran": False, "command": "a command", "runner": "a runner"}


# --------------------------------------------------------------------------
# The field on the record
# --------------------------------------------------------------------------


def test_the_field_carries_no_default_and_a_construction_omitting_it_raises():
    """Not merely undefaulted in the declaration: a construction site that adds
    itself later and forgets the field fails rather than recording a run as
    unnarrowed by accident.

    The fields beside it that *do* carry a default are the control — "has no
    default" is a property this dataclass's fields differ in, so finding it of
    the scope says something.
    """
    fields = {field.name: field for field in dataclasses.fields(RESULT)}
    defaulted = [name for name, field in fields.items()
                 if field.default is not dataclasses.MISSING
                 or field.default_factory is not dataclasses.MISSING]

    assert SCOPE in fields
    assert fields[SCOPE].default is dataclasses.MISSING
    assert fields[SCOPE].default_factory is dataclasses.MISSING
    assert defaulted and SCOPE not in defaulted

    with pytest.raises(TypeError):
        RESULT(**MINIMAL)
    assert RESULT(**MINIMAL, scope=()).scope == ()


def test_an_empty_scope_is_written_where_an_absent_optional_field_is_not():
    """The whole point of the field being required: an empty scope is a claim,
    and a claim that serialized as absence would be indistinguishable from the
    record of a version that never made it.

    The same record's optional fields are the control — this result carries no
    reason and no exit code, and neither appears — so the scope appearing is
    the convention being departed from deliberately rather than everything
    being written.
    """
    record = RESULT(**MINIMAL, scope=()).as_record()

    assert record[SCOPE] == []
    assert "reason" not in record
    assert "exit_code" not in record


def test_the_selections_are_recorded_verbatim_and_uninterpreted():
    """The harness neither parses nor understands the target's selector syntax,
    so what a strange-looking selection serializes as is itself."""
    selections = ("tests/test_x.py::test_y[a-b]", "-k not slow and weird")
    record = RESULT(**MINIMAL, scope=selections).as_record()

    assert record[SCOPE] == list(selections)


# --------------------------------------------------------------------------
# Every construction supplies it, enumerated rather than sampled
# --------------------------------------------------------------------------


def _is_the_result(func: ast.expr) -> bool:
    name = RESULT.__name__
    return (isinstance(func, ast.Name) and func.id == name) or (
        isinstance(func, ast.Attribute) and func.attr == name)


def constructions_in(source: str) -> list[int]:
    """The line of every `CleanCloneResult` construction in `source`."""
    return [node.lineno for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and _is_the_result(node.func)]


def constructions_without_the_scope(source: str) -> list[int]:
    """The line of every such construction that passes no scope."""
    return [node.lineno for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and _is_the_result(node.func)
            and not any(keyword.arg == SCOPE for keyword in node.keywords)]


#: A construction with no scope, appended to a source to show the scan reports
#: one. Parsed rather than run, so where it sits in the module is irrelevant.
OMITTING_CONSTRUCTION = f"\n{RESULT.__name__}(ran=False, command='c', runner='r')\n"

#: The same construction, supplying the field.
SUPPLYING_CONSTRUCTION = (
    f"\n{RESULT.__name__}(ran=False, command='c', runner='r', {SCOPE}=())\n")


def test_the_enumeration_finds_the_constructions_it_is_about():
    """The premise the assertion below rests on: a scan that found nothing
    would pass it vacuously."""
    assert constructions_in(COORDINATOR_SOURCE)


@pytest.mark.parametrize("module", ORCHESTRATION_MODULES,
                         ids=lambda path: path.name)
def test_no_construction_under_orchestration_omits_the_scope(module):
    """Enumerated over the whole of `orchestration/` rather than over the
    constructions some run happened to reach — including every path that
    reports a run that did not happen, which is where an omission would be
    least visible."""
    source = module.read_text(encoding="utf-8")
    assert constructions_without_the_scope(source) == []


def test_the_same_scan_reports_a_construction_that_omits_it():
    """The control. The scan is looking, and it reports the omission when there
    is one — and stays silent when the same construction supplies the field, so
    what it reports is the omission rather than the construction."""
    assert constructions_without_the_scope(
        COORDINATOR_SOURCE + OMITTING_CONSTRUCTION)
    assert constructions_without_the_scope(
        COORDINATOR_SOURCE + SUPPLYING_CONSTRUCTION) == []


# --------------------------------------------------------------------------
# Nothing decides on it
# --------------------------------------------------------------------------


def _reads_the_scope(node: ast.AST) -> bool:
    return any(
        (isinstance(child, ast.Attribute) and child.attr == SCOPE)
        or (isinstance(child, ast.Name) and child.id == SCOPE)
        for child in ast.walk(node))


def _decision_subjects(tree: ast.AST):
    """Every expression a control-flow decision is taken on."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.IfExp, ast.While, ast.Assert)):
            yield node.test
        elif isinstance(node, ast.Match):
            yield node.subject
        elif isinstance(node, (ast.Compare, ast.BoolOp)):
            yield node


def decisions_reading_the_scope(source: str) -> list[int]:
    """The line of every decision in `source` whose subject reads the scope."""
    return [node.lineno for node in _decision_subjects(ast.parse(source))
            if _reads_the_scope(node)]


#: The one rule the scope may be decided through, since story-085: whether a
#: passing suite run's recorded scope is a subset of an earlier failing run's,
#: which is what decides that the pass supersedes the failure. story-083
#: recorded the scope and routed on nothing; story-085 routes on it, and this
#: names the one decision that may. It is an exemption held shut from both
#: sides — a decision on the scope taken anywhere else is still reported by the
#: assertion below, and a repository that stops taking this one through the
#: rule fails the assertion beside it.
SHADOW_RULE = "suite_run_shadows"


def _calls_the_shadow_rule(node: ast.AST) -> bool:
    return any(isinstance(child, ast.Call)
               and isinstance(child.func, ast.Name)
               and child.func.id == SHADOW_RULE
               for child in ast.walk(node))


def decisions_outside_the_shadow_rule(source: str) -> list[int]:
    """The line of every decision on the scope taken other than through the rule."""
    return [node.lineno for node in _decision_subjects(ast.parse(source))
            if _reads_the_scope(node) and not _calls_the_shadow_rule(node)]


#: A branch on the field, and a comparison of it, appended to a source to show
#: the scan reports a read when there is one.
BRANCHING_ON_THE_SCOPE = f"\nif result.{SCOPE}:\n    pass\n"
COMPARING_THE_SCOPE = f"\nnarrowed = result.{SCOPE} != ()\n"


@pytest.mark.parametrize("module", ORCHESTRATION_MODULES,
                         ids=lambda path: path.name)
def test_no_decision_under_orchestration_consults_the_scope(module):
    """The scope is decided through one rule and read inline by nothing. It is
    declared, passed along and serialized, and no branch, comparison or
    assertion anywhere in `orchestration/` takes its value as a subject except
    by handing it to the rule that owns the comparison."""
    source = module.read_text(encoding="utf-8")
    assert decisions_outside_the_shadow_rule(source) == []


def test_the_exempt_rule_is_one_the_coordinator_actually_decides_through():
    """The other side of the exemption. A name kept after its decision is gone
    is a hole nobody notices, so the coordinator must still take a decision on
    the scope, and it must be that one."""
    taken = decisions_reading_the_scope(COORDINATOR_SOURCE)
    assert taken, "no decision reads the scope; the exemption above is stale"
    assert decisions_outside_the_shadow_rule(COORDINATOR_SOURCE) == []


@pytest.mark.parametrize("planted", [BRANCHING_ON_THE_SCOPE,
                                     COMPARING_THE_SCOPE],
                         ids=["a branch", "a comparison"])
def test_the_same_scan_reports_a_decision_planted_in_that_source(planted):
    """The control, in both shapes a decision on the field would take. Both
    scans report it: the exemption admits the rule, not the field."""
    assert decisions_reading_the_scope(COORDINATOR_SOURCE + planted)
    assert decisions_outside_the_shadow_rule(COORDINATOR_SOURCE + planted)


# --------------------------------------------------------------------------
# The three schemas
#
# Which checks make a coordinator suite run is read off the built definition
# that declares all three; which shipped record each of those writes is read off
# the shipped workflow, because a shipped schema is the subject here.
# --------------------------------------------------------------------------


FIXTURE_ARTIFACTS = suite_declarations_in(ALL_THREE["stages"])

#: Declaration key to the shipped schema describing that check's record, paired
#: off the two collections `test_coordinator_runs_the_suite` already derives —
#: the keys off the fixture that declares all three, the schema names off what
#: this repository ships. Reused rather than derived a second time here, so
#: this module resolves no shipped workflow of its own.
RECORD_SCHEMAS = dict(zip(SUITE_DECLARATION_KEYS, COORDINATOR_RECORD_SCHEMAS))


def test_the_derived_collection_names_the_records_this_story_is_about():
    """The premise the parametrization rests on, so that a derivation
    collecting nothing reddens here rather than emptying the cases."""
    shipped = schema_validator.shipped_schemas()
    assert set(RECORD_SCHEMAS) == set(FIXTURE_ARTIFACTS)
    assert len(set(RECORD_SCHEMAS.values())) == len(RECORD_SCHEMAS)
    for stem in RECORD_SCHEMAS.values():
        assert stem in shipped, stem


SCHEMA_STEMS = sorted(RECORD_SCHEMAS.values())


def sample_value(declaration: dict):
    """A value of the type a property declares, so an instance can be built
    from a schema's own `required` list rather than written out per schema."""
    return {"string": "x", "boolean": True, "integer": 0, "number": 0,
            "array": [], "object": {}}[declaration["type"]]


def minimal_instance(schema: dict) -> dict:
    return {name: sample_value(schema["properties"][name])
            for name in schema["required"]}


@pytest.mark.parametrize("stem", SCHEMA_STEMS)
def test_each_record_declares_the_scope_as_an_array_of_strings(stem):
    schema = schema_validator.load_schema(stem)
    declaration = schema["properties"][SCOPE]

    assert declaration["type"] == "array"
    assert declaration["items"]["type"] == "string"


@pytest.mark.parametrize("stem", SCHEMA_STEMS)
def test_a_record_omitting_the_scope_fails_validation(stem):
    """The field is required, so an empty scope and an absent one cannot
    serialize alike. The instance carrying it is the control: the same
    instance, minus the one field, is the only difference between the two
    verdicts."""
    schema = schema_validator.load_schema(stem)
    instance = minimal_instance(schema)

    assert SCOPE in schema["required"]
    assert schema_validator.validate(instance, schema) == []
    omitted = {key: value for key, value in instance.items() if key != SCOPE}
    errors = schema_validator.validate(omitted, schema)
    assert errors
    assert any(SCOPE in error for error in errors)


def sentences(text: str) -> list[str]:
    return [sentence.strip().lower()
            for sentence in text.replace("\n", " ").split(". ")
            if sentence.strip()]


DENIALS = ("not", "never", "nothing")


def explains_an_empty_scope(description: str) -> bool:
    """Whether some sentence says what an empty scope means: that the
    configured command was narrowed by nothing."""
    return any("empty" in sentence and "narrow" in sentence
               for sentence in sentences(description))


def claims_coverage(description: str) -> list[str]:
    """Every sentence that speaks of what the command covers without denying
    the claim. An empty result is what a description saying only what it may
    say looks like."""
    return [sentence for sentence in sentences(description)
            if "cover" in sentence
            and not any(denial in sentence for denial in DENIALS)]


#: A description that does make the claim, so the reading above can be shown to
#: report one. Its shape is the shape the real descriptions deliberately avoid.
CLAIMING_DESCRIPTION = (
    "The selections substituted into the configured command. An empty array "
    "means the configured command covers the whole of the target's tests."
)


@pytest.mark.parametrize("stem", SCHEMA_STEMS)
def test_each_description_says_what_an_empty_scope_means_and_what_it_does_not(
    stem,
):
    """Both halves, because either alone misleads: a description that says only
    "empty means unnarrowed" leaves a reader to supply the coverage claim
    themselves, and one that only denies coverage never says what the value
    does mean."""
    description = schema_validator.load_schema(stem)["properties"][SCOPE][
        "description"]

    assert explains_an_empty_scope(description)
    assert claims_coverage(description) == []
    assert any("cover" in sentence and any(d in sentence for d in DENIALS)
               for sentence in sentences(description))


def test_the_same_reading_reports_a_description_that_does_claim_coverage():
    """The control for the absence above: a description claiming the configured
    command covers everything is reported, and one that denies it is not."""
    assert claims_coverage(CLAIMING_DESCRIPTION)
    assert not explains_an_empty_scope(CLAIMING_DESCRIPTION)


# --------------------------------------------------------------------------
# What a coordinator run of the configured command records
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(FIXTURE_ARTIFACTS))
def test_a_run_of_the_configured_command_records_an_empty_scope(
    all_three_run, key,
):
    """One run making all three coordinator suite runs, each of them the
    configured `test_command` as configured. Each record carries the field, each
    carries it empty, and each still validates against the schema that describes
    it."""
    code, _, run_dir = all_three_run
    record = record_of(run_dir, FIXTURE_ARTIFACTS[key])

    assert code == 0
    assert SCOPE in record
    assert record[SCOPE] == []
    assert schema_validator.validate(
        record, schema_validator.load_schema(RECORD_SCHEMAS[key])) == []


def test_the_field_reaches_the_verifier_in_the_record_it_is_given(
    make_target, tmp_path,
):
    """Driven as a run rather than argued from the template: a harness root
    whose verifying stage carries the shipped verifier prompt, and the scope the
    coordinator recorded is in the prompt that stage was handed."""
    target_root = make_target("verifier-sees-the-scope")
    harness = materialize(WORKFLOW, tmp_path / "verifier-scope-harness",
                          {**PROMPTS, VERIFYING: VERIFIER_PROMPT})
    code, _, run_dir = drive(target_root, harness)
    prompt = rendered_prompt(run_dir, VERIFYING)

    assert code == 0
    assert f'"{SCOPE}": []' in prompt


# --------------------------------------------------------------------------
# The selector runs, which are the runs that are narrowed
# --------------------------------------------------------------------------


NOMINATED = "tests/test_something.py::test_the_thing"

#: A command that runs in a clone and exits zero. Its content is irrelevant —
#: what is asserted is the scope the result carries, not what it decided.
SELECTION_COMMAND = shlex.join([sys.executable, "-c", "pass"])


def test_the_result_a_selection_returns_carries_the_nomination_as_its_scope(
    target_root,
):
    """A filtered invocation and an unfiltered one are told apart from the
    record alone: this one ran, and it says what it was narrowed to."""
    result = story_coordinator._run_selection(
        target_root, {}, SELECTION_COMMAND, scope=(NOMINATED,))

    assert result.ran is True
    assert result.scope == (NOMINATED,)
    assert result.as_record()[SCOPE] == [NOMINATED]


def test_a_selection_whose_clone_could_not_be_built_carries_it_too(tmp_path):
    """The path that reports a run that did not happen. A selector run
    recording an empty scope would claim it ran the configured command
    unnarrowed, which is the one thing it never does."""
    not_a_repository = tmp_path / "not-a-repository"
    not_a_repository.mkdir()

    result = story_coordinator._run_selection(
        not_a_repository, {}, SELECTION_COMMAND, scope=(NOMINATED,))

    assert result.ran is False
    assert result.scope == (NOMINATED,)


def test_both_of_the_nominations_selector_runs_are_given_the_nomination(
    monkeypatch, tmp_path,
):
    """The applied run and the reverted one. Substituted for rather than
    cloned: what is asserted is the argument each call was given, so the runs
    are stood in for by results that make the check reach both of them —
    passing applied, failing reverted."""
    scopes = []
    codes = iter([0, 1])

    def record_the_scope(target_root, config, command, *, scope, **kwargs):
        scopes.append(scope)
        return RESULT(ran=True, command=command, runner="runner", scope=scope,
                      exit_code=next(codes))

    monkeypatch.setattr(story_coordinator, "_run_selection", record_the_scope)
    nomination = story_coordinator.run_nomination(
        tmp_path,
        {"test_selection_command":
         f"a-runner {story_coordinator.TEST_SUBSTITUTION}"},
        NOMINATED, ("some/path",), tmp_path / "baseline")

    assert nomination.short_circuited is True
    assert scopes == [(NOMINATED,), (NOMINATED,)]


# --------------------------------------------------------------------------
# What the field is not: the nomination block, and the census
# --------------------------------------------------------------------------


def test_the_nomination_block_carries_no_scope_of_its_own():
    """`nomination.test` already names what those runs were narrowed to, so a
    scope beside it would be a second spelling of one fact. The record around
    it is the control: the same lookup, one level out, finds the field."""
    check = story_coordinator.RevertCheckResult(
        result=RESULT(**MINIMAL, scope=()),
        paths=("some/path",),
        permitted=True,
        nomination=story_coordinator.Nomination(True, test=NOMINATED))
    record = check.as_record()

    assert SCOPE in record
    assert SCOPE not in record["nomination"]


def test_the_shipped_nomination_sub_schema_declares_no_scope_either():
    """The same division in the schema, with the schema around it as the
    control."""
    schema = schema_validator.load_schema(RECORD_SCHEMAS["revert_check"])
    nomination = schema["properties"]["nomination"]

    assert SCOPE in schema["properties"]
    assert SCOPE in schema["required"]
    assert SCOPE not in nomination["properties"]
    assert SCOPE not in nomination["required"]


CENSUS_SCHEMA = "census-result"


def test_the_census_record_and_its_schema_carry_no_scope():
    """The census command is not the suite and is never narrowed, so there is
    no scope there to record. The clean-clone record and schema are the
    control: the same three lookups find the field on the record shape this
    story did change."""
    census = schema_validator.load_schema(CENSUS_SCHEMA)
    suite = schema_validator.load_schema(RECORD_SCHEMAS["clean_clone"])
    census_fields = {field.name for field in
                     dataclasses.fields(story_coordinator.CensusResult)}

    assert SCOPE not in census_fields
    assert SCOPE not in census["properties"]
    assert SCOPE not in census.get("required", [])

    assert SCOPE in {field.name for field in dataclasses.fields(RESULT)}
    assert SCOPE in suite["properties"]
    assert SCOPE in suite["required"]
