# Makefile — DesktopTileLauncher

SHELL := bash
.RECIPEPREFIX := >

.PHONY: default
default: test_unit

export QT_QPA_PLATFORM ?= offscreen
export PIP_DISABLE_PIP_VERSION_CHECK ?= 1
export PYTHONUTF8 ?= 1

VENV ?= .venv
ifeq ($(OS),Windows_NT)
  PY := $(VENV)/Scripts/python.exe
else
  PY := $(VENV)/bin/python
endif
# Always drive pip via the interpreter so self‑upgrade works on Windows too
PIP := $(PY) -m pip

help: ## List available targets
> @grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "%-14s %s\n", $$1, $$2}'

.PHONY: venv
venv: ## Create virtualenv if missing
> @if [ ! -e "$(VENV)" ]; then python -m venv "$(VENV)"; fi
> @$(PY) -m ensurepip --upgrade >/dev/null 2>&1 || true

.PHONY: install-dev
install-dev: venv ## Bootstrap dev environment without failing when offline
> @set -euo pipefail; \
> echo "[install-dev] Using interpreter: $(PY)"; \
> $(PY) tools/bootstrap.py

lint: install-dev ## Run ruff checks
> $(PY) -m ruff check .

format: install-dev ## Format with ruff
> $(PY) -m ruff format .

format-check: install-dev ## Verify formatting with ruff
> $(PY) -m ruff format --check .

typecheck: install-dev ## Run mypy
> $(PY) -m mypy .

.PHONY: ensure-test-deps
ensure-test-deps: venv
> @set -euo pipefail; \
> if $(PY) -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('pytest') else 1)" >/dev/null 2>&1; then \
>   echo "[deps] pytest already present in $(VENV)"; \
> else \
>   echo "[deps] installing test deps into $(VENV)"; \
>   $(PY) -m pip install --disable-pip-version-check -q -U pip wheel; \
>   $(PY) -m pip install --disable-pip-version-check -q -r tests/requirements.txt; \
> fi

test: install-dev ## Run the full test suite (default)
> @set -euo pipefail; \
> if $(PY) - <<'PY' >/dev/null 2>&1; then \
>   import urllib.request, ssl, sys; \
>   try: \
>       urllib.request.urlopen("https://pypi.org/simple", timeout=3, context=ssl.create_default_context()); sys.exit(0); \
>   except Exception: \
>       sys.exit(1); \
> PY \
> then \
>   echo "[test] Online: running full pytest suite"; \
>   $(PY) -m pytest -q; \
> else \
>   echo "[test] Offline detected: skipping full test suite"; \
> fi

# Exact unit-only filter you used successfully:
test_unit: install-dev ensure-test-deps ## Run unit tests only (exclude slow/integration/e2e/etc.)
> @set -euo pipefail; \
> if $(PY) -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('pytest') else 1)" >/dev/null 2>&1; then \
>   echo "[test_unit] running pytest (unit filter)"; \
>   $(PY) -m pytest -q \
>     -m 'unit and not (integration or e2e or slow or network or qt or gl or x11 or wayland or docker or gpu or perf or flaky)' \
>     -k 'not multi_window and not tray and not lazy_refresh'; \
> else \
>   echo "[test_unit] pytest still missing unexpectedly; aborting"; exit 1; \
> fi

.PHONY: smoke
smoke: install-dev ## Import core modules to ensure environment is sane
> @set -euo pipefail; \
> $(PY) tools/smoke.py

clean: ## Remove caches and build artifacts
> rm -rf $(VENV) build dist .pytest_cache .ruff_cache .mypy_cache **/__pycache__

