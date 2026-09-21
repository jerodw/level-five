"""Two decisions the retry of story-183 changed, each shown in a run rather
than only at the function that decides it.

A planner writes one reasoned `do_not_modify` entry for several files —
"a.md, b.md and c.md (the reason)" — and before this retry the coordinator
read the whole text as one path, so an extension to any of them slipped past
the bound. Now each path the entry lists bars an extension to it, and this
module runs a story carrying such an entry to show a reach for the path in the
middle of the list is refused naming the entry, with the same reach accepted
once the reason is stripped.

A finding whose category the routing table does not define has no stage for
the bounds to ask about. Before this retry a blocked path or a reasoned entry
could refuse it with an empty stage name, hiding the category behind the
refusal; now it is left for the routing, which escalates naming the category
as it always did. The run here carries such a finding under the blocked
prefix and shows the escalation names the category, no refusal is written,
and no event of the run names an empty stage — beside the same finding under a
declared category, which is refused and lets the run complete.

The boundary the split reads is the sibling module's own fixture and this
repository's code: it is held to be the clause boundary plan_validation
already splits on, a semicolon is shown to split, and an "and" inside a path
component is shown not to.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import conftest
import plan_validation
import story_coordinator
from test_a_repair_reaches_outside_the_storys_scope_within_bounds import (  # noqa: F401 - the harness fixture the runs configure
    BARE_DIR,
    BARE_EXTENSION,
    BARE_FILE,
    DECLARED_SCOPE,
    EXTENSION_REFUSED,
    declared_target,
    FROZEN_DIR,
    FROZEN_REASON,
    HELPERS,
    IN_SCOPE_FILE,
    SAMPLE_SCOPE,
    SEALED,
    SEALED_EXTENSION,
    STORY_ID,
    announcement_line,
    announcements,
    assert_accepted,
    check,
    harness_root,
    refusals,
    story_path,
)
from test_repair_pass import (  # noqa: F401 - the fixture the runs configure
    DOCUMENTING,
    PASS,
    STAGE_NAMES,
    UNDECLARED_CATEGORY,
    configured_workflow,
    drive,
    events_of,
    history_of,
    read_state,
    record_path,
    repairing,
)

#: The listed paths, the bare entry's directory in the middle so the path a
#: run reaches is one the whole text neither equals nor prefixes.
LISTED = (HELPERS, BARE_DIR, FROZEN_DIR)
LISTING_TEXT = f"{LISTED[0]}, {LISTED[1]} and {LISTED[2]}"
LISTING_ENTRY = f"{LISTING_TEXT} ({FROZEN_REASON})"


def scope_with(entry: str) -> str:
    return f"""\
scope:
  modify:
    - {IN_SCOPE_FILE}
  do_not_modify:
    - {entry}
"""


def target_declaring(target_root: Path, entry: str) -> Path:
    """The shared target with its story's scope narrowed to one file and one
    do_not_modify entry, committed so the run starts from it."""
    path = story_path(target_root)
    installed = path.read_text(encoding="utf-8")
    assert SAMPLE_SCOPE in installed, "the shared story's scope block moved"
    path.write_text(conftest.STORY.replace(SAMPLE_SCOPE, scope_with(entry)),
                    encoding="utf-8")
    conftest.commit_setup(target_root, "declare one do_not_modify entry")
    return target_root


@pytest.fixture
def listing_target(target_root: Path) -> Path:
    return target_declaring(target_root, LISTING_ENTRY)


@pytest.fixture
def stripped_target(target_root: Path) -> Path:
    """The same listing with its reason stripped: a bare entry, barring nothing."""
    return target_declaring(target_root, LISTING_TEXT)


def test_the_listed_path_the_run_reaches_is_in_the_middle_of_the_list():
    """The premise: the whole text does not name the reached path, so a
    refusal below is the split's doing and not a prefix match on the text."""
    assert BARE_FILE.startswith(BARE_DIR)
    assert not story_coordinator._names_path(LISTING_TEXT, BARE_FILE)
    assert story_coordinator.carries_a_reason(LISTING_ENTRY)
    assert not story_coordinator.carries_a_reason(LISTING_TEXT)
    assert DECLARED_SCOPE != scope_with(LISTING_ENTRY)


def test_a_run_refuses_an_extension_to_a_path_in_the_middle_of_a_listed_entry(
        listing_target, harness_root):
    code, runner, run_dir = drive(listing_target, harness_root,
                                  [repairing(BARE_EXTENSION)])
    assert code == 0
    assert read_state(run_dir)["status"] == "completed"
    assert runner.calls == STAGE_NAMES
    assert read_state(run_dir)["repair_pass_count"] == 0
    assert not record_path(run_dir).exists()
    (refused,) = refusals(run_dir)
    assert LISTING_ENTRY in refused["message"]
    assert BARE_FILE in refused["message"]
    assert DOCUMENTING in refused["message"]
    assert announcements(run_dir) == []


def test_the_same_reach_is_accepted_once_the_listing_carries_no_reason(
        stripped_target, harness_root):
    """The control: the identical finding against the identical list with
    the parenthetical removed is granted, routed and announced."""
    run = drive(stripped_target, harness_root, [repairing(BARE_EXTENSION), PASS])
    assert_accepted(run, BARE_EXTENSION, DOCUMENTING)


@pytest.mark.parametrize("path", LISTED)
def test_every_path_the_entry_lists_refuses_a_reach_through_the_check(path):
    """Each listed path in turn, as a function, so the run above is one
    instance of a rule that holds for the first and last entries as well."""
    story = {"scope": {"do_not_modify": [LISTING_ENTRY]}}
    reaching = {**BARE_EXTENSION, "path": f"{path.rstrip('/')}/x.md"
                if path.endswith("/") else path}
    (refused,) = check(reaching, story=story).refused
    assert LISTING_ENTRY in refused.bound
    stripped = {"scope": {"do_not_modify": [LISTING_TEXT]}}
    assert check(reaching, story=stripped).refused == []


# --------------------------------------------------------------------------
# An unknown category is left for the routing, whatever its path
# --------------------------------------------------------------------------


UNKNOWN_SEALED = {**SEALED_EXTENSION, "category": UNDECLARED_CATEGORY}


def test_an_unknown_category_under_a_blocked_path_escalates_naming_the_category(
        declared_target, harness_root):
    """The finding crosses the blocked-path bound and names no stage; the run
    escalates on the category, writes no refusal, and no event of the run
    carries an empty stage name where a refusal would have."""
    code, runner, run_dir = drive(declared_target, harness_root,
                                  [repairing(UNKNOWN_SEALED)])
    assert code == 2
    assert runner.calls == STAGE_NAMES
    assert read_state(run_dir)["status"] == "escalated"
    entry = history_of(run_dir)[-1]
    assert entry["event"] == "escalated"
    assert UNDECLARED_CATEGORY in entry["message"]
    assert refusals(run_dir) == []
    assert EXTENSION_REFUSED not in events_of(run_dir)
    for event in history_of(run_dir):
        assert "may not change" not in event["message"], event
    assert not record_path(run_dir).exists()


def test_the_same_finding_under_a_declared_category_is_refused_and_completes(
        declared_target, harness_root):
    """The control for the silence above: only the category differs, and the
    refusal fires naming the blocked prefix and the stage the category resolves
    to, so an absent refusal is the category's doing."""
    assert UNKNOWN_SEALED["path"] == SEALED_EXTENSION["path"]
    code, _, run_dir = drive(declared_target, harness_root,
                             [repairing(SEALED_EXTENSION)])
    assert code == 0
    assert read_state(run_dir)["status"] == "completed"
    (refused,) = refusals(run_dir)
    assert SEALED in refused["message"]
    assert DOCUMENTING in refused["message"]
    assert "escalated" not in events_of(run_dir)


# --------------------------------------------------------------------------
# The boundary the split reads
# --------------------------------------------------------------------------


def test_the_entry_boundary_is_the_clause_boundary_plan_validation_splits_on():
    """One boundary, read twice: the run-time split and the plan-time
    strictness check agree on what separates one listed path from the next."""
    assert (story_coordinator._ENTRY_PATH_BOUNDARY.pattern
            == plan_validation._CLAUSE_BOUNDARY.pattern)


def test_a_semicolon_separates_listed_paths_and_a_word_holding_and_does_not():
    bound = story_coordinator.do_not_modify_bound
    semicolons = {"scope": {"do_not_modify": [
        f"{HELPERS}; {BARE_DIR} (kept as they are)"]}}
    assert bound(semicolons, BARE_FILE) == f"{HELPERS}; {BARE_DIR} (kept as they are)"
    assert bound(semicolons, HELPERS) == f"{HELPERS}; {BARE_DIR} (kept as they are)"
    # "and" inside a path component is not a boundary: the entry names one
    # directory and bars what is under it, and does not bar a file whose
    # name is what a split on the letters alone would leave.
    sandbox = {"scope": {"do_not_modify": ["sandbox/ (kept as it is)"]}}
    assert story_coordinator._entry_paths("sandbox/ (kept as it is)") == ["sandbox/"]
    assert bound(sandbox, "sandbox/README.md") == "sandbox/ (kept as it is)"
    assert bound(sandbox, "s/README.md") is None
    assert bound(sandbox, "box/README.md") is None
    # Control: the same letters standing alone are a boundary.
    assert story_coordinator._entry_paths("s and box/ (kept)") == ["s", "box/"]
