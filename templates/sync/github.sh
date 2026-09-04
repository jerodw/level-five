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
# ATOMICITY IS THIS COMMAND'S BUSINESS. Filing a finding here is several API
# calls — create, label, add to a project board, set the board's Status. The
# harness makes one invocation and tracks no partial state. If a later call
# fails, exit 75 and let the next sync re-run the whole thing; the search at
# the top is what makes that safe. That is why the two paths through the
# search — an issue found, an issue created — converge on one url before the
# board block rather than the found path answering and returning: an entry
# whose issue was created by an earlier invocation and whose board call failed
# must reach the board on the next sweep, and it can only do that if the
# invocation that finds the issue goes on to do the board work.
#
# EVERYTHING AFTER gh issue create SUCCEEDS IS TRANSIENT. The issue is the
# record and the board is a view of it, so a board that was briefly
# unreachable must not lose the item: every failure below the creation exits
# 75, the entry stays pending, and a later sweep runs this whole command again
# and finds the issue rather than creating a second one.
#
# THE BOARD MECHANICS ARE GENERIC; THE VALUES ARE THE TARGET'S. This file
# carries how a board is written to and no statement about which board: the
# project, its owner and the Status option are the constants below, and a
# target sets them in its installed .harness/sync/ copy. A template carrying a
# project number would file another repository's briefs onto this board.
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
PROJECT_OWNER="${L5_SYNC_PROJECT_OWNER:-@me}"   # who owns that project
# The board's Status field, by name, and the option a newly filed entry is put
# in. An empty option means the item is added and its Status is left at
# whatever the project's own default is, which is what a target whose board has
# no such field gets — so the values below are the ones a target sets in its
# installed copy, and the template carries none of them.
STATUS_FIELD="${L5_SYNC_STATUS_FIELD:-Status}"
STATUS_OPTION="${L5_SYNC_STATUS_OPTION:-}"

# How much of the project's item listing is read when looking for the item this
# invocation just added. Not an L5_SYNC_ constant, because it is a mechanic
# rather than something this target files against: it bounds a read, and an
# item the listing did not report is answered transiently rather than read as
# an empty Status, so a bound that was too small costs a pending entry and
# never an overwritten column.
ITEM_LIST_LIMIT=5000

# The searchable marker written once per path the payload carries.
# templates/query/github.sh searches for exactly this prefix, and a test reads
# this line out of both files and asserts the two strings are the same, so the
# pair cannot drift apart unnoticed. Change it in both or in neither.
PATH_MARKER_PREFIX="l5-path: "

# The marker the whole payload is recorded under, so a filed brief can be
# answered back whole rather than as a title and a body. The title and the body
# alone lose the slug, the category, the severity, the confidence, the effort
# and the workflow, and a fetched brief missing them fails the brief schema on
# fields the filing threw away. Held to the same string in both files by the
# same test, for the same reason. Change it in both or in neither.
PAYLOAD_MARKER_PREFIX="l5-payload: "

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

# The whole payload, recorded once under its own marker so the query script can
# answer a brief-fetch question with the brief as it was filed. Encoded rather
# than written as JSON, because a JSON document written raw into an HTML comment
# carries newlines and can carry the comment's own terminator; jq does both
# halves, so neither script needs a base64 binary.
encoded="$(printf '%s' "$entry" | jq -r '(.payload // {}) | tojson | @base64')" \
  || fail_terminal "the entry's payload could not be encoded"
body="${body}

<!-- ${PAYLOAD_MARKER_PREFIX}${encoded} -->"

# --- idempotency: search before creating --------------------------------
# A search that fails is transient rather than terminal: we do not know
# whether the issue exists, and creating on a failed search is exactly the
# duplicate this whole mechanism exists to avoid.
existing="$(gh issue list --search "\"${marker}\"" --state all --limit 1 \
              --json url --jq '.[0].url // ""' 2>/dev/null)" \
  || fail_transient "the search for ${key} failed, so nothing was created"

if [ -n "$existing" ]; then
  # Already filed — by an earlier invocation whose response we lost, or by
  # this one running twice. Take what the provider holds and fall through: the
  # board work below is what the earlier invocation may have failed at, and it
  # is only reachable on this path.
  url="$existing"
else
  url="$(gh issue create --title "$title" --body "$body" --label "$LABEL" 2>&1)" \
    || fail_transient "the issue could not be created: ${url}"

  url="$(printf '%s\n' "$url" | grep -Eo 'https://[^[:space:]]+' | tail -1)"
  [ -n "$url" ] || fail_transient "the issue was created but named no URL"
fi

# --- the board -----------------------------------------------------------
# The issue exists by here, whichever path we came down, so every failure
# below is transient: a later sync re-runs this whole command, the search
# finds the issue, and the board work is retried.
if [ -n "$PROJECT" ]; then
  # item-add for an issue already on the board reports the existing item
  # rather than adding a second one, which is what makes the retry safe. The
  # item's id is what item-edit takes; it deals in ids and not in names.
  added="$(gh project item-add "$PROJECT" --owner "$PROJECT_OWNER" --url "$url" \
             --format json 2>/dev/null)" \
    || fail_transient "the issue was created at ${url} but it could not be added to project ${PROJECT}"
  item_id="$(printf '%s' "$added" | jq -r '.id // ""')" \
    || fail_transient "the issue was added to project ${PROJECT} but the item id could not be read"
  [ -n "$item_id" ] || fail_transient "the issue was added to project ${PROJECT} but it named no item"

  if [ -n "$STATUS_OPTION" ]; then
    # What the board already says about this item. An item the listing did not
    # report is a failure to know rather than an empty Status: overwriting on
    # the strength of a listing that did not mention the item would move an
    # item out of the column a human put it in.
    listed="$(gh project item-list "$PROJECT" --owner "$PROJECT_OWNER" \
                --limit "$ITEM_LIST_LIMIT" --format json 2>/dev/null)" \
      || fail_transient "the project ${PROJECT} listing failed, so the item's ${STATUS_FIELD} is unknown"
    item="$(printf '%s' "$listed" \
              | jq -c --arg id "$item_id" '[.items[]? | select(.id == $id)] | .[0] // empty')" \
      || fail_transient "the project ${PROJECT} listing could not be read"
    [ -n "$item" ] \
      || fail_transient "item ${item_id} was not in the first ${ITEM_LIST_LIMIT} items of project ${PROJECT}, so its ${STATUS_FIELD} is unknown"
    current="$(printf '%s' "$item" | jq -r --arg name "$STATUS_FIELD" \
                 '[to_entries[] | select((.key | ascii_downcase) == ($name | gsub(" "; "") | ascii_downcase)) | .value] | .[0] // "" | tostring')" \
      || fail_transient "the item's ${STATUS_FIELD} could not be read"

    # Set it only where it is empty. A sweep re-running over an entry that
    # landed long ago finds a value here and leaves it alone; this script puts
    # an item into a column once and never moves it between columns.
    if [ -z "$current" ]; then
      project_id="$(gh project view "$PROJECT" --owner "$PROJECT_OWNER" --format json 2>/dev/null \
                      | jq -r '.id // ""')" \
        || fail_transient "project ${PROJECT} could not be read, so its ${STATUS_FIELD} was not set"
      fields="$(gh project field-list "$PROJECT" --owner "$PROJECT_OWNER" --format json 2>/dev/null)" \
        || fail_transient "the fields of project ${PROJECT} could not be read"
      field_id="$(printf '%s' "$fields" | jq -r --arg name "$STATUS_FIELD" \
                    '[.fields[]? | select(.name == $name) | .id] | .[0] // ""')" \
        || fail_transient "the fields of project ${PROJECT} could not be read"
      option_id="$(printf '%s' "$fields" | jq -r --arg name "$STATUS_FIELD" --arg option "$STATUS_OPTION" \
                     '[.fields[]? | select(.name == $name) | .options[]? | select(.name == $option) | .id] | .[0] // ""')" \
        || fail_transient "the options of ${STATUS_FIELD} could not be read"

      # A name that resolves to no id is transient like everything else after
      # the issue exists: a misconfigured field or option name costs a pending
      # entry, and the alternative costs the item.
      [ -n "$project_id" ] || fail_transient "project ${PROJECT} named no id"
      [ -n "$field_id" ] || fail_transient "project ${PROJECT} has no field named ${STATUS_FIELD}"
      [ -n "$option_id" ] || fail_transient "${STATUS_FIELD} in project ${PROJECT} has no option named ${STATUS_OPTION}"

      gh project item-edit --id "$item_id" --project-id "$project_id" \
        --field-id "$field_id" --single-select-option-id "$option_id" >/dev/null 2>&1 \
        || fail_transient "the item is on project ${PROJECT} but its ${STATUS_FIELD} could not be set to ${STATUS_OPTION}"
    fi
  fi
fi

echo "$url"
exit 0
