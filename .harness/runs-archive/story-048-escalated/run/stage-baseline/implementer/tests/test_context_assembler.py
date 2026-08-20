import json
from pathlib import Path

import context_assembler
import harness_config
import conftest
import schema_validator
import story_parser


#: The loaded workflow build_context has taken as a required argument
#: since story-028, which injects the workflow's own facts — its stages,
#: its create restrictions, its retry routes — into every stage prompt.
WORKFLOW = conftest.shipped_workflow(
    Path(context_assembler.__file__).resolve().parents[1], "story-workflow")


def parsed_story(story_text: str) -> dict:
    return story_parser.parse(story_text, schema_validator.load_schema("story"))


def test_render_replaces_placeholders_and_defaults_to_none():
    template = "Story:\n{{story}}\n\nRetry state:\n{{retry_state}}\n"
    rendered = context_assembler.render(template, {"story": "the story", "retry_state": None})
    assert "the story" in rendered
    assert "Retry state:\nNone" in rendered
    assert "{{" not in rendered


def test_real_templates_render_without_leftover_placeholders(target_root, harness_root):
    config = harness_config.load_config(target_root)
    rules = harness_config.load_rules(harness_root)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)

    context = context_assembler.build_context(
        story_text=story_text,
        story=parsed_story(story_text),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=config,
        rules=rules,
        workflow=WORKFLOW,
        retry_count=0,
    )
    for prompt_file in ("implementer.md", "tester.md", "verifier.md", "documenter.md"):
        template = context_assembler.load_template(harness_root, prompt_file)
        rendered = context_assembler.render(template, context)
        assert "{{" not in rendered, prompt_file
        assert "Sample story for coordinator tests" in rendered

    implementer = context_assembler.render(
        context_assembler.load_template(harness_root, "implementer.md"), context
    )
    assert "- rules/" in implementer          # blocked paths injected
    assert "echo tests-ok" in implementer     # test command injected
    assert "Retry state:\nNone" in implementer


def test_both_changed_files_records_injected_separately(target_root, harness_root):
    config = harness_config.load_config(target_root)
    rules = harness_config.load_rules(harness_root)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "changed-files.json").write_text(
        json.dumps({"modified": ["src/app.py"], "created": [], "deleted": []})
    )
    (run_dir / "tester-changed-files.json").write_text(
        json.dumps({"modified": [], "created": ["tests/test_app.py"], "deleted": []})
    )

    context = context_assembler.build_context(
        story_text=story_text,
        story=parsed_story(story_text),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=config,
        rules=rules,
        workflow=WORKFLOW,
        retry_count=0,
    )
    assert "src/app.py" in context["changed_files"]
    assert "tests/test_app.py" not in context["changed_files"]
    assert "tests/test_app.py" in context["tester_changed_files"]


def test_tester_changed_files_renders_none_when_absent(target_root, harness_root):
    config = harness_config.load_config(target_root)
    rules = harness_config.load_rules(harness_root)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)

    context = context_assembler.build_context(
        story_text=story_text,
        story=parsed_story(story_text),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=config,
        rules=rules,
        workflow=WORKFLOW,
        retry_count=0,
    )
    assert context["tester_changed_files"] is None
    verifier = context_assembler.render(
        context_assembler.load_template(harness_root, "verifier.md"), context
    )
    assert "{{" not in verifier


def test_harness_layer_is_single_source_of_truth(target_root, harness_root, tmp_path):
    config = harness_config.load_config(target_root)
    rules = harness_config.load_rules(harness_root)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Mirror the real prompts into a throwaway harness so we can edit the
    # shared partial without mutating the repository.
    stages = ("implementer.md", "tester.md", "documenter.md")
    fake_root = tmp_path / "harness"
    prompts = fake_root / "prompts"
    prompts.mkdir(parents=True)
    for name in (*stages, "harness-layer.md"):
        (prompts / name).write_text((harness_root / "prompts" / name).read_text(), encoding="utf-8")

    def render_stages():
        context = context_assembler.build_context(
            story_text=story_text, story=parsed_story(story_text), run_dir=run_dir, target_root=target_root,
            harness_root=fake_root, config=config, rules=rules, workflow=WORKFLOW, retry_count=0,
        )
        return {
            name: context_assembler.render(context_assembler.load_template(fake_root, name), context)
            for name in stages
        }

    before = render_stages()
    for name, text in before.items():
        assert "- rules/" in text, name            # blocked paths resolved inside the injected block
        assert "{{harness_layer}}" not in text, name
        assert "{{blocked_paths}}" not in text, name

    # A single-file edit to the shared partial changes every stage prompt.
    (prompts / "harness-layer.md").write_text(
        "[Harness Layer]\nSENTINEL shared rule change\nBlocked paths for every stage:\n{{blocked_paths}}",
        encoding="utf-8",
    )
    after = render_stages()
    for name in stages:
        assert "SENTINEL shared rule change" in after[name], name
        assert after[name] != before[name], name
        assert "- rules/" in after[name], name     # blocked paths still resolve after the edit


def _context(target_root, harness_root):
    config = harness_config.load_config(target_root)
    rules = harness_config.load_rules(harness_root)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    return context_assembler.build_context(
        story_text=story_text,
        story=parsed_story(story_text),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=config,
        rules=rules,
        workflow=WORKFLOW,
        retry_count=0,
    )


def test_every_schema_is_exposed_under_its_placeholder_name(target_root, harness_root):
    context = _context(target_root, harness_root)
    for path in sorted((harness_root / "schemas").glob("*.schema.json")):
        stem = path.name[: -len(".schema.json")]
        key = stem.replace("-", "_") + "_schema"
        assert context[key] == path.read_text(), key
        assert json.loads(context[key])["title"] == stem
    assert "verification_result_schema" in context


def test_prompts_carry_schema_placeholders_not_inline_json(harness_root):
    implementer = (harness_root / "prompts" / "implementer.md").read_text()
    tester = (harness_root / "prompts" / "tester.md").read_text()
    verifier = (harness_root / "prompts" / "verifier.md").read_text()

    assert "{{changed_files_schema}}" in implementer
    assert "{{test_results_schema}}" in tester
    assert "{{changed_files_schema}}" in tester
    assert "{{verification_result_schema}}" in verifier
    assert "{{retry_guidance_schema}}" in verifier

    # No inline JSON artifact body survives in any of the three.
    for name, text in (("implementer", implementer), ("tester", tester),
                       ("verifier", verifier)):
        assert '["<path>", "..."]' not in text, name
        assert '"passed" | "failed"' not in text, name
        assert "<int>" not in text, name


def test_rendered_prompts_contain_the_resolved_schema_text(target_root, harness_root):
    context = _context(target_root, harness_root)
    expected = {
        "implementer.md": ["changed-files"],
        "tester.md": ["test-results", "changed-files"],
        "verifier.md": ["verification-result", "retry-guidance"],
    }
    for prompt_file, schema_names in expected.items():
        rendered = context_assembler.render(
            context_assembler.load_template(harness_root, prompt_file), context
        )
        assert "{{" not in rendered, prompt_file
        for schema_name in schema_names:
            body = (harness_root / "schemas" / f"{schema_name}.schema.json").read_text()
            assert body in rendered, (prompt_file, schema_name)


def test_latest_verifier_finding_reads_newest_iteration(tmp_path: Path):
    run_dir = tmp_path
    (run_dir / "verification").mkdir()
    (run_dir / "verification" / "iteration-1.json").write_text(json.dumps({"status": "failed"}))
    (run_dir / "verification" / "iteration-2.json").write_text(json.dumps({"status": "passed"}))
    finding = context_assembler.latest_verifier_finding(run_dir)
    assert "passed" in finding
