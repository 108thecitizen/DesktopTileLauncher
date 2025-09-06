# AGENTS.md — Python Quality Gates & Test‑Safe Rules

**Purpose**  
This repository uses AI‑assisted code generation. This document binds any code‑generating agent (e.g., Codex) to non‑negotiable quality gates and *safe* test execution rules. Whenever the agent adds or edits Python, it MUST keep the repo in a state where the gates below **run** and **pass** cleanly.

> This version keeps your existing gates—Ruff (format + lint), mypy, pytest—and your “types‑first, minimal ignores” guidance, while adding: (1) a **unit‑only** test contract that avoids Qt/GL/GUI, and (2) explicit Ruff coverage of all Python files under `tests/`.

---

## 0) Capability Boundary (Authoritative)

- You **do not** have GUI, libGL, Wayland/X11, browsers, Docker, GPUs, or external network access.  
- Do **not** install system packages or attempt to verify system packages (e.g., **do not run** `python -m pip show PySide6`).  
- If a task appears to require PySide6/Qt, libGL, or any external service, **stop after the PLAN** (see Output Contract) and explain what a human/CI must run. Do not attempt workarounds.

> Rationale: commands like `pytest -q -k "multi_window or tray or lazy_refresh"` will fail in headless environments (e.g., `ImportError: libGL.so.1`), and `PySide6` may not be installed. These are outside the agent’s capabilities.

---

## 1) Definition of Done (Quality Gates — all must pass)

**These exact commands must exit with code 0. No `|| true`, no skipping, no downgrading severities.**

```bash
# Formatting and linting (entire repo and explicitly the tests/ tree)
ruff format --check .
ruff check .
ruff check tests

# Types
mypy .

# Unit tests only (see §2)
pytest -q -m "unit and not (integration or e2e or slow or network or gui or qt or gl or x11 or wayland or docker or gpu or perf or flaky)" -k "not multi_window and not tray and not lazy_refresh"
These gates intentionally duplicate Ruff linting over the whole repo and over tests/ to guarantee tests/**/*.py are included, independent of configuration. 

## 2) Test Execution Contract (Unit‑only)
You are allowed to run unit tests only—fast, hermetic, no GUI/Qt/GL, no network, no external services.

Allowed test command (only):

bash
Copy code
make test_unit
which must resolve to:

bash
Copy code
pytest -q \
  -m "unit and not (integration or e2e or slow or network or gui or qt or gl or x11 or wayland or docker or gpu or perf or flaky)" \
  -k "not multi_window and not tray and not lazy_refresh"
Never invoke pytest without the selector above.

Never run targeted GUI/Qt/libGL tests by name.

If you add tests, place unit tests in tests/unit/ and mark them @pytest.mark.unit.

If you must edit non‑unit tests (e.g., under tests/integration/), do not execute them; rely on CI/humans.

## 3) Ruff & mypy Requirements
Run both formatter and linter:

ruff format --check .

ruff check .

ruff check tests (explicit coverage of the test tree)

Run strict typing checks: mypy .

Keep imports, naming, and structure Ruff‑friendly. Prefer precise types; avoid broad ignores. 

## 4) Operating Principles (kept from prior guidance)
Never create binary files in the repo. 

Write types first. Fully annotate new/modified public APIs; use from __future__ import annotations.

Prefer fixes over ignores. Keep any # type: ignore[...] or # noqa: RULE minimal, local, and explained.

Handle third‑party typing gaps correctly (use types-<pkg> stubs or local stubs/ rather than global ignore_missing_imports).

Continuously run the gates and iterate until they pass. 

## 5) Output Contract (what the agent must return)
Produce one message with the following fenced sections, in order:

text
Copy code
### PLAN
<Short high-level plan and files you will touch (no hidden reasoning).>

### PATCH
<Unified diffs from repo root (apply with `git apply -p0`).>

### TESTS
<Diffs for new/updated unit tests only (placed under tests/unit/).>

### EVIDENCE
<Paste exact outputs for, in this order:
  ruff format --check .
  ruff check .
  ruff check tests
  mypy .
  make test_unit
Show pytest collected/selected counts and marker filtering; confirm no GUI/Qt/GL tests executed.>

### RISKS
<Potential regressions + why integration/GUI tests were not run; what CI/humans should verify.>

### NOTES
<Docs/config updates needed; any stub packages added; rationale for any narrow ignores.>
If any required fact is missing (e.g., a Make target doesn’t exist), stop after PLAN, propose the minimal patch to add it, and then continue.

## 6) Repository Configuration the Agent Should Maintain
If the files below already exist, update them; if not, add them at the repo root.

pytest.ini — explicit markers & hermetic defaults
ini
Copy code
[pytest]
addopts = -q
markers =
    unit: fast, hermetic tests with no external deps or GUI/Qt/GL
    integration: may touch DBs, services, or the OS
    e2e: end-to-end workflows
    slow: >5s runtime
    network: requires internet or external APIs
    gui: requires a display/Qt
    qt: requires PySide6/PyQt
    gl: requires OpenGL/libGL
    x11: requires X11
    wayland: requires Wayland
    docker: requires containers
    gpu: requires CUDA/Metal
    perf: performance benchmarking
    flaky: nondeterministic
conftest.py — proactively skip GUI/Qt/GL when not available
python
Copy code
import importlib.util
import os
import pytest

def _has_qt() -> bool:
    return importlib.util.find_spec("PySide6") is not None

def _headless() -> bool:
    # No display detected (common in agent environments)
    return os.environ.get("DISPLAY") in (None, "",) and os.environ.get("WAYLAND_DISPLAY") in (None, "",)

def pytest_collection_modifyitems(config, items):
    skip_reasons = []
    if not _has_qt():
        skip_qt = pytest.mark.skip(reason="PySide6/Qt not available in agent environment")
        for it in items:
            if any(m.name in {"qt", "gui"} for m in it.iter_markers()):
                it.add_marker(skip_qt)
        skip_reasons.append("qt/gui")
    if _headless():
        skip_headless = pytest.mark.skip(reason="Headless environment; GUI/GL tests disabled")
        for it in items:
            if any(m.name in {"gui", "gl", "x11", "wayland"} for m in it.iter_markers()):
                it.add_marker(skip_headless)
        skip_reasons.append("gui/gl")
    config.hook.pytest_deselected(items=[it for it in items if any(m.name in {"skip"} for m in it.own_markers)])
Makefile — standardized local commands
make
Copy code
.PHONY: setup fmt lint lint_tests types test_unit cov_unit qa

setup:
\tpython -m pip install --upgrade pip
\t@if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
\t@if [ -f requirements-dev.txt ]; then pip install -r requirements-dev.txt; fi

fmt:
\truff format .

lint:
\truff check .

lint_tests:
\truff check tests

types:
\tmypy .

test_unit:
\tpytest -q -m "unit and not (integration or e2e or slow or network or gui or qt or gl or x11 or wayland or docker or gpu or perf or flaky)" -k "not multi_window and not tray and not lazy_refresh"

cov_unit:
\tpytest --cov=src --cov-report=term-missing -m "unit and not (integration or e2e or slow or network or gui or qt or gl or x11 or wayland or docker or gpu or perf or flaky)" -k "not multi_window and not tray and not lazy_refresh"

qa: fmt lint lint_tests types test_unit
pyproject.toml — Ruff configuration (ensure tests/ is fully covered)
toml
Copy code
[tool.ruff]
line-length = 100
target-version = "py311"
# Keep src layout hints, but lint/format always run from repo root.
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM", "RUF"]
ignore = []
per-file-ignores = { "tests/**" = [] }  # Do not exempt tests from linting.

[tool.ruff.format]
quote-style = "single"
indent-style = "space"
line-ending = "lf"
mypy.ini — strict by default, scoped relaxations for tests if needed
ini
Copy code
[mypy]
python_version = 3.11
strict = True
pretty = True
show_error_codes = True
warn_unused_ignores = True
warn_redundant_casts = True
warn_unreachable = True
ignore_missing_imports = False
mypy_path = src

[mypy-tests.*]
disallow_untyped_defs = False
check_untyped_defs = False
## 7) Prohibited & Risky Commands (for clarity)
❌ pytest -q -k "multi_window or tray or lazy_refresh" — may import Qt/libGL and fail in headless envs.

❌ python -m pip show PySide6 — environment introspection not allowed; assume unavailable.

❌ Any pytest without the unit‑only selectors in §2.

## 8) Agent Checklist (before declaring “done”)
No binary files introduced.

ruff format --check ., ruff check ., and ruff check tests pass.

mypy . passes.

make test_unit runs only unit tests and passes; GUI/Qt/GL tests are not executed.

Any ignore is minimal, local, and explained.

If you needed new Make/pytest/config entries, you added them via patch and included evidence.

By contributing to this repository, any code‑generating agent agrees to stay within the capability boundary, to lint both source and tests/ with Ruff, and to execute unit‑only tests that are safe in a headless environment.

