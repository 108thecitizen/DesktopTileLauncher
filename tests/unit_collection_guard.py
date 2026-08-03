# SPDX-License-Identifier: Apache-2.0
"""Fail-closed collection and import guards for the Qt-free unit-test gate."""

from __future__ import annotations

import importlib.abc
import sys
from collections.abc import Sequence
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType

UNIT_ONLY_MARK_EXPRESSION = (
    "unit and not (integration or e2e or slow or network or gui or qt or gl or x11 "
    "or wayland or docker or gpu or perf or flaky)"
)

FORBIDDEN_UNIT_IMPORT_ROOTS = frozenset(
    {
        "DesktopTileLauncher",
        "PyQt5",
        "PyQt6",
        "PySide6",
        "debug_scaffold",
        "shiboken6",
        "tile_editor_dialog",
        "tile_launcher",
        "url_import_dialog",
    }
)

QT_FREE_UNIT_TEST_PATHS = frozenset(
    {
        "tests/test_browser_chrome_win_unit.py",
        "tests/unit/test_config_migration.py",
        "tests/unit/test_config_migration_v2.py",
        "tests/unit/test_config_persistence.py",
        "tests/unit/test_config_recovery.py",
        "tests/unit/test_config_runtime_v2.py",
        "tests/unit/test_config_schema.py",
        "tests/unit/test_config_schema_v2.py",
        "tests/unit/test_config_schema_v2_boundary.py",
        "tests/unit/test_config_serialization_v2.py",
        "tests/unit/test_page_title_lookup.py",
        "tests/unit/test_tab_order.py",
        "tests/unit/test_test_path_isolation.py",
        "tests/unit/test_tile_metadata_refresh.py",
        "tests/unit/test_unit_collection_boundary.py",
        "tests/unit/test_url_import.py",
    }
)

QUARANTINED_TEST_PATHS = frozenset(
    {
        "tests/test_auto_columns.py",
        "tests/test_available_browsers.py",
        "tests/test_browser.py",
        "tests/test_debug_scaffold_unit.py",
        "tests/test_launch_plan.py",
        "tests/test_smoke.py",
        "tests/unit/test_breadcrumb_ring.py",
        "tests/unit/test_compute_grid_fit.py",
        "tests/unit/test_crash_bundle.py",
        "tests/unit/test_fit_policy.py",
        "tests/unit/test_hidden_tabs.py",
        "tests/unit/test_launching.py",
        "tests/unit/test_sanitize_log_extra.py",
        "tests/unit/test_sanitize_url.py",
        "tests/unit/test_tab_visibility_dialog.py",
        "tests/unit/test_tile_editor_title_suggestion.py",
        "tests/unit/test_tile_name_diagnostics.py",
        "tests/unit/test_url_import_dialog.py",
        "tests/unit/test_url_import_integration.py",
        "tests/unit/test_whitespace_context_menu.py",
    }
)


def is_unit_only_expression(expression: str) -> bool:
    """Return whether a marker expression is the repository's exact unit contract."""

    return " ".join(expression.split()) == UNIT_ONLY_MARK_EXPRESSION


def repo_relative_path(path: Path, repo_root: Path) -> str | None:
    """Return one normalized repository-relative path, or ``None`` if external."""

    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return None


def should_ignore_test_module(
    path: Path,
    repo_root: Path,
    marker_expression: str,
) -> bool | None:
    """Ignore non-allowlisted test modules before import during the unit gate."""

    if not is_unit_only_expression(marker_expression):
        return None
    if path.suffix != ".py" or not path.name.startswith("test_"):
        return None
    relative = repo_relative_path(path, repo_root)
    if relative is None or not relative.startswith("tests/"):
        return True
    relative_path = Path(relative)
    if relative_path.suffix != ".py" or not relative_path.name.startswith("test_"):
        return None
    if relative in QT_FREE_UNIT_TEST_PATHS:
        return None
    return True


def forbidden_loaded_modules() -> tuple[str, ...]:
    """Return forbidden module names already loaded into the current process."""

    return tuple(
        sorted(
            name
            for name in sys.modules
            if name.partition(".")[0] in FORBIDDEN_UNIT_IMPORT_ROOTS
        )
    )


class ForbiddenUnitImportFinder(importlib.abc.MetaPathFinder):
    """Reject imports that would cross the Qt-free unit-test boundary."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        del path, target
        root = fullname.partition(".")[0]
        if root in FORBIDDEN_UNIT_IMPORT_ROOTS:
            raise RuntimeError(f"unit collection blocked forbidden import root: {root}")
        return None


__all__ = [
    "FORBIDDEN_UNIT_IMPORT_ROOTS",
    "ForbiddenUnitImportFinder",
    "QT_FREE_UNIT_TEST_PATHS",
    "QUARANTINED_TEST_PATHS",
    "UNIT_ONLY_MARK_EXPRESSION",
    "forbidden_loaded_modules",
    "is_unit_only_expression",
    "repo_relative_path",
    "should_ignore_test_module",
]
