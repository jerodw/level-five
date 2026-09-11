#!/usr/bin/env bash
#
# Close the brief a merged story was planned from.
#
#   close-brief.sh <branch-name>
#
# THIS IS TARGET-SIDE GLUE AND IT IS INSTALLED INTO NO TARGET. It assumes
# GitHub, a pull-request model, issues, and a deployment that wants its briefs
# closed when a story merges — assumptions the harness is built not to
# make, which is why this lives beside .harness/config.yaml and github.sh
# rather than under templates/. l5-init ships none of it, and a target on
# another tracker, or one that simply does not want this, is unaffected
# because nothing about it is installed anywhere.
#
# THE HARNESS LEARNS NOTHING FROM THIS. Its last tracker moment is
# ready_to_merge, sent when the run completes; a merge happens afterwards,
# where the harness is not watching, and nothing here gives it a fourth status
# token, a configuration key or any knowledge of what a merge is. What is here
# is a branch name, two values read out of this target's own configuration, one
# field read out of the story artifact the merge landed, and one call to gh.
#
# WHAT IT DOES
#
#   The branch name carries the story id behind the configured branch_prefix.
#   The story artifact under the configured stories_dir carries brief_key,
#   which this deployment happens to spell as an issue number. The issue is
#   asked for its state and closed when it is open.
#
#   Both configured values are READ from .harness/config.yaml rather than
#   restated here, which is the rule story-134 established for this target's
#   configured values: a configured value is read by what depends on it, never
#   written into it a second time. There is deliberately no fallback for
#   either — a fallback is that second writing-down, and a key the harness
#   reads that this file cannot find is a broken deployment rather than a pull
#   request that is not a story's.
#
#   The brief_key read is ANCHORED AT COLUMN ZERO. A brief_key mentioned in
#   prose inside an indented block scalar is not the field, and a story whose
#   description discusses brief_key is not thereby a story that carries one.
#
# WHAT IT MUST NOT DO
#
#   IT REFERENCES THE PULL REQUEST TO THE ISSUE NOWHERE. No comment, no
#   closing keyword, no body edit — nothing that creates a pull-request-to-
#   issue relationship. The board's built-in closed-to-Done automation is what
#   moves the column, and it needs only the close; a second automation drives
#   an item's Status from the state of its linked pull request and fights the
#   harness's own status moves for as long as that pull request is open. So
#   the link is the thing to avoid rather than a detail to get right, and that
#   is why `gh issue close` is invoked with no comment and this script writes
#   nothing anywhere else.
#
# QUIET AND LOUD, AND WHY THE SPLIT IS WHERE IT IS
#
#   Nothing to close is quiet: a branch carrying no configured prefix, an
#   artifact that is absent or carries no brief_key, a key this deployment
#   cannot read as an issue number, and an issue already closed are each
#   ordinary and exit 0 with one sentence on stderr saying which it was. Most
#   merged pull requests on a repository are not a story's, and they must cost
#   nothing and say nothing alarming.
#
#   A refused close is loud: a key that resolved and a close the API refused
#   exits non-zero with the refusal on stderr, so a red check on the merged
#   pull request says the manual step still needs doing. Exiting 0 there would
#   make a silent failure look exactly like a pull request that was never a
#   story's, which is the one outcome this must not have. A state that cannot
#   be read is loud for the same reason: it establishes nothing about whether
#   the brief is open.
#
# DRY RUN
#
#   L5_CLOSE_BRIEF_DRY_RUN, set to any non-empty value, resolves the key and
#   makes no tracker call: the sentence goes to stderr and the bare key is the
#   only thing on stdout. Every path above the tracker is therefore drivable
#   without one, which is how the suite covers the branch derivation and the
#   column-zero read.
#
#   It requires gh, authenticated, unless it is a dry run. gh's own --jq is
#   what reads the state, so jq is not needed here.

set -uo pipefail

BRIEF_KEY_FIELD="brief_key"
BRANCH_PREFIX_KEY="branch_prefix"
STORIES_DIR_KEY="stories_dir"

# The repository this script was installed into, resolved from the script's own
# location rather than from the working directory, so it answers the same
# wherever it is invoked from.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)" || exit 1
CONFIG="${ROOT}/.harness/config.yaml"

# There is nothing to close, and that is ordinary. One sentence saying which of
# them it was, and a zero exit.
nothing_to_close() { echo "$*" >&2; exit 0; }

# Something was to be closed and it did not happen. The job goes red.
refused() { echo "$*" >&2; exit 1; }

# A field's value out of a file, matched at column zero and stripped of the
# whitespace around it. Anchoring is the whole of it: a key named in a comment,
# one nested under another, and one mentioned in prose inside an indented block
# scalar are each not the field, and none of them can answer for it. Both
# readers below go through this, so the configuration read and the brief_key
# read cannot come to disagree about what naming a field means.
field_at_column_zero() {
  local file="$1" key="$2" line
  line="$(grep -m1 "^${key}:" "$file")" || return 1
  line="${line#*:}"
  line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  [ -n "$line" ] || return 1
  printf '%s' "$line"
}

# A value out of this target's own configuration.
configured() { field_at_column_zero "$CONFIG" "$1"; }

branch="${1:-}"
[ -n "$branch" ] || refused "close-brief.sh takes the merged pull request's head branch name as its first argument, and was given none"

[ -f "$CONFIG" ] || refused "there is no harness configuration at ${CONFIG}, so neither the branch prefix nor the stories directory can be read"

branch_prefix="$(configured "$BRANCH_PREFIX_KEY")" ||
  refused "${CONFIG} declares no ${BRANCH_PREFIX_KEY}, and this script reads that value rather than restating it"
stories_dir="$(configured "$STORIES_DIR_KEY")" ||
  refused "${CONFIG} declares no ${STORIES_DIR_KEY}, and this script reads that value rather than restating it"

# --- quiet: the branch is not a story's ---------------------------------
case "$branch" in
  "${branch_prefix}"?*) story_id="${branch#"${branch_prefix}"}" ;;
  *) nothing_to_close "the branch ${branch} does not carry the configured prefix ${branch_prefix}, so it is not a story's branch and there is no brief to close" ;;
esac

artifact="${ROOT}/${stories_dir}/${story_id}.yaml"

# --- quiet: the story landed no artifact --------------------------------
[ -f "$artifact" ] || nothing_to_close "there is no story artifact at ${stories_dir}/${story_id}.yaml, so nothing says which brief ${story_id} was planned from"

# --- quiet: the artifact carries no brief_key ---------------------------
brief_key="$(field_at_column_zero "$artifact" "$BRIEF_KEY_FIELD")" ||
  nothing_to_close "the story artifact for ${story_id} carries no top-level ${BRIEF_KEY_FIELD}, so it records no brief to close"

# --- quiet: the key is opaque to the harness and unreadable here --------
# The harness treats a brief key as opaque; this deployment reads one as an
# issue number, and this script is the only place that decides so.
case "$brief_key" in
  *[!0-9]*) nothing_to_close "${story_id} was planned from the brief ${brief_key}, which this deployment cannot read as an issue number, so there is nothing it knows how to close" ;;
esac

if [ -n "${L5_CLOSE_BRIEF_DRY_RUN:-}" ]; then
  echo "dry run: ${story_id} was planned from the brief ${brief_key}, and no tracker call was made" >&2
  printf '%s\n' "$brief_key"
  exit 0
fi

command -v gh >/dev/null 2>&1 || refused "${story_id} was planned from the brief ${brief_key} and gh is not on PATH, so the brief is still open"

cd "$ROOT" || refused "${ROOT} could not be entered, so the brief ${brief_key} is still open"

# The state is asked for before anything is closed, so a re-run, and a branch
# reverted and merged again, are a no-op this script decides rather than one
# inferred from gh's exit code for an issue that was already closed. A state
# that cannot be read establishes nothing about whether the brief is open, so
# it is loud.
state="$(gh issue view "$brief_key" --json state --jq .state 2>&1)" ||
  refused "the state of issue ${brief_key}, the brief ${story_id} was planned from, could not be read, so nothing establishes whether it is still open: ${state}"

if [ "$state" != "OPEN" ]; then
  nothing_to_close "issue ${brief_key}, the brief ${story_id} was planned from, is already ${state}, so it is left alone"
fi

# No comment, no body edit, no closing keyword: see WHAT IT MUST NOT DO above.
if ! output="$(gh issue close "$brief_key" 2>&1)"; then
  refused "closing issue ${brief_key}, the brief ${story_id} was planned from, was refused: ${output}"
fi

echo "closed issue ${brief_key}, the brief ${story_id} was planned from" >&2
exit 0
