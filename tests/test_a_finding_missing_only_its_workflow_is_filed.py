"""A finding complete but for its workflow is filed with one supplied.

The Inspector writes findings that are checked against the brief shape before
anything is filed, and a finding that named no workflow used to be thrown away
whole — the defect, the evidence, the severity and the paths with it. This
module holds the behaviour that replaced that: where a finding names no
workflow the harness fills in the one the target's configuration declares,
marks the brief as having had one supplied rather than chosen, and files it.
Everything else the shape requires is unchanged, a workflow the harness does
not define is still refused, and the two paths where a person is present to be
asked — a brief filed by hand and a brief fetched for planning — go on refusing
an absent workflow.

Where each claim is made, and against what:

  * **the supply, and what reaches the queue.** Real broad inspections of a
    target this module builds under a temporary directory, against the mirrored
    harness root `tests/test_inspection.py` builds: it defines two workflows
    this repository does not ship, so the target is configured with one of them
    and the finding beside the supplied one names the *other*. A supplied value
    is therefore traceable to the configuration rather than to "whichever
    workflow was lying about" — the two names differ, and the test that rests
    on it says so first.

  * **the value, spelled once.** `harness_config.configured_workflow` is driven
    directly, and the spelling of the fallback it carries is searched for over
    every file under `orchestration/` and `scripts/`. That scan is a claim about
    what this repository ships and reads the shipped tree deliberately; its
    control is the identical scan over the same files plus one this module
    plants, which reports both.

  * **the three report surfaces.** The broad report is read through
    `scripts/l5-inspect` itself against reports this module constructs, because
    what is under test there is the words that script prints. The post-story
    report is read out of a whole run's events.log, where the ordering claim —
    a line of its own before the summary — can be made at all. The completion
    filing is driven through `story_inspection.file_at_completion` against a
    held artifact, which is also where the mark is shown surviving the file a
    finding travels in between the inspection that supplied it and the filing
    that records it.

  * **the two prose artifacts.** `prompts/inspector.md` and
    `schemas/story-brief.schema.json` are read and searched, never eyeballed.

  * **the paths that still refuse.** `brief_filing.file_brief` and
    `brief_fetch.fetch` are driven against the same mirrored harness root with
    the workflow taken off the brief, and with it put back, so a refusal is the
    missing field deciding rather than the fixture.

Every absence asserted here carries a demonstration that it can fail:

  * "the body of a brief whose finding named its own workflow carries no
    supplied-workflow sentence" sits beside the supplied brief filed in the same
    inspection, whose body does carry it;
  * "a filed brief that named its own workflow is not marked" sits beside the
    supplied brief in the same report, which is;
  * "the fallback is spelled in exactly one file under `orchestration/` and
    `scripts/`" sits beside the identical scan over those files plus a planted
    one, which finds two;
  * "an inspection where every filed brief named its own workflow writes no line
    about supplying" sits beside the same run over a finding that named none,
    which writes one, on both narrow surfaces and in the broad report;
  * "a finding naming a workflow nothing defines is still dropped" sits beside
    the finding filed in the same inspection, so a module that had stopped
    filing anything could not pass.

Nothing here reaches a model. Broad inspections are handed a fake runner
explicitly; the whole-run cases install one in place of the autouse guard
imported from `tests/test_an_inspection_records_what_it_cost.py`, which fails
any test in this module that reaches the real one; and the completion filing
invokes no Inspector at all.
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

import brief_fetch
import brief_filing
import conftest
import harness_config
import inspection
import schema_validator

from test_inspection import (
    BRIEF_REQUIRED,
    MALFORMED,
    MIRROR_WORKFLOW,
    MIRROR_WORKFLOWS,
    OTHER_MIRROR_WORKFLOW,
    UNDEFINED_WORKFLOW,
    UNKNOWN_WORKFLOW,
    brief,
    configuration,
    harness_mirror,
    inspecting,
    target_repository,
    writes,
)
from test_an_inspection_records_what_it_cost import (  # noqa: F401 - shared
    harness,                                          # idioms and fixtures
    finding as cost_finding,
    no_model,
    run,
)
from test_a_brief_names_its_area import (  # noqa: F401 - the same two narrow
    a_target,                              # surfaces, read the same way
    narrow_run_with,
    inspection_lines,
    notes_in,
    summary_of,
)
from test_the_post_story_inspection_reports_its_other_outcomes import (
    PRE_STAGE_WORKFLOW,
    SIBLING_FINDING,
    held_record,
    pre_stage_harness,  # noqa: F401 - a fixture this module's cases request
    pre_stage_run,
)
from test_brief_fetch import (
    COMMAND_KEY,
    ONE_KEY,
    with_a_brief,
)

REPO_ROOT = Path(conftest.HARNESS_ROOT)

#: The field the supply is about. Spelled here because the whole claim of this
#: module is that *this* field, and no other, has a value supplied for it; it is
#: asserted below to be one of the fields the shipped schema requires, so a
#: rename is a failing assertion rather than a test that quietly stops asking.
WORKFLOW = "workflow"

#: What the module records on a finding it supplied into, and the sentence it
#: appends, read off the module rather than spelled here.
SUPPLIED = inspection.WORKFLOW_SUPPLIED
SENTENCE = inspection.SUPPLIED_WORKFLOW_SENTENCE

#: The two names the mirrored harness root defines, in the two roles this
#: module needs them in: one is what the target configures and is therefore what
#: a supply must produce, the other is what the finding beside it names. They
#: are different names, which is what makes a supplied value traceable to the
#: configuration rather than to whichever definition the mirror happened to
#: sort first — and `test_the_fixture_names_two_different_workflows` says so
#: before anything rests on it.
CONFIGURED = OTHER_MIRROR_WORKFLOW
NAMED_BY_THE_FINDING = MIRROR_WORKFLOW

#: The slugs the two findings of most cases carry, so a queue entry, a filed
#: record and a printed line can each be traced to the finding that produced it.
WITHOUT, WITH = "named-no-workflow", "named-its-own-workflow"

BRIEF_SCHEMA = schema_validator.load_schema(inspection.BRIEF_SCHEMA)
INSPECTOR_PROMPT_TEXT = (REPO_ROOT / "prompts"
                         / inspection.INSPECTOR_PROMPT).read_text(
    encoding="utf-8")

#: The required fields other than the one a value is supplied for. Every case
#: about "anything else is still dropped" is driven over this set rather than
#: over one member of it, so a field added to the shape is covered without this
#: module being edited.
OTHER_REQUIRED = [name for name in sorted(BRIEF_REQUIRED) if name != WORKFLOW]


def flattened(text: str) -> str:
    """One text with its wrapping and its markdown emphasis taken out.

    A prompt and a schema description are wrapped for a reader and emphasised
    for one, so a sentence in either is neither a line nor a plain string.
    Searching the flattened, unemphasised, lowered text asks about the claim
    rather than about where the line broke.
    """
    return " ".join(text.replace("*", "").replace("`", "").split()).lower()


# ==========================================================================
# The fixture, shown discriminating before anything rests on it
# ==========================================================================


def test_the_fixture_names_two_different_workflows_the_harness_defines():
    """What every "the supplied value is the configured one" claim below rests
    on: the name the target configures and the name the finding beside it
    carries are two different names, and the mirrored harness root defines both
    — so a brief filed under the configured name was filled in from the
    configuration and could not have arrived there by carrying the other."""
    assert CONFIGURED != NAMED_BY_THE_FINDING
    assert CONFIGURED in MIRROR_WORKFLOWS
    assert NAMED_BY_THE_FINDING in MIRROR_WORKFLOWS
    assert UNDEFINED_WORKFLOW not in MIRROR_WORKFLOWS
    assert WORKFLOW in BRIEF_REQUIRED


# ==========================================================================
# Driving one broad inspection against the mirror
# ==========================================================================


def without_workflow(**overrides) -> dict:
    """One conforming finding with its workflow taken off.

    Taken off the shared fixture rather than written out here, so the finding
    that reaches the supply differs from the one beside it in exactly the field
    this module is about.
    """
    finding = brief(**overrides)
    finding.pop(WORKFLOW)
    return finding


def inspected(tmp_path: Path, *findings: dict, configured: str = CONFIGURED,
              name: str = "supplied-workflow-target"):
    """One whole broad inspection of a target configuring `configured`."""
    return inspecting(tmp_path, act=writes(*findings),
                      config=configuration(**{WORKFLOW: configured}),
                      name=name)


def queued(found) -> dict:
    """Every entry the inspection's target holds, by the slug it was filed for."""
    return {entry["payload"]["slug"]: entry for entry in found.entries}


def records(found) -> dict:
    """What the report says was filed, by slug."""
    return {one.slug: one for one in found.report.filed}


# ==========================================================================
# 1. What is filed, and under which workflow
# ==========================================================================


def test_a_finding_complete_but_for_its_workflow_is_filed_under_the_configured_one(
        tmp_path):
    """The behaviour this module exists for, read off the queue rather than off
    the report: the finding that named no workflow reaches a brief, and the
    brief names what the target's configuration declares.

    The finding beside it names the harness's *other* definition and is filed
    carrying that one, so the value supplied is the configuration's and not
    "whatever the other brief said" — and a run that had stopped filing
    anything could not pass either half.
    """
    found = inspected(tmp_path, without_workflow(slug=WITHOUT),
                      brief(slug=WITH, workflow=NAMED_BY_THE_FINDING))

    assert sorted(found.filed_slugs) == sorted([WITHOUT, WITH])
    assert found.dropped(MALFORMED) == ()
    assert found.dropped(UNKNOWN_WORKFLOW) == ()

    entries = queued(found)
    assert entries[WITHOUT]["payload"][WORKFLOW] == CONFIGURED
    assert entries[WITH]["payload"][WORKFLOW] == NAMED_BY_THE_FINDING


def test_the_supplied_brief_says_in_its_body_that_the_workflow_was_supplied(
        tmp_path):
    """The mark where the person planning the brief meets it — on the board,
    in the brief's own text — with the brief beside it as the control.

    The sentence is appended, so the Inspector's own account of the defect is
    what a reader reads first and is still there whole. The brief whose finding
    named its own workflow carries a body identical to what the invocation
    wrote: without that half, "the sentence is on the supplied brief" would pass
    just as happily against a path that appended it to everything.
    """
    written = without_workflow(slug=WITHOUT)
    named = brief(slug=WITH, workflow=NAMED_BY_THE_FINDING)
    found = inspected(tmp_path, written, named)

    entries = queued(found)
    supplied_body = entries[WITHOUT]["payload"]["body"]
    assert supplied_body.startswith(written["body"])
    assert SENTENCE.format(workflow=CONFIGURED) in supplied_body
    assert CONFIGURED in supplied_body

    assert entries[WITH]["payload"]["body"] == named["body"]
    assert SENTENCE.format(workflow=CONFIGURED) not in \
        entries[WITH]["payload"]["body"]


def test_the_filed_record_says_which_brief_had_its_workflow_supplied(tmp_path):
    """The second of the three places the mark is visible: the record of what
    was filed, and the report property both narrow surfaces and the broad one
    print from.

    The brief that named its own workflow is required to be unmarked in the
    same report, so the mark is that finding's own fact rather than something
    every filing carries.
    """
    found = inspected(tmp_path, without_workflow(slug=WITHOUT),
                      brief(slug=WITH, workflow=NAMED_BY_THE_FINDING))

    filed = records(found)
    assert filed[WITHOUT].workflow_supplied is True
    assert filed[WITH].workflow_supplied is False
    assert found.report.supplied_workflows == (filed[WITHOUT],)


def test_supplying_a_workflow_changes_the_key_a_brief_is_filed_under_by_nothing(
        tmp_path):
    """Two inspections of two targets differing in one thing: whether the
    finding named the workflow the target configures or named none at all. What
    reaches the queue is filed under the same key and the same identity, so a
    brief filed before this behaviour existed does not refile under a different
    key now.

    Asserted end to end rather than on `story_brief.identity` alone, because
    what must not move is the key the queue files under, and that is derived
    from the identity by the outbox.
    """
    supplied = inspected(tmp_path, without_workflow(slug=WITHOUT),
                         name="supplied-identity")
    named = inspected(tmp_path, brief(slug=WITHOUT, workflow=CONFIGURED),
                      name="named-identity")

    assert supplied.filed_slugs == named.filed_slugs == [WITHOUT]
    one, other = supplied.entries[0], named.entries[0]
    assert one["identity"] == other["identity"]
    assert one["key"] == other["key"]
    assert records(supplied)[WITHOUT].key == records(named)[WITHOUT].key


# ==========================================================================
# 2. What is still refused
# ==========================================================================


def test_a_finding_naming_a_workflow_the_harness_does_not_define_is_still_dropped(
        tmp_path):
    """A wrong answer rather than a missing one, so nothing is supplied over
    it: the drop keeps its reason and its detail keeps naming the workflows the
    harness does define.

    The finding beside it named no workflow at all and is filed in the same
    inspection, which is what makes this the *name* being refused rather than
    the module having stopped filing.
    """
    found = inspected(tmp_path, without_workflow(slug=WITHOUT),
                      brief(slug="names-nothing-defined",
                            workflow=UNDEFINED_WORKFLOW))

    assert found.filed_slugs == [WITHOUT]
    detail = found.detail(UNKNOWN_WORKFLOW)
    assert UNDEFINED_WORKFLOW in detail, detail
    for defined in MIRROR_WORKFLOWS:
        assert defined in detail, detail


def test_a_finding_carrying_an_empty_workflow_is_refused_rather_than_supplied_for(
        tmp_path):
    """The trigger is the field being absent and nothing else.

    An empty name is a value the Inspector wrote, so it goes to the drop a name
    nothing defines already went to rather than being filled in. The finding
    that carries no such field at all is filed in the same inspection, which is
    the whole of the difference between the two.
    """
    found = inspected(tmp_path, without_workflow(slug=WITHOUT),
                      brief(slug="carries-an-empty-name", workflow=""))

    assert found.filed_slugs == [WITHOUT]
    assert len(found.dropped(UNKNOWN_WORKFLOW)) == 1
    assert queued(found)[WITHOUT]["payload"][WORKFLOW] == CONFIGURED


def test_a_target_configuring_a_workflow_the_harness_does_not_define_is_not_rescued(
        tmp_path):
    """The supplied value goes through the same check every named value goes
    through, so a target whose configuration names a workflow nothing defines
    has its findings refused there rather than filed under a name no plan can
    be run from.

    The control is the identical finding inspected under a configuration naming
    a defined workflow, which is filed — so what decides is the configured name
    and not the finding.
    """
    refused = inspected(tmp_path, without_workflow(slug=WITHOUT),
                        configured=UNDEFINED_WORKFLOW, name="configures-nothing")

    assert refused.filed_slugs == []
    assert refused.entries == []
    detail = refused.detail(UNKNOWN_WORKFLOW)
    assert UNDEFINED_WORKFLOW in detail, detail

    filed = inspected(tmp_path, without_workflow(slug=WITHOUT),
                      name="configures-a-definition")
    assert filed.filed_slugs == [WITHOUT]


@pytest.mark.parametrize("missing", OTHER_REQUIRED)
def test_a_finding_missing_any_other_required_field_is_still_dropped_as_malformed(
        missing, tmp_path):
    """Nothing else the shape requires becomes optional.

    Driven over every required field other than the one a value is supplied
    for, because a supply that had leaked into a second field would pass a
    sampled assertion. The finding that named no workflow is filed beside each
    of them, so the drop is that field and not the run.
    """
    broken = {key: value for key, value in brief(slug="missing-a-field").items()
              if key != missing}
    found = inspected(tmp_path, without_workflow(slug=WITHOUT), broken,
                      name=f"target-missing-{missing}")

    assert found.filed_slugs == [WITHOUT]
    assert missing in found.detail(MALFORMED), found.detail(MALFORMED)
    assert len(found.entries) == 1


@pytest.mark.parametrize("missing", OTHER_REQUIRED)
def test_a_finding_missing_its_workflow_and_one_other_field_is_dropped_as_malformed(
        missing, tmp_path):
    """A finding missing the workflow *and* something else is a genuinely
    incomplete finding, and is dropped as malformed rather than filed.

    The supply runs before the shape is checked, so the value it fills in is
    not what saves this finding: the field beside it is still missing, the drop
    still names that field, and nothing reaches the queue.
    """
    broken = {key: value
              for key, value in without_workflow(slug="missing-two").items()
              if key != missing}
    found = inspected(tmp_path, without_workflow(slug=WITHOUT), broken,
                      name=f"target-missing-{missing}-and-the-workflow")

    assert found.filed_slugs == [WITHOUT]
    assert missing in found.detail(MALFORMED), found.detail(MALFORMED)
    assert len(found.entries) == 1


# ==========================================================================
# 3. The supply itself
# ==========================================================================


def test_a_finding_that_named_its_workflow_comes_back_from_the_supply_untouched():
    """What the supply does to a finding it has nothing to say about: nothing.

    Three shapes it must leave exactly as they are — a finding naming a defined
    workflow, one naming an empty string, and one that is not an object at all
    — because each is a value the Inspector wrote or a shape the validation
    below refuses, and changing any of them would be this function deciding
    something it is not the place to decide.
    """
    named = brief(workflow=NAMED_BY_THE_FINDING)
    assert inspection.with_supplied_workflow(named, CONFIGURED) is named

    empty = brief(workflow="")
    assert inspection.with_supplied_workflow(empty, CONFIGURED) is empty

    for shape in ("a defect", None, 17):
        assert inspection.with_supplied_workflow(shape, CONFIGURED) is shape


def test_the_supply_leaves_what_the_invocation_wrote_as_it_wrote_it():
    """A new finding rather than an edit of the one it was handed, so what the
    invocation wrote stays readable beside what was supplied into it."""
    written = without_workflow()
    supplied = inspection.with_supplied_workflow(written, CONFIGURED)

    assert WORKFLOW not in written
    assert SUPPLIED not in written
    assert supplied is not written
    assert supplied[WORKFLOW] == CONFIGURED
    assert supplied[SUPPLIED] is True
    assert supplied["body"].startswith(written["body"])
    assert SENTENCE.format(workflow=CONFIGURED) in supplied["body"]
    assert schema_validator.validate(supplied, BRIEF_SCHEMA) == []


# ==========================================================================
# 4. The value, and the one place its fallback is spelled
# ==========================================================================


#: Where the fallback may be spelled, and where a second spelling of it would
#: be a second answer that can disagree with the first.
SCANNED_DIRECTORIES = ("orchestration", "scripts")

FALLBACK = harness_config.configured_workflow({})


def sources_under(directories) -> list[Path]:
    """Every file the named directories of this repository hold."""
    found = []
    for directory in directories:
        for path in sorted((REPO_ROOT / directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                found.append(path)
    return found


def spellings_of(text: str, paths) -> list[str]:
    """Where `text` occurs in `paths`, as one entry per line carrying it."""
    found = []
    for path in paths:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for number, line in enumerate(lines, start=1):
            if text in line:
                found.append(f"{path}:{number}")
    return found


def test_the_configured_workflow_is_what_the_target_declares_or_the_fallback():
    """The derivation itself, driven directly: a configuration naming a
    workflow reports that one, and a configuration naming none reports the
    fallback — which is required to be a workflow this repository actually
    ships, so the value a target gets when it configures nothing is one a run
    can be executed under."""
    assert harness_config.configured_workflow(
        {WORKFLOW: NAMED_BY_THE_FINDING}) == NAMED_BY_THE_FINDING
    assert harness_config.configured_workflow({}) == FALLBACK
    assert FALLBACK in harness_config.workflow_names(REPO_ROOT)


def test_the_fallback_is_spelled_in_exactly_one_file_the_harness_ships(tmp_path):
    """One fact with more than one reader, spelled once.

    A run resolves the configured workflow as the last of the three places one
    can be named, and an inspection supplies it into a finding that named none;
    two spellings of that fallback are two answers that can disagree, and the
    disagreement would be invisible. The subject here is what this repository
    ships, so the shipped tree is what is read.

    The control is the identical scan over the same files plus one this module
    plants, which finds both — so the single occurrence is the spelling being
    single rather than the search having stopped seeing anything.
    """
    shipped = sources_under(SCANNED_DIRECTORIES)
    found = spellings_of(FALLBACK, shipped)

    assert len(found) == 1, found
    assert Path(found[0].rsplit(":", 1)[0]).resolve() == \
        Path(harness_config.__file__).resolve(), found

    planted = tmp_path / "a-second-spelling.py"
    planted.write_text(f'WORKFLOW = "{FALLBACK}"\n', encoding="utf-8")
    controlled = spellings_of(FALLBACK, shipped + [planted])
    assert len(controlled) == 2, controlled
    assert any(str(planted) in one for one in controlled), controlled


# ==========================================================================
# 5. The broad report, read through scripts/l5-inspect itself
# ==========================================================================


def a_filed(slug: str, *, supplied: bool = False) -> inspection.Filed:
    """One filing, as a report carries it."""
    return inspection.Filed(key=f"key-{slug}", slug=slug,
                            title=f"something to do about {slug}",
                            severity=2, scope="src/",
                            workflow_supplied=supplied)


def printed(report: inspection.Report) -> tuple[str, int]:
    """What `l5-inspect` prints for a report, and what it returns.

    The report is constructed rather than inspected out of a target: what is
    under test is the words that script prints for a brief whose workflow was
    supplied, and a report is the input it prints them from.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = conftest.load_script("l5-inspect").report(report, report.dry_run)
    return buffer.getvalue(), code


def test_the_broad_report_gives_a_supplied_brief_its_own_lines():
    """The line a developer running an inspection by hand reads.

    The two reports differ in one field of one filing — whether that brief's
    workflow was supplied — so the lines that appear in one and not the other
    are that field deciding. The brief that named its own workflow must not be
    among them, and the command still exits zero, because the findings were
    filed.
    """
    supplied, code = printed(inspection.Report(
        filed=(a_filed(WITHOUT, supplied=True), a_filed(WITH))))
    neither, control_code = printed(inspection.Report(
        filed=(a_filed(WITHOUT), a_filed(WITH))))

    assert code == control_code == 0

    before = neither.splitlines()
    added = [line for line in supplied.splitlines() if line not in before]
    assert added, f"supplied:\n{supplied}\nneither:\n{neither}"
    assert any(WITHOUT in line for line in added), added
    assert not any(WITH in line for line in added), added


def test_the_broad_report_of_an_inspection_where_none_was_supplied_says_nothing():
    """The same claim counted rather than diffed, so "nothing is said" is a
    fact about the report of a brief that was filed.

    Both briefs are named the same number of times in the report where neither
    workflow was supplied — each is in the filed list and nowhere else — and
    the brief whose workflow was supplied is named once more than that, while
    the brief beside it is named no more often than it was. A surface that had
    stopped printing the filed list would fail the first half.
    """
    supplied, _ = printed(inspection.Report(
        filed=(a_filed(WITHOUT, supplied=True), a_filed(WITH))))
    neither, code = printed(inspection.Report(
        filed=(a_filed(WITHOUT), a_filed(WITH))))

    def named(text: str, slug: str) -> int:
        return len([line for line in text.splitlines() if slug in line])

    assert code == 0
    assert named(neither, WITHOUT) == named(neither, WITH) > 0, neither
    assert named(supplied, WITHOUT) == named(neither, WITHOUT) + 1, supplied
    assert named(supplied, WITH) == named(neither, WITH), supplied


# ==========================================================================
# 6. The post-story report, read out of a whole run's events.log
# ==========================================================================


def unnamed_finding(ordinal: int = 1, **overrides) -> dict:
    """One finding of the narrow fixture with its workflow taken off."""
    found = cost_finding(ordinal, **overrides)
    found.pop(WORKFLOW)
    return found


def lines_about(target: Path, slug: str) -> list[str]:
    """Every line of this run's inspection report that names one brief and is
    not the summary."""
    summary = summary_of(target)
    return [line for line in inspection_lines(target)
            if slug in line and line != summary]


def test_the_post_story_report_gives_a_supplied_brief_a_line_before_the_summary(
        tmp_path, harness, monkeypatch):
    """The same thing the broad report says, said where a run records it.

    The claims are that the line is its own line, that it names the brief, that
    it says the pick was not the Inspector's and may be changed, and that it is
    said *before* the summary — because a trailing clause on a summary is what a
    failed dedupe had, and it stayed true for thirty stories without being read.
    """
    target = a_target(tmp_path, "one-brief-with-a-supplied-workflow")
    code, inspector = narrow_run_with(target, harness, monkeypatch,
                                      findings=[unnamed_finding(1)])

    assert code == 0
    assert inspector.invocations, "the inspection was never attempted"

    said = lines_about(target, unnamed_finding(1)["slug"])
    assert len(said) == 1, inspection_lines(target)
    assert "supplied" in said[0], said[0]
    assert "may be changed" in said[0], said[0]

    ordered = inspection_lines(target)
    assert ordered.index(said[0]) < ordered.index(summary_of(target)), ordered


def test_a_post_story_inspection_whose_findings_named_their_workflow_says_nothing(
        tmp_path, harness, monkeypatch):
    """The control for the absence above.

    The same fixture, the same run, the same query that answers — and one
    finding that named its own workflow. Without it, "a line of its own" would
    be satisfied by a run that says nothing at all, and could not tell a finding
    that named a workflow from a reader looking in the wrong file. The summary
    is required to still carry its count, so what changed is the extra line and
    not the report.
    """
    target = a_target(tmp_path, "every-finding-named-its-workflow")
    code, inspector = narrow_run_with(target, harness, monkeypatch,
                                      findings=[cost_finding(1)])

    assert code == 0
    assert inspector.invocations, "the inspection was never attempted"
    assert notes_in(target) == [], inspection_lines(target)
    assert "1 finding(s)" in summary_of(target)


# ==========================================================================
# 7. The completion filing, where the mark has travelled through a file
# ==========================================================================


#: What the pre-stage fixture's Inspector reports: a defect beside the story's
#: own change, which is what that mode holds for the run's completion rather
#: than routing to a stage.
BESIDE_THE_CHANGE = {key: value for key, value in SIBLING_FINDING.items()
                     if key != WORKFLOW}


def supplied_lines(run) -> list[str]:
    return [line for line in run.messages
            if "the workflow was supplied for" in line]


def test_a_finding_held_for_a_runs_completion_is_filed_with_its_workflow_supplied(
        tmp_path, pre_stage_harness):
    """The third report surface, and the one that shows the mark surviving a
    file.

    A pre-stage inspection files nothing while the run is going: what it finds
    beside the story's own change is written into the run directory and filed
    when the run completes, hours and several stages later. So the workflow
    supplied here, and the fact that it was supplied, travel through that
    artifact — which is why the mark is on the finding rather than beside it,
    and the held record is read to show that it is. The brief that comes out
    names the workflow this target configures, its body carries the sentence,
    and the completion filing writes one line about it.
    """
    run = pre_stage_run(tmp_path, pre_stage_harness, "held-without-a-workflow",
                        findings=[BESIDE_THE_CHANGE])

    assert run.code == 0
    assert run.inspector.invocations, "the inspection was never attempted"

    held = held_record(run)["findings"]
    assert len(held) == 1, held
    assert held[0][WORKFLOW] == PRE_STAGE_WORKFLOW["name"]
    assert held[0][SUPPLIED] is True

    assert len(run.queue) == 1, run.queue
    payload = run.queue[0]["payload"]
    assert payload[WORKFLOW] == PRE_STAGE_WORKFLOW["name"]
    assert SENTENCE.format(workflow=PRE_STAGE_WORKFLOW["name"]) in \
        payload["body"]

    lines = supplied_lines(run)
    assert len(lines) == 1, run.messages
    assert BESIDE_THE_CHANGE["slug"] in lines[0], lines[0]


def test_a_completion_filing_whose_finding_named_its_workflow_says_nothing(
        tmp_path, pre_stage_harness):
    """The control for that absence: the same run of the same fixture over the
    finding as the Inspector wrote it, workflow and all.

    It is held exactly as it was written, it is filed, and no line is said — so
    the silence is the finding having named a workflow rather than nothing
    having been held or filed.
    """
    run = pre_stage_run(tmp_path, pre_stage_harness, "held-with-a-workflow",
                        findings=[SIBLING_FINDING])

    assert run.code == 0
    assert held_record(run)["findings"] == [SIBLING_FINDING]
    assert len(run.queue) == 1, run.queue
    assert run.queue[0]["payload"][WORKFLOW] == SIBLING_FINDING[WORKFLOW]
    assert run.queue[0]["payload"]["body"] == SIBLING_FINDING["body"]
    assert supplied_lines(run) == [], run.messages


# ==========================================================================
# 8. What the two documents say
# ==========================================================================


#: What the Inspector is told, one claim per entry: that omitting the field
#: costs it nothing, that what is filled in is marked as supplied rather than
#: chosen, and that a name the harness does not define is still refused with the
#: whole finding.
PROMPT_CLAIMS = (
    "omits this field is not thrown away",
    "supplied rather than chosen",
    "a name none of the definitions above carries is refused",
    "dropped with it",
)


@pytest.mark.parametrize("stated", PROMPT_CLAIMS)
def test_the_inspector_prompt_states_what_becomes_of_an_omitted_workflow(stated):
    """The producer is not told something untrue about what becomes of its
    output: a finding that omits the field has one supplied and marked, and a
    name nothing defines is still refused."""
    assert stated in flattened(INSPECTOR_PROMPT_TEXT), stated


#: What the shape itself records, one claim per entry: which path supplies an
#: absent value, which paths refuse one, and why the property stays required
#: even so.
SCHEMA_CLAIMS = (
    "supplied on one path and refused on the others",
    "marked as supplied rather than chosen",
    "files by hand",
    "fetched by key for planning",
    "refused where they name none",
)


@pytest.mark.parametrize("stated", SCHEMA_CLAIMS)
def test_the_workflow_property_records_which_paths_supply_and_which_refuse(
        stated):
    """The shipped schema and the legitimate subject: a reader of the shape
    learns from it that an absent value means different things on different
    paths."""
    said = flattened(BRIEF_SCHEMA["properties"][WORKFLOW]["description"])
    assert stated in said, said


def test_the_workflow_property_is_still_required_and_still_a_plain_string():
    """No field of the shape became optional: the supply happens before the
    shape is checked, so loosening it would let a genuinely incomplete brief
    through every path rather than one.

    The control for "required" is the same validator against the same shipped
    schema with the field taken off a conforming brief, which reports it.
    """
    assert WORKFLOW in BRIEF_SCHEMA["required"]
    assert BRIEF_SCHEMA["properties"][WORKFLOW]["type"] == "string"

    problems = schema_validator.validate(without_workflow(), BRIEF_SCHEMA)
    assert problems, "the shipped schema accepts a brief naming no workflow"
    assert any(f"$.{WORKFLOW}" in problem for problem in problems), problems


# ==========================================================================
# 9. The two paths where a person is present to be asked
# ==========================================================================


@pytest.fixture(scope="module")
def mirror(tmp_path_factory) -> Path:
    """The mirrored harness root, for the two paths driven below."""
    return harness_mirror(tmp_path_factory.mktemp("refusing-mirror"))


def test_a_brief_a_developer_files_by_hand_with_no_workflow_is_still_refused(
        tmp_path, mirror):
    """A person is present to be asked, so an absent workflow is refused there
    rather than guessed at.

    The same brief with its workflow put back is filed in the same fixture,
    which is what makes the refusal the missing field deciding.
    """
    target = target_repository(tmp_path, name="files-by-hand")

    refused = brief_filing.file_brief(without_workflow(), {}, target, mirror)
    assert not refused.filed
    assert WORKFLOW in refused.detail, refused.detail

    filed = brief_filing.file_brief(brief(workflow=NAMED_BY_THE_FINDING), {},
                                    target, mirror)
    assert filed.filed, filed.detail


def test_a_brief_fetched_for_planning_with_no_workflow_is_still_refused(
        tmp_path, mirror):
    """A fetched brief is a plan-time input read with the developer in the
    session, so the refusal stands there too.

    The same command answering with the same brief carrying a workflow is
    fetched, so what refuses is the missing field and not the fixture command.
    """
    without = brief_fetch.fetch(
        ONE_KEY,
        {COMMAND_KEY: str(with_a_brief(tmp_path / "without",
                                       without_workflow()))},
        tmp_path, mirror)
    assert without.brief is None
    assert without.fetched is False
    assert WORKFLOW in without.reason, without.reason

    fetched = brief_fetch.fetch(
        ONE_KEY,
        {COMMAND_KEY: str(with_a_brief(tmp_path / "with",
                                       brief(workflow=NAMED_BY_THE_FINDING)))},
        tmp_path, mirror)
    assert fetched.fetched, fetched.reason
    assert fetched.brief[WORKFLOW] == NAMED_BY_THE_FINDING
