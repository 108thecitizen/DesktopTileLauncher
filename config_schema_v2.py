# SPDX-License-Identifier: Apache-2.0
"""Dormant, Qt-free schema-version 2 wire types and strict validation.

This module deliberately does not parse files, register schema support, allocate
identities, migrate state, or import production startup and persistence code.
The object-pairs callback is a dormant capability for a later parser boundary;
``validate_v2`` accepts only an already-decoded mapping.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal, TypeAlias, TypedDict, cast
from uuid import UUID

SCHEMA_VERSION_V2: Final = 2

JsonScalar: TypeAlias = None | bool | int | float | str
StrictJsonValue: TypeAlias = (
    JsonScalar | list["StrictJsonValue"] | dict[str, "StrictJsonValue"]
)
JsonObject: TypeAlias = dict[str, StrictJsonValue]
Extensions: TypeAlias = dict[str, StrictJsonValue]
EntityUUID: TypeAlias = str
ExternalDeviceKey: TypeAlias = str

Visibility: TypeAlias = Literal["visible", "hidden"]
Lifecycle: TypeAlias = Literal["active", "archived"]
ViewMode: TypeAlias = Literal["display", "kanban"]
WorkflowStatus: TypeAlias = Literal["new", "in_use", "archived"]
OpenTarget: TypeAlias = Literal["tab", "window"]


class Application(TypedDict):
    """Closed schema-v2 Application wire shape."""

    title: str
    default_workspace_id: EntityUUID
    extensions: Extensions


class Workspace(TypedDict):
    """Closed schema-v2 Workspace wire shape."""

    id: EntityUUID
    name: str
    tab_order: list[EntityUUID]
    extensions: Extensions


class KanbanOrder(TypedDict):
    """Closed schema-v2 per-status Kanban queues."""

    new: list[EntityUUID]
    in_use: list[EntityUUID]
    archived: list[EntityUUID]


class Tab(TypedDict):
    """Closed schema-v2 Tab wire shape."""

    id: EntityUUID
    workspace_id: EntityUUID
    name: str
    visibility: Visibility
    lifecycle: Lifecycle
    view_mode: ViewMode
    display_filter: list[WorkflowStatus]
    display_order: list[EntityUUID]
    kanban_order: KanbanOrder
    extensions: Extensions


class UrlTarget(TypedDict):
    """Closed opaque URL target."""

    url: str


class LegacyStringIcon(TypedDict):
    """Closed opaque legacy icon value."""

    kind: Literal["legacy_string"]
    value: str


class Resource(TypedDict):
    """Closed URL-only schema-v2 Resource wire shape."""

    id: EntityUUID
    kind: Literal["url"]
    target: UrlTarget
    default_label: str
    default_icon: LegacyStringIcon | None
    extensions: Extensions


class Placement(TypedDict):
    """Closed schema-v2 Placement wire shape."""

    id: EntityUUID
    resource_id: EntityUUID
    tab_id: EntityUUID
    label_override: str | None
    icon_override: LegacyStringIcon | None
    background_color: str
    workflow_status: WorkflowStatus
    extensions: Extensions


class PortableFallback(TypedDict):
    """Closed portable-fallback applicability selector."""

    kind: Literal["portable_fallback"]


class DeviceSpecific(TypedDict):
    """Closed exact-device applicability selector."""

    kind: Literal["device_specific"]
    device_key: ExternalDeviceKey


DeviceApplicability: TypeAlias = PortableFallback | DeviceSpecific


class WindowSettings(TypedDict):
    """Closed Workspace/window settings."""

    columns: int
    auto_fit: bool
    window_x: int | None
    window_y: int | None
    window_w: int | None
    window_h: int | None


class UrlLaunchSettings(TypedDict):
    """Closed Placement/launch settings."""

    browser: str | None
    chrome_profile: str | None
    open_target: OpenTarget


class WorkspaceWindowBinding(TypedDict):
    """Closed Workspace/window DeviceBinding variant."""

    id: EntityUUID
    subject_kind: Literal["workspace"]
    subject_id: EntityUUID
    binding_kind: Literal["window"]
    applicability: DeviceApplicability
    settings: WindowSettings
    extensions: Extensions


class PlacementLaunchBinding(TypedDict):
    """Closed Placement/launch DeviceBinding variant."""

    id: EntityUUID
    subject_kind: Literal["placement"]
    subject_id: EntityUUID
    binding_kind: Literal["launch"]
    applicability: DeviceApplicability
    settings: UrlLaunchSettings
    extensions: Extensions


DeviceBinding: TypeAlias = WorkspaceWindowBinding | PlacementLaunchBinding


class Root(TypedDict):
    """Complete closed schema-v2 root wire shape."""

    schema_version: Literal[2]
    application: Application
    workspaces: list[Workspace]
    tabs: list[Tab]
    resources: list[Resource]
    placements: list[Placement]
    device_bindings: list[DeviceBinding]
    extensions: Extensions


class DuplicateJsonMemberError(ValueError):
    """A JSON object repeated a decoded member name at some nesting depth."""

    def __init__(self) -> None:
        super().__init__("malformed_json")


def reject_duplicate_json_members(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Build one object or reject a duplicate without exposing its key or value.

    This function is suitable as ``json.loads(..., object_pairs_hook=...)`` but
    intentionally performs no decoding itself and is not wired into production.
    """

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonMemberError
        result[key] = value
    return result


_ROOT_FIELDS: Final = frozenset(
    {
        "schema_version",
        "application",
        "workspaces",
        "tabs",
        "resources",
        "placements",
        "device_bindings",
        "extensions",
    }
)
_APPLICATION_FIELDS: Final = frozenset({"title", "default_workspace_id", "extensions"})
_WORKSPACE_FIELDS: Final = frozenset({"id", "name", "tab_order", "extensions"})
_TAB_FIELDS: Final = frozenset(
    {
        "id",
        "workspace_id",
        "name",
        "visibility",
        "lifecycle",
        "view_mode",
        "display_filter",
        "display_order",
        "kanban_order",
        "extensions",
    }
)
_KANBAN_FIELDS: Final = frozenset({"new", "in_use", "archived"})
_URL_TARGET_FIELDS: Final = frozenset({"url"})
_LEGACY_ICON_FIELDS: Final = frozenset({"kind", "value"})
_RESOURCE_FIELDS: Final = frozenset(
    {"id", "kind", "target", "default_label", "default_icon", "extensions"}
)
_PLACEMENT_FIELDS: Final = frozenset(
    {
        "id",
        "resource_id",
        "tab_id",
        "label_override",
        "icon_override",
        "background_color",
        "workflow_status",
        "extensions",
    }
)
_DEVICE_BINDING_FIELDS: Final = frozenset(
    {
        "id",
        "subject_kind",
        "subject_id",
        "binding_kind",
        "applicability",
        "settings",
        "extensions",
    }
)
_PORTABLE_FALLBACK_FIELDS: Final = frozenset({"kind"})
_DEVICE_SPECIFIC_FIELDS: Final = frozenset({"kind", "device_key"})
_WINDOW_SETTINGS_FIELDS: Final = frozenset(
    {
        "columns",
        "auto_fit",
        "window_x",
        "window_y",
        "window_w",
        "window_h",
    }
)
_URL_LAUNCH_SETTINGS_FIELDS: Final = frozenset(
    {"browser", "chrome_profile", "open_target"}
)
_DISPLAY_FILTER_VALUES: Final = frozenset(
    {
        (),
        ("new",),
        ("in_use",),
        ("archived",),
        ("new", "in_use"),
        ("new", "archived"),
        ("in_use", "archived"),
        ("new", "in_use", "archived"),
    }
)
_WORKFLOW_STATUSES: Final = ("new", "in_use", "archived")


def _is_utf8_text(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _is_strict_json(value: object) -> bool:
    """Validate a finite, UTF-8-encodable JSON tree without a depth limit."""

    active: set[int] = set()
    stack: list[tuple[object, bool]] = [(value, False)]
    while stack:
        current, leaving = stack.pop()
        value_type = type(current)
        if leaving:
            active.remove(id(current))
            continue
        if current is None or value_type in (bool, int):
            continue
        if value_type is float:
            if not math.isfinite(cast(float, current)):
                return False
            continue
        if value_type is str:
            if not _is_utf8_text(cast(str, current)):
                return False
            continue
        if value_type is list:
            marker = id(current)
            if marker in active:
                return False
            active.add(marker)
            stack.append((current, True))
            for item in reversed(cast(list[object], current)):
                stack.append((item, False))
            continue
        if value_type is dict:
            marker = id(current)
            if marker in active:
                return False
            mapping = cast(dict[object, object], current)
            for key in mapping:
                if type(key) is not str or not _is_utf8_text(key):
                    return False
            active.add(marker)
            stack.append((current, True))
            for item in reversed(list(mapping.values())):
                stack.append((item, False))
            continue
        return False
    return True


def _object(value: object) -> dict[str, object] | None:
    if type(value) is not dict:
        return None
    return cast(dict[str, object], value)


def _array(value: object) -> list[object] | None:
    if type(value) is not list:
        return None
    return cast(list[object], value)


def _closed(value: object, fields: frozenset[str]) -> dict[str, object] | None:
    mapping = _object(value)
    if mapping is None or set(mapping) != fields:
        return None
    return mapping


def _is_string(value: object) -> bool:
    return type(value) is str


def _is_nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value)


def _is_nullable_string(value: object) -> bool:
    return value is None or type(value) is str


def _is_exact_int(value: object) -> bool:
    return type(value) is int


def _is_nullable_int(value: object) -> bool:
    return value is None or _is_exact_int(value)


def _is_string_array(value: object) -> bool:
    array = _array(value)
    return array is not None and all(type(item) is str for item in array)


def _is_extensions(value: object) -> bool:
    return type(value) is dict


def _is_application(value: object) -> bool:
    application = _closed(value, _APPLICATION_FIELDS)
    return application is not None and all(
        (
            _is_string(application["title"]),
            _is_string(application["default_workspace_id"]),
            _is_extensions(application["extensions"]),
        )
    )


def _is_workspace(value: object) -> bool:
    workspace = _closed(value, _WORKSPACE_FIELDS)
    return workspace is not None and all(
        (
            _is_string(workspace["id"]),
            _is_nonempty_string(workspace["name"]),
            _is_string_array(workspace["tab_order"]),
            _is_extensions(workspace["extensions"]),
        )
    )


def _is_kanban_order(value: object) -> bool:
    order = _closed(value, _KANBAN_FIELDS)
    return order is not None and all(
        _is_string_array(order[status]) for status in _WORKFLOW_STATUSES
    )


def _is_tab(value: object) -> bool:
    tab = _closed(value, _TAB_FIELDS)
    if tab is None:
        return False
    return all(
        (
            _is_string(tab["id"]),
            _is_string(tab["workspace_id"]),
            _is_nonempty_string(tab["name"]),
            type(tab["visibility"]) is str
            and tab["visibility"] in ("visible", "hidden"),
            type(tab["lifecycle"]) is str
            and tab["lifecycle"] in ("active", "archived"),
            type(tab["view_mode"]) is str and tab["view_mode"] in ("display", "kanban"),
            _is_string_array(tab["display_filter"]),
            _is_string_array(tab["display_order"]),
            _is_kanban_order(tab["kanban_order"]),
            _is_extensions(tab["extensions"]),
        )
    )


def _is_url_target(value: object) -> bool:
    target = _closed(value, _URL_TARGET_FIELDS)
    return target is not None and _is_string(target["url"])


def _is_legacy_icon(value: object) -> bool:
    icon = _closed(value, _LEGACY_ICON_FIELDS)
    return (
        icon is not None
        and type(icon["kind"]) is str
        and icon["kind"] == "legacy_string"
        and _is_string(icon["value"])
    )


def _is_nullable_icon(value: object) -> bool:
    return value is None or _is_legacy_icon(value)


def _is_resource(value: object) -> bool:
    resource = _closed(value, _RESOURCE_FIELDS)
    return resource is not None and all(
        (
            _is_string(resource["id"]),
            type(resource["kind"]) is str and resource["kind"] == "url",
            _is_url_target(resource["target"]),
            _is_string(resource["default_label"]),
            _is_nullable_icon(resource["default_icon"]),
            _is_extensions(resource["extensions"]),
        )
    )


def _is_placement(value: object) -> bool:
    placement = _closed(value, _PLACEMENT_FIELDS)
    return placement is not None and all(
        (
            _is_string(placement["id"]),
            _is_string(placement["resource_id"]),
            _is_string(placement["tab_id"]),
            _is_nullable_string(placement["label_override"]),
            _is_nullable_icon(placement["icon_override"]),
            _is_string(placement["background_color"]),
            type(placement["workflow_status"]) is str
            and placement["workflow_status"] in _WORKFLOW_STATUSES,
            _is_extensions(placement["extensions"]),
        )
    )


def _is_applicability(value: object) -> bool:
    applicability = _object(value)
    if applicability is None:
        return False
    kind = applicability.get("kind")
    if type(kind) is not str:
        return False
    if kind == "portable_fallback":
        return set(applicability) == _PORTABLE_FALLBACK_FIELDS
    if kind == "device_specific":
        return set(applicability) == _DEVICE_SPECIFIC_FIELDS and _is_nonempty_string(
            applicability["device_key"]
        )
    return False


def _is_window_settings(value: object) -> bool:
    settings = _closed(value, _WINDOW_SETTINGS_FIELDS)
    return settings is not None and all(
        (
            _is_exact_int(settings["columns"]),
            type(settings["auto_fit"]) is bool,
            _is_nullable_int(settings["window_x"]),
            _is_nullable_int(settings["window_y"]),
            _is_nullable_int(settings["window_w"]),
            _is_nullable_int(settings["window_h"]),
        )
    )


def _is_url_launch_settings(value: object) -> bool:
    settings = _closed(value, _URL_LAUNCH_SETTINGS_FIELDS)
    return settings is not None and all(
        (
            _is_nullable_string(settings["browser"]),
            _is_nullable_string(settings["chrome_profile"]),
            type(settings["open_target"]) is str
            and settings["open_target"] in ("tab", "window"),
        )
    )


def _is_device_binding(value: object) -> bool:
    binding = _closed(value, _DEVICE_BINDING_FIELDS)
    if binding is None or not all(
        (
            _is_string(binding["id"]),
            _is_string(binding["subject_id"]),
            _is_applicability(binding["applicability"]),
            _is_extensions(binding["extensions"]),
        )
    ):
        return False
    subject_kind = binding["subject_kind"]
    binding_kind = binding["binding_kind"]
    if (
        type(subject_kind) is str
        and subject_kind == "workspace"
        and type(binding_kind) is str
        and binding_kind == "window"
    ):
        return _is_window_settings(binding["settings"])
    if (
        type(subject_kind) is str
        and subject_kind == "placement"
        and type(binding_kind) is str
        and binding_kind == "launch"
    ):
        return _is_url_launch_settings(binding["settings"])
    return False


@dataclass(frozen=True, slots=True)
class _LocalState:
    root: dict[str, object]
    application: dict[str, object]
    workspaces: list[dict[str, object]]
    tabs: list[dict[str, object]]
    resources: list[dict[str, object]]
    placements: list[dict[str, object]]
    device_bindings: list[dict[str, object]]


def _typed_objects(
    value: object,
    validator: Callable[[object], bool],
) -> list[dict[str, object]] | None:
    array = _array(value)
    if array is None:
        return None
    if not all(validator(item) for item in array):
        return None
    return [cast(dict[str, object], item) for item in array]


def _validate_local_shapes(document: object) -> _LocalState | None:
    root = _closed(document, _ROOT_FIELDS)
    if root is None:
        return None
    if (
        not _is_exact_int(root["schema_version"])
        or root["schema_version"] != SCHEMA_VERSION_V2
        or not _is_application(root["application"])
        or not _is_extensions(root["extensions"])
    ):
        return None
    workspaces = _typed_objects(root["workspaces"], _is_workspace)
    tabs = _typed_objects(root["tabs"], _is_tab)
    resources = _typed_objects(root["resources"], _is_resource)
    placements = _typed_objects(root["placements"], _is_placement)
    device_bindings = _typed_objects(
        root["device_bindings"],
        _is_device_binding,
    )
    if (
        workspaces is None
        or not workspaces
        or tabs is None
        or not tabs
        or resources is None
        or placements is None
        or device_bindings is None
    ):
        return None
    return _LocalState(
        root,
        cast(dict[str, object], root["application"]),
        workspaces,
        tabs,
        resources,
        placements,
        device_bindings,
    )


def _valid_display_filters(tabs: list[dict[str, object]]) -> bool:
    return all(
        tuple(cast(list[str], tab["display_filter"])) in _DISPLAY_FILTER_VALUES
        for tab in tabs
    )


def _canonical_uuid(value: object) -> str | None:
    if type(value) is not str:
        return None
    text = value
    try:
        parsed = UUID(text)
    except ValueError:
        return None
    canonical = str(parsed)
    return canonical if text == canonical else None


@dataclass(frozen=True, slots=True)
class _Indexes:
    workspaces: dict[str, dict[str, object]]
    tabs: dict[str, dict[str, object]]
    resources: dict[str, dict[str, object]]
    placements: dict[str, dict[str, object]]
    device_bindings: dict[str, dict[str, object]]


def _definition_index(
    definitions: list[dict[str, object]],
    used: set[str],
) -> dict[str, dict[str, object]] | None:
    index: dict[str, dict[str, object]] = {}
    for definition in definitions:
        entity_id = _canonical_uuid(definition["id"])
        if entity_id is None or entity_id in used:
            return None
        used.add(entity_id)
        index[entity_id] = definition
    return index


def _reference_values(state: _LocalState) -> list[object]:
    values: list[object] = [state.application["default_workspace_id"]]
    for workspace in state.workspaces:
        values.extend(cast(list[object], workspace["tab_order"]))
    for tab in state.tabs:
        values.append(tab["workspace_id"])
        values.extend(cast(list[object], tab["display_order"]))
        kanban = cast(dict[str, object], tab["kanban_order"])
        for status in _WORKFLOW_STATUSES:
            values.extend(cast(list[object], kanban[status]))
    for placement in state.placements:
        values.extend((placement["resource_id"], placement["tab_id"]))
    values.extend(binding["subject_id"] for binding in state.device_bindings)
    return values


def _validate_identities(state: _LocalState) -> _Indexes | None:
    used: set[str] = set()
    workspaces = _definition_index(state.workspaces, used)
    tabs = _definition_index(state.tabs, used)
    resources = _definition_index(state.resources, used)
    placements = _definition_index(state.placements, used)
    device_bindings = _definition_index(state.device_bindings, used)
    if any(
        index is None
        for index in (workspaces, tabs, resources, placements, device_bindings)
    ):
        return None
    if any(_canonical_uuid(value) is None for value in _reference_values(state)):
        return None
    return _Indexes(
        cast(dict[str, dict[str, object]], workspaces),
        cast(dict[str, dict[str, object]], tabs),
        cast(dict[str, dict[str, object]], resources),
        cast(dict[str, dict[str, object]], placements),
        cast(dict[str, dict[str, object]], device_bindings),
    )


def _validate_references(state: _LocalState, indexes: _Indexes) -> bool:
    if state.application["default_workspace_id"] not in indexes.workspaces:
        return False
    for tab in state.tabs:
        if tab["workspace_id"] not in indexes.workspaces:
            return False
    for placement in state.placements:
        if (
            placement["tab_id"] not in indexes.tabs
            or placement["resource_id"] not in indexes.resources
        ):
            return False
    for binding in state.device_bindings:
        subject_id = cast(str, binding["subject_id"])
        subject_kind = binding["subject_kind"]
        if subject_kind == "workspace":
            if subject_id not in indexes.workspaces:
                return False
        elif subject_kind == "placement":
            if subject_id not in indexes.placements:
                return False
        else:
            return False
    return True


def _exact_unique_members(actual: list[str], expected: set[str]) -> bool:
    return len(actual) == len(set(actual)) and set(actual) == expected


def _validate_definition_orders(state: _LocalState) -> bool:
    tabs_by_workspace: dict[str, set[str]] = {
        cast(str, workspace["id"]): set() for workspace in state.workspaces
    }
    for tab in state.tabs:
        tabs_by_workspace[cast(str, tab["workspace_id"])].add(cast(str, tab["id"]))
    for workspace in state.workspaces:
        workspace_id = cast(str, workspace["id"])
        order = cast(list[str], workspace["tab_order"])
        if not _exact_unique_members(order, tabs_by_workspace[workspace_id]):
            return False

    placements_by_tab: dict[str, set[str]] = {
        cast(str, tab["id"]): set() for tab in state.tabs
    }
    for placement in state.placements:
        placements_by_tab[cast(str, placement["tab_id"])].add(
            cast(str, placement["id"])
        )
    for tab in state.tabs:
        tab_id = cast(str, tab["id"])
        order = cast(list[str], tab["display_order"])
        if not _exact_unique_members(order, placements_by_tab[tab_id]):
            return False
    return True


def _validate_names_and_default(state: _LocalState) -> bool:
    workspace_names = [cast(str, workspace["name"]) for workspace in state.workspaces]
    if len(workspace_names) != len(set(workspace_names)):
        return False

    tab_names_by_workspace: dict[str, set[str]] = {
        cast(str, workspace["id"]): set() for workspace in state.workspaces
    }
    for tab in state.tabs:
        owner_id = cast(str, tab["workspace_id"])
        name = cast(str, tab["name"])
        if name in tab_names_by_workspace[owner_id]:
            return False
        tab_names_by_workspace[owner_id].add(name)

    default_workspace_id = state.application["default_workspace_id"]
    return any(
        tab["workspace_id"] == default_workspace_id
        and tab["lifecycle"] == "active"
        and tab["visibility"] == "visible"
        for tab in state.tabs
    )


def _validate_kanban_references(
    state: _LocalState,
    indexes: _Indexes,
) -> bool:
    for tab in state.tabs:
        tab_id = tab["id"]
        kanban = cast(dict[str, object], tab["kanban_order"])
        for status in _WORKFLOW_STATUSES:
            for placement_id in cast(list[str], kanban[status]):
                placement = indexes.placements.get(placement_id)
                if placement is None or placement["tab_id"] != tab_id:
                    return False
    return True


def _validate_kanban_membership(state: _LocalState) -> bool:
    placements_by_tab: dict[str, set[str]] = {
        cast(str, tab["id"]): set() for tab in state.tabs
    }
    placement_status: dict[str, str] = {}
    for placement in state.placements:
        placement_id = cast(str, placement["id"])
        placements_by_tab[cast(str, placement["tab_id"])].add(placement_id)
        placement_status[placement_id] = cast(str, placement["workflow_status"])

    for tab in state.tabs:
        tab_id = cast(str, tab["id"])
        kanban = cast(dict[str, object], tab["kanban_order"])
        represented: set[str] = set()
        for status in _WORKFLOW_STATUSES:
            queue = cast(list[str], kanban[status])
            queue_set = set(queue)
            if (
                len(queue) != len(queue_set)
                or represented.intersection(queue_set)
                or any(placement_status[item] != status for item in queue)
            ):
                return False
            represented.update(queue_set)
        if represented != placements_by_tab[tab_id]:
            return False
    return True


def _applicability_selector(applicability: dict[str, object]) -> tuple[str, str]:
    kind = cast(str, applicability["kind"])
    if kind == "portable_fallback":
        return kind, ""
    return kind, cast(str, applicability["device_key"])


def _validate_binding_selectors(state: _LocalState) -> bool:
    selectors: set[tuple[str, str, str, str, str]] = set()
    for binding in state.device_bindings:
        applicability = cast(dict[str, object], binding["applicability"])
        applicability_kind, device_key = _applicability_selector(applicability)
        selector = (
            cast(str, binding["subject_kind"]),
            cast(str, binding["subject_id"]),
            cast(str, binding["binding_kind"]),
            applicability_kind,
            device_key,
        )
        if selector in selectors:
            return False
        selectors.add(selector)
    return True


def validate_v2(document: object) -> bool:
    """Return whether *document* is the complete strict schema-v2 graph.

    Validation is reject-only and does not normalize, repair, sort, deduplicate,
    allocate, or mutate any value.
    """

    if not _is_strict_json(document):
        return False
    state = _validate_local_shapes(document)
    if state is None or not _valid_display_filters(state.tabs):
        return False
    indexes = _validate_identities(state)
    if indexes is None or not _validate_references(state, indexes):
        return False
    if not _validate_definition_orders(state):
        return False
    if not _validate_names_and_default(state):
        return False
    if not _validate_kanban_references(state, indexes):
        return False
    if not _validate_kanban_membership(state):
        return False
    return _validate_binding_selectors(state)


__all__ = [
    "Application",
    "DeviceApplicability",
    "DeviceBinding",
    "DeviceSpecific",
    "DuplicateJsonMemberError",
    "EntityUUID",
    "Extensions",
    "ExternalDeviceKey",
    "JsonObject",
    "JsonScalar",
    "KanbanOrder",
    "LegacyStringIcon",
    "Lifecycle",
    "OpenTarget",
    "Placement",
    "PlacementLaunchBinding",
    "PortableFallback",
    "Resource",
    "Root",
    "SCHEMA_VERSION_V2",
    "StrictJsonValue",
    "Tab",
    "UrlLaunchSettings",
    "UrlTarget",
    "ViewMode",
    "Visibility",
    "WindowSettings",
    "WorkflowStatus",
    "Workspace",
    "WorkspaceWindowBinding",
    "reject_duplicate_json_members",
    "validate_v2",
]
