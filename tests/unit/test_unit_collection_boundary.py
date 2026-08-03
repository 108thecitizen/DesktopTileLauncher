# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for the fail-closed Qt-free unit-test boundary."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from unit_collection_guard import (
    FORBIDDEN_UNIT_IMPORT_ROOTS,
    ForbiddenUnitImportFinder,
    QT_FREE_UNIT_TEST_PATHS,
    QUARANTINED_TEST_PATHS,
    UNIT_ONLY_MARK_EXPRESSION,
    forbidden_loaded_modules,
    is_pytest_test_module,
    should_ignore_test_module,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "tests"
MAKEFILE_PATH = REPO_ROOT / "Makefile"


def _test_module_paths() -> frozenset[str]:
    return frozenset(
        path.relative_to(REPO_ROOT).as_posix()
        for path in TESTS_ROOT.rglob("*.py")
        if is_pytest_test_module(path)
    )


def _imported_module_names(path: Path) -> frozenset[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module is not None:
                names.add(node.module)
    return frozenset(names)


def _repository_module_paths(module_name: str) -> tuple[Path, ...]:
    parts = module_name.split(".")
    candidates: list[Path] = []
    for root in (REPO_ROOT, TESTS_ROOT):
        module_path = root.joinpath(*parts).with_suffix(".py")
        package_path = root.joinpath(*parts, "__init__.py")
        for candidate in (module_path, package_path):
            if candidate.is_file() and candidate not in candidates:
                candidates.append(candidate)
    return tuple(candidates)


def _local_import_closure(paths: set[Path]) -> set[Path]:
    closure: set[Path] = set()
    pending = list(paths)
    while pending:
        path = pending.pop()
        if path in closure:
            continue
        closure.add(path)
        for module_name in _imported_module_names(path):
            pending.extend(_repository_module_paths(module_name))
    return closure


def _is_unit_marker(node: ast.AST) -> bool:
    candidate = node.func if isinstance(node, ast.Call) else node
    return (
        isinstance(candidate, ast.Attribute)
        and candidate.attr == "unit"
        and isinstance(candidate.value, ast.Attribute)
        and candidate.value.attr == "mark"
        and isinstance(candidate.value.value, ast.Name)
        and candidate.value.value.id == "pytest"
    )


def _contains_direct_unit_marker(node: ast.AST) -> bool:
    if _is_unit_marker(node):
        return True
    if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return any(_is_unit_marker(item) for item in node.elts)
    return False


def _module_has_unit_marker(tree: ast.Module) -> bool:
    return any(
        isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
        )
        and node.value is not None
        and _contains_direct_unit_marker(node.value)
        for node in tree.body
    )


def _unmarked_test_names(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_marked = _module_has_unit_marker(tree)
    unmarked: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test") and not (
                module_marked
                or any(_is_unit_marker(item) for item in node.decorator_list)
            ):
                unmarked.append(node.name)
            continue
        if not isinstance(node, ast.ClassDef) or not node.name.startswith("Test"):
            continue
        class_marked = module_marked or any(
            _is_unit_marker(item) for item in node.decorator_list
        )
        for child in node.body:
            if (
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name.startswith("test")
                and not (
                    class_marked
                    or any(_is_unit_marker(item) for item in child.decorator_list)
                )
            ):
                unmarked.append(f"{node.name}.{child.name}")
    return tuple(unmarked)


def _make_target_recipe(target: str) -> tuple[str, ...]:
    lines = MAKEFILE_PATH.read_text(encoding="utf-8").splitlines()
    target_index = next(
        (
            index
            for index, line in enumerate(lines)
            if not line.startswith((" ", "\t")) and line.partition(":")[0] == target
        ),
        None,
    )
    if target_index is None:
        return ()

    recipe: list[str] = []
    for line in lines[target_index + 1 :]:
        if line.startswith("\t"):
            recipe.append(line.strip())
            continue
        if line.strip():
            break
        if recipe:
            break
    return tuple(recipe)


def test_every_test_module_is_classified_exactly_once() -> None:
    assert QT_FREE_UNIT_TEST_PATHS.isdisjoint(QUARANTINED_TEST_PATHS)  # nosec B101
    assert (  # nosec B101
        QT_FREE_UNIT_TEST_PATHS | QUARANTINED_TEST_PATHS == _test_module_paths()
    )


def test_unit_collection_is_fail_closed_for_quarantined_and_unknown_tests() -> None:
    for relative in QUARANTINED_TEST_PATHS:
        assert (  # nosec B101
            should_ignore_test_module(
                REPO_ROOT / relative,
                REPO_ROOT,
                UNIT_ONLY_MARK_EXPRESSION,
            )
            is True
        )
    for filename in ("test_future_unclassified.py", "future_unclassified_test.py"):
        assert (  # nosec B101
            should_ignore_test_module(
                TESTS_ROOT / "unit" / filename,
                REPO_ROOT,
                UNIT_ONLY_MARK_EXPRESSION,
            )
            is True
        )


def test_unit_collection_allows_only_classified_safe_tests() -> None:
    for relative in QT_FREE_UNIT_TEST_PATHS:
        assert (  # nosec B101
            should_ignore_test_module(
                REPO_ROOT / relative,
                REPO_ROOT,
                UNIT_ONLY_MARK_EXPRESSION,
            )
            is None
        )


def test_allowlisted_tests_have_effective_unit_markers() -> None:
    violations = {
        relative: _unmarked_test_names(REPO_ROOT / relative)
        for relative in QT_FREE_UNIT_TEST_PATHS
        if _unmarked_test_names(REPO_ROOT / relative)
    }

    assert not violations  # nosec B101


def test_nested_parameter_mark_does_not_mark_the_whole_test(tmp_path: Path) -> None:
    path = tmp_path / "test_nested_parameter_mark.py"
    path.write_text(
        "import pytest\n\n"
        "@pytest.mark.parametrize(\n"
        "    'value',\n"
        "    [pytest.param(1, marks=pytest.mark.unit), 2],\n"
        ")\n"
        "def testMixedMarking(value: int) -> None:\n"
        "    del value\n",
        encoding="utf-8",
    )

    assert _unmarked_test_names(path) == ("testMixedMarking",)  # nosec B101


def test_unit_target_collects_only_the_guarded_tests_tree() -> None:
    makefile_lines = MAKEFILE_PATH.read_text(encoding="utf-8").splitlines()
    target_declaration = next(
        line
        for line in makefile_lines
        if not line.startswith((" ", "\t")) and line.partition(":")[0] == "test_unit"
    )
    prerequisites = target_declaration.partition(":")[2].partition("##")[0].split()
    phony_targets = {
        target
        for line in makefile_lines
        if line.startswith(".PHONY:")
        for target in line.partition(":")[2].split()
    }
    recipe = _make_target_recipe("test_unit")
    pytest_invocations = tuple(
        line
        for line in recipe
        if "pytest" in line
        and not line.startswith("echo ")
        and "find_spec('pytest')" not in line
    )

    assert "test_unit" in phony_targets  # nosec B101
    assert not prerequisites  # nosec B101
    assert pytest_invocations == (  # nosec B101
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(PY) -m pytest -q tests \\",
    )


def test_make_online_probe_is_lazy() -> None:
    makefile_lines = MAKEFILE_PATH.read_text(encoding="utf-8").splitlines()
    online_assignment = next(
        line for line in makefile_lines if line.startswith("ONLINE ")
    )

    assert online_assignment.startswith("ONLINE = ")  # nosec B101


def test_unit_collection_ignores_external_test_modules() -> None:
    external = REPO_ROOT.parent / "test_external_unclassified.py"

    assert (  # nosec B101
        should_ignore_test_module(
            external,
            REPO_ROOT,
            UNIT_ONLY_MARK_EXPRESSION,
        )
        is True
    )


def test_non_unit_collection_is_unchanged() -> None:
    for relative in _test_module_paths():
        assert (  # nosec B101
            should_ignore_test_module(
                REPO_ROOT / relative,
                REPO_ROOT,
                "qt or gui",
            )
            is None
        )


def test_import_finder_blocks_forbidden_roots_without_importing_them() -> None:
    finder = ForbiddenUnitImportFinder()

    assert finder.find_spec("json", None) is None  # nosec B101
    for root in FORBIDDEN_UNIT_IMPORT_ROOTS:
        with pytest.raises(RuntimeError, match="forbidden import root"):
            finder.find_spec(f"{root}.sentinel", None)


def test_allowlisted_import_closure_is_qt_free() -> None:
    allowlisted_paths = {REPO_ROOT / relative for relative in QT_FREE_UNIT_TEST_PATHS}
    closure = _local_import_closure(allowlisted_paths)
    violations = {
        (path.relative_to(REPO_ROOT).as_posix(), module_name)
        for path in closure
        for module_name in _imported_module_names(path)
        if module_name.partition(".")[0] in FORBIDDEN_UNIT_IMPORT_ROOTS
    }

    assert not violations  # nosec B101


def test_unit_session_has_not_loaded_forbidden_modules() -> None:
    assert not forbidden_loaded_modules()  # nosec B101
    assert all(  # nosec B101
        name.partition(".")[0] not in FORBIDDEN_UNIT_IMPORT_ROOTS
        for name in sys.modules
    )
