import json
from pathlib import Path

import context_assembler
import harness_config


def test_render_replaces_placeholders_and_defaults_to_none():
    template = "Story:\n{{story}}\n\nRetry state:\n{{retry_state}}\n"
    rendered = context_assembler.render(template, {"story": "the story", "retry_state": None})
    assert "the story" in rendered
    assert "Retry state:\nNone" in rendered
    assert "{{" not in rendered


def test_extract_section_pulls_one_block():
    story = "story:\n  id: x\nacceptance_criteria:\n  - a\n  - b\nscope:\n  modify: []\n"
    section = context_assembler.extract_section(story, "acceptance_criteria")
    assert section == "  - a\n  - b"
    assert context_assembler.extract_section(story, "missing_key") is None


def test_real_templates_render_without_leftover_placeholders(target_root, harness_root):
    config = harness_config.load_config(target_root)
    rules = harness_config.load_rules(harness_root)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)

    context = context_assembler.build_context(
        story_text=story_text,
        run_dir=run_dir,
        target_root=target_root,
        config=config,
        rules=rules,
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


def test_latest_verifier_finding_reads_newest_iteration(tmp_path: Path):
    run_dir = tmp_path
    (run_dir / "verification").mkdir()
    (run_dir / "verification" / "iteration-1.json").write_text(json.dumps({"status": "failed"}))
    (run_dir / "verification" / "iteration-2.json").write_text(json.dumps({"status": "passed"}))
    finding = context_assembler.latest_verifier_finding(run_dir)
    assert "passed" in finding
