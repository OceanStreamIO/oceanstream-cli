# Contributing

Thank you for your interest in contributing!

## Getting started
- Use Python 3.12+
- Create a virtual environment and install dev deps:
  ```bash
  python -m venv venv
  source venv/bin/activate  # On Windows: venv\Scripts\activate
  pip install -r requirements.txt -r requirements-dev.txt
  pip install -e oceanstream
  ```
- **Always activate the project venv before running any commands:**
  ```bash
  source venv/bin/activate  # On Windows: venv\Scripts\activate
  ```

## Running tests
**IMPORTANT:** Always use the project venv when running tests or performing other tasks.

```bash
# Activate venv first
source venv/bin/activate

# Run all tests using make (recommended)
make test

# Or run tests directly with pytest
./venv/bin/python -m pytest oceanstream/tests/

# Run with verbose output
./venv/bin/python -m pytest oceanstream/tests/ -v

# Run specific test file
./venv/bin/python -m pytest oceanstream/tests/unit/test_csv_reader.py

# Run with coverage
./venv/bin/python -m pytest oceanstream/tests/ --cov=oceanstream

# Show reasons for skipped tests
./venv/bin/python -m pytest oceanstream/tests/ -rs
```

## Other development commands
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
