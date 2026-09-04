"""Independent validation for the story that puts a standard on what an agent
says to a person, and makes the shared prose layer reach the agent the
developer talks to most.

Written from the story's acceptance criteria rather than from the
implementation. Three subjects, each asserted at the altitude it lives at:

  * **the layer's own text.** `prompts/prose-layer.md` is a live harness
    artifact and what this story adds to it is a fact about what this
    repository ships, so it is read as the subject rather than stood in for by
    a fixture: the widened scope sentence, the count entry beneath it carried
    across unchanged, the conversational entry's four statements, and the
    absence of any agent named by role.
  * **the worked pair.** The pair is what teaches the rule, so it is extracted
    from the layer rather than searched for by its wording: the two indented
    example blocks, the citation they share, and where in each half that
    citation falls. Leading with it in one and burying it in the other *is*
    the rule, and it is asserted as such.
  * **the reach.** `scripts/l5-assist` is driven with `execvp` intercepted, so
    what it would have handed `claude` is read rather than reasoned about, and
    `scripts/l5-plan` is driven as a subprocess with a stub `claude` on PATH,
    which is the render the planner actually receives. A placeholder in a
    template that nothing resolves is not reach, which is why neither
    assertion is made off the templates alone.

Every absence asserted here carries a demonstration that it can fail:

  * "the count entry is carried across unchanged" sits beside the same
    comparison against a copy of the layer with one word of that block
    altered, which reports it — a containment check says nothing until the
    comparison is shown to discriminate;
  * "the entry names no agent by role" sits beside the same search over a copy
    with a role planted in it and over a prompt that legitimately names roles,
    both of which report;
  * "the prompt the launcher appends carries the partial and leaves no
    placeholder unresolved" sits beside the same two checks over a launcher
    that appends the template unrendered, which fail — so a green check is the
    injection rather than the template file merely existing;
  * "the layer states each of these things" sits beside the same searches over
    a rendering with each statement removed, which report every one of them
    absent, so a search that has stopped seeing anything is told apart from a
    layer that says it;
  * "the worked pair carries both halves" sits beside the same extraction over
    a copy with each half removed in turn, which reports both.

Nothing here invokes a model.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import load_mutant, load_script

import context_assembler
import harness_config

REPO_ROOT = Path(context_assembler.__file__).resolve().parents[1]
PROMPTS = REPO_ROOT / "prompts"

#: The shared partial, named through the assembler's own constant so this
#: module and the code under test have one spelling of it between them.
PROSE_LAYER = context_assembler.PROSE_LAYER

#: The two templates this story is about, by the names their launchers load
#: them under. Neither is a stage name, a restricted prefix or an artifact
#: name, and neither is derived from a workflow: they are the files the two
#: entry points read.
ASSIST_TEMPLATE = "assist.md"
PLANNER_TEMPLATE = "planner.md"

ASSIST_LAUNCHER = "l5-assist"
PLANNER_LAUNCHER = "l5-plan"

#: The workflow a planning session is rendered against here, taken from this
#: repository's own configuration rather than written down: since story-072 an
#: invocation with no terminal and no --workflow is refused, and what this
#: module needs is *a* name a real invocation uses, not a particular one.
PLANNED_WORKFLOW = harness_config.load_config(REPO_ROOT)["workflow"]


def prose_layer() -> str:
    """The shared partial as this repository ships it."""
    return (PROMPTS / PROSE_LAYER).read_text(encoding="utf-8")


def collapsed(text: str) -> str:
    """One line, lowercased, so a phrase is found across a line break."""
    return " ".join(text.split()).lower()


# ==========================================================================
# The scope sentence, and the count entry carried across beneath it
# ==========================================================================

#: Where the count entry begins, which is also where the opening scope
#: paragraph ends. The one phrase this module locates the layer's existing
#: material by, and the layer's own words for it.
COUNT_ENTRY_OPENS = "Do not write the count"


def scope_paragraph(text: str) -> str:
    """Everything the layer says before its first entry."""
    opening = text.index(COUNT_ENTRY_OPENS)
    return text[:opening]


#: The count entry, its exception and its habit, as they stood before this
#: story — written out here rather than resolved out of this repository's
#: history. The text below is itself the statement of what "unchanged" means,
#: and a constant written in the test says it without moving under a commit, a
#: squash or a rebase: what
#: this module is about is the layer's text, not this repository's commit
#: graph. A word of it altered anywhere reddens the comparison below, which is
#: exactly what a byte-for-byte comparison against the earlier tree bought.
CARRIED_ACROSS = '''\
Do not write the count of a list you are about to write. "Four assertions:"
followed by six names, "converted 8 of 22" beside a breakdown reading 0, then
3, then 2 — in each case the number and the list said the same thing, the list
was right, and the number went stale the moment anything was added. Delete the
count and that whole class of defect disappears, because there is no longer a
second statement to disagree with the first. Say "the assertions below" and let
them be counted by whoever needs a number.

The exception is a count the adjacent content does not already carry: a
measurement, a budget, a bound, a figure from a source the reader cannot see
from here. Those are facts rather than restatements, and a reader has no other
way to get them.

When a count that matters is not adjacent to what it counts, this repository's
habit is to hold it with a test rather than with prose — an assertion that goes
red when the number and the thing it counts stop agreeing. A number nothing
checks is a number that will be wrong, and a reader has no way to tell which
one they are looking at.'''


def carried_across() -> str:
    """The count entry, its exception and its habit, as they stood before."""
    return CARRIED_ACROSS


def test_the_scope_sentence_covers_what_is_said_to_a_person_in_a_session():
    """Both halves: what is written for a person to read later, which the
    sentence already covered, and what is said to one in a session, which is
    what this story widened it to."""
    opening = collapsed(scope_paragraph(prose_layer()))

    assert "every word you write" in opening, opening
    assert "a human later reads" in opening, opening
    assert "every word you say to a person in a session" in opening, opening


def test_the_count_entry_its_exception_and_its_habit_are_carried_across():
    """Byte for byte and in the order they were in, which is what "unchanged"
    means. The control is below."""
    assert carried_across() in prose_layer()


def test_the_same_comparison_reports_a_block_with_one_word_altered():
    """The control for the equality above.

    A block recovered from the tree this story started on and found in the
    tree it is leaving could agree because the comparison has stopped
    discriminating, so the same comparison is pointed at a copy of the layer
    with one word of that block changed, and must report it.
    """
    block = carried_across()
    altered = prose_layer().replace(COUNT_ENTRY_OPENS,
                                    "Do not write the tally", 1)

    assert block not in altered


def test_the_count_entry_still_precedes_what_this_story_added_beneath_it():
    """The new material is beside the old, not interleaved with it: everything
    the earlier entry said is behind us before the conversational entry
    opens."""
    text = prose_layer()
    block = carried_across()

    assert text.index(block) + len(block) <= text.index(CONVERSATIONAL_OPENS)


# ==========================================================================
# The conversational entry, and what it has to state to be the rule
# ==========================================================================

#: Where the entry this story adds begins. Its subject is the act rather than
#: the agent, and this is the layer's own naming of that act.
CONVERSATIONAL_OPENS = "When you ask a person to decide something"

#: What the entry must state, keyed by why the story requires it. Each value
#: is the phrases that must all appear; the key is the requirement they serve.
ENTRY_STATES = {
    "the act it governs, named rather than the agent that performs it":
        ("ask a person to decide something", "explain something to them in a "
         "session"),
    "lead with the consequence rather than with the code":
        ("lead with the consequence",),
    "what a question carries to be answerable from itself":
        ("what is being decided", "what each option costs or risks",
         "what will happen by default if they have no preference"),
    "where the citation goes instead of the lead":
        ("the citation goes beneath the question", "on request",
         "it is not the lead"),
    "the first limit: precision is not traded away":
        ("do not trade away precision",),
    "the second limit: identifiers are not banned":
        ("identifiers, paths and line numbers",
         "they get the path and the line"),
}


@pytest.mark.parametrize("requirement", sorted(ENTRY_STATES),
                         ids=sorted(ENTRY_STATES))
def test_the_conversational_entry_states_what_the_rule_requires(requirement):
    said = collapsed(prose_layer())

    for phrase in ENTRY_STATES[requirement]:
        assert phrase in said, (requirement, phrase)


def test_the_same_searches_report_a_layer_with_each_statement_removed():
    """The control for the searches above.

    Every phrase is stripped out of a rendering of the layer and the same
    searches are run over it; each must report its absence. A search that has
    stopped seeing anything reports nothing whatever the layer says.
    """
    said = collapsed(prose_layer())

    for requirement, phrases in ENTRY_STATES.items():
        stripped = said
        for phrase in phrases:
            stripped = stripped.replace(phrase, "")
        for phrase in phrases:
            assert phrase not in stripped, (requirement, phrase)


# --------------------------------------------------------------------------
# No agent is named by role anywhere in the layer
# --------------------------------------------------------------------------


def templates() -> dict[str, str]:
    """Every prompt file this repository ships, by name."""
    return {path.name: path.read_text(encoding="utf-8")
            for path in sorted(PROMPTS.glob("*.md"))}


def partial_names(shipped: dict[str, str]) -> set[str]:
    """The prompt files that are injected into other prompts.

    A partial is recognised by another template carrying its placeholder,
    which is what being a partial *is*, rather than by a naming convention
    this module would be inventing. What is left is the prompts an agent is
    given, and each of those is one agent's role.
    """
    found = set()
    for name in shipped:
        placeholder = "{{" + name[: -len(".md")].replace("-", "_") + "}}"
        if any(placeholder in text for other, text in shipped.items()
               if other != name):
            found.add(name)
    return found


def role_words() -> tuple[str, ...]:
    """The role each shipped agent prompt names, derived from the prompts.

    The trailing component of the template's name, so a prompt a single
    workflow owns — named for the workflow and the role together — yields the
    role. Derived rather than listed, so an agent added later is one this
    assertion covers with no edit here.
    """
    shipped = templates()
    partials = partial_names(shipped)
    return tuple(sorted({name[: -len(".md")].split("-")[-1]
                         for name in shipped if name not in partials}))


def roles_named_in(text: str) -> set[str]:
    """Which agent roles a text identifies, as whole words."""
    return {role for role in role_words()
            if re.search(rf"\b{role}\b", text, re.IGNORECASE)}


def test_the_roles_this_module_looks_for_are_the_ones_the_harness_ships():
    """An anchor, so the absence below cannot pass against an empty list."""
    roles = role_words()

    assert roles
    assert len(roles) >= 2, roles


def test_the_layer_identifies_no_agent_by_role():
    """The entry governs the act, so a conversational agent wired to the layer
    later is governed with no edit to it — and an agent that holds no
    conversation has nothing to apply rather than a paragraph naming somebody
    else. The controls are below."""
    assert roles_named_in(prose_layer()) == set()


def test_the_same_search_reports_a_role_planted_in_a_copy_of_the_layer():
    """The first control for the absence above.

    A copy of the layer with the entry's opening rewritten to name an agent,
    which is exactly the drafting mistake the rule forbids, put through the
    same search.
    """
    for role in role_words():
        planted = prose_layer().replace(
            CONVERSATIONAL_OPENS, f"When the {role} asks a person to decide "
                                  f"something", 1)
        assert role in roles_named_in(planted), role


def test_the_same_search_reports_the_roles_a_shipped_prompt_does_name():
    """The second control: a prompt that legitimately names roles.

    A stage prompt is told which stages precede and follow it, so the search
    run over the prompts that carry the harness partial finds roles there. An
    empty result over the layer is therefore the layer's own silence rather
    than a search that has stopped seeing anything.
    """
    shipped = templates()
    named = {name: roles_named_in(text) for name, text in shipped.items()
             if name not in partial_names(shipped)}

    assert any(roles for roles in named.values()), named


# ==========================================================================
# The worked pair, extracted from the layer rather than searched for
# ==========================================================================

#: A citation as the layer writes one: a file and a line, which is what the
#: entry says belongs beneath a question rather than in front of it.
CITATION = re.compile(r"[\w./-]+\.\w+:\d+")


def example_blocks(text: str) -> list[tuple[str, str]]:
    """The layer's indented example blocks, each with the line introducing it.

    The pair is found by its shape rather than by its wording, so an example
    reworded stays found and an example deleted does not.
    """
    blocks: list[tuple[str, str]] = []
    lead = ""
    previous = ""
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("    ") and line.strip():
            if not current:
                lead = previous
            current.append(line.strip())
            continue
        if current:
            blocks.append((lead, " ".join(current)))
            current = []
        if line.strip():
            previous = line.strip()
    if current:
        blocks.append((lead, " ".join(current)))
    return blocks


def first_sentence(block: str) -> str:
    return re.split(r"(?<=[.?!])\s", block)[0]


def the_pair_holds(text: str) -> bool:
    """Whether `text` carries a worked pair: one explanation, then the other.

    Both halves have to be there, they have to be different, and the second
    has to be the *same* explanation rather than a second subject — which is
    what the shared citation establishes. Where that citation falls is the
    rule itself: leading the half written as it was given, and beneath the
    consequence in the half pitched at the decision.
    """
    blocks = example_blocks(text)
    if len(blocks) != 2:
        return False
    (given_lead, given), (pitched_lead, pitched) = blocks
    if not given_lead.endswith(":") or not pitched_lead.endswith(":"):
        return False
    if given_lead == pitched_lead or given == pitched:
        return False
    citations = CITATION.findall(given)
    if not citations:
        return False
    citation = citations[0]
    if citation not in first_sentence(given):
        return False
    if citation not in pitched:
        return False
    return citation not in first_sentence(pitched)


def carries_both_halves(prompt: str) -> bool:
    """Whether a rendered prompt carries both halves of the layer's pair.

    Asked of a prompt rather than of the layer, and by looking for the halves
    the layer holds rather than by extracting blocks from the prompt: a
    rendered prompt carries injected schema files, which are indented text
    that is not an example of anything. What matters here is that both halves
    arrived, which is a search; whether they are a *pair* is decided of the
    layer itself, above.
    """
    halves = [block for _, block in example_blocks(prose_layer())]
    if len(halves) != 2:
        return False
    return all(collapsed(half) in collapsed(prompt) for half in halves)


def without_block(text: str, index: int) -> str:
    """`text` with one of its example blocks taken out."""
    blocks = example_blocks(text)
    assert 0 <= index < len(blocks), (index, len(blocks))
    dropped = blocks[index][1]
    kept = []
    for line in text.splitlines():
        if line.startswith("    ") and line.strip() and line.strip() in dropped:
            continue
        kept.append(line)
    return "\n".join(kept)


def test_the_layer_carries_a_worked_pair_with_both_halves():
    """One explanation as it was given and the same one pitched at the
    decision, sharing a citation that leads the first and does not lead the
    second. The controls are below."""
    assert the_pair_holds(prose_layer())


@pytest.mark.parametrize("half", [0, 1], ids=["as-given", "pitched"])
def test_the_same_extraction_reports_a_layer_with_one_half_removed(half):
    """The control for the pair above.

    Each half is taken out of a copy of the layer in turn and the same
    predicate is asked again; neither copy may satisfy it, so a green pair is
    two halves present rather than an extraction that has stopped looking.
    """
    assert not the_pair_holds(without_block(prose_layer(), half))


def test_the_pair_is_the_layers_own_and_not_something_this_module_recognises():
    """The predicate is a claim about shape, so it is shown to accept a pair
    other than the shipped one — otherwise it would be a spelling of the
    shipped example rather than a rule about its form."""
    constructed = (
        "As it was given:\n"
        "\n"
        "    Refused at gate.py:12 because permitted is False.\n"
        "\n"
        "Pitched at the decision:\n"
        "\n"
        "    The run stopped, and you can let it through or move the work,\n"
        "    which costs a re-plan. (gate.py:12 decided it.)\n"
    )

    assert the_pair_holds(constructed)


# ==========================================================================
# The reach: what the assist launcher hands the session
# ==========================================================================


def launched(monkeypatch, argv: list[str], *, harness_root: Path | None = None,
             module=None) -> tuple[object, list[str]]:
    """`l5-assist` run with the exec intercepted, returning module and argv.

    The launcher's last act replaces the process, so what it decided is read by
    standing in for that call rather than by reading the source. The
    environment variable it exports is put under `monkeypatch`'s control first,
    so the launcher's own assignment to it is reverted when the test ends.

    `harness_root` repoints the launcher at a prompts tree the caller owns,
    which is how the one-file-edit assertion below drives the real launcher
    against an edited layer without editing this repository's.
    """
    recorded: dict = {}
    module = module or load_script(ASSIST_LAUNCHER, name="l5_assist_under_test")
    if harness_root is not None:
        monkeypatch.setattr(module, "HARNESS_ROOT", Path(harness_root))
    monkeypatch.setenv(module.HARNESS_ROOT_VARIABLE, "a value no launcher wrote")
    monkeypatch.setattr(module.os, "execvp",
                        lambda file, args: recorded.update(file=file,
                                                           args=list(args)))
    monkeypatch.setattr(module.sys, "argv", list(argv))
    module.main()
    assert recorded, "the launcher exec'd nothing"
    return module, recorded["args"]


def appended(args: list[str]) -> str:
    """The prompt an argument list hands to --append-system-prompt."""
    assert "--append-system-prompt" in args, args
    return args[args.index("--append-system-prompt") + 1]


def carries_the_partial(prompt: str, harness_root: Path = REPO_ROOT) -> bool:
    """Whether a prompt carries the resolved partial and resolved everything.

    The two halves of what injection means, asked as one predicate so the
    control below is the same question put to a launcher that does not
    inject.
    """
    partial = context_assembler.resolved_partial(harness_root, PROSE_LAYER, {})
    assert partial, harness_root
    return (partial in prompt
            and context_assembler.PLACEHOLDER.search(prompt) is None)


def test_the_assist_template_carries_the_placeholder_the_partial_lands_in():
    template = context_assembler.load_template(REPO_ROOT, ASSIST_TEMPLATE)

    assert "{{prose_layer}}" in template


def test_the_prompt_the_launcher_appends_carries_the_resolved_partial(
        monkeypatch):
    """Read off what the launcher would have handed `claude`, because a
    placeholder in a template that nothing resolves is not reach. The control
    is below."""
    _, args = launched(monkeypatch, [ASSIST_LAUNCHER])

    assert carries_the_partial(appended(args))


def test_a_launcher_that_reads_the_template_unrendered_fails_that_check(
        monkeypatch, tmp_path):
    """The control for the injection above.

    A copy of the launcher that appends the template it loaded instead of the
    render of it — which is what this launcher did before this story — is
    driven through the same seam, and the same predicate must refuse it. The
    check therefore reports the injection rather than the template file's mere
    existence.
    """
    raw = load_mutant(
        REPO_ROOT / "scripts" / ASSIST_LAUNCHER,
        [("assist_prompt = context_assembler.render(template, context)",
          "assist_prompt = template")],
        name="l5_assist_reading_the_template_raw", tmp_path=tmp_path)

    _, args = launched(monkeypatch, [ASSIST_LAUNCHER], harness_root=REPO_ROOT,
                       module=raw)

    prompt = appended(args)
    assert not carries_the_partial(prompt)
    assert "{{prose_layer}}" in prompt


def test_the_appended_prompt_carries_both_halves_of_the_worked_pair(
        monkeypatch):
    """What reaches the session is the whole layer, example included: the pair
    is what teaches the rule, so a partial that reached the agent without it
    would have injected the instruction and left out the demonstration."""
    _, args = launched(monkeypatch, [ASSIST_LAUNCHER])

    assert carries_both_halves(appended(args))


def test_the_assist_prompt_receives_none_of_the_harness_layer(monkeypatch):
    """The other partial's reach is untouched: the assist agent mutates no
    tree and is given no blocked-path block, before this story or after it."""
    _, args = launched(monkeypatch, [ASSIST_LAUNCHER])
    prompt = appended(args)

    assert "{{harness_layer}}" not in prompt
    assert "[Harness Layer]" not in prompt
    assert "All work must:" not in prompt


def test_the_same_searches_find_the_harness_layer_where_it_is_injected():
    """The control for the absence above.

    The harness partial's own text carries what those searches look for, so an
    empty result over the assist prompt is that prompt's reach rather than a
    search looking for something nothing spells.
    """
    partial = (PROMPTS / "harness-layer.md").read_text(encoding="utf-8")

    assert "[Harness Layer]" in partial
    assert "All work must:" in partial
    assert any("{{harness_layer}}" in text for text in templates().values())


def test_the_launcher_still_loads_the_shipped_plugin_directory(monkeypatch):
    """Loaded for the session rather than installed into the target, which is
    what makes the shipped skills available in any target."""
    module, args = launched(monkeypatch, [ASSIST_LAUNCHER])

    assert "--plugin-dir" in args, args
    assert args[args.index("--plugin-dir") + 1] == str(module.PLUGIN_DIR)
    assert Path(module.PLUGIN_DIR).is_dir(), module.PLUGIN_DIR


def test_the_launcher_still_tells_the_session_where_the_harness_is(monkeypatch):
    """A session standing in a target has no other way to find the harness it
    was started from."""
    module, _ = launched(monkeypatch, [ASSIST_LAUNCHER])

    assert os.environ[module.HARNESS_ROOT_VARIABLE] == str(module.HARNESS_ROOT)


def test_a_question_on_the_command_line_still_reaches_the_session(monkeypatch):
    _, args = launched(monkeypatch, [ASSIST_LAUNCHER, "why", "did", "it",
                                     "fail"])

    assert args[-1] == "why did it fail"


# ==========================================================================
# The reach: what the planning session is rendered with
# ==========================================================================


@pytest.fixture
def planner_prompt(tmp_path: Path) -> str:
    """The prompt `scripts/l5-plan` hands to a session, captured whole.

    Through the real script with a stub `claude` on PATH, which is the render
    the planner actually receives — the fixture shape
    `tests/test_planner_injection.py` established for the same reason.
    """
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "config.yaml").write_text(
        f"workflow: {PLANNED_WORKFLOW}\ntests_dir: tests/\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_path = tmp_path / "argv.json"
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(argv_path)!r}, 'w').write(json.dumps(sys.argv))\n",
        encoding="utf-8")
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    result = subprocess.run(
        # The session states the workflow it renders against: since story-072
        # an invocation with no terminal and no --workflow is refused rather
        # than falling back to the configured name.
        [sys.executable, str(REPO_ROOT / "scripts" / PLANNER_LAUNCHER),
         "--workflow", PLANNED_WORKFLOW, "a story request"],
        env=env, capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_path.read_text(encoding="utf-8"))
    return argv[argv.index("--append-system-prompt") + 1]


def test_the_planner_prompt_still_carries_the_resolved_partial(planner_prompt):
    """The reach this story widens the text of is the reach the planner
    already had, and it is unchanged."""
    assert carries_the_partial(planner_prompt)


def test_the_widened_text_reaches_the_planning_session_too(planner_prompt):
    """One file, so the entry written for the developer's sake reaches the
    agent that interviews them as well as the one that answers them."""
    said = collapsed(planner_prompt)

    for phrases in ENTRY_STATES.values():
        for phrase in phrases:
            assert phrase in said, phrase
    assert carries_both_halves(planner_prompt)


# --------------------------------------------------------------------------
# One file with one home: editing it changes both rendered prompts
# --------------------------------------------------------------------------

MARKER = "ONE-FILE-CONVERSATIONAL-EDIT-MARKER"


@pytest.fixture
def mirrored_prompts(tmp_path: Path) -> Path:
    """A harness root carrying copies of the prompts this story is about.

    A copy rather than this repository's own tree, because the assertion is
    about what editing the layer does and editing the shipped one is not
    something a test may do.
    """
    root = tmp_path / "harness"
    (root / "prompts").mkdir(parents=True)
    for name in (ASSIST_TEMPLATE, PLANNER_TEMPLATE, PROSE_LAYER):
        (root / "prompts" / name).write_text(
            (PROMPTS / name).read_text(encoding="utf-8"), encoding="utf-8")
    return root


def rendered_pair(monkeypatch, harness_root: Path) -> dict[str, str]:
    """Both developer-facing prompts, rendered against one prompts tree.

    The assist prompt comes from the real launcher with its exec intercepted;
    the planner's is the narrower render `l5-plan` assembles, resolved through
    the same shared helper, which is the whole point of the helper.
    """
    _, args = launched(monkeypatch, [ASSIST_LAUNCHER], harness_root=harness_root)

    context = context_assembler.schema_context(REPO_ROOT)
    context["prose_layer"] = context_assembler.resolved_partial(
        harness_root, PROSE_LAYER, context)
    planner = context_assembler.render(
        context_assembler.load_template(harness_root, PLANNER_TEMPLATE),
        context)
    return {ASSIST_TEMPLATE: appended(args), PLANNER_TEMPLATE: planner}


def test_editing_the_layer_alone_changes_both_rendered_prompts(
        monkeypatch, mirrored_prompts):
    """What makes it a partial rather than a paragraph written twice: neither
    template is touched, and both renders change."""
    before = rendered_pair(monkeypatch, mirrored_prompts)
    templates_before = {
        name: (mirrored_prompts / "prompts" / name).read_text(encoding="utf-8")
        for name in (ASSIST_TEMPLATE, PLANNER_TEMPLATE)}

    (mirrored_prompts / "prompts" / PROSE_LAYER).write_text(
        f"[Prose Layer]\n{MARKER}\n", encoding="utf-8")
    after = rendered_pair(monkeypatch, mirrored_prompts)

    for name in (ASSIST_TEMPLATE, PLANNER_TEMPLATE):
        assert MARKER in after[name], name
        assert after[name] != before[name], name
        # The templates themselves were not touched, so the change came from
        # the one file that was.
        assert (mirrored_prompts / "prompts" / name).read_text(
            encoding="utf-8") == templates_before[name], name


def test_a_prompts_tree_with_no_layer_leaves_the_prompts_without_it(
        monkeypatch, mirrored_prompts):
    """The control for the edit above: the text reaches both prompts *from*
    that file, so removing it removes the text from both.

    An absent partial resolves to None and renders as the literal None, which
    is the optional-placeholder convention every other injection follows, so
    the launcher still starts a session rather than raising.
    """
    (mirrored_prompts / "prompts" / PROSE_LAYER).unlink()

    after = rendered_pair(monkeypatch, mirrored_prompts)

    for name, prompt in after.items():
        assert COUNT_ENTRY_OPENS not in prompt, name
        assert CONVERSATIONAL_OPENS not in prompt, name
        assert not carries_both_halves(prompt), name


# ==========================================================================
# The layer stays one file with one home, and carries no placeholder of its own
# ==========================================================================


def test_the_layer_carries_no_placeholder_of_its_own():
    """What lets both launchers render it against a context narrower than a
    stage's. A placeholder here would resolve to the literal None in the two
    prompts that most need the rule. The control is below."""
    assert context_assembler.PLACEHOLDER.search(prose_layer()) is None


def test_the_same_detector_reports_the_placeholder_the_other_partial_carries():
    """The control for the absence above: an empty result means there is no
    placeholder rather than that the detector has stopped seeing them."""
    other = (PROMPTS / "harness-layer.md").read_text(encoding="utf-8")

    assert context_assembler.PLACEHOLDER.search(other) is not None


def test_the_two_launchers_resolve_the_layer_through_one_helper():
    """No second partial and no second resolution: both entry points name the
    assembler's own constant and call the shared helper, so the file this
    module reads is the file both of them inject."""
    for launcher in (ASSIST_LAUNCHER, PLANNER_LAUNCHER):
        source = (REPO_ROOT / "scripts" / launcher).read_text(encoding="utf-8")
        assert "context_assembler.resolved_partial" in source, launcher
        assert "context_assembler.PROSE_LAYER" in source, launcher
        assert PROSE_LAYER not in source, launcher
