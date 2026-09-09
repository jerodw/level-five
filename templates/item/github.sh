#!/usr/bin/env bash
#
# A reference item-update command: publishes a planned story onto the GitHub
# issue the brief was filed as.
#
# This is a template. It ships with the harness, l5-init installs a copy into a
# target's .harness/item/, and the target is expected to edit it — where a
# projection is written, and what it looks like once it is there, are this
# file's business rather than the harness's. What is not negotiable is the
# contract below, which the harness relies on and cannot enforce.
#
# THE CONTRACT
#
#   stdin    One JSON document, carrying "key" (the item's, opaque),
#            "story_id", and "document" — the projection to publish. It carries
#            "status" only where the caller supplied one, and nothing in the
#            harness supplies one yet; see THE STATUS HALF below.
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
# that family and the only one that rewrites a body somebody else wrote.
#
# THE STATUS HALF IS DECLARED AND UNSENT. The question document may carry a
# "status", because the sibling work that moves an item's status as its story
# runs is about the same item, and two scripts that must agree about one item
# are two scripts that can disagree. Nothing in the harness sends one today, so
# nothing here acts on one; that work fills this half in rather than adding a
# second script beside this one. The harness parses no tracker status and
# infers none either.
#
# It requires gh, authenticated, and jq. Both are the target's business.

set -uo pipefail

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
[ -n "$document" ] || fail "the question carried an empty document; nothing was published"

# The markers this story's projection lives between. They name the story, so an
# item carrying several stories' projections keeps each of them separate, and a
# second invocation for one story replaces that story's block alone.
begin="<!-- l5-story-begin: ${story_id} -->"
end="<!-- l5-story-end: ${story_id} -->"

body="$(gh issue view "$key" --json body --jq '.body // ""' 2>/dev/null)" \
  || fail "the item ${key} could not be read, so nothing was published onto it"

# Everything the item already says, with this story's own block removed if one
# is there. Only the region between this story's markers is dropped: the key
# marker, the path markers and the payload marker the sync script wrote are
# elsewhere in the body and are carried through untouched, which is what keeps
# filing, dedupe and brief fetch working over an item that has been published
# onto.
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

exit 0
