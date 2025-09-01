AGENTS.md — Python Quality Gates (Rules 1–2, 6–8)

Purpose. This repository uses AI-assisted code generation. This document binds any code‑generating agent (e.g., Codex) to the non‑negotiable quality gates for all Python work. Whenever the agent adds or edits Python code, it MUST ensure that the repository cleanly passes the checks below before considering the task complete.

The Rules

1) Never create a binary file.
2) Ensure that repository changes always pass Bandit security checks by running `bandit -r .`.

6) Ensure that all Python source would successfully pass mypy type checks.
7) Ensure that all Python source would successfully pass Ruff lint checks.
8) Ensure that all Python source would successfully pass Ruff format checks.

Definition of Done for any change touching Python code:

bandit -r .
ruff format --check .
ruff check .
mypy .
pytest


All five commands must exit with code 0.

Agent Operating Procedure

Adopt or create config files. If the repo already has config, respect it. If not, the agent must add the baseline configurations shown below and then update them as needed to fit the codebase.

Write types first. New and modified functions, methods, class attributes, and public module APIs must be fully annotated. Prefer precise types over Any. Use from __future__ import annotations at the top of new modules.

Prefer fixes over ignores. Only use # type: ignore[code] or # noqa: RULE as a last resort, and always with the narrowest scope and an explanatory comment. Never disable entire rule families repo‑wide just to “make it pass”.

Handle third‑party typing gaps properly. If a dependency lacks type hints:

Add the relevant types-<package> stub dependency, or

Add a minimal local stub in stubs/<package>/__init__.pyi, and

Use per‑module mypy overrides rather than global ignore_missing_imports = True.

Keep imports, naming, and structure Ruff‑friendly. Sort imports, remove unused code, prefer modern Python constructs, and keep cyclomatic complexity reasonable.

Continuously run the gates. After generating or editing code, the agent must run the five commands above and iterate until they pass.

Repository Configuration the Agent Should Maintain

If these files already exist, update them; if not, create them at the repo root.

pyproject.toml — Ruff configuration

Set target-version to the lowest supported Python version for this repo. If a [project] table exists with requires-python, mirror that here.

[tool.ruff]
# Adjust to your project layout.
src = ["src", "tests"]
line-length = 100
# Keep in sync with the project's supported Python version.
target-version = "py311"

[tool.ruff.lint]
# A pragmatic default: pycodestyle/pyflakes, import sorting, bugbear, pyupgrade,
# simplifications, and Ruff-specific rules.
select = ["E", "F", "I", "B", "UP", "SIM", "RUF"]
# Add ignores sparingly; example: allow 'assert' in tests only.
ignore = []

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101"]  # If security rules are later enabled.

[tool.ruff.format]
# Enforce a single coherent style across the repo.
quote-style = "single"
indent-style = "space"
line-ending = "lf"
skip-magic-trailing-comma = false


If the repository opts into additional rule families (e.g., S for security, PL* for pylint), the agent must fix violations rather than turning rules off globally.

mypy.ini — mypy configuration

Favor strictness for new code; relax per‑module only when absolutely necessary.

[mypy]
python_version = 3.11
strict = True
pretty = True
show_error_codes = True
warn_unused_ignores = True
warn_redundant_casts = True
warn_unreachable = True
# Prefer targeted ignores to global import ignoring.
ignore_missing_imports = False
# Treat 'src' as the primary code root if present.
mypy_path = src

# Example: ease constraints in tests or legacy areas (edit paths to match repo).
[mypy-tests.*]
disallow_untyped_defs = False
check_untyped_defs = False


If the project uses frameworks needing plugins (e.g., Pydantic), the agent should add them only when present in dependencies (e.g., plugins = pydantic.mypy) and never unconditionally.

.pre-commit-config.yaml — local commit gate (recommended)
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0 # pin to the latest stable tag when editing
    hooks:
      - id: ruff
        args: ["--fix"]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v0 # pin to the latest stable tag when editing
    hooks:
      - id: mypy
        # Keep in sync with mypy.ini and add type stubs as needed.
        additional_dependencies: []


After adding this file, run: pip install pre-commit && pre-commit install.

.github/workflows/quality.yml — CI enforcement (recommended)
name: Quality Gates

on:
  pull_request:
  push:
    branches: [ main, master ]

jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
          if [ -f requirements-dev.txt ]; then pip install -r requirements-dev.txt; fi
          # Ensure tools are present for the gates:
          pip install "ruff>=0.4" "mypy>=1.8"  # versions may be adjusted upward

      - name: Ruff format (check)
        run: ruff format --check .

      - name: Ruff lint
        run: ruff check .

      - name: mypy
        run: mypy .

Coding Guidance (so the gates pass the first time)

Type everything that crosses a boundary. All public functions, methods, dataclass fields, and module‑level variables must be annotated. For generics, avoid bare containers (list, dict)—use list[str], dict[str, int], etc.

Use postponed evaluation of annotations. Put from __future__ import annotations at the top of new modules to avoid runtime import cycles and enable forward references without quotes.

Prefer precise types over Any. Reach for TypedDict, Protocol, and Literal where helpful. Use typing.cast with an explanatory comment if narrowing is required.

Keep imports clean. Remove unused imports and variables, group and sort imports (Ruff will enforce), and avoid wildcard imports.

Modernize while you’re there. Prefer f‑strings, comprehensions, pathlib.Path, enumerate, dataclasses, and contextlib helpers—Ruff (UP, SIM, B) will guide these.

Localized exceptions only. If an ignore is unavoidable, use the narrowest code (e.g., # type: ignore[arg-type] # explanation) or line‑specific # noqa: RUF100 # explanation.

Convenience (optional but encouraged)

Add a Makefile to standardize local runs:

.PHONY: format lint typecheck qa
format:
	ruff format .
	ruff check --fix .

lint:
	ruff check .

typecheck:
	mypy .

qa: format lint typecheck

Agent Checklist (before declaring a task “done”)

Code compiles and runs.

No binary files have been introduced into the repository.

bandit -r . passes with zero findings.

ruff format --check . passes.

ruff check . passes with zero errors (and no broad, unjustified ignores).

mypy . passes with zero errors, using the repo’s mypy.ini.

Any added ignores or overrides are minimal, documented, and scoped.

CI configuration and pre‑commit hooks are updated if the change requires it (e.g., new stubs).

By contributing to this repository, any code‑generating agent agrees to follow Rules 1–2 and 6–8 above and to keep the repository in a state where these gates pass at all times.
