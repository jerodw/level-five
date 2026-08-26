# Testing Standards

- Tests live in `tests/` and run with `.venv/bin/python -m pytest tests/ -q -n auto`, which is `test_command` in `.harness/config.yaml` verbatim — the harness's own gates run that command, so a developer's invocation and a gate's are the same one. `-n auto` reads the machine's core count rather than naming a number.
- The dependencies that command needs are declared in `requirements-dev.txt`, and it must be installed into both `.venv` and `.venv310` — the interpreter a developer runs the suite in and the one `verification_runner` names. Installed into only the first, the local suite is green and the clean-clone check dies on an unrecognized argument.
- Deterministic coordinator logic (routing, state transitions, context assembly, rule enforcement) must be covered by unit tests that never invoke a model.
- Agent invocation is isolated behind `agent_runner.py` so tests can substitute a fake runner.
- A story is not complete until all existing tests pass plus the new tests written for the story.
- Tests must not weaken or skip existing assertions to pass; verification rules are immutable.
