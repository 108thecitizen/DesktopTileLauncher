# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from typing import cast

import pytest

import config_migration
import config_schema_v2 as schema
import config_serialization_v2 as serialization

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "schema_v2"
SCHEMA_PATH = REPO_ROOT / "config_schema_v2.py"
SERIALIZATION_PATH = REPO_ROOT / "config_serialization_v2.py"
MIGRATION_V2_PATH = REPO_ROOT / "config_migration_v2.py"
TRANSFORM_V1_TO_V2_PATH = REPO_ROOT / "config_transform_v1_to_v2.py"
PERSISTENCE_PATH = REPO_ROOT / "config_persistence.py"
PACKAGING_SPEC_PATH = REPO_ROOT / "DesktopTileLauncher.spec"
PRODUCTION_ENTRY_PATH = REPO_ROOT / "tile_launcher.py"
FORBIDDEN_V2_MODULES = frozenset(
    {
        "config_migration_v2.py",
        "config_schema_v2.py",
        "config_serialization_v2.py",
        "config_transform_v1_to_v2.py",
    }
)
QT_IMPORT_ROOTS = frozenset({"PyQt5", "PyQt6", "PySide2", "PySide6"})

SCHEMA_PUBLIC_FUNCTIONS = frozenset({"reject_duplicate_json_members", "validate_v2"})
SCHEMA_PUBLIC_TYPED_DICTS = frozenset(
    {
        "Application",
        "DeviceSpecific",
        "KanbanOrder",
        "LegacyStringIcon",
        "Placement",
        "PlacementLaunchBinding",
        "PortableFallback",
        "Resource",
        "Root",
        "Tab",
        "UrlLaunchSettings",
        "UrlTarget",
        "WindowSettings",
        "Workspace",
        "WorkspaceWindowBinding",
    }
)
SCHEMA_PUBLIC_EXCEPTIONS = frozenset({"DuplicateJsonMemberError"})
SCHEMA_PUBLIC_TYPE_ALIASES = frozenset(
    {
        "DeviceApplicability",
        "DeviceBinding",
        "EntityUUID",
        "Extensions",
        "ExternalDeviceKey",
        "JsonObject",
        "JsonScalar",
        "Lifecycle",
        "OpenTarget",
        "StrictJsonValue",
        "ViewMode",
        "Visibility",
        "WorkflowStatus",
    }
)
SCHEMA_PUBLIC_CONSTANTS = frozenset({"SCHEMA_VERSION_V2"})
SCHEMA_PUBLIC_NAMES = (
    SCHEMA_PUBLIC_FUNCTIONS
    | SCHEMA_PUBLIC_TYPED_DICTS
    | SCHEMA_PUBLIC_EXCEPTIONS
    | SCHEMA_PUBLIC_TYPE_ALIASES
    | SCHEMA_PUBLIC_CONSTANTS
)
SERIALIZATION_PUBLIC_FUNCTIONS = frozenset({"serialize_v2"})
SERIALIZATION_PUBLIC_DATACLASSES = frozenset(
    {"SerializedV2Document", "V2SerializationRejected"}
)
SERIALIZATION_PUBLIC_TYPE_ALIASES = frozenset({"V2SerializationResult"})
SERIALIZATION_PUBLIC_CONSTANTS = frozenset({"MAX_V2_CANDIDATE_BYTES"})
SERIALIZATION_PUBLIC_NAMES = (
    SERIALIZATION_PUBLIC_FUNCTIONS
    | SERIALIZATION_PUBLIC_DATACLASSES
    | SERIALIZATION_PUBLIC_TYPE_ALIASES
    | SERIALIZATION_PUBLIC_CONSTANTS
)
MIGRATION_V2_PUBLIC_FUNCTIONS = frozenset({"migrate_v1_to_v2"})
MIGRATION_V2_PUBLIC_CONSTANTS = frozenset(
    {"MIGRATION_NAMESPACE_V1_TO_V2", "V1_TO_V2_ID_ALLOCATION_ATTEMPTS"}
)
MIGRATION_V2_PUBLIC_NAMES = (
    MIGRATION_V2_PUBLIC_FUNCTIONS | MIGRATION_V2_PUBLIC_CONSTANTS
)
TRANSFORM_V1_TO_V2_PUBLIC_FUNCTIONS = frozenset({"transform_v1_to_v2"})
TRANSFORM_V1_TO_V2_PUBLIC_TYPE_ALIASES = frozenset({"V1ToV2TransformResult"})
TRANSFORM_V1_TO_V2_PUBLIC_NAMES = (
    TRANSFORM_V1_TO_V2_PUBLIC_FUNCTIONS | TRANSFORM_V1_TO_V2_PUBLIC_TYPE_ALIASES
)

EXPECTED_SCHEMA_IMPORTS = frozenset(
    {
        "from __future__ import annotations",
        "from collections.abc import Callable",
        "from dataclasses import dataclass",
        "from typing import Final",
        "from typing import Literal",
        "from typing import TypeAlias",
        "from typing import TypedDict",
        "from typing import cast",
        "from uuid import UUID",
        "import math",
    }
)
EXPECTED_SERIALIZATION_IMPORTS = frozenset(
    {
        "from __future__ import annotations",
        "from config_migration import PureEngineFailureCategory "
        "as _PureEngineFailureCategory",
        "from config_migration import PureExecutionRejectionCategory "
        "as _PureExecutionRejectionCategory",
        "from config_migration import PureExecutionStage as _PureExecutionStage",
        "from config_schema_v2 import validate_v2",
        "from dataclasses import dataclass",
        "from dataclasses import field",
        "from typing import Final",
        "from typing import TypeAlias",
        "import json",
    }
)
EXPECTED_MIGRATION_V2_IMPORTS = frozenset(
    {
        "from __future__ import annotations",
        "from collections.abc import Mapping",
        "from typing import Final",
        "from typing import TypeAlias",
        "from typing import TypedDict",
        "from typing import cast",
        "from uuid import UUID",
        "from uuid import uuid5",
        "import config_schema as v1",
        "import config_schema_v2 as v2",
    }
)
EXPECTED_TRANSFORM_V1_TO_V2_IMPORTS = frozenset(
    {
        "from __future__ import annotations",
        "from collections.abc import Mapping",
        "from typing import TypeAlias",
        "import config_migration_v2 as construction",
        "import config_schema as v1",
        "import config_schema_v2 as v2",
        "import config_serialization_v2 as serialization",
    }
)


def _parse_path(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _qualified_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _qualified_name(node.value)
        if base is None and isinstance(node.value, ast.Subscript):
            base = "<subscript>"
        return None if base is None else f"{base}.{node.attr}"
    if isinstance(node, ast.Call):
        called = _qualified_name(node.func)
        return None if called is None else f"{called}()"
    return None


def _literal_string_sequence(node: ast.AST, label: str) -> tuple[str, ...]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        raise AssertionError(f"{label} must be a literal string sequence")
    values: list[str] = []
    for item in node.elts:
        if not isinstance(item, ast.Constant) or type(item.value) is not str:
            raise AssertionError(f"{label} must contain only literal strings")
        values.append(item.value)
    return tuple(values)


def _import_declarations(tree: ast.Module) -> tuple[str, ...]:
    declarations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                suffix = "" if alias.asname is None else f" as {alias.asname}"
                declarations.append(f"import {alias.name}{suffix}")
        elif isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            for alias in node.names:
                suffix = "" if alias.asname is None else f" as {alias.asname}"
                declarations.append(f"from {module} import {alias.name}{suffix}")
    return tuple(declarations)


def _annotation_kind(node: ast.AnnAssign) -> str:
    annotation = _qualified_name(node.annotation)
    if annotation == "TypeAlias":
        return "type_alias"
    if annotation == "Final" or (
        isinstance(node.annotation, ast.Subscript)
        and _qualified_name(node.annotation.value) == "Final"
    ):
        return "constant"
    return "other"


def _public_annotated_names(tree: ast.Module, kind: str) -> set[str]:
    return {
        node.target.id
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and not node.target.id.startswith("_")
        and _annotation_kind(node) == kind
    }


def _class_kind(node: ast.ClassDef) -> str:
    bases = {_qualified_name(base) for base in node.bases}
    decorators = {
        _qualified_name(decorator.func)
        if isinstance(decorator, ast.Call)
        else _qualified_name(decorator)
        for decorator in node.decorator_list
    }
    if "TypedDict" in bases:
        return "typed_dict"
    if "dataclass" in decorators:
        return "dataclass"
    if bases.intersection({"Enum", "IntEnum", "StrEnum"}):
        return "enum"
    if bases.intersection({"Exception", "ValueError"}):
        return "exception"
    return "other"


def _public_classes(tree: ast.Module, kind: str) -> set[str]:
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and not node.name.startswith("_")
        and _class_kind(node) == kind
    }


def _public_functions(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }


def _module_all(tree: ast.Module) -> set[str]:
    declarations = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        )
    ]
    if len(declarations) != 1:
        raise AssertionError("module must declare one literal __all__")
    return set(_literal_string_sequence(declarations[0].value, "__all__"))


def _assert_utf8_encode_calls(tree: ast.Module, expected_count: int) -> None:
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (_qualified_name(node.func) or "").endswith(".encode")
    ]
    assert len(calls) == expected_count  # nosec B101
    for call in calls:
        assert len(call.args) == 1  # nosec B101
        assert isinstance(call.args[0], ast.Constant)  # nosec B101
        assert call.args[0].value == "utf-8"  # nosec B101
        assert call.keywords == []  # nosec B101


def _assert_serializer_json_surface(tree: ast.Module) -> None:
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    json_attributes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "json"
    ]
    assert len(json_attributes) == 1  # nosec B101
    attribute = json_attributes[0]
    assert _qualified_name(attribute) == "json.dumps"  # nosec B101
    parent = parents[attribute]
    assert isinstance(parent, ast.Call)  # nosec B101
    assert parent.func is attribute  # nosec B101


def _raw_import_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_parse_path(path)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def _path_inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_module(root: Path, module: str) -> Path | None:
    parts = module.split(".")
    if not parts or not all(part.isidentifier() for part in parts):
        return None
    stem = root.joinpath(*parts)
    candidates = (stem / "__init__.py", stem.with_suffix(".py"))
    resolved_root = root.resolve()
    for candidate in candidates:
        resolved = candidate.resolve()
        if _path_inside(resolved_root, resolved) and resolved.is_file():
            return resolved
    return None


def _module_prefix_paths(root: Path, module: str) -> set[Path]:
    parts = module.split(".")
    paths: set[Path] = set()
    for length in range(1, len(parts) + 1):
        resolved = _resolve_module(root, ".".join(parts[:length]))
        if resolved is not None:
            paths.add(resolved)
    return paths


def _current_package_parts(root: Path, path: Path) -> tuple[str, ...]:
    relative = path.resolve().relative_to(root.resolve())
    return relative.parts[:-1]


def _from_import_module(
    root: Path,
    current_path: Path,
    node: ast.ImportFrom,
) -> str | None:
    module_parts = tuple((node.module or "").split(".")) if node.module else ()
    if node.level == 0:
        return ".".join(module_parts)
    package_parts = _current_package_parts(root, current_path)
    if not package_parts:
        raise AssertionError(f"invalid relative import in {current_path}")
    parents_to_drop = node.level - 1
    if parents_to_drop > len(package_parts):
        raise AssertionError(
            f"relative import escapes repository package: {current_path}"
        )
    base_parts = package_parts[: len(package_parts) - parents_to_drop]
    return ".".join((*base_parts, *module_parts))


def _direct_local_imports(root: Path, path: Path) -> set[Path]:
    imports: set[Path] = set()
    for node in ast.walk(_parse_path(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.update(_module_prefix_paths(root, alias.name))
        elif isinstance(node, ast.ImportFrom):
            module = _from_import_module(root, path, node)
            if not module:
                continue
            imports.update(_module_prefix_paths(root, module))
            for alias in node.names:
                if alias.name != "*":
                    imports.update(_module_prefix_paths(root, f"{module}.{alias.name}"))
    return imports


def _repository_local_import_closure(
    root: Path,
    roots: set[Path],
) -> set[Path]:
    resolved_root = root.resolve()
    pending = [path.resolve() for path in roots]
    closure: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in closure:
            continue
        if (
            not _path_inside(resolved_root, path)
            or not path.is_file()
            or path.suffix not in {".py", ".pyw"}
        ):
            raise AssertionError(f"invalid production closure root: {path}")
        closure.add(path)
        pending.extend(_direct_local_imports(resolved_root, path) - closure)
    return closure


def _analysis_call(spec_tree: ast.Module) -> ast.Call:
    calls = [
        node
        for node in ast.walk(spec_tree)
        if isinstance(node, ast.Call)
        and (_qualified_name(node.func) or "").split(".")[-1] == "Analysis"
    ]
    if len(calls) != 1:
        raise AssertionError("packaging spec must contain exactly one Analysis call")
    return calls[0]


def _analysis_keyword(call: ast.Call, name: str) -> ast.AST:
    if any(keyword.arg is None for keyword in call.keywords):
        raise AssertionError("Analysis declarations must not use **kwargs")
    matches = [keyword.value for keyword in call.keywords if keyword.arg == name]
    if len(matches) != 1:
        raise AssertionError(f"Analysis must declare one literal {name}")
    return matches[0]


def _packaging_roots(root: Path, spec_path: Path) -> set[Path]:
    call = _analysis_call(_parse_path(spec_path))
    if len(call.args) != 1:
        raise AssertionError(
            "Analysis entry scripts must be its sole positional argument"
        )
    script_names = _literal_string_sequence(call.args[0], "Analysis entry scripts")
    hidden_names = _literal_string_sequence(
        _analysis_keyword(call, "hiddenimports"),
        "Analysis hidden imports",
    )

    resolved_root = root.resolve()
    roots: set[Path] = set()
    for script_name in script_names:
        candidate = (resolved_root / script_name).resolve()
        if (
            not _path_inside(resolved_root, candidate)
            or not candidate.is_file()
            or candidate.suffix not in {".py", ".pyw"}
            or "tests" in candidate.relative_to(resolved_root).parts
        ):
            raise AssertionError(
                f"invalid literal Analysis entry script: {script_name}"
            )
        roots.add(candidate)
    for hidden_name in hidden_names:
        roots.update(_module_prefix_paths(resolved_root, hidden_name))
    return roots


def _production_and_packaging_closure(
    root: Path,
    primary_entry: Path,
    spec_path: Path,
) -> set[Path]:
    roots = {primary_entry.resolve()} | _packaging_roots(root, spec_path)
    return _repository_local_import_closure(root, roots)


def _relative_paths(root: Path, paths: set[Path]) -> set[Path]:
    resolved_root = root.resolve()
    return {path.relative_to(resolved_root) for path in paths}


def _write_synthetic(root: Path, relative_path: str, source: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _assert_silent_and_private(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    sentinels: tuple[str, ...],
) -> None:
    captured = capsys.readouterr()
    assert captured.out == ""  # nosec B101
    assert captured.err == ""  # nosec B101
    for record in caplog.records:
        rendered = repr((record.msg, record.args, vars(record)))
        for sentinel in sentinels:
            assert sentinel not in rendered  # nosec B101


def test_object_pairs_callback_preserves_unique_decoded_members() -> None:
    pairs: list[tuple[str, object]] = [
        ("first", 1),
        ("second", {"nested": True}),
    ]

    result = schema.reject_duplicate_json_members(pairs)

    assert result == {"first": 1, "second": {"nested": True}}  # nosec B101
    assert list(result) == ["first", "second"]  # nosec B101


def test_direct_duplicate_rejection_is_fixed_private_and_silent(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinels = (
        "duplicate-key-7c889511",
        "duplicate-value-first-1593a0bd",
        "duplicate-value-second-20c1f26d",
    )
    caplog.set_level(logging.DEBUG)
    capsys.readouterr()
    caplog.clear()

    with pytest.raises(schema.DuplicateJsonMemberError) as exc_info:
        schema.reject_duplicate_json_members(
            [
                (sentinels[0], sentinels[1]),
                (sentinels[0], sentinels[2]),
            ]
        )

    error = exc_info.value
    assert type(error) is schema.DuplicateJsonMemberError  # nosec B101
    assert error.args == ("malformed_json",)  # nosec B101
    assert str(error) == "malformed_json"  # nosec B101
    assert repr(error) == "DuplicateJsonMemberError('malformed_json')"  # nosec B101
    assert vars(error) == {}  # nosec B101
    assert error.__cause__ is None  # nosec B101
    assert error.__context__ is None  # nosec B101
    rendered = f"{error!s} {error!r} {error.args!r} {vars(error)!r}"
    for sentinel in sentinels:
        assert sentinel not in rendered  # nosec B101
    _assert_silent_and_private(capsys, caplog, sentinels)


@pytest.mark.parametrize(
    "payload",
    [
        '{"private-root":1,"private-root":2}',
        '{"application":{"private-title":"one","private-title":"two"}}',
        '{"items":[{"private-id":"one","private-id":"two"}]}',
        '{"extensions":{"nested":{"private-secret":"one","private-secret":"two"}}}',
        '{"decoded-name":1,"\\u0064ecoded-name":2}',
    ],
)
def test_standard_decoder_callback_rejects_duplicates_at_every_nesting_depth(
    payload: str,
) -> None:
    with pytest.raises(schema.DuplicateJsonMemberError) as exc_info:
        json.loads(payload, object_pairs_hook=schema.reject_duplicate_json_members)

    rendered = f"{exc_info.value!s} {exc_info.value!r}"
    assert rendered == "malformed_json DuplicateJsonMemberError('malformed_json')"  # nosec B101
    for forbidden in ("private", "decoded-name", "one", "two"):
        assert forbidden not in rendered  # nosec B101


def test_valid_fixture_decodes_through_dormant_callback_then_validates() -> None:
    parsed = json.loads(
        (FIXTURE_ROOT / "representative.json").read_text(encoding="utf-8"),
        object_pairs_hook=schema.reject_duplicate_json_members,
    )

    assert schema.validate_v2(parsed)  # nosec B101


def test_validation_failure_exposes_no_candidate_content_or_side_channel(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinels = (
        "https://validation-private-72ec1727.example.test/path",
        "validation-title-45bed5ea",
        "90000000-0000-4000-8000-000000007711",
        "extension-key-validation-1dc76257",
        "extension-value-validation-ac81d9d5",
    )
    document = cast(
        schema.JsonObject,
        {
            "schema_version": 2,
            "application": {
                "title": sentinels[1],
                "default_workspace_id": sentinels[2],
                "extensions": {sentinels[3]: sentinels[4]},
            },
            "private_url": sentinels[0],
        },
    )
    caplog.set_level(logging.DEBUG)
    capsys.readouterr()
    caplog.clear()

    result = schema.validate_v2(document)

    assert result is False  # nosec B101
    rendered = repr(result)
    for sentinel in sentinels:
        assert sentinel not in rendered  # nosec B101
    _assert_silent_and_private(capsys, caplog, sentinels)


def test_public_structural_result_and_behavioral_surfaces_are_exact() -> None:
    schema_tree = _parse_path(SCHEMA_PATH)
    serialization_tree = _parse_path(SERIALIZATION_PATH)
    migration_v2_tree = _parse_path(MIGRATION_V2_PATH)
    transform_v1_to_v2_tree = _parse_path(TRANSFORM_V1_TO_V2_PATH)

    assert _public_functions(schema_tree) == SCHEMA_PUBLIC_FUNCTIONS  # nosec B101
    assert {  # nosec B101
        node.name
        for node in schema_tree.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    } == SCHEMA_PUBLIC_TYPED_DICTS | SCHEMA_PUBLIC_EXCEPTIONS
    assert (  # nosec B101
        _public_classes(schema_tree, "typed_dict") == SCHEMA_PUBLIC_TYPED_DICTS
    )
    assert (  # nosec B101
        _public_classes(schema_tree, "exception") == SCHEMA_PUBLIC_EXCEPTIONS
    )
    assert _public_classes(schema_tree, "dataclass") == set()  # nosec B101
    assert _public_classes(schema_tree, "enum") == set()  # nosec B101
    assert (  # nosec B101
        _public_annotated_names(schema_tree, "type_alias") == SCHEMA_PUBLIC_TYPE_ALIASES
    )
    assert (  # nosec B101
        _public_annotated_names(schema_tree, "constant") == SCHEMA_PUBLIC_CONSTANTS
    )
    assert _module_all(schema_tree) == SCHEMA_PUBLIC_NAMES  # nosec B101

    assert (  # nosec B101
        _public_functions(serialization_tree) == SERIALIZATION_PUBLIC_FUNCTIONS
    )
    assert {  # nosec B101
        node.name
        for node in serialization_tree.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    } == SERIALIZATION_PUBLIC_DATACLASSES
    assert (  # nosec B101
        _public_classes(serialization_tree, "dataclass")
        == SERIALIZATION_PUBLIC_DATACLASSES
    )
    assert _public_classes(serialization_tree, "typed_dict") == set()  # nosec B101
    assert _public_classes(serialization_tree, "enum") == set()  # nosec B101
    assert _public_classes(serialization_tree, "exception") == set()  # nosec B101
    assert (  # nosec B101
        _public_annotated_names(serialization_tree, "type_alias")
        == SERIALIZATION_PUBLIC_TYPE_ALIASES
    )
    assert (  # nosec B101
        _public_annotated_names(serialization_tree, "constant")
        == SERIALIZATION_PUBLIC_CONSTANTS
    )
    assert _module_all(serialization_tree) == SERIALIZATION_PUBLIC_NAMES  # nosec B101
    assert not hasattr(serialization, "V2SerializationFailureCategory")  # nosec B101
    assert all(  # nosec B101
        not (
            isinstance(node, (ast.Name, ast.ClassDef, ast.FunctionDef, ast.Constant))
            and (
                getattr(node, "id", None)
                or getattr(node, "name", None)
                or getattr(node, "value", None)
            )
            == "V2SerializationFailureCategory"
        )
        for node in ast.walk(serialization_tree)
    )

    assert (  # nosec B101
        _public_functions(migration_v2_tree) == MIGRATION_V2_PUBLIC_FUNCTIONS
    )
    assert {  # nosec B101
        node.name
        for node in migration_v2_tree.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    } == set()
    assert _public_classes(migration_v2_tree, "typed_dict") == set()  # nosec B101
    assert _public_classes(migration_v2_tree, "dataclass") == set()  # nosec B101
    assert _public_classes(migration_v2_tree, "enum") == set()  # nosec B101
    assert _public_classes(migration_v2_tree, "exception") == set()  # nosec B101
    assert (  # nosec B101
        _public_annotated_names(migration_v2_tree, "type_alias") == set()
    )
    assert (  # nosec B101
        _public_annotated_names(migration_v2_tree, "constant")
        == MIGRATION_V2_PUBLIC_CONSTANTS
    )
    assert _module_all(migration_v2_tree) == MIGRATION_V2_PUBLIC_NAMES  # nosec B101

    assert (  # nosec B101
        _public_functions(transform_v1_to_v2_tree)
        == TRANSFORM_V1_TO_V2_PUBLIC_FUNCTIONS
    )
    assert {  # nosec B101
        node.name
        for node in transform_v1_to_v2_tree.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    } == set()
    assert (  # nosec B101
        _public_annotated_names(transform_v1_to_v2_tree, "type_alias")
        == TRANSFORM_V1_TO_V2_PUBLIC_TYPE_ALIASES
    )
    assert (  # nosec B101
        _public_annotated_names(transform_v1_to_v2_tree, "constant") == set()
    )
    assert (  # nosec B101
        _module_all(transform_v1_to_v2_tree) == TRANSFORM_V1_TO_V2_PUBLIC_NAMES
    )


def test_exact_imports_and_focused_json_ast_contracts() -> None:
    schema_tree = _parse_path(SCHEMA_PATH)
    serialization_tree = _parse_path(SERIALIZATION_PATH)
    migration_v2_tree = _parse_path(MIGRATION_V2_PATH)
    transform_v1_to_v2_tree = _parse_path(TRANSFORM_V1_TO_V2_PATH)

    schema_imports = _import_declarations(schema_tree)
    serialization_imports = _import_declarations(serialization_tree)
    migration_v2_imports = _import_declarations(migration_v2_tree)
    transform_v1_to_v2_imports = _import_declarations(transform_v1_to_v2_tree)
    assert len(schema_imports) == len(EXPECTED_SCHEMA_IMPORTS)  # nosec B101
    assert set(schema_imports) == EXPECTED_SCHEMA_IMPORTS  # nosec B101
    assert len(serialization_imports) == len(  # nosec B101
        EXPECTED_SERIALIZATION_IMPORTS
    )
    assert set(serialization_imports) == EXPECTED_SERIALIZATION_IMPORTS  # nosec B101
    assert len(migration_v2_imports) == len(  # nosec B101
        EXPECTED_MIGRATION_V2_IMPORTS
    )
    assert set(migration_v2_imports) == EXPECTED_MIGRATION_V2_IMPORTS  # nosec B101
    assert len(transform_v1_to_v2_imports) == len(  # nosec B101
        EXPECTED_TRANSFORM_V1_TO_V2_IMPORTS
    )
    assert (  # nosec B101
        set(transform_v1_to_v2_imports) == EXPECTED_TRANSFORM_V1_TO_V2_IMPORTS
    )

    # These assertions describe direct JSON-related syntax, not general capabilities.
    assert not any(  # nosec B101
        isinstance(node, ast.Attribute)
        and (_qualified_name(node) or "").startswith("json.")
        for node in ast.walk(schema_tree)
    )
    _assert_serializer_json_surface(serialization_tree)
    _assert_utf8_encode_calls(schema_tree, 1)
    _assert_utf8_encode_calls(serialization_tree, 1)
    assert [  # nosec B101
        _qualified_name(node.type)
        for node in ast.walk(serialization_tree)
        if isinstance(node, ast.ExceptHandler) and node.type is not None
    ] == ["Exception"]


def test_vocabulary_dependency_is_symbol_only_one_way_and_qt_free() -> None:
    migration_path = REPO_ROOT / "config_migration.py"
    dependency_closure = _repository_local_import_closure(
        REPO_ROOT,
        {migration_path},
    )
    relative = _relative_paths(REPO_ROOT, dependency_closure)

    assert {  # nosec B101
        Path("config_migration.py"),
        Path("config_persistence.py"),
        Path("config_recovery.py"),
        Path("config_schema.py"),
    }.issubset(relative)
    assert not {path.name for path in relative}.intersection(  # nosec B101
        FORBIDDEN_V2_MODULES
    )
    for path in dependency_closure:
        import_roots = {
            name.split(".", maxsplit=1)[0] for name in _raw_import_names(path)
        }
        assert import_roots.isdisjoint(QT_IMPORT_ROOTS), path  # nosec B101


def test_transformer_dependency_closure_is_qt_free_and_not_runtime_rooted() -> None:
    dependency_closure = _repository_local_import_closure(
        REPO_ROOT,
        {MIGRATION_V2_PATH},
    )
    relative = _relative_paths(REPO_ROOT, dependency_closure)

    assert {  # nosec B101
        Path("config_migration_v2.py"),
        Path("config_schema.py"),
        Path("config_schema_v2.py"),
    }.issubset(relative)
    assert Path("config_migration.py") not in relative  # nosec B101
    assert Path("tile_launcher.py") not in relative  # nosec B101
    for path in dependency_closure:
        import_roots = {
            name.split(".", maxsplit=1)[0] for name in _raw_import_names(path)
        }
        assert import_roots.isdisjoint(QT_IMPORT_ROOTS), path  # nosec B101


def test_checked_transform_dependency_closure_is_qt_free_and_excludes_startup() -> None:
    dependency_closure = _repository_local_import_closure(
        REPO_ROOT,
        {TRANSFORM_V1_TO_V2_PATH},
    )
    relative = _relative_paths(REPO_ROOT, dependency_closure)

    assert {  # nosec B101
        Path("config_migration_v2.py"),
        Path("config_schema_v2.py"),
        Path("config_serialization_v2.py"),
        Path("config_transform_v1_to_v2.py"),
    }.issubset(relative)
    assert Path("tile_launcher.py") not in relative  # nosec B101
    for path in dependency_closure:
        import_roots = {
            name.split(".", maxsplit=1)[0] for name in _raw_import_names(path)
        }
        assert import_roots.isdisjoint(QT_IMPORT_ROOTS), path  # nosec B101


def test_persistence_dependency_closure_excludes_dormant_v2() -> None:
    dependency_closure = _repository_local_import_closure(
        REPO_ROOT,
        {PERSISTENCE_PATH},
    )
    relative = _relative_paths(REPO_ROOT, dependency_closure)

    assert Path("config_persistence.py") in relative  # nosec B101
    assert not {path.name for path in relative}.intersection(  # nosec B101
        FORBIDDEN_V2_MODULES
    )


def test_real_production_and_packaging_closure_excludes_v2() -> None:
    closure = _production_and_packaging_closure(
        REPO_ROOT,
        PRODUCTION_ENTRY_PATH,
        PACKAGING_SPEC_PATH,
    )
    relative = _relative_paths(REPO_ROOT, closure)

    assert Path("url_import.py") in relative  # nosec B101
    assert Path("config_migration.py") in relative  # nosec B101
    assert not {path.name for path in relative}.intersection(  # nosec B101
        FORBIDDEN_V2_MODULES
    )


def test_transitive_entry_import_of_v2_is_detected(tmp_path: Path) -> None:
    _write_synthetic(tmp_path, "entry.py", "import bridge\n")
    _write_synthetic(tmp_path, "bridge.py", "import config_schema_v2\n")
    _write_synthetic(tmp_path, "config_schema_v2.py", "VALUE = 2\n")
    _write_synthetic(
        tmp_path,
        "app.spec",
        "a = Analysis(['entry.py'], hiddenimports=[])\n",
    )

    closure = _production_and_packaging_closure(
        tmp_path,
        tmp_path / "entry.py",
        tmp_path / "app.spec",
    )

    assert Path("config_schema_v2.py") in _relative_paths(  # nosec B101
        tmp_path, closure
    )


def test_package_wins_over_same_name_module_during_resolution(
    tmp_path: Path,
) -> None:
    _write_synthetic(tmp_path, "entry.py", "import bridge\n")
    _write_synthetic(tmp_path, "bridge.py", "VALUE = 1\n")
    _write_synthetic(
        tmp_path,
        "bridge/__init__.py",
        "import config_schema_v2\n",
    )
    _write_synthetic(tmp_path, "config_schema_v2.py", "VALUE = 2\n")
    _write_synthetic(
        tmp_path,
        "app.spec",
        "a = Analysis(['entry.py'], hiddenimports=[])\n",
    )

    closure = _production_and_packaging_closure(
        tmp_path,
        tmp_path / "entry.py",
        tmp_path / "app.spec",
    )

    relative = _relative_paths(tmp_path, closure)
    assert Path("bridge/__init__.py") in relative  # nosec B101
    assert Path("bridge.py") not in relative  # nosec B101
    assert Path("config_schema_v2.py") in relative  # nosec B101


def test_reachable_module_under_tests_directory_still_reaches_v2(
    tmp_path: Path,
) -> None:
    _write_synthetic(tmp_path, "entry.py", "import application.tests.bridge\n")
    _write_synthetic(tmp_path, "application/__init__.py", "VALUE = 1\n")
    _write_synthetic(tmp_path, "application/tests/__init__.py", "VALUE = 1\n")
    _write_synthetic(
        tmp_path,
        "application/tests/bridge.py",
        "import config_schema_v2\n",
    )
    _write_synthetic(tmp_path, "config_schema_v2.py", "VALUE = 2\n")
    _write_synthetic(
        tmp_path,
        "app.spec",
        "a = Analysis(['entry.py'], hiddenimports=[])\n",
    )

    closure = _production_and_packaging_closure(
        tmp_path,
        tmp_path / "entry.py",
        tmp_path / "app.spec",
    )

    relative = _relative_paths(tmp_path, closure)
    assert Path("application/tests/bridge.py") in relative  # nosec B101
    assert Path("config_schema_v2.py") in relative  # nosec B101


def test_packaging_hidden_import_that_reaches_v2_is_detected(tmp_path: Path) -> None:
    _write_synthetic(tmp_path, "entry.py", "VALUE = 1\n")
    _write_synthetic(
        tmp_path,
        "packaging_bridge.py",
        "import config_serialization_v2\n",
    )
    _write_synthetic(tmp_path, "config_serialization_v2.py", "VALUE = 2\n")
    _write_synthetic(
        tmp_path,
        "app.spec",
        "a = Analysis(['entry.py'], hiddenimports=['packaging_bridge'])\n",
    )

    closure = _production_and_packaging_closure(
        tmp_path,
        tmp_path / "entry.py",
        tmp_path / "app.spec",
    )

    relative = _relative_paths(tmp_path, closure)
    assert Path("packaging_bridge.py") in relative  # nosec B101
    assert Path("config_serialization_v2.py") in relative  # nosec B101


def test_unreachable_v2_import_is_not_a_production_root(tmp_path: Path) -> None:
    _write_synthetic(tmp_path, "entry.py", "import reachable\n")
    _write_synthetic(tmp_path, "reachable.py", "VALUE = 1\n")
    _write_synthetic(tmp_path, "unrelated.py", "import config_schema_v2\n")
    _write_synthetic(tmp_path, "config_schema_v2.py", "VALUE = 2\n")
    _write_synthetic(
        tmp_path,
        "app.spec",
        "a = Analysis(['entry.py'], hiddenimports=[])\n",
    )

    closure = _production_and_packaging_closure(
        tmp_path,
        tmp_path / "entry.py",
        tmp_path / "app.spec",
    )

    relative = _relative_paths(tmp_path, closure)
    assert relative == {Path("entry.py"), Path("reachable.py")}  # nosec B101
    assert Path("unrelated.py") not in relative  # nosec B101
    assert Path("config_schema_v2.py") not in relative  # nosec B101


@pytest.mark.parametrize(
    "spec_source",
    [
        "scripts = ['entry.py']\na = Analysis(scripts, hiddenimports=[])\n",
        "hidden = []\na = Analysis(['entry.py'], hiddenimports=hidden)\n",
        "a = Analysis(['entry.py'])\n",
        "kwargs = {'hiddenimports': []}\na = Analysis(['entry.py'], **kwargs)\n",
    ],
)
def test_packaging_declarations_fail_closed_when_nonliteral(
    tmp_path: Path,
    spec_source: str,
) -> None:
    _write_synthetic(tmp_path, "entry.py", "VALUE = 1\n")
    _write_synthetic(tmp_path, "app.spec", spec_source)

    with pytest.raises(AssertionError):
        _production_and_packaging_closure(
            tmp_path,
            tmp_path / "entry.py",
            tmp_path / "app.spec",
        )


def test_production_registry_remains_exactly_v0_to_v1_and_rejects_v2() -> None:
    spec = config_migration.PRODUCTION_REGISTRY_SPEC
    assert spec.oldest_supported_version == 0  # nosec B101
    assert spec.current_version == 1  # nosec B101
    assert [(step.source_version, step.target_version) for step in spec.steps] == [  # nosec B101
        (0, 1)
    ]
    assert [validator.version for validator in spec.validators] == [0, 1]  # nosec B101

    parsed = json.loads(
        (FIXTURE_ROOT / "minimal.json").read_text(encoding="utf-8"),
        object_pairs_hook=schema.reject_duplicate_json_members,
    )
    result = config_migration.prepare_migration(
        cast(config_migration.JsonObject, parsed),
        config_migration.PRODUCTION_REGISTRY,
    )

    assert isinstance(result, config_migration.VersionRejected)  # nosec B101
    assert (  # nosec B101
        result.category is config_migration.VersionRejectionCategory.UNSUPPORTED_NEWER
    )
    assert result.version == 2  # nosec B101
