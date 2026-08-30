#!/usr/bin/env bash
#
# A reference sync command: files one outbox entry as a GitHub issue.
#
# This is a template. It ships with the harness, l5-init installs a copy into
# a target's .harness/sync/, and the target is expected to edit it — the
# label, the project board and the repository are this file's business rather
# than the harness's. What is not negotiable is the contract below, which the
# harness relies on and cannot enforce.
#
# THE CONTRACT
#
#   stdin    One JSON document: the entry, carrying "key", "identity",
#            "state" and "payload".
#   L5_SYNC_KEY
#            The entry's idempotency key, the same value the document on
#            stdin carries under "key".
#   stdout   The reference — whatever this command wants recorded against the
#            entry, printed as the last non-empty line. The harness records
#            it and never parses it, so it need not be a URL.
#   stderr   Why it did not land. The harness carries a tail of this back as
#            the entry's last_error, so a failed or pending entry says why in
#            its own file.
#   exit 0   Landed. The last non-empty line of stdout is the reference. A
#            zero exit that names no reference is read as transient, because
#            it establishes nothing about whether the request arrived.
#   exit 75  Transient (EX_TEMPFAIL). The entry stays pending with the
#            attempt counted, and a later sync tries again.
#   exit *   Terminal. The entry fails and no later sync invokes this command
#            for it again.
#
# IDEMPOTENT GIVEN THE KEY. This is the sentence the whole design rests on.
# The harness makes exactly one invocation per entry per sync, and an
# ambiguous write — the issue was created and the response never came back —
# is resolved by invoking this command again. So it must search for the key
# before it creates anything, and answer with what it finds. The harness
# cannot check this; it is this command's promise. The search below is that
# promise kept: the key goes into the issue body, and the issue is created
# only when a search for the key finds nothing.
#
# ATOMICITY IS THIS COMMAND'S BUSINESS. Filing a finding here is three API
# calls — create, label, add to a project board. The harness makes one
# invocation and tracks no partial state. If a later call fails, exit 75 and
# let the next sync re-run the whole thing; the search at the top is what
# makes that safe.
#
# THE QUERY SCRIPT IS THIS ONE'S PAIR. templates/query/github.sh asks what is
# already filed against a set of paths, and it finds what this script wrote by
# searching for the per-path marker below. The two are written by the same
# target and must agree about where a path is recorded in a tracker item; the
# harness requires the agreement and cannot enforce it, so a target that
# installs one without the other gets no dedupe rather than an error. Change
# the marker here and change it there in the same edit.
#
# A SYNC COMMAND MUST NOT COMMIT. It writes to a tracker; a human or a run
# commits to the repository. The harness does not enforce this and says so
# rather than implying a check that does not exist. Do not add a git commit
# to this file.
#
# It requires gh, authenticated, and jq. Both are the target's business.

set -uo pipefail

# --- what this target files against. Edit these. ------------------------
LABEL="${L5_SYNC_LABEL:-l5}"
PROJECT="${L5_SYNC_PROJECT:-}"   # a project number or URL; empty skips the board

# The searchable marker written once per path the payload carries.
# templates/query/github.sh searches for exactly this prefix, and a test reads
# this line out of both files and asserts the two strings are the same, so the
# pair cannot drift apart unnoticed. Change it in both or in neither.
PATH_MARKER_PREFIX="l5-path: "

fail_transient() { echo "$*" >&2; exit 75; }
fail_terminal()  { echo "$*" >&2; exit 1; }

command -v gh >/dev/null 2>&1 || fail_transient "gh is not on PATH"
command -v jq >/dev/null 2>&1 || fail_transient "jq is not on PATH"

key="${L5_SYNC_KEY:-}"
[ -n "$key" ] || fail_terminal "L5_SYNC_KEY is empty; there is no key to be idempotent on"

entry="$(cat)" || fail_transient "the entry could not be read from stdin"

title="$(printf '%s' "$entry" | jq -r '.payload.title // ("l5: " + .key)')" \
  || fail_terminal "the entry carries no title this command can use"
body="$(printf '%s' "$entry" | jq -r '.payload.body // ""')" \
  || fail_terminal "the entry carries no body this command can use"

# The key is written into the body, which is what makes the search below able
# to find it. Change the marker if you like; search for whatever you write.
marker="l5-sync-key: ${key}"
body="${body}

<!-- ${marker} -->"

# One marker per path the payload carries, so the query script can find this
# item by searching for a path. A payload carrying no paths adds nothing and
# files exactly as it did before this existed.
paths="$(printf '%s' "$entry" | jq -r '(.payload.paths // []) | .[]' 2>/dev/null)" || paths=""
if [ -n "$paths" ]; then
  while IFS= read -r one; do
    [ -n "$one" ] || continue
    body="${body}
<!-- ${PATH_MARKER_PREFIX}${one} -->"
  done <<PATHS
$paths
PATHS
fi

# --- idempotency: search before creating --------------------------------
# A search that fails is transient rather than terminal: we do not know
# whether the issue exists, and creating on a failed search is exactly the
# duplicate this whole mechanism exists to avoid.
existing="$(gh issue list --search "\"${marker}\"" --state all --limit 1 \
              --json url --jq '.[0].url // ""' 2>/dev/null)" \
  || fail_transient "the search for ${key} failed, so nothing was created"

if [ -n "$existing" ]; then
  # Already filed — by an earlier invocation whose response we lost, or by
  # this one running twice. Answer with what the provider holds.
  echo "$existing"
  exit 0
fi

url="$(gh issue create --title "$title" --body "$body" --label "$LABEL" 2>&1)" \
  || fail_transient "the issue could not be created: ${url}"

url="$(printf '%s\n' "$url" | grep -Eo 'https://[^[:space:]]+' | tail -1)"
[ -n "$url" ] || fail_transient "the issue was created but named no URL"

if [ -n "$PROJECT" ]; then
  # The issue exists now, so a board failure must not be terminal: a later
  # sync re-runs this whole command, the search finds the issue, and the
  # board call is retried.
  gh project item-add "$PROJECT" --owner "@me" --url "$url" >/dev/null 2>&1 \
    || fail_transient "the issue was created at ${url} but the project board could not be updated"
fi

echo "$url"
exit 0
