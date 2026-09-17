# level-five (l5)

[![tests](https://github.com/jerodw/level-five/actions/workflows/tests.yml/badge.svg)](https://github.com/jerodw/level-five/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A **level 3 agentic harness** built by following *Agentic Programming* by Jerod W. Wilkerson. The harness is a story execution system: stories enter with an approved plan, move through implementation, testing, documentation, and verification, retry when verification fails, and end completed or escalated — or pause, when capacity runs out, and continue where they stopped.

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

## What the harness does now

Everything below arrived as a story the harness planned, executed, verified and documented itself. Where the book argues for a capability, the chapter is named.

**Planning.** An interactive session interviews you and writes an approved story artifact, which it validates, commits, pushes, and offers to run. A story can be planned from typed text or from a brief the harness filed earlier (`--brief`). The session proposes which workflow the work belongs under and will not begin the interview until that is settled, because a workflow's stage list shapes the interview. Planning happens on a worktree of its own, so a plan written during a run does not land in that run's branch. *(Ch. 15, Ch. 18)*

**Execution.** The Story Coordinator advances a story stage by stage, assembling each stage's context, injecting it into a prompt and invoking the agent headlessly. Two workflows ship: the story workflow, and a refactor workflow whose correctness claim is that behaviour is unchanged and whose implementer is guarded by a suite census instead of the create and revert checks. A run works in a worktree of its own, carrying the build state the configured commands need. *(Ch. 12–17, Ch. 19)*

**Governance.** Every writing stage declares what it may write and carries a check that records what it wrote — the implementer what it may not create, the tester the directory it is confined to, the documenter the architecture documents the target configures. An edit outside a confinement is not forbidden but asked about: it stands where the suite fails without it and is undone where the suite passes. A deny-only tool guard travels with each invocation, and a stage that executes no suite cannot invoke one. *(Ch. 15, Ch. 18)*

**Verification and routing.** The verifier writes a verdict and the coordinator routes from the artifact: advance, retry, or escalate. A retry goes to the stage that owns the defect, named by the verifier from a category table the workflow declares, carrying structured guidance. A verdict may also report that no retry can finish the work, which escalates without spending the budget. On a passing verdict the suite runs again in a fresh clone with the story committed. A stage that fails *mechanically* runs again in place on a budget retries do not share. A red suite is carried forward for the verifier to judge rather than handed back to whichever stage ran last. *(Ch. 17–18)*

**Pausing and resuming.** An invocation stopped because capacity ran out is not a failed one, so the run pauses rather than escalating: nothing is reset, nothing archived, the work is committed, and the run either waits in place or exits for a later `l5-run` to continue where it stopped. *(Ch. 19)*

**Inspection and briefs.** A completed story is inspected — the files it changed, plus what git tracks directly beside them. A finding about the story's own change is handed to that story's verifier as evidence; a finding about the code around it is filed as a brief. Every routed finding is fixed, filed, or named in the run's own accounting, so none can go quiet. Briefs carry a category, a severity, a confidence and the area of the system they concern, and land in a triage column that classification already decides. *(Ch. 18)*

**The outbox.** Work to file externally goes to a durable local queue first and reaches a tracker on a sweep, so a provider that is slow or unreachable can never turn a finished story into a failed one. Entries are deduplicated by identity, receipts are kept apart from the queue, and a partly-filed entry says so. *(Ch. 17)*

**Observability.** `l5-status` reports runs and their stages. Every run leaves its state, the same events in two renderings, a record of every retry, and the artifacts each stage produced. Long steps announce themselves before they start and say what the wait is worth. What a run and an inspection cost is recorded where a reader meets it. *(Ch. 18)*

## What's next

The roadmap, in the order it is being worked:

- **One defect is filed once per run**, however many attempts the run takes. An inspection reads the change on each attempt and re-derives the name it files under, so one defect can reach the backlog three times.
- **Long operations say how long they are expected to take**, from what they have taken before, rather than saying only that the wait is long.
- **A planning session runs under a declared tool policy**, as a stage does.
- **A completed story can be sent back for work** without editing its branch by hand.
- **The architecture document is inspected as the code is** — it is changed by nearly every story and read by no inspection.

Further out, in the order Chapters 18 and 19 recommend: per-agent logs and a watcher, a fuller hook-based tool policy in place of the static `allowed_tools` allowlist, an adjudicator, parallel story execution, and a real initialization library.

## How it was built

Every capability above arrived as a numbered story, and each story's commit is titled with the behaviour it produced. So the list of what has changed since the appendix is one command rather than a document somebody maintains:

    git log appendix-a..main --oneline --grep="^story-"

The approved plan behind each is committed in `.harness/stories/`, and the reasoning — why a thing is the way it is, and what it deliberately does not do — is in [`.harness/docs/ARCHITECTURE.md`](.harness/docs/ARCHITECTURE.md), which the documenter maintains as a stage of every run.

Run directories are execution state rather than source, so `.harness/runs/` is gitignored and does not travel with a clone. The runs worth keeping — the escalations, and the runs preserved for what they showed — are copied into `.harness/runs-archive/`.

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
| `l5-plan` | Plan a story interactively, from request text or from a filed brief named with `--brief`; commits and pushes the artifact, then offers to run it |
| `l5-run` | Execute an approved story through its workflow |
| `l5-status` | Show a snapshot of story runs, or one run's detail |
| `l5-assist` | Launch the interactive assist agent with harness context and the skills the harness ships |
| `l5-check` | Report whether a stage's required outputs are present, fresh and valid — the coordinator's own check, reachable while the turn is still open. It decides nothing |
| `l5-inspect` | Inspect a scope of the code deliberately and file what it finds as briefs |
| `l5-sync` | Drain the outbox, reporting what landed, what is pending, and what no sweep will clear on its own |

Example:

    scripts/l5-plan "Add a --dry-run flag to l5-run"
    scripts/l5-plan --brief <the key the tracker reported for a filed brief>
    scripts/l5-run story-001
    scripts/l5-status

## Layout

    workflows/       workflow definitions: stages, artifact routes, retry rules
    schemas/         JSON Schemas for the structured artifacts, plus their manifest
    prompts/         reusable agent prompt templates ({{placeholder}} injection)
    plugin/          the skills the harness loads into the sessions it starts
    orchestration/   the Story Coordinator and its supporting modules
    rules/           execution rules enforced by the coordinator
    scripts/         thin l5- entry points
    hooks/           the deny-only tool guard each invocation carries, and the
                     turn-end check that asks a stage for the outputs it owes
    templates/       starter files l5-init copies into a new target repository
    tests/           the coordinator's test suite, run without model calls
    .harness/        target-repository state: config, standards, stories and
                     docs/ARCHITECTURE.md; plus runs, logs and the outbox queue,
                     which are gitignored execution state

The harness pieces are reusable across target repositories. The `.harness/` directory is target-repository state; run `l5-init` to create it in any other repository you want the harness to work on.

This repository is both the harness repository and its own first target repository. Every demo story is a real harness feature, so the harness participates in building itself from the start.

**Looking for the architecture document?** It is [`.harness/docs/ARCHITECTURE.md`](.harness/docs/ARCHITECTURE.md), not a top-level `docs/`. Its location is a configuration value rather than a fixed part of the layout: `architecture_docs` in `.harness/config.yaml` names it, and the coordinator injects whatever that key names into the implementer's context on every run. It sits beside `.harness/standards/` because both are agent context, and a target repository is free to keep them elsewhere.

## How a story runs

1. **Plan.** `l5-plan` interviews you, writes the story artifact, validates it, commits and pushes it, and offers to run it.
2. **Enter.** `l5-run` hands the story to the Story Coordinator, which stands the run on a story branch in a worktree of its own and creates a run directory under `.harness/runs/<story-id>/`.
3. **Advance.** The coordinator walks the workflow's stages, assembling context, rendering the stage prompt and invoking the agent. After each turn it checks what the stage produced: the required artifacts are present, they were written by this attempt, they satisfy their schemas, and what the stage changed is what it was allowed to change.
4. **Judge.** The documenter runs before verification, so what it writes is judged rather than taken on trust. The verifier writes a verdict; the coordinator routes from it.
5. **Repair or retry.** A failing verdict routes to the stage that owns the defect. A passing verdict carrying small findings re-enters a stage to act on them without spending a retry. A stage that failed mechanically runs again in place.
6. **Confirm.** On a passing verdict the suite runs again in a fresh clone with the story committed, because the working tree is the one place that commit does not yet exist.
7. **Inspect.** A completed run is inspected, and what it finds reaches either this story's verifier or the backlog.

Every one of these steps has a reason, a boundary and a set of things it deliberately does not do. Those are in [`.harness/docs/ARCHITECTURE.md`](.harness/docs/ARCHITECTURE.md), which the documenter maintains as a stage of every run.

## Tests

The Story Coordinator is deterministic and fully unit-tested without any model calls (a fake runner plays back scripted stage artifacts). Run the suite with:

    .venv/bin/python -m pytest tests/ -q -n auto

That is `test_command` in `.harness/config.yaml` verbatim, and running it verbatim is the point: the harness's own gates — the revert check, the coordinator's suite run in the tree, and the clean-clone check — execute the configured command, so what a developer runs and what the gates run cannot drift apart. `-n auto` reads the core count of whatever machine it lands on, so no core count is written down anywhere.

The dependencies that command needs are declared in `requirements-dev.txt`, and it has to be installed into **both** interpreters this repository configures — the one you run the suite in and the one `verification_runner` names:

    .venv/bin/pip install -r requirements-dev.txt
    .venv310/bin/pip install -r requirements-dev.txt

Install it into only the first and your local suite is green while the clean-clone check dies on an unrecognized argument, because that check runs the same command under the second interpreter. That is the failure this instruction exists to prevent.

## Contributing and feedback

This repository tracks the book through level 3, so its scope is what Part 3 and Appendix A describe. Small fixes — genuine bugs, or errors in the code and its docs — are welcome via pull request. Improvements the book's roadmap calls for are welcome as issues; they are best planned and executed through the harness itself, which is the whole point of it. Changes that would take the harness past level 3, or in a direction the book does not argue for, are out of scope here.

Found a bug in the harness code? Open a GitHub issue. For anything about the **book's content** — typos, unclear passages, errata — please use the feedback form at [agenticprogrammingbook.com/feedback](https://agenticprogrammingbook.com/feedback) rather than GitHub Issues.
