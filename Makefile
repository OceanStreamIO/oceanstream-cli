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

.PHONY: help venv install run-cli-sample run-cli-sample-py dry-run schema list-columns test test-unit test-integration coverage coverage-html coverage-xml coverage-check coverage-clean clean

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

