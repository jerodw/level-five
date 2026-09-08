"""Independent validation for the severity floor: what an inspection files,
and what it says about what it did not file.

Written from the story's acceptance criteria rather than from the
implementation. The subjects are kept apart deliberately:

  * **what the floor files.** Driven through a whole inspection against a fake
    runner, so what is asserted is the queue the inspection left and the report
    it returned rather than a partition computed here. The three settings the
    story names — the key unset, the key at the lowest severity the scale
    defines, and the key at the highest — are three inspections over the same
    three findings, so what separates their answers is the configured floor and
    nothing else.

  * **the ordering against the cap.** Asserted twice: once where the cap was
    never reached, so a finding the floor excluded cannot have been crowded
    out, and once where the cap did exclude something, so the two exclusions
    are shown to be reported apart and to name different findings.

  * **the refusal.** Read at `inspection.bounds`, which is where a caller that
    did not refuse is told, and again through `scripts/l5-inspect` itself,
    where the refusal has to happen before an invocation is made and before
    anything is enqueued.

  * **what the invocation is told.** Read off the rendered prompt. The floor
    block is read through a template this module wrote, so what is asserted is
    that the render supplies the value rather than what the shipped wording
    happens to be today; the shipped template is then required to carry the
    placeholder and to render with nothing left unfilled.

  * **the post-story producer.** Driven through a whole run, so that the floor
    it applies is the one its own `bounds` call resolved from the target's
    configuration rather than one this module handed it.

Every absence asserted here carries a demonstration that it can fail:

  * "a floor of one names no floor drop" and "nothing below the floor was
    filed" sit beside the same reads of the same report under a floor that does
    exclude, so an empty drop list is about the floor rather than about a read
    that has stopped seeing;
  * "the floor's exclusion is never reported under the cap's reason" sits
    beside the same report's cap list carrying a different finding;
  * "the refused floor invoked nothing and enqueued nothing" sits beside the
    same two reads of a target where a log directory and a queue entry were
    planted, which they do report;
  * "the block at the lowest severity names no severity numeral" sits beside
    the same reading of the block at a real floor, which does name one;
  * "the shipped render carries the floor rather than an unfilled field" sits
    beside the literal this harness's render substitutes for a field nothing
    supplied, which the rendered block is required to differ from;
  * "no second place builds the floor's drop" sits beside a planted source
    where a second function does, which the same scan reports;
  * "the run the floor emptied still completed" sits beside the same run under
    no floor, which files what it dropped.

Nothing here invokes a model: every inspection below goes through a fake
runner, and every run below goes through a fake agent runner installed in its
place.
"""
from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest

import harness_config
import inspection
import outbox
import story_inspection

import test_inspection as producer
import test_a_completed_story_is_inspected as after_story
from test_a_completed_story_is_inspected import harness  # noqa: F401

REPO_ROOT = producer.REPO_ROOT
SCRIPTS = producer.SCRIPTS

#: The key, the reason and the default, read off the module that owns them so
#: this module spells none of them beside the definition that decides them.
MIN_SEVERITY_KEY = inspection.MIN_SEVERITY_KEY
BENEATH_THE_FLOOR = inspection.BENEATH_THE_FLOOR
DEFAULT_MIN_SEVERITY = inspection.DEFAULT_MIN_SEVERITY

#: The severities the scale defines, read off the shipped brief schema through
#: the module that loads it: the floor names a severity the scale defines, and
#: a fixture that spelled its own numbers would be asserting about a scale this
#: harness does not hold anyone to.
SEVERITIES = producer.SEVERITIES
LOWEST, MIDDLE, HIGHEST = producer.LOWEST, producer.MIDDLE, producer.HIGHEST

#: Every other way this module's producers name a drop, so "the floor's reason
#: is distinct from every existing one" is a comparison against the module's
#: own set rather than against a list written here.
OTHER_REASONS = (
    inspection.ALREADY_FILED,
    inspection.ALREADY_FILED_LOCALLY,
    inspection.ALREADY_QUEUED,
    inspection.MALFORMED,
    inspection.UNKNOWN_WORKFLOW,
    inspection.PAST_THE_CAP,
    inspection.LOST_BY_THE_QUEUE,
    inspection.NO_ARTIFACT,
)

#: One finding at each severity the scale defines, written in an order that
#: makes "written first" and "lowest severity" different answers.
LOW = "zzz-the-lowest-thing-found"
MIDDLING = "zzz-the-middling-thing-found"
HIGH = "zzz-the-highest-thing-found"


def three_severities() -> tuple[dict, ...]:
    """One conforming finding at each severity, distinguishable by slug."""
    return (
        producer.brief(slug=MIDDLING, severity=MIDDLE),
        producer.brief(slug=LOW, severity=LOWEST),
        producer.brief(slug=HIGH, severity=HIGHEST,
                       confidence=producer.CONFIDENCES[-1]),
    )


def inspecting_at(tmp_path: Path, floor: int | None, *, name: str,
                  findings=None, **overrides) -> producer.Inspected:
    """One whole inspection of a target this module owns, at one floor.

    `floor` of `None` leaves the key undeclared, which is the setting the first
    acceptance criterion is about: what a target that configured nothing files.
    """
    departures = dict(overrides)
    if floor is not None:
        departures[MIN_SEVERITY_KEY] = str(floor)
    return producer.inspecting(
        tmp_path, name=name,
        act=producer.writes(*(three_severities() if findings is None
                              else findings)),
        config=producer.configuration(**departures))


def slugs_beneath(found: producer.Inspected) -> list[str]:
    """The slug of every finding the floor excluded, taken from the drops the
    report carries rather than from what was configured."""
    return [drop.detail.split(":", 1)[0]
            for drop in found.dropped(BENEATH_THE_FLOOR)]


def queued_slugs(found: producer.Inspected) -> list[str]:
    """What actually reached the queue, read off the entries themselves."""
    return sorted(entry["payload"]["slug"] for entry in found.entries)


# ==========================================================================
# The three settings, over one set of findings
# ==========================================================================


def test_an_unconfigured_floor_files_above_it_and_drops_what_is_beneath(
        tmp_path):
    """The first criterion: with the key unset, the two higher severities are
    filed and the lowest is not.

    Observed at the queue as well as at the report, so what is asserted is the
    filing rather than a partition the report happened to describe. The control
    for the absence — that the lowest was not filed — is the same two reads in
    the same test reporting the other two, so an empty answer would have to be
    empty of everything.
    """
    found = inspecting_at(tmp_path, None, name="unset-floor")

    assert sorted(found.filed_slugs) == sorted([HIGH, MIDDLING])
    assert queued_slugs(found) == sorted([HIGH, MIDDLING])
    assert LOW not in found.filed_slugs
    assert LOW not in queued_slugs(found)
    assert slugs_beneath(found) == [LOW]


def test_the_finding_the_floor_excluded_is_named_with_its_own_severity(
        tmp_path):
    """The second criterion: a reader is told which bound excluded it.

    The reason is required to be one no other way of dropping a finding
    carries, so a reader meeting it in the report cannot mistake it for a
    duplicate, a malformed brief or a finding crowded out by the cap.
    """
    found = inspecting_at(tmp_path, None, name="named-with-severity")

    assert BENEATH_THE_FLOOR not in OTHER_REASONS
    dropped = found.dropped(BENEATH_THE_FLOOR)
    assert len(dropped) == 1
    assert dropped[0].severity == LOWEST
    assert LOW in dropped[0].detail
    assert str(LOWEST) in dropped[0].detail
    # And it is that bound's answer alone: no other reason claims this finding.
    for reason in OTHER_REASONS:
        assert LOW not in found.detail(reason), reason


def test_a_floor_at_the_lowest_severity_files_all_three_and_names_no_drop(
        tmp_path):
    """The third criterion: configured at the lowest severity the scale
    defines, the floor excludes nothing.

    This is the negative control the two absences above need — the same reads
    of the same report over the same findings report an exclusion when the
    floor is in force and none when it is not, so an empty drop list is a fact
    about the setting rather than about a read that stopped seeing.
    """
    found = inspecting_at(tmp_path, LOWEST, name="no-floor")

    assert sorted(found.filed_slugs) == sorted([HIGH, LOW, MIDDLING])
    assert queued_slugs(found) == sorted([HIGH, LOW, MIDDLING])
    assert found.dropped(BENEATH_THE_FLOOR) == ()


def test_a_floor_at_the_highest_severity_files_only_that_one(tmp_path):
    """The fourth criterion: both of the other findings are named as excluded
    by the floor, each with its own severity."""
    found = inspecting_at(tmp_path, HIGHEST, name="highest-floor")

    assert found.filed_slugs == [HIGH]
    assert queued_slugs(found) == [HIGH]
    assert sorted(slugs_beneath(found)) == sorted([LOW, MIDDLING])
    assert sorted(drop.severity for drop in found.dropped(BENEATH_THE_FLOOR)) \
        == sorted([LOWEST, MIDDLE])


# ==========================================================================
# The floor is applied before the cap
# ==========================================================================


def test_a_finding_beneath_the_floor_is_not_reported_as_past_the_cap(tmp_path):
    """The fifth criterion, in the case that decides it: the cap was never
    reached and the floor still excluded a finding.

    The cap is set past what the invocation wrote, so nothing can have been
    crowded out; the finding beneath the floor is reported under the floor's
    reason and the cap's list is empty. The control for that emptiness is the
    test below, where the same read of the same list does carry a finding.
    """
    found = inspecting_at(tmp_path, None, name="cap-not-reached",
                          **{producer.MAX_FINDINGS_KEY: str(len(SEVERITIES) + 1)})

    assert slugs_beneath(found) == [LOW]
    assert found.dropped(inspection.PAST_THE_CAP) == ()
    assert LOW not in found.detail(inspection.PAST_THE_CAP)


def test_the_cap_spends_its_places_on_what_the_floor_left(tmp_path):
    """The same ordering where both bounds exclude something.

    Four findings, one beneath the floor and three above it, and a cap of two.
    A cap applied first would have spent a place on the finding beneath the
    floor and left one of the higher ones unexplained; here the floor takes
    the low one and the cap takes the lowest of what remains, and the two
    exclusions name different findings under different reasons.
    """
    second_middling = "zzz-the-other-middling-thing-found"
    findings = (*three_severities(),
                producer.brief(slug=second_middling, severity=MIDDLE))
    found = inspecting_at(tmp_path, None, name="cap-after-floor",
                          findings=findings,
                          **{producer.MAX_FINDINGS_KEY: "2"})

    assert slugs_beneath(found) == [LOW]
    past = [drop.detail.split(":", 1)[0]
            for drop in found.dropped(inspection.PAST_THE_CAP)]
    assert len(past) == 1
    assert past[0] in (MIDDLING, second_middling)
    assert LOW not in past
    assert HIGH in found.filed_slugs
    assert LOW not in found.filed_slugs


# ==========================================================================
# A dry run applies the same floor and enqueues nothing
# ==========================================================================


def test_a_dry_run_applies_the_floor_and_still_enqueues_nothing(tmp_path):
    """The sixth criterion, over one target: what the dry run says it would
    file is what the ordinary inspection then files, floor included.

    The queue is read before the dry run and after it, so "enqueued nothing"
    is a comparison rather than a claim, and the ordinary inspection that
    follows is the control for it — the same target, the same findings and the
    same floor do put entries there.
    """
    target = producer.target_repository(tmp_path, name="dry-and-ordinary")
    config = producer.configuration()

    before = producer.queue_listing(target)
    dry = producer.inspecting(tmp_path, target=target, config=config,
                              act=producer.writes(*three_severities()),
                              dry_run=True)

    assert producer.queue_listing(target) == before == []
    assert dry.report.dry_run is True
    assert sorted(dry.filed_slugs) == sorted([HIGH, MIDDLING])
    assert slugs_beneath(dry) == [LOW]

    ordinary = producer.inspecting(tmp_path, target=target, config=config,
                                   act=producer.writes(*three_severities()))
    assert sorted(ordinary.filed_slugs) == sorted(dry.filed_slugs)
    assert queued_slugs(ordinary) == sorted(dry.filed_slugs)
    assert slugs_beneath(ordinary) == slugs_beneath(dry)


# ==========================================================================
# The bound, and the refusal that precedes every invocation
# ==========================================================================


def test_the_floor_defaults_in_source_to_a_severity_the_scale_defines():
    """Asserted at the resolution rather than at the constant alone, so what an
    inspection with the key unset actually runs under is what is pinned. It is
    a severity the scale defines, and it is not the lowest — a default equal to
    the lowest would be the mechanism switched off."""
    assert DEFAULT_MIN_SEVERITY in SEVERITIES
    assert DEFAULT_MIN_SEVERITY > LOWEST

    bound, problem = inspection.bounds({})
    assert problem == ""
    assert bound.min_severity == DEFAULT_MIN_SEVERITY


@pytest.mark.parametrize("value", [
    pytest.param(str(LOWEST - 1), id="below-the-scale"),
    pytest.param(str(HIGHEST + 1), id="above-the-scale"),
    pytest.param("severe", id="not-an-integer"),
    pytest.param("", id="empty"),
])
def test_a_floor_that_is_not_a_severity_is_refused_naming_the_key_and_the_value(
        value):
    """Refused rather than defaulted: a bound that cannot be read is a bound
    the target did not declare, so no `Bounds` is returned at all and the
    default cannot be obeyed in its place."""
    bound, problem = inspection.bounds({MIN_SEVERITY_KEY: value})

    assert bound is None
    assert MIN_SEVERITY_KEY in problem
    assert repr(value) in problem


@pytest.mark.parametrize("value", [pytest.param(str(one), id=f"severity-{one}")
                                   for one in SEVERITIES])
def test_every_severity_the_scale_defines_is_a_floor_that_resolves(value):
    """The control for the refusals above: the same reader accepts each of the
    severities the scale does define, so a refusal is about the value rather
    than about a reader that refuses everything."""
    bound, problem = inspection.bounds({MIN_SEVERITY_KEY: value})

    assert problem == ""
    assert bound.min_severity == int(value)


def test_a_bad_floor_is_reported_together_with_the_other_bounds_and_not_instead():
    """The eighth criterion's middle clause: a target that got more than one
    bound wrong is told all of them.

    The control is in the same test: each key is also wrong on its own, and the
    problem then names that key alone — so the three names appearing together
    is a fact about the reporting rather than about a message that names every
    key whatever happened.
    """
    _, problem = inspection.bounds({
        MIN_SEVERITY_KEY: "severe",
        producer.MAX_FINDINGS_KEY: "several",
        producer.MAX_COST_KEY: "cheap",
    })
    for key in (MIN_SEVERITY_KEY, producer.MAX_FINDINGS_KEY,
                producer.MAX_COST_KEY):
        assert key in problem, key

    _, alone = inspection.bounds({MIN_SEVERITY_KEY: "severe"})
    assert MIN_SEVERITY_KEY in alone
    assert producer.MAX_FINDINGS_KEY not in alone
    assert producer.MAX_COST_KEY not in alone


def test_an_inspection_given_a_floor_it_cannot_read_invokes_nothing(tmp_path):
    """The module's own half of the refusal, for a caller that did not refuse:
    no invocation is made, nothing is filed, and the unreadable key is named."""
    found = inspecting_at(tmp_path, None, name="unreadable-floor",
                          **{MIN_SEVERITY_KEY: "severe"})

    assert found.invocations == []
    assert found.report.filed == ()
    assert found.entries == []
    assert MIN_SEVERITY_KEY in found.detail(inspection.MALFORMED)


def test_the_script_refuses_a_floor_it_cannot_read_before_invoking_anything(
        tmp_path):
    """The rest of the eighth criterion, through the real entry point.

    That nothing was invoked and nothing was enqueued is observed rather than
    argued: the run leaves no logs directory, which is where an invocation's
    findings artifact and log are written, and no queue. Both absences are
    controlled by the same two reads of a second target where a logs directory
    and a queue entry were planted, which they do report.
    """
    value = "severe"
    target = producer.configured_target(
        tmp_path, f"{MIN_SEVERITY_KEY}: {value}\n", "refuses-the-floor")
    result = producer.run_script(target)

    assert result.returncode == 1, result.stdout
    assert MIN_SEVERITY_KEY in result.stderr
    assert value in result.stderr
    assert result.stdout == ""
    assert not (target / producer.LOGS_DIR).exists()
    assert producer.queue_listing(target) == []

    planted = producer.configured_target(
        tmp_path, f"{MIN_SEVERITY_KEY}: {value}\n", "planted-after-the-refusal")
    (planted / producer.LOGS_DIR).mkdir(parents=True)
    key = outbox.enqueue(outbox.queue_dir(planted), {"slug": LOW},
                         {"kind": "zzz-floor", "subject": LOW})
    assert key
    assert (planted / producer.LOGS_DIR).exists()
    assert producer.queue_listing(planted) != []


# ==========================================================================
# What the invocation is told
# ==========================================================================

#: The label the fixture template renders the floor under. Named here — in the
#: fixture — for the reason `producer.FIXTURE_PROMPT_FIELDS` is: an assertion
#: that the floor reached the prompt finds it by the label it derived from this
#: constant rather than from what the shipped template happens to say today.
#: That the shipped template carries the same placeholder is its own assertion
#: below.
FLOOR_FIELD = "severity_floor"

FIXTURE_FIELDS = (*producer.FIXTURE_PROMPT_FIELDS, FLOOR_FIELD)


def floor_prompt() -> str:
    """A template this module wrote, carrying the floor beside every field the
    shipped one is rendered with. Each label on its own line with its value on
    the line below it, so an assertion can find a value by its label."""
    lines = ["# a template this module wrote", ""]
    for name in FIXTURE_FIELDS:
        lines += [f"{name}:", f"{{{{{name}}}}}", ""]
    return "\n".join(lines)


def rendered_floor(tmp_path: Path, floor: int, name: str) -> str:
    """The block one invocation was handed under the floor's label."""
    root = producer.harness_mirror(tmp_path, name=f"{name}-harness")
    (root / "prompts" / inspection.INSPECTOR_PROMPT).write_text(
        floor_prompt(), encoding="utf-8")
    found = producer.inspecting(
        tmp_path, name=name, harness=root, act=producer.writes_nothing,
        config=producer.configuration(**{MIN_SEVERITY_KEY: str(floor)}))
    lines = found.prompt().splitlines()
    return lines[lines.index(f"{FLOOR_FIELD}:") + 1]


def names_a_severity(block: str) -> list[int]:
    """Which of the severities the scale defines the block names."""
    return [one for one in SEVERITIES if str(one) in block]


def test_the_rendered_prompt_states_the_floor_in_force(tmp_path):
    """The ninth criterion's first half: the invocation is told the floor it is
    writing under, and is told the one in force rather than another.

    Read by label rather than by wording, so what is asserted is that the
    render supplies the value: each block names its own floor and neither names
    the other's.
    """
    middling = rendered_floor(tmp_path, MIDDLE, "floor-at-middle")
    highest = rendered_floor(tmp_path, HIGHEST, "floor-at-highest")

    assert names_a_severity(middling) == [MIDDLE]
    assert names_a_severity(highest) == [HIGHEST]
    assert middling != highest


def test_at_the_lowest_severity_the_block_reads_as_no_floor(tmp_path):
    """The ninth criterion's second half: where the floor is the lowest
    severity there is no floor, and saying "the floor is 1" would tell an
    invocation nothing.

    The absence — that no severity numeral appears — is controlled by the same
    reading of the block at a real floor in the same test, which does name one.
    """
    none_at_all = rendered_floor(tmp_path, LOWEST, "floor-at-lowest")
    real = rendered_floor(tmp_path, MIDDLE, "floor-that-is-one")

    assert names_a_severity(none_at_all) == []
    assert names_a_severity(real) == [MIDDLE]
    assert none_at_all != real
    assert none_at_all.strip()


def shipped_prompt_harness(tmp_path: Path, name: str) -> Path:
    """The mirrored harness root with this repository's own prompts in it.

    The prompt is the subject here rather than an input: what is asserted is
    that the template *this harness ships* carries the floor and renders with
    nothing left unfilled. The rules and workflows around it stay the mirror's,
    because those are inputs to the render and not its subject.
    """
    root = producer.harness_mirror(tmp_path, name=name)
    shutil.copytree(REPO_ROOT / "prompts", root / "prompts", dirs_exist_ok=True)
    return root


def test_the_shipped_prompt_carries_the_floor_and_renders_it_filled(tmp_path):
    """The shipped template names the floor's placeholder, and a render of the
    template this harness ships carries the floor rather than an unfilled one.

    "Unfilled" is not the absence of a placeholder here: this harness's render
    substitutes the literal `None` for a field nothing supplied, so an
    unsupplied floor would render as that word and no `{{` would survive to
    find. So the control is that literal — asserted to be what an unsupplied
    field renders as, beside the block the shipped render actually carries.
    """
    import context_assembler

    template = (REPO_ROOT / "prompts" / inspection.INSPECTOR_PROMPT).read_text(
        encoding="utf-8")
    placeholder = f"{{{{{FLOOR_FIELD}}}}}"
    assert placeholder in template

    unsupplied = context_assembler.render(placeholder, {})
    block = rendered_floor(tmp_path, MIDDLE, "floor-block-as-shipped")

    root = shipped_prompt_harness(tmp_path, "ships-the-prompt")
    found = producer.inspecting(
        tmp_path, name="rendered-as-shipped", harness=root,
        act=producer.writes_nothing,
        standards={producer.STANDARDS_FILE: f"- {producer.STANDARDS_MARKER}\n"},
        config=producer.configuration(**{MIN_SEVERITY_KEY: str(MIDDLE)}))
    rendered = found.prompt()

    assert block != unsupplied
    assert block in rendered
    assert str(MIDDLE) in rendered


# ==========================================================================
# What the report says
# ==========================================================================


def report_at(tmp_path: Path, floor: int, name: str, findings=None) -> str:
    """What a developer is shown for one inspection, through the real script."""
    return producer.report_text(
        inspecting_at(tmp_path, floor, name=name, findings=findings))


def test_the_report_says_which_floor_the_inspection_ran_under(tmp_path):
    """The tenth criterion, including where the floor excluded nothing.

    Two inspections over the same single finding, which both floors file, so
    the reports differ only in the floor they ran under. Each report is
    required to carry a line naming its own floor that the other does not, so
    what is asserted is the floor being stated rather than a numeral appearing
    somewhere.
    """
    only_high = (producer.brief(slug=HIGH, severity=HIGHEST,
                                confidence=producer.CONFIDENCES[-1]),)
    middling = report_at(tmp_path, MIDDLE, "reports-floor-middle",
                         findings=only_high)
    highest = report_at(tmp_path, HIGHEST, "reports-floor-highest",
                        findings=only_high)

    assert HIGH in middling and HIGH in highest
    assert BENEATH_THE_FLOOR not in middling
    assert BENEATH_THE_FLOOR not in highest

    only_in_middling = set(middling.splitlines()) - set(highest.splitlines())
    only_in_highest = set(highest.splitlines()) - set(middling.splitlines())
    assert [line for line in only_in_middling if str(MIDDLE) in line]
    assert [line for line in only_in_highest if str(HIGHEST) in line]


def test_the_report_names_the_floors_exclusion_and_never_the_caps(tmp_path):
    """The second criterion as a developer meets it: the excluded finding is
    named in the report under the floor's reason, with its severity.

    The control for the cap's absence is the reason itself: the same report
    carries the floor's reason and not the cap's, and the report at the lowest
    severity carries neither.
    """
    printed = report_at(tmp_path, None, "reports-the-floor-drop")
    lines = printed.splitlines()

    naming_it = [line for line in lines if LOW in line]
    assert len(naming_it) == 1, naming_it
    assert BENEATH_THE_FLOOR in naming_it[0]
    assert str(LOWEST) in naming_it[0]
    assert [line for line in lines if inspection.PAST_THE_CAP in line] == []

    none_at_all = report_at(tmp_path, LOWEST, "reports-no-floor-drop")
    assert BENEATH_THE_FLOOR not in none_at_all
    assert LOW in none_at_all


# ==========================================================================
# The post-story producer applies the same floor, from the same key
# ==========================================================================


def inspection_findings() -> list[dict]:
    """One finding beneath the harness default and one above it, for the run."""
    return [after_story.finding(1, severity=LOWEST),
            after_story.finding(2, severity=HIGHEST)]


def filed_by(target: Path) -> list[str]:
    return sorted(entry["payload"]["slug"]
                  for entry in after_story.queue_entries(target))


def test_the_post_story_inspection_applies_the_floor_and_names_it_in_its_line(
        tmp_path, harness, monkeypatch):
    """The seventh criterion: the same floor, resolved from the same key by the
    producer's own `bounds` call, and named in the run's events.log with a
    count of how many went that way.

    The floor is configured on the target rather than handed to the producer,
    so what is observed is the key being read where the post-story inspection
    reads it. The control for "the low finding was not filed" is the high one
    beside it in the same queue, and the control for the line is the run below
    under no floor, where the same line carries no such count.
    """
    target, _journal, code, _inspector, _runner = after_story.completing_run(
        tmp_path, harness, monkeypatch, name="post-story-floor",
        findings=inspection_findings(),
        **{MIN_SEVERITY_KEY: str(MIDDLE)})

    assert code == 0
    assert filed_by(target) == [after_story.finding(2)["slug"]]
    assert after_story.finding(1)["slug"] not in filed_by(target)

    line = after_story.inspection_line(target)
    assert f"{BENEATH_THE_FLOOR}: 1" in line, line


def test_the_same_run_under_no_floor_files_what_the_floor_dropped(
        tmp_path, harness, monkeypatch):
    """The control for the run above, and the one that makes its absences mean
    something: the same run at the lowest severity files both findings and its
    line names no floor drop."""
    target, _journal, code, _inspector, _runner = after_story.completing_run(
        tmp_path, harness, monkeypatch, name="post-story-no-floor",
        findings=inspection_findings(),
        **{MIN_SEVERITY_KEY: str(LOWEST)})

    assert code == 0
    assert filed_by(target) == sorted([after_story.finding(1)["slug"],
                                       after_story.finding(2)["slug"]])
    assert BENEATH_THE_FLOOR not in after_story.inspection_line(target)


def test_a_floor_that_excludes_every_finding_still_completes_the_run(
        tmp_path, harness, monkeypatch):
    """The floor may exclude a brief and may not refuse, delay or block a run.

    The strongest case for it: a floor no finding of this run clears. The run
    still exits as it would, its inspection still writes its line, and the line
    still says what the floor did — so a run that filed nothing is
    distinguishable from a run that inspected nothing.
    """
    target, _journal, code, inspector, _runner = after_story.completing_run(
        tmp_path, harness, monkeypatch, name="post-story-everything-dropped",
        findings=[after_story.finding(1, severity=LOWEST)],
        **{MIN_SEVERITY_KEY: str(HIGHEST)})

    assert code == 0
    assert inspector.invocations, "the inspection never ran"
    assert filed_by(target) == []
    assert f"{BENEATH_THE_FLOOR}: 1" in after_story.inspection_line(target)


def test_the_post_story_entry_point_still_offers_no_way_to_stop_a_run():
    """The constraint the floor may not have loosened: the entry point returns
    nothing, declares no parameter a caller could be told to stop by, and the
    floor added none.

    Read off the signature rather than off a run, because what is being denied
    is the existence of a way rather than one run's use of it.
    """
    source = (REPO_ROOT / "orchestration" / "story_inspection.py").read_text(
        encoding="utf-8")
    entry = [node for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.FunctionDef)
             and node.name == story_inspection.inspect_after_story.__name__]
    assert len(entry) == 1
    declared = {argument.arg for argument in
                (*entry[0].args.args, *entry[0].args.kwonlyargs)}
    assert MIN_SEVERITY_KEY not in declared
    assert "min_severity" not in declared
    assert entry[0].returns is None or ast.unparse(entry[0].returns) == "None"


# ==========================================================================
# One floor, applied in one place, reached by both producers
# ==========================================================================


HARNESS_SOURCES = ("orchestration/inspection.py",
                   "orchestration/story_inspection.py",
                   "scripts/l5-inspect")

#: The name the floor's drop is built with, and the name of the type it is
#: built as, read off the module so the scan below looks for what the module
#: writes rather than for a spelling this file chose.
FLOOR_REASON_NAME = "BENEATH_THE_FLOOR"
DROP_TYPE_NAME = inspection.Drop.__name__


def functions_building_the_floors_drop(source: str) -> set[str]:
    """Every function that constructs the floor's drop, by name.

    A function that merely *names* the reason — the post-story summary lists
    every reason so it can count each one — is not building the drop and is not
    reported here, which is what makes the answer "where the floor decides".
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            name = (inner.func.id if isinstance(inner.func, ast.Name)
                    else getattr(inner.func, "attr", ""))
            if name != DROP_TYPE_NAME:
                continue
            if any(FLOOR_REASON_NAME in ast.unparse(argument)
                   for argument in inner.args):
                found.add(node.name)
    return found


def test_the_floor_builds_its_drop_in_one_place_and_both_producers_reach_it():
    """The floor is written once, so the two producers cannot diverge on it.

    The control is a planted source where a second function builds the same
    drop, which the same scan reports — so one name is a fact about the shipped
    sources rather than about a scan that finds nothing.
    """
    building = {relative: functions_building_the_floors_drop(
        (REPO_ROOT / relative).read_text(encoding="utf-8"))
        for relative in HARNESS_SOURCES}

    assert building["orchestration/inspection.py"] == \
        {inspection.file_findings.__name__}
    assert building["orchestration/story_inspection.py"] == set()
    assert building["scripts/l5-inspect"] == set()

    planted = (
        f"def files_them(found):\n"
        f"    return [{DROP_TYPE_NAME}({FLOOR_REASON_NAME}, one, 1)"
        f" for one in found]\n\n\n"
        f"def files_them_again(found):\n"
        f"    return [{DROP_TYPE_NAME}({FLOOR_REASON_NAME}, one, 1)"
        f" for one in found]\n"
    )
    assert functions_building_the_floors_drop(planted) == \
        {"files_them", "files_them_again"}


def test_the_post_story_producer_hands_the_resolved_floor_to_that_one_place():
    """It passes the floor it resolved rather than applying one of its own.

    Read at the call: every mention of the floor in the post-story producer is
    an argument to the shared filing call, so there is no second partition
    there to diverge. The behavioural half of this is the run above, where the
    key configured on the target decides what that producer files.
    """
    source = (REPO_ROOT / "orchestration" / "story_inspection.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)

    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and getattr(node.func, "attr", "") ==
             inspection.file_findings.__name__]
    assert len(calls) == 1
    passed = {keyword.arg for keyword in calls[0].keywords}
    assert "min_severity" in passed

    # Every mention of the floor in this producer is an argument it hands on —
    # to the shared filing call, or to the report that states the floor it ran
    # under. None of them is a partition of its own, so there is nothing here
    # to diverge from the call above.
    carriers = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and (getattr(node.func, "attr", "")
                     in (inspection.file_findings.__name__,
                         inspection.Report.__name__))]
    inside = {id(node) for carrier in carriers for node in ast.walk(carrier)
              if isinstance(node, ast.Attribute) and node.attr == "min_severity"}
    mentions = [node for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
                and node.attr == "min_severity"]
    assert mentions
    assert {id(node) for node in mentions} == inside


# ==========================================================================
# The key is declared, and a target meets it in the template
# ==========================================================================


def test_the_floor_is_declared_in_the_config_schema_beside_the_other_bounds():
    """Declared, which is what makes it a key the harness may read and what the
    declared-keys coverage check compares against."""
    declared = harness_config.declared_config_keys()

    assert MIN_SEVERITY_KEY in declared
    assert producer.MAX_FINDINGS_KEY in declared
    assert producer.MAX_COST_KEY in declared


def test_the_template_comments_the_floor_in_beside_the_two_existing_bounds():
    """Where a target meets the key. Commented like its neighbours, so a target
    that copies the template configures nothing it did not choose to."""
    template = (REPO_ROOT / "templates" / "config.yaml").read_text(
        encoding="utf-8")
    commented = [line.lstrip("# ").split(":", 1)[0]
                 for line in template.splitlines()
                 if line.startswith("#") and ":" in line]

    for key in (MIN_SEVERITY_KEY, producer.MAX_FINDINGS_KEY,
                producer.MAX_COST_KEY):
        assert key in commented, key
    # And it is commented rather than set: a template that declared it would
    # configure a floor for a target that never chose one. The control is the
    # same template with that one comment marker taken off, where the same
    # search does find the key declared.
    assert f"\n{MIN_SEVERITY_KEY}:" not in template
    uncommented = template.replace(f"# {MIN_SEVERITY_KEY}:",
                                   f"{MIN_SEVERITY_KEY}:")
    assert f"\n{MIN_SEVERITY_KEY}:" in uncommented


# ==========================================================================
# Nothing here reaches a model
# ==========================================================================


def test_every_inspection_in_this_module_is_driven_through_a_fake_runner():
    """The architecture standard that keeps model calls out of this suite.

    Every call this module makes into the producer's helper passes an `act`,
    which those helpers only accept alongside the fake runner they build; the
    post-story runs install a fake inspector in the agent runner's place.
    Asserted by reading this file: no call here names `agent_runner`.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    named = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "agent_runner" not in named
    assert "agent_runner" not in {alias.name for node in ast.walk(tree)
                                  if isinstance(node, ast.Import)
                                  for alias in node.names}

    planted = "import agent_runner\n\n\ndef go():\n    return agent_runner\n"
    assert "agent_runner" in {alias.name for node in ast.walk(ast.parse(planted))
                              if isinstance(node, ast.Import)
                              for alias in node.names}


def test_the_findings_this_module_writes_conform_to_the_shipped_shape():
    """The fixture's own control: every finding above is a conforming brief, so
    a drop asserted here is the floor's answer rather than a malformed one."""
    import schema_validator

    for finding in (*three_severities(), *inspection_findings()):
        assert schema_validator.validate(finding, producer.BRIEF_SCHEMA) == [], \
            finding["slug"]
        assert finding["severity"] in SEVERITIES

    # The control: the same validation over a finding whose severity is not one
    # the scale defines does report it, so an empty list above is the shape
    # holding rather than a validator answering nothing.
    off_the_scale = dict(three_severities()[0], severity=HIGHEST + 1)
    assert schema_validator.validate(off_the_scale, producer.BRIEF_SCHEMA) != []
