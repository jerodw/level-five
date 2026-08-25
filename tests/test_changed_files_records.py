"""Independent validation for story-002: per-stage changed-files records.

Validates the acceptance criteria from the artifacts outward — the workflow
definition, the prompt templates, and the coordinator's declaration-driven
blocked-path enforcement — using its own fake runner rather than the one in
test_story_coordinator.py.
"""
import json
import shutil
from pathlib import Path

import story_coordinator
from agent_runner import AgentResult


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class StageRunner:
    """Minimal fake runner: every stage succeeds and writes its artifacts."""

    def __init__(self, target_root: Path, story_id: str = "story-001",
                 tester_record: dict | None = None,
                 write_tester_record: bool = True,
                 documenter_record: dict | None = None,
                 write_documenter_record: bool = True):
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.tester_record = tester_record or {
            "modified": [], "created": ["tests/test_app.py"], "deleted": []
        }
        self.write_tester_record = write_tester_record
        self.documenter_record = documenter_record if documenter_record is not None \
            else {"modified": [], "created": [], "deleted": []}
        self.write_documenter_record = write_documenter_record
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode,
                 model, allowed_tools=None, max_budget_usd=None,
                 suite_command=None):
        self.calls.append(stage)
        if stage == "implementer":
            _write(self.run_dir / "changed-files.json",
                   {"modified": ["src/app.py"], "created": [], "deleted": []})
            (self.run_dir / "implementation-summary.md").write_text("done\n")
        elif stage == "tester":
            _write(self.run_dir / "test-results.json", {
                "status": "passed", "tests_written": 1, "tests_run": 1,
                "tests_passed": 1, "tests_failed": 0, "failures": [],
            })
            if self.write_tester_record:
                _write(self.run_dir / "tester-changed-files.json", self.tester_record)
        elif stage == "verifier":
            _write(self.run_dir / "verification-result.json", {
                "status": "passed", "blocking_issues": [],
                "unverified": [], "retry_recommended": False,
            })
        elif stage == "documenter":
            (self.run_dir / "documentation-report.md").write_text("n/a\n")
            if self.write_documenter_record:
                _write(self.run_dir / "documenter-changed-files.json",
                       self.documenter_record)
        return AgentResult(ok=True, result_text=f"{stage} ok")


def test_workflow_declares_per_stage_changed_files_records(harness_root):
    workflow = json.loads(
        (harness_root / "workflows" / "story-workflow.json").read_text()
    )
    stages = {s["name"]: s for s in workflow["stages"]}
    assert "tester-changed-files.json" in stages["tester"]["outputs"]
    assert stages["implementer"]["changed_files"] == "changed-files.json"
    assert stages["tester"]["changed_files"] == "tester-changed-files.json"
    assert "documenter-changed-files.json" in stages["documenter"]["outputs"]
    assert stages["documenter"]["changed_files"] == "documenter-changed-files.json"
    assert stages["documenter"]["schemas"] == {
        "documenter-changed-files.json": "changed-files",
    }


def test_tester_prompt_requires_tester_changed_files_record(harness_root):
    prompt = (harness_root / "prompts" / "story-tester.md").read_text()
    assert "tester-changed-files.json" in prompt
    assert "same schema as changed-files.json" in prompt
    for group in ("modified", "created", "deleted"):
        assert f'"{group}"' in prompt


def test_verifier_prompt_injects_both_records_with_distinct_guidance(harness_root):
    prompt = (harness_root / "prompts" / "story-verifier.md").read_text()
    assert "{{changed_files}}" in prompt
    assert "{{tester_changed_files}}" in prompt
    implementer_pos = prompt.index("{{changed_files}}")
    tester_pos = prompt.index("{{tester_changed_files}}")
    implementer_guidance = prompt[:implementer_pos].rsplit("\n\n", 1)[-1]
    tester_guidance = prompt[implementer_pos:tester_pos]
    assert "scope" in implementer_guidance
    assert "expected additions" in tester_guidance
    assert "not" in tester_guidance and "violations" in tester_guidance


def test_blocked_violation_checks_only_the_named_record(tmp_path):
    _write(tmp_path / "changed-files.json",
           {"modified": ["src/app.py"], "created": [], "deleted": []})
    _write(tmp_path / "tester-changed-files.json",
           {"modified": [], "created": [], "deleted": ["rules/execution-rules.json"]})
    blocked = [".git/", ".harness/runs/", "rules/"]
    assert story_coordinator._blocked_violation(
        tmp_path, "changed-files.json", blocked) is None
    assert story_coordinator._blocked_violation(
        tmp_path, "tester-changed-files.json", blocked) == "rules/execution-rules.json"


def test_tester_deleting_blocked_path_escalates(target_root, harness_root):
    runner = StageRunner(
        target_root,
        tester_record={"modified": [], "created": [],
                       "deleted": ["rules/execution-rules.json"]},
    )
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    assert runner.calls == ["implementer", "tester"]
    summary = (runner.run_dir / "escalation-summary.md").read_text()
    assert "tester modified blocked path: rules/execution-rules.json" in summary


def _invocations_before_escalating(harness_root: Path, stage_name: str) -> int:
    """How many times a stage runs before a mechanical failure escalates it.

    Its first invocation plus its declared self-route budget, read off the
    stage's own declaration. Written out as a literal, this was
    `["implementer", "tester"]` — a list that assumed the tester never runs
    again in place, and that went red the moment story-047 granted it a budget
    of two. What the assertion below claims is unchanged; only how it resolves
    the number of calls is.
    """
    workflow = json.loads(
        (harness_root / "workflows" / "story-workflow.json").read_text())
    declaration = next(s for s in workflow["stages"] if s["name"] == stage_name)
    return 1 + declaration.get("max_self_routes", 0)


def test_tester_without_record_escalates_before_verifier(target_root, harness_root):
    runner = StageRunner(target_root, write_tester_record=False)
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    tries = _invocations_before_escalating(harness_root, "tester")
    assert runner.calls == ["implementer"] + ["tester"] * tries
    # The escalation is still before the verifier however many tries it took.
    assert "verifier" not in runner.calls
    summary = (runner.run_dir / "escalation-summary.md").read_text()
    assert "tester did not produce required artifacts" in summary
    assert "tester-changed-files.json" in summary


def test_enforcement_follows_declaration_not_stage_name(target_root, harness_root, tmp_path):
    """Removing the tester's changed_files declaration disables its check,
    proving enforcement is driven by the workflow definition."""
    harness_copy = tmp_path / "harness-copy"
    for sub in ("prompts", "workflows", "rules", "schemas"):
        shutil.copytree(harness_root / sub, harness_copy / sub)
    workflow_path = harness_copy / "workflows" / "story-workflow.json"
    workflow = json.loads(workflow_path.read_text())
    for stage in workflow["stages"]:
        if stage["name"] == "tester":
            del stage["changed_files"]
    _write(workflow_path, workflow)

    runner = StageRunner(
        target_root,
        tester_record={"modified": ["rules/execution-rules.json"],
                       "created": [], "deleted": []},
    )
    code = story_coordinator.run_story("story-001", harness_copy, target_root, runner)
    assert code == 0
    assert runner.calls == ["implementer", "tester", "documenter", "verifier"]


# --------------------------------------------------------------------------
# story-044: the documenter records what it changed
#
# The documenter is the one stage that writes to the repository after the
# verdict, and until this story it was the only writing stage whose edits
# nothing checked. Enabling the check is one declaration on the stage, so
# these cases are written against a real coordinator run rather than against
# the workflow file: the workflow file says what was declared, and only a run
# says whether the declaration is what the coordinator acts on.
# --------------------------------------------------------------------------


ALL_STAGES = ["implementer", "tester", "documenter", "verifier"]

#: Where a run ends when the documenter's own record is what escalates it.
#: Since story-045 the documenter runs before the verifier, so an escalation
#: at that stage leaves the stage after it uninvoked. Derived from the list
#: above rather than written out, so the two cannot disagree.
THROUGH_DOCUMENTER = ALL_STAGES[:ALL_STAGES.index("documenter") + 1]

#: A record naming a path the rules block, used for both the tester's and the
#: documenter's blocked-path case so the two messages are comparable.
BLOCKED_RECORD = {"modified": ["rules/execution-rules.json"],
                  "created": [], "deleted": []}


def _changed_files_fields(harness_root: Path) -> list[str]:
    schema = json.loads(
        (harness_root / "schemas" / "changed-files.schema.json").read_text()
    )
    return sorted(schema["properties"])


def _fields_stated_in_prose(template: str, fields: list[str]) -> list[str]:
    """Which schema field names the template states itself.

    The placeholder is removed first: the rendered prompt names every field,
    because the schema is injected into it. The question is whether the
    template restates them beside the injection.
    """
    body = template.replace("{{changed_files_schema}}", "")
    return [field for field in fields if field in body]


def _harness_without_documenter_declaration(harness_root: Path,
                                            tmp_path: Path) -> Path:
    """A copy of the harness in which the documenter declares no record.

    The pre-story harness, for the purpose of these cases: everything else —
    the required-artifact check, the schema check, the blocked-path check —
    is untouched, so a case that behaves differently here behaves differently
    because of the declaration and nothing else.
    """
    harness_copy = tmp_path / "harness-before-story-044"
    for sub in ("prompts", "workflows", "rules", "schemas"):
        shutil.copytree(harness_root / sub, harness_copy / sub)
    workflow_path = harness_copy / "workflows" / "story-workflow.json"
    workflow = json.loads(workflow_path.read_text())
    for stage in workflow["stages"]:
        if stage["name"] == "documenter":
            record = stage.pop("changed_files")
            stage["outputs"] = [o for o in stage["outputs"] if o != record]
            stage.pop("schemas", None)
    _write(workflow_path, workflow)
    return harness_copy


def test_documenter_prompt_requires_the_record_with_the_schema_injected(harness_root):
    prompt = (harness_root / "prompts" / "documenter.md").read_text()
    assert "documenter-changed-files.json" in prompt
    assert "{{changed_files_schema}}" in prompt

    fields = _changed_files_fields(harness_root)
    # The absence: the template restates no field of the schema it injects.
    assert _fields_stated_in_prose(prompt, fields) == []
    # Two controls for that absence. The first is a template that does state
    # the fields — the tester's, which lists them rather than injecting them —
    # and the second is this same template with the schema pasted in where the
    # placeholder stands. Both must be reported, or the check above is passing
    # because it has stopped seeing field names at all.
    tester_prompt = (harness_root / "prompts" / "story-tester.md").read_text()
    assert _fields_stated_in_prose(tester_prompt, fields) == fields
    pasted = prompt.replace(
        "{{changed_files_schema}}",
        (harness_root / "schemas" / "changed-files.schema.json").read_text(),
    )
    assert _fields_stated_in_prose(pasted, fields) == fields


def test_documenter_prompt_separates_the_record_it_writes_from_the_one_injected(harness_root):
    """{{changed_files}} still carries the implementer's record inward, and the
    template says so where it stands, so the two cannot be confused."""
    prompt = (harness_root / "prompts" / "documenter.md").read_text()
    assert "{{changed_files}}" in prompt

    request = prompt[prompt.index("documenter-changed-files.json"):
                     prompt.index("{{changed_files_schema}}")]
    assert "outward" in request
    assert "not the injected" in request

    injection = prompt[:prompt.index("{{changed_files}}")].rsplit("\n\n", 1)[-1]
    assert "implementer" in injection
    assert "inward" in injection


def test_documenter_record_reuses_the_one_changed_files_schema(harness_root):
    """One definition of the record's shape, validated the same way for all
    three stages that write one."""
    workflow = json.loads(
        (harness_root / "workflows" / "story-workflow.json").read_text()
    )
    stages = {s["name"]: s for s in workflow["stages"]}
    documenter_schema = stages["documenter"]["schemas"]["documenter-changed-files.json"]
    assert documenter_schema == stages["implementer"]["schemas"]["changed-files.json"]
    assert documenter_schema == stages["tester"]["schemas"]["tester-changed-files.json"]

    # The absence: no schema file was added for the documenter's record. The
    # control is a copy of schemas/ with exactly such a file planted, which
    # the same scan must report.
    def documenter_schemas(directory: Path) -> list[str]:
        return sorted(p.name for p in directory.glob("*.schema.json")
                      if "documenter" in p.name)

    assert documenter_schemas(harness_root / "schemas") == []


def test_documenter_schema_scan_reports_a_planted_schema_file(harness_root, tmp_path):
    """The control for the absence asserted above, kept beside it."""
    copy = tmp_path / "schemas"
    shutil.copytree(harness_root / "schemas", copy)
    (copy / "documenter-changed-files.schema.json").write_text("{}\n")
    planted = sorted(p.name for p in copy.glob("*.schema.json")
                     if "documenter" in p.name)
    assert planted == ["documenter-changed-files.schema.json"]


def _sources_naming(directory: Path, name: str) -> list[str]:
    return sorted(p.name for p in directory.rglob("*.py")
                  if name in p.read_text(encoding="utf-8"))


#: The one module allowed to spell the documenter's record, and it is the
#: injection side rather than the enforcement side: since story-045 the
#: verifier is handed the documenter's record through a placeholder, and
#: context_assembler already spells the implementer's and the tester's records
#: the same way. The exemption is held shut from both directions below — the
#: exempt module must actually contain the name, or it is stale — and the
#: subject is unchanged: what the *coordinator* enforces still reaches it only
#: off the loaded workflow.
NAMES_THE_RECORD_FOR_INJECTION = "context_assembler.py"


def test_no_orchestration_source_names_the_documenters_record(harness_root, tmp_path):
    """The record name reaches the coordinator only off the loaded workflow.

    The absence is that no module under orchestration/ spells it, save the one
    exempt module named above; the control is a copy of orchestration/ with the
    name planted in one module, which the same scan reports.
    """
    orchestration = harness_root / "orchestration"
    naming = _sources_naming(orchestration, "documenter-changed-files.json")
    assert naming == [NAMES_THE_RECORD_FOR_INJECTION]
    # Held shut from the other side: the coordinator, which is what enforces
    # the record, still spells neither the record nor the stage's own name for
    # it, so the exemption cannot quietly widen into the routing code.
    assert "documenter-changed-files.json" not in (
        orchestration / "story_coordinator.py").read_text(encoding="utf-8")

    planted = tmp_path / "orchestration-with-the-name"
    shutil.copytree(orchestration, planted)
    (planted / "planted.py").write_text(
        'RECORD = "documenter-changed-files.json"\n', encoding="utf-8")
    assert _sources_naming(planted, "documenter-changed-files.json") == [
        NAMES_THE_RECORD_FOR_INJECTION, "planted.py"]


def test_documenter_writing_a_clean_record_completes_the_run(target_root, harness_root):
    runner = StageRunner(
        target_root,
        documenter_record={"modified": [".harness/docs/ARCHITECTURE.md"],
                           "created": [], "deleted": []},
    )
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 0
    assert runner.calls == ALL_STAGES
    assert (runner.run_dir / "completion-report.md").is_file()
    assert json.loads((runner.run_dir / "state.json").read_text())["status"] == "completed"


def test_documenter_without_a_record_escalates_as_a_missing_artifact(
        target_root, harness_root):
    """Reported by the path that reports any other missing stage output: the
    control is the tester's own missing-record escalation above, which reads
    the same sentence with its own stage and artifact named.

    A missing required output is a mechanical failure, so the documenter runs
    again in place while it has budget. The call list is resolved off its
    declaration for the reason the tester's is: written out as the literal
    `THROUGH_DOCUMENTER`, it assumed the documenter never runs again in place,
    and it went red the moment story-060 granted it a budget. The claim is
    unchanged — the escalation is still at the documenter, however many tries
    it took.

    The recorded reason gained the second half a budgeted stage's escalation
    has carried since story-036: the mechanical failure, then that the stage
    has spent its budget. Both halves are still asserted exactly, so a reason
    that stopped naming the missing artifact still goes red.
    """
    runner = StageRunner(target_root, write_documenter_record=False)
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    budget = _invocations_before_escalating(harness_root, "documenter") - 1
    assert runner.calls == THROUGH_DOCUMENTER[:-1] + ["documenter"] * (budget + 1)
    assert "verifier" not in runner.calls
    reason = story_coordinator.escalation_reason(runner.run_dir)
    assert reason == ("documenter did not produce required artifacts: "
                      "documenter-changed-files.json"
                      f"; documenter has exhausted its self-route budget of "
                      f"{budget}")
    assert not (runner.run_dir / "completion-report.md").is_file()


def test_documenter_naming_a_blocked_path_escalates(target_root, harness_root):
    runner = StageRunner(target_root, documenter_record=BLOCKED_RECORD)
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    assert runner.calls == THROUGH_DOCUMENTER
    reason = story_coordinator.escalation_reason(runner.run_dir)
    assert reason == "documenter modified blocked path: rules/execution-rules.json"


def test_documenter_blocked_path_message_has_the_shape_the_tester_produces(
        target_root, harness_root, tmp_path):
    """The same sentence, differing only in the stage that produced it — the
    two are rendered by one line of the coordinator, and this is what says so
    from the outside."""
    # Copied before either run, so the second run starts from a target no run
    # has touched rather than from one already carrying a story branch.
    second_target = tmp_path / "second-target"
    shutil.copytree(target_root, second_target)

    documenter_runner = StageRunner(target_root, documenter_record=BLOCKED_RECORD)
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, documenter_runner) == 2
    documenter_reason = story_coordinator.escalation_reason(documenter_runner.run_dir)

    tester_runner = StageRunner(second_target, tester_record=BLOCKED_RECORD)
    assert story_coordinator.run_story(
        "story-001", harness_root, second_target, tester_runner) == 2
    tester_reason = story_coordinator.escalation_reason(tester_runner.run_dir)

    assert documenter_reason == tester_reason.replace("tester", "documenter", 1)


def test_documenter_record_failing_the_schema_escalates_as_invalid(
        target_root, harness_root):
    runner = StageRunner(
        target_root,
        documenter_record={"modified": ".harness/docs/ARCHITECTURE.md"},
    )
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    assert runner.calls == THROUGH_DOCUMENTER
    reason = story_coordinator.escalation_reason(runner.run_dir)
    assert reason.startswith(
        "documenter wrote an invalid artifact: documenter-changed-files.json "
        "does not match the changed-files schema"
    )


def test_documenter_enforcement_comes_from_the_declaration(
        target_root, harness_root, tmp_path):
    """The control for all three escalations above.

    Against a harness whose documenter declares no record — the shape this
    stage had before this story — the same runs behave as they did then: a
    documenter naming a blocked path completes, and a documenter writing no
    record completes, because nothing required one. Neither escalation above
    is therefore something the coordinator would have produced anyway.
    """
    harness_copy = _harness_without_documenter_declaration(harness_root, tmp_path)
    second_target = tmp_path / "target-without-record"
    shutil.copytree(target_root, second_target)

    blocked_runner = StageRunner(target_root, documenter_record=BLOCKED_RECORD)
    assert story_coordinator.run_story(
        "story-001", harness_copy, target_root, blocked_runner) == 0
    assert blocked_runner.calls == ALL_STAGES

    silent_runner = StageRunner(second_target, write_documenter_record=False)
    assert story_coordinator.run_story(
        "story-001", harness_copy, second_target, silent_runner) == 0
    assert silent_runner.calls == ALL_STAGES
