"""A repair may reach a file outside the story's declared scope, within bounds
the coordinator holds, and every reach is announced.

Before this story a verifier that judged an Inspector finding correct could fix
it only where the fix lived in one file one stage owned and the story already
listed; four verdicts declined correct findings for that reason alone. Now a
repairable finding may name a file the story did not declare and say why in
`scope_extension` — the rule the story changed and the line in that file that
states it. The coordinator decides each such reach against three bounds before
any pass record is written: a path under a repository-wide blocked path from
the execution rules, a path the resolved stage's own restriction governs, and a
path a reasoned `scope.do_not_modify` entry names or is a directory above. A
refusal drops that finding from the pass, is one event naming the bound, and
hands the accounting a verdict in which the finding is declined with the
refusal as its reason, so it is filed as a brief at completion; the rest of the
pass routes and the run continues. An accepted extension is granted as any
finding is and announced, one event per file, through the same writer the grant
lines use.

Every run below executes a workflow `tests/test_repair_pass.py` built and this
module reuses whole — four stages, a writing stage confined to one directory, a
verifier declaring the repair pass and a routing table sending three categories
to three stages — under execution rules this module writes, against the shared
target with a story whose declared scope this module narrows and whose
`do_not_modify` carries one reasoned entry and one bare one. Every name comes
off those definitions rather than being spelled at an assertion. The cases
whose subject genuinely is what this repository ships — the two verifier
prompts, the verdict and history schemas, where `carries_a_reason` is defined,
that no workflow definition changed — read the shipped artifacts through the
sibling modules' readers and say so.

Every absence asserted here carries a demonstration that it can fail:

  * each refusal — "no grant for that path is applied" — sits beside the same
    finding accepted when the bound does not apply: the blocked path moved out
    from under the prefix, the confined stage's finding routed to an
    unconfined one, the reasoned entry's path swapped for the bare entry's;
  * "no scope-extension event is written for an in-scope finding" sits beside
    the same path under a finding that does carry the field, which writes one;
  * "a refused-only verdict takes no pass and spends nothing" sits beside a
    verdict carrying a refusal and an acceptance, which takes one, and beside
    a run that takes the whole budget through verdicts each carrying a refusal;
  * "a slug fixed across two files with one refused is not acted on" sits
    beside the same pair with the refused path moved out from under the
    prefix, which is acted on whole and files nothing;
  * "the story artifact is not written to" sits beside a rendering of it with
    the scope block altered, which the same comparison tells apart;
  * "no workflow definition changed" resolves through the shared story range
    and sits beside constructed histories the same call tells apart.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import conftest
import plan_validation
import schema_validator
import story_coordinator
import story_inspection
import story_parser
from test_a_routed_finding_is_repaired_unless_the_verdict_says_why import (
    CORRECTION_BOUNDS_PHRASE,
    DEFAULT_OPENING,
    REPAIRABLE_PHRASE,
    RIGHT_TO_DECLINE,
    paragraph_containing,
    sentences,
    verifier_prompts,
)
from test_execution_history import emittable_kinds
import test_inspection_findings_reach_the_story as inspected
from test_inspection_findings_reach_the_story import (  # noqa: F401 - fixtures used by name
    filings,
    no_model,
)
from test_repair_pass import (  # noqa: F401 - the fixture the runs configure
    ARTIFACT,
    BEHAVIOUR_CATEGORY,
    BEHAVIOUR_FINDING,
    BUDGET,
    DOCUMENTING,
    GRANT_EVENT,
    HISTORY_SCHEMA,
    NOTES,
    PASS,
    PROMPTS,
    RECORD_CATEGORY,
    RECORDED,
    REPAIRABLE,
    REQUIRED_OF_A_FINDING,
    ROUTED,
    ROUTES,
    STAGE_NAMES,
    VERDICT_SCHEMA,
    VERIFYING,
    WORKFLOW,
    WRITING,
    configured_workflow,
    content,
    destination_of,
    drive,
    entry_shape,
    events_of,
    events_of_kind,
    history_of,
    paths_named_in,
    read_json,
    read_state,
    record_of,
    record_path,
    repairing,
)

REPO_ROOT = conftest.HARNESS_ROOT
COORDINATOR_SOURCE = Path(story_coordinator.__file__).read_text(encoding="utf-8")
PLAN_VALIDATION_SOURCE = Path(plan_validation.__file__).read_text(encoding="utf-8")

STORY_ID = conftest.SAMPLE_STORY_ID

#: The field a repairable finding carries to ask for an extension, spelled once
#: here and read from the coordinator's own constant so the two cannot drift.
EXTENSION = story_coordinator.SCOPE_EXTENSION

#: The two events this story adds, as the execution-history schema enumerates
#: them. Neither is spelled in any run-level assertion below except through
#: these names.
EXTENSION_GRANT = "scope-extension-grant-applied"
EXTENSION_REFUSED = "scope-extension-refused"

# --------------------------------------------------------------------------
# The execution rules these runs run under
# --------------------------------------------------------------------------

#: A prefix this module's rules block for every stage. Deliberately not a
#: prefix the shipped rules block, so a refusal naming it is a fact about the
#: rules the run loaded rather than about the shipped ones.
SEALED = "sealed/"

RULES = {
    "max_retries": 2,
    "require_verifier_pass": True,
    "blocked_paths": [".git/", ".harness/runs/", ".harness/history/",
                      ".harness/stories/", SEALED],
}

# --------------------------------------------------------------------------
# The story these runs execute: a narrow scope, and both kinds of entry
# --------------------------------------------------------------------------

#: The scope block the shared sample story carries, replaced whole below.
SAMPLE_SCOPE = """\
scope:
  modify:
    - src/
  do_not_modify:
    - rules/
"""

#: The one file the narrowed story declares it modifies. Inside the writing
#: stage's confinement, so an extension to a sibling under that confinement is
#: outside the story's scope and inside the stage's — the case the restriction
#: bound must leave alone.
IN_SCOPE_FILE = "src/app.py"

#: A directory a reasoned entry bars, and one a bare entry names. The reason
#: is what tells a developer's decision from a planner's default, which is why
#: the bare entry bars nothing.
FROZEN_DIR = "docs/frozen/"
FROZEN_REASON = ("the frozen pages are published from elsewhere, so nothing "
                 "under them moves here")
FROZEN_ENTRY = f"{FROZEN_DIR} ({FROZEN_REASON})"
BARE_DIR = "docs/bare/"

DECLARED_SCOPE = f"""\
scope:
  modify:
    - {IN_SCOPE_FILE}
  do_not_modify:
    - {FROZEN_ENTRY}
    - {BARE_DIR}
"""

DECLARED_STORY = conftest.STORY.replace(SAMPLE_SCOPE, DECLARED_SCOPE)
STORY_SCHEMA = schema_validator.load_schema("story")
DECLARED = story_parser.parse(DECLARED_STORY, STORY_SCHEMA)

# --------------------------------------------------------------------------
# The files an extension may name, and the findings that name them
# --------------------------------------------------------------------------

#: Outside the story's scope and inside the writing stage's confinement.
HELPERS = "src/helpers.py"
#: Outside both; the writing stage's confinement governs it.
OUTSIDE_CONFINEMENT = NOTES
#: Under the prefix the rules block.
SEALED_FILE = f"{SEALED}README.md"
#: Under the prefix a blocked-path control moves the same file to.
UNSEALED_FILE = "unsealed/README.md"
#: Under the reasoned entry, and under the bare one.
FROZEN_FILE = f"{FROZEN_DIR}README.md"
BARE_FILE = f"{BARE_DIR}README.md"


def extending(path: str, category: str, marker: str) -> dict:
    """A repairable finding reaching `path` through `category`, carrying the
    reason an extension carries. `marker` makes each finding's words its own,
    so a search of an event or a prompt is looking for this finding."""
    return {
        "path": path,
        "location": f"{path} - the line that states the rule",
        "finding": f"MARKER-{marker} {path} states the rule the story changed",
        "correction": f"MARKER-{marker}-FIX restate the rule as the story left it",
        "category": category,
        EXTENSION: f"MARKER-{marker}-REASON the story changed the rule; "
                   f"{path} line 3 states it",
    }


HELPERS_EXTENSION = extending(HELPERS, BEHAVIOUR_CATEGORY, "HELPERS")
NOTES_EXTENSION = extending(OUTSIDE_CONFINEMENT, BEHAVIOUR_CATEGORY, "NOTES")
NOTES_UNRESTRICTED_EXTENSION = extending(OUTSIDE_CONFINEMENT, RECORD_CATEGORY,
                                         "NOTES-UNRESTRICTED")
SEALED_EXTENSION = extending(SEALED_FILE, RECORD_CATEGORY, "SEALED")
UNSEALED_EXTENSION = extending(UNSEALED_FILE, RECORD_CATEGORY, "UNSEALED")
FROZEN_EXTENSION = extending(FROZEN_FILE, RECORD_CATEGORY, "FROZEN")
BARE_EXTENSION = extending(BARE_FILE, RECORD_CATEGORY, "BARE")

#: Every extension the runs below accept, beside the one each is a control for.
ACCEPTED = (HELPERS_EXTENSION, NOTES_UNRESTRICTED_EXTENSION, UNSEALED_EXTENSION,
            BARE_EXTENSION)
REFUSED = (NOTES_EXTENSION, SEALED_EXTENSION, FROZEN_EXTENSION)


def without_the_field(finding: dict) -> dict:
    return {key: value for key, value in finding.items() if key != EXTENSION}


# --------------------------------------------------------------------------
# Fixture plumbing
# --------------------------------------------------------------------------


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """The sibling module's definition under this module's rules."""
    return conftest.materialize_workflow(
        WORKFLOW, tmp_path / "scope-extension-harness",
        prompts=PROMPTS, rules=RULES)


@pytest.fixture
def declared_target(target_root: Path) -> Path:
    """The shared target with the story's scope narrowed and both kinds of
    do_not_modify entry declared, committed so the run starts from it."""
    path = story_path(target_root)
    installed = path.read_text(encoding="utf-8")
    assert SAMPLE_SCOPE in installed, "the shared story's scope block moved"
    path.write_text(DECLARED_STORY, encoding="utf-8")
    conftest.commit_setup(target_root, "narrow the story's scope for these runs")
    return target_root


def story_path(target_root: Path) -> Path:
    return target_root / ".harness" / "stories" / f"{STORY_ID}.yaml"


def stage_named(name: str) -> dict:
    return next(stage for stage in WORKFLOW["stages"] if stage["name"] == name)


def confinement_of(name: str) -> story_coordinator.StageRestriction:
    (restriction,) = story_coordinator.restrictions_on(stage_named(name))
    return restriction


def in_declared_scope(path: str) -> bool:
    """Whether the narrowed story's scope.modify covers `path`: an entry
    ending in a slash covers what is under it, any other entry itself alone."""
    return any(path.startswith(entry) if entry.endswith("/") else path == entry
               for entry in DECLARED["scope"]["modify"])


def refusals(run_dir: Path) -> list[dict]:
    return events_of_kind(run_dir, EXTENSION_REFUSED)


def announcements(run_dir: Path, stage: str | None = None) -> list[dict]:
    return events_of_kind(run_dir, EXTENSION_GRANT, stage)


def grants_naming(run_dir: Path, path: str) -> list[dict]:
    """The repair-pass grant events whose message names `path` as a whole
    token — the sibling module's matcher, because one granted name here is
    the tail of another."""
    return [event for event in events_of_kind(run_dir, GRANT_EVENT)
            if paths_named_in(event["message"], (path,))]


def events_log(run_dir: Path) -> str:
    return (run_dir / "events.log").read_text(encoding="utf-8")


def announcement_line(stage: str, finding: dict) -> str:
    """The one form the announcement takes, as the story specifies it."""
    return (f"scope-extension grant applied: {stage} may change "
            f"{finding['path']}: {finding[EXTENSION]}")


# ==========================================================================
# The fixture is what it claims to be
# ==========================================================================


def test_the_narrowed_story_declares_one_file_and_both_kinds_of_entry(
        declared_target):
    """Read back through the parser off the target the runs use, so the scope
    the runs see is the scope these premises are about."""
    parsed = story_parser.parse(story_path(declared_target).read_text(
        encoding="utf-8"), STORY_SCHEMA)
    assert parsed["scope"]["modify"] == [IN_SCOPE_FILE]
    assert parsed["scope"]["do_not_modify"] == [FROZEN_ENTRY, BARE_DIR]
    assert parsed == DECLARED
    assert story_coordinator.carries_a_reason(FROZEN_ENTRY)
    assert not story_coordinator.carries_a_reason(BARE_DIR)


def test_every_extension_names_a_file_outside_the_declared_scope():
    for finding in (*ACCEPTED, *REFUSED):
        assert not in_declared_scope(finding["path"]), finding["path"]
    assert in_declared_scope(IN_SCOPE_FILE)
    # And each names a category the table routes, so a refusal below is the
    # bound's doing rather than an undeclared category's escalation.
    for finding in (*ACCEPTED, *REFUSED):
        assert finding["category"] in ROUTES, finding["category"]


def test_each_bound_has_a_case_it_applies_to_and_a_control_it_does_not():
    """The premises the three refusal cases rest on, stated of the fixture:
    the writing stage's confinement governs the notes and not the helpers, the
    documenting stage declares no restriction, the sealed prefix is blocked and
    the unsealed one is not, and the frozen entry carries a reason where the
    bare one does not."""
    confinement = confinement_of(WRITING)
    assert confinement.sense == story_coordinator.CONFINEMENT
    assert confinement.governs(OUTSIDE_CONFINEMENT)
    assert not confinement.governs(HELPERS)
    assert story_coordinator.restrictions_on(stage_named(DOCUMENTING)) == []
    assert destination_of(BEHAVIOUR_CATEGORY) == WRITING
    assert destination_of(RECORD_CATEGORY) == DOCUMENTING
    assert STAGE_NAMES.index(WRITING) < STAGE_NAMES.index(DOCUMENTING)

    blocked = RULES["blocked_paths"]
    assert story_coordinator.is_blocked(SEALED_FILE, blocked)
    assert not story_coordinator.is_blocked(UNSEALED_FILE, blocked)
    assert not any(story_coordinator.is_blocked(one["path"], blocked)
                   for one in ACCEPTED)

    assert story_coordinator.do_not_modify_bound(DECLARED, FROZEN_FILE) == FROZEN_ENTRY
    assert story_coordinator.do_not_modify_bound(DECLARED, BARE_FILE) is None
    assert BUDGET >= 2, "the budget case needs more than one pass"


# ==========================================================================
# The bounds, decided as a function
# ==========================================================================


def check(*findings: dict, story: dict = DECLARED, rules: dict = RULES):
    """The bounds asked over the fixture's own table, stages, story and
    rules. The findings are handed over as given, as the coordinator hands
    the verdict's own list, because the verdict handed on drops a refused
    finding by identity."""
    return story_coordinator.scope_extension_check(
        list(findings), ROUTES, STAGE_NAMES, WORKFLOW["stages"], story, rules,
        # Every path this module's fixture reaches is read as existing, so
        # the confinement, blocked-path and do_not_modify bounds decide
        # exactly as they did before the check asked about existence.
        exists=lambda path: True)


def test_the_check_accepts_what_no_bound_applies_to_and_refuses_the_rest():
    decided = check(*ACCEPTED, *REFUSED)
    assert decided.accepted == list(ACCEPTED)
    assert [one.finding for one in decided.refused] == list(REFUSED)
    for refused in decided.refused:
        assert refused.stage == destination_of(refused.finding["category"])


def test_each_refusal_names_its_own_bound_in_the_bounds_own_words():
    by_path = {one.finding["path"]: one for one in check(*REFUSED).refused}

    blocked = by_path[SEALED_FILE].bound
    assert SEALED in blocked
    assert "execution rules" in blocked

    restricted = by_path[OUTSIDE_CONFINEMENT].bound
    assert confinement_of(WRITING).wording in restricted
    assert OUTSIDE_CONFINEMENT in restricted

    reasoned = by_path[FROZEN_FILE].bound
    assert FROZEN_ENTRY in reasoned
    assert "do_not_modify" in reasoned


def test_the_bounds_are_asked_in_order_and_the_first_that_applies_is_named():
    """A sealed path routed to the confined stage crosses two bounds; the
    refusal names the blocked path, which is asked first."""
    doubly = extending(SEALED_FILE, BEHAVIOUR_CATEGORY, "DOUBLY")
    assert confinement_of(WRITING).governs(SEALED_FILE), "the premise"
    (refused,) = check(doubly).refused
    assert SEALED in refused.bound
    assert confinement_of(WRITING).wording not in refused.bound


def test_a_finding_carrying_no_extension_is_accepted_without_being_asked():
    """The in-scope control, as a function: the same path the confinement
    refuses under an extension is accepted untouched without the field — the
    grant an in-scope repairable finding gets lifts confinement today and
    keeps doing so."""
    plain = without_the_field(NOTES_EXTENSION)
    decided = check(plain, BEHAVIOUR_FINDING)
    assert decided.accepted == [plain, BEHAVIOUR_FINDING]
    assert decided.refused == []
    assert check(NOTES_EXTENSION).accepted == []


def test_a_bare_entry_bars_nothing_and_a_reasoned_one_bars_its_directory():
    """The do_not_modify bound directly: the entry's text before its closing
    parenthetical equals the path or is a directory prefix of it, and only a
    reasoned entry counts."""
    bound = story_coordinator.do_not_modify_bound
    assert bound(DECLARED, FROZEN_FILE) == FROZEN_ENTRY
    assert bound(DECLARED, FROZEN_DIR.rstrip("/")) is None
    assert bound(DECLARED, "docs/frozenness/README.md") is None
    assert bound(DECLARED, BARE_FILE) is None
    # The same path against the same entry with its reason stripped is not
    # barred, so the refusal above is the reason's doing.
    stripped = {"scope": {"do_not_modify": [FROZEN_DIR]}}
    assert bound(stripped, FROZEN_FILE) is None
    # An entry naming a file bars that file alone; one naming a directory
    # without its slash still covers what is under it.
    one_file = {"scope": {"do_not_modify": [f"{HELPERS} (kept as it is)"]}}
    assert bound(one_file, HELPERS) == f"{HELPERS} (kept as it is)"
    assert bound(one_file, f"{HELPERS}.bak") is None
    slashless = {"scope": {"do_not_modify": [f"{FROZEN_DIR.rstrip('/')} (frozen)"]}}
    assert bound(slashless, FROZEN_FILE) == f"{FROZEN_DIR.rstrip('/')} (frozen)"
    assert bound(None, FROZEN_FILE) is None
    assert bound({"scope": {}}, FROZEN_FILE) is None


#: A reasoned entry listing several paths before its parenthetical, in the
#: shape planners actually write — this story's own artifact carries one.
LISTING_ENTRY = (f"{HELPERS}, {OUTSIDE_CONFINEMENT} and {FROZEN_DIR} "
                 f"({FROZEN_REASON})")
LISTING = {"scope": {"do_not_modify": [LISTING_ENTRY]}}


def test_a_reasoned_entry_listing_several_paths_bars_each_of_them():
    """The text before the parenthetical is split on commas and a standalone
    "and", so every path the entry lists bars an extension to it — a file by
    equality, a directory by prefix — and a path the entry does not list is
    left alone. An area-style entry naming no path still bars nothing."""
    bound = story_coordinator.do_not_modify_bound
    assert story_coordinator.carries_a_reason(LISTING_ENTRY)
    assert bound(LISTING, HELPERS) == LISTING_ENTRY
    assert bound(LISTING, OUTSIDE_CONFINEMENT) == LISTING_ENTRY
    assert bound(LISTING, FROZEN_FILE) == LISTING_ENTRY
    assert bound(LISTING, IN_SCOPE_FILE) is None
    assert bound(LISTING, f"{HELPERS}.bak") is None
    # The middle path is reached only through the split: the whole text
    # neither equals it nor prefixes it.
    assert not story_coordinator._names_path(
        LISTING_ENTRY.split(" (")[0], OUTSIDE_CONFINEMENT)
    area = {"scope": {"do_not_modify": [
        "the retry-routing path of the verifier branch (left alone)"]}}
    assert bound(area, HELPERS) is None
    assert bound(area, "the") is None


def test_an_extension_to_either_listed_path_is_refused_naming_the_entry():
    """Through the check itself: two findings reaching two paths one entry
    lists are both refused naming that entry, and the same findings against
    the entry with its reason stripped are both accepted."""
    for_helpers = extending(HELPERS, RECORD_CATEGORY, "LISTED-HELPERS")
    for_notes = extending(OUTSIDE_CONFINEMENT, RECORD_CATEGORY, "LISTED-NOTES")
    decided = check(for_helpers, for_notes, story=LISTING)
    assert decided.accepted == []
    assert [one.finding for one in decided.refused] == [for_helpers, for_notes]
    for refused in decided.refused:
        assert LISTING_ENTRY in refused.bound
    stripped = {"scope": {"do_not_modify": [LISTING_ENTRY.split(" (")[0]]}}
    assert check(for_helpers, for_notes, story=stripped).refused == []


def test_the_blocked_bound_reads_the_rules_it_is_given():
    """With the sealed prefix absent from the rules the same finding is
    accepted, so the refusal is the rules' doing and not the path's."""
    unsealing = {**RULES, "blocked_paths": [one for one in RULES["blocked_paths"]
                                            if one != SEALED]}
    assert check(SEALED_EXTENSION, rules=unsealing).refused == []
    assert check(SEALED_EXTENSION).accepted == []


@pytest.mark.parametrize("finding", [HELPERS_EXTENSION, SEALED_EXTENSION,
                                     FROZEN_EXTENSION],
                         ids=["unbounded", "blocked-path", "reasoned-entry"])
def test_a_finding_naming_an_undeclared_category_is_left_for_the_routing(
        finding):
    """The bounds have no stage to ask about, so the finding stays in
    `accepted` before any bound is asked — whatever its path — and the routing
    that follows escalates naming the unknown category as before. The
    blocked-path and reasoned-entry cases are refused under a declared
    category, so what leaves them accepted here is the category alone."""
    unknown = {**finding, "category": "a-category-this-workflow-does-not-declare"}
    assert unknown["category"] not in ROUTES
    decided = check(unknown)
    assert decided.accepted == [unknown]
    assert decided.refused == []
    if finding is not HELPERS_EXTENSION:
        assert check(finding).accepted == []


def test_the_verdict_handed_on_drops_each_refusal_and_declines_it_by_slug():
    verdict = repairing(FROZEN_EXTENSION, BARE_EXTENSION)
    slug = "zzz-the-routed-finding"
    named = {**verdict, REPAIRABLE: [
        {**verdict[REPAIRABLE][0], "location": f"{FROZEN_FILE} ({slug})"},
        verdict[REPAIRABLE][1]]}
    decided = check(*named[REPAIRABLE])
    handed = story_coordinator.verdict_after_refusals(named, decided, (slug,))
    assert handed[REPAIRABLE] == [BARE_EXTENSION]
    (declined,) = handed["declined_findings"]
    assert declined["slug"] == slug
    assert declined["reason"] == story_coordinator.refusal_reason(decided.refused[0])
    assert "scope extension refused" in declined["reason"]
    assert FROZEN_ENTRY in declined["reason"]
    assert schema_validator.validate(handed, VERDICT_SCHEMA) == []
    # Untouched keys travel; the verifier's own verdict is not mutated.
    assert handed["status"] == "passed"
    assert named[REPAIRABLE] == [{**FROZEN_EXTENSION,
                                  "location": f"{FROZEN_FILE} ({slug})"},
                                 BARE_EXTENSION]


def test_a_refusal_whose_location_names_no_routed_slug_is_keyed_by_the_location():
    verdict = repairing(FROZEN_EXTENSION)
    decided = check(*verdict[REPAIRABLE])
    handed = story_coordinator.verdict_after_refusals(verdict, decided, ("zzz-other",))
    assert handed[REPAIRABLE] == []
    (declined,) = handed["declined_findings"]
    assert declined["slug"] == FROZEN_EXTENSION["location"]
    assert not story_inspection.names_a_slug(declined["slug"], ("zzz-other",))


def test_a_check_that_refused_nothing_hands_the_verdict_back_as_it_was():
    verdict = repairing(BARE_EXTENSION, BEHAVIOUR_FINDING)
    decided = check(*verdict[REPAIRABLE])
    assert decided.refused == []
    assert story_coordinator.verdict_after_refusals(verdict, decided) is verdict


# ==========================================================================
# Two files, two stages: granted, entered at the earlier, announced
# ==========================================================================


@pytest.fixture
def two_stage_run(declared_target, harness_root):
    """A passing verdict carrying two extensions across two stages, the later
    stage's listed first so the entry is shown to be chosen by order rather
    than by position."""
    return drive(declared_target, harness_root,
                 [repairing(NOTES_UNRESTRICTED_EXTENSION, HELPERS_EXTENSION),
                  PASS])


def test_two_extensions_across_two_stages_enter_at_the_earlier_and_complete(
        two_stage_run):
    code, runner, run_dir = two_stage_run
    assert code == 0
    assert read_state(run_dir)["status"] == "completed"
    assert runner.calls == entry_shape(WRITING)
    assert read_state(run_dir)["repair_pass_count"] == 1
    assert read_state(run_dir)["retry_count"] == 0
    assert refusals(run_dir) == []
    record = record_of(run_dir)
    assert record["stage"] == WRITING
    assert record["findings"] == [
        {**NOTES_UNRESTRICTED_EXTENSION, "stage": DOCUMENTING},
        {**HELPERS_EXTENSION, "stage": WRITING},
    ]
    assert (run_dir / "completion-report.md").is_file()


def test_each_granted_file_is_announced_once_naming_stage_file_and_reason(
        two_stage_run):
    _, _, run_dir = two_stage_run
    expected = {WRITING: HELPERS_EXTENSION,
                DOCUMENTING: NOTES_UNRESTRICTED_EXTENSION}
    announced = announcements(run_dir)
    assert len(announced) == len(expected)
    for event in announced:
        finding = expected[event["stage"]]
        assert event["message"] == announcement_line(event["stage"], finding)
    assert {event["stage"] for event in announced} == set(expected)
    log = events_log(run_dir)
    for stage, finding in expected.items():
        assert announcement_line(stage, finding) in log
    assert schema_validator.validate(history_of(run_dir), HISTORY_SCHEMA) == []


def test_the_announcement_rides_beside_the_grant_that_it_announces(two_stage_run):
    """Each extension is granted exactly as any finding is — the grant event
    names the file for its stage — and the announcement follows it, at the
    entered stage's post-stage checks."""
    _, _, run_dir = two_stage_run
    for stage, finding in ((WRITING, HELPERS_EXTENSION),
                           (DOCUMENTING, NOTES_UNRESTRICTED_EXTENSION)):
        (grant,) = grants_naming(run_dir, finding["path"])
        assert grant["stage"] == stage
        (announced,) = announcements(run_dir, stage)
        assert announced["sequence"] > grant["sequence"]
    state = story_coordinator.load_state(run_dir)
    assert story_coordinator.repair_pass_grants(
        run_dir, WORKFLOW["stages"], state, WRITING, 1) == [HELPERS]
    assert story_coordinator.repair_pass_extensions(
        run_dir, WORKFLOW["stages"], state, WRITING, 1) == [
            (HELPERS, HELPERS_EXTENSION[EXTENSION])]
    assert story_coordinator.repair_pass_extensions(
        run_dir, WORKFLOW["stages"], state, DOCUMENTING, 1) == [
            (OUTSIDE_CONFINEMENT, NOTES_UNRESTRICTED_EXTENSION[EXTENSION])]


def test_the_announcement_is_one_write_rendered_three_ways(
        declared_target, harness_root, capsys):
    """Through append_event: the events.log line, the execution-history entry
    and the console line carry the same message, and the history carries it
    under the announcement's kind."""
    code, _, run_dir = drive(declared_target, harness_root,
                             [repairing(HELPERS_EXTENSION), PASS])
    assert code == 0
    line = announcement_line(WRITING, HELPERS_EXTENSION)
    (entry,) = announcements(run_dir)
    assert entry["message"] == line
    assert entry["event"] == EXTENSION_GRANT
    assert line in events_log(run_dir)
    assert line in capsys.readouterr().out


def test_the_entered_stage_is_told_an_extension_reaches_outside_the_scope(
        two_stage_run):
    """The pass statement in the record — the text the entered stages read —
    says what a finding carrying the field is and why it is granted."""
    _, _, run_dir = two_stage_run
    statement = record_of(run_dir)["statement"]
    assert statement == story_coordinator.repair_pass_statement(WRITING)
    assert EXTENSION in statement
    assert "outside the story's declared scope" in statement
    assert "granted it for this pass" in statement
    assert "reason recorded on the finding" in statement


def test_the_archived_verdict_is_what_the_verifier_wrote(two_stage_run):
    """The iteration archive holds the findings with their field, as judged;
    what the run decided about them is in the record and the events."""
    _, _, run_dir = two_stage_run
    archived = read_json(run_dir / "verification" / "iteration-1.json")
    assert archived[REPAIRABLE] == [NOTES_UNRESTRICTED_EXTENSION,
                                    HELPERS_EXTENSION]
    assert "declined_findings" not in archived


# ==========================================================================
# The story artifact is not written to during a run that takes an extension
# ==========================================================================


def test_the_story_artifact_is_untouched_by_a_run_taking_an_extension(
        two_stage_run):
    _, _, run_dir = two_stage_run
    relative = f".harness/stories/{STORY_ID}.yaml"
    assert content(run_dir, relative) == DECLARED_STORY
    # Control for the equality: the same comparison tells a rendering with the
    # scope block altered apart, so equality is about the file and not about
    # a comparison that cannot see a change.
    altered = DECLARED_STORY.replace(FROZEN_ENTRY, FROZEN_DIR)
    assert altered != DECLARED_STORY
    assert content(run_dir, relative) != altered


# ==========================================================================
# The three refusals, each beside the acceptance that is its control
# ==========================================================================


def assert_refused_and_the_run_went_on(run, finding: dict, stage: str) -> dict:
    """What every refusal has in common: one event naming the location, the
    file, the stage and a bound; the run completed; no grant of any kind names
    the path; no pass was taken on that finding. Returns the event."""
    code, runner, run_dir = run
    assert code == 0, runner.calls
    assert read_state(run_dir)["status"] == "completed"
    (refused,) = refusals(run_dir)
    assert refused["stage"] == VERIFYING
    message = refused["message"]
    assert finding["location"] in message
    assert finding["path"] in message
    assert stage in message
    assert message.startswith("scope extension refused")
    assert message in events_log(run_dir)
    assert grants_naming(run_dir, finding["path"]) == []
    assert not any(finding["path"] in one["message"]
                   for one in announcements(run_dir))
    assert "escalated" not in events_of(run_dir)
    assert schema_validator.validate(history_of(run_dir), HISTORY_SCHEMA) == []
    return refused


def assert_accepted(run, finding: dict, stage: str) -> None:
    """The control every refusal sits beside: the same shape of finding with
    the bound not applying is granted, announced and routed."""
    code, runner, run_dir = run
    assert code == 0, runner.calls
    assert refusals(run_dir) == []
    (grant,) = grants_naming(run_dir, finding["path"])
    assert grant["stage"] == stage
    (announced,) = announcements(run_dir)
    assert announced["message"] == announcement_line(stage, finding)
    assert runner.calls == entry_shape(stage)
    assert record_of(run_dir)["findings"] == [{**finding, "stage": stage}]


def test_a_path_under_a_blocked_prefix_is_refused_naming_that_bound(
        declared_target, harness_root):
    run = drive(declared_target, harness_root, [repairing(SEALED_EXTENSION)])
    refused = assert_refused_and_the_run_went_on(run, SEALED_EXTENSION, DOCUMENTING)
    assert SEALED in refused["message"]
    assert "execution rules" in refused["message"]
    _, runner, run_dir = run
    assert runner.calls == STAGE_NAMES
    assert ROUTED not in events_of(run_dir)


def test_the_same_file_out_from_under_the_prefix_is_accepted(
        declared_target, harness_root):
    run = drive(declared_target, harness_root,
                [repairing(UNSEALED_EXTENSION), PASS])
    assert_accepted(run, UNSEALED_EXTENSION, DOCUMENTING)


def test_a_path_the_resolved_stages_restriction_governs_is_refused_in_its_words(
        declared_target, harness_root):
    run = drive(declared_target, harness_root, [repairing(NOTES_EXTENSION)])
    refused = assert_refused_and_the_run_went_on(run, NOTES_EXTENSION, WRITING)
    assert confinement_of(WRITING).wording in refused["message"]
    _, runner, run_dir = run
    assert runner.calls == STAGE_NAMES
    assert ROUTED not in events_of(run_dir)


def test_the_same_file_routed_to_an_unrestricted_stage_is_accepted(
        declared_target, harness_root):
    run = drive(declared_target, harness_root,
                [repairing(NOTES_UNRESTRICTED_EXTENSION), PASS])
    assert_accepted(run, NOTES_UNRESTRICTED_EXTENSION, DOCUMENTING)


def test_a_path_a_reasoned_do_not_modify_entry_names_is_refused_naming_the_entry(
        declared_target, harness_root):
    run = drive(declared_target, harness_root, [repairing(FROZEN_EXTENSION)])
    refused = assert_refused_and_the_run_went_on(run, FROZEN_EXTENSION, DOCUMENTING)
    assert FROZEN_ENTRY in refused["message"]
    _, runner, run_dir = run
    assert runner.calls == STAGE_NAMES
    assert ROUTED not in events_of(run_dir)


def test_the_same_path_against_a_bare_entry_is_not_refused_on_that_ground(
        declared_target, harness_root):
    run = drive(declared_target, harness_root,
                [repairing(BARE_EXTENSION), PASS])
    assert_accepted(run, BARE_EXTENSION, DOCUMENTING)


# ==========================================================================
# A refused-only verdict takes no pass; a mixed one takes one on the rest
# ==========================================================================


def test_a_verdict_whose_only_finding_is_refused_takes_no_pass_and_spends_nothing(
        declared_target, harness_root):
    code, runner, run_dir = drive(declared_target, harness_root,
                                  [repairing(FROZEN_EXTENSION)])
    assert code == 0
    assert runner.calls == STAGE_NAMES
    state = read_state(run_dir)
    assert state["repair_pass_count"] == 0
    assert state["retry_count"] == 0
    assert not record_path(run_dir).exists()
    assert ROUTED not in events_of(run_dir)
    assert RECORDED not in events_of(run_dir)
    assert len(refusals(run_dir)) == 1
    # The passing verdict still stands: no attempt was archived and the run
    # completed on it.
    assert not (run_dir / "attempts").exists()
    assert read_state(run_dir)["status"] == "completed"


def test_a_verdict_carrying_a_refusal_and_an_acceptance_routes_the_rest(
        declared_target, harness_root):
    """The control for "no pass": the same refused finding beside an accepted
    one takes one pass carrying the accepted finding alone."""
    code, runner, run_dir = drive(
        declared_target, harness_root,
        [repairing(FROZEN_EXTENSION, BARE_EXTENSION), PASS])
    assert code == 0
    assert runner.calls == entry_shape(DOCUMENTING)
    assert read_state(run_dir)["repair_pass_count"] == 1
    record = record_of(run_dir)
    assert record["findings"] == [{**BARE_EXTENSION, "stage": DOCUMENTING}]
    assert events_of(run_dir).count(ROUTED) == 1
    (refused,) = refusals(run_dir)
    assert FROZEN_FILE in refused["message"]
    assert grants_naming(run_dir, FROZEN_FILE) == []
    (grant,) = grants_naming(run_dir, BARE_FILE)
    assert grant["stage"] == DOCUMENTING
    (announced,) = announcements(run_dir)
    assert announced["message"] == announcement_line(DOCUMENTING, BARE_EXTENSION)
    state = story_coordinator.load_state(run_dir)
    assert story_coordinator.repair_pass_grants(
        run_dir, WORKFLOW["stages"], state, DOCUMENTING, 1) == [BARE_FILE]
    # The refusal precedes the routing in the stream: decided before the
    # record was written.
    events = events_of(run_dir)
    assert events.index(EXTENSION_REFUSED) < events.index(ROUTED)


def test_a_refusal_spends_nothing_from_the_repair_pass_budget(
        declared_target, harness_root):
    """Every verdict up to the budget carries a refusal beside an acceptance,
    and every one of them is routed: the budget is spent by passes taken and
    not by findings refused. The verdict after the last pass is recorded as
    over budget, as before this story."""
    code, runner, run_dir = drive(
        declared_target, harness_root,
        [repairing(FROZEN_EXTENSION, BARE_EXTENSION)] * BUDGET
        + [repairing(BARE_EXTENSION)])
    assert code == 0
    state = read_state(run_dir)
    assert state["repair_pass_count"] == BUDGET
    assert events_of(run_dir).count(ROUTED) == BUDGET
    assert len(refusals(run_dir)) == BUDGET
    assert events_of(run_dir).count(RECORDED) == 1
    assert runner.calls.count(VERIFYING) == BUDGET + 1


# ==========================================================================
# An in-scope finding carrying no extension is exactly as it was
# ==========================================================================


def test_a_repairable_finding_carrying_no_extension_is_granted_as_today(
        declared_target, harness_root):
    """The sibling module's own finding — the notes, outside the confinement,
    with no field — is granted, routed and recorded as it always was, and no
    scope-extension event of either kind is written for it."""
    code, runner, run_dir = drive(declared_target, harness_root,
                                  [repairing(BEHAVIOUR_FINDING), PASS])
    assert code == 0
    assert EXTENSION not in BEHAVIOUR_FINDING
    assert runner.calls == entry_shape(WRITING)
    assert record_of(run_dir)["findings"] == [
        {**BEHAVIOUR_FINDING, "stage": WRITING}]
    (grant,) = grants_naming(run_dir, BEHAVIOUR_FINDING["path"])
    assert grant["stage"] == WRITING
    assert announcements(run_dir) == []
    assert refusals(run_dir) == []
    assert not any(kind.startswith("scope-extension") for kind in events_of(run_dir))


def test_the_same_path_carrying_the_field_does_write_such_an_event(
        declared_target, harness_root):
    """Control for the absence above: the identical path under a finding that
    carries the field writes a scope-extension event — here a refusal, since
    that stage's confinement governs the path — so the silence above is the
    field's absence and not a writer that never fires."""
    assert NOTES_EXTENSION["path"] == BEHAVIOUR_FINDING["path"]
    assert NOTES_EXTENSION["category"] == BEHAVIOUR_FINDING["category"]
    _, _, run_dir = drive(declared_target, harness_root,
                          [repairing(NOTES_EXTENSION)])
    assert len(refusals(run_dir)) == 1


# ==========================================================================
# A refused finding is accounted as declined and filed as a brief
# ==========================================================================
#
# These run the sibling inspection module's workflow — an inspection declared
# on the judging stage, a fake Inspector routing one finding to the story —
# with the repair pass declared on that stage, so a refusal has an accounting
# to reach and a completion to be filed at.


DECLINED_ARTIFACT = "repair-after-inspection.json"
INSPECTING_NAME = "pre-stage-inspection-with-a-repair-pass"

#: The sibling's routed finding, naming the workflow these runs execute: an
#: inspection drops a finding naming a workflow the harness does not define
#: before it can be routed anywhere.
ROUTED_FINDING = {**inspected.OWN_FINDING, "workflow": INSPECTING_NAME}


def inspected_workflow_with_a_repair_pass(budget: int = 1) -> dict:
    workflow = json.loads(json.dumps(inspected.WORKFLOW))
    for stage in workflow["stages"]:
        if stage.get("inspection"):
            stage["repair_pass"] = {"result": DECLINED_ARTIFACT, "budget": budget}
    workflow["name"] = INSPECTING_NAME
    return workflow


@pytest.fixture
def inspecting_harness(tmp_path: Path) -> Path:
    return inspected.materialize(
        inspected_workflow_with_a_repair_pass(),
        tmp_path / "inspecting-harness", rules=RULES)


@pytest.fixture
def inspecting_target(tmp_path: Path) -> Path:
    return inspected.build_target(
        tmp_path / "inspecting-target",
        workflow=inspected_workflow_with_a_repair_pass()["name"])


def routed_repair(path: str, marker: str) -> dict:
    """A repairable finding raised from the routed finding — its location
    names the slug — reaching `path` with an extension."""
    finding = extending(path, inspected.RETRY_CATEGORY, marker)
    return inspected.naming_the_slug(ROUTED_FINDING, finding)


def declined_by_slug(recorded: dict) -> dict:
    return {one["slug"]: one for one in recorded["declined"]}


def test_a_refused_finding_is_declined_with_the_refusal_and_filed_at_completion(
        inspecting_target, inspecting_harness, tmp_path, filings):
    """One finding routed; the verdict raises a repairable finding from it
    whose path the rules block. The accounting records it as left with a
    reason — the refusal — the events.log line counts it so, no pass is
    taken, and the completion files it as a brief rather than losing it."""
    sealed = routed_repair(SEALED_FILE, "SEALED-ROUTED")
    run = inspected.drive(inspecting_target, inspecting_harness, tmp_path,
                          findings=[ROUTED_FINDING],
                          verdicts=[repairing(sealed)], name="refused")
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.state["repair_pass_count"] == 0
    assert not (run.run_dir / story_coordinator.repair_pass_result_file(
        DECLINED_ARTIFACT, 1)).exists()

    (refused,) = inspected.events_of_kind(run, EXTENSION_REFUSED)
    recorded = inspected.accounting(run)
    assert recorded["routed"] == 1
    assert recorded["acted_on"] == 0
    assert recorded["remainder_slugs"] == [ROUTED_FINDING["slug"]]
    entry = declined_by_slug(recorded)[ROUTED_FINDING["slug"]]
    assert entry["reason_given"] is True
    assert entry["reason"] == refused["message"]
    assert SEALED in entry["reason"]
    assert recorded["declined_with_reason"] == 1
    assert recorded["declined_without_reason"] == 0
    (line,) = [one["message"] for one in inspected.accounting_events(run)]
    assert "1 declined with a reason and 0 declined without one" in line

    assert inspected.filings_of(filings, ROUTED_FINDING) == 1
    (filed,) = inspected.filed_events(run)
    assert filed["filed"] == 1
    assert inspected.routed_markers(run) == [ROUTED_FINDING["body"]]


def test_the_same_finding_accepted_is_acted_on_and_filed_by_nobody(
        inspecting_target, inspecting_harness, tmp_path, filings):
    """The control: the identical verdict with the path out from under every
    bound is acted on — the pass is taken, the finding is not the remainder,
    nothing is declined and nothing is filed."""
    reaching = routed_repair("docs/NOTES.md", "REACHING")
    assert inspected_check(reaching).refused == [], "the premise"
    assert inspected_check(routed_repair(SEALED_FILE, "SEALED-ROUTED")).accepted \
        == [], "the premise of the case above"
    run = inspected.drive(inspecting_target, inspecting_harness, tmp_path,
                          findings=[ROUTED_FINDING],
                          verdicts=[repairing(reaching), inspected.PASS],
                          name="accepted")
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.state["repair_pass_count"] == 1
    assert inspected.events_of_kind(run, EXTENSION_REFUSED) == []
    (announced,) = inspected.events_of_kind(run, EXTENSION_GRANT)
    assert announced["message"] == announcement_line(inspected.WRITING, reaching)
    recorded = inspected.accounting(run)
    assert recorded["acted_on_slugs"] == [ROUTED_FINDING["slug"]]
    assert recorded["remainder_slugs"] == []
    assert recorded["declined"] == []
    assert inspected.filings_of(filings, ROUTED_FINDING) == 0
    assert inspected.routed_markers(run) == []


# --------------------------------------------------------------------------
# A fix spanning files, one of them refused
#
# The shape the prompts and the schema prescribe for a fix spanning files is
# one repairable finding per file, each naming the same routed slug in its
# location. Where a bound refuses one of those files, the accepted sibling
# still names the slug — so the accounting, reading the verdict's locations
# alone, would count the routed finding as acted on and discard the refusal
# the coordinator recorded for it. The runs below hold that the slug is left
# instead, whatever its accepted sibling did, and filed at completion.
# --------------------------------------------------------------------------


#: The accepted half: the sibling module's in-scope repairable finding, its
#: location naming the routed slug and carrying no extension.
IN_SCOPE_HALF = inspected.naming_the_slug(ROUTED_FINDING,
                                          inspected.REPAIRABLE_FINDING)


def assert_the_pass_was_taken_for_the_in_scope_half(run) -> None:
    """What the subject and its control share: one pass, entered at the stage
    the in-scope half resolves to, granting that half's file."""
    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.state["repair_pass_count"] == 1
    record = read_json(run.run_dir / story_coordinator.repair_pass_result_file(
        DECLINED_ARTIFACT, 1))
    assert record["stage"] == inspected.WRITING
    assert {**IN_SCOPE_HALF, "stage": inspected.WRITING} in record["findings"]
    granted = [one for one in inspected.events_of_kind(run, GRANT_EVENT)
               if paths_named_in(one["message"], (IN_SCOPE_HALF["path"],))]
    assert len(granted) == 1
    assert granted[0]["stage"] == inspected.WRITING


def test_a_slug_fixed_across_two_files_with_one_refused_is_left_and_filed(
        inspecting_target, inspecting_harness, tmp_path, filings):
    """Two repairable findings name the routed slug: the in-scope half is
    accepted and the pass is taken for it; the other reaches a path the rules
    block and is refused. The accounting leaves the slug with the refusal as
    its reason, and the completion files it as a brief — the accepted half
    does not stand in for the refused one."""
    refused_half = routed_repair(SEALED_FILE, "SEALED-SIBLING")
    assert EXTENSION not in IN_SCOPE_HALF
    assert story_inspection.names_a_slug(IN_SCOPE_HALF["location"],
                                         (ROUTED_FINDING["slug"],))
    assert story_inspection.names_a_slug(refused_half["location"],
                                         (ROUTED_FINDING["slug"],))
    decided = inspected_check(IN_SCOPE_HALF, refused_half)
    assert decided.accepted == [IN_SCOPE_HALF], "the premise"
    assert [one.finding for one in decided.refused] == [refused_half], "the premise"

    run = inspected.drive(inspecting_target, inspecting_harness, tmp_path,
                          findings=[ROUTED_FINDING],
                          verdicts=[repairing(IN_SCOPE_HALF, refused_half),
                                    inspected.PASS],
                          name="half-refused")
    assert_the_pass_was_taken_for_the_in_scope_half(run)
    record = read_json(run.run_dir / story_coordinator.repair_pass_result_file(
        DECLINED_ARTIFACT, 1))
    assert record["findings"] == [{**IN_SCOPE_HALF, "stage": inspected.WRITING}]
    (refused,) = inspected.events_of_kind(run, EXTENSION_REFUSED)
    assert SEALED_FILE in refused["message"]
    assert not any(paths_named_in(one["message"], (SEALED_FILE,))
                   for one in inspected.events_of_kind(run, GRANT_EVENT))

    recorded = inspected.accounting(run)
    assert recorded["routed"] == 1
    assert recorded["acted_on"] == 0
    assert recorded["acted_on_slugs"] == []
    assert recorded["remainder_slugs"] == [ROUTED_FINDING["slug"]]
    entry = declined_by_slug(recorded)[ROUTED_FINDING["slug"]]
    assert entry["reason_given"] is True
    assert entry["reason"] == refused["message"]
    assert SEALED in entry["reason"]
    assert recorded["declined_with_reason"] == 1
    assert recorded["declined_without_reason"] == 0

    assert inspected.filings_of(filings, ROUTED_FINDING) == 1
    (filed,) = inspected.filed_events(run)
    assert filed["filed"] == 1
    assert inspected.routed_markers(run) == [ROUTED_FINDING["body"]]


def test_the_same_two_findings_with_nothing_refused_are_acted_on_and_not_filed(
        inspecting_target, inspecting_harness, tmp_path, filings):
    """The control: the identical pair with the second half's path moved out
    from under the blocked prefix is accepted whole — the slug is acted on,
    nothing is declined and nothing is filed — so the filing above is the
    refusal's doing and not the pair's."""
    accepted_half = routed_repair(UNSEALED_FILE, "UNSEALED-SIBLING")
    assert inspected_check(IN_SCOPE_HALF, accepted_half).refused == [], "the premise"

    run = inspected.drive(inspecting_target, inspecting_harness, tmp_path,
                          findings=[ROUTED_FINDING],
                          verdicts=[repairing(IN_SCOPE_HALF, accepted_half),
                                    inspected.PASS],
                          name="nothing-refused")
    assert_the_pass_was_taken_for_the_in_scope_half(run)
    record = read_json(run.run_dir / story_coordinator.repair_pass_result_file(
        DECLINED_ARTIFACT, 1))
    assert record["findings"] == [
        {**IN_SCOPE_HALF, "stage": inspected.WRITING},
        {**accepted_half, "stage": inspected.WRITING}]
    assert inspected.events_of_kind(run, EXTENSION_REFUSED) == []
    (announced,) = inspected.events_of_kind(run, EXTENSION_GRANT)
    assert announced["message"] == announcement_line(inspected.WRITING,
                                                     accepted_half)

    recorded = inspected.accounting(run)
    assert recorded["acted_on_slugs"] == [ROUTED_FINDING["slug"]]
    assert recorded["remainder_slugs"] == []
    assert recorded["declined"] == []
    assert inspected.filings_of(filings, ROUTED_FINDING) == 0
    assert inspected.routed_markers(run) == []


def test_a_slug_in_refused_slugs_is_left_even_where_a_location_names_it():
    """The split held directly: with `refused_slugs` empty a routed finding a
    location names is acted on, as before the parameter existed; with its
    slug in `refused_slugs` the same verdict leaves it. A slug not in the
    verdict is left either way, and a refused slug that is not this finding's
    changes nothing."""
    own = inspected.OWN_FINDING
    other = inspected.BOTH_FINDING
    verdict = repairing(IN_SCOPE_HALF)
    assert story_inspection.names_a_slug(IN_SCOPE_HALF["location"], (own["slug"],))

    acted, remainder = story_inspection.acted_on_by([own, other], verdict)
    assert acted == [own]
    assert remainder == [other]

    acted, remainder = story_inspection.acted_on_by(
        [own, other], verdict, refused_slugs=(own["slug"],))
    assert acted == []
    assert remainder == [own, other]

    acted, remainder = story_inspection.acted_on_by(
        [own, other], verdict, refused_slugs=(other["slug"],))
    assert acted == [own]
    assert remainder == [other]

    # The default is the empty split, so a caller naming nothing is the
    # caller before the parameter existed.
    assert story_inspection.acted_on_by([own, other], verdict) == \
        story_inspection.acted_on_by([own, other], verdict, refused_slugs=())


def test_the_refused_slugs_handed_to_the_accounting_are_the_ones_declined():
    """The one match between a refusal and a routed slug: the slugs the
    refused findings' locations name, each once, are what the verdict handed
    on declines — so the accounting cannot be told to leave a slug the
    verdict does not decline, or the reverse. A check refusing nothing names
    nothing."""
    slug = ROUTED_FINDING["slug"]
    first = routed_repair(SEALED_FILE, "SEALED-FIRST")
    second = routed_repair(FROZEN_FILE, "FROZEN-SECOND")
    unrelated = extending(SEALED_FILE, inspected.RETRY_CATEGORY, "UNRELATED")
    # Decided over the verdict's own list, as the coordinator decides, because
    # the verdict handed on drops a refused finding by identity.
    verdict = repairing(IN_SCOPE_HALF, first, second, unrelated)
    decided = inspected_check(*verdict[REPAIRABLE])
    assert [one.finding for one in decided.refused] == [first, second, unrelated]

    routed = (slug, "zzz-never-routed")
    named = story_coordinator.refused_routed_slugs(decided, routed)
    assert named == (slug,)
    handed = story_coordinator.verdict_after_refusals(verdict, decided, routed)
    assert handed[REPAIRABLE] == [IN_SCOPE_HALF]
    assert {one["slug"] for one in handed["declined_findings"]} == \
        set(named) | {unrelated["location"]}
    assert story_coordinator.refused_routed_slugs(
        inspected_check(IN_SCOPE_HALF), routed) == ()
    assert story_coordinator.refused_routed_slugs(decided, ()) == ()


def inspected_check(*findings: dict):
    """The bounds asked over the inspection fixture's own table and stages,
    against this module's declared story — the one carrying the reasoned
    do_not_modify entry that bars FROZEN_FILE, which the shared sample story
    does not carry — so a check here refuses what the runs above refuse."""
    judging = next(stage for stage in inspected.WORKFLOW["stages"]
                   if stage.get("inspection"))
    return story_coordinator.scope_extension_check(
        list(findings), judging["on_failure"]["retry_routing"],
        inspected.STAGE_NAMES, inspected.WORKFLOW["stages"],
        DECLARED, RULES, exists=lambda path: True)


def test_the_routed_slugs_are_read_off_the_findings_artifact_before_accounting(
        tmp_path):
    """The reader the coordinator declines by: the slugs of the findings one
    attempt routed, off the findings artifact itself, empty where none ran."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    artifact = inspected.FINDINGS_ARTIFACT
    assert story_inspection.findings_slugs(run_dir, artifact, 1) == ()
    story_inspection.write_findings(
        run_dir, artifact, 1, STORY_ID, ran=True,
        findings=[inspected.OWN_FINDING, inspected.BOTH_FINDING])
    assert story_inspection.findings_slugs(run_dir, artifact, 1) == (
        inspected.OWN_FINDING["slug"], inspected.BOTH_FINDING["slug"])
    assert story_inspection.findings_slugs(run_dir, artifact, 2) == ()
    story_inspection.write_findings(run_dir, artifact, 2, STORY_ID, ran=False,
                                    findings=[])
    assert story_inspection.findings_slugs(run_dir, artifact, 2) == ()


# ==========================================================================
# The schemas this harness ships
# ==========================================================================


def test_the_verdict_schema_validates_a_finding_with_and_without_the_field():
    item = VERDICT_SCHEMA["properties"][REPAIRABLE]["items"]
    assert EXTENSION in item["properties"]
    assert EXTENSION not in item["required"]
    assert set(item["required"]) == REQUIRED_OF_A_FINDING
    assert item["properties"][EXTENSION]["type"] == "string"
    assert schema_validator.validate(repairing(HELPERS_EXTENSION),
                                     VERDICT_SCHEMA) == []
    assert schema_validator.validate(
        repairing(without_the_field(HELPERS_EXTENSION)), VERDICT_SCHEMA) == []
    assert schema_validator.validate(
        repairing(HELPERS_EXTENSION, BEHAVIOUR_FINDING), VERDICT_SCHEMA) == []


@pytest.mark.parametrize("wrong", [3, ["a reason"], {"reason": "a reason"}, True],
                         ids=["an int", "a list", "a mapping", "a bool"])
def test_the_verdict_schema_refuses_an_extension_that_is_not_a_string(wrong):
    broken = repairing({**HELPERS_EXTENSION, EXTENSION: wrong})
    errors = schema_validator.validate(broken, VERDICT_SCHEMA)
    assert errors
    assert any(EXTENSION in error for error in errors), errors


def test_the_schema_descriptions_say_what_the_field_is_and_what_a_span_is():
    """The field's description names the three bounds and what a refusal
    does; the repairable and path descriptions say a fix spanning files is one
    finding per file rather than a blocking issue; the declined reason says
    being out of scope is not a reason."""
    item = VERDICT_SCHEMA["properties"][REPAIRABLE]["items"]["properties"]
    field = item[EXTENSION]["description"]
    assert "rule the story changed" in field
    assert "execution rules block" in field
    assert "restriction on the stage" in field
    assert "do_not_modify" in field
    assert "refuses" in field
    assert "brief" in field
    assert "never escalates" in field
    for description in (VERDICT_SCHEMA["properties"][REPAIRABLE]["description"],
                        item["path"]["description"]):
        assert "one finding per file" in description
        assert "same slug" in description
    assert "or a blocking issue" not in item["path"]["description"]
    assert EXTENSION in item["path"]["description"]
    reason = VERDICT_SCHEMA["properties"]["declined_findings"]["items"][
        "properties"]["reason"]["description"]
    assert "not a reason" in reason
    assert EXTENSION in reason
    assert "no single stage" not in reason


def test_the_history_enum_names_both_kinds_and_the_coordinator_emits_both():
    """The closed enum declares the two new kinds, the coordinator's source
    spells both as append_event kinds, and every kind it can pass is in the
    enum — the sibling scan, restated over the two this story adds."""
    allowed = HISTORY_SCHEMA["items"]["properties"]["event"]["enum"]
    emitted = emittable_kinds(COORDINATOR_SOURCE)
    for kind in (EXTENSION_GRANT, EXTENSION_REFUSED):
        assert kind in allowed, kind
        assert kind in emitted, kind
    assert sorted(emitted - set(allowed)) == []
    for kind in (EXTENSION_GRANT, EXTENSION_REFUSED):
        entry = {"sequence": 1, "timestamp": "2026-01-01 00:00:00",
                 "event": kind, "message": "something happened"}
        assert schema_validator.validate([entry], HISTORY_SCHEMA) == [], kind


# ==========================================================================
# The verifier prompts this harness ships
# ==========================================================================


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_default_paragraph_extends_an_out_of_scope_fix_rather_than_declining(
        prompt: Path):
    text = prompt.read_text(encoding="utf-8")
    default = paragraph_containing(text, DEFAULT_OPENING)
    assert "out of scope is not a reason" in default
    assert "extended" in default
    assert "rather than declined" in default
    assert "declined_findings" in default
    # The right to decline is kept, and the phrase naming size appears once.
    # Since story-202 size is no longer one of the grounds — a correct finding
    # judged small is corrected or repaired, and one too small to be worth
    # either is dropped — so the phrase this reads for is the drop's, and
    # wrongness is named as the ground it still is in the words the paragraph
    # now uses rather than the ones it used when the two were listed together.
    assert RIGHT_TO_DECLINE in default
    assert "the finding is wrong" in default
    assert "no single stage can make in one file" not in default


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_repairable_paragraph_names_the_field_and_states_the_three_bounds(
        prompt: Path):
    text = prompt.read_text(encoding="utf-8")
    repairable = paragraph_containing(text, REPAIRABLE_PHRASE)
    assert f"`{EXTENSION}`" in repairable
    assert "rule the story changed" in repairable
    assert "rather than declined" in repairable
    assert "one finding per file" in repairable
    assert "same slug" in repairable
    assert "one stage" in repairable
    assert "naming the bound" in repairable
    assert "execution rules block" in repairable
    assert "restriction on the stage the category resolves to" in repairable
    assert "scope.do_not_modify" in repairable
    assert "with a reason beside it" in repairable
    assert "accounted as declined" in repairable
    assert "the run continues" in repairable
    # The bounds are three sentences' worth of one sentence: the one naming
    # them carries all three.
    (bounds,) = [one for one in sentences(repairable)
                 if "three bounds" in one]
    for bound in ("execution rules block", "restriction on the stage",
                  "scope.do_not_modify"):
        assert bound in bounds, bound


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_correction_bounds_paragraph_sends_a_span_to_repairable_findings(
        prompt: Path):
    text = prompt.read_text(encoding="utf-8")
    bounds = paragraph_containing(text, CORRECTION_BOUNDS_PHRASE)
    assert "one per file" in bounds
    assert "repairable findings" in bounds
    assert "in the file it names" in bounds
    assert "needing two stages to coordinate" not in bounds
    assert "in one file" not in bounds


def test_the_rewritten_passages_read_identically_between_the_shipped_prompts():
    """The parity the sibling module holds, restated over the sentences this
    story added: the sentence naming the field and the sentence naming the
    bounds are found in each prompt and are the same sentence."""
    prompts = verifier_prompts()
    assert len(prompts) > 1
    for needle in (f"`{EXTENSION}`", "three bounds", "out of scope is not a reason",
                   "one per file"):
        found = set()
        for prompt in prompts:
            text = prompt.read_text(encoding="utf-8")
            carrying = [one for paragraph in (
                paragraph_containing(text, DEFAULT_OPENING),
                paragraph_containing(text, CORRECTION_BOUNDS_PHRASE),
                paragraph_containing(text, REPAIRABLE_PHRASE),
            ) for one in sentences(paragraph) if needle in one]
            assert carrying, (prompt.name, needle)
            found.add(tuple(carrying))
        assert len(found) == 1, (needle, found)


# ==========================================================================
# carries_a_reason is defined once, in the coordinator
# ==========================================================================


def functions_named(source: str, name: str) -> list[ast.FunctionDef]:
    return [node for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef) and node.name == name]


def test_carries_a_reason_is_defined_once_and_imported_under_the_same_name():
    assert plan_validation.carries_a_reason is story_coordinator.carries_a_reason
    assert len(functions_named(COORDINATOR_SOURCE, "carries_a_reason")) == 1
    assert functions_named(PLAN_VALIDATION_SOURCE, "carries_a_reason") == []
    assert "carries_a_reason = story_coordinator.carries_a_reason" in \
        PLAN_VALIDATION_SOURCE


def test_the_plan_time_reason_check_decides_as_it_did():
    """The plan-time check reads the moved predicate: a bare entry is reported
    naming its index, a reasoned one is not, and an empty parenthetical is a
    bare entry."""
    problems = plan_validation.scope_reason_problems(DECLARED)
    assert len(problems) == 1
    assert "$.scope.do_not_modify[1]" in problems[0]
    assert BARE_DIR in problems[0]
    assert plan_validation.scope_reason_problems(
        {"scope": {"do_not_modify": [FROZEN_ENTRY]}}) == []
    assert plan_validation.scope_reason_problems(
        {"scope": {"do_not_modify": [f"{FROZEN_DIR} ()"]}})
    assert plan_validation.scope_reason_problems({}) == []


# ==========================================================================
# What this story left alone
# ==========================================================================


#: The paths the story's own scope.do_not_modify names, resolved through the
#: shared story range so the comparison stays honest once the story commits.
LEFT_ALONE = [
    "workflows/",
    "schemas/retry-guidance.schema.json",
    "schemas/story.schema.json",
    "orchestration/story_parser.py",
    "prompts/story-implementer.md",
    "prompts/story-tester.md",
    "prompts/documenter.md",
    "prompts/refactor-implementer.md",
]


def test_no_workflow_definition_and_none_of_the_left_alone_paths_changed():
    assert conftest.story_diff(LEFT_ALONE, validation_file=Path(__file__)) == ""


def test_the_same_comparison_tells_an_edited_workflow_apart(tmp_path):
    """Control for the absence above, over histories built here: one story
    respects the workflows directory and one edits it, and the identical call
    is empty for the first and non-empty for the second."""
    respecting = conftest.constructed_story(tmp_path, respected=["workflows/"],
                                            name="workflows-left-alone")
    assert conftest.constructed_story_diff(respecting, ["workflows/"]) == ""
    violating = conftest.constructed_story(tmp_path, violated=["workflows/"],
                                           name="workflows-edited")
    assert conftest.constructed_story_diff(violating, ["workflows/"]) != ""


def test_no_budget_key_is_read_for_extensions():
    """No new key: the repair-pass block reads the budget it read before, and
    the extension section names none. Read off the fixture's declaration and
    the coordinator's own source."""
    declaration = stage_named(VERIFYING)["repair_pass"]
    assert set(declaration) == {"result", "budget"}
    section = COORDINATOR_SOURCE[COORDINATOR_SOURCE.index(
        "def scope_extension_check"):COORDINATOR_SOURCE.index(
        "def repair_pass_extensions")]
    assert "budget" not in section
    assert "run_agent" not in section
    # Control for the two absences: the same slice of a copy with each word
    # planted is reported, so the silence is the section's and not the slice's.
    for planted in ("budget", "run_agent"):
        assert planted in section + f"\n    {planted} = None\n"
