"""The planner decides with the standards every judged stage is judged against.

story-171's subject, in two halves:

- the target's standards reach the planner *whole*, through the one reader
  every judged stage's context and the Inspector's prompt are built from,
  rendered into `prompts/planner.md` under the heading every stage prompt
  uses — so a plan is decided against the same text the stages that build
  it are held to, rather than against an instruction to go and read it.
- a workflow that cannot finish the work is refused before the interview: the
  finishing test — will the finished work assert something nothing asserts
  today? — is written into both shipped `applies_when` statements, which is
  the one place both proposers read, and the planner prompt carries a
  backstop that tells the developer, before the interview, when no stage of
  the settled workflow may write the assertion the claim needs.

Written from the acceptance criteria rather than from the implementation, and
preferring observable behaviour where it exists: what `scripts/l5-plan`
actually hands to `claude --append-system-prompt`, captured by a fake `claude`
on PATH the way `tests/test_planner_injection.py` captures it; what
`standards_context`, `build_context` and the Inspector's render resolve for a
target built here; and what the shipped template and definitions say.

Every absence below has its control beside it. The verbatim-standards
property is controlled by rendering a copy of the template with the
placeholder removed and asserting the same check reports every file missing.
The "no longer instructs", "defines no reader" and "carries no leftover
placeholder" properties are controlled against the text at this story's own
baseline, read through the shared resolution `tests/conftest.py` provides,
where each of those things was still present. The "left alone" properties are
controlled by the same diff over a path this story did change and by a
repository built here in which a story did touch those paths.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
from conftest import BASELINE, repository_file_at, story_diff

import context_assembler
import filed_query
import harness_config
import inspection
import schema_validator
import story_parser

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATION = REPO_ROOT / "orchestration"
SCRIPTS = REPO_ROOT / "scripts"
WORKFLOWS = REPO_ROOT / "workflows"
PLANNER_REL = "prompts/planner.md"
INSPECTION_REL = "orchestration/inspection.py"

#: The workflow the captured sessions below are rendered against. Stated
#: rather than left to a fallback, for the reason `test_planner_injection.py`
#: states it: an invocation with no terminal and no --workflow is refused.
PLANNED_WORKFLOW = "story-workflow"

#: The two definitions this repository ships, by the name each is loaded by.
#: They are the subject of the applies_when assertions below — a claim about
#: what this harness ships has to read what it ships — and nothing here uses
#: them as an input.
SHIPPED_DEFINITIONS = ("refactor-workflow", "story-workflow")

#: The prompts this story was told to leave alone.
UNTOUCHED_PROMPTS = ("prompts/workflow-selector.md", "prompts/inspector.md")

#: The heading every stage prompt renders the standards beneath, and the
#: placeholder that carries them. Derived nowhere: the template and the
#: assembler have to agree on the name, and the assertions below hold them
#: to it, as `test_planner_injection.py` does for the restrictions placeholder.
STANDARDS_HEADING = "Repository standards:"
STANDARDS_PLACEHOLDER = "{{repository_standards}}"
TESTING_PLACEHOLDER = "{{testing_standards}}"

PLACEHOLDER = re.compile(r"\{\{[a-z_]+\}\}")

#: The one glob the standards directory is read with. A source assertion
#: counts this literal, so it is spelled once here.
STANDARDS_GLOB = 'glob("*.md")'

#: The standards a target built here declares. Names nothing in the harness
#: could have guessed, so "the text reached the prompt" means it was read from
#: the directory rather than already being in the template; `testing.md` is
#: among them because that is the one file the testing placeholder is
#: pointed at by name, and the shared reader has to keep answering it.
STANDARDS = {
    "xyzzy-conventions.md": (
        "# xyzzy conventions\n"
        "- one fact is spelled in one module: xyzzy-marker-one\n"
    ),
    "zzz-naming.md": (
        "# zzz naming\n"
        "- a thing is named for what it is: zzz-marker-two\n"
    ),
    "testing.md": (
        "# testing\n"
        "- an absence needs a control beside it: testing-marker-three\n"
    ),
}

#: The instruction the process list used to carry, and no longer does.
STANDARDS_DIRECTORY_INSTRUCTION = ".harness/standards/"

#: The configuration key the process list still instructs a reading of.
ARCHITECTURE_DOCS_KEY = "architecture_docs"

#: What the backstop has to say, as phrases a reader of the prompt would look
#: for: the question, the fact it is decided against, the remedy, and the
#: refusal to proceed. The fact is a pattern, since the template wraps it.
BACKSTOP_QUESTION = "assert something nothing asserts today"
BACKSTOP_FACT = r"may create files under the tests\s+directory"
BACKSTOP_REMEDY = "l5-plan --workflow"
BACKSTOP_REFUSAL = "do not proceed into the interview"


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------


def planner_template() -> str:
    return (REPO_ROOT / PLANNER_REL).read_text(encoding="utf-8")


def baseline_planner_template() -> str:
    """The template as it stood before this story, through the shared
    resolution rather than a `git show HEAD:`."""
    return repository_file_at(PLANNER_REL, validation_file=Path(__file__),
                              bound=BASELINE)


def process_section(template: str) -> str:
    """The text between the [Process] heading and the next bracketed one."""
    match = re.search(r"\[Process\]\n(.*?)\n\[", template, re.DOTALL)
    assert match is not None, "the template has no [Process] section"
    return match.group(1)


def before_the_numbered_steps(section: str) -> str:
    """What the process section says before its first numbered step, which
    is where a decision about whether the interview starts at all has to be."""
    match = re.search(r"^1\. ", section, re.MULTILINE)
    assert match is not None, "the process section has no numbered steps"
    return section[: match.start()]


def raw_definition(name: str) -> dict:
    """A shipped definition as the file holds it, tokens unresolved, so the
    comparison against the base commit is byte-for-byte on each key."""
    return json.loads((WORKFLOWS / f"{name}.json").read_text(encoding="utf-8"))


def baseline_definition(name: str) -> dict:
    return json.loads(repository_file_at(f"workflows/{name}.json",
                                         validation_file=Path(__file__),
                                         bound=BASELINE))


def missing_standards(rendered: str, standards: dict[str, str]) -> set[str]:
    """The standards files whose text the rendered prompt does not carry
    verbatim. The check both the assertion and its control use."""
    return {name for name, text in standards.items() if text not in rendered}


def build_target(root: Path, standards: dict[str, str] | None) -> Path:
    """A target repository l5-plan can plan in, declaring the given standards
    or — with None — no standards directory at all.

    A repository, because since story-117 planning happens in a worktree cut
    from the base. tests_dir is what the create restriction resolves to, so
    the deployed workflow's facts render against a value rather than a token.
    """
    (root / ".harness").mkdir(parents=True)
    (root / ".harness" / "config.yaml").write_text(
        f"workflow: {PLANNED_WORKFLOW}\ntests_dir: tests/\n", encoding="utf-8"
    )
    if standards is not None:
        directory = root / ".harness" / "standards"
        directory.mkdir()
        for name, text in standards.items():
            (directory / name).write_text(text, encoding="utf-8")
    conftest.init_repository(root)
    return root


@pytest.fixture
def plan_capture(tmp_path: Path):
    """A fake `claude` on PATH, returning a callable that runs l5-plan from a
    target and reports (result, captured argv or None) — the capture
    `tests/test_planner_injection.py` uses, pointed at a target built here."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_path = tmp_path / "argv.json"
    fake = bin_dir / "claude"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(argv_path)!r}, 'w').write(json.dumps(sys.argv))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    def run_plan(cwd: Path):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "l5-plan"),
             "--workflow", PLANNED_WORKFLOW, "a story request"],
            cwd=cwd, env=env, capture_output=True, text=True, timeout=120,
        )
        argv = (json.loads(argv_path.read_text(encoding="utf-8"))
                if argv_path.is_file() else None)
        return result, argv

    return run_plan


def captured_prompt(plan_capture, root: Path) -> str:
    result, argv = plan_capture(root)
    assert result.returncode == 0, result.stderr
    assert argv is not None, "l5-plan started no session"
    return argv[argv.index("--append-system-prompt") + 1]


# --------------------------------------------------------------------------
# The prompt l5-plan hands to claude carries the standards verbatim
# --------------------------------------------------------------------------


def test_the_captured_prompt_carries_every_standards_file_verbatim(
    plan_capture, tmp_path
):
    prompt = captured_prompt(plan_capture, build_target(tmp_path / "t", STANDARDS))
    assert missing_standards(prompt, STANDARDS) == set()
    assert PLACEHOLDER.search(prompt) is None


def test_the_standards_sit_beneath_the_heading_every_judged_stage_uses(
    plan_capture, tmp_path
):
    """Under the shared heading and in the shared shape: each file as its own
    "--- name ---" section, in name order, after the heading and not before
    it — the form `build_context` hands every judged stage."""
    prompt = captured_prompt(plan_capture, build_target(tmp_path / "t", STANDARDS))
    assert STANDARDS_HEADING in prompt
    body = prompt[prompt.index(STANDARDS_HEADING):]
    positions = [body.index(f"--- {name} ---\n{text}")
                 for name, text in sorted(STANDARDS.items())]
    assert positions == sorted(positions)
    assert missing_standards(prompt[: prompt.index(STANDARDS_HEADING)],
                             STANDARDS) == set(STANDARDS)


def test_the_verbatim_check_reports_a_render_with_the_placeholder_removed(
    tmp_path
):
    """Negative control: the same check, over the same context, against a copy
    of the template with the placeholder removed, reports every file missing.
    Green above then means the injection carried them, not that the check
    cannot see an absence or that the prose already held the text."""
    root = build_target(tmp_path / "t", STANDARDS)
    context = context_assembler.schema_context(REPO_ROOT)
    context.update(context_assembler.workflow_context(
        conftest.build_workflow(conftest.workflow_stage()),
        harness_config.load_rules(REPO_ROOT)))
    context.update(context_assembler.standards_context(
        root, harness_config.load_config(root)))

    intact = context_assembler.render(planner_template(), context)
    assert missing_standards(intact, STANDARDS) == set()

    stripped = planner_template().replace(STANDARDS_PLACEHOLDER, "")
    assert stripped != planner_template()
    rendered = context_assembler.render(stripped, context)
    assert missing_standards(rendered, STANDARDS) == set(STANDARDS)


# --------------------------------------------------------------------------
# A target declaring no standards directory still gets a session
# --------------------------------------------------------------------------


def test_a_target_with_no_standards_directory_still_starts_the_session(
    plan_capture, tmp_path
):
    prompt = captured_prompt(plan_capture, build_target(tmp_path / "t", None))
    assert PLACEHOLDER.search(prompt) is None
    assert STANDARDS_HEADING in prompt
    assert missing_standards(prompt, STANDARDS) == set(STANDARDS)


def test_the_placeholder_search_sees_the_placeholder_the_template_carries():
    """Control for the two leftover-placeholder absences above: the same
    search over the unrendered template finds the standards placeholder, so
    an empty result on a render is the render having resolved it."""
    found = {match.group(0) for match in PLACEHOLDER.finditer(planner_template())}
    assert STANDARDS_PLACEHOLDER in found


def test_standards_context_yields_none_for_both_placeholders_without_a_directory(
    tmp_path
):
    root = build_target(tmp_path / "t", None)
    config = harness_config.load_config(root)
    assert not (root / ".harness" / "standards").exists()
    context = context_assembler.standards_context(root, config)
    assert context == {"repository_standards": None, "testing_standards": None}
    # None is the optional-placeholder convention, and this is what it renders as.
    assert context_assembler.render(
        f"{STANDARDS_HEADING}\n{STANDARDS_PLACEHOLDER}", context
    ) == f"{STANDARDS_HEADING}\nNone"


def test_standards_context_reads_every_file_whole_and_the_testing_file_by_name(
    tmp_path
):
    root = build_target(tmp_path / "t", STANDARDS)
    context = context_assembler.standards_context(
        root, harness_config.load_config(root))
    assert set(context) == {"repository_standards", "testing_standards"}
    assert missing_standards(context["repository_standards"], STANDARDS) == set()
    assert context["testing_standards"] == STANDARDS["testing.md"]


def test_standards_context_reads_the_directory_the_target_configures(tmp_path):
    """The reader follows `standards_dir`, so a target that keeps its standards
    somewhere the default does not name is still read, and the default location
    is not consulted beside it."""
    root = tmp_path / "t"
    elsewhere = ".harness/zzz-house-rules"
    for name, text in STANDARDS.items():
        path = root / elsewhere / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / ".harness" / "standards").mkdir()
    (root / ".harness" / "standards" / "decoy.md").write_text(
        "decoy-marker: the default location, which is not configured\n",
        encoding="utf-8")
    context = context_assembler.standards_context(root, {"standards_dir": elsewhere})
    assert missing_standards(context["repository_standards"], STANDARDS) == set()
    assert "decoy-marker" not in context["repository_standards"]
    assert context["testing_standards"] == STANDARDS["testing.md"]


# --------------------------------------------------------------------------
# One reader of the standards directory, and every context resolves through it
# --------------------------------------------------------------------------


def function_holding(source: str, needle: str) -> list[str]:
    """The names of the top-level functions in `source` whose body carries
    `needle`, one entry per occurrence."""
    names = []
    for chunk in re.split(r"^(?=def )", source, flags=re.MULTILINE):
        if chunk.startswith("def ") and needle in chunk:
            names += [chunk[4:chunk.index("(")]] * chunk.count(needle)
    return names


def test_the_standards_glob_appears_in_exactly_one_function_of_the_package():
    holders = {
        path.name: function_holding(path.read_text(encoding="utf-8"),
                                    STANDARDS_GLOB)
        for path in sorted(ORCHESTRATION.glob("*.py"))
    }
    assert {name: found for name, found in holders.items() if found} == {
        "context_assembler.py": ["standards_context"]}


def test_the_glob_count_reports_the_reader_the_base_commit_still_carried():
    """Control for the count above: at this story's baseline the same search
    found the Inspector's private reader too, so one holder today is the
    second having been removed rather than the search having gone blind."""
    before = repository_file_at(INSPECTION_REL, validation_file=Path(__file__),
                                bound=BASELINE)
    assert function_holding(before, STANDARDS_GLOB) == ["_standards"]


def test_inspection_defines_no_standards_reader_of_its_own():
    source = (ORCHESTRATION / "inspection.py").read_text(encoding="utf-8")
    assert re.search(r"^def _standards\b", source, re.MULTILINE) is None
    assert STANDARDS_GLOB not in source
    # Its control: the baseline did define one, and the same search sees it.
    before = repository_file_at(INSPECTION_REL, validation_file=Path(__file__),
                                bound=BASELINE)
    assert re.search(r"^def _standards\b", before, re.MULTILINE) is not None


def test_build_context_takes_both_placeholders_from_the_shared_reader(
    target_root, harness_root, monkeypatch
):
    """Behaviourally rather than by reading the source: a reader substituted
    for the shared one is what reaches the built context, for both keys."""
    sentinel = {"repository_standards": "zzz-repository-standards-sentinel",
                "testing_standards": "zzz-testing-standards-sentinel"}
    seen = []

    def substitute(root, config):
        seen.append(root)
        return dict(sentinel)

    monkeypatch.setattr(context_assembler, "standards_context", substitute)
    built = build_context_for(target_root, harness_root)
    assert seen == [target_root]
    assert built["repository_standards"] == sentinel["repository_standards"]
    assert built["testing_standards"] == sentinel["testing_standards"]


def test_build_context_and_the_shared_reader_agree_on_a_real_target(
    target_root, harness_root
):
    config = harness_config.load_config(target_root)
    built = build_context_for(target_root, harness_root)
    shared = context_assembler.standards_context(target_root, config)
    assert shared["repository_standards"] and shared["testing_standards"]
    assert built["repository_standards"] == shared["repository_standards"]
    assert built["testing_standards"] == shared["testing_standards"]


def build_context_for(target_root: Path, harness_root: Path) -> dict:
    run_dir = target_root / ".harness" / "runs" / conftest.SAMPLE_STORY_ID
    run_dir.mkdir(parents=True, exist_ok=True)
    story_text = (target_root / ".harness" / "stories"
                  / f"{conftest.SAMPLE_STORY_ID}.yaml").read_text(encoding="utf-8")
    return context_assembler.build_context(
        story_text=story_text,
        story=story_parser.parse(story_text, schema_validator.load_schema("story")),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=harness_config.load_config(target_root),
        rules=harness_config.load_rules(REPO_ROOT),
        workflow=conftest.build_workflow(conftest.workflow_stage()),
        retry_count=0,
    )


def test_the_inspectors_prompt_takes_the_standards_from_the_shared_reader(
    tmp_path, monkeypatch
):
    sentinel = "zzz-inspector-standards-sentinel"
    seen = []

    def substitute(root, config):
        seen.append((root, config.get("standards_dir")))
        return {"repository_standards": sentinel, "testing_standards": None}

    monkeypatch.setattr(context_assembler, "standards_context", substitute)
    config = {"standards_dir": ".harness/zzz-house-rules"}
    rendered = inspection._render(
        REPO_ROOT, tmp_path, config,
        inspection.Scope(path="", kind=inspection.SOURCE), (),
        filed_query.Answer(answered=False, reason="no query ran for this test"),
        tmp_path / "findings.json",
    )
    assert seen == [(tmp_path, ".harness/zzz-house-rules")]
    assert sentinel in rendered


def test_the_inspectors_prompt_carries_the_standards_the_target_declares(
    tmp_path
):
    """The unpatched route, on a target built here: what reaches the
    Inspector is what the directory holds, whole, and a target declaring
    none renders the optional-placeholder convention rather than a leftover."""
    config = {"standards_dir": ".harness/zzz-house-rules"}
    for name, text in STANDARDS.items():
        path = tmp_path / config["standards_dir"] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    answer = filed_query.Answer(answered=False, reason="no query ran for this test")
    scope = inspection.Scope(path="", kind=inspection.SOURCE)
    rendered = inspection._render(REPO_ROOT, tmp_path, config, scope, (),
                                  answer, tmp_path / "findings.json")
    assert missing_standards(rendered, STANDARDS) == set()

    bare = tmp_path / "declares-none"
    bare.mkdir()
    rendered = inspection._render(REPO_ROOT, bare, config, scope, (),
                                  answer, bare / "findings.json")
    assert missing_standards(rendered, STANDARDS) == set(STANDARDS)
    assert PLACEHOLDER.search(rendered) is None


def test_l5_plan_resolves_the_planner_standards_through_the_shared_reader():
    """The script is a subprocess, so its route is read off its source beside
    the end-to-end capture above: the shared reader is called against the
    target's root and configuration, and no glob of its own is added."""
    source = (SCRIPTS / "l5-plan").read_text(encoding="utf-8")
    assert source.count(
        "context_assembler.standards_context(target_root, config)") == 1
    assert STANDARDS_GLOB not in source
    assert "standards_dir" not in source


# --------------------------------------------------------------------------
# prompts/planner.md: the heading, the process step, the backstop
# --------------------------------------------------------------------------


def test_the_template_carries_the_heading_immediately_followed_by_the_placeholder():
    template = planner_template()
    assert f"{STANDARDS_HEADING}\n{STANDARDS_PLACEHOLDER}" in template
    assert template.count(STANDARDS_PLACEHOLDER) == 1
    # The planner is not the testing stage, and the testing file reaches it
    # inside the whole body rather than a second time on its own.
    assert TESTING_PLACEHOLDER not in template


def test_the_process_list_no_longer_instructs_a_reading_of_the_standards_directory():
    assert STANDARDS_DIRECTORY_INSTRUCTION not in process_section(planner_template())
    # Nowhere else in the template either: the standards are below, not to be
    # fetched.
    assert STANDARDS_DIRECTORY_INSTRUCTION not in planner_template()


def test_the_process_list_still_instructs_the_architecture_docs_reading():
    section = process_section(planner_template())
    assert re.search(rf"Read the architecture documents.*`{ARCHITECTURE_DOCS_KEY}`",
                     section, re.DOTALL)


def test_the_process_list_says_the_standards_are_rendered_below():
    section = process_section(planner_template())
    assert "rendered below" in section
    assert STANDARDS_HEADING.rstrip(":") in section


def test_the_baseline_template_carried_the_instruction_and_not_the_heading():
    """Control for the absences above: at this story's baseline the process
    list did instruct a reading of the standards directory, and the template
    carried no standards heading and no placeholder — so the same checks can
    go the other way, and green today is the template having changed."""
    before = baseline_planner_template()
    assert STANDARDS_DIRECTORY_INSTRUCTION in process_section(before)
    assert STANDARDS_HEADING not in before
    assert STANDARDS_PLACEHOLDER not in before
    # The reading this story leaves alone was there before it too.
    assert re.search(rf"Read the architecture documents.*`{ARCHITECTURE_DOCS_KEY}`",
                     process_section(before), re.DOTALL)


def test_the_backstop_is_asked_before_the_interview_begins():
    """The question sits above the numbered steps, since it decides whether
    the interview — one of those steps — starts at all."""
    head = before_the_numbered_steps(process_section(planner_template()))
    assert BACKSTOP_QUESTION in head
    assert re.search(BACKSTOP_FACT, head)
    assert BACKSTOP_REMEDY in head
    assert BACKSTOP_REFUSAL in head.lower()


def test_the_backstop_decides_from_the_injected_restrictions():
    """It reads whether any stage may create under the tests directory from
    the stage restrictions the workflow injects, and tells the developer
    rather than writing an artifact naming another workflow."""
    head = before_the_numbered_steps(process_section(planner_template()))
    assert re.search(r"stage\s+restrictions", head)
    assert re.search(BACKSTOP_FACT, head)
    assert "developer" in head
    assert "cannot change the workflow" in head


def test_the_baseline_template_carried_no_backstop():
    """Control: none of the backstop's phrases were in the process section
    before this story, so their presence is the story's and not the prose's."""
    before = process_section(baseline_planner_template())
    assert BACKSTOP_QUESTION not in before
    assert BACKSTOP_REMEDY not in before
    assert BACKSTOP_REFUSAL not in before.lower()


# --------------------------------------------------------------------------
# The applies_when statements carry the finishing test, and nothing else moved
# --------------------------------------------------------------------------


def test_the_refactor_statement_says_a_new_standing_claim_is_not_its_work():
    statement = raw_definition("refactor-workflow")["applies_when"].lower()
    assert "new standing claim" in statement
    assert "only place" in statement
    assert "not this workflow's work" in statement
    assert re.search(r"no stage that may write", statement)
    assert "assertion" in statement


def test_the_story_statement_claims_such_work_even_where_behaviour_is_unchanged():
    statement = raw_definition("story-workflow")["applies_when"].lower()
    assert "new standing claim" in statement
    assert "only place" in statement
    assert "even where behaviour is unchanged" in statement
    assert "assertion" in statement


@pytest.mark.parametrize("name", SHIPPED_DEFINITIONS)
def test_each_statement_extends_the_one_the_base_commit_carried(name):
    """Extended rather than rewritten: what each said before is what it still
    says first, and this is also the control for the byte-identity below —
    the two reads are of different revisions, or this would not differ."""
    before = baseline_definition(name)["applies_when"]
    after = raw_definition(name)["applies_when"]
    assert after != before
    assert after.startswith(before)
    assert isinstance(after, str)


@pytest.mark.parametrize("name", SHIPPED_DEFINITIONS)
def test_everything_but_the_statement_is_byte_identical_to_the_base_commit(name):
    before = baseline_definition(name)
    after = raw_definition(name)
    assert set(before) == set(after)
    for key in ("stages", "escalation_rules", "max_run_cost_usd",
                "max_run_cost_usd_reason", "name"):
        assert key in before
        assert after[key] == before[key], key
    assert {key for key in after if after[key] != before[key]} == {"applies_when"}


def test_the_two_statements_still_differ_and_still_load_as_candidates():
    statements = [raw_definition(name)["applies_when"] for name in SHIPPED_DEFINITIONS]
    assert len(set(statements)) == len(statements)
    for name in SHIPPED_DEFINITIONS:
        loaded = harness_config.load_workflow(REPO_ROOT, name,
                                              harness_config.load_config(REPO_ROOT))
        assert loaded["applies_when"] == raw_definition(name)["applies_when"]


# --------------------------------------------------------------------------
# What this story leaves alone
# --------------------------------------------------------------------------


def this_story_diff(paths: list[str]) -> str:
    return story_diff(paths, validation_file=Path(__file__))


def test_the_resolution_this_story_is_bounded_at_sees_what_it_changed():
    """The control the emptiness below is read against: a path this story
    edited in place, required to show in the same diff."""
    assert this_story_diff([PLANNER_REL]) != ""


def test_this_story_left_the_selector_and_inspector_prompts_alone():
    assert this_story_diff(list(UNTOUCHED_PROMPTS)) == ""


def test_the_resolution_reports_a_story_that_did_touch_those_prompts(tmp_path):
    """Taken further: the same resolution against a repository built here in
    which one story did edit those prompts reports it."""
    root = conftest.constructed_story(
        tmp_path, respected=["docs/"], violated=list(UNTOUCHED_PROMPTS))
    assert conftest.constructed_story_diff(root, ["docs/"]) == ""
    for rel in UNTOUCHED_PROMPTS:
        assert conftest.constructed_story_diff(root, [rel]) != ""
