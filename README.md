# level-five (l5)

[![tests](https://github.com/jerodw/level-five/actions/workflows/tests.yml/badge.svg)](https://github.com/jerodw/level-five/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A **level 3 agentic harness** built by following *Agentic Programming* by Jerod W. Wilkerson. The harness is a story execution system: stories enter with an approved plan, move through implementation, testing, documentation, and verification, retry when verification fails, and end completed or escalated.

> **About the name.** The name is aspirational. What you'll find here is a *level 3* harness, but level five is where the ladder leads, and the repository is built to grow in that direction.

## Companion to the book

This repository tracks *Agentic Programming* through level 3. Part 3 (Chapters 12–19) explains how an agentic harness works, and **Appendix A, "Building a Sample Level 3 Harness,"** builds this one from an empty directory to a working system, including the real escalations that happened along the way.

The book is at **[agenticprogrammingbook.com](https://agenticprogrammingbook.com)**.

The appendix is the starting point, not the finish line. It stops at a deliberately small harness so the essential structure stays readable, and then hands you a roadmap: harden first, following Chapter 18, then scale, following Chapter 19. This repository is walking that roadmap. Every improvement arrives the way the book says it should — as a story the harness plans, executes, verifies, and documents itself.

### The appendix state

The exact harness Appendix A describes is tagged **`appendix-a`**. If you are reading the appendix and want to check your own build against it, or want to start where the appendix stops, use that tag:

    git clone https://github.com/jerodw/level-five.git
    cd level-five
    git checkout appendix-a

`main` has moved past it. The appendix's code excerpts — the workflow definition, the implementer prompt, the coordinator's routing loop — match the tag, and the differences on `main` are the point rather than drift.

### What has changed since

Each story below is a step the book's roadmap calls for, or a failure the build hit that the roadmap did not anticipate. The story artifacts are committed in `.harness/stories/`. Run directories are execution state rather than source, so `.harness/runs/` is gitignored and does not travel with a clone; the runs worth keeping — four escalations, and three runs preserved for what they showed — are copied into `.harness/runs-archive/`.

| Story | Change | Where the book argues for it |
| --- | --- | --- |
| 001–002 | `l5-status`; per-stage changed-files records | Appendix A (`appendix-a`) |
| 003–004 | One shared harness layer; machine-readable artifact schemas | Ch. 16, prompt layering; Ch. 14, artifact contracts |
| 005–007 | Schema-directed story parser and pre-flight validation; one reader of a story artifact; coordinator-enforced stage output ownership | Ch. 15, governance boundaries; Ch. 18, hardening |
| 008–009 | The story schema and the workflow's stage rules injected into the planner prompt | Ch. 16, injection over restatement |
| 010–012 | `attempts/attempt-N/` archives, `execution-history.json`, `retry-history.json` | Ch. 17–18, retry evidence |
| 013–017 | Verification hardening: the suite re-run in a clean clone, assertions that can be shown to fail, the schema inventory moved out of `tests/`, the coordinator's output contract asserted directly, an implementer's test edits decided by reverting them | Failures this build hit; Ch. 18 in spirit |
| 018–020 | Story artifacts validated at plan time; the revert check reverting to what the stage found rather than to `HEAD`; escalated runs made resumable, committing their work when they stop | Ch. 18, hardening; Ch. 17, retry evidence |
| 021–024 | A run commits only what it produced; required outputs must be written by the attempt that ran; `l5-plan` commits the artifact it caused; `escalation-summary.md` carries the finding rather than a pointer to it | Ch. 18, hardening |
| 025–027 | Plan time validates the artifact it just wrote; one resolution of a story's own commit range; a re-run onto a branch already holding finished work refused | Ch. 18, hardening |
| 028–031 | Retries routed to the stage that owns the defect; loading code retired out of git history and the rule enforced mechanically; a story branched from a declared base; mutation controls that mutate the working tree, never a pinned revision | Ch. 17, retry routing; Ch. 18 |
| 032–035 | A plan refused when it assigns work a stage cannot own; cloning over the normal transport instead of copying a live object store; a resume guard that works when the harness is its own target; stages granted the read-only tools they need, with mutation denied at the door by a hook | Ch. 15, governance boundaries; Ch. 18 |
| 036–038 | A stage that failed mechanically runs again in place, on its own budget; a stage's baseline is what that stage first found; a test module named for what it checks | Ch. 18, hardening |
| 039–043 | Every configurable value proven configurable; no target-stack literal in harness source; the verification runner no longer assumed to be Python; a plan may assign an existing file to the implementer; an undeclared config key refused | Ch. 15, governance; portability the appendix assumes |
| 044–046 | The documenter records what it changed; the documenter runs before verification, so its output is judged; the test location comes from configuration | Ch. 18, hardening |
| 047–051 | The tester writes fixture-based tests, asking whether a shipped artifact is an assertion's subject or its input; every test that needed a workflow as an *input* now builds one, leaving only the modules the shipped definition is genuinely about; a verifier verdict can say that retrying cannot finish the work, ending the run without spending the budget; retry guidance declares what would satisfy each instruction, so guidance that sanctions the outcome it then fails is caught and rewritten rather than charged to the stage; and a documented claim about a story with no merged work is reported, because the run directories and request files a documenter writes from are untracked and reach no clone | Ch. 18, hardening |
| 052–053 | A new test may not resolve this repository's own git history, so a test's result stops depending on what has been committed since; the modules that still did are declared under a ceiling that only a conversion lowers, and the conversion took that ceiling to zero — the texts those tests needed are committed fixtures now, read from the tree rather than out of the commit graph | Ch. 18, hardening |
| 054 | A documented quantity counts whether it is written in digits or in words, against a bounded vocabulary that excludes the words ordinary prose uses without enumerating anything | Ch. 18, hardening |
| 055 | A finding too small to fail a run has somewhere to go: a passing verdict carrying corrections re-enters the workflow for a correction pass, which spends no retry budget and may change words but never behaviour; and a shared prose layer reaches every stage that writes for a reader, planner included | Ch. 18, hardening |
| 056 | A story's commit range ends where the story ended rather than where it escalated, so an assertion bounded by it stops being silently blind to the work a resumed run did | Ch. 18, hardening |
| 057 | A plan that assigns a stage a path it is restricted from is refused when the session ends, naming the grant that would make it legal — rather than a stage discovering it hours later against a check that could only refuse | Ch. 15, governance boundaries |
| 058 | A check that re-runs the suite says so before it starts, and says what the wait is for, so a console that used to sit silent for the length of a suite run no longer reads as a hang | Ch. 18, hardening |
| 059 | `l5-plan` offers to run the story it just committed, and skips without reading when nothing can answer, so a scripted invocation cannot hang on the prompt | Appendix A, the `l5-` entry points |
| 060 | Every stage declares a self-route budget, so a mechanical failure anywhere runs again in place rather than ending the run — landable only once the compatibility guards were held against a built workflow instead of the deployed one | Ch. 18, hardening |
| 061 | A crashed run's resume archives the interrupted attempt before re-running the stage, and refuses rather than overwrites — a run that stopped for a reason nobody recorded is where a partial artifact is most likely to be the only account of what happened | Ch. 17, retry evidence; Ch. 18 |
| 062 | A resume restores the run's attempt allowance and archives the entry it ends, so a run that escalated at its ceiling does not resume with nothing left to spend | Ch. 17, retry evidence; Ch. 18 |
| 063 | A run has a cost ceiling: one for the run and one per stage execution, declared with the reasoning behind each figure, with every invocation's cost recorded in `cost.json` | Ch. 18, hardening |
| 064 | The implementer runs the tests its change touches rather than the whole suite, which the revert check, the coordinator and the clean-clone check each run after it — so an eleven-minute command stops sitting inside a ten-minute agent turn | Ch. 18, hardening |
| 065 | A story that escalated and resumed leaves a completion its merge cannot drop: with an unchanged tree the run amends its own escalation commit rather than adding an empty one, and the reader looks for its two pieces of evidence anywhere in a message rather than in particular fields | Ch. 18, hardening |
| 066 | The coordinator runs the target's configured suite and the tester only authors: an eleven-minute command no longer sits inside a ten-minute agent turn, the verdict is an exit code rather than a stage's account of one, and every coordinator suite run keeps its full output rather than a tail | Ch. 18, hardening |
| 067 | A correction pass costs a correction rather than a re-run: it enters at the stage the workflow declares instead of the one a finding's category routes to, is no longer asked to prove the suite itself, and is confirmed by a single clean-clone check that now runs after the pass rather than on both sides of it | Ch. 18, hardening |
| 068 | A plan may declare that a change forces an existing test to adapt, so plan-time validation refuses only the governed entries a run would actually refuse rather than also refusing the repairs the revert check permits | Ch. 18, hardening |
| 069 | A run executes the workflow its work item names: a story artifact may select the workflow definition its run loads, so the choice belongs to the work rather than to the target's configuration, and a name with no definition is refused when the plan is written and again before the run starts | Ch. 18, hardening |
| 070 | A refactor runs under a workflow whose guard suits it: `refactor-workflow.json` drops the create restriction and the revert check, which assume every legitimate test edit is forced by a change elsewhere, and guards the implementer with a suite census instead — because a refactor's threat is not authoring the validation that judges it but weakening the validation that already exists | Ch. 18, hardening |
| 071 | A prompt's filename says which workflow owns it: the three prompts `story-workflow` owns take a `story-` prefix beside story-070's `refactor-` pair, while the documenter and planner keep their names because they are shared rather than owned — and the rename ran as the first story under `refactor-workflow`, its suite census taking two seconds where a revert check would have taken twelve minutes | Ch. 18, hardening |
| 072 | The planner proposes the workflow it plans for: without `--workflow` a first phase classifies the request, shows its reasoning and asks the developer to confirm or override, and every workflow definition must now declare an `applies_when` saying when it is the right one — so a run also announces which definition is executing it, and a headless invocation that can name no workflow and ask nobody is refused before anything is invoked | Ch. 18, hardening |
| 073 | A stage that runs no suite has the invocation denied at the door: the deny-only Bash guard reduces a command to the program and the targets it was pointed at, and refuses it when that equals the same reduction of the target's configured test command — enforcement replacing a prompt paragraph that said the same thing and did not hold | Ch. 18, hardening |
| 074 | The suite denial says what it does: the guard's rule is recorded as a filter over the spellings agents reach for rather than a boundary an invocation cannot cross, since exact equality on the reduced words lets a different spelling of the same suite through — with the measurement that makes it worth having, and a corrected account of the run that motivated it | Ch. 18, hardening |
| 075 | A superseded attempt keeps its evidence: the attempt archive now collects each check's result artifact off the shape of the declaration that carries it, so a check declared later archives itself, and follows a copied record's own `output_path` to bring the whole-output file with it and repoint the archived copy — closing the known cost that a retry overwrote every check result in place | Ch. 18, hardening |
| 076 | Phase one can deliver its answer: the workflow proposal asks for its answer beneath the target root under the configured logs directory, runs with its working directory there so the permission mode accepts the write, is granted the one tool it needs, and keeps its transcript — so a classifying turn that delivered nothing is the one whose reasoning a developer can read | Ch. 18, hardening |
| 077 | The revert check asks one test first: a writing stage may nominate the test that fails without its change, and the check decides on that test alone — run where the stage left the tree, where it must pass, and again with the governed edits reverted, where it must fail — because one failing test proves a failing suite while no number of passing tests proves a passing suite; anything short of pass-then-fail falls through to the whole suite | Ch. 18, hardening |
| 078 | An interrupt test does not race its own teardown: the pty teardown reads to end of output, reaps the child, and closes the master last — because closing it first makes the child's exit-time flush fail, and a child that cannot flush exits 120 whatever status it meant to exit with, so the teardown was overwriting the very status the test asserts | Ch. 18, hardening |
| 079 | The suite uses the machine it runs on: the configured test command runs the suite in parallel with no fixed worker count, the test dependencies it needs are declared in a tracked file CI installs from rather than a hand-listed set, and the one assertion that globbed the shared system temp root now observes only the scratch directory the call under test created | Ch. 18, hardening |

Still ahead, in the order Chapters 18 and 19 recommend: per-agent logs and a watcher, a fuller hook-based tool policy in place of the static `allowed_tools` allowlist, an adjudicator, an inspector, pause-and-resume on capacity exhaustion, git worktrees and parallel story execution, a real initialization library, and a `.harness/history/` record across runs.

The harness stays at level 3. Epics and products (Chapters 20–22) are a different unit of coordination, and the book is explicit that the story workflow earns that step through a track record rather than a feature list.

## Prerequisites

- Claude Code CLI (`claude`) with an active subscription
- Python 3 (3.10+)
- Git

The harness itself has no third-party runtime dependency — it uses only the Python standard library. Running its test suite needs what `requirements-dev.txt` declares; see [Tests](#tests).

## Scripts

All harness capabilities are invoked through `l5-` scripts in `scripts/`:

| Script | Purpose |
| --- | --- |
| `l5-init` | Initialize a `.harness/` structure in a target repository |
| `l5-plan` | Plan a story interactively with the planner agent; commits and pushes the story artifact the session produced, then offers to run it |
| `l5-run` | Execute an approved story through the story workflow |
| `l5-status` | Show a snapshot of story runs (status, current stage, retries), or one run's detail |
| `l5-assist` | Launch the interactive assist agent with harness context |

Example:

    scripts/l5-plan "Add a --dry-run flag to l5-run"
    scripts/l5-run story-001
    scripts/l5-status

## Layout

    workflows/       workflow definitions (stages, artifact routes, retry rules):
                     story-workflow.json, and since story-070
                     refactor-workflow.json for behaviour-preserving work
    schemas/         JSON Schemas for the structured artifacts, plus their manifest
    prompts/         reusable agent prompt templates ({{placeholder}} injection)
    orchestration/   the Story Coordinator and its supporting modules
    rules/           execution rules enforced by the coordinator
    scripts/         thin l5- entry points
    hooks/           the deny-only tool guard each stage invocation carries
    templates/       starter files l5-init copies into a new target repository
    tests/           the coordinator's test suite, run without model calls
    .harness/        target-repository state: config, standards, stories, and
                     docs/ARCHITECTURE.md; plus runs, logs and requests, which
                     are gitignored execution state

The harness pieces (`workflows/`, `schemas/`, `prompts/`, `orchestration/`, `rules/`, `scripts/`, `hooks/`, `templates/`) are reusable across target repositories. The `.harness/` directory is target-repository state; run `l5-init` to create it in any other repository you want the harness to work on.

This repository is both the harness repository and its own first target repository. Every demo story is a real harness feature, so the harness participates in building itself from the start.

**Looking for the architecture document?** It is [`.harness/docs/ARCHITECTURE.md`](.harness/docs/ARCHITECTURE.md), not a top-level `docs/`. Its location is a configuration value rather than a fixed part of the layout: `architecture_docs` in `.harness/config.yaml` names it, and the coordinator injects whatever that key names into the implementer's context on every run. It sits beside `.harness/standards/` because both are agent context, and a target repository is free to keep them elsewhere.

## How a story runs

1. `l5-plan` runs an interactive planning session and writes an approved story artifact to `.harness/stories/`, then validates, commits and pushes it and offers to run it: Enter starts `l5-run` for the story just planned, anything else skips and prints the command that would have started it. When stdin is not a terminal the offer is not made at all and the command is printed, so a scripted invocation cannot hang on a prompt nothing can answer. Unless `--workflow` names one, the session runs in two phases: a first classifying turn proposes the workflow the request should run under and shows its reasoning, and the interview begins only once that proposal is confirmed or overridden — because a workflow's stage list is injected before the interview, so the choice cannot be made partway through it. With no terminal and no `--workflow` there is nobody to confirm a proposal, and the invocation is refused before anything is invoked, written or committed.
2. `l5-run` hands the story to the Story Coordinator, which creates a story branch and a run directory under `.harness/runs/<story-id>/`.
3. The coordinator advances the workflow stage by stage — implement → test → document → verify under `story-workflow.json`, or implement → document → verify under `refactor-workflow.json`, whose correctness claim is that behaviour is unchanged and whose implementer is guarded by a suite census rather than by the create and revert checks. A second workflow is not a second unit of work: the story is still the unit, and which definition its run loads is a field on the story artifact. The coordinator assembles each stage's context, injects it into the stage prompt, and invokes the agent headlessly (`claude -p`). A stage that runs no suite cannot invoke one: the deny-only Bash guard each invocation carries reduces a command to its program and the targets it was pointed at, and refuses it when that matches the same reduction of the target's configured test command. The documenter runs before verification so that what it writes is judged rather than taken on trust.
4. The verifier writes `verification-result.json`. The coordinator routes from that artifact: advance, retry, or escalate. A retry goes to the stage that owns the defect — named by the verifier as a category the workflow defines, with no default route — carrying structured guidance in `retry-guidance.json`. A verdict may also report that retrying cannot finish the work at all, which escalates immediately and leaves the retry budget unspent. On a passing verdict the coordinator re-runs the suite in a fresh clone with the story committed, because the working tree is the one place that commit does not yet exist.
5. A stage that fails *mechanically* — rather than being judged wrong — runs again in place, on a separate per-stage budget that retries do not share.
6. Every run leaves its state (`state.json`), the same events in two renderings (`events.log` and `execution-history.json`), a record of any retry (`retry-history.json`), and the artifacts each stage produced.

See `.harness/docs/ARCHITECTURE.md` for the full architecture.

## Tests

The Story Coordinator is deterministic and fully unit-tested without any model calls (a fake runner plays back scripted stage artifacts). Run the suite with:

    .venv/bin/python -m pytest tests/ -q -n auto

That is `test_command` in `.harness/config.yaml` verbatim, and running it verbatim is the point: the harness's own gates — the revert check, the coordinator's suite run in the tree, and the clean-clone check — execute the configured command, so what a developer runs and what the gates run cannot drift apart. `-n auto` reads the core count of whatever machine it lands on, so no core count is written down anywhere.

The revert check reaches that command second. A writing stage may nominate the test that fails without its change, and the check then runs `test_selection_command` — the same configuration's selector, with the nominated test substituted at `{test}` — on the tree the stage left, where it must pass, and again with the governed edits reverted, where it must fail. Pass-then-fail decides the check on that one test; anything else falls through to the configured suite command above, so a bad nomination costs one selector run and changes no verdict.

The dependencies that command needs are declared in `requirements-dev.txt`, and it has to be installed into **both** interpreters this repository configures — the one you run the suite in and the one `verification_runner` names:

    .venv/bin/pip install -r requirements-dev.txt
    .venv310/bin/pip install -r requirements-dev.txt

Install it into only the first and your local suite is green while the clean-clone check dies on an unrecognized argument, because that check runs the same command under the second interpreter. That is the failure this instruction exists to prevent.

## Contributing and feedback

This repository tracks the book through level 3, so its scope is what Part 3 and Appendix A describe. Small fixes — genuine bugs, or errors in the code and its docs — are welcome via pull request. Improvements the book's roadmap calls for are welcome as issues; they are best planned and executed through the harness itself, which is the whole point of it. Changes that would take the harness past level 3, or in a direction the book does not argue for, are out of scope here.

Found a bug in the harness code? Open a GitHub issue. For anything about the **book's content** — typos, unclear passages, errata — please use the feedback form at [agenticprogrammingbook.com/feedback](https://agenticprogrammingbook.com/feedback) rather than GitHub Issues.

## License

MIT — see [LICENSE](LICENSE). Copyright © 2026 Jerod W. Wilkerson.
