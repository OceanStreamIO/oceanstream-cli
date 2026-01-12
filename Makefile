SHELL := /bin/zsh

# Environment
export PYTHONUTF8 := 1
export PYTHONWARNINGS := ignore
export NO_COLOR := 1

# Ensure 'src' package imports work without installing
export PYTHONPATH := $(PWD)/oceanstream:$(PYTHONPATH)

# Virtual environment awareness
VENV_DIR ?= venv
# Prefer venv python/pip if present, otherwise fall back to system
PYTHON := $(shell if [ -x "$(VENV_DIR)/bin/python" ]; then echo "$(VENV_DIR)/bin/python"; else command -v python3 || command -v python; fi)
PIP := $(shell if [ -x "$(VENV_DIR)/bin/pip" ]; then echo "$(VENV_DIR)/bin/pip"; else command -v pip3 || command -v pip; fi)

TEST_RAW_DIR := oceanstream/tests/data/raw_data
DEFAULT_RAW_DIR := raw_data
OUT_DIR := out/geoparquet

.PHONY: help venv install run-cli-sample run-cli-sample-py dry-run schema list-columns test test-unit test-integration coverage coverage-html coverage-xml coverage-check coverage-clean clean docs-serve docs-build docs-clean docs-install docs-version-deploy docs-version-list docs-version-delete docs-version-serve build publish publish-test

help:
	@echo "Available targets:"
	@echo "  venv                - Create a local virtualenv in ./$(VENV_DIR) (python -m venv)"
	@echo "  install             - Install the CLI in editable mode (pip install -e oceanstream)"
	@echo "  run-cli-sample      - Run CLI against test fixture data (uses 'oceanstream' if installed)"
	@echo "  run-cli-sample-py   - Run CLI via 'python -m oceanstream.cli' using PYTHONPATH (no install required)"
	@echo "  dry-run             - Analyze default ./raw_data (no writes)"
	@echo "  schema              - Print GeoParquet schema from test fixture data"
	@echo "  list-columns        - List columns from test fixture data"
	@echo "  test                - Run all tests"
	@echo "  test-unit           - Run unit tests only"
	@echo "  test-integration    - Run the updated integration test only"
	@echo "  coverage            - Run tests with coverage (terminal report)"
	@echo "  coverage-html       - Run tests with coverage and generate HTML report at ./coverage_html"
	@echo "  coverage-xml        - Run tests with coverage and generate coverage.xml (Cobertura)"
	@echo "  coverage-check      - Enforce fail-under threshold from .coveragerc"
	@echo "  coverage-clean      - Remove coverage artifacts (.coverage, coverage_html/)"
	@echo "  clean               - Remove generated dataset at $(OUT_DIR)"
	@echo ""
	@echo "PyPI packaging targets:"
	@echo "  build               - Build sdist and wheel packages in dist/"
	@echo "  publish             - Upload packages to PyPI (requires auth)"
	@echo "  publish-test        - Upload packages to TestPyPI (requires auth)"
	@echo ""
	@echo "Documentation targets:"
	@echo "  docs-install        - Install documentation build dependencies"
	@echo "  docs-serve          - Serve documentation locally at http://127.0.0.1:8765"
	@echo "  docs-build          - Build static documentation site"
	@echo "  docs-clean          - Remove built documentation site"
	@echo ""
	@echo "Documentation versioning targets (mike):"
	@echo "  docs-version-deploy - Deploy a new version (usage: make docs-version-deploy VERSION=1.0 ALIAS=latest)"
	@echo "  docs-version-list   - List all deployed versions"
	@echo "  docs-version-delete - Delete a version (usage: make docs-version-delete VERSION=1.0)"
	@echo "  docs-version-serve  - Serve versioned docs locally"

venv:
	@echo "[make] Creating virtualenv at ./$(VENV_DIR) if missing ..."
	@test -d $(VENV_DIR) || python3 -m venv $(VENV_DIR)
	@$(PIP) install --upgrade pip
	@echo "[make] Virtualenv ready: $(VENV_DIR)"

install:
	@echo "[make] Installing in editable mode (project root pyproject)..."
	@if [ -f "$(VENV_DIR)/bin/activate" ]; then source "$(VENV_DIR)/bin/activate"; fi; \
		$(PIP) install -e .
	@echo "[make] Done. Try: oceanstream --help"

run-cli-sample:
	@echo "[make] Running CLI against fixture data at $(TEST_RAW_DIR) ..."
	@if [ -f "$(VENV_DIR)/bin/activate" ]; then source "$(VENV_DIR)/bin/activate"; fi; \
		if command -v oceanstream >/dev/null 2>&1; then \
			oceanstream process geotrack --dry-run --input-dir $(TEST_RAW_DIR) -v; \
		else \
			$(PYTHON) -m oceanstream.cli process geotrack --dry-run --input-dir $(TEST_RAW_DIR) -v; \
		fi
	@echo "[make] Done. Output (if any) at $(OUT_DIR)"

run-cli-sample-py:
	@echo "[make] Running CLI via python -m src.cli against fixture data ..."
	@if [ -f "$(VENV_DIR)/bin/activate" ]; then source "$(VENV_DIR)/bin/activate"; fi; \
		$(PYTHON) -m oceanstream.cli process geotrack --input-dir $(TEST_RAW_DIR) -v
	@echo "[make] Done. Output (if any) at $(OUT_DIR)"

dry-run:
	@echo "[make] Dry run over $(DEFAULT_RAW_DIR) ..."
	@if [ -f "$(VENV_DIR)/bin/activate" ]; then source "$(VENV_DIR)/bin/activate"; fi; \
		if command -v oceanstream >/dev/null 2>&1; then \
			oceanstream process geotrack --dry-run --input-dir $(DEFAULT_RAW_DIR) -v; \
		else \
			$(PYTHON) -m oceanstream.cli process geotrack --dry-run --input-dir $(DEFAULT_RAW_DIR) -v; \
		fi
	@echo "[make] Dry run complete."

schema:
	@echo "[make] Printing schema from $(TEST_RAW_DIR) ..."
	@if [ -f "$(VENV_DIR)/bin/activate" ]; then source "$(VENV_DIR)/bin/activate"; fi; \
		if command -v oceanstream >/dev/null 2>&1; then \
			oceanstream process geotrack --print-schema --input-dir $(TEST_RAW_DIR) -v; \
		else \
			$(PYTHON) -m oceanstream.cli process geotrack --print-schema --input-dir $(TEST_RAW_DIR) -v; \
		fi

list-columns:
	@echo "[make] Listing columns from $(TEST_RAW_DIR) ..."
	@if [ -f "$(VENV_DIR)/bin/activate" ]; then source "$(VENV_DIR)/bin/activate"; fi; \
		if command -v oceanstream >/dev/null 2>&1; then \
			oceanstream process geotrack --list-columns --input-dir $(TEST_RAW_DIR) -v; \
		else \
			$(PYTHON) -m oceanstream.cli process geotrack --list-columns --input-dir $(TEST_RAW_DIR) -v; \
		fi

test:
	@if [ -f "$(VENV_DIR)/bin/activate" ]; then source "$(VENV_DIR)/bin/activate"; fi; \
		$(PYTHON) -m pytest -q oceanstream/tests

test-unit:
	@if [ -f "$(VENV_DIR)/bin/activate" ]; then source "$(VENV_DIR)/bin/activate"; fi; \
		$(PYTHON) -m pytest -q oceanstream/tests/unit

test-integration:
	@if [ -f "$(VENV_DIR)/bin/activate" ]; then source "$(VENV_DIR)/bin/activate"; fi; \
		$(PYTHON) -m pytest -q oceanstream/tests/integration

coverage:
	@if [ -f "$(VENV_DIR)/bin/activate" ]; then source "$(VENV_DIR)/bin/activate"; fi; \
		$(PYTHON) -m pytest --cov=oceanstream --cov-report=term-missing --cov-fail-under=0 -q

coverage-html:
	@if [ -f "$(VENV_DIR)/bin/activate" ]; then source "$(VENV_DIR)/bin/activate"; fi; \
		$(PYTHON) -m pytest --cov=oceanstream --cov-report=html:coverage_html --cov-fail-under=0 -q; \
		open coverage_html/index.html || true

coverage-xml:
	@if [ -f "$(VENV_DIR)/bin/activate" ]; then source "$(VENV_DIR)/bin/activate"; fi; \
		$(PYTHON) -m pytest --cov=oceanstream --cov-report=xml:coverage.xml --cov-fail-under=0 -q

coverage-check:
	@if [ -f "$(VENV_DIR)/bin/activate" ]; then source "$(VENV_DIR)/bin/activate"; fi; \
		$(PYTHON) -m pytest --cov=oceanstream --cov-report=term-missing -q

coverage-clean:
	rm -f .coverage
	rm -rf coverage_html

clean:
	@echo "[make] Cleaning $(OUT_DIR) ..."
	rm -rf $(OUT_DIR)
	@echo "[make] Cleaned."

# Documentation targets
docs-install:
	@echo "[make] Installing documentation dependencies..."
	@$(PIP) install -r .docs-website/requirements.txt
	@echo "[make] Documentation dependencies installed."

docs-serve: docs-install
	@echo "[make] Serving documentation at http://127.0.0.1:8765 ..."
	@echo "[make] Press Ctrl+C to stop the server"
	@cd .docs-website && ../$(PYTHON) -m mkdocs serve --dev-addr=127.0.0.1:8765

docs-build: docs-install
	@echo "[make] Building documentation site..."
	@cd .docs-website && ../$(PYTHON) -m mkdocs build
	@echo "[make] Documentation built at .docs-website/site/"

docs-clean:
	@echo "[make] Cleaning documentation build artifacts..."
	@rm -rf .docs-website/site
	@echo "[make] Documentation cleaned."

# Mintlify documentation targets
mintlify-install:
	@echo "[make] Installing Mintlify CLI..."
	@command -v npm >/dev/null 2>&1 || { echo "Error: npm is required but not installed. Install Node.js first."; exit 1; }
	@npm install -g mintlify
	@echo "[make] Mintlify CLI installed."

mintlify-dev:
	@echo "[make] Starting Mintlify development server at http://localhost:3000 ..."
	@echo "[make] Press Ctrl+C to stop the server"
	@command -v mintlify >/dev/null 2>&1 || { echo "Error: Mintlify not installed. Run 'make mintlify-install' first."; exit 1; }
	@mintlify dev

mintlify-build:
	@echo "[make] Building Mintlify static site..."
	@command -v mintlify >/dev/null 2>&1 || { echo "Error: Mintlify not installed. Run 'make mintlify-install' first."; exit 1; }
	@mintlify build
	@echo "[make] Mintlify site built at _site/"

# Documentation versioning targets (mike)
docs-version-deploy: docs-install
	@echo "[make] Deploying documentation version..."
	@if [ -z "$(VERSION)" ]; then \
		echo "Error: VERSION not specified. Usage: make docs-version-deploy VERSION=1.0 ALIAS=latest"; \
		exit 1; \
	fi; \
	cd .docs-website && \
	if [ -n "$(ALIAS)" ]; then \
		../$(PYTHON) -m mike deploy --push --update-aliases $(VERSION) $(ALIAS); \
	else \
		../$(PYTHON) -m mike deploy --push $(VERSION); \
	fi
	@echo "[make] Version $(VERSION) deployed."

docs-version-list:
	@echo "[make] Listing deployed versions..."
	@cd .docs-website && ../$(PYTHON) -m mike list

docs-version-delete:
	@echo "[make] Deleting documentation version..."
	@if [ -z "$(VERSION)" ]; then \
		echo "Error: VERSION not specified. Usage: make docs-version-delete VERSION=1.0"; \
		exit 1; \
	fi; \
	cd .docs-website && ../$(PYTHON) -m mike delete $(VERSION)
	@echo "[make] Version $(VERSION) deleted."

docs-version-serve: docs-install
	@echo "[make] Serving versioned documentation at http://127.0.0.1:8765 ..."
	@echo "[make] Press Ctrl+C to stop the server"
	@cd .docs-website && ../$(PYTHON) -m mike serve --dev-addr=127.0.0.1:8765

# PyPI packaging targets
build:
	@echo "[make] Building sdist and wheel packages..."
	@rm -rf dist/
	@$(PYTHON) -m build --sdist --wheel
	@$(PYTHON) -m twine check dist/*
	@echo "[make] Build complete. Packages in dist/"
	@ls -la dist/

publish: build
	@echo "[make] Uploading to PyPI..."
	@$(PYTHON) -m twine upload dist/*
	@echo "[make] Published to PyPI."

publish-test: build
	@echo "[make] Uploading to TestPyPI..."
	@$(PYTHON) -m twine upload --repository testpypi dist/*
	@echo "[make] Published to TestPyPI."
