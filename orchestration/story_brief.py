"""What a brief is filed under, defined once because there are two producers.

The Inspector reads a scope of a target's code deliberately and files what it
finds; an assist session files a brief a developer asked for. Both file the
same artifact, and both must file it under the same name — so this module holds
the kind, the bare-path rule, the identity and the payload, and neither
producer derives any of them for itself.

**That is the whole reason this module exists.** An identity derived twice is
two derivations that agree today, and the first time they stop agreeing the
same piece of work is filed twice: once by whoever noticed it by hand and again
by every inspection that follows, with nothing local able to notice, because a
landed entry drops the payload the comparison would have needed. The cost of
one shared derivation is one import; the cost of two is a duplicate a human
reconciles.

**The identity carries the mechanically stable parts of a brief and never its
prose.** Kind, category, sorted bare paths and slug; not the title, not the
body, not the severity, not the confidence. Those are the parts a writer
rephrases and re-rates between two writings of one piece of work, and an
identity carrying them is an identity that drifts.

**The paths a brief carries are bare repository-relative paths.** The reference
sync command writes one searchable marker per entry of the payload's paths and
the reference query command searches for the marker of a bare path, so a path
carrying a line number would be filed under a marker no scoped query ever asks
for, and the dedupe both producers depend on would silently never match. The
line is not lost: file:line evidence is the evidentiary standard and it lives
in the body, where a person reads it rather than a script searching for it.

**Nothing here hashes anything.** `outbox.identity_key` is the only derivation
of a key in the harness and `outbox.enqueue` is the only way a brief reaches
the queue; this module says what goes into that derivation and nothing about
how it is made.
"""
from __future__ import annotations

#: What both producers file. Part of every identity, so an entry either of them
#: wrote is distinguishable from any other producer's without reading its
#: payload — which a landed entry no longer has.
KIND = "story-brief"


def bare_path(path: str) -> str:
    """One path with any line-level suffix taken off it.

    `orchestration/inspection.py:42` and `…:42:7` both become the file. The
    reason is mechanical rather than stylistic and is stated in this module's
    docstring: a path filed with a line number is invisible to every scoped
    query that follows.
    """
    head = path.strip()
    while True:
        stem, separator, tail = head.rpartition(":")
        if not separator or not tail.isdigit() or not stem:
            return head
        head = stem


def bare_paths(brief: dict) -> tuple[str, ...]:
    """The paths a brief is about: bare, deduplicated and sorted.

    Sorted and deduplicated because they are part of the identity, and an
    identity that depended on the order a writer happened to put two paths in
    would file one piece of work twice.
    """
    declared = brief.get("paths") or []
    return tuple(sorted({
        bare_path(one) for one in declared
        if isinstance(one, str) and one.strip()
    }))


def identity(brief: dict) -> dict:
    """What a brief is filed under, and nothing else.

    The kind, the category, the sorted bare paths and the slug. Two briefs
    carrying those four alike are the same piece of work however differently
    they are written, which is what makes a brief a developer filed by hand and
    a finding the Inspector reported land on one key.
    """
    return {
        "kind": KIND,
        "category": brief["category"],
        "paths": list(bare_paths(brief)),
        "slug": brief["slug"],
    }


def payload(brief: dict, scope: str = "") -> dict:
    """What is filed with a brief: the brief, with its paths made bare.

    `scope` is where the brief came from. That is a part of the tree for a
    broad inspection, which passes its own scope; it is an account of what
    produced the brief for a producer that is not reading a part of the tree at
    all, such as an inspection of what one story changed; and it is empty for a
    brief nothing scoped, which is every brief a developer files by hand. It is
    payload rather than identity in every one of those cases, so a brief about
    one file files under the same key whichever producer found it — which is
    what stops one mode's findings and another's dividing the dedupe.

    Everything here is JSON-serializable by construction: it came out of a JSON
    document and the fields added to it are strings, which matters because the
    outbox coerces nothing and a value it cannot render is an item it drops.
    """
    return {
        **brief,
        "kind": KIND,
        "scope": scope,
        "paths": list(bare_paths(brief)),
    }
