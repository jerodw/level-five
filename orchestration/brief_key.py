"""The one place the name of a story artifact's brief key is spelled.

A story planned from a brief carries the key of the brief it was planned from,
so a run has something to name when it says where the work has got to. Two
processes read and write that field — `l5-plan`, which records it because it
already holds the key, and the coordinator, which reads it off a parsed story
at the two moments a run reports on itself — and two spellings of one field
name are two spellings that can disagree. So the name lives here, and neither
side writes it.

This module is small on purpose, for the reason `story_ids` and `plan_mandate`
are: it exists so that one name is spelled in one place, not because recording
a line of text is difficult.

**The key is opaque.** It is held and written exactly as it was given: nothing
here resolves it, normalizes it, strips it, joins it against a root, checks
that it exists or decides anything from its form. A URL, a repository-relative
document path and a digest are all keys, which is how `brief_fetch` and
`item_update` already treat one.

**Recording refuses rather than rewrites.** An artifact that already carries a
key keeps the key it was committed with: a re-plan that overwrote it would
silently move a committed story from one item to another, and the reader of
that item would never learn it had happened. So a second recording changes the
artifact on no byte and comes back saying it did nothing.

Every function returns what happened rather than printing it, which is the
shape `plan_commit`, `plan_mandate` and `plan_validation` already have with the
script that calls them. Nothing here prints and nothing here raises except
where the artifact cannot be read at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: The field the key is written under, at the top level of a story artifact.
#: Top-level rather than nested inside the story object because it is written
#: by appending, the way the mandate block is: inserting a line into an
#: existing indented block would be text surgery on a session's artifact for no
#: gain.
FIELD = "brief_key"


@dataclass(frozen=True)
class Recorded:
    """What recording a key on one artifact did.

    `recorded` is whether this call appended the field. When it is false the
    artifact on disk is untouched on every byte and `detail` says why nothing
    was written — which is what an artifact already carrying a key gets, and
    is a report rather than a failure.
    """

    path: Path
    key: str
    recorded: bool
    detail: str = ""


def carries_a_key(text: str) -> bool:
    """Whether an artifact already carries the field at the top level.

    Read as a top-level key, because that is where the declaration puts it and
    where the parse would find it — the same reading `plan_mandate` makes of
    the block it refuses to write twice.
    """
    return any(
        line.rstrip() == f"{FIELD}:" or line.startswith(f"{FIELD}: ")
        for line in text.splitlines()
    )


def record(path: Path, key: str) -> Recorded:
    """Record `key` on the artifact at `path`, or say why nothing was written.

    The key is appended as one top-level line, leaving every byte the session
    wrote where it is. An artifact already carrying the field is reported and
    left alone: it keeps the key it was committed with, because a re-plan that
    silently moved a story from one item to another would be a change nobody
    could see.

    An empty key writes nothing either. There is no item behind it, so a field
    recording one would name nothing and would still be enough to make a run
    invoke a command about it.
    """
    if not key:
        return Recorded(path, key, False, "no brief key was given to record")
    text = path.read_text(encoding="utf-8")
    if carries_a_key(text):
        return Recorded(
            path,
            key,
            False,
            f"the artifact already carries a {FIELD}, and it keeps the key it "
            f"was committed with rather than being moved to another item",
        )
    ending = "" if text.endswith("\n") else "\n"
    path.write_text(f"{text}{ending}\n{FIELD}: {key}\n", encoding="utf-8")
    return Recorded(path, key, True, "")


def key_of(story: dict | None) -> str:
    """The brief key a parsed story carries, or the empty string for none.

    Takes a story already parsed by `story_parser` rather than text, so this
    is not a second reader of a story artifact: the run's one reading is what
    it is handed. A story that carries no key, and one that could not be
    parsed at all, both answer the empty string, so a caller asks one question
    rather than two.
    """
    value = (story or {}).get(FIELD, "")
    return value if isinstance(value, str) else ""
