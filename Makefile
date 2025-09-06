.RECIPEPREFIX := >
.PHONY: setup fmt lint lint_tests types test_unit cov_unit qa

setup:
>python -m pip install --upgrade pip
>@if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
>@if [ -f requirements-dev.txt ]; then pip install -r requirements-dev.txt; fi

fmt:
>ruff format .

lint:
>ruff check .

lint_tests:
>ruff check tests

types:
>mypy .

test_unit:
>pytest -q -m "unit and not (integration or e2e or slow or network or gui or qt or gl or x11 or wayland or docker or gpu or perf or flaky)" -k "not multi_window and not tray and not lazy_refresh"

cov_unit:
>pytest --cov=src --cov-report=term-missing -m "unit and not (integration or e2e or slow or network or gui or qt or gl or x11 or wayland or docker or gpu or perf or flaky)" -k "not multi_window and not tray and not lazy_refresh"

qa: fmt lint lint_tests types test_unit
