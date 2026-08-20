# Texts this repository once carried

Every file here is a text this repository held at a named past bound, committed
so that a test can read it from the working tree instead of resolving it out of
the commit graph.

They arrived with story-053, which converted twenty-six modules under `tests/`
off `story_commit_range`, `story_diff`, `repository_file_at`,
`function_source_at` and `revision_carrying`. Those five are the sanctioned
route to this repository's own history and they stay — what changed is that a
module wanting an *input* (a sentence, an earlier version of a function, the
text of a file before a repair) no longer asks git for it. Asked of the commit
graph, an input's answer moves when something is committed, renamed, squashed
or rebased, and none of that is a property of the code under test:

* a rename gives a path a new add-commit, and every assertion bounded by that
  path's range silently goes green against nothing;
* a squash merge leaves a pinned revision unreachable in a clone even while it
  still resolves in the working repository;
* CI carried `fetch-depth: 0` for no reason other than these resolutions.

A committed fixture is the same evidence with none of that. It is in the tree,
it is diffable, and a story that changes the regression set changes it visibly.

## Naming

    <subject>.at-story-<NNN>-<baseline|endpoint>.<ext>.txt

`<subject>` is the module, function or document the text belongs to;
`baseline` is the parent of the commit that added that story's validation file,
and `endpoint` is that commit itself — exactly the two bounds
`conftest.story_commit_range` resolves. A few names predate that shape:
`test_story_0NN_validation.py.txt` are the four pre-repair sources story-015's
module feeds back through its own scanner, `story-013-tree.json` carries the
name and content digest of every file in `schemas/` and `tests/` at both ends of
story-013's range, and `prompts-*.at-this-storys-baseline.md.txt` belong to the
module whose own story is the newest.

## Reading one

Through `conftest.history_fixture(name)`, which raises rather than returning
empty when a fixture has moved, so a stale reference fails as itself rather than
as an assertion that has stopped seeing anything.

A `.py.txt` suffix and a directory of their own, deliberately: the suite's scans
glob `tests/*.py`, so a fixture is neither collected as a test module nor
reported as carrying the very defect a pre-repair fixture exists to carry.

## Changing one

Don't, unless the claim it supports is changing. These are records of what was,
and every one of them is asserted to be what it claims: each is compared against
something present-tense, or fed through the scan it is the regression case for,
or shown to differ from its counterpart at the other bound.
