"""Independent validation for story-046: the test location comes from
configuration.

The harness used to write a target repository's test layout into its own
definitions — `workflows/story-workflow.json` declared the implementer's create
restriction as the literal `tests/`, and `prompts/tester.md` told every tester
that new tests belong there. A target keeping its tests anywhere else was
governed in the wrong place, and a target with no test directory at all could
not be expressed.

Written from the story's acceptance criteria rather than from the
implementation, and almost entirely by *exercising* the configured location
rather than by reading the code that resolves it. The recurring fixture
configures a target's tests at `xyzzy-spec/`, a location no harness would guess
and this repository does not contain, so a check observed to fire there can
only have learned it from the configuration.

Four altitudes:

  * **the resolution.** `harness_config.load_workflow` is driven directly with
    configs that set the key, omit it, and answer nothing, and what comes back
    is read through `story_coordinator.stage_restrictions` rather than out of
    the definition file.
  * **the refusal.** A workflow carrying a reference the config cannot answer
    is run through the real `story_coordinator.run_story`, and what the refusal
    *left behind* is read off the tree.
  * **the enforcement.** The ownership check, the stage baseline, the revert
    check and the grant validation are each exercised against the configured
    location by running the coordinator, with a real suite under `xyzzy-spec/`
    for the two that need one.
  * **the prompt.** The tester prompt is rendered against two different configs
    with `prompts/tester.md` itself unedited between the renderings.

Every absence asserted here carries a demonstration that it can fail:

  * "an unset key leaves no create restriction" sits beside the same load with
    the key set, which reports the pair;
  * "no resolved restriction is ever the empty string" sits beside a mutant
    resolver that substitutes an empty entry instead of dropping it, which the
    same check reports — and beside the demonstration that an empty prefix
    would govern every path;
  * "the refused run created no run directory, no state file, no log, no branch
    and invoked no agent" sits beside the same fixture whose workflow carries a
    resolvable reference, where the same five observations find all five;
  * "creating under the configured location escalates" is paired with the same
    record naming `tests/` — the location the harness used to assume — which
    does *not* escalate, so the governance is shown to have moved rather than
    merely to exist;
  * "the stage baseline holds the configured location" sits beside the same
    baseline directory asserted to hold nothing under `tests/`;
  * "a grant outside the configured location is refused" sits beside grants at
    and beneath it, which are accepted;
  * "the rendered tester prompt carries no unresolved placeholder" sits beside
    the placeholder the prompt does carry, resolved to the configured value and
    to a different one;
  * "every committed story artifact passes plan-time validation" sits beside a
    copy of one of them carrying a planted defect, which the same validation
    reports.

Nothing here invokes a model: every run goes through the fake runner below and
every suite that runs is a handful of local files.
"""
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
from conftest import commit_setup, load_mutant

import context_assembler
import harness_config
import plan_validation
import schema_validator
import story_coordinator
import story_parser
from agent_runner import AgentResult

REPO_ROOT = Path(harness_config.__file__).resolve().parents[1]
STORIES_DIR = REPO_ROOT / ".harness" / "stories"
WORKFLOW_NAME = "story-workflow"
STORY_ID = "story-001"

#: The configured location every fixture below uses. Deliberately a name this
#: repository does not contain and no harness would guess: a check observed
#: firing here cannot have learned the location from anywhere but the config.
CONFIGURED = "xyzzy-spec/"

#: The location the harness used to assume, kept as the paired control. A
#: record naming this one under a target configured at CONFIGURED must go
#: *un*governed, or "the restriction moved" would be indistinguishable from
#: "the restriction is enforced in both places".
ASSUMED = "tests/"

#: A reference no configuration answers. `branch_prefix` is a real, declared
#: config key, so this is the strongest form of the unanswerable case: the
#: story rejected a general mechanism, and a declared key that is nonetheless
#: not referable is what "narrow" means.
UNANSWERABLE = "branch_prefix"

PASS_VERDICT = {"status": "passed", "blocking_issues": [], "unverified": [],
                "retry_recommended": False}
EMPTY_RECORD = {"modified": [], "created": [], "deleted": []}

#: A runner that exists everywhere this suite runs, so a control run's
#: clean-clone check resolves it.
WORKING_RUNNER = "/bin/echo"


# --------------------------------------------------------------------------
# Loading the shipped definition against a config of this module's choosing
# --------------------------------------------------------------------------


def config_with(tests_dir: str | None) -> dict:
    """This repository's config with its test location set, or removed.

    Built off the real config rather than from a literal so the load below
    differs from a real one in exactly the key under test.
    """
    config = dict(conftest.repository_config())
    if tests_dir is None:
        config.pop("tests_dir", None)
    else:
        config["tests_dir"] = tests_dir
    return config


def restrictions_under(tests_dir: str | None,
                       harness_root: Path = REPO_ROOT) -> list[tuple[str, str]]:
    """What the coordinator would enforce for a target configured this way.

    Read through `story_coordinator.stage_restrictions`, which is the one
    derivation every reader of a loaded workflow goes through, rather than out
    of the definition file — where the value is not written at all.
    """
    workflow = harness_config.load_workflow(harness_root, WORKFLOW_NAME,
                                            config_with(tests_dir))
    return story_coordinator.stage_restrictions(workflow["stages"])


DEFINITION_TEXT = (REPO_ROOT / "workflows" / f"{WORKFLOW_NAME}.json").read_text(
    encoding="utf-8")


# --------------------------------------------------------------------------
# 1. The token resolves out of configuration
# --------------------------------------------------------------------------


def test_a_configured_location_is_the_restriction_the_coordinator_enforces():
    """The definition names no directory, so the pair can only have come from
    the configuration."""
    assert restrictions_under(CONFIGURED) == [("implementer", CONFIGURED)]
    assert CONFIGURED not in DEFINITION_TEXT
    assert "{{tests_dir}}" in DEFINITION_TEXT


def test_a_different_configured_location_moves_the_restriction_with_it():
    """The same definition, a different config, a different restriction — with
    nothing on disk changed between the two loads."""
    assert restrictions_under("spec/") == [("implementer", "spec/")]
    assert restrictions_under("__tests__/") == [("implementer", "__tests__/")]


def test_a_target_declaring_no_test_location_carries_no_create_restriction():
    """The absence, with its control in the same test: the identical load with
    the key set reports the pair, so "no pair" is the unset key and not a
    `stage_restrictions` that stopped seeing anything."""
    assert restrictions_under(None) == []
    assert restrictions_under(CONFIGURED) != []


def test_the_unset_key_removes_the_entry_rather_than_emptying_the_list_item():
    """`may_not_create` resolves to an empty list, not to a list holding an
    empty string. Read off the loaded stage itself, because that is what every
    reader of the definition other than `stage_restrictions` looks at."""
    workflow = harness_config.load_workflow(REPO_ROOT, WORKFLOW_NAME,
                                            config_with(None))
    implementer = next(s for s in workflow["stages"] if s["name"] == "implementer")
    assert implementer.get("may_not_create", []) == []
    assert "may_not_create" not in implementer or implementer["may_not_create"] == []


@pytest.mark.parametrize("tests_dir", [CONFIGURED, "spec/", None])
def test_no_resolved_restriction_is_ever_the_empty_string(tests_dir):
    assert "" not in [prefix for _, prefix in restrictions_under(tests_dir)]


def test_a_resolver_that_emptied_the_entry_is_reported_by_the_same_check(tmp_path):
    """The control for the absence above.

    The failure it guards against is not hypothetical: an unset key resolving
    to `""` leaves a restriction whose prefix every path in the repository
    starts with. A mutant resolver that substitutes the empty entry instead of
    dropping it is loaded here, the same definition is loaded through it with
    the key unset, and the same check reports the empty prefix — and the
    prefix is shown to match a path it has no business matching.
    """
    mutant = load_mutant(
        REPO_ROOT / "orchestration" / "harness_config.py",
        [("            elif values[name]:\n                resolved.append(values[name])",
          "            else:\n                resolved.append(values[name] or \"\")")],
        name="harness_config_emptying_the_entry", tmp_path=tmp_path)

    workflow = mutant.load_workflow(REPO_ROOT, WORKFLOW_NAME, config_with(None))
    prefixes = [p for _, p in story_coordinator.stage_restrictions(workflow["stages"])]

    assert "" in prefixes, prefixes
    # And that is what makes it wrong rather than merely odd.
    assert "src/app.py".startswith("")


def test_the_narrow_set_does_not_admit_an_arbitrary_declared_config_key(tmp_path):
    """The story rejected a general mechanism. `branch_prefix` is a declared,
    set config key and is still not referable, which is what makes the
    resolution narrow rather than general."""
    harness = mirror_harness(tmp_path, referencing(UNANSWERABLE))
    config = config_with(CONFIGURED)
    assert config.get(UNANSWERABLE)  # the key really is set

    with pytest.raises(harness_config.UnresolvedWorkflowToken) as raised:
        harness_config.load_workflow(harness, WORKFLOW_NAME, config)

    assert raised.value.workflow == WORKFLOW_NAME
    assert raised.value.tokens == [UNANSWERABLE]
    assert any(UNANSWERABLE in problem for problem in raised.value.problems)


# --------------------------------------------------------------------------
# 2. A harness root carrying a doctored definition
# --------------------------------------------------------------------------


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def raw_definition() -> dict:
    """The shipped definition with its references *unresolved*.

    Read as JSON rather than through `load_workflow`, because what these
    fixtures need is the declaration as it ships — token and all — to write
    back out into a harness root of their own.
    """
    return json.loads(DEFINITION_TEXT)


def referencing(key: str) -> dict:
    """The shipped definition with the implementer's restriction pointed at
    another configuration key."""
    definition = raw_definition()
    for stage in definition["stages"]:
        if stage["name"] == "implementer":
            stage["may_not_create"] = ["{{%s}}" % key]
    return definition


def mirror_harness(tmp_path: Path, definition: dict) -> Path:
    """A harness root identical to this one but for its workflow definition."""
    fake = tmp_path / "harness"
    (fake / "workflows").mkdir(parents=True)
    for shared in ("prompts", "schemas", "rules"):
        (fake / shared).symlink_to(REPO_ROOT / shared)
    write_json(fake / "workflows" / f"{WORKFLOW_NAME}.json", definition)
    return fake


# --------------------------------------------------------------------------
# 3. The fixture target, configured somewhere the harness would never guess
# --------------------------------------------------------------------------


def set_config(target_root: Path, **overrides) -> None:
    """Rewrite the target's config keys, adding those it does not carry.

    Committed, because story-021's clean-tree pre-flight refuses a run whose
    target tree holds work no stage produced, and a test's own configuration is
    part of the repository the run starts *from*.
    """
    path = target_root / ".harness" / "config.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    for key, value in overrides.items():
        rendered = f"{key}: {value}"
        for index, line in enumerate(lines):
            if line.startswith(f"{key}:"):
                lines[index] = rendered
                break
        else:
            lines.append(rendered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    commit_setup(target_root, "configure the target for this test")


def drop_config(target_root: Path, *keys: str) -> None:
    path = target_root / ".harness" / "config.yaml"
    kept = [line for line in path.read_text(encoding="utf-8").splitlines()
            if not any(line.startswith(f"{key}:") for key in keys)]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    commit_setup(target_root, "remove config keys for this test")


@pytest.fixture
def elsewhere(target_root: Path) -> Path:
    """The shared target, configuring its tests at a location no harness would
    guess, and with a resolvable verification runner so a control run
    completes."""
    set_config(target_root, tests_dir=CONFIGURED,
               verification_runner=WORKING_RUNNER)
    return target_root


class Runner:
    """A fake agent runner whose per-stage changed-files record is the input.

    It records every stage it was asked to run, which is how "no agent was
    invoked" becomes a fact about the coordinator rather than the absence of a
    log nobody wrote.
    """

    def __init__(self, target_root: Path, story_id: str = STORY_ID, *,
                 records: dict[str, dict] | None = None):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.records = records or {}
        self.calls: list[str] = []

    def _record(self, stage: str) -> dict:
        return self.records.get(stage, dict(EMPTY_RECORD))

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None):
        self.calls.append(stage)
        self.prompts = getattr(self, "prompts", {})
        self.prompts[stage] = prompt
        if log_path is not None:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(f"===== stage: {stage} =====\n")
        if stage == "implementer":
            write_json(self.run_dir / "changed-files.json", self._record(stage))
            (self.run_dir / "implementation-summary.md").write_text(
                "Did it.\n", encoding="utf-8")
        elif stage == "tester":
            write_json(self.run_dir / "test-results.json", {
                "status": "passed", "tests_written": 1, "tests_run": 1,
                "tests_passed": 1, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / "tester-changed-files.json",
                       self._record(stage))
        elif stage == "documenter":
            (self.run_dir / "documentation-report.md").write_text(
                "Nothing.\n", encoding="utf-8")
            write_json(self.run_dir / "documenter-changed-files.json",
                       self._record(stage))
        elif stage == "verifier":
            write_json(self.run_dir / "verification-result.json", PASS_VERDICT)
        return AgentResult(ok=True, result_text=f"{stage} done")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


def branches(root: Path) -> set[str]:
    return set(git(root, "branch", "--format=%(refname:short)").split())


def evidence(target_root: Path, story_id: str = STORY_ID) -> tuple[str, str]:
    run_dir = target_root / ".harness" / "runs" / story_id
    return ((run_dir / "events.log").read_text(encoding="utf-8"),
            (run_dir / "escalation-summary.md").read_text(encoding="utf-8"))


def run(target_root: Path, harness_root: Path, **kwargs):
    runner = Runner(target_root, **kwargs)
    code = story_coordinator.run_story(STORY_ID, harness_root, target_root,
                                       runner)
    return code, runner, runner.run_dir


# --------------------------------------------------------------------------
# 4. The pre-flight refusal
# --------------------------------------------------------------------------


def test_a_workflow_referencing_configuration_the_config_cannot_answer_is_refused(
    elsewhere, tmp_path, capsys,
):
    harness = mirror_harness(tmp_path, referencing(UNANSWERABLE))

    code, _, _ = run(elsewhere, harness)

    refusal = capsys.readouterr().err
    assert code == 1
    assert f"{{{{{UNANSWERABLE}}}}}" in refusal, refusal
    assert WORKFLOW_NAME in refusal, refusal


def test_that_refusal_leaves_no_run_directory_no_state_no_log_no_branch_no_agent(
    elsewhere, tmp_path,
):
    """Read off the refused target's tree, as the story asks, rather than off
    the exit status. Its control is the next test, which makes the same five
    observations of the same fixture under a definition that resolves."""
    harness = mirror_harness(tmp_path, referencing(UNANSWERABLE))
    before = branches(elsewhere)

    code, runner, run_dir = run(elsewhere, harness)

    assert code == 1
    assert not run_dir.exists()
    assert not (run_dir / "state.json").exists()
    assert not (elsewhere / ".harness" / "logs" / f"{STORY_ID}.log").exists()
    assert branches(elsewhere) == before
    assert runner.calls == []


def test_the_same_fixture_under_a_resolvable_definition_creates_all_five(
    elsewhere, tmp_path,
):
    """The control the five absences above need: the identical mirror carrying
    the shipped definition, whose one reference this config does answer."""
    harness = mirror_harness(tmp_path, raw_definition())
    before = branches(elsewhere)

    code, runner, run_dir = run(elsewhere, harness)

    assert code == 0, runner.calls
    assert run_dir.is_dir()
    assert json.loads((run_dir / "state.json").read_text(
        encoding="utf-8"))["status"] == "completed"
    assert (elsewhere / ".harness" / "logs" / f"{STORY_ID}.log").is_file()
    assert branches(elsewhere) - before == {f"story/{STORY_ID}"}
    assert runner.calls == ["implementer", "tester", "documenter", "verifier"]


# --------------------------------------------------------------------------
# 5. The ownership check governs the configured location
# --------------------------------------------------------------------------


def test_a_stage_creating_under_the_configured_location_escalates(elsewhere,
                                                                  harness_root):
    created = f"{CONFIGURED}test_new.py"
    code, runner, _ = run(elsewhere, harness_root, records={
        "implementer": {"modified": [], "created": [created], "deleted": []},
    })

    assert code == 2
    assert runner.calls == ["implementer"]
    events, summary = evidence(elsewhere)
    for text in (events, summary):
        assert "implementer" in text
        assert created in text
        assert CONFIGURED in text


def test_the_same_stage_creating_under_the_location_the_harness_used_to_assume_does_not(
    elsewhere, harness_root,
):
    """The control for the escalation above, and the whole point of the story:
    under a target configured at `xyzzy-spec/`, `tests/` is an ordinary
    directory. If both escalated, the restriction would not have *moved*."""
    code, runner, _ = run(elsewhere, harness_root, records={
        "implementer": {"modified": [], "created": [f"{ASSUMED}test_new.py"],
                        "deleted": []},
    })

    assert code == 0, runner.calls
    assert runner.calls == ["implementer", "tester", "documenter", "verifier"]


def test_a_target_declaring_no_test_location_governs_nothing(elsewhere,
                                                             harness_root):
    """The absence at run time: with the key removed the implementer may create
    under both locations. Its control is the two tests above, which are the
    same fixture and the same records with the key set."""
    drop_config(elsewhere, "tests_dir")

    code, runner, _ = run(elsewhere, harness_root, records={
        "implementer": {"modified": [],
                        "created": [f"{CONFIGURED}test_new.py",
                                    f"{ASSUMED}test_new.py"],
                        "deleted": []},
    })

    assert code == 0, runner.calls
    assert runner.calls == ["implementer", "tester", "documenter", "verifier"]


# --------------------------------------------------------------------------
# 6. The grant validation reads the configured location
# --------------------------------------------------------------------------


def stages_at(tests_dir: str | None) -> list[dict]:
    return harness_config.load_workflow(REPO_ROOT, WORKFLOW_NAME,
                                        config_with(tests_dir))["stages"]


def granting(create: str) -> dict:
    return {"stage_exceptions": [
        {"stage": "implementer", "create": create,
         "reason": "the deliverable is the suite"}]}


@pytest.mark.parametrize("granted", [
    CONFIGURED,
    f"{CONFIGURED}test_the_thing.py",
    f"{CONFIGURED}unit/",
], ids=["the whole location", "a file beneath it", "a directory beneath it"])
def test_a_grant_at_or_beneath_the_configured_location_is_accepted(granted):
    assert story_coordinator.stage_exception_problems(
        granting(granted), stages_at(CONFIGURED)) == []


@pytest.mark.parametrize("granted", [ASSUMED, "src/helpers.py"],
                         ids=["the assumed location", "somewhere else entirely"])
def test_a_grant_outside_the_configured_location_is_refused(granted):
    """The control for the acceptances above sits in the same pair of tests:
    the identical call shape, differing only in whether the granted path falls
    under what the configuration declared."""
    problems = story_coordinator.stage_exception_problems(
        granting(granted), stages_at(CONFIGURED))
    assert len(problems) == 1, problems
    assert granted in problems[0]


def test_under_an_unset_location_no_grant_is_accepted_at_all():
    """A stage restricted from nothing was never restricted from creating the
    granted path, so the grant means nothing and is refused — the same answer
    the check has always given for an ungoverned grant."""
    problems = story_coordinator.stage_exception_problems(
        granting(CONFIGURED), stages_at(None))
    assert len(problems) == 1, problems
    assert CONFIGURED in problems[0]


# --------------------------------------------------------------------------
# 7. The stage baseline and the revert check, over a real suite
# --------------------------------------------------------------------------

TEST_COMMAND = shlex.join([sys.executable, "-m", "pytest", CONFIGURED.rstrip("/"),
                           "-q", "-p", "no:cacheprovider"])

APP_AT_HEAD = '''\
def greet(name):
    return f"hello, {name}"
'''

APP_RENAMED = '''\
def salute(name):
    return f"hello, {name}"
'''

SPEC_AT_HEAD = '''\
from app import greet


def test_greet():
    assert greet("world") == "hello, world"
'''

SPEC_REPAIRED = '''\
from app import salute


def test_greet():
    assert salute("world") == "hello, world"
'''

ROOT_CONFTEST = '''\
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
'''


@pytest.fixture
def suite_target(tmp_path: Path) -> Path:
    """A target whose tests live at the configured location and really run.

    Everything the coordinator's revert check needs is here — a module, a suite
    over it, and a configured command that runs that suite — with the suite
    sitting at `xyzzy-spec/` rather than at the name the harness used to
    assume.
    """
    root = tmp_path / "elsewhere-target"
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml", f"""\
workflow: {WORKFLOW_NAME}
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: {TEST_COMMAND}
tests_dir: {CONFIGURED}
""")
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", conftest.STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "conftest.py", ROOT_CONFTEST)
    write(root / "src" / "app.py", APP_AT_HEAD)
    write(root / CONFIGURED / "test_app.py", SPEC_AT_HEAD)
    write(root / ".gitignore", ".pytest_cache/\n__pycache__/\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root,
                   check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    return root


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class RenamingRunner(Runner):
    """An implementer whose rename forces the edit to the suite beside it.

    The repair under the configured location is exactly the shape the revert
    check exists to permit: revert it and the suite stops compiling, so the
    edit was maintenance the change forced rather than validation the
    implementer authored.
    """

    def __call__(self, prompt, *, stage, **kwargs):
        if stage == "implementer":
            write(self.target_root / "src" / "app.py", APP_RENAMED)
            write(self.target_root / CONFIGURED / "test_app.py", SPEC_REPAIRED)
            self.records["implementer"] = {
                "modified": ["src/app.py", f"{CONFIGURED}test_app.py"],
                "created": [], "deleted": [],
            }
        return super().__call__(prompt, stage=stage, **kwargs)


def implementer_declaration() -> dict:
    stage = next(s for s in stages_at(CONFIGURED) if s["name"] == "implementer")
    return stage["revert_check"]


def test_the_stage_baseline_is_captured_over_the_configured_location(suite_target,
                                                                     harness_root):
    """What the tree held under the *configured* prefix before the stage ran.

    Asserted as an exact set rather than a containment, so it says both what
    the capture followed and what it did not sweep in: exactly the one file at
    the configured location, and nothing from `src/` — which the same stage
    modified in the same record and which is not governed. The paired control
    is `test_the_same_target_with_no_configured_location_reverts_nothing`,
    where the identical run with the one config line removed captures nothing.
    """
    runner = RenamingRunner(suite_target)
    code = story_coordinator.run_story(STORY_ID, harness_root, suite_target,
                                       runner)
    assert code == 0, runner.calls

    declaration = implementer_declaration()
    baseline = story_coordinator.stage_baseline_dir(
        runner.run_dir, declaration["baseline"], "implementer")
    captured = sorted(str(p.relative_to(baseline))
                      for p in baseline.rglob("*") if p.is_file())

    assert captured == [f"{CONFIGURED}test_app.py"]
    # And what it holds is what the tree held *before* the stage, not after.
    assert (baseline / CONFIGURED / "test_app.py").read_text(
        encoding="utf-8") == SPEC_AT_HEAD


def test_an_edit_under_the_configured_location_reaches_the_revert_check(
    suite_target, harness_root,
):
    """The check ran, it reverted the path at the configured location, and it
    decided on the suite that actually ran there."""
    runner = RenamingRunner(suite_target)
    code = story_coordinator.run_story(STORY_ID, harness_root, suite_target,
                                       runner)
    assert code == 0, runner.calls

    result = json.loads(
        (runner.run_dir / implementer_declaration()["result"]).read_text(
            encoding="utf-8"))
    schema_validator.validate(result, schema_validator.load_schema(
        "revert-check-result"))

    assert result["ran"] is True, result
    assert result["paths"] == [f"{CONFIGURED}test_app.py"], result
    assert result["permitted"] is True, result
    # The source edit is not governed, so the check is shown to have narrowed
    # to the configured location rather than reverting the whole record.
    assert "src/app.py" not in result["paths"]


def test_the_same_target_with_no_configured_location_reverts_nothing(
    suite_target, harness_root,
):
    """The control for both assertions above: remove the one config line and
    the identical run captures an empty baseline and reverts nothing, because
    the implementer is no longer governed anywhere."""
    drop_config(suite_target, "tests_dir")

    runner = RenamingRunner(suite_target)
    code = story_coordinator.run_story(STORY_ID, harness_root, suite_target,
                                       runner)
    assert code == 0, runner.calls

    declaration = implementer_declaration()
    baseline = story_coordinator.stage_baseline_dir(
        runner.run_dir, declaration["baseline"], "implementer")
    assert [p for p in baseline.rglob("*") if p.is_file()] == []

    # With nothing governed there is nothing to revert, so the check has
    # nothing to decide and writes no result at all — where the same run with
    # the key set writes one naming the path at the configured location.
    assert not (runner.run_dir / declaration["result"]).exists()


# --------------------------------------------------------------------------
# 8. The rendered tester prompt
# --------------------------------------------------------------------------

TESTER_PROMPT = REPO_ROOT / "prompts" / "tester.md"


def rendered_tester_prompt(target_root: Path, config: dict) -> str:
    story_text = (target_root / ".harness" / "stories"
                  / f"{STORY_ID}.yaml").read_text(encoding="utf-8")
    run_dir = target_root / ".harness" / "runs" / STORY_ID
    run_dir.mkdir(parents=True, exist_ok=True)
    context = context_assembler.build_context(
        story_text=story_text,
        story=story_parser.parse(story_text,
                                 schema_validator.load_schema("story")),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=REPO_ROOT,
        config=config,
        rules=harness_config.load_rules(REPO_ROOT),
        workflow=harness_config.load_workflow(REPO_ROOT, WORKFLOW_NAME, config),
        retry_count=0,
    )
    return context_assembler.render(
        context_assembler.load_template(REPO_ROOT, "tester.md"), context)


def test_the_rendered_tester_prompt_names_the_configured_location(target_root):
    config = {**harness_config.load_config(target_root), "tests_dir": CONFIGURED}
    rendered = rendered_tester_prompt(target_root, config)

    assert CONFIGURED in rendered
    assert "{{" not in rendered
    # The template really did carry a placeholder here, so the value in the
    # rendering is an injection and not prose that happens to match.
    assert "{{tests_dir}}" in TESTER_PROMPT.read_text(encoding="utf-8")


def test_changing_the_configured_location_changes_the_rendered_prompt(target_root):
    """With `prompts/tester.md` unedited between the two renderings — asserted
    by reading the file's bytes before and after, so "no prompt edit" is
    observed rather than assumed."""
    base = harness_config.load_config(target_root)
    before = TESTER_PROMPT.read_bytes()

    # Two locations neither of which is a substring of the other, so "the
    # other one is absent" is a real observation rather than an accident of
    # spelling.
    one, other = "xyzzy-spec/", "plugh-probes/"
    first = rendered_tester_prompt(target_root, {**base, "tests_dir": one})
    second = rendered_tester_prompt(target_root, {**base, "tests_dir": other})

    assert TESTER_PROMPT.read_bytes() == before
    assert first != second
    assert one in first and one not in second
    assert other in second and other not in first


def test_a_target_declaring_no_test_location_renders_the_optional_placeholder(
    target_root,
):
    """The optional-placeholder convention: nothing to inject renders as None
    rather than as an empty string or a leftover token."""
    config = {k: v for k, v in harness_config.load_config(target_root).items()
              if k != "tests_dir"}
    rendered = rendered_tester_prompt(target_root, config)

    assert "{{" not in rendered
    assert "New tests belong in None" in rendered


#: What the two replaced sentences said, and the check each one is caught by.
#: Written here so the control below can put them back rather than argue that
#: the absence above is meaningful.
REPLACED_PROSE = (
    "New tests belong in tests/ and become permanent repository assets.",
    "Use the shared resolution in `tests/conftest.py`.",
)

#: A target layout name and a test-framework filename, neither of which a
#: harness may state on a target's behalf.
FORBIDDEN_IN_PROMPTS = ("tests/", "conftest.py", "pytest")


def forbidden_in(text: str) -> list[str]:
    return [name for name in FORBIDDEN_IN_PROMPTS if name in text]


def test_the_tester_prompt_names_no_directory_and_no_framework_filename():
    """The prose the story replaced, gone from the template it was in."""
    assert forbidden_in(TESTER_PROMPT.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("prose", REPLACED_PROSE)
def test_the_same_check_reports_the_replaced_prose_put_back(prose):
    """The control for the absence above: each replaced sentence appended to a
    rendering of the same template is reported by the same check, so the empty
    list is the prose being gone rather than the check having stopped reading
    anything."""
    restored = TESTER_PROMPT.read_text(encoding="utf-8") + "\n" + prose + "\n"
    assert forbidden_in(restored) != []


def test_no_prompt_in_the_repository_names_either():
    """The criterion is stated over `prompts/`, not over the tester prompt
    alone. Its control is the two tests above, which run the same check over
    the same kind of text and report."""
    offending = {path.name: forbidden_in(path.read_text(encoding="utf-8"))
                 for path in sorted((REPO_ROOT / "prompts").glob("*.md"))}
    assert {name: found for name, found in offending.items() if found} == {}
    assert len(offending) >= 5, offending


# --------------------------------------------------------------------------
# 9. Every committed story artifact validates exactly as it did before
#
# Three of plan-time validation's checks read the restriction this story moved
# into configuration: the stage-exception cross-check, the strictness check and
# the assignment check. They are what is run here, over *every* committed
# artifact rather than a sample.
#
# The fourth thing `artifact_problems` does — `read_story`'s schema pass — is
# deliberately not asserted clean. Plan-time validation has only ever run on
# the artifacts a planning session *adds*, and this repository's earliest
# stories predate fields the story schema has since required, so they do not
# pass it today and did not pass it before this story either. Asserting they do
# would be asserting something this story neither caused nor could fix, and the
# comparison below is the honest form of "unchanged": the same artifacts,
# through the same three checks, give the identical answer whether the prefix
# arrives as a resolved token or as the literal the definition used to carry.
# --------------------------------------------------------------------------


def committed_artifacts() -> list[Path]:
    return sorted(STORIES_DIR.glob("*.yaml"))


def restriction_problems(artifact: Path, stages: list[dict]) -> list[str]:
    """The three plan-time checks that read `stage_restrictions`, for one
    artifact. Nothing here names a stage or a prefix; both come off `stages`."""
    reading = story_coordinator.read_story(
        artifact.read_text(encoding="utf-8"))
    return (
        story_coordinator.stage_exception_problems(reading.parsed, stages)
        + plan_validation.strictness_problems(reading.parsed, stages)
        + plan_validation.assignment_problems(reading.parsed, stages, REPO_ROOT)
    )


def literal_stages(prefix: str) -> list[dict]:
    """The definition as it read before this story: the prefix spelled out.

    Built from today's shipped declaration with the token replaced by the
    literal, so the comparison is against the pre-story shape reconstructed
    from what ships rather than recovered from history — which keeps it honest
    once this story commits.
    """
    definition = raw_definition()
    for stage in definition["stages"]:
        if "may_not_create" in stage:
            stage["may_not_create"] = [prefix]
    return definition["stages"]


def test_every_committed_story_artifact_validates_exactly_as_it_did_before():
    """All 46 of them, not a sample — including every one whose scope,
    do_not_modify or stage_exceptions names `tests/`."""
    artifacts = committed_artifacts()
    assert len(artifacts) > 40, len(artifacts)
    location = conftest.repository_config()["tests_dir"]

    resolved = {a: restriction_problems(a, stages_at(location))
                for a in artifacts}
    literal = {a: restriction_problems(a, literal_stages(location))
               for a in artifacts}

    assert resolved == literal


def test_that_comparison_reports_a_difference_when_there_is_one():
    """The control for the equality above, which is an absence of difference:
    resolve the token somewhere else and the same comparison goes red, so the
    equality is the restriction being unchanged rather than the comparison
    having stopped looking at anything."""
    artifacts = committed_artifacts()
    location = conftest.repository_config()["tests_dir"]

    resolved = {a: restriction_problems(a, stages_at(location))
                for a in artifacts}
    elsewhere = {a: restriction_problems(a, stages_at(CONFIGURED))
                 for a in artifacts}

    assert resolved != elsewhere


def test_every_committed_grant_is_still_accepted_against_the_resolved_prefix():
    """The criterion's own words: the artifacts whose `stage_exceptions` name
    `tests/` are accepted with the value resolved rather than literal.

    The grants are counted rather than assumed, so "all accepted" cannot be
    true of an empty set.
    """
    location = conftest.repository_config()["tests_dir"]
    stages = stages_at(location)
    granting_artifacts, refused = [], {}
    for artifact in committed_artifacts():
        reading = story_coordinator.read_story(
            artifact.read_text(encoding="utf-8"))
        if not reading.parsed.get("stage_exceptions"):
            continue
        granting_artifacts.append(artifact)
        problems = story_coordinator.stage_exception_problems(
            reading.parsed, stages)
        if problems:
            refused[artifact.name] = problems

    assert len(granting_artifacts) >= 5, [a.name for a in granting_artifacts]
    assert refused == {}


def test_the_same_check_refuses_a_planted_grant(tmp_path):
    """The control for the empty mapping above.

    A copy of a real artifact is given a grant naming a path outside the
    configured location, and the same check reports it — so "no grant refused"
    is the grants being sound rather than `stage_exception_problems` having
    stopped looking.
    """
    source = committed_artifacts()[-1]
    planted = tmp_path / source.name
    planted.write_text(
        source.read_text(encoding="utf-8")
        + "\nstage_exceptions:\n"
          "  - stage: implementer\n"
          "    create: src/somewhere-else.py\n"
          "    reason: it is not under the configured location\n",
        encoding="utf-8")

    reading = story_coordinator.read_story(
        planted.read_text(encoding="utf-8"))
    problems = story_coordinator.stage_exception_problems(
        reading.parsed, stages_at(conftest.repository_config()["tests_dir"]))

    assert len(problems) == 1, problems
    assert "src/somewhere-else.py" in problems[0]


# --------------------------------------------------------------------------
# 10. This repository declares its own location, and is unchanged by it
# --------------------------------------------------------------------------


def test_the_key_is_declared_and_the_reader_returns_it():
    assert "tests_dir" in harness_config.declared_config_keys()
    schema = json.loads(
        (REPO_ROOT / "schemas" / "harness-config.schema.json").read_text(
            encoding="utf-8"))
    description = schema["properties"]["tests_dir"]["description"]
    # It says what a set value governs and what its absence means.
    assert "unset" in description.lower()


def test_this_repository_declares_the_directory_the_workflow_used_to_name():
    location = conftest.repository_config()["tests_dir"]
    assert location == ASSUMED
    assert (REPO_ROOT / location).is_dir()
    assert story_coordinator.stage_restrictions(
        conftest.shipped_workflow()["stages"]) == [("implementer", location)]


def test_a_newly_initialized_target_declares_a_location_too():
    template = (REPO_ROOT / "templates" / "config.yaml").read_text(
        encoding="utf-8")
    declared = [line for line in template.splitlines()
                if line.startswith("tests_dir:")]
    assert declared, template
    assert declared[0].split(":", 1)[1].strip()
