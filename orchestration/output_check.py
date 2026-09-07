"""Answer, while a stage's turn is still open, the question asked after it ends.

The coordinator's required-output, freshness and schema checks are the
authority and this changes nothing about them. What did not exist before this
module is any way for a stage to reach the same answer without ending its turn
first: a stage that forgot an output learned so from a re-entry, which costs a
whole agent invocation to write one JSON file.

So this reads the pre-stage baseline the coordinator persists and reports
whether a stage's required outputs are present, written by this invocation,
and schema-valid. **It computes none of those three itself.** The required-
output list is `story_coordinator.required_artifacts`, the freshness
comparison is `story_coordinator.stale_artifacts` and the schema check is
`story_coordinator._schema_violation`, so a change to any of them moves the
answer here with no edit to this file.

The private name is called across the module boundary deliberately: three test
modules name `_schema_violation`, so renaming it is left to a story that can
move both halves at once.

Two things run this: `scripts/l5-check`, which a stage may run on itself, and
`hooks/stop_check.py`, which runs at the end of a turn. Neither is consulted
by the coordinator, which re-enters a stage that reports itself complete and
is not exactly as it did before this existed.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]

import harness_config  # noqa: E402
import story_coordinator  # noqa: E402

#: The thin entry point over this module. Named here rather than at each call
#: site so the command a stage is invited to run, the grant that permits it and
#: the hook declaration that registers its sibling all resolve one path.
SCRIPT_NAME = "l5-check"


def entry_point(harness_root: Path | None = None) -> Path:
    """The absolute path of the command, invoked by its own shebang.

    No interpreter is named: not here, not in the rendered command line, not
    in the appended grant and not in the hook declaration.
    """
    return (harness_root or HARNESS_ROOT) / "scripts" / SCRIPT_NAME


def grant(harness_root: Path | None = None) -> str:
    """The allowlist entry that permits a stage to run the command on itself.

    Appended to the grants the coordinator passes for a stage invocation, so
    no target repository has to edit its configuration to make it runnable.
    """
    return f"Bash({entry_point(harness_root)}:*)"


def command_line(harness_root: Path | None, run_dir: Path, stage: str) -> str:
    """The command a stage is invited to run, fully resolved.

    The entry point's absolute path, the run directory and the stage name,
    rather than a shape the stage has to assemble.
    """
    return f"{entry_point(harness_root)} {run_dir} {stage}"


@dataclass(frozen=True)
class Verdict:
    """What is wrong with a stage's outputs, or that nothing is.

    `missing`, `stale` and `invalid` are the coordinator's own three verdicts
    in its own order. `problem` is this module's fourth answer and is a
    different kind of thing: it means the question could not be asked at all —
    no baseline, no state, no such stage — and it is deliberately not silence,
    because a check that could not run must not read as a check that passed.
    """

    stage: str = ""
    missing: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    invalid: str | None = None
    problem: str | None = None

    @property
    def complete(self) -> bool:
        return not (self.missing or self.stale or self.invalid or self.problem)


def _stage(workflow: dict, name: str) -> dict | None:
    for stage in workflow.get("stages", []):
        if stage.get("name") == name:
            return stage
    return None


def check(
    run_dir: Path, stage_name: str | None = None, *, harness_root: Path | None = None
) -> Verdict:
    """Whether the named stage's required outputs are present, fresh and valid.

    The stage defaults to the one the run's state.json names as current, and
    the workflow is the one that state.json records — the definition the run
    is executing, rather than whatever the target configuration names today.

    Only *required* outputs are reported missing. A conditional artifact the
    stage did not write is not reported at all, and one it did write is
    schema-checked, which is exactly what the coordinator does with the same
    two sets.
    """
    state = story_coordinator.load_state(run_dir)
    if state is None:
        return Verdict(problem=f"{run_dir} holds no readable state.json")
    name = stage_name or state.current_stage
    if not name:
        return Verdict(
            problem="no stage was named and state.json records no current stage"
        )
    if not state.workflow:
        return Verdict(
            stage=name, problem="state.json records no workflow for this run"
        )

    # The shared walk-up, read through the half of it that answers rather than
    # exits: one of this module's two callers is a hook whose every failure
    # path must yield no decision.
    root = harness_config.target_root(run_dir.resolve())
    if root is None:
        return Verdict(
            stage=name,
            problem=f"no .harness/config.yaml at or above {run_dir}",
        )
    try:
        config = harness_config.load_config(root)
        workflow = harness_config.load_workflow(
            harness_root or HARNESS_ROOT, state.workflow, config
        )
    except Exception as error:  # an unloadable workflow is a problem, not a verdict
        return Verdict(
            stage=name,
            problem=f"workflow '{state.workflow}' could not be loaded: {error}",
        )

    stage = _stage(workflow, name)
    if stage is None:
        return Verdict(
            stage=name,
            problem=f"workflow '{state.workflow}' defines no stage '{name}'",
        )

    baseline = story_coordinator.read_output_baseline(run_dir)
    if baseline is None:
        return Verdict(
            stage=name,
            problem=f"no readable {story_coordinator.OUTPUT_BASELINE} in {run_dir}",
        )
    if baseline.stage != name:
        return Verdict(
            stage=name,
            problem=(
                f"the baseline in {run_dir} was taken for stage "
                f"'{baseline.stage}', so nothing about '{name}' can be decided "
                f"from it"
            ),
        )

    required = story_coordinator.required_artifacts(stage)
    return Verdict(
        stage=name,
        missing=[
            artifact for artifact in required if not (run_dir / artifact).is_file()
        ],
        stale=story_coordinator.stale_artifacts(
            run_dir, required, baseline.signatures
        ),
        invalid=story_coordinator._schema_violation(run_dir, stage),
    )


def report(verdict: Verdict) -> list[str]:
    """The lines the command prints, in the coordinator's own order.

    Absent and present-but-unwritten are distinguished here in the two senses
    the coordinator distinguishes them by: absent is missing, and present and
    unchanged since the baseline is a previous attempt's.
    """
    if verdict.problem:
        return [f"cannot check {verdict.stage or 'this stage'}: {verdict.problem}"]
    if verdict.complete:
        return [
            f"{verdict.stage}: every required output is present, was written "
            f"by this invocation, and satisfies its declared schema"
        ]
    lines = []
    if verdict.missing:
        lines.append(
            f"{verdict.stage} has not written these required outputs at all: "
            + ", ".join(verdict.missing)
        )
    if verdict.stale:
        lines.append(
            f"{verdict.stage} left these required outputs unwritten; what is "
            f"there is a previous attempt's, not this one's: "
            + ", ".join(verdict.stale)
        )
    if verdict.invalid:
        lines.append(f"{verdict.stage} wrote an invalid artifact: {verdict.invalid}")
    return lines


def main(argv: list[str]) -> int:
    """Print the verdict; zero when there is nothing wrong and one when there is."""
    if not argv or len(argv) > 2:
        print(
            f"Usage: {SCRIPT_NAME} <run-dir> [stage]", file=sys.stderr
        )
        return 2
    verdict = check(Path(argv[0]), argv[1] if len(argv) == 2 else None)
    stream = sys.stdout if verdict.complete else sys.stderr
    for line in report(verdict):
        print(line, file=stream)
    return 0 if verdict.complete else 1
