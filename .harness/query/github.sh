#!/usr/bin/env bash
#
# A reference filed-query command: answers what is already filed against a set
# of paths, by searching GitHub issues.
#
# This is a template. It ships with the harness, l5-init installs a copy into a
# target's .harness/query/, and the target is expected to edit it — what counts
# as filed, and which items count, are this file's business rather than the
# harness's. What is not negotiable is the contract below, which the harness
# relies on and cannot enforce.
#
# THE CONTRACT
#
#   stdin    One JSON document: the question, carrying "paths" — the paths the
#            harness is asking about. The question is scoped and never a
#            listing: it asks what is filed against these paths, not what is
#            open, so what this transfers stays proportional to the code being
#            asked about rather than to the tracker's backlog.
#   stdout   One JSON document and NOTHING ELSE, in the shape of
#            schemas/filed-items.schema.json: an object with an "items" array,
#            each item carrying "key" and "title" and optionally "summary" and
#            "paths". A debug line, a progress message or a shell trace printed
#            beside it makes stdout not one document, and the harness reads
#            that as nothing known rather than parsing what it can. An empty
#            items array is an answer — it says nothing is filed.
#   stderr   Everything else. Diagnostics, progress, what a search did. The
#            harness carries a tail of this back as the reason it knows
#            nothing, so say why here.
#   exit 0   Answered. Whatever is on stdout is the answer.
#   exit *   Could not answer. The harness knows nothing, which is a different
#            thing from knowing that nothing is filed, and it says so to
#            whoever asked. A failure here costs dedupe and nothing else: no
#            run is blocked, refused or failed by it.
#
# THIS SCRIPT IS templates/sync/github.sh's PAIR. That script writes one
# searchable marker per path into the issue body, and this one searches for
# exactly that marker. The two are written by the same target and must agree
# about where a path is recorded in a tracker item; the harness requires the
# agreement and cannot enforce it, so a target that installs one without the
# other gets no dedupe rather than an error. Change the marker here and change
# it there in the same edit.
#
# WHICH ITEMS TO REPORT IS THIS SCRIPT'S DECISION, and deliberately not the
# harness's — it parses no status and infers no policy from what comes back.
# The recommendation, which the harness declines to encode:
#
#   Suppress what was REJECTED. A finding a human looked at and turned down
#   must never be filed again; refiling it is how a mechanism teaches a
#   developer to ignore it.
#
#   Do not suppress what was COMPLETED. A finding that was fixed and which the
#   code still exhibits is a regression, and hearing about it again is the
#   point.
#
# On GitHub those are distinguishable: an issue closed as "not planned" is a
# rejection, and one closed as "completed" is a fix. So the filter below keeps
# open issues and issues closed as not planned, and drops issues closed as
# completed. A tracker without that distinction has to pick one; picking
# "suppress nothing closed" errs toward hearing a finding twice, which is the
# cheaper mistake.
#
# It requires gh, authenticated, and jq. Both are the target's business.

set -uo pipefail

# --- what this target searches. Edit these. -----------------------------
# The searchable marker written once per path by the sync script. A test reads
# this line out of both files and asserts the two strings are the same, so the
# pair cannot drift apart unnoticed. Change it in both or in neither.
PATH_MARKER_PREFIX="l5-path: "

# How many items one path's search may return. The harness bounds what it will
# read as well; this bound is about what the tracker is asked for.
LIMIT="${L5_QUERY_LIMIT:-50}"

fail() { echo "$*" >&2; exit 1; }

command -v gh >/dev/null 2>&1 || fail "gh is not on PATH"
command -v jq >/dev/null 2>&1 || fail "jq is not on PATH"

question="$(cat)" || fail "the question could not be read from stdin"

asked="$(printf '%s' "$question" | jq -c '(.paths // [])' 2>/dev/null)" \
  || fail "the question on stdin is not a JSON document carrying paths"

paths="$(printf '%s' "$asked" | jq -r '.[]' 2>/dev/null)" \
  || fail "the question's paths could not be read"

if [ -z "$paths" ]; then
  # Asked about nothing, so nothing is filed against it. This is an answer,
  # not a failure: the harness may conclude that dedupe ran and found nothing.
  echo '{"items":[]}'
  exit 0
fi

# One search per path. A search that fails makes the whole answer unreliable —
# reporting the paths that did answer would say that nothing is filed against
# the ones that did not — so a failure here is a failure to answer.
found=""
while IFS= read -r one; do
  [ -n "$one" ] || continue
  marker="${PATH_MARKER_PREFIX}${one}"
  echo "searching for ${marker}" >&2
  page="$(gh issue list --search "\"${marker}\"" --state all --limit "$LIMIT" \
            --json number,title,body,url,state,stateReason 2>/dev/null)" \
    || fail "the search for ${one} failed, so what is filed is not known"
  found="${found}${page}
"
done <<PATHS
$paths
PATHS

# One document on stdout and nothing else. Every item's fields are what the
# tracker said; nothing is invented for an item the searches did not return.
printf '%s' "$found" | jq -s -c \
  --arg prefix "$PATH_MARKER_PREFIX" \
  --argjson asked "$asked" '
  {
    items: (
      [ .[] | .[] ]
      | map(select(.state == "OPEN" or .stateReason == "NOT_PLANNED"))
      | unique_by(.url)
      | map(. as $issue | {
          key: ($issue.url // ($issue.number | tostring)),
          title: ($issue.title // ""),
          summary: (($issue.body // "") | split("\n")
                    | map(select(contains("<!-- " + $prefix) | not))
                    | join("\n")),
          paths: [ $asked[] as $one
                   | select(($issue.body // "")
                            | contains("<!-- " + $prefix + $one + " -->"))
                   | $one ]
        })
    )
  }' || fail "the answer could not be composed"

exit 0
