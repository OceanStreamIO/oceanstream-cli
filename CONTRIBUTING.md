# Contributing

Thank you for your interest in contributing!

## Getting started
- Use Python 3.12+
- Create a virtual environment and install dev deps:
  - `pip install -r requirements.txt -r requirements-dev.txt`
  - `pip install -e oceanstream`
- Run tests: `pytest -q`
- Lint/type-check: `ruff check .` and `mypy oceanstream`
- Optional: install pre-commit hooks: `pre-commit install`

## Development workflow
1. Create a feature branch
2. Add tests for any new behavior
3. Keep changes small and focused
4. Ensure all checks pass locally
5. Open a PR with a clear description

## Code style
- Ruff enforces linting and formatting (`ruff` + `ruff-format`)
- Prefer small, pure functions and clear names
- Add docstrings for public functions and modules

## Tests
- Put unit tests under `oceanstream/tests/unit/`
- Put integration tests under `oceanstream/tests/integration/`
- Aim for meaningful assertions; avoid fragile filesystem assumptions

## Commit messages
- Use imperative mood (e.g., “Add…”, “Fix…”, “Refactor…”) 
- Reference issues when relevant (e.g., "Fixes #123")

## Releases
- Keep CHANGELOG notes in PR descriptions; maintainers will collect them

## Reporting issues
- Use the Bug report template; include reproducible steps and logs
