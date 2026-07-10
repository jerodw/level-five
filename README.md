# level-five (l5)

A level 3 agentic harness built by following *Agentic Programming* (Chapters 12–17). The harness is a story execution system: stories enter with an approved plan, move through implementation, testing, and verification, retry when verification fails, and end completed or escalated.

This repository is both the harness repository and its own first target repository. Every demo story is a real harness feature, so the harness participates in building itself from the start.

## Prerequisites

- Claude Code CLI (`claude`) with an active subscription
- Python 3 (3.10+)
- Git

## Scripts

All harness capabilities are invoked through `l5-` scripts in `scripts/`:

| Script | Purpose |
| --- | --- |
| `l5-init` | Initialize `.harness/` structure in a target repository |
| `l5-plan` | Plan a story interactively with the planner agent; produces a story artifact |
| `l5-run` | Execute an approved story through the story workflow |
| `l5-assist` | Launch the interactive assist agent with harness context |

Example:

    scripts/l5-plan "Add a --dry-run flag to l5-run"
    scripts/l5-run story-001

## Layout

    workflows/       workflow definitions (stages, artifact routes, retry rules)
    prompts/         reusable agent prompt templates ({{placeholder}} injection)
    orchestration/   the Story Coordinator and its supporting modules
    rules/           execution rules enforced by the coordinator
    scripts/         thin l5- entry points
    .harness/        target-repository state: config, standards, docs, stories, runs, logs

The harness pieces (`workflows/`, `prompts/`, `orchestration/`, `rules/`, `scripts/`) are reusable across target repositories. The `.harness/` directory is target-repository state; run `l5-init` to create it in any other repository you want the harness to work on.

## How a story runs

1. `l5-plan` runs an interactive planning session and writes an approved story artifact to `.harness/stories/`.
2. `l5-run` hands the story to the Story Coordinator, which creates a story branch and a run directory under `.harness/runs/<story-id>/`.
3. The coordinator advances the workflow stage by stage (implement → test → verify → document), assembling each stage's context, injecting it into the stage prompt, and invoking the agent headlessly (`claude -p`).
4. The verifier writes `verification-result.json`. The coordinator routes from that artifact: advance, retry the implementer with structured retry guidance, or escalate.
5. Every run leaves durable state (`state.json`), an append-only event history (`events.log`), and the artifacts each stage produced.

See `.harness/docs/ARCHITECTURE.md` for the full architecture.
