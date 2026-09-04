"""Independent validation for the brief fetch: asking the same configured
command a second question, by key, and getting one brief back whole.

Written from the story's acceptance criteria rather than from the
implementation. The subjects are kept apart deliberately:

  * **the question.** Observed at the command rather than inferred from the
    call: a fake writes what it read on stdin to a file, and the test parses
    that file and requires it to be one JSON document carrying the key and no
    paths. The dedupe question is driven at the same fake beside it, so
    "neither question changed the other" is an observation of two documents
    rather than a claim about one.

  * **the opacity of the key.** A URL, a repository-relative document path and
    a hex digest are each sent through and read back off the command's own
    transcript, so what is asserted is what arrived rather than what was
    passed.

  * **the whole brief.** A body longer than the per-field bound the dedupe
    answer applies comes back unshortened, and the control beside it is the
    same text put through the dedupe question, where it *is* shortened -- so
    "no bound shortened it" is a fact about this question rather than about a
    bound that never fires.

  * **every way of not having a brief.** An empty key, a whitespace key, no
    configured command, a refused configuration, a command that cannot be
    launched, one that exits non-zero, one that runs past its timeout, one
    whose stdout is prose beside the document, one whose answer fails the
    envelope, one that answered with no brief, one whose brief fails the brief
    schema, and one whose brief names a workflow the harness does not define.
    Each is driven on its own against a fake this module wrote, and the
    reasons are then required to differ from one another: a developer told
    only "no brief" cannot tell a key that did not resolve from a tracker that
    could not be reached.

  * **the bounded run, written once and called twice.** The stdout bound, the
    timeout and the process-group kill are driven past through the fetch, and
    each is required to come back saying what the dedupe query says about the
    same command -- which is what makes "one run" checkable rather than
    asserted.

  * **the query's own totality.** `filed_query.query` is driven over the same
    failing commands and required to return an answer on every one, so the
    difference the story draws -- the fetch refuses where the dedupe query
    absorbs -- is observed on one pair of calls rather than argued.

  * **the shape.** `schemas/fetched-brief.schema.json` is a live harness
    artifact and is the subject of the assertions that name it. What a brief
    is held to is `schemas/story-brief.schema.json` and nothing else, so a
    malformed brief is required to be named by the field that failed.

  * **the reference pair.** `templates/sync/github.sh` and
    `templates/query/github.sh` are live harness artifacts and are read as
    they ship, then run against the stub tracker `tests/test_filed_query.py`
    already writes -- so "a filed brief comes back whole" is a fact about what
    the pair does rather than about what its headers say.

The workflows driven here are **built, not shipped**. Which names a brief may
name is the set of definitions a harness root holds, and that set is the
*input* to every assertion about it: a harness root this module builds is what
lets "a third workflow becomes plannable by shipping a definition" be driven
by shipping one, without this file's answers moving when this repository ships
or renames a workflow of its own.

Every absence asserted here carries a demonstration that it can fail:

  * "the question carries no paths" sits beside the dedupe question put to the
    same fake, whose document does carry them;
  * "the long body was not shortened" sits beside the same text through the
    dedupe query, which shortens it and says so;
  * "an empty key asks the command nothing" sits beside the same command asked
    a non-empty key, which does record a question;
  * "a brief naming an undefined workflow is refused" sits beside the same
    brief after that definition is written into the root, which fetches;
  * "`orchestration/brief_fetch.py` has one importer" sits beside a throwaway
    root with an importer planted in it, which the same scan reports;
  * "the two scripts record and read one payload marker" sits beside a
    rendering of one of them with the marker changed, which the same
    extraction reports;
  * "an item carrying no payload answers with no brief" sits beside the same
    item with its payload marker intact, which answers with the brief.

Every command driven as a `filed_query_command` here is a file this module or
`tests/test_filed_query.py` wrote, and `fixture_command_problems` is what makes
that a checked property rather than a habit. Nothing here invokes a model or
reaches a network: the two reference scripts are run against a stub `gh` first
on `PATH`.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest

import conftest
from test_filed_query import (  # noqa: F401 - shared fixtures and idioms
    ASKED,
    CHILD_SLEEP_SECONDS,
    KILL_BOUND_SECONDS,
    KILL_CEILING_SECONDS,
    LEDGER_VARIABLE,
    LONGER_THAN_ANY_BOUND,
    PATIENCE_SECONDS,
    QUERY_DIR,
    REPO_ROOT,
    SYNC_DIR,
    TEMPLATES,
    bodies,
    declared_marker,
    exits,
    file_through_the_reference_sync,
    fixture_command,
    fixture_file,
    ledger_state,
    needs_jq,
    planted_root,
    prints_prose_beside_the_document,
    reference_script,
    sleeps_forever,
    sources_importing,
    spawns_a_child,
    stub_tracker,
    unlaunchable,
    waited_for,
)

import brief_fetch
import filed_query
import harness_config
import schema_validator

#: What the modules say about themselves, read off them so this file names no
#: key, bound or schema of its own.
COMMAND_KEY = filed_query.COMMAND_KEY
TIMEOUT_KEY = filed_query.TIMEOUT_KEY
MAX_ITEMS_KEY = filed_query.MAX_ITEMS_KEY
MAX_STDOUT_BYTES = filed_query.MAX_STDOUT_BYTES
MAX_TEXT_LENGTH = filed_query.MAX_TEXT_LENGTH
ENVELOPE_SCHEMA = brief_fetch.ENVELOPE_SCHEMA
BRIEF_SCHEMA = brief_fetch.BRIEF_SCHEMA

#: The entry point the fetch is reached from, as `sources_importing` reports a
#: path. Derived from the module's own location rather than spelled twice.
FETCH_MODULE = Path(brief_fetch.__file__).stem
PLANNING_ENTRY_POINT = str(Path("scripts") / "l5-plan")


# --------------------------------------------------------------------------
# The workflows this module builds
#
# A harness root carrying definitions this repository does not ship, because
# which names a brief may name is the input to every assertion below about
# what is refused -- and reading the shipped set here would turn a deployment
# fact into something this module enforces.
# --------------------------------------------------------------------------

PLANNED_UNDER = "answering-workflow"
ALSO_DEFINED = "preserving-workflow"
#: A name no harness root this module builds carries.
UNDEFINED_WORKFLOW = "cartographer-workflow"


def built_workflow(name: str) -> dict:
    """One definition a harness root can hold, carrying what it must."""
    return conftest.build_workflow(
        conftest.workflow_stage(name=f"{name}-writing"),
        conftest.workflow_stage(name=conftest.VERIFYING_STAGE),
        name=name,
    )


@pytest.fixture
def harness(tmp_path) -> Path:
    """A harness root holding two definitions and nothing this repository ships.

    Its `schemas/` is linked at the shipped inventory by
    `materialize_workflow`, which is correct: a brief is held to the schema
    this harness ships, and that schema is the subject of the assertions that
    name it. What is fixture here is the *workflow* set.
    """
    root = tmp_path / "harness"
    for name in (PLANNED_UNDER, ALSO_DEFINED):
        conftest.materialize_workflow(built_workflow(name), root)
    return root


def test_the_built_root_holds_what_this_module_says_it_holds(harness):
    """The derivation every refusal assertion below rests on: the root defines
    two names and not the third, so "refused for a workflow with no
    definition" is a fact about this root rather than about a name that
    happens to be missing everywhere."""
    assert set(harness_config.workflow_names(harness)) == {
        PLANNED_UNDER, ALSO_DEFINED}
    assert UNDEFINED_WORKFLOW not in harness_config.workflow_names(harness)


# --------------------------------------------------------------------------
# The brief a command answers with
# --------------------------------------------------------------------------

def a_brief(**overrides) -> dict:
    """One brief as a tracker would hand it back.

    It carries every field the brief schema requires and both optional lists,
    so "the request carries the paths and the not-in-scope" is answerable from
    what came back. `overrides` is how a case states the one field it is about.
    """
    brief = {
        "title": "the parser drops the last token",
        "slug": "parser-drops-last-token",
        "body": "The reader stops one token early; see src/parser.py:118.",
        "category": "correctness",
        "severity": 3,
        "confidence": "high",
        "effort": "M",
        "workflow": PLANNED_UNDER,
        "paths": ["src/parser.py", "src/reader.py"],
        "not_in_scope": ["rewriting the tokenizer"],
    }
    brief.update(overrides)
    return brief


#: Keys of three different shapes, none of which the harness may resolve,
#: normalize, join against a root or decide anything from.
KEYS = {
    "a url": "https://tracker.invalid/issues/17",
    "a repository-relative path": ".harness/briefs/parser-drops-last-token.md",
    "a hex digest": "9f2c1d8ab3e04f5c9d7e6a1b2c3d4e5f60718293",
}
ONE_KEY = KEYS["a url"]


def answering(directory: Path, document: dict | str, *,
              name: str = "answers-a-brief.sh") -> Path:
    """A command that prints one document and exits zero.

    The document is written to a file beside the script and printed with
    `cat`, so nothing about the answer depends on a shell's quoting.
    """
    text = document if isinstance(document, str) else json.dumps(document)
    body = fixture_file(directory, f"{name}.document", text, executable=False)
    return fixture_command(directory, name, f'cat "{body}"\n')


def with_a_brief(directory: Path, brief: dict, **kwargs) -> Path:
    return answering(directory, {"brief": brief}, **kwargs)


def recording(directory: Path, transcript: Path, brief: dict | None = None,
              *, name: str = "records-the-question.sh") -> Path:
    """A command that writes what it read on stdin, then answers.

    It answers both questions the same way -- with the document below -- so
    what a caller reads back is not what distinguishes the two questions; the
    transcript is.
    """
    answer = json.dumps({"brief": brief} if brief is not None else {})
    body = fixture_file(directory, f"{name}.document", answer, executable=False)
    return fixture_command(
        directory, name,
        f'cat > "{transcript}"\n'
        f'cat "{body}"\n')


def fetched(command, tmp_path: Path, harness: Path, key: str = ONE_KEY,
            **overrides) -> brief_fetch.Fetched:
    """One brief-fetch question put to `command`, from a root this test owns."""
    config = {COMMAND_KEY: str(command), **overrides}
    return brief_fetch.fetch(key, config, tmp_path, harness)


def refused(answer: brief_fetch.Fetched) -> bool:
    return (answer.brief is None and answer.fetched is False
            and bool(answer.reason))


# ==========================================================================
# 1. The question the command is asked
# ==========================================================================


def read_one_document(transcript: Path) -> dict:
    """What the command read on stdin, required to be one JSON document.

    `raw_decode` must consume all of it, so a second document or a trailing
    line is reported here rather than parsed past.
    """
    read = transcript.read_text(encoding="utf-8")
    question, consumed = json.JSONDecoder().raw_decode(read)
    assert read[consumed:].strip() == "", read
    return question


def test_the_fetch_asks_one_json_document_carrying_the_key_and_no_paths(
        tmp_path, harness):
    """Observed at the command, not inferred from the call.

    Its control is the test below: the same fake, asked the dedupe question,
    records a document carrying the paths and no key -- so the absence here is
    the fetch's question rather than a transcript nothing writes to.
    """
    transcript = tmp_path / "the-question"
    answer = fetched(recording(tmp_path / "fake", transcript, a_brief()),
                     tmp_path, harness)

    assert answer.fetched is True, answer.reason
    assert read_one_document(transcript) == {"key": ONE_KEY}


def test_the_dedupe_question_is_what_it_was_and_carries_no_key(tmp_path,
                                                              harness):
    """The other half of the same fake: neither question changed the other.

    Driven through `filed_query.query`, which is the caller the dedupe
    question belongs to, so what is compared is the document each caller
    actually sends.
    """
    for_dedupe = tmp_path / "dedupe-question"
    for_fetch = tmp_path / "fetch-question"
    command = recording(tmp_path / "fake", for_dedupe, name="dedupe.sh")

    filed_query.query(ASKED, {COMMAND_KEY: str(command)}, target_root=tmp_path)
    asked_for_dedupe = read_one_document(for_dedupe)

    fetching = recording(tmp_path / "fake", for_fetch, a_brief(),
                         name="fetch.sh")
    fetched(fetching, tmp_path, harness)
    asked_for_fetch = read_one_document(for_fetch)

    assert asked_for_dedupe == {"paths": list(ASKED)}
    assert asked_for_fetch == {"key": ONE_KEY}
    assert "key" not in asked_for_dedupe
    assert "paths" not in asked_for_fetch


@pytest.mark.parametrize("shape", sorted(KEYS))
def test_every_shape_of_key_reaches_the_command_verbatim(shape, tmp_path,
                                                         harness):
    """The key is opaque: nothing resolves it, normalizes it, joins it against
    a root or decides anything from whether it looks like a URI. What arrived
    is read off the command's own transcript rather than off the call."""
    key = KEYS[shape]
    transcript = tmp_path / "the-question"
    answer = fetched(recording(tmp_path / "fake", transcript, a_brief()),
                     tmp_path, harness, key=key)

    assert answer.fetched is True, answer.reason
    assert read_one_document(transcript) == {"key": key}


def test_a_key_that_looks_like_a_path_is_not_joined_against_the_root(
        tmp_path, harness):
    """The relative-path key names nothing on disk, and the fetch neither
    creates it nor requires it: whether a key resolves is the answering
    command's judgement, and here it answers."""
    key = KEYS["a repository-relative path"]
    answer = fetched(with_a_brief(tmp_path / "fake", a_brief()), tmp_path,
                     harness, key=key)

    assert answer.fetched is True, answer.reason
    assert not (tmp_path / key).exists()


# ==========================================================================
# 2. The whole brief comes back
# ==========================================================================


def test_the_brief_comes_back_exactly_as_the_command_answered(tmp_path,
                                                              harness):
    brief = a_brief()
    answer = fetched(with_a_brief(tmp_path / "fake", brief), tmp_path, harness)

    assert answer.reason == ""
    assert answer.brief == brief


def test_a_body_past_the_dedupe_per_field_bound_comes_back_whole(tmp_path,
                                                                 harness):
    """No per-field bound shortens a fetched brief, because a truncated brief
    is a brief that plans wrong.

    Its control is the second half: the same text carried by the *dedupe*
    answer is shortened to the bound and the shortening is named -- so the
    whole body here is this question being unbounded rather than a bound that
    never fires.
    """
    long_body = "b" * (MAX_TEXT_LENGTH + 1)
    brief = a_brief(body=long_body)

    answer = fetched(with_a_brief(tmp_path / "fetching", brief), tmp_path,
                     harness)

    assert answer.fetched is True, answer.reason
    assert answer.brief["body"] == long_body
    assert len(answer.brief["body"]) > MAX_TEXT_LENGTH
    assert long_body in brief_fetch.render(answer.brief, ONE_KEY)

    shortened = filed_query.query(
        ASKED,
        {COMMAND_KEY: str(answering(
            tmp_path / "dedupe",
            {"items": [{"key": ONE_KEY, "title": "t", "summary": long_body}]},
            name="dedupe-answer.sh"))},
        target_root=tmp_path)
    assert shortened.answered is True, shortened.reason
    assert len(shortened.items[0].summary) == MAX_TEXT_LENGTH
    assert shortened.excluded


# ==========================================================================
# 3. Every way of not having a brief
# ==========================================================================


def ways_of_refusing(tmp_path: Path, harness: Path
                     ) -> dict[str, brief_fetch.Fetched]:
    """Every failure the story names, each put to the fetch for real.

    Built as a mapping rather than as a parametrization so the refusals can
    also be compared with one another below: what separates them is the reason
    each gives, and a repair that collapsed two of them into one string would
    pass every assertion made about them individually.
    """
    answering_command = with_a_brief(tmp_path / "fine", a_brief())
    return {
        "an empty key": fetched(answering_command, tmp_path, harness, key=""),
        "no configured command": brief_fetch.fetch(ONE_KEY, {}, tmp_path,
                                                   harness),
        "a refused configuration": fetched(
            answering_command, tmp_path, harness, **{TIMEOUT_KEY: "soon"}),
        "a command that cannot be launched": fetched(
            unlaunchable(tmp_path), tmp_path, harness),
        "a non-zero exit": fetched(
            exits(tmp_path / "failing", 1, "the tracker refused the read"),
            tmp_path, harness),
        "a timeout": fetched(
            sleeps_forever(tmp_path / "sleeping"), tmp_path, harness,
            **{TIMEOUT_KEY: str(KILL_BOUND_SECONDS)}),
        "prose beside the document": fetched(
            prints_prose_beside_the_document(tmp_path / "noisy"), tmp_path,
            harness),
        "an answer failing the envelope": fetched(
            answering(tmp_path / "envelope", {"brief": "not an object"},
                      name="bad-envelope.sh"), tmp_path, harness),
        "a key that did not resolve": fetched(
            answering(tmp_path / "unresolved", {}, name="no-brief.sh"),
            tmp_path, harness),
        "a brief failing its schema": fetched(
            with_a_brief(tmp_path / "malformed", a_brief(severity="bad"),
                         name="malformed-brief.sh"), tmp_path, harness),
        "a workflow with no definition": fetched(
            with_a_brief(tmp_path / "undefined",
                         a_brief(workflow=UNDEFINED_WORKFLOW),
                         name="undefined-workflow.sh"), tmp_path, harness),
    }


def test_every_way_of_not_having_a_brief_returns_a_refusal_and_raises_on_none(
        tmp_path, harness):
    """The fetch raises on nothing: every failure comes back carrying its own
    reason, so the caller decides what a refusal is worth."""
    for way, answer in ways_of_refusing(tmp_path, harness).items():
        assert isinstance(answer, brief_fetch.Fetched), way
        assert refused(answer), (way, answer)


def test_each_way_of_not_having_a_brief_says_which_way_it_was(tmp_path,
                                                              harness):
    """"No brief" is not the whole answer; which way it was is.

    The reasons are required to be distinct from one another, so a repair that
    funnelled two ways into one string is reported here even though every
    assertion about them individually would still hold.
    """
    answers = ways_of_refusing(tmp_path, harness)
    reasons = [answer.reason for answer in answers.values()]
    assert len(set(reasons)) == len(reasons), reasons


#: What each way's reason must name, so a developer reading it can tell which
#: way it was. One table over one set of answers rather than a parametrization,
#: because rebuilding every fake per case would spawn each command once per
#: assertion.
NAMES_ITS_WAY = {
    "an empty key": "empty key",
    "no configured command": COMMAND_KEY,
    "a refused configuration": TIMEOUT_KEY,
    "a command that cannot be launched": "could not be launched",
    "a non-zero exit": "exited 1",
    "a timeout": str(KILL_BOUND_SECONDS),
    "prose beside the document": "not a single JSON document",
    "an answer failing the envelope": ENVELOPE_SCHEMA,
    "a key that did not resolve": "did not resolve",
    "a brief failing its schema": BRIEF_SCHEMA,
    "a workflow with no definition": UNDEFINED_WORKFLOW,
}


def test_every_refusal_names_the_way_it_was(tmp_path, harness):
    answers = ways_of_refusing(tmp_path, harness)
    assert set(answers) == set(NAMES_ITS_WAY)
    for way, expected in NAMES_ITS_WAY.items():
        assert expected in answers[way].reason, (way, answers[way].reason)


def test_an_answer_carrying_no_brief_is_worded_as_the_key_not_resolving(
        tmp_path, harness):
    """A command that ran and looked has said something a command that never
    ran has not, and the two are told apart from what each refusal says."""
    answers = ways_of_refusing(tmp_path, harness)
    unresolved = answers["a key that did not resolve"].reason
    unreachable = answers["a command that cannot be launched"].reason

    assert ONE_KEY in unresolved
    assert unresolved != unreachable
    assert "could not be launched" not in unresolved


def test_an_empty_key_is_refused_without_the_command_being_asked(tmp_path,
                                                                harness):
    """The one refusal made without asking, because there is nothing to ask
    about. Its control is the second half: the same command asked a non-empty
    key does record a question, so the missing transcript is the refusal
    rather than a fake that never writes one."""
    transcript = tmp_path / "the-question"
    command = recording(tmp_path / "fake", transcript, a_brief())

    for nothing_to_ask_about in ("", "   "):
        answer = fetched(command, tmp_path, harness, key=nothing_to_ask_about)
        assert refused(answer), repr(nothing_to_ask_about)
        assert "empty key" in answer.reason
        assert not transcript.exists(), repr(nothing_to_ask_about)

    assert fetched(command, tmp_path, harness).fetched is True
    assert transcript.exists()


@pytest.mark.parametrize("field", ["severity", "confidence", "slug"])
def test_a_malformed_brief_is_refused_naming_the_field_that_failed(
        field, tmp_path, harness):
    """Named by field rather than by shape, which is why the envelope types the
    brief only as an object. Its control is the same brief unmodified, which
    fetches."""
    brief = a_brief()
    brief.pop(field)

    answer = fetched(with_a_brief(tmp_path / "malformed", brief), tmp_path,
                     harness)

    assert refused(answer)
    assert field in answer.reason, answer.reason
    assert fetched(with_a_brief(tmp_path / "whole", a_brief(),
                                name="whole-brief.sh"),
                   tmp_path, harness).fetched is True


def test_a_brief_naming_an_undefined_workflow_is_refused_listing_the_defined(
        tmp_path, harness):
    """The acceptable set is the definitions the harness holds, so the refusal
    can list them beside the name it refused."""
    answer = fetched(with_a_brief(tmp_path / "undefined",
                                  a_brief(workflow=UNDEFINED_WORKFLOW)),
                     tmp_path, harness)

    assert refused(answer)
    assert UNDEFINED_WORKFLOW in answer.reason
    for defined in harness_config.workflow_names(harness):
        assert defined in answer.reason, defined


def test_a_third_workflow_becomes_plannable_by_shipping_a_definition(
        tmp_path, harness):
    """The control for the refusal above, and the property the derivation buys:
    the same brief, the same command, and one definition written into the root
    between the two calls."""
    command = with_a_brief(tmp_path / "third",
                           a_brief(workflow=UNDEFINED_WORKFLOW))
    assert refused(fetched(command, tmp_path, harness))

    conftest.materialize_workflow(built_workflow(UNDEFINED_WORKFLOW), harness)

    answer = fetched(command, tmp_path, harness)
    assert answer.fetched is True, answer.reason
    assert answer.brief["workflow"] == UNDEFINED_WORKFLOW


# ==========================================================================
# 4. The bounded run is written once and called twice
# ==========================================================================


def bounded_reasons(command, tmp_path: Path, harness: Path,
                    **overrides) -> tuple[str, str]:
    """What each caller says about the same command overrunning the same bound.

    The pair is the assertion: the reason the dedupe query gives has to be
    carried in the reason the fetch gives, which is what one shared run means
    and what two spellings of one bound could not satisfy.
    """
    fetch = fetched(command, tmp_path, harness, **overrides)
    query = filed_query.query(
        ASKED, {COMMAND_KEY: str(command), **overrides}, target_root=tmp_path)
    return fetch.reason, query.reason


def test_the_fetch_obeys_the_stdout_bound_the_dedupe_query_obeys(tmp_path,
                                                                 harness):
    """A document truncated mid-token is not a document, whichever question it
    answers. The fake's answer is a *valid* fetched-brief document; the only
    thing wrong with it is its size."""
    oversized = json.dumps({"brief": a_brief(body="x" * MAX_STDOUT_BYTES)})
    assert len(oversized) > MAX_STDOUT_BYTES
    command = answering(tmp_path / "oversized", oversized,
                        name="oversized.sh")

    fetch_reason, query_reason = bounded_reasons(command, tmp_path, harness)

    assert str(MAX_STDOUT_BYTES) in fetch_reason
    assert query_reason in fetch_reason


def test_the_fetch_obeys_the_timeout_the_dedupe_query_obeys(tmp_path, harness):
    """The kill is observed rather than the argument asserted: the command was
    asked to sleep for far longer than the ceiling, so a call that returned
    inside it can only have returned because the command was killed."""
    command = sleeps_forever(tmp_path / "sleeping")

    started = time.monotonic()
    answer = fetched(command, tmp_path, harness,
                     **{TIMEOUT_KEY: str(KILL_BOUND_SECONDS)})
    elapsed = time.monotonic() - started

    assert refused(answer)
    assert str(KILL_BOUND_SECONDS) in answer.reason
    assert elapsed < KILL_CEILING_SECONDS, elapsed
    assert elapsed < LONGER_THAN_ANY_BOUND

    fetch_reason, query_reason = bounded_reasons(
        command, tmp_path, harness, **{TIMEOUT_KEY: str(KILL_BOUND_SECONDS)})
    assert query_reason in fetch_reason


def test_the_fetchs_kill_reaches_the_children_the_command_spawned(tmp_path,
                                                                  harness):
    """The command is killed as a process group here too, so its children go
    with it.

    The absence is controlled below rather than beside itself: the same child,
    spawned by a command that is not killed, does write its marker -- so a
    marker that never appears is a fact about the kill rather than about a
    child that was never going to write one.
    """
    marker = tmp_path / "the-child-survived"
    command = spawns_a_child(
        tmp_path / "killed", marker, name="killed-leader.sh",
        then=f"sleep {LONGER_THAN_ANY_BOUND}\n")

    answer = fetched(command, tmp_path, harness,
                     **{TIMEOUT_KEY: str(KILL_BOUND_SECONDS)})

    assert refused(answer)
    assert not waited_for(marker, CHILD_SLEEP_SECONDS * 2), \
        "a child of the killed command outlived it"


def test_the_same_child_writes_its_marker_when_its_leader_answers(tmp_path,
                                                                  harness):
    """The control for the absence above."""
    marker = tmp_path / "the-child-survived"
    document = fixture_file(tmp_path / "surviving", "answer.document",
                            json.dumps({"brief": a_brief()}), executable=False)
    command = spawns_a_child(
        tmp_path / "surviving", marker, name="surviving-leader.sh",
        then=f'cat "{document}"\n')

    answer = fetched(command, tmp_path, harness)

    assert answer.fetched is True, answer.reason
    assert waited_for(marker, PATIENCE_SECONDS), \
        "the child never writes its marker, so its absence above proves nothing"


# ==========================================================================
# 5. The fetch refuses where the dedupe query absorbs
# ==========================================================================


def test_the_query_stays_total_over_the_commands_the_fetch_refuses(tmp_path,
                                                                   harness):
    """Two callers of one run, and the difference between them is deliberate.

    Every command that makes the fetch refuse is put to `filed_query.query`,
    which must come back with an answer rather than an exception, carrying no
    items, `answered` false and a reason -- its contract before this story and
    after it. A failed query costs dedupe and costs nothing else; a failed
    fetch refuses the invocation a developer asked for.
    """
    commands = {
        "unlaunchable": unlaunchable(tmp_path),
        "non-zero exit": str(exits(tmp_path / "failing", 1, "refused")),
        "prose beside the document": str(
            prints_prose_beside_the_document(tmp_path / "noisy")),
        "not this shape": str(answering(tmp_path / "shape", {"brief": {}},
                                        name="brief-not-items.sh")),
    }
    for way, command in commands.items():
        answer = filed_query.query(ASKED, {COMMAND_KEY: command},
                                   target_root=tmp_path)
        assert isinstance(answer, filed_query.Answer), way
        assert answer.items == (), way
        assert answer.answered is False, way
        assert answer.reason, way

        assert refused(fetched(command, tmp_path, harness)), way


def test_a_command_answering_both_questions_serves_dedupe_and_the_fetch(
        tmp_path, harness):
    """The control for the totality above: the same seam, asked two questions
    it can answer, answers both -- so the silence above is the failure rather
    than a query that no longer works at all."""
    brief = a_brief()
    dedupe = filed_query.query(
        ASKED,
        {COMMAND_KEY: str(answering(
            tmp_path / "dedupe",
            {"items": [{"key": ONE_KEY, "title": brief["title"]}]},
            name="items.sh"))},
        target_root=tmp_path)

    assert dedupe.answered is True, dedupe.reason
    assert [item.key for item in dedupe.items] == [ONE_KEY]
    assert fetched(with_a_brief(tmp_path / "fetch", brief), tmp_path,
                   harness).brief == brief


# ==========================================================================
# 6. The shape a command answers with
# ==========================================================================


def envelope_schema() -> dict:
    return schema_validator.load_schema(ENVELOPE_SCHEMA)


def test_the_envelope_is_registered_and_loads_under_the_name_the_module_uses():
    manifest = json.loads(
        (REPO_ROOT / "schemas" / "manifest.json").read_text(encoding="utf-8"))
    assert ENVELOPE_SCHEMA in manifest["schemas"]
    assert envelope_schema()["properties"]["brief"]["type"] == "object"


def test_an_answer_with_a_brief_and_an_answer_with_none_both_satisfy_it():
    """The property the optional field buys: a command that looked and found
    nothing has answered, and its answer is not a malformed one."""
    assert schema_validator.validate({"brief": a_brief()},
                                     envelope_schema()) == []
    assert schema_validator.validate({}, envelope_schema()) == []


@pytest.mark.parametrize("instance", [
    pytest.param({"brief": "the parser drops the last token"}, id="brief-is-text"),
    pytest.param({"brief": [a_brief()]}, id="brief-is-a-list"),
    pytest.param({"brief": None}, id="brief-is-null"),
])
def test_an_answer_whose_brief_is_not_an_object_is_refused(instance):
    assert schema_validator.validate(instance, envelope_schema())


def test_a_brief_is_held_to_the_brief_schema_and_to_no_second_shape():
    """There is one definition of what a brief is: the envelope says only that
    it is an object, and the brief schema is what decides the rest.

    Driven as the pair the module drives: a brief the envelope accepts and the
    brief schema refuses is refused, which is what makes the second validation
    load-bearing rather than decorative.
    """
    missing_a_field = a_brief()
    missing_a_field.pop("workflow")

    assert schema_validator.validate({"brief": missing_a_field},
                                     envelope_schema()) == []
    assert schema_validator.validate(
        missing_a_field, schema_validator.load_schema(BRIEF_SCHEMA))


# ==========================================================================
# 7. What renders the brief as a request
# ==========================================================================


def test_the_rendered_request_carries_the_briefs_own_prose(tmp_path):
    """The planner receives the evidence rather than a paraphrase of it."""
    brief = a_brief()
    rendered = brief_fetch.render(brief, ONE_KEY)

    assert brief["title"] in rendered
    assert brief["body"] in rendered
    for path in brief["paths"]:
        assert path in rendered, path
    for one in brief["not_in_scope"]:
        assert one in rendered, one


def test_the_rendered_request_carries_the_key_and_the_instruction_to_record_it(
):
    """The whole of the traceability this adds: the brief is not written into
    the artifact, so its key as prose is the only trace that survives."""
    rendered = brief_fetch.render(a_brief(), ONE_KEY)

    assert ONE_KEY in rendered
    assert re.search(r"(?i)record", rendered), rendered
    assert re.search(r"(?i)description", rendered), rendered


def test_a_brief_carrying_neither_optional_list_renders_without_their_headings(
):
    """The control for the render above: with the lists, their headings are
    there; without them the block carries neither, rather than an empty
    heading a planner would read as a statement."""
    bare = {"title": "the parser drops the last token",
            "body": "The reader stops one token early.",
            "workflow": PLANNED_UNDER}
    without = brief_fetch.render(bare, ONE_KEY)
    with_both = brief_fetch.render(a_brief(), ONE_KEY)

    assert "src/parser.py" in with_both
    assert "rewriting the tokenizer" in with_both
    assert "src/parser.py" not in without
    assert "rewriting the tokenizer" not in without
    assert bare["title"] in without
    assert bare["body"] in without


# ==========================================================================
# 8. Who reaches the fetch
# ==========================================================================


def test_the_fetch_is_imported_by_the_planning_entry_point_and_by_nothing_else(
):
    """The brief is a plan-time input and nowhere else.

    An exact set equality in both directions over `orchestration/` and
    `scripts/`, so a second importer fails here and an entry point that
    stopped importing fails here too. No orchestration module imports it at
    all, which is what makes "no run, resume or sweep path reaches it" a fact
    about the whole graph rather than about the three modules a list would
    name: a module a run reaches cannot reach this one without appearing here.

    Its control is beside it, where the same scan over a throwaway root with an
    importer planted in it reports that importer.
    """
    assert sources_importing(FETCH_MODULE, REPO_ROOT) == {PLANNING_ENTRY_POINT}


def test_the_import_scan_reports_an_orchestration_importer_when_there_is_one(
        tmp_path):
    """The control for the equality above, in both import forms."""
    root = planted_root(
        tmp_path, "a_run_path.py",
        f"import {FETCH_MODULE}\n\n\ndef reach(key, config):\n"
        f"    return {FETCH_MODULE}.fetch(key, config)\n")
    assert sources_importing(FETCH_MODULE, root) == \
        {str(Path("orchestration") / "a_run_path.py")}

    other = planted_root(tmp_path / "from-form", "another_run_path.py",
                         f"from {FETCH_MODULE} import fetch\n")
    assert sources_importing(FETCH_MODULE, other) == \
        {str(Path("orchestration") / "another_run_path.py")}


def test_the_fetch_reads_no_configuration_key_of_its_own(tmp_path):
    """It resolves its settings through `filed_query.resolve_settings`, which
    is why `filed_query.py` remains the only source that reads the command key.

    Driven behaviourally rather than by reading the source: a configuration
    whose bounds are refused refuses the fetch too, which a module reading the
    key directly and skipping the resolution would not do.
    """
    settings, problem = filed_query.resolve_settings({TIMEOUT_KEY: "soon"})
    assert settings is None and TIMEOUT_KEY in problem

    answer = brief_fetch.fetch(
        ONE_KEY, {COMMAND_KEY: "a-command-nothing-here-runs",
                  TIMEOUT_KEY: "soon"}, tmp_path)
    assert refused(answer)
    assert TIMEOUT_KEY in answer.reason


# ==========================================================================
# 9. The reference pair, and the payload it records
#
# The two shipped scripts are the subject here, so they are read and run as
# they ship. What they talk to is not: `gh` is the stub `tests/
# test_filed_query.py` writes, first on PATH, so nothing reaches a network.
# ==========================================================================

#: How the payload marker is stated in each reference script. One assignment on
#: one line in each file, which is what makes the two comparable without either
#: script being parsed as a shell program -- the shape the path-marker pair is
#: already held in.
PAYLOAD_ASSIGNMENT = re.compile(r'^PAYLOAD_MARKER_PREFIX="(?P<marker>.*)"$',
                                re.MULTILINE)


def declared_payload_marker(text: str) -> str | None:
    found = PAYLOAD_ASSIGNMENT.search(text)
    return None if found is None else found.group("marker")


def script_text(directory: str) -> str:
    return (TEMPLATES / directory / "github.sh").read_text(encoding="utf-8")


def test_the_pair_records_and_reads_the_same_payload_marker():
    """Read out of both shipped scripts, so they cannot drift apart unnoticed.

    The harness requires the agreement and cannot enforce it, which is why it
    is held here beside the path marker's own comparison.
    """
    records = declared_payload_marker(script_text(SYNC_DIR))
    reads = declared_payload_marker(script_text(QUERY_DIR))

    assert records, "the sync script declares no payload marker"
    assert reads, "the query script declares no payload marker"
    assert records == reads


def test_the_payload_marker_comparison_reports_a_pair_that_drifted():
    """The control: the same extraction over a rendering of one script with its
    marker changed, which must come back different."""
    shipped = script_text(SYNC_DIR)
    drifted = PAYLOAD_ASSIGNMENT.sub(
        'PAYLOAD_MARKER_PREFIX="l5-other-payload: "', shipped, count=1)

    assert drifted != shipped
    assert declared_payload_marker(drifted) != declared_payload_marker(shipped)
    assert declared_payload_marker(drifted) == "l5-other-payload: "


def test_the_payload_marker_is_not_the_path_marker():
    """Two markers doing two jobs: one searchable per path, one carrying the
    whole payload. A pair that collapsed them would answer a path search with
    a payload."""
    assert declared_payload_marker(script_text(SYNC_DIR)) != \
        declared_marker(script_text(SYNC_DIR))


def with_the_reference_command(environment: dict, call):
    """Run `call` with the stub tracker's environment in place.

    The scripts are launched by `filed_query` as subprocesses inheriting this
    process's environment, so the stub's PATH and ledger have to be in it for
    the duration -- and out of it afterwards, whatever the call did.
    """
    previous = dict(os.environ)
    os.environ.update({key: environment[key]
                       for key in ("PATH", LEDGER_VARIABLE)})
    try:
        return call()
    finally:
        os.environ.clear()
        os.environ.update(previous)


@needs_jq
def test_a_brief_filed_through_the_reference_pair_comes_back_whole(tmp_path,
                                                                   harness):
    """The round trip the story exists to close, driven end to end.

    The brief is filed by the shipped sync script and fetched back through the
    harness's own fetch with the shipped query script configured, so what is
    asserted is a brief the reference implementation recorded and answered --
    every field, including the ones a title and a body alone would have thrown
    away, and a body longer than the dedupe answer's per-field bound.
    """
    environment, ledger = stub_tracker(tmp_path)
    brief = a_brief(body="the evidence, at length. " * 400)
    assert len(brief["body"]) > MAX_TEXT_LENGTH
    url = file_through_the_reference_sync(tmp_path, environment,
                                          key="brief-1", payload=brief)

    answer = with_the_reference_command(
        environment,
        lambda: brief_fetch.fetch(
            url, {COMMAND_KEY: reference_script(QUERY_DIR)}, tmp_path, harness))

    assert answer.fetched is True, answer.reason
    assert answer.brief == brief


@needs_jq
def test_an_item_carrying_no_payload_answers_that_the_key_did_not_resolve(
        tmp_path, harness):
    """An item filed before the payload marker existed, or by something else:
    the command ran and looked, and there is nothing under that key.

    Its control is the fetch above, and the second half here: the same item
    with its payload marker intact answers with the brief, so the refusal is
    the missing payload rather than a script that answers nothing.
    """
    environment, ledger = stub_tracker(tmp_path)
    brief = a_brief()
    url = file_through_the_reference_sync(tmp_path, environment,
                                          key="brief-2", payload=brief)
    marker = declared_payload_marker(script_text(SYNC_DIR))
    assert any(marker in body for body in bodies(ledger))

    # Read and written back through the shared shape rather than as a bare
    # list, because the stub's ledger now holds a board beside the issues.
    filed = ledger_state(ledger)
    issue = filed["issues"][0]
    stripped = [line for line in issue["body"].splitlines()
                if marker not in line]
    issue["body"] = "\n".join(stripped)
    ledger.write_text(json.dumps(filed), encoding="utf-8")

    answer = with_the_reference_command(
        environment,
        lambda: brief_fetch.fetch(
            url, {COMMAND_KEY: reference_script(QUERY_DIR)}, tmp_path, harness))

    assert refused(answer)
    assert "did not resolve" in answer.reason


@needs_jq
def test_the_query_script_answers_both_questions_and_neither_changed_the_other(
        tmp_path, harness):
    """One command, two questions, driven at the shipped script.

    The dedupe question still answers with the item filed against the paths,
    and the payload marker does not leak into the summary it composes -- the
    encoded document is not prose a reader wants in a tracker summary. The
    fetch answers with the brief.
    """
    environment, _ = stub_tracker(tmp_path)
    brief = a_brief()
    url = file_through_the_reference_sync(tmp_path, environment,
                                          key="brief-3", payload=brief)

    def both():
        dedupe = filed_query.query(
            brief["paths"], {COMMAND_KEY: reference_script(QUERY_DIR)},
            target_root=tmp_path)
        fetch = brief_fetch.fetch(
            url, {COMMAND_KEY: reference_script(QUERY_DIR)}, tmp_path, harness)
        return dedupe, fetch

    dedupe, fetch = with_the_reference_command(environment, both)

    assert dedupe.answered is True, dedupe.reason
    assert [item.key for item in dedupe.items] == [url]
    assert dedupe.items[0].title == brief["title"]
    assert set(dedupe.items[0].paths) == set(brief["paths"])
    marker = declared_payload_marker(script_text(QUERY_DIR))
    assert marker not in dedupe.items[0].summary

    assert fetch.fetched is True, fetch.reason
    assert fetch.brief == brief
