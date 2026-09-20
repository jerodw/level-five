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
  the settled workflow has writing that assertion as its work.

story-172 corrected what that backstop decides from. story-171's wording asked
whether any stage *may* create under the tests directory, and asked of the
injected restrictions the answer is yes under both shipped workflows, because
neither verifier carries a restriction at all -- so the backstop fired under
neither. What actually distinguishes the workflows is a stage confined to the
tests directory, a stage whose work is validation; the story-workflow declares
one and the refactor workflow does not. The definitions stayed as story-070
declared them and the prose moved toward them, and the section "The backstop's
rule, applied to what l5-plan renders" holds that with an assertion rather
than with wording: both shipped definitions are rendered through the function
l5-plan renders them with, and the rule is applied to the result.

story-174 closed the two inputs that section still read from elsewhere. The
planner is now told the tests directory as `{{tests_dir}}`, rendered through
`config_context` beside the workflow facts, and the rule here reads it off
the same rendering it reads the restrictions from — so a rendering that names
no directory is refused whatever it carries. And the story workflow's
`applies_when` now says from its side what story-172 made the refactor one
say from the other: a stage confined to the tests directory whose work is
validation, rather than a stage that "may write" the assertion.

story-177 added a step to the same process list and a rule to the same
skeleton: before scope is written the planner sweeps for every text that
states or consumes the rule the story changes and lists what it found in the
draft, and every do_not_modify entry carries the developer's reason in a
closing parenthetical, which l5-plan refuses an artifact for lacking. The
section "story-177: the consequence sweep and the reason rule" holds both on
the prompt l5-plan hands the session, with the baseline template — which
carried neither — as the control.

story-179 made the sweep step name its places by what the target declares.
As story-177 wrote it the step listed this harness's own layout — prompts,
schemas, workflow definitions — as though every target had one; now it names
the source directories under [Workflow facts], the architecture documents
under `architecture_docs` and the tests directory under [Workflow facts], and
the planner is told the source directories as `{{source_dirs}}`, rendered
through `config_context` from the same key the inspection reads. The section
"story-179: the sweep names the places the target declares" holds the step's
wording, the placeholder on the prompt l5-plan hands the session, and the
None case, with the baseline template — which carried none of it — as the
control.

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
from conftest import BASELINE, ENDPOINT, repository_file_at, story_diff

import context_assembler
import filed_query
import harness_config
import inspection
import schema_validator
import story_coordinator
import story_parser
from story_coordinator import CONFINEMENT, CREATE_RESTRICTION, StageRestriction

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

#: The placeholder `config_context` resolves to the target's tests directory,
#: which since story-174 the planner template carries under [Workflow facts].
#: Derived nowhere, for the reason the two above are not.
TESTS_DIR_PLACEHOLDER = "{{tests_dir}}"

#: The placeholder `config_context` resolves to the target's source
#: directories, which since story-179 the planner template carries under
#: [Workflow facts] beside the one above, for the sweep step to search.
#: Derived nowhere, for the same reason.
SOURCE_DIRS_PLACEHOLDER = "{{source_dirs}}"

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
BACKSTOP_FACT = r"confines a stage[^.]*to the tests\s+directory"
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


def build_target(root: Path, standards: dict[str, str] | None,
                 source_dirs: tuple[str, ...] = ()) -> Path:
    """A target repository l5-plan can plan in, declaring the given standards
    or — with None — no standards directory at all, and declaring the given
    source directories or — left empty — no `source_dirs` key at all.

    A repository, because since story-117 planning happens in a worktree cut
    from the base. tests_dir is what the create restriction resolves to, so
    the deployed workflow's facts render against a value rather than a token.
    """
    (root / ".harness").mkdir(parents=True)
    config = f"workflow: {PLANNED_WORKFLOW}\ntests_dir: tests/\n"
    if source_dirs:
        config += "source_dirs:\n" + "".join(f"  - {d}\n" for d in source_dirs)
    (root / ".harness" / "config.yaml").write_text(config, encoding="utf-8")
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
    """It reads whether any restriction confines a stage to the tests
    directory from the stage restrictions the workflow injects, and tells the developer
    rather than writing an artifact naming another workflow."""
    head = before_the_numbered_steps(process_section(planner_template()))
    assert re.search(r"stage\s+restrictions", head)
    assert re.search(BACKSTOP_FACT, head)
    assert "developer" in head
    assert "cannot change the workflow" in head


def workflow_facts_section(template: str) -> str:
    """The text between the [Workflow facts] heading and the next bracketed
    one, or the end of the template."""
    match = re.search(r"\[Workflow facts\]\n(.*?)(?:\n\[|\Z)", template, re.DOTALL)
    assert match is not None, "the template has no [Workflow facts] section"
    return match.group(1)


def test_the_backstop_reads_the_tests_directory_from_the_workflow_facts():
    """Since story-174 the planner is told the tests directory rather than
    left to recognise it from a restriction's prefix: the [Workflow facts]
    section carries the placeholder beneath a line naming it as the target's
    tests directory, and the backstop paragraph says it reads that directory
    from there, says not to take it from a prefix, and says what None means
    — that no confinement can be recognised. The control is the baseline
    template, which carried none of this."""
    facts = workflow_facts_section(planner_template())
    assert TESTS_DIR_PLACEHOLDER in facts
    assert re.search(r"tests\s+directory", facts[: facts.index(TESTS_DIR_PLACEHOLDER)])
    assert planner_template().count(TESTS_DIR_PLACEHOLDER) == 1

    head = before_the_numbered_steps(process_section(planner_template()))
    assert re.search(r"the same section names as the target's tests\s+directory",
                     head)
    assert re.search(r"not one you recognise from the prefix", head)
    assert re.search(r"rendered as None", head)
    assert re.search(r"no confinement\s+to it can be recognised", head)

    before = baseline_planner_template()
    assert TESTS_DIR_PLACEHOLDER not in before
    assert "rendered as None" not in process_section(before)


def test_the_baseline_template_carried_no_backstop():
    """Control: none of the backstop's phrases were in the process section
    before this story, so their presence is the story's and not the prose's."""
    before = process_section(baseline_planner_template())
    assert BACKSTOP_QUESTION not in before
    assert BACKSTOP_REMEDY not in before
    assert BACKSTOP_REFUSAL not in before.lower()


# --------------------------------------------------------------------------
# story-177: the consequence sweep and the reason rule
#
# Before scope is written the planner is told to look for everywhere the rule
# the story changes is stated — since story-179 the places the target itself
# declares, held in that story's section below — and to list what it found in
# the draft the developer approves; and every
# do_not_modify entry carries the developer's reason in a closing
# parenthetical, which l5-plan refuses an artifact for lacking. Both are held
# on the prompt l5-plan actually hands the session, captured the way the
# standards are captured above, and controlled against the baseline template
# this module already reads, which carried neither.
# --------------------------------------------------------------------------


def numbered_steps(section: str) -> list[str]:
    """The process section's numbered steps, each with its number stripped,
    in the order the template gives them."""
    steps = re.split(r"^\d+\. ", section, flags=re.MULTILINE)[1:]
    assert steps, "the process section has no numbered steps"
    return [step.strip() for step in steps]


def step_index(steps: list[str], pattern: str) -> int:
    """The position of the one step matching `pattern`; a pattern matching
    none or several is an error rather than a guess."""
    found = [index for index, step in enumerate(steps)
             if re.search(pattern, step, re.DOTALL)]
    assert len(found) == 1, (pattern, found)
    return found[0]


#: How each step the sweep is placed relative to is recognised: the one that
#: reads the architecture documents and the standards, the one that asks the
#: developer questions, and the one that presents the draft. The first is
#: recognised by the reading it has instructed since before this module's
#: baseline, so the same pattern finds it in the baseline template too.
STANDARDS_STEP = r"Read the architecture documents"
QUESTIONS_STEP = r"Ask the developer any questions"
DRAFT_STEP = r"Present the draft plan"
SWEEP_STEP = r"Sweep for the consequences"

#: What the sweep step has to tell the planner to search, as the phrases a
#: reader would look for: each place by what the target declares it as. The
#: source and tests directories are named by the section the planner is told
#: them under, and the architecture documents by the configuration key the
#: earlier step already reads them under. Since story-179; before it the
#: tuple spelled this harness's own layout, held below as the words the step
#: no longer says.
SWEEP_SEARCHES = (
    r"source directories named under \[Workflow facts\]",
    rf"architecture documents named under `{ARCHITECTURE_DOCS_KEY}`",
    r"tests directory named under \[Workflow facts\]",
)

#: The places story-177's wording named and story-179 removed: a layout this
#: repository happens to have, spoken of as the target's. Bounded as whole
#: words, because the step still says "a prompt that tells a stage the rule,
#: a schema description that declares it" when it lists the kinds of text a
#: rule is written in, and those name kinds rather than places.
SWEEP_NO_LONGER_SEARCHES = (
    r"\bprompts\b",
    r"\bschemas\b",
    r"workflow\s+definitions",
)
SWEEP_LISTS_ITS_FINDINGS = r"List what the\s+sweep found in the draft the developer approves"
SWEEP_LANDS_EACH_FILE = r"`modify` or in `do_not_modify` with a reason"

#: The reason rule, as the rendered prompt has to state it.
REASON_RULE_FORM = r"do_not_modify entry is a path or area followed by the developer's\s+reason"
REASON_RULE_PARENTHETICAL = r"closing parenthetical"
REASON_RULE_REFUSAL = r"l5-plan\s+refuses an artifact whose entry carries none"

#: The skeleton's do_not_modify item, showing the form: a path or area
#: placeholder followed by a parenthetical holding a reason placeholder.
SKELETON_DO_NOT_MODIFY_ITEM = re.compile(
    r"^\t  do_not_modify:\n\t    - <path or area> \(<[^>]+reason[^>]*>\)$",
    re.MULTILINE)


def sweep_step_of(text: str) -> str:
    steps = numbered_steps(process_section(text))
    return steps[step_index(steps, SWEEP_STEP)]


def test_the_rendered_prompt_carries_the_sweep_step_before_the_draft_step(
    plan_capture, tmp_path
):
    """On what l5-plan hands the session: a numbered step telling the planner
    to sweep, after the step that reads the standards and before both the
    questions step and the step that presents the draft."""
    prompt = captured_prompt(plan_capture, build_target(tmp_path / "t", STANDARDS))
    steps = numbered_steps(process_section(prompt))
    sweep = step_index(steps, SWEEP_STEP)
    assert step_index(steps, STANDARDS_STEP) < sweep
    assert sweep < step_index(steps, QUESTIONS_STEP)
    assert sweep < step_index(steps, DRAFT_STEP)
    assert step_index(steps, QUESTIONS_STEP) < step_index(steps, DRAFT_STEP)


def test_the_sweep_step_names_every_place_to_search_and_what_to_do_with_it(
    plan_capture, tmp_path
):
    prompt = captured_prompt(plan_capture, build_target(tmp_path / "t", STANDARDS))
    sweep = sweep_step_of(prompt)
    assert re.search(r"Name the\s+fact or rule the story changes", sweep)
    assert re.search(r"states\s+or consumes it", sweep)
    for searched in SWEEP_SEARCHES:
        assert re.search(searched, sweep), searched
    assert re.search(SWEEP_LISTS_ITS_FINDINGS, sweep)
    assert re.search(SWEEP_LANDS_EACH_FILE, sweep)
    # The step is one the sweep belongs in on its own rather than a sentence
    # added to a neighbour: none of the neighbouring steps carries the sweep's
    # instruction.
    steps = numbered_steps(process_section(prompt))
    for other in (STANDARDS_STEP, QUESTIONS_STEP, DRAFT_STEP):
        assert not re.search(SWEEP_LISTS_ITS_FINDINGS,
                             steps[step_index(steps, other)]), other


def test_the_rendered_prompt_states_the_reason_rule_and_the_refusal(
    plan_capture, tmp_path
):
    prompt = captured_prompt(plan_capture, build_target(tmp_path / "t", STANDARDS))
    assert re.search(REASON_RULE_FORM, prompt)
    assert re.search(REASON_RULE_PARENTHETICAL, prompt)
    assert re.search(REASON_RULE_REFUSAL, prompt)
    # What the reason is for, said where the rule is: a later reader telling
    # a decision from a default.
    assert re.search(r"later reader", prompt)
    assert re.search(r"decision the developer made\s+from a default", prompt)
    # And the placeholders the rule sits among were all resolved, so the rule
    # reached the session as text and not as a template.
    assert PLACEHOLDER.search(prompt) is None


def test_the_rendered_skeleton_shows_the_do_not_modify_form(plan_capture, tmp_path):
    prompt = captured_prompt(plan_capture, build_target(tmp_path / "t", STANDARDS))
    assert SKELETON_DO_NOT_MODIFY_ITEM.search(prompt) is not None
    # The modify item beside it stays the bare placeholder: the reason is a
    # do_not_modify thing, and the skeleton says so by the contrast.
    assert re.search(r"^\t  modify:\n\t    - <path or area>$", prompt, re.MULTILINE)


def test_the_template_carries_the_same_text_the_session_receives():
    """The three assertions above read a render; this reads the template, so
    a regression in either the template or the rendering is caught by name."""
    template = planner_template()
    assert re.search(SWEEP_STEP, template)
    assert re.search(REASON_RULE_FORM, template)
    assert re.search(REASON_RULE_REFUSAL, template)
    assert SKELETON_DO_NOT_MODIFY_ITEM.search(template) is not None
    assert template.count("do_not_modify:") == 1


def test_the_baseline_template_carried_neither_the_sweep_nor_the_reason_rule():
    """Control for the presences above, in this module's own idiom: the
    template before it carried no sweep step, stated no reason rule, and its
    skeleton's do_not_modify item was the bare placeholder — so the same
    checks go the other way there, and green today is the text having been
    added rather than the checks matching what was always written."""
    before = baseline_planner_template()
    steps = numbered_steps(process_section(before))
    assert [index for index, step in enumerate(steps)
            if re.search(SWEEP_STEP, step)] == []
    assert not re.search(SWEEP_LISTS_ITS_FINDINGS, before)
    assert not re.search(REASON_RULE_FORM, before)
    assert not re.search(REASON_RULE_REFUSAL, before)
    assert SKELETON_DO_NOT_MODIFY_ITEM.search(before) is None
    assert re.search(r"^\t  do_not_modify:\n\t    - <path or area>$", before,
                     re.MULTILINE)
    # The steps the sweep is placed between were there, in that order, so the
    # placement above is a step inserted between existing ones.
    assert step_index(steps, STANDARDS_STEP) < step_index(steps, QUESTIONS_STEP)
    assert step_index(steps, QUESTIONS_STEP) < step_index(steps, DRAFT_STEP)


def test_the_skeleton_check_reports_a_render_with_the_parenthetical_removed(
    tmp_path
):
    """A second control that needs no history: the same skeleton check over a
    copy of the template with the reason stripped from the item, rendered
    through the same context as the real one, reports the form missing —
    so the presence above is the item carrying the form and not the pattern
    matching any do_not_modify item at all."""
    root = build_target(tmp_path / "t", STANDARDS)
    context = context_assembler.schema_context(REPO_ROOT)
    context.update(context_assembler.workflow_context(
        conftest.build_workflow(conftest.workflow_stage()),
        harness_config.load_rules(REPO_ROOT)))
    context.update(context_assembler.standards_context(
        root, harness_config.load_config(root)))
    intact = context_assembler.render(planner_template(), context)
    assert SKELETON_DO_NOT_MODIFY_ITEM.search(intact) is not None

    stripped = SKELETON_DO_NOT_MODIFY_ITEM.sub(
        "\t  do_not_modify:\n\t    - <path or area>", planner_template())
    assert stripped != planner_template()
    rendered = context_assembler.render(stripped, context)
    assert SKELETON_DO_NOT_MODIFY_ITEM.search(rendered) is None
    assert re.search(REASON_RULE_FORM, rendered), \
        "the strip took more than the skeleton item with it"


# --------------------------------------------------------------------------
# story-179: the sweep names the places the target declares
#
# The sweep step names where to search by what the target declares rather
# than by a layout this repository has: the source directories under
# [Workflow facts], the architecture documents under `architecture_docs`, and
# the tests directory under [Workflow facts]. The source directories reach
# the planner as `{{source_dirs}}`, rendered by `config_context` from the
# same key the inspection reads, beneath a paragraph naming them and saying
# what None means. Held on the prompt l5-plan hands the session for a target
# built here, and on the template; the absences — the old places no longer
# named, the baseline carrying none of this — each have a control beside
# them, one against a copy of the template with the old wording restored and
# the rest against the baseline this module already reads.
# --------------------------------------------------------------------------


#: Source directories no target of this repository configures, for rendering
#: what a target that declares them would see: two, so "one per line" is a
#: claim about the list and not about a single value.
PROBE_SOURCE_DIRS = ("zzz-src/", "zzz-lib/")

#: How the paragraph introducing the placeholder is recognised, and what it
#: has to say about the absent case.
SOURCE_DIRS_PARAGRAPH = r"target's source\s+directories"
SOURCE_DIRS_NONE_MEANS = r"whatever it tracks\s+outside the tests directory"

#: The wording story-177 gave the step's list of places, restored into a copy
#: of the template as the control for the absence assertion: the same search
#: that finds nothing in the shipped step has to find it here.
STORY_177_SWEEP_PLACES = (
    "the target's prompts, its schemas, its workflow definitions, "
    "the architecture documents named under `architecture_docs`, and "
    "the tests directory named under [Workflow facts]"
)
STORY_179_SWEEP_PLACES = re.compile(
    r"the source directories named under \[Workflow facts\],\s+"
    r"the architecture documents named under `architecture_docs`, and the\s+"
    r"tests directory named under \[Workflow facts\]")


def paragraph_and_block_beneath(section: str, paragraph: str) -> tuple[str, str]:
    """The rest of the one paragraph matching `paragraph` in a section, and
    the block that follows it: the text between the blank line that ends the
    paragraph and the next blank line, which is where the template puts a
    placeholder and so where a render puts its value. Returned as a pair so
    a caller can ask what the paragraph says without searching the section
    for a value — None, say — that other paragraphs also carry."""
    found = [m for m in re.finditer(paragraph, section)]
    assert len(found) == 1, (paragraph, len(found))
    rest = section[found[0].end():]
    assert "\n\n" in rest, "the paragraph is the last thing in the section"
    paragraph_rest, after = rest.split("\n\n", 1)
    return paragraph_rest, after.split("\n\n", 1)[0].strip()


def old_places_named_by(step: str) -> list[str]:
    """Which of story-177's places the sweep step still names, for the
    absence assertion and its control to share."""
    return [old for old in SWEEP_NO_LONGER_SEARCHES if re.search(old, step)]


def test_the_sweep_step_no_longer_names_this_harness_layout_as_the_places(
    plan_capture, tmp_path
):
    """On what l5-plan hands the session: none of prompts, schemas or
    workflow definitions is named as a place to search, while the sentence
    listing the kinds of text a rule is written in — a prompt, a schema
    description, a document, a test — is still there."""
    prompt = captured_prompt(plan_capture, build_target(tmp_path / "t", STANDARDS))
    sweep = sweep_step_of(prompt)
    assert old_places_named_by(sweep) == []
    assert re.search(r"a prompt that tells a stage the rule", sweep)
    assert re.search(r"a\s+schema description that declares it", sweep)
    assert re.search(r"a document that records it", sweep)
    assert re.search(r"a test\s+that holds it", sweep)


def test_the_old_places_check_reports_a_render_with_story_177s_wording_restored(
    tmp_path
):
    """Control for the absence above, needing no history: the same check over
    a copy of the template with story-177's list put back in the step's
    place, rendered through the same context, reports every old place — so
    an empty result on the shipped step is the wording having changed and
    not the word bounds having stopped seeing anything."""
    root = build_target(tmp_path / "t", STANDARDS)
    context = context_assembler.schema_context(REPO_ROOT)
    context.update(context_assembler.workflow_context(
        conftest.build_workflow(conftest.workflow_stage()),
        harness_config.load_rules(REPO_ROOT)))
    context.update(context_assembler.standards_context(
        root, harness_config.load_config(root)))
    context.update(context_assembler.config_context(
        harness_config.load_config(root)))
    intact = context_assembler.render(planner_template(), context)
    assert old_places_named_by(sweep_step_of(intact)) == []

    restored, count = STORY_179_SWEEP_PLACES.subn(STORY_177_SWEEP_PLACES,
                                                  planner_template())
    assert count == 1
    rendered = context_assembler.render(restored, context)
    assert old_places_named_by(sweep_step_of(rendered)) == list(
        SWEEP_NO_LONGER_SEARCHES)
    # And the restored step lost the declared name it replaced, so the two
    # wordings are alternatives and the positive assertion is not vacuous.
    assert not re.search(SWEEP_SEARCHES[0], sweep_step_of(rendered))


def test_the_captured_prompt_lists_each_declared_source_directory_on_a_line_of_its_own(
    plan_capture, tmp_path
):
    """For a target declaring source directories, the [Workflow facts]
    section of what l5-plan hands the session carries each as a dashed line
    of its own, beneath the paragraph naming them as the target's source
    directories, and nowhere in the prose above that paragraph."""
    prompt = captured_prompt(
        plan_capture, build_target(tmp_path / "t", STANDARDS, PROBE_SOURCE_DIRS))
    facts = workflow_facts_section(prompt)
    paragraph, block = paragraph_and_block_beneath(facts, SOURCE_DIRS_PARAGRAPH)
    assert dashed_items(block) == list(PROBE_SOURCE_DIRS)
    assert re.search(SOURCE_DIRS_NONE_MEANS, paragraph)
    for directory in PROBE_SOURCE_DIRS:
        assert prompt.count(directory) == 1, directory
    assert PLACEHOLDER.search(prompt) is None


def test_a_target_declaring_no_source_directories_sees_none_beneath_the_paragraph(
    plan_capture, tmp_path
):
    """The absent case, and what the paragraph says it means: None on its own
    line beneath the same paragraph, with no placeholder left behind and no
    probe directory anywhere — so the dashed lines above are the target's
    declaration and not the template's."""
    prompt = captured_prompt(plan_capture, build_target(tmp_path / "t", STANDARDS))
    facts = workflow_facts_section(prompt)
    paragraph, block = paragraph_and_block_beneath(facts, SOURCE_DIRS_PARAGRAPH)
    assert block == "None"
    assert re.search(r"renders as None", paragraph)
    assert re.search(SOURCE_DIRS_NONE_MEANS, paragraph)
    for directory in PROBE_SOURCE_DIRS:
        assert directory not in prompt
    assert PLACEHOLDER.search(prompt) is None


def test_the_template_carries_the_source_dirs_placeholder_once_beneath_its_paragraph():
    """The template side of the two renders above: the placeholder sits under
    [Workflow facts], exactly once, as the block beneath the paragraph naming
    the source directories, and that paragraph says what None means. The
    sweep step points at the same section by name."""
    template = planner_template()
    assert template.count(SOURCE_DIRS_PLACEHOLDER) == 1
    facts = workflow_facts_section(template)
    paragraph, block = paragraph_and_block_beneath(facts, SOURCE_DIRS_PARAGRAPH)
    assert block == SOURCE_DIRS_PLACEHOLDER
    assert re.search(SOURCE_DIRS_NONE_MEANS, paragraph)
    assert re.search(SWEEP_SEARCHES[0], sweep_step_of(template))
    # The tests directory placeholder it sits beside is still there, once,
    # and before it: story-179 added a paragraph and moved none.
    assert template.count(TESTS_DIR_PLACEHOLDER) == 1
    assert facts.index(TESTS_DIR_PLACEHOLDER) < facts.index(SOURCE_DIRS_PLACEHOLDER)


def test_the_baseline_template_carried_none_of_the_source_directories_text():
    """Control for the presences above: the template at this module's
    baseline carried no source_dirs placeholder, no paragraph naming the
    source directories, and no step naming them as a place to search — so
    the same checks go the other way there, and green today is the text
    having been added."""
    before = baseline_planner_template()
    assert SOURCE_DIRS_PLACEHOLDER not in before
    assert re.search(SOURCE_DIRS_PARAGRAPH, before) is None
    assert re.search(SOURCE_DIRS_NONE_MEANS, before) is None
    assert re.search(SWEEP_SEARCHES[0], before) is None
    # Its [Workflow facts] section is there to be searched, so the absences
    # are of the text and not of the section.
    assert workflow_facts_section(before)


def test_config_context_renders_the_source_directories_as_dashed_lines():
    """The assembler side: the key is rendered through the shared helper,
    None when undeclared or empty, and the keys it rendered before this
    story render exactly as before beside it."""
    declared = context_assembler.config_context(
        {"source_dirs": list(PROBE_SOURCE_DIRS)})
    assert declared["source_dirs"] == context_assembler._dashed_lines(
        list(PROBE_SOURCE_DIRS))
    assert dashed_items(declared["source_dirs"]) == list(PROBE_SOURCE_DIRS)
    assert context_assembler.config_context({})["source_dirs"] is None
    assert context_assembler.config_context({"source_dirs": []})["source_dirs"] is None


def test_config_context_still_renders_every_key_it_rendered_before_story_179():
    """The keys the assembler rendered before — the granted tools as dashed
    lines and the tests directory as the bare value — render unchanged, and
    the new key joined them rather than replacing one."""
    grants = ["Bash(grep:*)", "Bash(git show:*)"]
    config = {"allowed_tools": grants, "tests_dir": PROBE_TESTS_DIR,
              "source_dirs": list(PROBE_SOURCE_DIRS)}
    rendered = context_assembler.config_context(config)
    assert rendered["allowed_tools"] == context_assembler._dashed_lines(grants)
    assert rendered["tests_dir"] == PROBE_TESTS_DIR
    assert set(rendered) == {"allowed_tools", "tests_dir", "source_dirs"}
    empty = context_assembler.config_context({})
    assert empty == {"allowed_tools": None, "tests_dir": None, "source_dirs": None}


def test_rendered_for_this_repository_the_source_directories_are_its_own():
    """Where this harness is the target, nothing the step said before is lost:
    the rendering l5-plan makes from this repository's configuration lists
    every directory that configuration declares under source_dirs, in its
    order — which is where prompts/, schemas/ and workflows/ now come from.
    The subject is what this harness ships, so its configuration is read."""
    declared = conftest.repository_config()["source_dirs"]
    assert declared, "this repository declares no source_dirs"
    rendered = rendered_facts("story-workflow")
    assert dashed_items(rendered["source_dirs"]) == list(declared)
    # The control: a configuration declaring none renders None there while
    # still carrying restrictions, so the list above came from the key.
    absent = rendered_facts("story-workflow", {
        key: value for key, value in conftest.repository_config().items()
        if key != "source_dirs"})
    assert absent["source_dirs"] is None
    assert absent["stage_path_restrictions"] is not None


# --------------------------------------------------------------------------
# The backstop's rule, applied to what l5-plan renders
#
# The backstop is a prompt rule read off facts the assembler injects, so the
# only way to hold that it decides correctly for the shipped workflows is to
# render each through the function l5-plan renders them with and apply the
# rule mechanically to the rendered lines. Nothing here spells a stage name,
# a prefix or a sense: the rendered lines are read back into facts through
# the wording `StageRestriction.wording` composes, the tests directory is
# read off the rendering's own `tests_dir` — the line the planner is told it
# by since story-174 — and the two rules below are the backstop's own
# question and the question it replaced. A rendering for another target's
# configuration loads the shipped definition against that configuration, so
# it is the rendering that target gets and not this repository's declarations
# beside another directory.
# --------------------------------------------------------------------------


#: The two senses a rendered restriction line can carry, in the coordinator's
#: vocabulary. The reader below tries them in this order.
SENSES = (CREATE_RESTRICTION, CONFINEMENT)


def sense_phrase(sense: str) -> str:
    """The words a rendered line carries for one sense, taken from the wording
    the coordinator composes rather than restated: a probe restriction with an
    empty stage and prefix leaves exactly the phrase between them."""
    return StageRestriction(stage="", prefix="", sense=sense, prefixes=()).wording.strip()


def rendered_facts(workflow: str | dict, config: dict | None = None) -> dict:
    """What l5-plan hands the planner about a workflow, rendered the same way:
    `context_assembler.workflow_context` against the shipped rules, with the
    target's own facts merged over it through `config_context` — since
    story-174 the planner is told the tests directory as `tests_dir` rather
    than left to recognise it from a restriction's prefix.

    A shipped definition is passed by name and loaded here, through
    `harness_config.load_workflow` against the same configuration the facts
    are rendered from, so its `{{tests_dir}}` declarations resolve to what
    that configuration names — the rendering a target with that
    configuration actually gets, not this repository's declarations beside
    another target's directory. A definition built in a test is passed as
    the dict it was built as; its prefixes are literals and resolve against
    nothing. The configuration defaults to this repository's."""
    config = conftest.repository_config() if config is None else config
    definition = (harness_config.load_workflow(REPO_ROOT, workflow, config)
                  if isinstance(workflow, str) else workflow)
    rendered = context_assembler.workflow_context(
        definition, harness_config.load_rules(REPO_ROOT))
    rendered.update(context_assembler.config_context(config))
    return rendered


#: A directory no target of this repository configures, for rendering what a
#: target that declares it would see.
PROBE_TESTS_DIR = "zzz-spec/"


def configuration_without_a_tests_directory() -> dict:
    """This repository's configuration with its `tests_dir` removed: a
    target declaring everything else and no tests directory, so a rendering
    from it still carries restrictions and names no directory."""
    return {key: value for key, value in conftest.repository_config().items()
            if key != "tests_dir"}


def dashed_items(rendered: str | None) -> list[str]:
    """The items of a rendered dash-prefixed list; None renders nothing."""
    items = []
    for line in (rendered or "").splitlines():
        assert line.startswith("- "), line
        items.append(line[2:])
    return items


def restriction_facts(rendered: dict) -> list[tuple[str, str, str]]:
    """The rendered stage_path_restrictions read back into (stage, sense,
    prefix) facts — what a reader of the prompt can know about who may write
    where, and nothing the definition itself was consulted for."""
    facts = []
    for item in dashed_items(rendered["stage_path_restrictions"]):
        for sense in SENSES:
            phrase = f" {sense_phrase(sense)} "
            if phrase in item:
                stage, prefix = item.split(phrase, 1)
                facts.append((stage, sense, prefix))
                break
        else:
            raise AssertionError(f"no sense wording in the rendered line {item!r}")
    return facts


def backstop_refuses(rendered: dict) -> bool:
    """The backstop's rule as prompts/planner.md states it, applied to the
    rendered facts: where the finished work will assert something new, refuse
    unless some restriction confines a stage to the tests directory — the
    directory the rendering itself names as `tests_dir`, which is the only
    place the planner is told it. A rendering naming none (the value is None)
    admits no confinement to it, so the rule refuses whatever restrictions
    the rendering carries."""
    tests_dir = rendered["tests_dir"]
    if tests_dir is None:
        return True
    return not any(sense == CONFINEMENT and prefix == tests_dir
                   for _, sense, prefix in restriction_facts(rendered))


def some_stage_may_create_under(rendered: dict) -> bool:
    """The rule story-171 wrote and story-172 replaced: may any stage of the
    workflow create files under the tests directory? Decided with the
    coordinator's own `governs` predicate over the rendered facts, so a stage
    is free there exactly when no restriction of its own governs that path —
    and a stage the rendering names under no restriction at all is free. The
    directory is read off the rendering, as the backstop's own rule reads it."""
    tests_dir = rendered["tests_dir"]
    facts = restriction_facts(rendered)
    restrictions = [
        StageRestriction(stage, prefix, sense, tuple(
            other_prefix for other_stage, other_sense, other_prefix in facts
            if other_stage == stage and other_sense == sense))
        for stage, sense, prefix in facts
    ]
    return any(
        not any(restriction.stage == stage and restriction.governs(tests_dir)
                for restriction in restrictions)
        for stage in dashed_items(rendered["workflow_stages"])
    )


def configured_tests_dir() -> str:
    """The value the shipped declarations' `{{tests_dir}}` resolves to, from
    this repository's own configuration, as tests/test_refactor_workflow.py
    reads it."""
    return conftest.repository_config()["tests_dir"]


def shipped(name: str) -> dict:
    return conftest.shipped_workflow(REPO_ROOT, name)


def test_the_reader_recovers_the_facts_the_coordinator_derives():
    """Control for every reading below: for each shipped definition, the facts
    read back off the rendered lines are the restrictions the coordinator
    derived from the definition, one for one and in order — so a rule
    applied to the rendering is applied to what the definition declares."""
    for name in SHIPPED_DEFINITIONS:
        workflow = shipped(name)
        derived = [(r.stage, r.sense, r.prefix)
                   for r in story_coordinator.stage_restrictions(workflow["stages"])]
        assert derived, name
        assert restriction_facts(rendered_facts(name)) == derived
        assert dashed_items(rendered_facts(name)["workflow_stages"]) == [
            stage["name"] for stage in workflow["stages"]]


def test_the_backstop_refuses_the_refactor_workflow_and_admits_the_story_workflow():
    """The assertion the brief named as missing: the refusal for the refactor
    workflow and the interview for story-workflow, held by applying the rule
    to what l5-plan renders rather than by the wording of either statement.
    Both of the rule's inputs — the restrictions and the tests directory —
    are read off the same rendering, so this is decided from what the planner
    sees and nothing read off the configuration beside it."""
    assert backstop_refuses(rendered_facts("refactor-workflow"))
    assert not backstop_refuses(rendered_facts("story-workflow"))


def test_the_rendering_names_the_tests_directory_the_declarations_resolved_against():
    """Control for the comparison inside the rule: the `tests_dir` the
    rendering carries is this repository's configured one, and it is the
    prefix the story workflow's confinement resolved to — so an equality
    between the two is a directory named twice from one configuration, not
    a value that could not have differed."""
    rendered = rendered_facts("story-workflow")
    assert rendered["tests_dir"] == configured_tests_dir()
    assert rendered["tests_dir"] in {
        prefix for _, sense, prefix in restriction_facts(rendered)
        if sense == CONFINEMENT}
    # And both move together: rendered for a target configuring another
    # directory, the declarations resolve to that directory and the rendering
    # names it, so the confinement is still to what the rendering names.
    elsewhere = rendered_facts("story-workflow", {"tests_dir": PROBE_TESTS_DIR})
    assert elsewhere["tests_dir"] == PROBE_TESTS_DIR
    assert elsewhere["tests_dir"] != configured_tests_dir()
    assert {prefix for _, sense, prefix in restriction_facts(elsewhere)
            if sense == CONFINEMENT} == {PROBE_TESTS_DIR}
    assert configured_tests_dir() not in {
        prefix for _, _, prefix in restriction_facts(elsewhere)}


def test_under_another_directory_the_shipped_workflows_are_decided_the_same_way():
    """What a target declaring another tests directory gets: its rendering
    of each shipped definition carries that directory both as `tests_dir`
    and as the prefix the story workflow's confinement resolved to, so the
    rule admits story-workflow and refuses refactor-workflow there exactly
    as it does here. The directory is one nothing in this repository
    configures, so an admission is the two resolving together and not this
    repository's directory read twice."""
    assert PROBE_TESTS_DIR != configured_tests_dir()
    assert not backstop_refuses(
        rendered_facts("story-workflow", {"tests_dir": PROBE_TESTS_DIR}))
    assert backstop_refuses(
        rendered_facts("refactor-workflow", {"tests_dir": PROBE_TESTS_DIR}))


def test_a_rendering_naming_no_tests_directory_is_refused_whatever_it_carries():
    """The absent case the template states: rendered for a target that
    declares no tests directory, `tests_dir` is None and the backstop refuses
    — for the story workflow, which the rule admits when the directory is
    named, and for the refactor workflow, whose restrictions the rule already
    refuses. The configuration is this repository's less its `tests_dir`, so
    the rendering still carries restrictions and the refusal is the absent
    directory and not an empty rendering. The story-workflow half is the
    control: the same definition is admitted a line above and refused here,
    so the refusal is the absent directory and not the restrictions."""
    for name in SHIPPED_DEFINITIONS:
        absent = rendered_facts(name, configuration_without_a_tests_directory())
        assert absent["tests_dir"] is None, name
        assert absent["stage_path_restrictions"] is not None, name
        assert backstop_refuses(absent), name
    assert not backstop_refuses(rendered_facts("story-workflow"))


def test_the_distinguishing_fact_is_a_confinement_to_the_tests_directory():
    """What the rule read: story-workflow's rendering carries a confinement
    to the tests directory the rendering names, and the refactor workflow's
    carries restrictions — it is not empty — and no such confinement among
    them."""
    confinements_to_tests = {}
    for name in SHIPPED_DEFINITIONS:
        rendered = rendered_facts(name)
        confinements_to_tests[name] = [
            stage for stage, sense, prefix in restriction_facts(rendered)
            if sense == CONFINEMENT and prefix == rendered["tests_dir"]]
    assert len(confinements_to_tests["story-workflow"]) == 1
    assert confinements_to_tests["refactor-workflow"] == []
    assert restriction_facts(rendered_facts("refactor-workflow")) != []


def built_without_a_validation_stage() -> dict:
    """The case the old rule got wrong: a definition whose first stage carries
    no create restriction and none of whose stages is confined to the tests
    directory. One stage is confined elsewhere and one is unrestricted, so the
    rendering is not empty and the second stage is free to create anywhere."""
    return conftest.build_workflow(
        conftest.workflow_stage(),
        conftest.workflow_stage(may_only_change=["docs/zzz-elsewhere/"]),
        conftest.workflow_stage(),
    )


def test_a_built_definition_with_no_stage_confined_to_the_tests_directory_is_refused():
    """Control: the rule reads a confinement and not a create permission. The
    same definition with one stage confined to the tests directory added is
    admitted, so the refusal is the absence of that confinement and nothing
    else about the rendering."""
    tests_dir = configured_tests_dir()
    refused = built_without_a_validation_stage()
    assert backstop_refuses(rendered_facts(refused))

    admitted = conftest.build_workflow(
        *refused["stages"], conftest.workflow_stage(may_only_change=[tests_dir]))
    assert not backstop_refuses(rendered_facts(admitted))
    # The mismatch control, on a definition whose prefix is a literal and so
    # resolves against nothing: rendered for a target naming another
    # directory, the confinement still says the directory it was built with
    # while the rendering names the other, and the rule refuses — it compares
    # against what the rendering names and not against what the test built.
    # A shipped definition is not this control: its declarations resolve to
    # whatever directory it is rendered for, and the rule admits it there.
    assert backstop_refuses(rendered_facts(admitted, {"tests_dir": PROBE_TESTS_DIR}))


def test_the_old_rule_answers_yes_for_both_shipped_workflows():
    """Why the rule changed, held rather than remembered: asked whether any
    stage may create under the tests directory, the rendered facts of both
    shipped workflows say yes, because neither verifier is restricted — so a
    backstop deciding on that question fired under neither workflow."""
    for name in SHIPPED_DEFINITIONS:
        assert some_stage_may_create_under(rendered_facts(name)), name


def test_the_old_rule_can_answer_no_and_still_disagrees_with_the_new_one():
    """Control for the yes above: the old rule is not stuck on yes. Over a
    definition whose every stage is either kept out of the tests directory or
    confined elsewhere it answers no, while over the definition the old rule
    got wrong — no validation stage, a stage free to create anywhere — it
    answers yes where the backstop's rule refuses."""
    tests_dir = configured_tests_dir()
    closed = conftest.build_workflow(
        conftest.workflow_stage(may_not_create=[tests_dir]),
        conftest.workflow_stage(may_only_change=["docs/zzz-elsewhere/"]),
    )
    assert not some_stage_may_create_under(rendered_facts(closed))

    wrong_case = rendered_facts(built_without_a_validation_stage())
    assert some_stage_may_create_under(wrong_case)
    assert backstop_refuses(wrong_case)


# --------------------------------------------------------------------------
# The applies_when statements carry the finishing test, and nothing else moved
# --------------------------------------------------------------------------


def test_the_refactor_statement_says_a_new_standing_claim_is_not_its_work():
    statement = raw_definition("refactor-workflow")["applies_when"].lower()
    assert "new standing claim" in statement
    assert "only place" in statement
    assert "not this workflow's work" in statement
    assert re.search(r"no stage whose work is to write", statement)
    assert "written by the stage whose own change it validates" in statement
    assert "assertion" in statement


def test_the_story_statement_claims_such_work_even_where_behaviour_is_unchanged():
    statement = raw_definition("story-workflow")["applies_when"].lower()
    assert "new standing claim" in statement
    assert "only place" in statement
    assert "even where behaviour is unchanged" in statement
    assert "assertion" in statement


def test_the_story_statement_says_from_its_side_what_the_refactor_statement_says():
    """The sibling of the refactor assertion above, holding the story
    statement to the same vocabulary since story-174: a stage confined to
    the tests directory, whose work is validation, so the assertion is
    written by a stage other than the one whose change it validates. Not the
    permission story-172 removed from the refactor statement — that a stage
    "may write" the assertion — which read beside its sibling implied the
    refactor workflow had no stage that may write one."""
    statement = raw_definition("story-workflow")["applies_when"].lower()
    assert re.search(r"declares a stage confined to the tests directory", statement)
    assert "whose work is validation" in statement
    assert ("written by a stage other than the one whose change it validates"
            in statement)
    assert "may write" not in statement
    # The control for that absence: the revision story-172 started from
    # carried the permission, and the same search finds it there.
    started = json.loads(definition_where_story_172_started("story-workflow"))
    assert "may write" in started["applies_when"].lower()
    assert "confined to the tests directory" not in started["applies_when"].lower()


def test_the_two_statements_name_the_confinement_from_opposite_sides():
    """Read side by side: both say the assertion's author is decided by
    whether a stage confined to the tests directory exists, the refactor
    statement that it has no such stage and the story statement that it
    declares one — the one distinction the backstop decides from."""
    refactor = raw_definition("refactor-workflow")["applies_when"].lower()
    story = raw_definition("story-workflow")["applies_when"].lower()
    assert "written by the stage whose own change it validates" in refactor
    assert "written by a stage other than the one whose change it validates" in story
    for statement in (refactor, story):
        assert "may write" not in statement


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


def definition_where_story_172_started(name: str) -> str:
    """A shipped definition's text at the commit story-172 started from.

    This module's own range is story-171's, and its ENDPOINT — the commit that
    added this module, which is the commit story-171 finished with — is
    exactly where story-172 began. Read through the shared resolution rather
    than as `HEAD`, which would go vacuously green the moment this story
    commits.
    """
    return repository_file_at(f"workflows/{name}.json",
                              validation_file=Path(__file__), bound=ENDPOINT)


def test_since_story_172_started_only_the_two_statements_changed():
    """The definitions stand as story-070 declared them: each shipped
    definition differs from where story-172 started in applies_when and in
    no other key. story-172 reworded the refactor statement; story-174
    reworded the story statement to match it, so the byte-for-byte pin that
    held story-workflow.json to that revision is rebounded here to the same
    claim its sibling was already held to."""
    for name in SHIPPED_DEFINITIONS:
        started = json.loads(definition_where_story_172_started(name))
        after = raw_definition(name)
        assert set(started) == set(after), name
        assert {key for key in after if after[key] != started[key]} == {
            "applies_when"}, name

    started = json.loads(definition_where_story_172_started("refactor-workflow"))
    after = raw_definition("refactor-workflow")
    assert "no stage that may write" in started["applies_when"]
    assert "no stage that may write" not in after["applies_when"]


def test_the_start_of_story_172_is_neither_the_baseline_nor_the_working_tree():
    """Control for the identity above: the revision it reads is a third one.
    It differs from this module's BASELINE, where story-171 had not yet
    written the finishing test into either statement, and it differs from the
    working tree on the one key each of story-172 and story-174 changed — so
    the equality above on every other key is two revisions agreeing, not one
    revision read twice."""
    for name in SHIPPED_DEFINITIONS:
        started = json.loads(definition_where_story_172_started(name))
        assert started["applies_when"] != baseline_definition(name)["applies_when"]
        assert started["applies_when"] != raw_definition(name)["applies_when"]


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
