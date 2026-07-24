# SPDX-License-Identifier: Apache-2.0
"""Pure schema-version 1 validation, construction, and v0 migration.

This module deliberately has no Qt, filesystem, clock, environment, or random
dependency.  Runtime callers inject UUIDv4 allocation for native documents;
legacy migration identities are deterministic UUIDv5 values derived only from
the complete detached v0 mapping.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from typing import Final, TypeAlias, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from config_recovery import LegacyConstructionFailure, validate_legacy_mapping

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
Uuid4Allocator: TypeAlias = Callable[[], UUID | str]
Uuid5Factory: TypeAlias = Callable[[UUID, str], UUID | str]

SCHEMA_VERSION: Final = 1
DEFAULT_WORKSPACE_NAME: Final = "Default Workspace"
DEFAULT_APPLICATION_TITLE: Final = "My Launcher"
LEGACY_APPLICATION_TITLE: Final = "Launcher"
DEFAULT_TAB_NAME: Final = "Main"
LEGACY_EXTENSION_NAMESPACE: Final = "io.github.108thecitizen.legacy"
MIGRATION_NAME_ROOT: Final = (
    "https://github.com/108thecitizen/DesktopTileLauncher/migration/v0"
)
NATIVE_ID_ALLOCATION_ATTEMPTS: Final = 32

_ROOT_FIELDS: Final = frozenset(
    {
        "schema_version",
        "application",
        "workspaces",
        "tabs",
        "tiles",
        "columns",
        "auto_fit",
        "window_x",
        "window_y",
        "window_w",
        "window_h",
        "extensions",
    }
)
_APPLICATION_FIELDS: Final = frozenset({"title", "default_workspace_id", "extensions"})
_WORKSPACE_FIELDS: Final = frozenset({"id", "name", "tab_order", "extensions"})
_TAB_FIELDS: Final = frozenset(
    {"id", "workspace_id", "name", "visibility", "extensions"}
)
_TILE_FIELDS: Final = frozenset(
    {
        "name",
        "url",
        "tab_id",
        "icon",
        "bg",
        "browser",
        "chrome_profile",
        "open_target",
    }
)
_RECOGNIZED_V0_FIELDS: Final = frozenset(
    {
        "title",
        "columns",
        "tiles",
        "tabs",
        "hidden_tabs",
        "tab_ids",
        "tab_order",
        "auto_fit",
        "window_x",
        "window_y",
        "window_w",
        "window_h",
    }
)


class NativeV1ConstructionError(ValueError):
    """A native v1 identity could not be allocated safely."""

    def __init__(self) -> None:
        super().__init__("native_v1_identity_allocation_failed")


def _is_int(value: object) -> bool:
    return type(value) is int


def _is_nullable_int(value: object) -> bool:
    return value is None or _is_int(value)


def _is_nullable_string(value: object) -> bool:
    return value is None or isinstance(value, str)


def _canonical_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError):
        return None
    canonical = str(parsed)
    return canonical if value == canonical else None


def _canonical_legacy_uuid(value: object) -> str | None:
    """Canonicalize the legacy format, accepting canonical uppercase text."""

    if not isinstance(value, str):
        return None
    try:
        canonical = str(UUID(value))
    except (AttributeError, ValueError):
        return None
    return canonical if value.lower() == canonical else None


def _is_utf8_text(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _is_strict_json(value: object, active: set[int]) -> bool:
    value_type = type(value)
    if value is None or value_type in (bool, int):
        return True
    if value_type is str:
        return _is_utf8_text(cast(str, value))
    if value_type is float:
        return math.isfinite(cast(float, value))
    if value_type is list:
        marker = id(value)
        if marker in active:
            return False
        active.add(marker)
        try:
            return all(
                _is_strict_json(item, active) for item in cast(list[object], value)
            )
        finally:
            active.remove(marker)
    if value_type is dict:
        marker = id(value)
        if marker in active:
            return False
        active.add(marker)
        try:
            mapping = cast(dict[object, object], value)
            return all(
                isinstance(key, str)
                and _is_utf8_text(key)
                and _is_strict_json(item, active)
                for key, item in mapping.items()
            )
        finally:
            active.remove(marker)
    return False


def _valid_extensions(value: object) -> bool:
    if type(value) is not dict:
        return False
    extensions = cast(dict[str, object], value)
    if not extensions:
        return True
    if set(extensions) != {LEGACY_EXTENSION_NAMESPACE}:
        return False
    legacy = extensions[LEGACY_EXTENSION_NAMESPACE]
    return type(legacy) is dict and _is_strict_json(legacy, set())


def _valid_application(value: object) -> tuple[str, str] | None:
    if type(value) is not dict:
        return None
    application = cast(dict[str, object], value)
    if set(application) != _APPLICATION_FIELDS:
        return None
    title = application["title"]
    workspace_id = application["default_workspace_id"]
    if (
        not isinstance(title, str)
        or _canonical_uuid(workspace_id) is None
        or application["extensions"] != {}
        or type(application["extensions"]) is not dict
    ):
        return None
    return title, cast(str, workspace_id)


def _valid_workspace(value: object) -> tuple[str, list[str]] | None:
    if type(value) is not dict:
        return None
    workspace = cast(dict[str, object], value)
    if set(workspace) != _WORKSPACE_FIELDS:
        return None
    workspace_id = workspace["id"]
    name = workspace["name"]
    raw_order = workspace["tab_order"]
    if (
        _canonical_uuid(workspace_id) is None
        or not isinstance(name, str)
        or not name
        or type(raw_order) is not list
        or workspace["extensions"] != {}
        or type(workspace["extensions"]) is not dict
    ):
        return None
    order = cast(list[object], raw_order)
    if any(_canonical_uuid(tab_id) is None for tab_id in order):
        return None
    return cast(str, workspace_id), cast(list[str], order)


def _valid_tabs(
    value: object,
    workspace_id: str,
) -> tuple[set[str], set[str]] | None:
    if type(value) is not list or not value:
        return None
    tab_ids: set[str] = set()
    tab_names: set[str] = set()
    visible_count = 0
    for raw_tab in cast(list[object], value):
        if type(raw_tab) is not dict:
            return None
        tab = cast(dict[str, object], raw_tab)
        if set(tab) != _TAB_FIELDS:
            return None
        tab_id = tab["id"]
        name = tab["name"]
        visibility = tab["visibility"]
        if (
            _canonical_uuid(tab_id) is None
            or tab["workspace_id"] != workspace_id
            or not isinstance(name, str)
            or not name
            or name in tab_names
            or visibility not in ("visible", "hidden")
            or tab["extensions"] != {}
            or type(tab["extensions"]) is not dict
        ):
            return None
        canonical_tab_id = cast(str, tab_id)
        if canonical_tab_id in tab_ids or canonical_tab_id == workspace_id:
            return None
        tab_ids.add(canonical_tab_id)
        tab_names.add(name)
        if visibility == "visible":
            visible_count += 1
    return (tab_ids, tab_names) if visible_count else None


def _valid_tile(value: object, tab_ids: set[str]) -> bool:
    if type(value) is not dict:
        return False
    tile = cast(dict[str, object], value)
    if set(tile) != _TILE_FIELDS:
        return False
    tab_id = tile["tab_id"]
    return (
        isinstance(tile["name"], str)
        and isinstance(tile["url"], str)
        and isinstance(tab_id, str)
        and tab_id in tab_ids
        and _is_nullable_string(tile["icon"])
        and isinstance(tile["bg"], str)
        and _is_nullable_string(tile["browser"])
        and _is_nullable_string(tile["chrome_profile"])
        and tile["open_target"] in ("tab", "window")
    )


def validate_v1(document: Mapping[str, JsonValue]) -> bool:
    """Return whether ``document`` is the exact strict identity-only v1 shape."""

    try:
        strict_json = _is_strict_json(dict(document), set())
    except RecursionError:
        return False
    if not strict_json:
        return False
    if set(document) != _ROOT_FIELDS:
        return False
    if document["schema_version"] != SCHEMA_VERSION or not _is_int(
        document["schema_version"]
    ):
        return False

    application = _valid_application(document["application"])
    raw_workspaces = document["workspaces"]
    if application is None or type(raw_workspaces) is not list:
        return False
    workspaces = cast(list[object], raw_workspaces)
    if len(workspaces) != 1:
        return False
    workspace = _valid_workspace(workspaces[0])
    if workspace is None:
        return False
    _, default_workspace_id = application
    workspace_id, tab_order = workspace
    if default_workspace_id != workspace_id:
        return False

    tabs = _valid_tabs(document["tabs"], workspace_id)
    if tabs is None:
        return False
    tab_ids, _ = tabs
    if len(tab_order) != len(tab_ids) or set(tab_order) != tab_ids:
        return False

    raw_tiles = document["tiles"]
    if type(raw_tiles) is not list or not all(
        _valid_tile(tile, tab_ids) for tile in cast(list[object], raw_tiles)
    ):
        return False
    if not _is_int(document["columns"]):
        return False
    if type(document["auto_fit"]) is not bool:
        return False
    if not all(
        _is_nullable_int(document[field])
        for field in ("window_x", "window_y", "window_w", "window_h")
    ):
        return False
    return _valid_extensions(document["extensions"])


def validate_v0(document: Mapping[str, JsonValue]) -> bool:
    """Apply the completed shallow legacy validation contract to implicit v0."""

    if "schema_version" in document:
        return False
    try:
        validate_legacy_mapping(cast(dict[str, object], dict(document)))
    except LegacyConstructionFailure:
        return False
    return True


def _canonical_v0(
    document: Mapping[str, JsonValue],
) -> tuple[JsonObject, bytes] | None:
    try:
        serialized = json.dumps(
            dict(document),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
        detached: object = json.loads(serialized)
    except (TypeError, ValueError, OverflowError, UnicodeError):
        return None
    if type(detached) is not dict:
        return None
    return cast(JsonObject, detached), serialized


def _discover_tab_names(source: JsonObject) -> list[str]:
    names: list[str] = []
    seen_names: set[str] = set()
    raw_tabs = source.get("tabs")
    if type(raw_tabs) is list:
        for item in raw_tabs:
            if isinstance(item, str) and item not in seen_names:
                names.append(item)
                seen_names.add(item)

    raw_tiles = source.get("tiles")
    if type(raw_tiles) is list:
        for raw_tile in raw_tiles:
            tile = cast(dict[str, JsonValue], raw_tile)
            tab_name = cast(str, tile.get("tab", DEFAULT_TAB_NAME))
            if tab_name not in seen_names:
                names.append(tab_name)
                seen_names.add(tab_name)
    if not names:
        names.append(DEFAULT_TAB_NAME)
    return names


def _reserved_legacy_ids(source: JsonObject) -> set[str]:
    reserved: set[str] = set()
    raw_tab_ids = source.get("tab_ids")
    if type(raw_tab_ids) is dict:
        for value in raw_tab_ids.values():
            candidate = _canonical_legacy_uuid(value)
            if candidate is not None:
                reserved.add(candidate)
    raw_tab_order = source.get("tab_order")
    if type(raw_tab_order) is list:
        for value in raw_tab_order:
            candidate = _canonical_legacy_uuid(value)
            if candidate is not None:
                reserved.add(candidate)
    return reserved


def _retained_tab_ids(source: JsonObject, names: list[str]) -> dict[str, str]:
    raw_tab_ids = source.get("tab_ids")
    saved_ids = raw_tab_ids if type(raw_tab_ids) is dict else {}
    retained: dict[str, str] = {}
    used: set[str] = set()
    for name in names:
        candidate = _canonical_legacy_uuid(saved_ids.get(name))
        if candidate is not None and candidate not in used:
            retained[name] = candidate
            used.add(candidate)
    return retained


def _ordered_tab_names(
    source: JsonObject,
    discovered: list[str],
    retained: Mapping[str, str],
) -> list[str]:
    title_by_id = {tab_id: title for title, tab_id in retained.items()}
    ordered: list[str] = []
    seen_titles: set[str] = set()
    raw_order = source.get("tab_order")
    if type(raw_order) is list:
        for raw_id in raw_order:
            tab_id = _canonical_legacy_uuid(raw_id)
            title = title_by_id.get(tab_id) if tab_id is not None else None
            if title is not None and title not in seen_titles:
                ordered.append(title)
                seen_titles.add(title)
    for title in discovered:
        if title not in seen_titles:
            ordered.append(title)
            seen_titles.add(title)
    return ordered


def _derived_uuid(
    name: str,
    uuid5_factory: Uuid5Factory,
) -> str | None:
    result = uuid5_factory(NAMESPACE_URL, name)
    if isinstance(result, UUID):
        return str(result) if result.version == 5 else None
    canonical = _canonical_uuid(result)
    if canonical is None or UUID(canonical).version != 5:
        return None
    return canonical


def _allocate_migration_ids(
    source: JsonObject,
    canonical_bytes: bytes,
    ordered_names: list[str],
    retained: Mapping[str, str],
    uuid5_factory: Uuid5Factory,
) -> tuple[str, dict[str, str]] | None:
    digest = hashlib.sha256(canonical_bytes).hexdigest()
    reserved = _reserved_legacy_ids(source)
    workspace_id = _derived_uuid(
        f"{MIGRATION_NAME_ROOT}/{digest}/workspace/0",
        uuid5_factory,
    )
    if workspace_id is None or workspace_id in reserved:
        return None

    used = {workspace_id}
    tab_ids: dict[str, str] = {}
    for ordinal, name in enumerate(ordered_names):
        tab_id = retained.get(name)
        if tab_id is None:
            tab_id = _derived_uuid(
                f"{MIGRATION_NAME_ROOT}/{digest}/tab/{ordinal}",
                uuid5_factory,
            )
            if tab_id is None or tab_id in reserved or tab_id in used:
                return None
        elif tab_id in used:
            return None
        tab_ids[name] = tab_id
        used.add(tab_id)
    return workspace_id, tab_ids


def _migrated_tiles(source: JsonObject, tab_ids: Mapping[str, str]) -> list[JsonValue]:
    migrated: list[JsonValue] = []
    raw_tiles = source.get("tiles")
    if type(raw_tiles) is not list:
        return migrated
    for raw_tile in raw_tiles:
        tile = cast(dict[str, JsonValue], raw_tile)
        tab_name = cast(str, tile.get("tab", DEFAULT_TAB_NAME))
        migrated.append(
            {
                "name": cast(str, tile["name"]),
                "url": cast(str, tile["url"]),
                "tab_id": tab_ids[tab_name],
                "icon": tile.get("icon"),
                "bg": tile.get("bg", "#F5F6FA"),
                "browser": tile.get("browser"),
                "chrome_profile": tile.get("chrome_profile"),
                "open_target": tile.get("open_target", "tab"),
            }
        )
    return migrated


def migrate_v0_to_v1(
    document: Mapping[str, JsonValue],
    *,
    uuid5_factory: Uuid5Factory = uuid5,
) -> JsonObject | None:
    """Return the exact deterministic identity-v1 candidate, or reject v0."""

    if not validate_v0(document):
        return None
    canonical = _canonical_v0(document)
    if canonical is None:
        return None
    source, canonical_bytes = canonical
    discovered = _discover_tab_names(source)
    if any(name == "" for name in discovered):
        return None
    retained = _retained_tab_ids(source, discovered)
    ordered_names = _ordered_tab_names(source, discovered, retained)
    allocated = _allocate_migration_ids(
        source,
        canonical_bytes,
        ordered_names,
        retained,
        uuid5_factory,
    )
    if allocated is None:
        return None
    workspace_id, tab_ids = allocated

    hidden_names: list[str] = []
    seen_hidden_names: set[str] = set()
    raw_hidden = source.get("hidden_tabs")
    if type(raw_hidden) is list:
        for name in raw_hidden:
            if (
                isinstance(name, str)
                and name in tab_ids
                and name not in seen_hidden_names
            ):
                hidden_names.append(name)
                seen_hidden_names.add(name)
    if len(hidden_names) == len(ordered_names):
        hidden_names.remove(ordered_names[0])
    hidden = set(hidden_names)

    unknown = {
        key: value for key, value in source.items() if key not in _RECOGNIZED_V0_FIELDS
    }
    extensions: JsonObject = {LEGACY_EXTENSION_NAMESPACE: unknown} if unknown else {}
    candidate: JsonObject = {
        "schema_version": SCHEMA_VERSION,
        "application": {
            "title": source.get("title", LEGACY_APPLICATION_TITLE),
            "default_workspace_id": workspace_id,
            "extensions": {},
        },
        "workspaces": [
            {
                "id": workspace_id,
                "name": DEFAULT_WORKSPACE_NAME,
                "tab_order": [tab_ids[name] for name in ordered_names],
                "extensions": {},
            }
        ],
        "tabs": [
            {
                "id": tab_ids[name],
                "workspace_id": workspace_id,
                "name": name,
                "visibility": "hidden" if name in hidden else "visible",
                "extensions": {},
            }
            for name in ordered_names
        ],
        "tiles": _migrated_tiles(source, tab_ids),
        "columns": source.get("columns", 5),
        "auto_fit": source.get("auto_fit", True),
        "window_x": source.get("window_x"),
        "window_y": source.get("window_y"),
        "window_w": source.get("window_w"),
        "window_h": source.get("window_h"),
        "extensions": extensions,
    }
    return candidate


def _allocator_uuid4(value: object) -> str | None:
    if isinstance(value, UUID):
        parsed = value
    elif isinstance(value, str):
        canonical = _canonical_uuid(value)
        if canonical is None:
            return None
        parsed = UUID(canonical)
    else:
        return None
    return str(parsed) if parsed.version == 4 else None


def _allocate_native_id(
    allocator: Uuid4Allocator,
    blocked: set[str],
) -> str:
    for _ in range(NATIVE_ID_ALLOCATION_ATTEMPTS):
        try:
            candidate = _allocator_uuid4(allocator())
        except Exception:
            raise NativeV1ConstructionError from None
        if candidate is not None and candidate not in blocked:
            return candidate
    raise NativeV1ConstructionError


def build_native_v1(id_allocator: Uuid4Allocator) -> JsonObject:
    """Build the friendly native v1 document with two injected UUIDv4 IDs."""

    workspace_id = _allocate_native_id(id_allocator, set())
    tab_id = _allocate_native_id(id_allocator, {workspace_id})
    candidate: JsonObject = {
        "schema_version": SCHEMA_VERSION,
        "application": {
            "title": DEFAULT_APPLICATION_TITLE,
            "default_workspace_id": workspace_id,
            "extensions": {},
        },
        "workspaces": [
            {
                "id": workspace_id,
                "name": DEFAULT_WORKSPACE_NAME,
                "tab_order": [tab_id],
                "extensions": {},
            }
        ],
        "tabs": [
            {
                "id": tab_id,
                "workspace_id": workspace_id,
                "name": DEFAULT_TAB_NAME,
                "visibility": "visible",
                "extensions": {},
            }
        ],
        "tiles": [
            {
                "name": "ChatGPT",
                "url": "https://chat.openai.com",
                "tab_id": tab_id,
                "icon": None,
                "bg": "#F5F6FA",
                "browser": None,
                "chrome_profile": None,
                "open_target": "tab",
            },
            {
                "name": "Gmail",
                "url": "https://mail.google.com",
                "tab_id": tab_id,
                "icon": None,
                "bg": "#F5F6FA",
                "browser": None,
                "chrome_profile": None,
                "open_target": "tab",
            },
            {
                "name": "Notion",
                "url": "https://www.notion.so",
                "tab_id": tab_id,
                "icon": None,
                "bg": "#F5F6FA",
                "browser": None,
                "chrome_profile": None,
                "open_target": "tab",
            },
        ],
        "columns": 5,
        "auto_fit": True,
        "window_x": None,
        "window_y": None,
        "window_w": None,
        "window_h": None,
        "extensions": {},
    }
    if not validate_v1(candidate):
        raise NativeV1ConstructionError
    return candidate


__all__ = [
    "DEFAULT_APPLICATION_TITLE",
    "DEFAULT_TAB_NAME",
    "DEFAULT_WORKSPACE_NAME",
    "JsonObject",
    "JsonScalar",
    "JsonValue",
    "LEGACY_APPLICATION_TITLE",
    "LEGACY_EXTENSION_NAMESPACE",
    "MIGRATION_NAME_ROOT",
    "NATIVE_ID_ALLOCATION_ATTEMPTS",
    "NativeV1ConstructionError",
    "SCHEMA_VERSION",
    "Uuid4Allocator",
    "Uuid5Factory",
    "build_native_v1",
    "migrate_v0_to_v1",
    "validate_v0",
    "validate_v1",
]
