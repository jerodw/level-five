# story-048 Escalation Summary

## Status
Escalated

## Reason
verification failed and retries are exhausted

## Where Execution Stopped
Stage: verifier, retry count: 2

## Outstanding Issues
From verification-result.json, the same verdict recorded as verification/iteration-3.json. It may predate the stage this run escalated at.

- [high] The conversion the story is named for is still substantially undone. Verified by running the suite's own classification: tests/test_baseline_honesty.workflow_reading_modules() reports 22 modules resolving the shipped workflow; 8 are classified WORKFLOW_SUBJECT_READERS and 14 remain in WORKFLOW_INPUT_READERS_AWAITING_CONVERSION (tests/test_baseline_honesty.py:2366-2381). Confirmed against the tree: tests/test_execution_history.py:42, tests/test_resume_guard.py:66, tests/test_required_output_freshness.py:58 and tests/test_attempt_archiving.py:32,265 still bind WORKFLOW = conftest.shipped_workflow(...). This retry converted two more modules (tests/test_coordinator_contract.py, tests/test_shared_baseline_resolution.py) and reclassified tests/test_planner_injection.py as a subject reader, bringing the total converted to five; the story's task 'Convert each input reader to a workflow it builds for itself' and the twenty-four tester entries in technical_plan.likely_file_changes name the rest. Three verifications have now named this same gap, and the rate of the last retry (two modules) does not close it.
  Location: tests/test_baseline_honesty.py:2366-2381 and the fourteen modules it names
  Required behavior: WORKFLOW_INPUT_READERS_AWAITING_CONVERSION is empty: each module on it drives its run from a definition built through conftest.build_workflow and materialized through conftest.materialize_workflow, every assertion keeping its subject and its strength, and each displaced configuration assertion moved into tests/test_shipped_workflow_is_valid.py rather than dropped.

- [high] Acceptance criterion 7 -- 'Every module on the declared list carries a stated reason saying why the shipped artifact is the subject of its assertions rather than an input to them' -- is not met, and cannot be while the awaiting set is non-empty. Verified by reading each reason on PERMITTED_LIVE_ARTIFACT_READERS: six entries state that the workflow is the only live artifact the module resolves AND that the read is an input awaiting conversion -- test_attempt_archiving.py, test_escalation_resume.py, test_execution_history.py, test_required_output_freshness.py, test_resume_guard.py, test_single_story_reader.py. Each therefore sits on the declared list of permitted live-artifact readers carrying a reason that explicitly disclaims subjecthood and names no other subject read. Eight further entries disclose an awaiting workflow read but do name a non-workflow subject (a shipped schema or the shipped rules), which is the honest form; the six above have nothing keeping them on the list. The disclosure itself is a real improvement -- the previous finding's contradiction is genuinely fixed, and contradictory_reasons/AWAITING_CONVERSION_MARKER (tests/test_baseline_honesty.py:2389-2437) now hold the two structures to agreement with a working control that plants a subject-claiming reason and observes it reported -- but honest bookkeeping of an unmet criterion is not the criterion.
  Location: tests/test_baseline_honesty.py:2176-2293
  Required behavior: Every name remaining on PERMITTED_LIVE_ARTIFACT_READERS carries a reason naming a shipped artifact that is the subject of that module's assertions. A module whose only shipped read is a workflow used as an input is converted and leaves the list rather than remaining on it with a disclosure.

## Retry History

### Attempt 1, rerouted to tester

- [high] The conversion the story exists for was not performed. The story's task list requires each input reader to be converted to a workflow it builds for itself, and the technical plan names roughly twenty-five modules under tests/ for the tester, starting explicitly with tests/test_self_routing_retry.py and tests/test_retry_routing.py -- 'the two the evidence names'. Exactly one module was converted: tests/test_story_coordinator.py (the only test module besides conftest.py, test_baseline_honesty.py and the new configuration module that `git diff --stat` shows touched, and one of only two non-infrastructure modules that references build_workflow). tests/test_self_routing_retry.py:108 and tests/test_retry_routing.py:73 still read `conftest.shipped_workflow(REPO_ROOT, "story-workflow")` at module scope and are unmodified in this run's diff, so the coupling the story's measured evidence names -- a max_self_routes grant reddening four assertions in a module with nothing to say about it -- is still present in exactly the module where it was measured.
  Location: tests/test_self_routing_retry.py:108, tests/test_retry_routing.py:73, and the ~23 further modules named in technical_plan.likely_file_changes with stage: tester
  Required behavior: Each module the plan classifies as an input reader is converted to a workflow it builds through conftest.build_workflow / materialize_workflow, keeping every assertion's subject and strength, or -- where a module genuinely is a subject reader -- the classification is stated against the specific read that keeps it there and reconciled with the plan's contrary reason for that module.

- [high] The stated reasons on PERMITTED_LIVE_ARTIFACT_READERS do not hold against the reads they justify, so the classification is not verifiable by reading the reason against the module -- which verification_requirements names as the check. For test_self_routing_retry.py the reason is 'reads the shipped self-route schema, the shipped rules and the shipped prompts a self-routed stage is rendered from' and for test_retry_routing.py 'reads the shipped rules, the shipped verifier prompt ... and the shipped schema a verdict must satisfy'. Neither mentions the module-scope shipped-workflow read each actually carries, and in both modules that read is used as a mutable base (`json.loads(json.dumps(WORKFLOW))` at test_retry_routing.py:275,782,786 and test_self_routing_retry.py:326) -- the textbook input use the story defines. The same applies to test_stage_output_ownership.py, whose reason ('which stage of this deployment is restricted from creating what, and that only one is') describes a configuration assertion the plan says belongs in the configuration module, while the module keeps six shipped-workflow reads (lines 44, 140, 235, 357, 382, 472) driving ownership enforcement. As written, the reasons would justify keeping every module on the list, which makes the classification unfalsifiable.
  Location: tests/test_baseline_honesty.py:2170-2260
  Required behavior: Each reason names the specific read that keeps its module on the list and is true of that module's actual reads; a module whose shipped-workflow read is used as an input is converted rather than justified, and a configuration assertion that survives in such a module moves to tests/test_shipped_workflow_is_valid.py.

- [medium] The 'after' wall-clock runtime was not measured, so the story's final acceptance criterion is unmet. The 'before' figure is on the record (2449 tests, 474.49 s / 7:54.84 wall at dcda75d, implementation-summary.md). test-results.json records 2479 passing tests and no duration, and no other run artifact in .harness/runs/story-048/ carries one. The documentation report states the gap rather than concealing it, which is the honest handling, but the criterion asks for two measurements and only one exists.
  Location: .harness/runs/story-048/test-results.json
  Required behavior: The tester runs the full suite and records the measured wall-clock time after the conversion alongside the before figure, so the comparison is a measurement rather than an estimate.

Archived at attempts/attempt-1

### Attempt 2, rerouted to tester

- [high] The conversion is still incomplete: 3 modules converted, 17 declared as unconverted input readers. The retry converted the two modules the previous finding named -- tests/test_self_routing_retry.py (WORKFLOW = conftest.build_workflow(...) at line 140, four stages assembled from arguments, every name derived from it) and tests/test_retry_routing.py (build_workflow at line 98) -- and both now drive real runs against conftest.materialize_workflow roots. That part is done and verified. But the story's task 'Convert each input reader to a workflow it builds for itself' and technical_plan.likely_file_changes name roughly twenty-four tester modules, and the remaining seventeen were not converted; they were instead recorded in a new set WORKFLOW_INPUT_READERS_AWAITING_CONVERSION (tests/test_baseline_honesty.py:2335-2353), which the module's own comment calls 'the remaining work'. Declaring the debt is honest and the both-directions equality assertion (test_the_modules_reading_the_shipped_workflow_are_exactly_the_classified_ones) makes it survive, but it is not the conversion, and it leaves acceptance criterion 7 unsatisfiable: PERMITTED_LIVE_ARTIFACT_READERS still carries seventeen modules whose shipped-workflow read the suite itself classifies as an input.
  Location: tests/test_baseline_honesty.py:2335-2353 and the seventeen modules it names
  Required behavior: Each module in WORKFLOW_INPUT_READERS_AWAITING_CONVERSION is converted to a workflow it builds through conftest.build_workflow / materialize_workflow, keeping every assertion's subject and strength, with each displaced configuration assertion moved into tests/test_shipped_workflow_is_valid.py -- so the set empties and PERMITTED_LIVE_ARTIFACT_READERS names only modules whose reads are subjects.

- [high] Eight stated reasons on PERMITTED_LIVE_ARTIFACT_READERS assert that the shipped workflow IS the subject of a module's assertions, while WORKFLOW_INPUT_READERS_AWAITING_CONVERSION in the same file declares that same module's workflow read an input. The two structures contradict each other and nothing asserts they agree. Verified by printing both structures: test_attempt_archiving.py ('the stage list it archives per attempt is the deployed one'), test_documenter_before_verification.py ('its subject is the shipped stage order'), test_escalation_resume.py ('what --stage accepts is a claim about this deployment's stage list'), test_execution_history.py ('the event record ... for the stages it deploys'), test_required_output_freshness.py ('the outputs this deployment declares its stages must produce'), test_resume_guard.py ('the stage list this deployment defines'), test_shared_baseline_resolution.py ('the workflow it drives a run under is this repository's'), test_stage_baseline.py ('the baseline captured before each deployed stage'). Exactly one of the seventeen -- test_stage_output_ownership.py -- had its reason rewritten to disclose the awaiting-conversion status, which shows the correction was understood and then applied to one module rather than to the family. This is the unfalsifiability the previous finding named, now split across two structures that disagree: reading the reason against the module (the check verification_requirements names) yields the opposite answer to reading the classification.
  Location: tests/test_baseline_honesty.py:2176-2274
  Required behavior: Either the module is converted, or its reason stops claiming the workflow as a subject and names only the non-workflow read that keeps it on the list, in the form test_stage_output_ownership.py already uses. Additionally, an assertion should hold the two structures to agreement, so a reason claiming workflow-subjecthood for a module in WORKFLOW_INPUT_READERS_AWAITING_CONVERSION fails rather than sitting unnoticed.

- [low] The story's 'after' wall-clock runtime is still absent from every run artifact after a retry that named the gap explicitly. test-results.json records 2488 passed / 0 failed and no duration; no other artifact in .harness/runs/story-048/ carries one. The documentation report states the gap rather than concealing it. Severity is low rather than medium only because this verification supplies the measurement: the verifier ran `.venv/bin/python -m pytest tests/ -q` under `time` on the current tree and observed 2488 passed in 474.10s, 7:54.33 total wall clock -- against the before figure of 2449 tests, 474.49s / 7:54.84 wall at dcda75d. The conversion recovered no measurable runtime, which is now on the record here.
  Location: .harness/runs/story-048/test-results.json
  Required behavior: The after figure is recorded in a run artifact a later reader finds, beside the before figure, so the story's final criterion is met by the run rather than by the verifier.

Archived at attempts/attempt-2

## Where to Look
See events.log for the run history and the verification/ directory for verifier findings.

## Recommended Investigation

Artifacts this run left in /Users/jerodw/Work/AgenticProgramming/level-five/.harness/runs/story-048:

- changed-files.json
- documentation-report.md
- documenter-changed-files.json
- events.log
- execution-history.json
- implementation-summary.md
- prompt-documenter-attempt-1.md
- prompt-documenter-attempt-2.md
- prompt-documenter-attempt-3.md
- prompt-implementer-attempt-1.md
- prompt-tester-attempt-1.md
- prompt-tester-attempt-2.md
- prompt-tester-attempt-3.md
- prompt-verifier-attempt-1.md
- prompt-verifier-attempt-2.md
- prompt-verifier-attempt-3.md
- retry-guidance.json
- retry-history.json
- state.json
- suite-runtime.md
- test-results.json
- tester-changed-files.json
- verification-result.json

The escalated work is committed on branch story/story-048 at 390b6be96100148cfcd49703212b890ebbefc938, so it survives a checkout of another branch. To put those changes back in the working tree:

    git reset --mixed HEAD~2

Once you have made a change, `l5-run story-048` resumes this run at the stage it stopped at (verifier); `--stage <stage>` overrides that and enters somewhere else. The resume is refused while the story artifact, the branch and the harness are all unchanged, because it would reach the same point the same way.
