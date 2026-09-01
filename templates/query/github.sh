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
# TWO QUESTIONS ARE ASKED OF THIS COMMAND, and which one is on stdin is decided
# by what the question document carries.
#
#   stdin    One JSON document. Carrying "paths", it is the DEDUPE question —
#            the paths the harness is asking about. That question is scoped and
#            never a listing: it asks what is filed against these paths, not
#            what is open, so what this transfers stays proportional to the code
#            being asked about rather than to the tracker's backlog. Carrying
#            "key", it is the FETCH question — one brief, by the key this
#            command itself reported for it, wanted in full because a truncated
#            brief is a brief that plans wrong. The key is opaque and arrives
#            exactly as the harness was given it; nothing on the harness's side
#            resolves it, normalizes it or decides anything from its form.
#   stdout   One JSON document and NOTHING ELSE. For the dedupe question, the
#            shape of schemas/filed-items.schema.json: an object with an "items"
#            array, each item carrying "key" and "title" and optionally
#            "summary" and "paths". For the fetch question, the shape of
#            schemas/fetched-brief.schema.json: an object carrying "brief", or
#            carrying nothing where the key resolved to nothing — which is an
#            answer, and is distinguished from failing to answer at all. A debug
#            line, a progress message or a shell trace printed beside it makes
#            stdout not one document, and the harness reads that as nothing
#            known rather than parsing what it can. An empty items array is an
#            answer — it says nothing is filed.
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
# exactly that marker; it also records the whole payload under a marker of its
# own, and this one reads that marker back to answer the fetch question. The two
# are written by the same target and must agree about where a path and a payload
# are recorded in a tracker item; the harness requires the agreement and cannot
# enforce it, so a target that installs one without the other gets no dedupe
# rather than an error. Change a marker here and change it there in the same
# edit.
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

# The marker the sync script records the whole payload under, read back to
# answer the fetch question. Held to the same string in both files by the same
# test. Change it in both or in neither.
PAYLOAD_MARKER_PREFIX="l5-payload: "

# How many items one path's search may return. The harness bounds what it will
# read as well; this bound is about what the tracker is asked for.
LIMIT="${L5_QUERY_LIMIT:-50}"

fail() { echo "$*" >&2; exit 1; }

command -v gh >/dev/null 2>&1 || fail "gh is not on PATH"
command -v jq >/dev/null 2>&1 || fail "jq is not on PATH"

question="$(cat)" || fail "the question could not be read from stdin"

# Which question this is. A key means the fetch; anything else is the dedupe
# question this script has always answered, and that path is left exactly as it
# was so neither question changed the other.
key="$(printf '%s' "$question" | jq -r '(.key // "")' 2>/dev/null)" \
  || fail "the question on stdin is not a JSON document"

if [ -n "$key" ]; then
  # The key is this script's own — it is what the dedupe answer reports as an
  # item's key, which for this implementation is the issue's URL. Fetched
  # whole: no per-field bound shortens a brief, because a truncated brief is a
  # brief that plans wrong.
  body="$(gh issue view "$key" --json body --jq '.body' 2>/dev/null)" \
    || fail "the item ${key} could not be read, so its brief is not known"
  encoded="$(printf '%s\n' "$body" \
    | sed -n "s/^<!-- ${PAYLOAD_MARKER_PREFIX}\(.*\) -->\$/\1/p" | tail -1)"
  if [ -z "$encoded" ]; then
    # The item exists and carries no payload — filed before the payload marker
    # existed, or by something else. An answer carrying no brief, which the
    # harness reads as the key not having resolved to one. That is a different
    # answer from failing to answer, and saying so is this script's job.
    echo '{}'
    exit 0
  fi
  printf '%s' "$encoded" | jq -R -c '{brief: (. | @base64d | fromjson)}' \
    || fail "the payload recorded against ${key} could not be decoded"
  exit 0
fi

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
  --arg payload "$PAYLOAD_MARKER_PREFIX" \
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
                    | map(select((contains("<!-- " + $prefix)
                                  or contains("<!-- " + $payload)) | not))
                    | join("\n")),
          paths: [ $asked[] as $one
                   | select(($issue.body // "")
                            | contains("<!-- " + $prefix + $one + " -->"))
                   | $one ]
        })
    )
  }' || fail "the answer could not be composed"

exit 0
