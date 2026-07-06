# Coding Standards

- Python 3.10+, standard library only (no third-party runtime dependencies; pytest is allowed for tests).
- Modules use type hints on public functions and dataclasses for structured values.
- File and JSON artifact names use kebab-case (`verification-result.json`); Python modules use snake_case.
- Fail loudly: raise or exit non-zero on unexpected state rather than continuing in a degraded state.
- Keep functions small enough to read in one pass; prefer plain code over cleverness.
