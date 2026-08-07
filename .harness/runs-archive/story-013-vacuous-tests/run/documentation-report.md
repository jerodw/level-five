# story-013 — documentation report

## Documents updated

### `.harness/docs/ARCHITECTURE.md`

The technical plan predicted this file, and the verifier explicitly flagged it as
expected-stale at its stage. Four sites changed, all inside the *Artifact schemas
(`schemas/`)* section, the orchestration module list, and *Decisions and
constraints*.

**1. Opening paragraph of *Artifact schemas*.** Added that the per-artifact
schema list is *named by* `schemas/manifest.json`. The six names it recorded are
unchanged — this story added and removed no schema.

**2. The inventory paragraph (rewritten).** It previously read:

> The schemas directory is an *inventory* two tests assert set equality over
> (`tests/test_schema_validator.py` and `tests/test_story_004_validation.py`).
> Adding a schema file necessarily fails both, by design … Expect to update both
> in the same story …

That was the stale two-copy manual-sync rule, and it named the two test files as
the place the fact lived. It is replaced by four paragraphs recording:

- The manifest as the one declared inventory — its shape (object, single
  `schemas` key, sorted kebab-case stems without the suffix, matching how
  `load_schema` names them), read through `schema_validator.shipped_schemas()`.
- That the two tests still assert **exact set equality** in **both directions**
  (file without entry fails; entry without file fails), with the standing
  instruction never to relax it to a subset or containment check carried
  forward verbatim in force — that instruction was the one thing in the old
  paragraph worth keeping, and it is now attached to the manifest rather than to
  the test literals.
- *Why the placement is the point*, including the story-011 collision that
  forced it: `execution-history.schema.json` landed with three `tests/` paths in
  its `changed-files.json`, the verifier raised it as blocking, and the retry
  could only record it as a deviation. This is the fact a future planner most
  needs, because without it the next planner reads the move as tidying and may
  re-file the inventory somewhere convenient. It also records the consequence
  they can now rely on: **adding a schema requires no edit under `tests/`**, so
  the standing `tests/`-independence sentence holds verbatim.
- The accepted weakness — manifest written and read by the same stage — and
  what buys it down (one-line diff of filename stems; every named schema still
  passing the draft-2020-12, unsupported-keyword and no-`additionalProperties`
  checks parametrized over it). Recorded as a deliberate trade rather than left
  for a later reviewer to rediscover as a gap.
- The narrowed `*.schema.json` glob, the companion "nothing but `*.schema.json`
  plus `manifest.json`" assertion that closes the hole the narrowing would open,
  and the two negative facts a planner would otherwise have to re-derive:
  `schema_context` already globs `*.schema.json` so no `{{manifest_schema}}`
  placeholder exists, and nothing in orchestration routes on the manifest.

**3. The "schemas ship with the harness code" paragraph.** Extended to name
`shipped_schemas` alongside `schemas_dir` and `load_schema` as carrying the same
optional `harness_root` override, and to record that `shipped_schemas` resolves
the manifest *through* `schemas_dir` so the three behave identically — which is
what makes a throwaway-harness-root test possible.

**4. The `schema_validator.py` bullet in the module list.** Added
`shipped_schemas` (and `schemas_dir`) to the function list, with its signature,
the `MANIFEST_NAME`/`MANIFEST_KEY` constants that keep the manifest filename out
of the tests, and its raise-loudly contract — specifically that it raises rather
than returning an empty inventory, because an empty one would make the
parametrized per-schema checks silently vacuous.

**5. New bullet under *Decisions and constraints*.** The transferable lesson,
placed beside the existing `FIRST_SCHEMA_ERA_STORY` bullet it contrasts with: a
declared fact belongs with the thing it describes, and which thing that is
decides which stage owns it. The inventory is a fact about what the harness
*ships* (→ `schemas/`, implementer); the era constant is a fact about what the
corpus tests *validate* (→ `tests/`, deliberately not moved). It also records
the two roads not taken — routing the inventory edit to the tester (rejected:
leaves the suite red between stages and treats a misplaced source of truth as a
routing problem) and weakening `may_not_create` or the `tests/`-independence
wording (rejected: the collision was removed by moving a definition) — and the
meta-rule this story demonstrated: a story that must delete a misplaced
definition cannot carry the requirement it is protecting, so it writes the
narrower true requirement for itself rather than relaxing the standing one.

## Deliberately not changed

- The `execution-history.schema.json` exception paragraph. It says
  `schema_context` "makes any file in `schemas/` an injectable placeholder";
  that was already imprecise before this story (the glob was `*.schema.json`
  then too) and the sentence is about *why that schema exists*, not about the
  glob. Rewriting it would be editing a section this story did not affect.
- The `FIRST_SCHEMA_ERA_STORY` bullet's own text. Its stated home under `tests/`
  is still accurate; the open request proposing a move to `tests/conftest.py`
  stays open and unblocked, and recording a move that has not happened would be
  wrong.
- Everything about the coordinator, the run-directory anatomy, prompts, and
  workflow definitions. No coordinator behavior changed, no stage prompt is
  re-rendered, and `story_coordinator.py` was untouched.
- No implementation, test, or test-adjacent file was edited by this stage. The
  only file changed is `.harness/docs/ARCHITECTURE.md`.

## Verifier's open notes

Neither `unverified` item asked for a documentation change. The first was this
file's own staleness, now closed. The second — that the mutation tests in
`tests/test_story_013_validation.py` assert a non-zero pytest exit code rather
than a specific failing assertion — is a test-precision observation about a file
this stage may not modify, and it is not an architectural fact worth recording
in the document. It stays where the verifier put it.
