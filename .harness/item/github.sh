#!/usr/bin/env bash
#
# A reference item-update command: publishes a planned story onto the GitHub
# issue the brief was filed as, and moves that issue's board Status as the
# story is planned, run and finished.
#
# This is a template. It ships with the harness, l5-init installs a copy into a
# target's .harness/item/, and the target is expected to edit it — where a
# projection is written, what it looks like once it is there, and which board
# and column a status names, are this file's business rather than the
# harness's. What is not negotiable is the contract below, which the harness
# relies on and cannot enforce.
#
# THE CONTRACT
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
#            retries: unlike a sync command, there is no queue behind this and
#            no later sweep that invokes it again. That is why this script has
#            one failure helper rather than the sync script's two.
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
# projection is written between two markers naming the story, and this script
# searches for them before it writes: found, the block they delimit is replaced;
# absent, one is appended. Two invocations for one story therefore leave one
# projection, and one item can carry the projections of several stories side by
# side.
#
# IT MUST NOT DISTURB THE OTHER TWO SCRIPTS' MARKERS. templates/sync/github.sh
# writes a key marker, one marker per path and the whole payload into an issue
# body, and templates/query/github.sh searches for exactly those; filing,
# dedupe and brief fetch all stop working if they go missing. This script
# therefore only ever replaces the block between its own markers and appends
# after everything else, and it must keep doing both. It is the third member of
# that family and the only one that rewrites a body somebody else wrote. A
# question carrying a status and no document rewrites no body at all, so every
# one of those markers is left exactly as it was found.
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
# THE BOARD MECHANICS ARE GENERIC; THE VALUES ARE THE TARGET'S. This file
# carries how a Status is written and no statement about which board: the
# project, its owner, the field's name and the option each of the three tokens
# names are the constants below, so a board that spells its columns differently
# is a configuration rather than an edit to this logic. An empty project means
# no board, and then a status is something this copy cannot honour and says so.
#
# A STATUS MOVES THE ITEM WHEREVER IT ALREADY IS, which is where this differs
# from the sync script beside it. That script sets a Status only where it finds
# one empty, because it puts a newly filed item into a column once and never
# moves it between columns; the whole point of these three moments is the
# movement, so this one writes over what it finds.
#
# It requires gh, authenticated, and jq. Both are the target's business.

set -uo pipefail

# --- what this target moves items on. Edit these. ------------------------
PROJECT="${L5_ITEM_PROJECT:-}"   # a project number or URL; empty means no board
PROJECT_OWNER="${L5_ITEM_PROJECT_OWNER:-@me}"   # who owns that project
STATUS_FIELD="${L5_ITEM_STATUS_FIELD:-Status}"

# The option each of the three tokens names, by the name the board spells it
# with. They are read from the environment for the reason the field name is: a
# board saying "In Progress", "Doing" or "En cours" is a configuration rather
# than an edit to the logic above it.
PLANNED_OPTION="${L5_ITEM_PLANNED_OPTION:-Planned}"
IN_PROGRESS_OPTION="${L5_ITEM_IN_PROGRESS_OPTION:-In progress}"
READY_TO_MERGE_OPTION="${L5_ITEM_READY_TO_MERGE_OPTION:-Ready to merge}"

# How much of the project's item listing is read when looking for this issue's
# item. Not an L5_ITEM_ constant, for the reason the sync script's own bound is
# not: it is a mechanic rather than something this target files against, and a
# bound that was too small costs a reported failure rather than a wrong write.
ITEM_LIST_LIMIT=5000

fail() { echo "$*" >&2; exit 1; }

command -v gh >/dev/null 2>&1 || fail "gh is not on PATH"
command -v jq >/dev/null 2>&1 || fail "jq is not on PATH"

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

# --- the status: which column each token names --------------------------
# The whole of what this script knows about a token is which option it names.
# An unrecognised one is refused rather than guessed at: writing a column
# nobody asked for would be worse than saying the word was not understood.
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
  [ -n "$PROJECT" ] \
    || fail "no project is configured here, so there is no board to move ${key} to ${option} on"
fi

# --- the document: publish the projection -------------------------------
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
  # key marker, the path markers and the payload marker the sync script wrote
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

# --- the status: move the item's column ---------------------------------
# Reached only where a status was given, and the project was established above,
# so everything from here is the board work itself. Every way of not reaching
# the board — the item, the project, the field or the option — is said on
# stderr and exits non-zero: an item that was not moved must not be reported as
# one that was.
if [ -n "$status" ]; then
  url="$(gh issue view "$key" --json url --jq '.url // ""' 2>/dev/null)" \
    || fail "the item ${key} could not be read, so it was not moved to ${option}"
  [ -n "$url" ] || fail "the item ${key} named no URL, so it was not moved to ${option}"

  # item-add for an issue already on the board reports the existing item rather
  # than adding a second one, so an item this project already carries is found
  # here and one it does not is put on it. The item's id is what item-edit
  # takes; it deals in ids and not in names.
  added="$(gh project item-add "$PROJECT" --owner "$PROJECT_OWNER" --url "$url" \
             --format json 2>/dev/null)" \
    || fail "${key} could not be added to project ${PROJECT}, so it was not moved to ${option}"
  item_id="$(printf '%s' "$added" | jq -r '.id // ""')" \
    || fail "project ${PROJECT} named no item for ${key}, so it was not moved to ${option}"
  [ -n "$item_id" ] \
    || fail "project ${PROJECT} named no item for ${key}, so it was not moved to ${option}"

  project_id="$(gh project view "$PROJECT" --owner "$PROJECT_OWNER" --format json 2>/dev/null \
                  | jq -r '.id // ""')" \
    || fail "project ${PROJECT} could not be read, so ${key} was not moved to ${option}"
  [ -n "$project_id" ] \
    || fail "project ${PROJECT} named no id, so ${key} was not moved to ${option}"

  fields="$(gh project field-list "$PROJECT" --owner "$PROJECT_OWNER" --format json 2>/dev/null)" \
    || fail "the fields of project ${PROJECT} could not be read, so ${key} was not moved to ${option}"

  field_id="$(printf '%s' "$fields" | jq -r --arg name "$STATUS_FIELD" \
                '[.fields[]? | select(.name == $name) | .id] | .[0] // ""')" \
    || fail "the fields of project ${PROJECT} could not be read, so ${key} was not moved to ${option}"
  [ -n "$field_id" ] \
    || fail "project ${PROJECT} has no field named ${STATUS_FIELD}, so ${key} was not moved to ${option}"

  option_id="$(printf '%s' "$fields" | jq -r --arg name "$STATUS_FIELD" --arg option "$option" \
                 '[.fields[]? | select(.name == $name) | .options[]? | select(.name == $option) | .id] | .[0] // ""')" \
    || fail "the options of ${STATUS_FIELD} could not be read, so ${key} was not moved to ${option}"
  [ -n "$option_id" ] \
    || fail "${STATUS_FIELD} in project ${PROJECT} has no option named ${option}, so ${key} was not moved"

  # Written over whatever the field already says. This is the movement the
  # three moments exist for, so unlike the sync script beside it there is no
  # only-where-empty rule: an item that is already in a column is exactly the
  # item a later moment has to move out of it.
  gh project item-edit --id "$item_id" --project-id "$project_id" \
    --field-id "$field_id" --single-select-option-id "$option_id" >/dev/null 2>&1 \
    || fail "the item is on project ${PROJECT} but its ${STATUS_FIELD} could not be set to ${option}"
fi

exit 0
