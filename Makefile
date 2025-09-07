# Makefile — DesktopTileLauncher

SHELL := bash

.PHONY: default
default: test_unit

VENV ?= .venv
ifeq ($(OS),Windows_NT)
  PY := $(VENV)/Scripts/python.exe
else
  PY := $(VENV)/bin/python
endif
# Always drive pip via the interpreter so self‑upgrade works on Windows too
PIP := $(PY) -m pip
PYTEST := $(PY) -m pytest

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "%-14s %s\n", $$1, $$2}'

$(PY):
	python -m venv $(VENV)

venv: $(PY) ## Create a local virtualenv

install-dev: venv ## Install runtime + dev dependencies
	$(PIP) install -U pip wheel
	@if [ -f requirements.txt ]; then $(PIP) install -r requirements.txt; fi
	$(PIP) install -U pytest ruff mypy

lint: venv ## Run ruff checks
	$(PY) -m ruff check .

format: venv ## Format with ruff
	$(PY) -m ruff format .
	
format-check: venv ## Verify formatting with ruff
	$(PY) -m ruff format --check .

typecheck: venv ## Run mypy
	$(PY) -m mypy .

test: venv ## Run the full test suite (default)
	$(PYTEST) -q

# Exact unit-only filter you used successfully:
test_unit: venv ## Run unit tests only (exclude slow/integration/e2e/etc.)
	$(PYTEST) -q \
	  -m 'unit and not (integration or e2e or slow or network or gui or qt or gl or x11 or wayland or docker or gpu or perf or flaky)' \
	  -k 'not multi_window and not tray and not lazy_refresh'

clean: ## Remove caches and build artifacts
	rm -rf $(VENV) build dist .pytest_cache .ruff_cache .mypy_cache **/__pycache__
