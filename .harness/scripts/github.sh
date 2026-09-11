#!/usr/bin/env bash
#
# The reference tracker commands: one file answering every question this
# target's tracker is asked. Its first argument names the job.
#
#   github.sh sync    file one outbox entry as a GitHub issue
#   github.sh query   answer what is already filed, and fetch one brief
#   github.sh item    publish a planned story onto an item, and move its Status
#
# This is a template. It ships with the harness, l5-init installs a copy into a
# target's .harness/scripts/, and the target is expected to edit it — the
# label, the project board, the repository and the names a board spells its
# columns with are this file's business rather than the harness's. What is not
# negotiable are the three contracts below, which the harness relies on and
# cannot enforce.
#
# THE THREE JOBS ARE ONE FILE because they share most of what they know: the
# project and its owner, the Status field's name, the markers written into an
# item's body, the rule that decides whether a configured field name and a
# board's field name are the same, and the resolution of a field's id and an
# option's id from a project's field listing. Each of those is declared once
# below and used by whichever branches need it, so a fix to one of them is a
# fix everywhere rather than a fix in one of three copies. What is genuinely
# per-job — the label and the column a newly filed item lands in, the six
# classification field names, the three moment options, the search limit —
# stays with its branch.
#
# The harness needs nothing new for this: a configured command is split into
# words before it is run, so it still invokes three commands and still
# interprets three sets of exit codes. What changed is that the three commands
# are three arguments to one file.
#
# ==========================================================================
# THE SYNC CONTRACT — github.sh sync
# ==========================================================================
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
# THE SYNC BRANCH SETS A STATUS ONLY WHERE IT FINDS ONE EMPTY. It puts a newly
# filed item into a column once and never moves it between columns; moving one
# is the item branch's job, and the difference between them is deliberate.
#
# A SYNC COMMAND MUST NOT COMMIT. It writes to a tracker; a human or a run
# commits to the repository. The harness does not enforce this and says so
# rather than implying a check that does not exist. Do not add a git commit
# to this file.
#
# ==========================================================================
# THE QUERY CONTRACT — github.sh query
# ==========================================================================
#
# TWO QUESTIONS ARE ASKED OF THIS BRANCH, and which one is on stdin is decided
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
#            run is blocked, refused or failed by it. NO EXIT CODE HERE IS READ
#            AS A RETRY, because nothing retries behind this branch.
#
# WHICH ITEMS TO REPORT IS THIS BRANCH'S DECISION, and deliberately not the
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
# ONE SEARCH PER PATH DOES NOT SCALE, and a target writing its own command has
# to be told so, because nothing about the failure is loud. The harness asks one
# question carrying the whole scope and holds the answer to one bound, so a
# command whose searches are proportional to the scope is killed partway through
# and never answers — which the harness reads as dedupe not having run, on every
# inspection, for as long as nobody looks. Measured against this tracker one
# search costs roughly 0.85 seconds, so a 60-file scope spent close to a minute
# against a 30-second bound. This branch therefore batches: BATCH path markers
# are quoted and OR'd into one search, and the pages are unioned through the
# composition below, which already deduplicates by URL. The cost of a search is
# nearly flat in batch size — 5 markers measured 0.83 seconds, 10 measured 0.90
# and 20 measured about 1.0 — so a 60-file scope becomes three searches rather
# than sixty. Neither the harness's bound nor the scope it hands over is what
# changed: the scope is not capped, because a capped scope means inventing a
# partial answer and an answer here means the whole question was answered.
#
# BATCHING IS ONLY SAFE WITH A FALLBACK, and the fallback is what a target
# writing its own command must carry too. A search is capped at LIMIT results.
# With one path per search that cap is per path; with many paths in one search a
# filled page could be several paths' worth of issues truncated, and a truncated
# page read as complete is a duplicate filed. So a batch whose page fills to the
# limit, and a batch whose search fails, are both re-asked one path at a time —
# which is also what keeps this safe on a tracker whose limit on query length is
# tighter than this one's, since a batch that is too long to search falls back
# rather than failing. A per-path search that fails after that still fails the
# whole answer, on the rule above: reporting the paths that did answer would say
# that nothing is filed against the ones that did not.
#
# THE QUERY BRANCH IS THE SYNC BRANCH'S PAIR, and they are now the same file,
# which is what removes the way they used to be able to drift: the sync branch
# writes one searchable marker per path and records the whole payload under a
# marker of its own, and the query branch searches for exactly those markers,
# declared once below. A target that edits one of them edits it for both. That
# the two agree is a contract the harness requires and cannot enforce — when
# they were two files a target could edit one and not the other, and what that
# bought was no dedupe rather than an error, which is the failure mode being
# one file removes.
#
# ==========================================================================
# THE ITEM CONTRACT — github.sh item
# ==========================================================================
#
#   stdin    One JSON document, carrying "key" (the item's, opaque) and
#            "story_id". It carries "document" — the projection to publish —
#            only where the caller had one, and "status" only where the caller
#            had one, and at least one of the two is always there; see THE
#            THREE MOMENTS below.
#   L5_ITEM_KEY
#            The item's key, the same value the document on stdin carries
#            under "key", taken from the same field so the two cannot disagree.
#   stdout   NOT READ. No reference is recorded and nothing is parsed, so a
#            command that prints a page publishes exactly as one that prints
#            nothing. Say things on stderr.
#   stderr   Why it did not publish. The harness carries a tail of this back as
#            the reason, which is the one line a developer sees.
#   exit 0   Published.
#   exit *   Did not publish. NO EXIT CODE IS READ AS A RETRY, because nothing
#            retries: unlike the sync branch, there is no queue behind this and
#            no later sweep that invokes it again. That is why the sync branch
#            alone has a transient exit 75.
#
# A FAILURE TO PUBLISH REFUSES NOTHING. By the time the harness invokes this,
# the story artifact has been committed and pushed; neither is reconsidered on
# what this says, and l5-plan's exit status is the same whatever happens here.
# So a tracker that is down costs a projection and costs nothing else.
#
# WHAT IS PUBLISHED IS A PROJECTION AND NOT A MOVE. The story artifact in the
# repository is what runs — it is what the harness reads, what decides which
# files each stage may change, and what records who approved the work. This
# copy is for whoever reads the tracker to see what was decided. The harness
# reads it back on no path, and an edit made on the item reaches nothing; the
# projection says so in its own heading, which the harness composes.
#
# IDEMPOTENT GIVEN THE STORY ID. The harness may invoke this more than once for
# one story — a re-plan, a hand invocation — and an item that grew a second
# copy of a story each time would be worse than one that had none. So the
# projection is written between two markers naming the story, and this branch
# searches for them before it writes: found, the block they delimit is replaced;
# absent, one is appended. Two invocations for one story therefore leave one
# projection, and one item can carry the projections of several stories side by
# side.
#
# IT MUST NOT DISTURB THE MARKERS THE SYNC BRANCH WRITES. The sync branch
# writes a key marker, one marker per path and the whole payload into an issue
# body, and the query branch searches for exactly those; filing, dedupe and
# brief fetch all stop working if they go missing. This branch therefore only
# ever replaces the block between its own markers and appends after everything
# else, and it must keep doing both. It is the only one of the three that
# rewrites a body another wrote. A question carrying a status and no document
# rewrites no body at all, so every one of those markers is left exactly as it
# was found.
#
# THE THREE MOMENTS. The harness sends a status at three points in a story's
# life: planned, when the planning session's artifact has been committed and
# pushed; in_progress, when the run for that story begins; and ready_to_merge,
# when that run completes. The first arrives beside a document, because the
# invocation that publishes the projection carries it; the other two arrive
# alone, because there is nothing new to publish then.
#
# THE HARNESS DOES NOT KNOW WHAT A STATUS IS. It knows a key and which of those
# three moments the work has reached; what a token means in a tracker is
# decided here and nowhere else. This copy reads it as a column on a project
# board, and a target whose tracker says it differently — a label, a row in a
# table — changes this file and changes nothing in the harness. No status is
# ever read back: stdout is unread, and the harness parses none and infers
# none.
#
# A STATUS MOVES THE ITEM WHEREVER IT ALREADY IS, which is where this differs
# from the sync branch. That one sets a Status only where it finds one empty,
# because it puts a newly filed item into a column once and never moves it
# between columns; the whole point of these three moments is the movement, so
# this one writes over what it finds.
#
# ==========================================================================
# THE BOARD MECHANICS ARE GENERIC; THE VALUES ARE THE TARGET'S. This file
# carries how a board is written to and no statement about which board: the
# project, its owner, the Status field's name, the column a newly filed item
# lands in, the names of the classification fields and the option each of the
# three moments names are the constants below, and a target sets them in its
# installed .harness/scripts/ copy. A template carrying a project number would
# file another repository's briefs onto this board.
#
# THIS SCRIPT ENUMERATES NO CATEGORY, SEVERITY, CONFIDENCE, EFFORT OR WORKFLOW.
# It writes the values the entry carries, so the acceptable values stay the
# brief schema's enums and the workflows the harness defines, and a category
# added to the schema later is a board option added rather than a second list
# to keep in step here.
#
# It requires gh, authenticated, and jq. Both are the target's business.
# ==========================================================================

set -uo pipefail

# --- what every job shares. Edit these. ----------------------------------
# The project all three jobs work against, its owner, and the name of the
# board's Status field. Declared once here rather than once per job: one
# deployment has one board, and the three jobs disagreeing about which one it
# is was how a status move could report a failure while a filing succeeded.
L5_TRACKER_PROJECT="${L5_TRACKER_PROJECT:-1}"   # a project number or URL; empty means no board
L5_TRACKER_PROJECT_OWNER="${L5_TRACKER_PROJECT_OWNER:-@me}"   # who owns that project
L5_TRACKER_STATUS_FIELD="${L5_TRACKER_STATUS_FIELD:-Status}"

# The searchable marker written once per path the payload carries. The sync
# branch writes it and the query branch searches for exactly it; they are one
# file, so the two cannot drift apart. Change it and both change together.
PATH_MARKER_PREFIX="l5-path: "

# The marker the whole payload is recorded under, so a filed brief can be
# answered back whole rather than as a title and a body. The title and the body
# alone lose the slug, the category, the severity, the confidence, the effort
# and the workflow, and a fetched brief missing them fails the brief schema on
# fields the filing threw away. Written by the sync branch and read by the
# query branch, for the reason above.
PAYLOAD_MARKER_PREFIX="l5-payload: "

# --- what only the sync job uses. Edit these. ----------------------------
LABEL="${L5_SYNC_LABEL:-l5}"
# The option a newly filed entry is put in, by the name the board spells it
# with. An empty option means the item is added and its Status is left at
# whatever the project's own default is, which is what a target whose board has
# no such field gets — so it is a value a target sets in its installed copy,
# and the template carries none of it.
STATUS_OPTION="${L5_SYNC_STATUS_OPTION:-Inbox}"
# The board's fields, by name, that a brief's classification is written into.
# Each is empty here and set in a target's installed copy: an empty name means
# that field is not written, so a board that has no such column files exactly as
# it did before these existed. What goes into them is the value the payload
# carries and nothing this script decides.
CATEGORY_FIELD="${L5_SYNC_CATEGORY_FIELD:-Category}"
SEVERITY_FIELD="${L5_SYNC_SEVERITY_FIELD:-Severity}"
CONFIDENCE_FIELD="${L5_SYNC_CONFIDENCE_FIELD:-Confidence}"
EFFORT_FIELD="${L5_SYNC_EFFORT_FIELD:-Effort}"
WORKFLOW_FIELD="${L5_SYNC_WORKFLOW_FIELD:-Workflow}"
# The area of the target's own vocabulary the work concerns. It is one more
# user of the mechanism above and needs nothing new of it: the value is free
# text the payload carries and this script neither holds the vocabulary nor
# checks a name against it, exactly as it holds none of the values above.
# A brief that named no area writes nothing here, which is the ordinary case
# rather than a failure.
AREA_FIELD="${L5_SYNC_AREA_FIELD:-Area}"

# The label a brief's category is applied under: this prefix followed by the
# category the payload carries. It defaults to something non-empty because it is
# a mechanic rather than a property of a particular board — a label travels with
# the issue and is searchable everywhere, which every target that files briefs
# wants — and a payload carrying no category applies no label at all.
CATEGORY_LABEL_PREFIX="${L5_SYNC_CATEGORY_LABEL_PREFIX:-l5-}"

# The colour a category label is created with. Not an environment constant: it
# is a mechanic rather than something this target files against. It exists so
# the create is idempotent — gh's --force updates a label that already exists
# rather than failing on it, and a create naming no colour would give the label
# a fresh random one every filing.
CATEGORY_LABEL_COLOR="ededed"

# --- what only the item job uses. Edit these. ----------------------------
# The option each of the three moments names, by the name the board spells it
# with. They are read from the environment for the reason the field name is: a
# board saying "In Progress", "Doing" or "En cours" is a configuration rather
# than an edit to the logic above it.
PLANNED_OPTION="${L5_ITEM_PLANNED_OPTION:-Planned}"
IN_PROGRESS_OPTION="${L5_ITEM_IN_PROGRESS_OPTION:-In Progress}"
READY_TO_MERGE_OPTION="${L5_ITEM_READY_TO_MERGE_OPTION:-Implementation Complete}"

# --- what only the query job uses. Edit these. ---------------------------
# How many items one search may return. The harness bounds what it will
# read as well; this bound is about what the tracker is asked for.
LIMIT="${L5_QUERY_LIMIT:-50}"

# How many path markers one search carries. It exists because the number of
# searches, not the cost of one, is what stopped this branch answering a scope
# of any size: see ONE SEARCH PER PATH DOES NOT SCALE above for the measurement
# and for what the fallback below guarantees. It bounds two things at once —
# how long one search's text is, for a tracker whose limit on query length is
# tighter than this one's, and how many paths one filled page can hide, since a
# batch whose page fills to LIMIT is re-asked path by path and a smaller batch
# makes that fallback rarer.
BATCH="${L5_QUERY_BATCH:-20}"

# --- the failure vocabularies -------------------------------------------
# fail_transient is the sync branch's alone. Exit 75 means "the entry stays
# pending and a later sweep tries again", and nothing retries behind the query
# or the item branch, so a 75 there would name a mechanism that does not exist.
fail() { echo "$*" >&2; exit 1; }
fail_transient() { echo "$*" >&2; exit 75; }
fail_terminal()  { echo "$*" >&2; exit 1; }

# Which of the two a shared board helper answers with. It is `fail` by default
# and the sync branch sets it to `fail_transient` before it does any board
# work, so one implementation of each lookup serves both writers while each
# keeps the failure vocabulary its own call site is read with.
BOARD_FAIL=fail

# The two dependencies every job has, checked with the branch's own failure
# helper: an absent gh is transient for the sync branch, because a later sweep
# on a repaired machine files the entry, and terminal for the other two,
# because nothing retries behind them.
require_tools() {
  command -v gh >/dev/null 2>&1 || "$1" "gh is not on PATH"
  command -v jq >/dev/null 2>&1 || "$1" "jq is not on PATH"
}

# --- what makes two field names the same --------------------------------
# Said once for every lookup that matches a field name. The configured name and
# the board's name are compared with their spaces removed and their case
# ignored, so a target that configures "status" against a board whose field is
# titled "Status" resolves that field however the name is spelled — for the
# sync branch and the item branch alike, which is what a rule written twice
# could not promise. Option names are not matched by it; they are compared
# against the board verbatim.
SAME_FIELD_NAME='def same_field_name($a; $b):
    ($a | gsub(" "; "") | ascii_downcase) == ($b | gsub(" "; "") | ascii_downcase);'

# --- resolving the project, its fields and one item's values -------------
# The project's id, its field list and an item's own field values are each read
# at most once per invocation rather than once per write, so the writes below do
# not multiply the reads. Each is read the first time something needs it, so an
# invocation with nothing to write makes none of these calls.
project_id=""
fields=""
item=""
item_id=""

read_the_project() {
  [ -z "$fields" ] || return 0
  project_id="$(gh project view "$L5_TRACKER_PROJECT" --owner "$L5_TRACKER_PROJECT_OWNER" \
                  --format json 2>/dev/null | jq -r '.id // ""')" \
    || "$BOARD_FAIL" "project ${L5_TRACKER_PROJECT} could not be read, so none of its fields were set"
  [ -n "$project_id" ] || "$BOARD_FAIL" "project ${L5_TRACKER_PROJECT} named no id"
  fields="$(gh project field-list "$L5_TRACKER_PROJECT" --owner "$L5_TRACKER_PROJECT_OWNER" \
              --format json 2>/dev/null)" \
    || "$BOARD_FAIL" "the fields of project ${L5_TRACKER_PROJECT} could not be read"
  [ -n "$fields" ] || "$BOARD_FAIL" "project ${L5_TRACKER_PROJECT} named no fields"
}

read_the_item() {
  [ -z "$item" ] || return 0
  # What the board already says about this item, asked for by the item's own
  # node id rather than selected out of a listing of the whole project. The id
  # is already in hand, so one graphql read answers the same question
  # consistently: it is not read against an index that lags behind an add, and
  # it has no size to outgrow as the board grows.
  #
  # An item whose field values could not be obtained -- the read failing, or
  # answering with no such node -- is a failure to know rather than a set of
  # empty fields: writing on the strength of an answer that did not describe
  # the item would overwrite values a human put there.
  local answered
  answered="$(gh api graphql -f item="$item_id" -f query='
    query($item: ID!) {
      node(id: $item) {
        ... on ProjectV2Item {
          fieldValues(first: 100) {
            nodes {
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                field { ... on ProjectV2FieldCommon { name } }
              }
            }
          }
        }
      }
    }' 2>/dev/null)" \
    || "$BOARD_FAIL" "the field values of item ${item_id} in project ${L5_TRACKER_PROJECT} could not be read, so its fields are unknown"
  # One object mapping field name to value. Only single-select values are
  # selected, because those are the only ones this script writes, and an entry
  # missing either half is dropped -- so a field the board reports no value for
  # contributes no key and reads as empty. An answer carrying no such node
  # yields nothing at all rather than an empty object, which is what makes it
  # distinguishable from an item with no values.
  item="$(printf '%s' "$answered" | jq -c '
    (.data.node.fieldValues.nodes? // empty)
    | [ .[] | select((.name? != null) and (.field?.name? != null))
            | {key: .field.name, value: .name} ]
    | from_entries')" \
    || "$BOARD_FAIL" "the field values of item ${item_id} in project ${L5_TRACKER_PROJECT} could not be read, so its fields are unknown"
  [ -n "$item" ] \
    || "$BOARD_FAIL" "the field values of item ${item_id} in project ${L5_TRACKER_PROJECT} could not be obtained, so its fields are unknown"
}

# What the read reports this item's named field as, empty where the board
# reports none.
board_value() {
  printf '%s' "$item" | jq -r --arg name "$1" "$SAME_FIELD_NAME"'
    [to_entries[] | select(same_field_name(.key; $name)) | .value] | .[0] // "" | tostring'
}

# A field's id, by the configured name, matched through the shared rule above.
field_id_for() {
  printf '%s' "$fields" | jq -r --arg name "$1" "$SAME_FIELD_NAME"'
    [.fields[]? | select(same_field_name(.name; $name)) | .id] | .[0] // ""'
}

# An option's id. The field name is matched by the shared definition; the
# option name is matched against the board verbatim, which is deliberate.
option_id_for() {
  printf '%s' "$fields" | jq -r --arg name "$1" --arg option "$2" "$SAME_FIELD_NAME"'
    [.fields[]? | select(same_field_name(.name; $name)) | .options[]? | select(.name == $option) | .id] | .[0] // ""'
}

# ==========================================================================
# The sync job
# ==========================================================================

do_sync() {
  BOARD_FAIL=fail_transient
  require_tools fail_transient

  local key entry title body category severity confidence effort workflow area
  local marker paths encoded existing url category_label current field_id option_id added

  key="${L5_SYNC_KEY:-}"
  [ -n "$key" ] || fail_terminal "L5_SYNC_KEY is empty; there is no key to be idempotent on"

  entry="$(cat)" || fail_transient "the entry could not be read from stdin"

  title="$(printf '%s' "$entry" | jq -r '.payload.title // ("l5: " + .key)')" \
    || fail_terminal "the entry carries no title this command can use"
  body="$(printf '%s' "$entry" | jq -r '.payload.body // ""')" \
    || fail_terminal "the entry carries no body this command can use"

  # The classification the payload carries: one label below, six board fields
  # further down. An absent value reads as empty and nothing is written for it,
  # which is what an entry that is not a brief gets, and what a brief that
  # named no area gets for that one field.
  payload_value() {
    printf '%s' "$entry" | jq -r --arg name "$1" '(.payload[$name] // "") | tostring'
  }

  category="$(payload_value category)" || category=""
  severity="$(payload_value severity)" || severity=""
  confidence="$(payload_value confidence)" || confidence=""
  effort="$(payload_value effort)" || effort=""
  workflow="$(payload_value workflow)" || workflow=""
  area="$(payload_value area)" || area=""

  # The key is written into the body, which is what makes the search below able
  # to find it. Change the marker if you like; search for whatever you write.
  marker="l5-sync-key: ${key}"
  body="${body}

<!-- ${marker} -->"

  # One marker per path the payload carries, so the query branch can find this
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

  # The whole payload, recorded once under its own marker so the query branch
  # can answer a brief-fetch question with the brief as it was filed. Encoded
  # rather than written as JSON, because a JSON document written raw into an
  # HTML comment carries newlines and can carry the comment's own terminator;
  # jq does both halves, so neither branch needs a base64 binary.
  encoded="$(printf '%s' "$entry" | jq -r '(.payload // {}) | tojson | @base64')" \
    || fail_terminal "the entry's payload could not be encoded"
  body="${body}

<!-- ${PAYLOAD_MARKER_PREFIX}${encoded} -->"

  # --- idempotency: search before creating ------------------------------
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

  # --- the category label -----------------------------------------------
  # The issue exists by here, whichever path we came down, so both calls are
  # transient on failure, exactly as the board work below is: a later sync
  # re-runs this whole command, the search finds the issue, and the label work
  # is retried. An entry whose payload carries no category makes neither call
  # and files with the one label it always had.
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

  # --- the board ---------------------------------------------------------
  # The issue exists by here, whichever path we came down, so every failure
  # below is transient: a later sync re-runs this whole command, the search
  # finds the issue, and the board work is retried.
  if [ -n "$L5_TRACKER_PROJECT" ]; then
    # item-add for an issue already on the board reports the existing item
    # rather than adding a second one, which is what makes the retry safe. The
    # item's id is what item-edit takes; it deals in ids and not in names.
    added="$(gh project item-add "$L5_TRACKER_PROJECT" --owner "$L5_TRACKER_PROJECT_OWNER" \
               --url "$url" --format json 2>/dev/null)" \
      || fail_transient "the issue was created at ${url} but it could not be added to project ${L5_TRACKER_PROJECT}"
    item_id="$(printf '%s' "$added" | jq -r '.id // ""')" \
      || fail_transient "the issue was added to project ${L5_TRACKER_PROJECT} but the item id could not be read"
    [ -n "$item_id" ] || fail_transient "the issue was added to project ${L5_TRACKER_PROJECT} but it named no item"

    if [ -n "$STATUS_OPTION" ]; then
      read_the_item
      current="$(board_value "$L5_TRACKER_STATUS_FIELD")" \
        || fail_transient "the item's ${L5_TRACKER_STATUS_FIELD} could not be read"

      # Set it only where it is empty. A sweep re-running over an entry that
      # landed long ago finds a value here and leaves it alone; this branch puts
      # an item into a column once and never moves it between columns.
      if [ -z "$current" ]; then
        read_the_project
        field_id="$(field_id_for "$L5_TRACKER_STATUS_FIELD")" \
          || fail_transient "the fields of project ${L5_TRACKER_PROJECT} could not be read"
        option_id="$(option_id_for "$L5_TRACKER_STATUS_FIELD" "$STATUS_OPTION")" \
          || fail_transient "the options of ${L5_TRACKER_STATUS_FIELD} could not be read"

        # A Status name that resolves to no id is transient like everything else
        # after the issue exists: a misconfigured field or option name costs a
        # pending entry, and the alternative costs the item. This is the one
        # write that answers that way — the classification fields below are
        # skipped instead, because the column an item lands in is not something
        # this branch may quietly decline to set.
        [ -n "$field_id" ] || fail_transient "project ${L5_TRACKER_PROJECT} has no field named ${L5_TRACKER_STATUS_FIELD}"
        [ -n "$option_id" ] || fail_transient "${L5_TRACKER_STATUS_FIELD} in project ${L5_TRACKER_PROJECT} has no option named ${STATUS_OPTION}"

        gh project item-edit --id "$item_id" --project-id "$project_id" \
          --field-id "$field_id" --single-select-option-id "$option_id" >/dev/null 2>&1 \
          || fail_transient "the item is on project ${L5_TRACKER_PROJECT} but its ${L5_TRACKER_STATUS_FIELD} could not be set to ${STATUS_OPTION}"
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
        || fail_transient "the fields of project ${L5_TRACKER_PROJECT} could not be read"
      if [ -z "$field_id" ]; then
        echo "project ${L5_TRACKER_PROJECT} has no field named ${field_name}, so ${value} was not written there" >&2
        return 0
      fi
      option_id="$(option_id_for "$field_name" "$value")" \
        || fail_transient "the options of ${field_name} could not be read"
      if [ -z "$option_id" ]; then
        echo "${field_name} in project ${L5_TRACKER_PROJECT} has no option named ${value}, so it was not written there" >&2
        return 0
      fi

      gh project item-edit --id "$item_id" --project-id "$project_id" \
        --field-id "$field_id" --single-select-option-id "$option_id" >/dev/null 2>&1 \
        || fail_transient "the item is on project ${L5_TRACKER_PROJECT} but its ${field_name} could not be set to ${value}"
    }

    set_board_field "$CATEGORY_FIELD" "$category"
    set_board_field "$SEVERITY_FIELD" "$severity"
    set_board_field "$CONFIDENCE_FIELD" "$confidence"
    set_board_field "$EFFORT_FIELD" "$effort"
    set_board_field "$WORKFLOW_FIELD" "$workflow"
    set_board_field "$AREA_FIELD" "$area"
  fi

  echo "$url"
  exit 0
}

# ==========================================================================
# The query job
# ==========================================================================

# The batched search's own state, held here rather than in do_query's locals
# because query_batch reads and appends to all four. `found` accumulates the
# pages every search returned, in the order they were made; the other three are
# the batch being assembled.
found=""
batch_search=""
batch_paths=""
batch_count=0

# Search for one batch's markers at once, and fall back to one search per path
# where the batch's answer cannot be trusted.
#
# Two answers cannot be trusted and both fall back rather than being read. A
# search that exited non-zero says nothing about what is filed against any of
# its paths. And a page holding LIMIT items is a page the tracker truncated:
# with one path per search that cap is per path, but a batch's filled page could
# be several paths' worth of issues cut off, and a truncated page read as
# complete is a duplicate filed. The fallback is also what keeps this safe on a
# tracker whose limit on query length is tighter than this one's — a batch too
# long to search fails, and failing is what re-asks its paths one at a time.
#
# A per-path search that fails after that fails the whole answer, which is the
# behaviour this branch has always had: reporting the paths that did answer
# would say that nothing is filed against the ones that did not.
query_batch() {
  local page returned fallback one marker

  echo "searching for ${batch_count} path marker(s) in one search" >&2
  fallback=0
  if page="$(gh issue list --search "$batch_search" --state all --limit "$LIMIT" \
               --json number,title,body,url,state,stateReason 2>/dev/null)"; then
    returned="$(printf '%s' "$page" | jq 'length' 2>/dev/null)" || returned=""
    if [ -z "$returned" ]; then
      echo "the batched search's page could not be counted, so it is re-asked one path at a time" >&2
      fallback=1
    elif [ "$returned" -ge "$LIMIT" ]; then
      echo "the batched search filled its page of ${LIMIT}, so it may be truncated and is re-asked one path at a time" >&2
      fallback=1
    fi
  else
    echo "the batched search failed, so it is re-asked one path at a time" >&2
    fallback=1
  fi

  if [ "$fallback" -eq 0 ]; then
    found="${found}${page}
"
    return 0
  fi

  while IFS= read -r one; do
    [ -n "$one" ] || continue
    marker="${PATH_MARKER_PREFIX}${one}"
    echo "searching for ${marker}" >&2
    page="$(gh issue list --search "\"${marker}\"" --state all --limit "$LIMIT" \
              --json number,title,body,url,state,stateReason 2>/dev/null)" \
      || fail "the search for ${one} failed, so what is filed is not known"
    found="${found}${page}
"
  done <<BATCH_PATHS
$batch_paths
BATCH_PATHS
}

do_query() {
  require_tools fail

  local question key body encoded asked paths one

  question="$(cat)" || fail "the question could not be read from stdin"

  # Which question this is. A key means the fetch; anything else is the dedupe
  # question, and that path is left exactly as it was so neither question
  # changed the other.
  key="$(printf '%s' "$question" | jq -r '(.key // "")' 2>/dev/null)" \
    || fail "the question on stdin is not a JSON document"

  if [ -n "$key" ]; then
    # The key is this branch's own — it is what the dedupe answer reports as an
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
      # answer from failing to answer, and saying so is this branch's job.
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

  # BATCH markers to a search rather than one search per path, so the number of
  # searches is proportional to the scope divided by BATCH rather than to the
  # scope. Each batch's search text is its markers quoted and joined with OR;
  # every page goes into `found` and the composition below unions them.
  found=""
  batch_search=""
  batch_paths=""
  batch_count=0
  while IFS= read -r one; do
    [ -n "$one" ] || continue
    if [ "$batch_count" -gt 0 ]; then
      batch_search="${batch_search} OR "
    fi
    batch_search="${batch_search}\"${PATH_MARKER_PREFIX}${one}\""
    batch_paths="${batch_paths}${one}
"
    batch_count=$((batch_count + 1))
    if [ "$batch_count" -ge "$BATCH" ]; then
      query_batch
      batch_search=""
      batch_paths=""
      batch_count=0
    fi
  done <<PATHS
$paths
PATHS

  # The last batch, which is short of BATCH whenever the scope does not divide
  # by it. A scope smaller than one batch is answered by exactly one search.
  if [ "$batch_count" -gt 0 ]; then
    query_batch
  fi

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
}

# ==========================================================================
# The item job
# ==========================================================================

do_item() {
  require_tools fail

  local question key story_id document status option
  local begin end body kept updated url added project field_id option_id

  question="$(cat)" || fail "the question could not be read from stdin"

  # The key from the environment, which carries the same value the document does.
  # Falling back to the document rather than requiring the variable keeps the two
  # spellings one value: they come from one field on the harness's side.
  key="${L5_ITEM_KEY:-}"
  if [ -z "$key" ]; then
    key="$(printf '%s' "$question" | jq -r '.key // ""')" \
      || fail "the question carries no key this command can use"
  fi
  [ -n "$key" ] || fail "no item key was given; there is nothing to publish onto"

  story_id="$(printf '%s' "$question" | jq -r '.story_id // ""')" \
    || fail "the question carries no story id this command can use"
  [ -n "$story_id" ] || fail "the question named no story id, so nothing could be marked with one"

  document="$(printf '%s' "$question" | jq -r '.document // ""')" \
    || fail "the question carries no document this command can use"

  status="$(printf '%s' "$question" | jq -r '.status // ""')" \
    || fail "the question carries no status this command can use"

  # One of the two is always there on the harness's side, so a question carrying
  # neither is a question with nothing to say and is refused rather than treated
  # as a no-op that succeeded.
  if [ -z "$document" ] && [ -z "$status" ]; then
    fail "the question carried neither a document nor a status; there was nothing to do to ${key}"
  fi

  # --- the document: publish the projection -----------------------------
  # Only where one was given. A status arriving alone leaves the item's body
  # exactly as it is, which is what the two run-time moments want: the projection
  # the planning session published is already there and has not changed.
  if [ -n "$document" ]; then
    # The markers this story's projection lives between. They name the story, so
    # an item carrying several stories' projections keeps each of them separate,
    # and a second invocation for one story replaces that story's block alone.
    begin="<!-- l5-story-begin: ${story_id} -->"
    end="<!-- l5-story-end: ${story_id} -->"

    body="$(gh issue view "$key" --json body --jq '.body // ""' 2>/dev/null)" \
      || fail "the item ${key} could not be read, so nothing was published onto it"

    # Everything the item already says, with this story's own block removed if
    # one is there. Only the region between this story's markers is dropped: the
    # key marker, the path markers and the payload marker the sync branch wrote
    # are elsewhere in the body and are carried through untouched, which is what
    # keeps filing, dedupe and brief fetch working over an item that has been
    # published onto.
    kept="$(printf '%s' "$body" | awk -v begin="$begin" -v end="$end" '
      index($0, begin) { skipping = 1; next }
      index($0, end)   { skipping = 0; next }
      !skipping        { print }
    ')" || fail "the item ${key} body could not be read, so nothing was published onto it"

    updated="$(mktemp)" || fail "no temporary file could be made to write the body in"
    trap 'rm -f "$updated"' EXIT

    {
      printf '%s\n' "$kept"
      printf '\n%s\n\n' "$begin"
      printf '%s\n' "$document"
      printf '\n%s\n' "$end"
    } >"$updated" || fail "the new body could not be written"

    gh issue edit "$key" --body-file "$updated" >/dev/null 2>&1 \
      || fail "the item ${key} could not be updated, so ${story_id} was not published onto it"
  fi

  # --- the status: which column each token names ------------------------
  # The whole of what this branch knows about a token is which option it names.
  # An unrecognised one is refused rather than guessed at: writing a column
  # nobody asked for would be worse than saying the word was not understood.
  #
  # This runs after the document above rather than before it, so a status that
  # cannot be honoured leaves the projection on the item and then exits
  # non-zero, instead of preventing it from ever being published.
  option=""
  if [ -n "$status" ]; then
    case "$status" in
      planned)        option="$PLANNED_OPTION" ;;
      in_progress)    option="$IN_PROGRESS_OPTION" ;;
      ready_to_merge) option="$READY_TO_MERGE_OPTION" ;;
      *) fail "the status ${status} is not one this command knows an option for, so ${key} was not moved" ;;
    esac
    [ -n "$option" ] \
      || fail "this copy names no board option for ${status}, so ${key} was not moved"
  fi

  # --- the status: move the item's column -------------------------------
  # Reached only where a status was given and a project is configured. An empty
  # project is a statement about this target rather than a failure — it declares
  # no board, so there is nothing here that failed — and the board work is
  # skipped silently without reaching the tracker, exactly as the sync branch
  # above skips it. Where there is a board, every way of not reaching it — the
  # item, the project, the field or the option — is said on stderr and exits
  # non-zero: an item that was not moved must not be reported as one that was.
  if [ -n "$status" ] && [ -n "$L5_TRACKER_PROJECT" ]; then
    url="$(gh issue view "$key" --json url --jq '.url // ""' 2>/dev/null)" \
      || fail "the item ${key} could not be read, so it was not moved to ${option}"
    [ -n "$url" ] || fail "the item ${key} named no URL, so it was not moved to ${option}"

    # item-add for an issue already on the board reports the existing item rather
    # than adding a second one, so an item this project already carries is found
    # here and one it does not is put on it. The item's id is what item-edit
    # takes; it deals in ids and not in names.
    added="$(gh project item-add "$L5_TRACKER_PROJECT" --owner "$L5_TRACKER_PROJECT_OWNER" \
               --url "$url" --format json 2>/dev/null)" \
      || fail "${key} could not be added to project ${L5_TRACKER_PROJECT}, so it was not moved to ${option}"
    item_id="$(printf '%s' "$added" | jq -r '.id // ""')" \
      || fail "project ${L5_TRACKER_PROJECT} named no item for ${key}, so it was not moved to ${option}"
    [ -n "$item_id" ] \
      || fail "project ${L5_TRACKER_PROJECT} named no item for ${key}, so it was not moved to ${option}"

    # The project's id, its field list, the field's id and the option's id, all
    # resolved through the same helpers the sync branch resolves them through —
    # so a field name spelled with different case resolves here exactly as it
    # resolves there.
    read_the_project
    field_id="$(field_id_for "$L5_TRACKER_STATUS_FIELD")" \
      || fail "the fields of project ${L5_TRACKER_PROJECT} could not be read, so ${key} was not moved to ${option}"
    [ -n "$field_id" ] \
      || fail "project ${L5_TRACKER_PROJECT} has no field named ${L5_TRACKER_STATUS_FIELD}, so ${key} was not moved to ${option}"

    option_id="$(option_id_for "$L5_TRACKER_STATUS_FIELD" "$option")" \
      || fail "the options of ${L5_TRACKER_STATUS_FIELD} could not be read, so ${key} was not moved to ${option}"
    [ -n "$option_id" ] \
      || fail "${L5_TRACKER_STATUS_FIELD} in project ${L5_TRACKER_PROJECT} has no option named ${option}, so ${key} was not moved"

    # Written over whatever the field already says. This is the movement the
    # three moments exist for, so unlike the sync branch there is no
    # only-where-empty rule: an item that is already in a column is exactly the
    # item a later moment has to move out of it.
    gh project item-edit --id "$item_id" --project-id "$project_id" \
      --field-id "$field_id" --single-select-option-id "$option_id" >/dev/null 2>&1 \
      || fail "the item is on project ${L5_TRACKER_PROJECT} but its ${L5_TRACKER_STATUS_FIELD} could not be set to ${option}"
  fi

  exit 0
}

# ==========================================================================
# The dispatcher
#
# It refuses rather than guesses. A first argument that is not one of the three
# jobs, and no first argument at all, each exit non-zero saying what they were
# given and what the jobs are, so a mistyped configuration is reported rather
# than silently answered as some other job. Neither case reads stdin, reaches a
# tracker, or performs any part of any job.
# ==========================================================================

job="${1:-}"
case "$job" in
  sync)  shift; do_sync ;;
  query) shift; do_query ;;
  item)  shift; do_item ;;
  "")    fail "no job was named; this command answers to one of: sync, query, item" ;;
  *)     fail "the job ${job} is not one this command answers to; it answers to one of: sync, query, item" ;;
esac
