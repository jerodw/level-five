# Areas

The parts of this system, as a vocabulary for saying which one a piece of work
concerns. Not a rule about the code — a list of names, so that a backlog can be
sorted by feature rather than only by kind and severity.

**Name exactly one, or name none.** Where a brief concerns two areas, name the
one the work would land in. Where none of them fits, name none and say so: a
missing area is reported and gets a name added here, and a wrong one is silent
and misfiles the work for everyone who filters later. A wrong area is worse
than a missing one.

**Never coin a name.** This list grows by a person editing this file, so that
one part of the system does not end up under three spellings. An area that
does not exist yet is a gap to report, not a name to invent.

Many areas share a file — `orchestration/story_coordinator.py` is most of
several of them — so the area is the concern the work is about rather than the
file it happens to touch.

- **run-execution** — driving a story's stages: invoking one, what it must
  produce, the record of what it changed, which paths it may write, the tool
  grants it runs under.
- **retry-and-escalation** — what happens when a stage fails: retries,
  self-routes, correction passes, escalation, and resuming a stopped run.
- **branch-and-base** — which branch a run stands on and which it records, the
  base it was cut from, the remote it resolves, and what a checkout or a
  history it cannot read does.
- **verification-checks** — what the coordinator runs between stages: the
  clean-clone check, the revert check, suite runs and their scope, the census,
  claim support.
- **planning** — `l5-plan` and everything it does: the interview, the mandate,
  story ids and their reservation, committing and validating the artifact,
  fetching a brief, offering the run.
- **agent-invocation** — how a stage's agent is actually run: the model and
  effort it runs under, its permission mode, how many agents a stage is, and
  what comes back from a turn.
- **artifact-schemas** — the schemas artifacts are held to, the validator that
  holds them, the inventory that keeps the set honest, and the parsing that
  reads an artifact against its schema.
- **cost-and-budgets** — what a run and a stage may spend, what is recorded
  about what they did spend, and what stops on a ceiling.
- **inspection-and-briefs** — the Inspector, what it files and what it
  declines to, the brief contract, and filing a brief by hand.
- **outbox** — the durable queue: entries, identity and dedupe, receipts, and
  the sweeps that drain it.
- **tracker-commands** — the configured sync, query and item commands, the
  scripts that implement them, and what reaches the board.
- **worktrees** — cutting, finding and cleaning a working tree, its build
  state, and how the repository is located from inside one.
- **configuration** — what a target may declare, how it is read, what an
  undeclared or unusable value does, and what `l5-init` installs.
- **command-transport** — running a command the target configured: its bound,
  its kill, its exit codes, and what its output is read as.
- **observability** — what a run says about itself while it runs and
  afterwards: `l5-status`, run status, events, and the history logs.
- **prompt-assembly** — the prompts stages are given, the partials they share,
  and how context is resolved into them.
- **workflows** — workflow definitions, the stage rules they declare, and
  choosing one for a piece of work.
- **suite-rules** — the standing rules the test suite holds itself to: what a
  test may bound, what it may pin, naming, and the controls over its controls.
