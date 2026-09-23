"""Independent validation for the step after `ready_to_merge`: a merge on this
repository closes the brief its story was planned from.

Written from the story's acceptance criteria rather than from the
implementation. The subjects below are kept apart because they answer
different questions:

  * **the script**, which is where all the deciding lives. It is never
    reasoned about from its source: a copy of it is installed into a target
    repository this module builds under `tmp_path`, beside a configuration
    declaring a branch prefix and a stories directory this target does not
    have, and driven. The resolution — the branch derivation and the
    column-zero `brief_key` read — is driven through the script's own dry-run
    mode, which makes no tracker call at all; the tracker paths are driven
    against a stub `gh` this module wrote, first on PATH, which records every
    call made to it.

  * **the workflow definition**, which contributes only the trigger, the
    merged guard, the permission the close needs and the commit the artifact
    is read out of. Those are facts about the file this repository deploys, so
    the deployed file is what is read — a fixture definition could say only
    what this module had just written into it. Each reader takes the text as a
    parameter, so the same reader that is silent about the shipped file is run
    over a copy with the declaration removed.

What this module deliberately does not write down is either of the two values
this target configures. The branch prefix and the stories directory reach the
constructed targets as inventions, and the claim that the script carries no
second copy of this deployment's own values is made by reading them out of
`.harness/config.yaml` and searching the change for them — never by spelling
them here, which would be the second writing-down the rule forbids.

Every absence asserted here carries a demonstration that it can fail:

  * "this path made no tracker call" sits beside the drive that closes an
    issue, where the same ledger records two;
  * "nothing references the pull request to the issue" sits beside the same
    scan over a copy of each shipped file with a comment and a closing keyword
    planted in it, and beside a driven copy whose close call carries a
    comment, which the same reading of the recorded call reports;
  * "neither configured value is written down" sits beside the same reader run
    over a throwaway copy of the script carrying a fallback, the value
    interpolated from the configuration rather than spelled here — one reader,
    so a reader that had stopped seeing anything reddens the control;
  * "an artifact mentioning the field only in prose resolves nothing" sits
    beside the same artifact with the field added at column zero, which
    resolves;
  * "no run line interpolates the merged ref into the command" sits beside the
    same readers over a copy of the definition written the interpolated way,
    which report it;
  * every declaration read out of the shipped workflow definition sits beside
    the same reader over a copy with that declaration removed.

Nothing here reaches a network and nothing here runs `git`: the targets are
directories under `tmp_path`, and the only `gh` on PATH is the stub.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
from test_filed_query import fixture_file

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The two files this story ships. Both are this deployment's own glue and are
#: installed into no target, so both are read as they ship: the definition's
#: trigger, guard and permission are claims about the file GitHub will run, and
#: the script is copied rather than imitated so that what every drive below
#: exercises is the shipped text.
SCRIPT = REPO_ROOT.joinpath(".harness", "scripts", "close-brief.sh")
DEFINITION = REPO_ROOT / ".github" / "workflows" / "close-brief-on-merge.yml"

SCRIPT_SOURCE = SCRIPT.read_text(encoding="utf-8")
DEFINITION_SOURCE = DEFINITION.read_text(encoding="utf-8")

#: Where the script sits under the repository it was installed into. Derived
#: rather than written a second time, so a copy lands where the shipped one
#: lives and the definition's invocation and this module cannot disagree.
SCRIPT_REL = SCRIPT.relative_to(REPO_ROOT).as_posix()
CONFIG_REL = ".harness/config.yaml"

#: The two configuration keys the script reads. Keys rather than values: what
#: the rule forbids writing down is what this target configured them to be.
BRANCH_PREFIX_KEY = "branch_prefix"
STORIES_DIR_KEY = "stories_dir"


def one_spelling(pattern: str, source: str, subject: str) -> str:
    """The single distinct match of `pattern` in `source`.

    Used to take the script's own interface off the script rather than spell it
    here: a name that moved is a resolution that fails loudly, and a name that
    grew a second spelling is reported rather than silently picked from.
    """
    found = sorted(set(re.findall(pattern, source)))
    assert len(found) == 1, (subject, found)
    return found[0]


#: The environment variable that selects the dry run, read off the script.
DRY_RUN_VARIABLE = one_spelling(r"\bL5_[A-Z0-9_]*DRY_RUN\b", SCRIPT_SOURCE,
                                "the dry-run variable")


# --------------------------------------------------------------------------
# The target this module builds, and the `gh` it drives against
# --------------------------------------------------------------------------


#: A branch prefix and a stories directory this target does not have. They are
#: inventions, which is what makes "the script follows the configuration it was
#: installed beside" a statement about the file rather than about a coincidence
#: — and it is why neither of this target's own values appears in this module.
INVENTED_PREFIX = "an-invented-prefix/"
INVENTED_STORIES_DIR = "somewhere/invented-stories"

#: A second pair, for the drive that shows one configuration resolving what the
#: other calls not a story's branch at all.
OTHER_PREFIX = "another-invented-prefix/"
OTHER_STORIES_DIR = "somewhere-else/invented-stories"

STORY_ID = "story-invented"
BRIEF_KEY = "4312"
#: A different number, mentioned inside an indented block scalar, which the
#: column-zero read must not answer with.
PROSE_KEY = "9137"

LEDGER_VARIABLE = "L5_TEST_CLOSE_BRIEF_LEDGER"
REFUSE_VIEW_VARIABLE = "L5_TEST_CLOSE_BRIEF_REFUSE_VIEW"
REFUSE_CLOSE_VARIABLE = "L5_TEST_CLOSE_BRIEF_REFUSE_CLOSE"

STUB_GH = '''#!INTERPRETER
"""A `gh` this module wrote: the two calls the script makes, and a record of
every call made at all.

The record is the point. Most of what this module asserts about the quiet
paths is that no tracker call was made, and an assertion like that says
nothing unless something is there to have been called — so the stub is on
PATH for every drive, including the ones that must not reach it.

Two variables make it refuse: one at `issue view`, one at `issue close`, each
carrying the refusal it prints. A call it does not recognise is a failure
rather than a silence, so a script that started making a third kind of call
would be reported here rather than pass unnoticed.
"""
import json
import os
import sys

argv = sys.argv[1:]
ledger = os.environ["LEDGER_VARIABLE"]


def state():
    with open(ledger, encoding="utf-8") as handle:
        return json.load(handle)


def keep(recorded):
    with open(ledger, "w", encoding="utf-8") as handle:
        json.dump(recorded, handle)


def refuse(message):
    sys.stderr.write(message + "\\n")
    raise SystemExit(1)


recorded = state()
recorded["calls"].append(argv)
keep(recorded)

if argv[:2] == ["issue", "view"]:
    refusal = os.environ.get("REFUSE_VIEW_VARIABLE")
    if refusal:
        refuse(refusal)
    key = argv[2]
    if key not in recorded["issues"]:
        refuse("no issue is filed under %s" % key)
    print(recorded["issues"][key])
elif argv[:2] == ["issue", "close"]:
    refusal = os.environ.get("REFUSE_CLOSE_VARIABLE")
    if refusal:
        refuse(refusal)
    key = argv[2]
    recorded["issues"][key] = "CLOSED"
    keep(recorded)
    print("closed %s" % key)
else:
    refuse("this stub was given a call it knows nothing of: %r" % (argv,))
'''


def an_artifact(*, brief_key: str | None, prose_key: str | None = None) -> str:
    """One story artifact, optionally carrying the field and a decoy.

    The decoy is the demonstration the story's own description is: a
    `brief_key` named in prose inside an indented block scalar is not the
    field, and an artifact carrying both must resolve the field.
    """
    prose = ""
    if prose_key is not None:
        prose = (f"    The brief this was planned from is discussed here, and\n"
                 f"    brief_key: {prose_key} is a mention rather than the\n"
                 f"    field, because it is indented inside a block scalar.\n")
    field = "" if brief_key is None else f"brief_key: {brief_key}\n"
    return (
        "story:\n"
        f"  id: {STORY_ID}\n"
        "  title: an invented story\n"
        "  description: |\n"
        "    Something this module invented.\n"
        f"{prose}"
        f"{field}"
    )


def a_target(tmp_path: Path, name: str, *, prefix: str, stories_dir: str,
             artifacts: dict[str, str] | None = None) -> Path:
    """A repository the script is installed into, built under `tmp_path`.

    The shipped script is copied in at the path it occupies here, beside a
    configuration declaring the two values it reads. Neither value is this
    target's, which is the whole of why they can be written: what may not be
    written down is what this deployment configured, and these are inventions.
    """
    root = tmp_path / name
    (root / Path(SCRIPT_REL).parent).mkdir(parents=True, exist_ok=True)
    installed = root / SCRIPT_REL
    shutil.copy2(SCRIPT, installed)
    installed.chmod(0o755)

    (root / CONFIG_REL).write_text(
        "# A configuration this module invented.\n"
        f"{BRANCH_PREFIX_KEY}: {prefix}\n"
        f"{STORIES_DIR_KEY}: {stories_dir}\n",
        encoding="utf-8")

    for story_id, text in (artifacts or {}).items():
        artifact = root / stories_dir / f"{story_id}.yaml"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(text, encoding="utf-8")
    return root


def a_tracker(tmp_path: Path, name: str = "stub-bin", *,
              issues: dict[str, str] | None = None) -> tuple[dict, Path]:
    """The stub `gh`, first on PATH, and the ledger it records into."""
    ledger = tmp_path / f"{name}-ledger.json"
    fixture_file(tmp_path / name, "gh",
                 STUB_GH.replace("INTERPRETER", sys.executable)
                        .replace("LEDGER_VARIABLE", LEDGER_VARIABLE)
                        .replace("REFUSE_VIEW_VARIABLE", REFUSE_VIEW_VARIABLE)
                        .replace("REFUSE_CLOSE_VARIABLE", REFUSE_CLOSE_VARIABLE))
    ledger.write_text(json.dumps({"issues": dict(issues or {}), "calls": []}),
                      encoding="utf-8")
    environment = {
        **{name: value for name, value in os.environ.items()
           if name not in (DRY_RUN_VARIABLE, REFUSE_VIEW_VARIABLE,
                           REFUSE_CLOSE_VARIABLE)},
        "PATH": f"{tmp_path / name}{os.pathsep}{os.environ.get('PATH', '')}",
        LEDGER_VARIABLE: str(ledger),
    }
    return environment, ledger


def calls(ledger: Path) -> list[list[str]]:
    return json.loads(ledger.read_text(encoding="utf-8"))["calls"]


def issues(ledger: Path) -> dict[str, str]:
    return json.loads(ledger.read_text(encoding="utf-8"))["issues"]


def drive(root: Path, branch: str, environment: dict, *,
          dry_run: bool = False, **extra: str) -> subprocess.CompletedProcess:
    """One invocation of the installed script, exactly as the step makes it."""
    environment = {**environment, **extra}
    if dry_run:
        environment[DRY_RUN_VARIABLE] = "1"
    return subprocess.run([str(root / SCRIPT_REL), branch],
                          capture_output=True, text=True, env=environment)


@pytest.fixture
def tracker(tmp_path: Path):
    environment, ledger = a_tracker(tmp_path)
    return environment, ledger


# --------------------------------------------------------------------------
# 1. The resolution, driven in dry run against a constructed repository
# --------------------------------------------------------------------------


def test_the_key_a_story_records_is_the_key_the_script_resolves(tmp_path,
                                                                tracker):
    """The first criterion: a branch carrying the configured prefix and an
    artifact carrying a `brief_key` resolve that key, and the resolution is
    taken from the script itself rather than from a reading of its source."""
    environment, ledger = tracker
    root = a_target(tmp_path, "resolves", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: an_artifact(brief_key=BRIEF_KEY)})

    result = drive(root, INVENTED_PREFIX + STORY_ID, environment, dry_run=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == BRIEF_KEY
    assert STORY_ID in result.stderr
    # The dry run is a dry run: the stub was on PATH and was never reached.
    assert calls(ledger) == []


def test_the_field_is_read_and_not_a_mention_inside_a_block_scalar(tmp_path,
                                                                   tracker):
    """The column-zero read, against the artifact the story's own description
    demonstrates: a `brief_key` in prose inside an indented block scalar, and a
    top-level field carrying a different value."""
    environment, _ = tracker
    text = an_artifact(brief_key=BRIEF_KEY, prose_key=PROSE_KEY)
    # The decoy is really in the artifact, and really differs, or the assertion
    # below would hold for a reason that has nothing to do with anchoring.
    assert f"    brief_key: {PROSE_KEY}" in text
    assert PROSE_KEY != BRIEF_KEY

    root = a_target(tmp_path, "anchored", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: text})

    result = drive(root, INVENTED_PREFIX + STORY_ID, environment, dry_run=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == BRIEF_KEY
    assert PROSE_KEY not in result.stdout


def test_an_artifact_mentioning_the_field_only_in_prose_resolves_nothing(
        tmp_path, tracker):
    """The other half of the anchoring, and a quiet path: the mention is not
    the field, so there is nothing recorded to close."""
    environment, ledger = tracker
    root = a_target(tmp_path, "prose-only", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: an_artifact(brief_key=None,
                                                     prose_key=PROSE_KEY)})

    result = drive(root, INVENTED_PREFIX + STORY_ID, environment, dry_run=True)

    assert result.returncode == 0
    assert result.stdout.strip() == ""
    assert "brief_key" in result.stderr
    assert calls(ledger) == []


def test_the_same_artifact_with_the_field_at_column_zero_does_resolve(tmp_path,
                                                                      tracker):
    """The control the silence above needs: the identical artifact with the
    field added resolves, so "resolves nothing" is a property of where the key
    was written rather than of a read that has stopped seeing anything."""
    environment, _ = tracker
    prose_only = an_artifact(brief_key=None, prose_key=PROSE_KEY)
    with_the_field = prose_only + f"brief_key: {BRIEF_KEY}\n"
    root = a_target(tmp_path, "field-added", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: with_the_field})

    result = drive(root, INVENTED_PREFIX + STORY_ID, environment, dry_run=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == BRIEF_KEY


# --------------------------------------------------------------------------
# 2. The five quiet paths: exit 0, no tracker call, and which one it was
# --------------------------------------------------------------------------


def test_a_branch_carrying_no_configured_prefix_is_quiet(tmp_path, tracker):
    """A merged pull request that is not a story's costs nothing and says
    nothing alarming."""
    environment, ledger = tracker
    root = a_target(tmp_path, "not-a-story", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: an_artifact(brief_key=BRIEF_KEY)})

    result = drive(root, "a-branch-of-somebody-elses", environment)

    assert result.returncode == 0
    assert "a-branch-of-somebody-elses" in result.stderr
    assert INVENTED_PREFIX in result.stderr
    assert calls(ledger) == []


def test_a_story_whose_artifact_the_merge_did_not_land_is_quiet(tmp_path,
                                                                tracker):
    """It exits 0 and says which artifact it looked for, because a branch
    carrying the prefix and no artifact records no brief."""
    environment, ledger = tracker
    root = a_target(tmp_path, "no-artifact", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR)

    result = drive(root, INVENTED_PREFIX + STORY_ID, environment)

    assert result.returncode == 0
    assert f"{INVENTED_STORIES_DIR}/{STORY_ID}.yaml" in result.stderr
    assert calls(ledger) == []


def test_an_artifact_carrying_no_field_is_quiet(tmp_path, tracker):
    environment, ledger = tracker
    root = a_target(tmp_path, "no-field", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: an_artifact(brief_key=None)})

    result = drive(root, INVENTED_PREFIX + STORY_ID, environment)

    assert result.returncode == 0
    assert "brief_key" in result.stderr
    assert calls(ledger) == []


#: A key this deployment happens not to spell as an issue number. The harness
#: treats a brief key as opaque, and what can be done with one is the script's
#: judgement, so a key it cannot read is ordinary rather than a failure.
AN_OPAQUE_KEY = "PROJ-14"


def test_a_key_this_deployment_cannot_read_as_an_issue_number_is_quiet(
        tmp_path, tracker):
    environment, ledger = tracker
    root = a_target(tmp_path, "opaque-key", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: an_artifact(brief_key=AN_OPAQUE_KEY)})

    result = drive(root, INVENTED_PREFIX + STORY_ID, environment)

    assert result.returncode == 0
    assert AN_OPAQUE_KEY in result.stderr
    assert calls(ledger) == []


def test_an_issue_already_closed_is_left_alone(tmp_path):
    """A re-run, and a branch reverted and merged again, cost nothing: the
    state is asked for, it is not open, and no close call is made."""
    environment, ledger = a_tracker(tmp_path,
                                    issues={BRIEF_KEY: "CLOSED"})
    root = a_target(tmp_path, "already-closed", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: an_artifact(brief_key=BRIEF_KEY)})

    result = drive(root, INVENTED_PREFIX + STORY_ID, environment)

    assert result.returncode == 0, result.stderr
    assert BRIEF_KEY in result.stderr
    made = calls(ledger)
    assert [call[:2] for call in made] == [["issue", "view"]], made
    assert issues(ledger)[BRIEF_KEY] == "CLOSED"


# --------------------------------------------------------------------------
# 3. The close itself, and the one loud path
# --------------------------------------------------------------------------


def test_an_open_brief_is_closed(tmp_path):
    """The control every "no tracker call" above rests on: driven identically
    against an issue that is open, the script reaches the tracker, asks the
    state and closes it — so a ledger recording nothing elsewhere is a
    property of the path taken rather than of a stub nothing can reach."""
    environment, ledger = a_tracker(tmp_path, issues={BRIEF_KEY: "OPEN"})
    root = a_target(tmp_path, "closes", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: an_artifact(brief_key=BRIEF_KEY)})

    result = drive(root, INVENTED_PREFIX + STORY_ID, environment)

    assert result.returncode == 0, result.stderr
    assert issues(ledger)[BRIEF_KEY] == "CLOSED"
    made = calls(ledger)
    assert [call[:2] for call in made] == [["issue", "view"],
                                           ["issue", "close"]], made


A_REFUSAL = "the tracker refused this close"


def test_a_close_the_tracker_refused_is_loud(tmp_path):
    """The one loud path: a key resolved and a close refused exits non-zero
    with the refusal on stderr, so a red check on the merged pull request says
    the manual step still needs doing."""
    environment, ledger = a_tracker(tmp_path, issues={BRIEF_KEY: "OPEN"})
    root = a_target(tmp_path, "refused", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: an_artifact(brief_key=BRIEF_KEY)})

    result = drive(root, INVENTED_PREFIX + STORY_ID, environment,
                   **{REFUSE_CLOSE_VARIABLE: A_REFUSAL})

    assert result.returncode != 0
    assert A_REFUSAL in result.stderr
    assert BRIEF_KEY in result.stderr
    assert issues(ledger)[BRIEF_KEY] == "OPEN"


def test_a_state_that_could_not_be_read_is_loud(tmp_path):
    """A state that cannot be read establishes nothing about whether the brief
    is open, so it is not one of the quiet paths."""
    environment, ledger = a_tracker(tmp_path, issues={BRIEF_KEY: "OPEN"})
    root = a_target(tmp_path, "unreadable-state", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: an_artifact(brief_key=BRIEF_KEY)})

    result = drive(root, INVENTED_PREFIX + STORY_ID, environment,
                   **{REFUSE_VIEW_VARIABLE: A_REFUSAL})

    assert result.returncode != 0
    assert A_REFUSAL in result.stderr
    made = calls(ledger)
    assert [call[:2] for call in made] == [["issue", "view"]], made


#: How the state read asks for the state, as the tracker receives it: the one
#: field, and the field's own value rather than the object carrying it. Both
#: are load-bearing — everything the script decides afterwards compares what it
#: captured against the bare word the second flag leaves behind, and a read
#: that came back as `{"state":"OPEN"}` would be neither open nor closed to it.
STATE_READ = ["--json", "state", "--jq", ".state"]


def viewed(ledger: Path) -> list[list[str]]:
    """Every state read the script made, as the tracker was given it."""
    return [call for call in calls(ledger) if call[:2] == ["issue", "view"]]


def test_the_state_read_asks_for_the_one_field_and_for_its_value(tmp_path):
    """Driven rather than read off the script's source: the argument list the
    tracker was handed carries the key and both flags, so what is asserted is
    the call the script makes rather than a line somebody could rewrite into a
    comment. The control is below."""
    environment, ledger = a_tracker(tmp_path, issues={BRIEF_KEY: "OPEN"})
    root = a_target(tmp_path, "state-read", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: an_artifact(brief_key=BRIEF_KEY)})

    result = drive(root, INVENTED_PREFIX + STORY_ID, environment)

    assert result.returncode == 0, result.stderr
    assert viewed(ledger) == [["issue", "view", BRIEF_KEY, *STATE_READ]]


@pytest.mark.parametrize("dropped", [
    pytest.param(["--json", "state"], id="the-field-selector"),
    pytest.param(["--jq", ".state"], id="the-value-selector"),
])
def test_a_copy_dropping_either_flag_is_reported_by_that_reading(tmp_path,
                                                                 dropped):
    """The control for the drive above.

    A copy of the script differing only in the flag its state read carries is
    installed and driven, and the same reading of the recorded call must
    report it — so the argument list asserted above is a property of the
    shipped script rather than of a stub that discards what it was given or a
    reading that would have passed for any call at all.
    """
    environment, ledger = a_tracker(tmp_path, issues={BRIEF_KEY: "OPEN"})
    root = a_target(tmp_path, "-".join(dropped).strip("-."),
                    prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: an_artifact(brief_key=BRIEF_KEY)})
    installed = root / SCRIPT_REL
    source = installed.read_text(encoding="utf-8")
    without = source.replace(" " + " ".join(dropped), "", 1)
    assert without != source, dropped
    installed.write_text(without, encoding="utf-8")

    result = drive(root, INVENTED_PREFIX + STORY_ID, environment)

    assert result.returncode == 0, result.stderr
    made = viewed(ledger)
    assert len(made) == 1, made
    assert made != [["issue", "view", BRIEF_KEY, *STATE_READ]], made
    for flag in dropped:
        assert flag not in made[0], (flag, made)


# --------------------------------------------------------------------------
# 4. Nothing references the pull request to the issue
# --------------------------------------------------------------------------


#: What would create a pull-request-to-issue relationship: a comment on the
#: issue, an edit to its body, or a closing keyword naming it. The board's
#: Status-from-linked-pull-request automation reads such a link and fights the
#: harness's own status moves for as long as the pull request is open, so the
#: link is the thing to avoid rather than a detail to get right.
LINKING = (
    re.compile(r"--comment\b"),
    re.compile(r"--body(-file)?\b"),
    re.compile(r"(?i)\b(clos(e|es|ed)|fix(e[sd])?|resolv(e|es|ed))\s+#\d"),
)


def linking_lines(text: str) -> list[str]:
    """Every line of a file that would link a pull request to an issue."""
    return [line for line in text.splitlines()
            if any(pattern.search(line) for pattern in LINKING)]


@pytest.mark.parametrize("shipped", [SCRIPT, DEFINITION],
                         ids=lambda path: path.name)
def test_neither_shipped_file_links_the_pull_request_to_the_issue(shipped: Path):
    found = linking_lines(shipped.read_text(encoding="utf-8"))
    assert found == [], (shipped.name, found)


@pytest.mark.parametrize("planted", [
    pytest.param('gh issue close "$brief_key" --comment "done"\n', id="a-comment"),
    pytest.param('gh issue edit "$brief_key" --body-file /tmp/x\n', id="a-body-edit"),
    pytest.param("# closes #14 when this merges\n", id="a-closing-keyword"),
])
@pytest.mark.parametrize("shipped", [SCRIPT, DEFINITION],
                         ids=lambda path: path.name)
def test_the_same_scan_reports_a_link_planted_in_that_file(shipped: Path,
                                                           planted: str):
    """The negative control the absence needs, on the real text of the file the
    scan has just been silent about."""
    found = linking_lines(shipped.read_text(encoding="utf-8") + planted)
    assert found == [planted.rstrip("\n")], found


def test_the_close_call_the_script_makes_carries_nothing_but_the_key(tmp_path):
    """Driven rather than read: the argument list the tracker was handed is
    the close and the key, and nothing that could carry a reference to the
    pull request."""
    environment, ledger = a_tracker(tmp_path, issues={BRIEF_KEY: "OPEN"})
    root = a_target(tmp_path, "bare-close", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: an_artifact(brief_key=BRIEF_KEY)})

    result = drive(root, INVENTED_PREFIX + STORY_ID, environment)

    assert result.returncode == 0, result.stderr
    close = [call for call in calls(ledger) if call[:2] == ["issue", "close"]]
    assert close == [["issue", "close", BRIEF_KEY]], close


def test_a_copy_whose_close_carries_a_comment_is_reported_by_that_reading(
        tmp_path):
    """The control for the drive above: the same assertion, against a copy of
    the script differing only in the comment its close carries, reports it —
    so a bare argument list is a property of the shipped script rather than of
    a stub that discards what it was given."""
    environment, ledger = a_tracker(tmp_path, issues={BRIEF_KEY: "OPEN"})
    root = a_target(tmp_path, "commenting", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: an_artifact(brief_key=BRIEF_KEY)})
    installed = root / SCRIPT_REL
    source = installed.read_text(encoding="utf-8")
    commented = source.replace('gh issue close "$brief_key"',
                               'gh issue close "$brief_key" --comment "merged"')
    assert commented != source
    installed.write_text(commented, encoding="utf-8")

    result = drive(root, INVENTED_PREFIX + STORY_ID, environment)

    assert result.returncode == 0, result.stderr
    close = [call for call in calls(ledger) if call[:2] == ["issue", "close"]]
    assert close != [["issue", "close", BRIEF_KEY]], close
    assert linking_lines(commented)


# --------------------------------------------------------------------------
# 5. The two configured values are read, and written down nowhere else
# --------------------------------------------------------------------------


#: What this target configures. Read rather than written: this module names
#: neither value, and every assertion about them below is made against what
#: `.harness/config.yaml` says today.
THIS_TARGETS_CONFIG = conftest.repository_config()


def test_this_target_configures_both_values_the_script_reads():
    """The companion the searches below need: a value that was empty would be
    found nowhere for a reason that has nothing to do with the script."""
    for key in (BRANCH_PREFIX_KEY, STORIES_DIR_KEY):
        value = THIS_TARGETS_CONFIG.get(key)
        assert isinstance(value, str) and value.strip(), key


def writing_down(text: str, value: str) -> list[str]:
    """Every line of a file that writes a configured value down.

    The one reader for this question: the assertion about the shipped files
    and the control below are the same call, so a reader that had stopped
    seeing anything — a value it never looks for, a split that yields no
    lines — turns the control red instead of turning the assertion green.
    """
    return [line for line in text.splitlines() if value in line]


@pytest.mark.parametrize("key", [BRANCH_PREFIX_KEY, STORIES_DIR_KEY])
@pytest.mark.parametrize("shipped", [SCRIPT, DEFINITION],
                         ids=lambda path: path.name)
def test_neither_configured_value_is_written_down_in_the_change(shipped: Path,
                                                                key: str):
    """A configured value is read by what depends on it and never written into
    it a second time, which is the rule story-134 established."""
    value = THIS_TARGETS_CONFIG[key]
    text = shipped.read_text(encoding="utf-8")
    assert writing_down(text, value) == [], (shipped.name, key)
    # And the key itself is named, so the file is silent about the value
    # because it reads it rather than because it is silent about the whole
    # subject. The definition names neither, which the script's reads cover.
    if shipped == SCRIPT:
        assert key in text, key


@pytest.mark.parametrize("key", [BRANCH_PREFIX_KEY, STORIES_DIR_KEY])
def test_the_same_reader_reports_a_fallback_planted_in_a_copy(key: str,
                                                              tmp_path: Path):
    """The negative control for the silence above.

    A throwaway copy of the shipped script with a default for the value
    appended — the second writing-down the rule forbids — is read back off
    disk by the reader the assertion above runs, which must report the line it
    was planted on. The copy is read rather than the text held in memory, and
    the value is interpolated from the configuration rather than spelled here,
    so this control writes nothing down either.
    """
    value = THIS_TARGETS_CONFIG[key]
    copy = tmp_path / SCRIPT.name
    shutil.copy2(SCRIPT, copy)
    # The copy is the shipped script, so the reader is silent about it too:
    # what the planting changes is the one line, and this is what says so.
    assert writing_down(copy.read_text(encoding="utf-8"), value) == []

    fallback = f'{key}="${{{key}:-{value}}}"'
    copy.write_text(copy.read_text(encoding="utf-8") + f"\n{fallback}\n",
                    encoding="utf-8")

    assert writing_down(copy.read_text(encoding="utf-8"), value) == [fallback]


def test_the_script_follows_the_configuration_it_was_installed_beside(tmp_path,
                                                                      tracker):
    """The strong form of "it reads the configuration": the same branch and the
    same artifact, in two targets configuring different values, resolve in one
    and are not a story's branch at all in the other."""
    environment, _ = tracker
    artifact = an_artifact(brief_key=BRIEF_KEY)
    mine = a_target(tmp_path, "configured-one", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: artifact})
    theirs = a_target(tmp_path, "configured-other", prefix=OTHER_PREFIX,
                      stories_dir=OTHER_STORIES_DIR,
                      artifacts={STORY_ID: artifact})
    assert INVENTED_PREFIX != OTHER_PREFIX

    branch = INVENTED_PREFIX + STORY_ID
    resolved = drive(mine, branch, environment, dry_run=True)
    assert resolved.returncode == 0, resolved.stderr
    assert resolved.stdout.strip() == BRIEF_KEY

    refused = drive(theirs, branch, environment, dry_run=True)
    assert refused.returncode == 0
    assert refused.stdout.strip() == ""
    assert OTHER_PREFIX in refused.stderr

    # And the stories directory is followed too: the branch this second target
    # does recognise finds its artifact under the directory it declares.
    also = drive(theirs, OTHER_PREFIX + STORY_ID, environment, dry_run=True)
    assert also.returncode == 0, also.stderr
    assert also.stdout.strip() == BRIEF_KEY


# --------------------------------------------------------------------------
# 6. The workflow definition: the trigger, the guard, the permission, the ref
# --------------------------------------------------------------------------
#
# Every reader below takes the text as a parameter, so the reader that is
# silent about the shipped definition is the one run over a copy with the
# declaration removed. A reader that had stopped seeing anything would be
# indistinguishable from a definition that declares the right thing.


def block_under(text: str, key: str) -> dict[str, str]:
    """The mapping written immediately under a top-level `key:`.

    Small enough to be obvious and confined to the one shape this definition
    writes — a top-level key whose children are `name: value` at one
    indentation. Nothing here is a YAML parser, and nothing depends on one.
    """
    lines = text.splitlines()
    found: dict[str, str] = {}
    inside = False
    for line in lines:
        if re.match(rf"^{re.escape(key)}:\s*$", line):
            inside = True
            continue
        if inside:
            if not line.strip():
                continue
            if not line.startswith((" ", "\t")):
                break
            named = re.match(r"^\s+([\w.-]+):\s*(.*)$", line)
            if named:
                found[named.group(1)] = named.group(2).strip()
    return found


def trigger_types(text: str) -> list[str]:
    """The event types the `pull_request` trigger names."""
    found = re.search(
        r"(?m)^on:\s*$\n\s+pull_request:\s*$\n\s+types:\s*\[?([^\]\n]*)\]?\s*$",
        text)
    if not found:
        return []
    return [one.strip() for one in found.group(1).split(",") if one.strip()]


def job_guards(text: str) -> list[str]:
    """Every `if:` condition a job or step declares."""
    return [line.split(":", 1)[1].strip()
            for line in text.splitlines() if re.match(r"^\s+if:\s", line)]


def checkout_ref(text: str) -> str | None:
    """What the checkout step is pinned to, if it is pinned to anything."""
    found = re.search(r"uses:\s*actions/checkout@[^\n]*\n\s+with:\s*\n\s+ref:\s*(.+)",
                      text)
    return found.group(1).strip() if found else None


def run_lines(text: str) -> list[str]:
    return [line.split(":", 1)[1].strip()
            for line in text.splitlines() if re.match(r"^\s+run:\s", line)]


def test_the_definition_is_triggered_by_a_pull_request_closing():
    assert trigger_types(DEFINITION_SOURCE) == ["closed"]


def test_the_job_is_guarded_by_the_pull_request_having_merged():
    """A pull request closed without merging runs nothing at all."""
    guards = job_guards(DEFINITION_SOURCE)
    assert len(guards) == 1, guards
    assert "github.event.pull_request.merged" in guards[0]
    assert re.search(r"==\s*true", guards[0]), guards[0]


def test_the_definition_declares_the_permission_the_close_needs():
    """The default token cannot close an issue, so a token that cannot is a
    test failure here rather than a runtime one on a merged pull request."""
    declared = block_under(DEFINITION_SOURCE, "permissions")
    assert declared.get("issues") == "write", declared
    assert declared.get("contents") == "read", declared


def test_the_checkout_is_pinned_to_the_merge_commit():
    """So the story artifact the script reads is the one this merge landed
    rather than whatever a default ref would give."""
    ref = checkout_ref(DEFINITION_SOURCE)
    assert ref is not None
    assert "github.event.pull_request.merge_commit_sha" in ref


def test_the_definition_invokes_the_script_this_story_ships():
    """One step, invoking a file that exists and can be executed."""
    invocations = [line for line in run_lines(DEFINITION_SOURCE)
                   if SCRIPT_REL in line]
    assert len(invocations) == 1, run_lines(DEFINITION_SOURCE)
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK), SCRIPT


#: The expression the definition takes the merged branch name from. A name
#: GitHub defines rather than one this module invents, which is why it can be
#: written here: what the rule forbids writing down is what this deployment
#: configured, and this is the event payload's own spelling.
HEAD_REF = "github.event.pull_request.head.ref"


def test_the_branch_the_step_hands_the_script_is_the_pull_requests_head():
    assert HEAD_REF in DEFINITION_SOURCE


def branch_variable(text: str) -> str | None:
    """The environment variable a step puts the merged head branch into."""
    found = re.search(
        rf"(?m)^\s+([A-Z][A-Z0-9_]*):\s*\$\{{\{{\s*{re.escape(HEAD_REF)}\s*\}}\}}\s*$",
        text)
    return found.group(1) if found else None


def interpolating_run_lines(text: str) -> list[str]:
    """Every command line the definition builds by interpolating into it."""
    return [line for line in run_lines(text) if "${{" in line]


def test_the_head_branch_reaches_the_script_through_the_environment():
    """A branch name is somebody else's text, and a runner holding a token
    with `issues: write` is where that matters: a name written to look like
    shell reaches the step as the value of a variable, which is a string, and
    not as characters pasted into the command line before it runs. The
    controls are below."""
    variable = branch_variable(DEFINITION_SOURCE)
    assert variable is not None, DEFINITION_SOURCE

    invocations = [line for line in run_lines(DEFINITION_SOURCE)
                   if SCRIPT_REL in line]
    assert len(invocations) == 1, invocations
    assert f'"${variable}"' in invocations[0], invocations
    assert interpolating_run_lines(DEFINITION_SOURCE) == []


def test_the_same_readers_report_a_copy_written_the_interpolated_way(tmp_path):
    """The control for the assertion above.

    A copy of the shipped definition with the environment entry removed and
    the ref interpolated straight into the command line — the form the comment
    in that file exists to warn against — written out and read back by the
    same two readers, which must report both halves of it. Silence over the
    shipped definition is then that file's own shape rather than a reader
    matching nothing.
    """
    variable = branch_variable(DEFINITION_SOURCE)
    interpolated = DEFINITION_SOURCE.replace(f'"${variable}"',
                                             "${{ " + HEAD_REF + " }}")
    assert interpolated != DEFINITION_SOURCE
    copy = tmp_path / DEFINITION.name
    copy.write_text(re.sub(rf"(?m)^\s+{variable}:.*\n", "", interpolated),
                    encoding="utf-8")
    written = copy.read_text(encoding="utf-8")

    assert branch_variable(written) is None
    reported = interpolating_run_lines(written)
    assert [line for line in reported if SCRIPT_REL in line] == reported
    assert reported


#: A story id written to look like shell: command substitution in both
#: spellings, a statement separator, a background operator and a subshell.
#: What a hostile branch name on a merged pull request would carry.
A_STORY_ID_WRITTEN_LIKE_SHELL = "invented-$(echo one)-`echo two`;&()"


def test_a_branch_written_to_look_like_shell_is_a_string_the_script_is_given(
        tmp_path, tracker):
    """The other half of the defence, driven rather than read.

    The definition hands the name over as a variable's value; this is what the
    script does with such a name once it has it. The name is carried whole
    into the sentence the script prints, which is a name it passed around as
    data — a name any of these characters had been acted on would have come
    back out of the shell changed, shortened or not at all.
    """
    for character in "$`();&":
        assert character in A_STORY_ID_WRITTEN_LIKE_SHELL, character

    environment, ledger = tracker
    root = a_target(tmp_path, "shell-shaped-branch", prefix=INVENTED_PREFIX,
                    stories_dir=INVENTED_STORIES_DIR,
                    artifacts={STORY_ID: an_artifact(brief_key=BRIEF_KEY)})

    result = drive(root, INVENTED_PREFIX + A_STORY_ID_WRITTEN_LIKE_SHELL,
                   environment)

    assert result.returncode == 0, result.stderr
    assert (f"{INVENTED_STORIES_DIR}/{A_STORY_ID_WRITTEN_LIKE_SHELL}.yaml"
            in result.stderr), result.stderr
    assert calls(ledger) == []


#: Each declaration the definition makes, and a rewriting of the shipped text
#: that removes exactly that one. The reader is then run over the rewriting,
#: where it must report the declaration gone — which is what makes the
#: assertion that reads that declaration a statement about this file rather
#: than about a reader that matches nothing.
@pytest.mark.parametrize("removing,reader", [
    pytest.param(r"(?m)^\s+types:.*\n", trigger_types, id="the-closed-type"),
    pytest.param(r"(?m)^\s+if:.*\n", job_guards, id="the-merged-guard"),
    pytest.param(r"(?m)^\s+ref:.*\n", checkout_ref, id="the-merge-commit"),
])
def test_each_reader_reports_the_declaration_gone_from_a_copy(removing, reader):
    stripped = re.sub(removing, "", DEFINITION_SOURCE)
    assert stripped != DEFINITION_SOURCE, removing
    assert reader(DEFINITION_SOURCE), removing
    assert not reader(stripped), removing


def test_the_permission_reader_reports_a_copy_that_asks_for_no_permission():
    """The same control for the permissions block, which is a mapping rather
    than a list: a copy declaring no issues permission is reported."""
    stripped = re.sub(r"(?m)^\s+issues:\s*write\s*\n", "", DEFINITION_SOURCE)
    assert stripped != DEFINITION_SOURCE
    assert block_under(stripped, "permissions").get("issues") is None
    # And the rest of the block survives the rewriting, so the reader is
    # reporting the one declaration rather than a parse that fell over.
    assert block_under(stripped, "permissions").get("contents") == "read"


def test_the_block_reader_stops_at_the_end_of_the_block():
    """The reader's own control: it reads the mapping under the key it was
    given and not whatever follows it at column zero."""
    text = ("permissions:\n"
            "  contents: read\n"
            "  issues: write\n"
            "jobs:\n"
            "  a-job:\n"
            "    runs-on: ubuntu-latest\n")
    assert block_under(text, "permissions") == {"contents": "read",
                                                "issues": "write"}
    assert block_under(text, "nothing-declares-this") == {}


# --------------------------------------------------------------------------
# 7. This module's own standing in the suite
# --------------------------------------------------------------------------


def test_this_module_is_declared_where_a_live_artifact_reader_must_be():
    """It reads what this deployment ships, which is its whole subject, so it
    is declared — and the declaration is shown to be about something: the scan
    reports this module, and the table's equality is asserted in both
    directions by the module that holds it."""
    import test_baseline_honesty as scan

    name = Path(__file__).name
    assert scan.live_artifact_reads(
        Path(__file__).read_text(encoding="utf-8"), name), (
        "this module resolves no live artifact, so its declaration is stale")
    assert name in scan.DECLARED_LIVE_ARTIFACT_READERS
    assert scan.DECLARED_LIVE_ARTIFACT_READERS[name].strip()
