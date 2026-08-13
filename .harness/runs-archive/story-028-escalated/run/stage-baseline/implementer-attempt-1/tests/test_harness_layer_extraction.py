"""Independent tester validation for story-003.

Validates that the shared [Harness Layer] block was extracted into a single
injected file (prompts/harness-layer.md) and injected into the implementer,
tester, and documenter stage templates via {{harness_layer}}, while the
verifier's distinct harness layer and the non-stage prompts stay intact.

These assertions are written against the story's acceptance criteria only,
independently of how the extraction was implemented.
"""
import context_assembler
import harness_config
import schema_validator
import story_parser

STAGE_TEMPLATES = ("implementer.md", "tester.md", "documenter.md")


def parsed_story(story_text: str) -> dict:
    return story_parser.parse(story_text, schema_validator.load_schema("story"))

# The shared block whose duplication story-003 removes. Matched exactly,
# including the {{blocked_paths}} placeholder line.
SHARED_BLOCK = (
    "[Harness Layer]\n"
    "\n"
    "All work must:\n"
    "- stay within the scope defined by the injected workflow state,\n"
    "- produce the required output artifacts in the run directory, and\n"
    "- avoid modifying blocked paths under any circumstances.\n"
    "\n"
    "Blocked paths for every stage:\n"
    "{{blocked_paths}}"
)


def _build(target_root, harness_root):
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
        retry_count=0,
    )
    return context, rules


def test_shared_partial_file_holds_the_block_once(harness_root):
    """AC1: prompts/harness-layer.md exists and holds the shared block including
    the {{blocked_paths}} placeholder line."""
    partial = (harness_root / "prompts" / "harness-layer.md").read_text()
    assert partial == SHARED_BLOCK
    assert "{{blocked_paths}}" in partial


def test_stage_templates_use_placeholder_not_the_literal_block(harness_root):
    """AC2: each stage template contains {{harness_layer}} and no longer holds
    the literal duplicated harness-layer block."""
    for name in STAGE_TEMPLATES:
        template = context_assembler.load_template(harness_root, name)
        assert "{{harness_layer}}" in template, name
        # The distinctive lines of the shared block must not survive inline.
        assert "All work must:" not in template, name
        assert "Blocked paths for every stage:" not in template, name


def test_verifier_and_non_stage_prompts_are_intact(harness_root):
    """AC3: verifier keeps its distinct evidence-discipline harness layer and
    is not switched to the shared placeholder; planner and assist have no
    harness layer at all."""
    verifier = context_assembler.load_template(harness_root, "verifier.md")
    assert "{{harness_layer}}" not in verifier
    assert "All verification claims must:" in verifier      # its own, distinct block
    assert "All work must:" not in verifier
    for name in ("planner.md", "assist.md"):
        template = context_assembler.load_template(harness_root, name)
        assert "{{harness_layer}}" not in template, name
        assert "[Harness Layer]" not in template, name


def test_rendered_stage_prompts_have_no_leftover_placeholders(target_root, harness_root):
    """AC4: the two-pass render resolves both {{harness_layer}} and the nested
    {{blocked_paths}} in every stage prompt."""
    context, _ = _build(target_root, harness_root)
    for name in STAGE_TEMPLATES:
        rendered = context_assembler.render(
            context_assembler.load_template(harness_root, name), context
        )
        assert "{{harness_layer}}" not in rendered, name
        assert "{{blocked_paths}}" not in rendered, name
        assert "{{" not in rendered, name


def test_rendered_harness_layer_matches_pre_change_text(target_root, harness_root):
    """AC5: the injected, resolved harness layer is equivalent to the pre-change
    inline block — same rule text with the actual blocked paths resolved."""
    context, rules = _build(target_root, harness_root)
    resolved_blocked = "\n".join(f"- {p}" for p in rules.get("blocked_paths", []))
    expected = SHARED_BLOCK.replace("{{blocked_paths}}", resolved_blocked)
    assert resolved_blocked  # guard: blocked paths are actually present
    for name in STAGE_TEMPLATES:
        rendered = context_assembler.render(
            context_assembler.load_template(harness_root, name), context
        )
        assert expected in rendered, name


def test_one_file_edit_changes_every_stage(target_root, harness_root, tmp_path):
    """AC6: editing only prompts/harness-layer.md changes the harness layer of
    all three rendered stage prompts."""
    config = harness_config.load_config(target_root)
    rules = harness_config.load_rules(harness_root)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)

    fake_root = tmp_path / "harness"
    prompts = fake_root / "prompts"
    prompts.mkdir(parents=True)
    for name in (*STAGE_TEMPLATES, "harness-layer.md"):
        (prompts / name).write_text(
            (harness_root / "prompts" / name).read_text(), encoding="utf-8"
        )

    def render_all():
        context = context_assembler.build_context(
            story_text=story_text, story=parsed_story(story_text), run_dir=run_dir, target_root=target_root,
            harness_root=fake_root, config=config, rules=rules, retry_count=0,
        )
        return {
            name: context_assembler.render(
                context_assembler.load_template(fake_root, name), context
            )
            for name in STAGE_TEMPLATES
        }

    before = render_all()

    (prompts / "harness-layer.md").write_text(
        "[Harness Layer]\nONE-FILE-EDIT-MARKER\nBlocked paths for every stage:\n{{blocked_paths}}",
        encoding="utf-8",
    )
    after = render_all()

    for name in STAGE_TEMPLATES:
        assert "ONE-FILE-EDIT-MARKER" in after[name], name
        assert after[name] != before[name], name
        # blocked paths still resolve inside the edited block
        assert "- rules/" in after[name], name


def test_harness_layer_renders_none_when_partial_absent(target_root, harness_root, tmp_path):
    """The graceful-absence path: with no shared partial, harness_layer is unset
    and renders as None rather than raising."""
    fake_root = tmp_path / "harness"
    (fake_root / "prompts").mkdir(parents=True)
    config = harness_config.load_config(target_root)
    rules = harness_config.load_rules(harness_root)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)

    context = context_assembler.build_context(
        story_text=story_text, story=parsed_story(story_text), run_dir=run_dir, target_root=target_root,
        harness_root=fake_root, config=config, rules=rules, retry_count=0,
    )
    assert context.get("harness_layer") is None
    rendered = context_assembler.render("x {{harness_layer}} y", context)
    assert rendered == "x None y"
