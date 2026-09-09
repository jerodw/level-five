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
# calls — create, label, add to a project board, set the board's fields. The
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
# A MISSING LABEL IS CREATED AND A MISSING FIELD IS SKIPPED, and the two differ
# on purpose. gh can create a label and cannot create a project field, so a
# category filed for the first time labels its issue without anybody having set
# anything up, while a field the board does not have — or an option it does not
# offer — costs that field and nothing else: it is said on stderr, the fields
# beside it are still written, the item is still on the board, and the entry
# still lands. The Status write below is the exception and keeps the behaviour
# it has always had, because the column an item lands in is not something this
# script may quietly decline to set.
#
# THIS SCRIPT ENUMERATES NO CATEGORY, SEVERITY, CONFIDENCE, EFFORT OR WORKFLOW.
# It writes the values the entry carries, so the acceptable values stay the
# brief schema's enums and the workflows the harness defines, and a category
# added to the schema later is a board option added rather than a second list
# to keep in step here.
#
# THE BOARD MECHANICS ARE GENERIC; THE VALUES ARE THE TARGET'S. This file
# carries how a board is written to and no statement about which board: the
# project, its owner, the Status option and the names of the classification
# fields are the constants below, and a target sets them in its installed
# .harness/sync/ copy. A template carrying a project number would file another
# repository's briefs onto this board.
#
# THE QUERY SCRIPT IS THIS ONE'S PAIR. templates/query/github.sh asks what is
# already filed against a set of paths, and it finds what this script wrote by
# searching for the per-path marker below. The two are written by the same
# target and must agree about where a path is recorded in a tracker item; the
# harness requires the agreement and cannot enforce it, so a target that
# installs one without the other gets no dedupe rather than an error. Change
# the marker here and change it there in the same edit.
#
# templates/item/github.sh IS THE THIRD MEMBER OF THAT FAMILY. It publishes a
# planned story onto an item this script filed, so it is the only one of the
# three that rewrites a body this one wrote. What it must not disturb is
# everything this script records: the key marker the search above finds, the
# per-path markers the query script searches for, and the payload marker the
# brief fetch reads back. It writes its projection between markers of its own
# and replaces only what they delimit, which is what keeps filing, dedupe and
# brief fetch working over an item that has been published onto. Nothing in
# the harness reads a projection back, and a publish that fails refuses
# nothing.
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
# The board's fields, by name, that a brief's classification is written into.
# Each is empty here and set in a target's installed copy: an empty name means
# that field is not written, so a board that has no such column files exactly as
# it did before these existed. What goes into them is the value the payload
# carries and nothing this script decides.
CATEGORY_FIELD="${L5_SYNC_CATEGORY_FIELD:-}"
SEVERITY_FIELD="${L5_SYNC_SEVERITY_FIELD:-}"
CONFIDENCE_FIELD="${L5_SYNC_CONFIDENCE_FIELD:-}"
EFFORT_FIELD="${L5_SYNC_EFFORT_FIELD:-}"
WORKFLOW_FIELD="${L5_SYNC_WORKFLOW_FIELD:-}"

# The label a brief's category is applied under: this prefix followed by the
# category the payload carries. It defaults to something non-empty because it is
# a mechanic rather than a property of a particular board — a label travels with
# the issue and is searchable everywhere, which every target that files briefs
# wants — and a payload carrying no category applies no label at all.
CATEGORY_LABEL_PREFIX="${L5_SYNC_CATEGORY_LABEL_PREFIX:-l5-}"

# The colour a category label is created with. Not an L5_SYNC_ constant, for the
# reason ITEM_LIST_LIMIT below is not: it is a mechanic rather than something
# this target files against. It exists so the create is idempotent — gh's
# --force updates a label that already exists rather than failing on it, and a
# create naming no colour would give the label a fresh random one every filing.
CATEGORY_LABEL_COLOR="ededed"

# How much of the project's item listing is read when looking for the item this
# invocation just added. Not an L5_SYNC_ constant, because it is a mechanic
# rather than something this target files against: it bounds a read, and an
# item the listing did not report is answered transiently rather than read as a
# set of empty fields, so a bound that was too small costs a pending entry and
# never an overwritten value.
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

# The classification the payload carries: one label below, five board fields
# further down. An absent value reads as empty and nothing is written for it,
# which is what an entry that is not a brief gets.
payload_value() {
  printf '%s' "$entry" | jq -r --arg name "$1" '(.payload[$name] // "") | tostring'
}

category="$(payload_value category)" || category=""
severity="$(payload_value severity)" || severity=""
confidence="$(payload_value confidence)" || confidence=""
effort="$(payload_value effort)" || effort=""
workflow="$(payload_value workflow)" || workflow=""

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

# --- the category label ---------------------------------------------------
# The issue exists by here, whichever path we came down, so both calls are
# transient on failure, exactly as the board work below is: a later sync re-runs
# this whole command, the search finds the issue, and the label work is retried.
# An entry whose payload carries no category makes neither call and files with
# the one label it always had.
if [ -n "$category" ]; then
  category_label="${CATEGORY_LABEL_PREFIX}${category}"

  # Created before it is applied, so a category filed for the first time does
  # not need somebody to have made its label by hand. --force is what makes the
  # create idempotent: it updates a label that already exists rather than
  # failing on it, and the colour and description above are fixed so that
  # updating one changes nothing about it.
  gh label create "$category_label" --color "$CATEGORY_LABEL_COLOR" \
    --description "l5 brief category: ${category}" --force >/dev/null 2>&1 \
    || fail_transient "the issue is filed at ${url} but the label ${category_label} could not be created"

  # Added beside the label the issue already carries rather than replacing it.
  gh issue edit "$url" --add-label "$category_label" >/dev/null 2>&1 \
    || fail_transient "the issue is filed at ${url} but the label ${category_label} could not be added"
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

  # The project's id, its field list and its item listing are each read at most
  # once per filing rather than once per field, so the writes below do not
  # multiply the reads. Each is read the first time something needs it, so a
  # filing with nothing to write makes none of these calls.
  project_id=""
  fields=""
  item=""

  read_the_project() {
    [ -z "$fields" ] || return 0
    project_id="$(gh project view "$PROJECT" --owner "$PROJECT_OWNER" --format json 2>/dev/null \
                    | jq -r '.id // ""')" \
      || fail_transient "project ${PROJECT} could not be read, so none of its fields were set"
    [ -n "$project_id" ] || fail_transient "project ${PROJECT} named no id"
    fields="$(gh project field-list "$PROJECT" --owner "$PROJECT_OWNER" --format json 2>/dev/null)" \
      || fail_transient "the fields of project ${PROJECT} could not be read"
    [ -n "$fields" ] || fail_transient "project ${PROJECT} named no fields"
  }

  read_the_item() {
    [ -z "$item" ] || return 0
    # What the board already says about this item. An item the listing did not
    # report is a failure to know rather than a set of empty fields: writing on
    # the strength of a listing that did not mention the item would overwrite
    # values a human put there.
    local listed
    listed="$(gh project item-list "$PROJECT" --owner "$PROJECT_OWNER" \
                --limit "$ITEM_LIST_LIMIT" --format json 2>/dev/null)" \
      || fail_transient "the project ${PROJECT} listing failed, so the item's fields are unknown"
    item="$(printf '%s' "$listed" \
              | jq -c --arg id "$item_id" '[.items[]? | select(.id == $id)] | .[0] // empty')" \
      || fail_transient "the project ${PROJECT} listing could not be read"
    [ -n "$item" ] \
      || fail_transient "item ${item_id} was not in the first ${ITEM_LIST_LIMIT} items of project ${PROJECT}, so its fields are unknown"
  }

  # What the listing reports this item's named field as, empty where the board
  # reports none. gh names a field's key after the field itself, so the name is
  # matched with its spaces removed and its case ignored.
  board_value() {
    printf '%s' "$item" | jq -r --arg name "$1" \
      '[to_entries[] | select((.key | ascii_downcase) == ($name | gsub(" "; "") | ascii_downcase)) | .value] | .[0] // "" | tostring'
  }

  field_id_for() {
    printf '%s' "$fields" | jq -r --arg name "$1" \
      '[.fields[]? | select(.name == $name) | .id] | .[0] // ""'
  }

  option_id_for() {
    printf '%s' "$fields" | jq -r --arg name "$1" --arg option "$2" \
      '[.fields[]? | select(.name == $name) | .options[]? | select(.name == $option) | .id] | .[0] // ""'
  }

  if [ -n "$STATUS_OPTION" ]; then
    read_the_item
    current="$(board_value "$STATUS_FIELD")" \
      || fail_transient "the item's ${STATUS_FIELD} could not be read"

    # Set it only where it is empty. A sweep re-running over an entry that
    # landed long ago finds a value here and leaves it alone; this script puts
    # an item into a column once and never moves it between columns.
    if [ -z "$current" ]; then
      read_the_project
      field_id="$(field_id_for "$STATUS_FIELD")" \
        || fail_transient "the fields of project ${PROJECT} could not be read"
      option_id="$(option_id_for "$STATUS_FIELD" "$STATUS_OPTION")" \
        || fail_transient "the options of ${STATUS_FIELD} could not be read"

      # A Status name that resolves to no id is transient like everything else
      # after the issue exists: a misconfigured field or option name costs a
      # pending entry, and the alternative costs the item. This is the one
      # write that answers that way — the classification fields below are
      # skipped instead, because the column an item lands in is not something
      # this script may quietly decline to set.
      [ -n "$field_id" ] || fail_transient "project ${PROJECT} has no field named ${STATUS_FIELD}"
      [ -n "$option_id" ] || fail_transient "${STATUS_FIELD} in project ${PROJECT} has no option named ${STATUS_OPTION}"

      gh project item-edit --id "$item_id" --project-id "$project_id" \
        --field-id "$field_id" --single-select-option-id "$option_id" >/dev/null 2>&1 \
        || fail_transient "the item is on project ${PROJECT} but its ${STATUS_FIELD} could not be set to ${STATUS_OPTION}"
    fi
  fi

  # One classification field, resolved by name and edited by id. A field this
  # target named no name for, and a value the payload does not carry, are each
  # nothing to write. A name or an option that resolves to no id costs this
  # field alone: it is said on stderr and the fields beside it are still
  # written. The board is only written where it reports the field empty, which
  # is the rule the Status write above keeps.
  set_board_field() {
    local field_name="$1"
    local value="$2"
    local current field_id option_id

    [ -n "$field_name" ] || return 0
    [ -n "$value" ] || return 0

    read_the_item
    current="$(board_value "$field_name")" \
      || fail_transient "the item's ${field_name} could not be read"
    [ -z "$current" ] || return 0

    read_the_project
    field_id="$(field_id_for "$field_name")" \
      || fail_transient "the fields of project ${PROJECT} could not be read"
    if [ -z "$field_id" ]; then
      echo "project ${PROJECT} has no field named ${field_name}, so ${value} was not written there" >&2
      return 0
    fi
    option_id="$(option_id_for "$field_name" "$value")" \
      || fail_transient "the options of ${field_name} could not be read"
    if [ -z "$option_id" ]; then
      echo "${field_name} in project ${PROJECT} has no option named ${value}, so it was not written there" >&2
      return 0
    fi

    gh project item-edit --id "$item_id" --project-id "$project_id" \
      --field-id "$field_id" --single-select-option-id "$option_id" >/dev/null 2>&1 \
      || fail_transient "the item is on project ${PROJECT} but its ${field_name} could not be set to ${value}"
  }

  set_board_field "$CATEGORY_FIELD" "$category"
  set_board_field "$SEVERITY_FIELD" "$severity"
  set_board_field "$CONFIDENCE_FIELD" "$confidence"
  set_board_field "$EFFORT_FIELD" "$effort"
  set_board_field "$WORKFLOW_FIELD" "$workflow"
fi

echo "$url"
exit 0
