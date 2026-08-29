"""Independent validation for story-040: no target-stack literal in harness
source, held by a scan rather than by a paragraph.

Written from the story's acceptance criteria rather than from the
implementation. The story fixes no tie. Its deliverable is a scan plus two
lists, so what is validated here is the scan's *reach* and the lists'
*exactness*, not any repair:

  * **the two lists.** `TEMPORARY_TIES` and `PERMANENT_MENTIONS` below are
    asserted to equal exactly what `harness_source.scan()` reports against
    this repository, in both directions, and to classify every reported
    entry exactly once. Each entry is keyed by repository-relative path and
    the exact text of the matched line rather than by a line number, so an
    unrelated edit above a tie does not churn the list and look like the
    burn-down.
  * **the burn-down, now complete.** `TEMPORARY_TIES` is empty: the audit's
    five ties are all repaired — two in `orchestration/story_coordinator.py`
    by the-interpreter-is-not-assumed-to-be-python, and the remaining three
    in `workflows/story-workflow.json` and `prompts/story-tester.md` by
    the-test-location-comes-from-configuration. What is asserted is that the
    scan reports *nothing* in either repaired file, by running the scan
    rather than by reading the empty list, with each historical tie replanted
    in a throwaway copy and reported there — a scan that cannot see the ties
    that motivated it has not been shown to work.
  * **the matcher.** The four boundary cases the story names are
    constructed and run through the real `scan`, not reasoned about.
  * **the stated limits.** Read out of `orchestration/harness_source.py`'s
    own docstring, and then *exercised* — a language absent from
    `STACK_TOKENS` really is invisible, and the layout half really cannot
    see `orchestration/` — so the limits are known to match the behaviour
    rather than to overstate or undersell it.

Every absence asserted here carries a demonstration that it can fail, and
every demonstration is built against a throwaway repository root rather
than by editing this one:

  * "the lists equal what the scan reports" sits beside a throwaway root
    with a tie planted in a file that is clean today, where the same
    comparison reports an unexpected entry, and beside one with a known tie
    removed, where it reports a stale entry;
  * "every reported entry is classified exactly once" sits beside an entry
    handed to the same check on both lists, and beside a planted tie that is
    on neither, both of which it reports;
  * "the coordinator carries nothing but its one permanent mention" sits
    beside a coordinator tie replanted in a throwaway copy, which the same
    checks report;
  * "nothing under `.harness/` or `tests/` is reported" sits beside ties
    planted in both, and beside the same tie planted in a scanned directory,
    which is reported;
  * "the layout half does not read `orchestration/`" sits beside the same
    literal planted in a target-facing file, which is reported;
  * "the declaring module is exempt" sits beside a second file carrying its
    tokens, which is reported;
  * "every permanent mention carries a reason" sits beside an entry with
    none, which the same check reports;
  * "the module states its limits" sits beside a rendering of that module
    with each stated limit stripped out, which the same check reports;
  * "the repaired files carry no mention at all" sits beside each of the
    three historical ties replanted in a throwaway copy of the file it used
    to sit in, which the same scan reports and which turns the same
    two-directional list comparison red.

Nothing here invokes a model, and nothing here writes to this repository.
"""
import ast
import shutil
from pathlib import Path

import pytest

import harness_source

REPO_ROOT = Path(harness_source.__file__).resolve().parents[1]
DECLARING_MODULE = "orchestration/harness_source.py"

#: The files the burn-down repaired last, and which the scan must now report
#: nothing at all in. `orchestration/story_coordinator.py` was repaired ahead
#: of them by the-interpreter-is-not-assumed-to-be-python, and is asserted
#: separately below because one permanent mention legitimately survives there.
REPAIRED_FILES = (
    "workflows/story-workflow.json",
    "prompts/story-tester.md",
)

#: The exact lines that used to sit in those files, kept verbatim so each can
#: be replanted. This is what makes "the scan reports nothing there" an
#: assertion rather than a claim about where the scan happens to be looking:
#: put the tie back and the same scan reports it.
HISTORICAL_TIES = (
    ("workflows/story-workflow.json", '      "may_not_create": ["tests/"],'),
    ("prompts/story-tester.md",
     "New tests belong in tests/ and become permanent repository assets."),
    ("prompts/story-tester.md", "shared resolution in `tests/conftest.py`."),
)


# ==========================================================================
# The two lists
#
# An entry is (repository-relative path, exact text of the matched line).
# Keyed on the text rather than the line number so that an unrelated edit
# above a tie does not churn the list and read as burn-down progress.
#
# The split is the whole point. TEMPORARY_TIES is a burn-down: a mention
# that names or assumes a *target's* stack or layout, which the harness has
# no business saying, and which the-interpreter-is-not-assumed-to-be-python
# and the-test-location-comes-from-configuration exist to remove. This list
# reaching empty is their completion signal. PERMANENT_MENTIONS is a
# judgement on the record: a mention that describes *this harness's own*
# implementation language, or that explicitly says a target need not be
# Python, is the opposite of a tie and will never be removed. Merged, a list
# that stops shrinking could not be told from work that finished.
# ==========================================================================


TEMPORARY_TIES: frozenset[tuple[str, str]] = frozenset()

#: Empty, and that is the completion signal for the whole burn-down.
#: the-interpreter-is-not-assumed-to-be-python removed every
#: `orchestration/story_coordinator.py` entry — the version probe, the
#: record's interpreter-shaped fields, and the retired configuration key with
#: the prose explaining it — together with every entry in the three schemas it
#: names. the-test-location-comes-from-configuration removed the last three:
#: the workflow's create restriction, which is now the token `{{tests_dir}}`
#: resolved from configuration when the definition loads, and the two lines of
#: prose in the tester prompt, which now render the configured location and
#: describe the shared baseline resolution without naming the file this
#: repository keeps it in.
#:
#: An empty list is exactly the shape an assertion goes vacuous against, so no
#: assertion below rests on reading it. `list_problems` and
#: `classification_problems` both take it as one of two lists and would report
#: an entry landing on neither, which is how a new tie surfaces; what is
#: asserted about this repository is that the scan reports nothing outside
#: `PERMANENT_MENTIONS`, and every one of those assertions carries a control
#: that replants a historical tie, or hands the same check an entry it must
#: object to, and observes it reported.


#: Each entry carries a one-line reason saying why that mention is not a
#: tie, so the permanent half is a judgement on the record rather than a
#: suppression list.
PERMANENT_MENTIONS: dict[tuple[str, str], str] = {
    ('hooks/bash_guard.py',
     '#!/usr/bin/env python3'):
        "the harness's own hook is a Python program and says so to the kernel",
    ('scripts/l5-assist',
     '#!/usr/bin/env python3'):
        "the harness's own entry point is a Python program and says so to the kernel",
    ('scripts/l5-init',
     '#!/usr/bin/env python3'):
        "the harness's own entry point is a Python program and says so to the kernel",
    ('scripts/l5-plan',
     '#!/usr/bin/env python3'):
        "the harness's own entry point is a Python program and says so to the kernel",
    ('scripts/l5-run',
     '#!/usr/bin/env python3'):
        "the harness's own entry point is a Python program and says so to the kernel",
    ('scripts/l5-status',
     '#!/usr/bin/env python3'):
        "the harness's own entry point is a Python program and says so to the kernel",
    ('scripts/l5-sync',
     '#!/usr/bin/env python3'):
        "the harness's own entry point is a Python program and says so to the kernel",
    ('orchestration/story_coordinator.py',
     '    count that cannot be spent, and `True` is not a budget however much Python'):
        "names the language this validation itself is written in, explaining why a bool is refused",
    ('orchestration/story_parser.py',
     '- No type coercion. Every scalar parses to a Python ``str``; ``42`` and'):
        "a fact about what this parser returns to its own callers, not about any target",
}


#: The mentions that are honest sentences rather than ties. A rule that
#: cannot tell these from a tie gets turned off within two stories.
LEGITIMATE_MENTIONS = (
    ("orchestration/story_parser.py", "Every scalar parses to a Python"),
    ("orchestration/story_coordinator.py",
     "is not a budget however much Python"),
)


# ==========================================================================
# The comparison, written once so the controls drive the same code
# ==========================================================================


def reported_entries(findings) -> set[tuple[str, str]]:
    """What the scan reports, as the (path, line text) pairs the lists use.

    A line matching both rules yields two findings carrying the same pair;
    the lists classify a *line*, not a rule, so the pair collapses to one
    entry here.
    """
    return {(finding.path, finding.line) for finding in findings}


def list_problems(findings) -> list[str]:
    """Where the two lists and what the scan reports disagree, both ways.

    Returned rather than asserted so that a control can require the same
    comparison to report something. The message names the file and line of
    every unexpected or stale entry.
    """
    reported = reported_entries(findings)
    listed = set(TEMPORARY_TIES) | set(PERMANENT_MENTIONS)
    numbers = {(f.path, f.line): f.line_number for f in findings}
    problems = []
    for entry in sorted(reported - listed):
        problems.append(
            f"unexpected: {entry[0]}:{numbers[entry]} is a new tie on no list "
            f"-- {entry[1].strip()!r}"
        )
    for entry in sorted(listed - reported):
        problems.append(
            f"stale: {entry[0]} no longer carries the listed line, so its "
            f"entry must come off the list -- {entry[1].strip()!r}"
        )
    return problems


def classification_problems(findings, temporary=None,
                            permanent=None) -> list[str]:
    """Every reported entry the two lists do not classify exactly once.

    Exclusivity and coverage are one question -- the lists partition what the
    scan reports -- and asking it this way keeps it answerable while
    `TEMPORARY_TIES` is empty: an entry on neither list is a new tie, which is
    a failure this repository can still produce. The lists are parameters so a
    control can hand the same check an entry sitting on both.
    """
    temporary = set(TEMPORARY_TIES if temporary is None else temporary)
    permanent = set(PERMANENT_MENTIONS if permanent is None else permanent)
    problems = []
    for entry in sorted(reported_entries(findings)):
        on = [name for name, listed in (("temporary", temporary),
                                        ("permanent", permanent))
              if entry in listed]
        if len(on) == 1:
            continue
        where = " and ".join(on) if on else "neither list"
        problems.append(
            f"{entry[0]} is on {where}, so its classification says nothing "
            f"-- {entry[1].strip()!r}"
        )
    return problems


def reasonless(mentions: dict) -> list[tuple[str, str]]:
    """Every permanent entry whose reason is missing or not one line."""
    return sorted(entry for entry, reason in mentions.items()
                  if not isinstance(reason, str) or not reason.strip()
                  or "\n" in reason)


def findings_for(findings, path: str, fragment: str) -> list:
    return [f for f in findings if f.path == path and fragment in f.line]


@pytest.fixture(scope="module")
def here():
    """What the scan reports against this repository. Resolved once."""
    return harness_source.scan()


# ==========================================================================
# The throwaway root: the same scan, run somewhere it is safe to break
# ==========================================================================


def build_throwaway(root: Path) -> Path:
    """A copy of the harness's source directories, plus the two the scan
    must leave alone, in a root a test may vandalize.

    The copy is faithful, so the same lists apply to it — which is what lets
    a control plant one violation and attribute the resulting failure to
    that plant alone.
    """
    root.mkdir(parents=True)
    for name in harness_source.HARNESS_SOURCE_DIRS:
        shutil.copytree(REPO_ROOT / name, root / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    plant(root, ".harness/docs/ARCHITECTURE.md",
          "# A target's own configuration lives here.\n")
    plant(root, "tests/test_placeholder.py", "def test_it():\n    assert True\n")
    return root


def plant(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def append(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def pristine(tmp_path_factory) -> Path:
    return build_throwaway(tmp_path_factory.mktemp("pristine") / "repo")


@pytest.fixture
def throwaway(pristine, tmp_path) -> Path:
    """A fresh vandalizable copy per test, so one plant is one failure."""
    root = tmp_path / "repo"
    shutil.copytree(pristine, root)
    return root


def test_the_throwaway_root_reports_what_this_repository_does(throwaway, here):
    """The controls below are only worth something if the copy is a faithful
    stand-in: same code path, same findings, before anything is planted."""
    assert reported_entries(harness_source.scan(throwaway)) \
        == reported_entries(here)
    assert not list_problems(harness_source.scan(throwaway))


# ==========================================================================
# The five ties that motivated the story, all now repaired
# ==========================================================================


def test_the_burn_down_is_complete(here):
    """The completion signal, stated as a fact about the scan rather than
    about the list: nothing this repository's harness source says is a tie."""
    assert TEMPORARY_TIES == frozenset()
    assert reported_entries(here) <= set(PERMANENT_MENTIONS)


@pytest.mark.parametrize("repaired", REPAIRED_FILES)
def test_the_repaired_files_carry_no_mention_at_all(here, repaired):
    """Read off the scan rather than off the empty list: the workflow no
    longer names a directory in the target and the tester prompt no longer
    names a layout or a test-framework filename."""
    assert [f for f in here if f.path == repaired] == []


@pytest.mark.parametrize("path,tie", HISTORICAL_TIES,
                         ids=[f"{p}:{t[:28]}" for p, t in HISTORICAL_TIES])
def test_replanting_a_historical_tie_is_reported_by_the_same_scan(throwaway,
                                                                  path, tie):
    """The control for the two assertions above, which are both absences.

    Each historical tie is put back verbatim into a throwaway copy of the file
    it used to sit in, and the same scan has to report it and the same
    two-directional comparison has to call it unexpected. Without this, "the
    scan reports nothing there" would be satisfied just as happily by a scan
    that had stopped reading the file at all.
    """
    assert not [f for f in harness_source.scan(throwaway) if f.path == path], \
        f"{path} still carries a mention, so the replant proves nothing"

    append(throwaway, path, "\n" + tie + "\n")

    assert [f for f in harness_source.scan(throwaway) if f.path == path]
    problems = list_problems(harness_source.scan(throwaway))
    assert len(problems) == 1, problems
    assert problems[0].startswith(f"unexpected: {path}:")


#: The three schemas the-interpreter-is-not-assumed-to-be-python names.
#: Every mention in them burned down with it, so the scan now reports
#: nothing at all in any of the three.
REPAIRED_SCHEMAS = (
    "schemas/clean-clone-result.schema.json",
    "schemas/revert-check-result.schema.json",
    "schemas/harness-config.schema.json",
)


@pytest.mark.parametrize("schema", REPAIRED_SCHEMAS)
def test_the_repaired_schemas_carry_no_mention_at_all(here, schema):
    """Read off the scan rather than off the diff: an entry taken off the
    temporary list for a mention still in source would be reported here."""
    assert [f for f in here if f.path == schema] == []


COORDINATOR = "orchestration/story_coordinator.py"

#: One of the ties the-interpreter-is-not-assumed-to-be-python removed from
#: the coordinator, kept verbatim so the absence below can be shown to fail.
HISTORICAL_COORDINATOR_TIE = "    version = platform.python_version()"


def test_the_coordinator_carries_no_temporary_tie(here):
    """Every grandfathered tie in the coordinator is repaired. What the scan
    still reports there is the one permanent mention describing this
    harness's own implementation language, which is the opposite of a tie."""
    coordinator = [f for f in here if f.path == COORDINATOR]
    assert coordinator, \
        f"the scan reports nothing at all in {COORDINATOR}, so it is not " \
        "looking there and this proves nothing"
    assert not classification_problems(coordinator)
    assert reported_entries(coordinator) <= set(PERMANENT_MENTIONS)


def test_a_tie_replanted_in_the_coordinator_is_reported_as_one(throwaway):
    """The control for the absence above: the version probe put back verbatim
    into a throwaway copy, where the same scan reports it, it lands on neither
    list, and it is no longer inside `PERMANENT_MENTIONS`."""
    append(throwaway, COORDINATOR, "\n" + HISTORICAL_COORDINATOR_TIE + "\n")

    coordinator = [f for f in harness_source.scan(throwaway)
                   if f.path == COORDINATOR]
    problems = classification_problems(coordinator)
    assert len(problems) == 1, problems
    assert problems[0].startswith(f"{COORDINATOR} is on neither list")
    assert not reported_entries(coordinator) <= set(PERMANENT_MENTIONS)


@pytest.mark.parametrize("path,fragment", LEGITIMATE_MENTIONS)
def test_a_legitimate_mention_is_permanent_and_never_a_tie(here, path,
                                                           fragment):
    """The docstring saying this parser's scalars are Python strings, and the
    one explaining why a bool is refused as a budget: both describe the
    language this harness is written in, not any target's."""
    matches = findings_for(here, path, fragment)
    assert matches, (path, fragment)
    assert not classification_problems(matches)
    for finding in matches:
        entry = (finding.path, finding.line)
        assert entry in PERMANENT_MENTIONS, entry


# ==========================================================================
# The two lists, exact in both directions
# ==========================================================================


def test_the_two_lists_are_exactly_what_the_scan_reports(here):
    """Both directions: no tie is reported that no list claims, and no list
    entry survives its line disappearing from the source."""
    assert not list_problems(here), "\n".join(list_problems(here))


def test_every_reported_entry_is_classified_exactly_once(here):
    """What "the two lists share no entry" became once `TEMPORARY_TIES` went
    empty: intersecting an empty set is a question with one possible answer,
    while partitioning what the scan reports is one this repository can still
    fail -- a mention that is a tie and a permanent mention at once, or a new
    one that is neither."""
    assert not classification_problems(here), \
        "\n".join(classification_problems(here))


def test_an_entry_on_both_lists_is_reported_by_the_same_check(here):
    """The exclusivity half of the control: a mention the scan really reports,
    handed to the same check as temporary *and* permanent."""
    both = sorted(reported_entries(here) & set(PERMANENT_MENTIONS))[0]
    problems = classification_problems(here, temporary={both})
    assert len(problems) == 1, problems
    assert problems[0].startswith(f"{both[0]} is on temporary and permanent")


def test_an_entry_on_neither_list_is_reported_by_the_same_check(throwaway):
    """The coverage half: a tie planted in a file that is clean today lands on
    neither list, and the same check reports it."""
    clean = "prompts/documenter.md"
    assert not [f for f in harness_source.scan(throwaway) if f.path == clean], \
        f"{clean} is no longer clean, so it cannot serve as the plant site"

    append(throwaway, clean, "\nThe target is built with gradle.\n")

    problems = classification_problems(harness_source.scan(throwaway))
    assert len(problems) == 1, problems
    assert problems[0].startswith(f"{clean} is on neither list")


def test_every_permanent_mention_carries_a_reason():
    assert not reasonless(PERMANENT_MENTIONS)


def test_an_entry_with_no_reason_is_reported_by_the_same_check():
    """The control for the assertion above: the check has to be able to see
    a missing reason, or it is a claim about what was typed rather than a
    check on it."""
    entry = ("orchestration/somewhere.py", "python")
    assert reasonless({**PERMANENT_MENTIONS, entry: ""}) == [entry]
    assert reasonless({**PERMANENT_MENTIONS, entry: "  "}) == [entry]
    assert reasonless({**PERMANENT_MENTIONS, entry: "two\nlines"}) == [entry]


def test_a_planted_tie_in_a_clean_file_is_reported_and_turns_the_lists_red(
    throwaway,
):
    """The control for the equality assertion, in the direction that
    matters: a tie that lands tomorrow cannot join a list quietly."""
    clean = "prompts/story-implementer.md"
    assert not [f for f in harness_source.scan(throwaway) if f.path == clean], \
        f"{clean} is no longer clean, so it cannot serve as the plant site"

    append(throwaway, clean, "\nRun pytest before you finish.\n")

    planted = [f for f in harness_source.scan(throwaway) if f.path == clean]
    assert [f.token.lower() for f in planted] == ["pytest"]
    problems = list_problems(harness_source.scan(throwaway))
    assert len(problems) == 1, problems
    assert problems[0].startswith("unexpected: prompts/story-implementer.md:")


def test_a_list_entry_left_behind_after_its_tie_is_removed_turns_the_lists_red(
    throwaway,
):
    """The other direction: a file that stops violating must be taken off
    the list, or the burn-down counts work that is already done."""
    removed = ('scripts/l5-status', '#!/usr/bin/env python3')
    assert removed in PERMANENT_MENTIONS

    text = (throwaway / removed[0]).read_text(encoding="utf-8")
    assert removed[1] + "\n" in text
    plant(throwaway, removed[0], text.replace(removed[1] + "\n", ""))

    problems = list_problems(harness_source.scan(throwaway))
    assert len(problems) == 1, problems
    assert problems[0].startswith("stale: scripts/l5-status")


# ==========================================================================
# The matcher, on the four cases the story constructs
# ==========================================================================


BOUNDARY_CASES = (
    ("clean_clone_python = config.get('clean_clone_python')", True),
    ("version = platform.python_version()", True),
    ("results = pipeline(stages)", False),
    ("stream = pipe(left, right)", False),
)


@pytest.mark.parametrize("line,expected", BOUNDARY_CASES,
                         ids=[c[0].split(" ")[0] for c in BOUNDARY_CASES])
def test_a_stack_token_is_bounded_by_a_non_alphanumeric(throwaway, line,
                                                        expected):
    """Constructed and run, not reasoned about. `_` and `.` are not letters
    or digits, so an identifier-embedded token is seen; `e` is, so `pipeline`
    and `pipe` are not."""
    plant(throwaway, "orchestration/boundary_case.py", line + "\n")
    reported = [f for f in harness_source.scan(throwaway)
                if f.path == "orchestration/boundary_case.py"]
    assert bool(reported) is expected, (line, reported)


# ==========================================================================
# What the scan does not read, and why
# ==========================================================================


@pytest.mark.parametrize("relative", (
    ".harness/docs/notes.md",
    ".harness/config.yaml",
    "tests/test_planted.py",
))
def test_a_tie_under_harness_or_tests_is_not_reported(throwaway, relative):
    """A target's ties belong in `.harness/`, and this repository's own suite
    is legitimately full of Python. The positive control is beside it: the
    identical text in a scanned directory *is* reported."""
    tie = "run pytest in tests/ with the configured python3\n"
    plant(throwaway, relative, tie)
    assert not [f for f in harness_source.scan(throwaway)
                if f.path == relative]

    plant(throwaway, "prompts/planted.md", tie)
    assert [f for f in harness_source.scan(throwaway)
            if f.path == "prompts/planted.md"], \
        "the same text is invisible everywhere, so this proves nothing"


def test_the_layout_rule_does_not_read_orchestration(throwaway):
    """The deliberate limit: this harness's own suite is called `tests/`, so
    in Python source a `tests/` literal cannot be told from an honest
    reference to it. The same literal in a target-facing file is reported."""
    literal = 'MAY_NOT_CREATE = ["tests/"]\n'
    plant(throwaway, "orchestration/layout_case.py", literal)
    plant(throwaway, "prompts/layout_case.md", literal)

    found = harness_source.scan(throwaway)
    assert not [f for f in found
                if f.path == "orchestration/layout_case.py"
                and f.rule == harness_source.LAYOUT_RULE]
    assert [f for f in found
            if f.path == "prompts/layout_case.md"
            and f.rule == harness_source.LAYOUT_RULE]


@pytest.mark.parametrize("directory", harness_source.HARNESS_SOURCE_DIRS)
def test_every_declared_source_directory_is_actually_read(throwaway,
                                                          directory):
    """The declaration is only worth what the walk honours: a tie planted in
    each of the eight named directories is reported."""
    relative = f"{directory}/planted_tie.md"
    plant(throwaway, relative, "the target is built with gradle\n")
    assert [f for f in harness_source.scan(throwaway) if f.path == relative]


def test_the_exemption_covers_the_declaring_module_alone(throwaway):
    """Exempt by name and by nothing else: a second file carrying the same
    tokens is reported like any other."""
    assert not [f for f in harness_source.scan(throwaway)
                if f.path == DECLARING_MODULE]

    twin = "orchestration/twin_of_harness_source.py"
    plant(throwaway, twin,
          (REPO_ROOT / DECLARING_MODULE).read_text(encoding="utf-8"))
    assert [f for f in harness_source.scan(throwaway) if f.path == twin]
    assert not [f for f in harness_source.scan(throwaway)
                if f.path == DECLARING_MODULE]


# ==========================================================================
# The stated limits: read, then exercised
# ==========================================================================


#: Each limit the module must state, as the fragments that have to appear in
#: its docstring. Lowercased before searching, so casing is not the subject.
STATED_LIMITS = {
    "the token list is a guess": ("stack_tokens", "guess"),
    "the token list is incomplete": ("incomplete",),
    "the layout half cannot read orchestration": ("cannot read `orchestration/`",),
    "harness and tests are outside the scan": ("`.harness/` and `tests/` are outside",),
    "the exemption is by name": ("exempt from its own scan by name",),
}


def module_docstring(text: str) -> str:
    return ast.get_docstring(ast.parse(text)) or ""


def unstated_limits(docstring: str) -> list[str]:
    lowered = docstring.lower()
    return sorted(label for label, fragments in STATED_LIMITS.items()
                  if not all(fragment in lowered for fragment in fragments))


def test_the_module_states_what_it_does_not_catch():
    """Read out of the module rather than eyeballed."""
    docstring = module_docstring(
        (REPO_ROOT / DECLARING_MODULE).read_text(encoding="utf-8"))
    assert docstring
    assert not unstated_limits(docstring)


@pytest.mark.parametrize("label", sorted(STATED_LIMITS))
def test_a_missing_limit_is_reported_by_the_same_check(label):
    """The control: each stated limit stripped out of a rendering of the
    docstring, which the same search has to report as missing."""
    docstring = module_docstring(
        (REPO_ROOT / DECLARING_MODULE).read_text(encoding="utf-8"))
    stripped = docstring
    for fragment in STATED_LIMITS[label]:
        stripped = stripped.replace(fragment, "")
        stripped = stripped.replace(fragment.upper(), "")
        stripped = stripped.replace(fragment.capitalize(), "")
    assert label in unstated_limits(stripped)


def test_the_stated_incompleteness_is_true_rather_than_modest(throwaway):
    """The docstring says the token list is a guess that will miss languages
    nobody has tried. Exercised rather than believed: a Ruby and an Elixir
    tie, planted in a scanned directory, really are invisible."""
    for token in ("python", "pytest"):
        assert token in harness_source.STACK_TOKENS
    for token in ("gemfile", "mix.exs", "composer.json"):
        assert token not in harness_source.STACK_TOKENS

    plant(throwaway, "prompts/other_stacks.md",
          "run the target's suite with Gemfile and mix.exs\n")
    assert not [f for f in harness_source.scan(throwaway)
                if f.path == "prompts/other_stacks.md"]


# ==========================================================================
# The declaration is still a declaration: nothing runs it
# ==========================================================================


def readers_of_the_scan(root: Path) -> list[str]:
    """Every module under `root`'s orchestration/ that names the scan."""
    declaring = Path(DECLARING_MODULE).name
    return sorted(module.name
                  for module in (root / "orchestration").glob("*.py")
                  if module.name != declaring
                  and "harness_source" in module.read_text(encoding="utf-8"))


def test_the_coordinator_does_not_run_the_scan():
    """A declaration the suite drives, not run-time behaviour. Nothing under
    orchestration/ reads it, so no run is decided by what it reports."""
    assert not readers_of_the_scan(REPO_ROOT)


def test_a_module_that_did_read_the_scan_is_reported(throwaway):
    """The control: the same search over a root where one module imports it."""
    plant(throwaway, "orchestration/would_run_it.py",
          "import harness_source\n\nharness_source.scan()\n")
    assert readers_of_the_scan(throwaway) == ["would_run_it.py"]
