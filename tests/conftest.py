# SPDX-License-Identifier: Apache-2.0
"""
Pytest bootstrap for headless/CI runs.

- Preserves existing behavior:
  * QT_QPA_PLATFORM=offscreen (don't require a display)
  * Add repo root to sys.path for imports

- Adds:
  * QT_OPENGL=software to avoid libGL/OpenGL driver lookups in headless CI
  * A safe XDG_RUNTIME_DIR with 0700 perms (Qt checks this)
  * Optional marker 'requires_opengl' you can use to skip GL-only tests when libGL isn't available
"""

from __future__ import annotations

import ctypes
import os
import pathlib
import sys
import tempfile
from typing import Protocol

import pytest

from unit_collection_guard import (
    ForbiddenUnitImportFinder,
    forbidden_loaded_modules,
    is_unit_only_expression,
    repo_relative_path,
    should_ignore_test_module,
)

# ---------- Test-only path sandbox ----------
# Keep application config, logs, caches, and temp files out of the real user profile.
ROOT_PATH = pathlib.Path(__file__).resolve().parents[1]
TEST_RUNTIME_ROOT = ROOT_PATH / ".pytest_cache" / "test-runtime"
TEST_TEMP_ROOT = TEST_RUNTIME_ROOT / "temp"

for path in (
    TEST_RUNTIME_ROOT,
    TEST_TEMP_ROOT,
    TEST_RUNTIME_ROOT / "appdata",
    TEST_RUNTIME_ROOT / "localappdata",
    TEST_RUNTIME_ROOT / "xdg-config",
    TEST_RUNTIME_ROOT / "xdg-state",
    TEST_RUNTIME_ROOT / "xdg-cache",
):
    path.mkdir(parents=True, exist_ok=True)

_TEST_ENV_PATHS = {
    "APPDATA": TEST_RUNTIME_ROOT / "appdata",
    "LOCALAPPDATA": TEST_RUNTIME_ROOT / "localappdata",
    "TEMP": TEST_TEMP_ROOT,
    "TMP": TEST_TEMP_ROOT,
    "TMPDIR": TEST_TEMP_ROOT,
    "XDG_CONFIG_HOME": TEST_RUNTIME_ROOT / "xdg-config",
    "XDG_STATE_HOME": TEST_RUNTIME_ROOT / "xdg-state",
    "XDG_CACHE_HOME": TEST_RUNTIME_ROOT / "xdg-cache",
}
for name, path in _TEST_ENV_PATHS.items():
    os.environ[name] = str(path)

# tempfile caches its chosen temp dir, so force it to observe the test env above.
tempfile.tempdir = None

# ---------- Headless-safe Qt defaults ----------
# Keep your original setting and add a software GL fallback.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

# Provide a writable runtime dir with correct perms so Qt doesn't complain.
try:
    uid = os.getuid()  # not present on Windows; handled below
except AttributeError:
    uid = 0
_xdg = TEST_TEMP_ROOT / f"xdg-runtime-{uid}"
try:
    _xdg.mkdir(parents=True, exist_ok=True)
    # Some filesystems (e.g., Windows mounts) may not support chmod; ignore if it fails.
    _xdg.chmod(0o700)
except Exception:
    pass
os.environ["XDG_RUNTIME_DIR"] = str(_xdg)

# ---------- Preserve your original sys.path tweak ----------
ROOT = str(ROOT_PATH)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


_UNIT_IMPORT_FINDER: ForbiddenUnitImportFinder | None = None
_UNIT_IGNORED_PATHS: set[str] = set()
_UNIT_SELECTED_COUNT = 0
_UNIT_DESELECTED_COUNT = 0


class _TerminalReporter(Protocol):
    stats: dict[str, list[object]]

    def write_line(self, line: str) -> None: ...


def _marker_expression(config: pytest.Config) -> str:
    expression = config.getoption("markexpr")
    return expression if isinstance(expression, str) else ""


@pytest.hookimpl(tryfirst=True)
def pytest_ignore_collect(
    collection_path: pathlib.Path,
    config: pytest.Config,
) -> bool | None:
    """Exclude unsafe or unclassified tests before Python imports them."""

    ignored = should_ignore_test_module(
        collection_path,
        ROOT_PATH,
        _marker_expression(config),
    )
    if ignored:
        relative = repo_relative_path(collection_path, ROOT_PATH)
        if relative is not None:
            _UNIT_IGNORED_PATHS.add(relative)
    return ignored


# ---------- Optional: mark and skip GL-only tests in headless CI ----------
def _has_libgl() -> bool:
    """Detect presence of the OpenGL runtime."""
    try:
        ctypes.CDLL("libGL.so.1")
        return True
    except OSError:
        return False


def pytest_configure(config: pytest.Config) -> None:
    # Register a marker so pytest doesn't warn about it if/when you use it.
    config.addinivalue_line(
        "markers",
        "requires_opengl: test depends on an OpenGL runtime (libGL); skip in headless CI",
    )

    if not is_unit_only_expression(_marker_expression(config)):
        return
    loaded = forbidden_loaded_modules()
    if loaded:
        joined = ", ".join(loaded)
        raise pytest.UsageError(
            f"forbidden modules loaded before Qt-free unit collection: {joined}"
        )

    global _UNIT_IMPORT_FINDER
    _UNIT_IMPORT_FINDER = ForbiddenUnitImportFinder()
    sys.meta_path.insert(0, _UNIT_IMPORT_FINDER)


def pytest_runtest_setup(item: pytest.Item) -> None:
    # Only skip tests you explicitly mark; nothing else is affected.
    if "requires_opengl" in item.keywords and not _has_libgl():
        pytest.skip("OpenGL runtime (libGL.so.1) not present in this environment")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Record the final item count after marker and keyword filtering."""

    if not is_unit_only_expression(_marker_expression(config)):
        return
    global _UNIT_SELECTED_COUNT
    _UNIT_SELECTED_COUNT = len(items)


def pytest_deselected(items: list[pytest.Item]) -> None:
    """Record marker/keyword deselections for explicit unit-gate evidence."""

    global _UNIT_DESELECTED_COUNT
    _UNIT_DESELECTED_COUNT += len(items)


def pytest_terminal_summary(
    terminalreporter: _TerminalReporter,
    exitstatus: int,
    config: pytest.Config,
) -> None:
    """Report pre-collection exclusions in the required unit-gate evidence."""

    del exitstatus
    if not is_unit_only_expression(_marker_expression(config)):
        return
    terminalreporter.write_line(
        "unit collection safety: "
        f"{len(_UNIT_IGNORED_PATHS)} non-allowlisted test modules excluded before import; "
        "forbidden imports blocked"
    )
    terminalreporter.write_line(
        "unit marker/keyword selection: "
        f"{_UNIT_SELECTED_COUNT} selected, {_UNIT_DESELECTED_COUNT} deselected"
    )
    terminalreporter.write_line(
        "unit test outcomes: "
        f"{len(terminalreporter.stats.get('passed', []))} passed, "
        f"{len(terminalreporter.stats.get('failed', []))} failed, "
        f"{len(terminalreporter.stats.get('skipped', []))} skipped, "
        f"{len(terminalreporter.stats.get('error', []))} errors"
    )


def pytest_unconfigure(config: pytest.Config) -> None:
    """Remove the unit-only import guard after the pytest session."""

    del config
    global _UNIT_IMPORT_FINDER
    if _UNIT_IMPORT_FINDER in sys.meta_path:
        sys.meta_path.remove(_UNIT_IMPORT_FINDER)
    _UNIT_IMPORT_FINDER = None
