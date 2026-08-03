# SPDX-License-Identifier: Apache-2.0
"""Dormant, Qt-free runtime projections for validated schema-version 2 state.

This module resolves the complete persisted graph into immutable values needed
by the existing URL launcher and metadata-refresh behavior.  It performs no
schema registration, startup integration, persistence, discovery, platform,
filesystem, network, or GUI work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias, cast

import config_schema_v2 as v2

RuntimeAdapterFailureCategory: TypeAlias = Literal[
    "invalid_graph",
    "invalid_device_key",
    "subject_not_found",
    "refresh_failure",
]


@dataclass(frozen=True, slots=True)
class RuntimeAdapterRejected:
    """A content-free rejection from a runtime-adapter boundary."""

    category: RuntimeAdapterFailureCategory


@dataclass(frozen=True, slots=True)
class WindowSettingsProjection:
    """One selected Workspace/window settings object."""

    columns: int = field(repr=False)
    auto_fit: bool = field(repr=False)
    window_x: int | None = field(repr=False)
    window_y: int | None = field(repr=False)
    window_w: int | None = field(repr=False)
    window_h: int | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class LaunchSettingsProjection:
    """One selected Placement/launch settings object."""

    browser: str | None = field(repr=False)
    chrome_profile: str | None = field(repr=False)
    open_target: v2.OpenTarget = field(repr=False)


@dataclass(frozen=True, slots=True)
class PlacementProjection:
    """Resolved URL presentation and optional launch settings for a Placement."""

    id: v2.EntityUUID = field(repr=False)
    resource_id: v2.EntityUUID = field(repr=False)
    tab_id: v2.EntityUUID = field(repr=False)
    url: str = field(repr=False)
    label: str = field(repr=False)
    icon: str | None = field(repr=False)
    background_color: str = field(repr=False)
    workflow_status: v2.WorkflowStatus = field(repr=False)
    launch_settings: LaunchSettingsProjection | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class TabProjection:
    """One Tab with complete Display membership and its filtered projection."""

    id: v2.EntityUUID = field(repr=False)
    workspace_id: v2.EntityUUID = field(repr=False)
    name: str = field(repr=False)
    visibility: v2.Visibility = field(repr=False)
    lifecycle: v2.Lifecycle = field(repr=False)
    view_mode: v2.ViewMode = field(repr=False)
    display_filter: tuple[v2.WorkflowStatus, ...] = field(repr=False)
    placements: tuple[PlacementProjection, ...] = field(repr=False)
    displayed_placements: tuple[PlacementProjection, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class WorkspaceProjection:
    """One Workspace in semantic Tab order with normal-bar membership."""

    application_title: str = field(repr=False)
    id: v2.EntityUUID = field(repr=False)
    name: str = field(repr=False)
    tabs: tuple[TabProjection, ...] = field(repr=False)
    normal_tabs: tuple[TabProjection, ...] = field(repr=False)
    window_settings: WindowSettingsProjection | None = field(repr=False)


WorkspaceProjectionResult: TypeAlias = WorkspaceProjection | RuntimeAdapterRejected
MetadataRefreshResult: TypeAlias = v2.Root | RuntimeAdapterRejected
_JsonContainer: TypeAlias = list[v2.StrictJsonValue] | dict[str, v2.StrictJsonValue]


@dataclass(frozen=True, slots=True)
class _BindingIndexes:
    window_fallbacks: dict[v2.EntityUUID, v2.WindowSettings] = field(repr=False)
    window_devices: dict[
        tuple[v2.EntityUUID, v2.ExternalDeviceKey], v2.WindowSettings
    ] = field(repr=False)
    launch_fallbacks: dict[v2.EntityUUID, v2.UrlLaunchSettings] = field(repr=False)
    launch_devices: dict[
        tuple[v2.EntityUUID, v2.ExternalDeviceKey], v2.UrlLaunchSettings
    ] = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Graph:
    root: v2.Root = field(repr=False)
    workspaces: dict[v2.EntityUUID, v2.Workspace] = field(repr=False)
    tabs: dict[v2.EntityUUID, v2.Tab] = field(repr=False)
    resources: dict[v2.EntityUUID, v2.Resource] = field(repr=False)
    placements: dict[v2.EntityUUID, v2.Placement] = field(repr=False)
    bindings: _BindingIndexes = field(repr=False)


def _index_bindings(root: v2.Root) -> _BindingIndexes:
    window_fallbacks: dict[v2.EntityUUID, v2.WindowSettings] = {}
    window_devices: dict[
        tuple[v2.EntityUUID, v2.ExternalDeviceKey], v2.WindowSettings
    ] = {}
    launch_fallbacks: dict[v2.EntityUUID, v2.UrlLaunchSettings] = {}
    launch_devices: dict[
        tuple[v2.EntityUUID, v2.ExternalDeviceKey], v2.UrlLaunchSettings
    ] = {}
    for binding in root["device_bindings"]:
        applicability = binding["applicability"]
        if binding["subject_kind"] == "workspace":
            if applicability["kind"] == "portable_fallback":
                window_fallbacks[binding["subject_id"]] = binding["settings"]
            else:
                window_devices[(binding["subject_id"], applicability["device_key"])] = (
                    binding["settings"]
                )
        elif applicability["kind"] == "portable_fallback":
            launch_fallbacks[binding["subject_id"]] = binding["settings"]
        else:
            launch_devices[(binding["subject_id"], applicability["device_key"])] = (
                binding["settings"]
            )
    return _BindingIndexes(
        window_fallbacks,
        window_devices,
        launch_fallbacks,
        launch_devices,
    )


def _validated_graph(document: object) -> _Graph | None:
    if not v2.validate_v2(document):
        return None
    root = cast(v2.Root, document)
    return _Graph(
        root=root,
        workspaces={item["id"]: item for item in root["workspaces"]},
        tabs={item["id"]: item for item in root["tabs"]},
        resources={item["id"]: item for item in root["resources"]},
        placements={item["id"]: item for item in root["placements"]},
        bindings=_index_bindings(root),
    )


def _is_utf8_text(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _clone_strict_json(value: v2.StrictJsonValue) -> v2.StrictJsonValue:
    """Clone an exact built-in JSON tree without using the Python call stack."""

    if type(value) is list:
        cloned: _JsonContainer = []
    elif type(value) is dict:
        cloned = {}
    else:
        return value

    pending: list[tuple[_JsonContainer, _JsonContainer]] = [(value, cloned)]
    while pending:
        source, target = pending.pop()
        if type(source) is list:
            target_items = cast(list[v2.StrictJsonValue], target)
            for item in source:
                if type(item) is list:
                    child_items: list[v2.StrictJsonValue] = []
                    target_items.append(child_items)
                    pending.append((item, child_items))
                elif type(item) is dict:
                    child_object: dict[str, v2.StrictJsonValue] = {}
                    target_items.append(child_object)
                    pending.append((item, child_object))
                else:
                    target_items.append(item)
            continue

        source_object = cast(dict[str, v2.StrictJsonValue], source)
        target_object = cast(dict[str, v2.StrictJsonValue], target)
        for key, item in source_object.items():
            if type(item) is list:
                child_items = []
                target_object[key] = child_items
                pending.append((item, child_items))
            elif type(item) is dict:
                child_object = {}
                target_object[key] = child_object
                pending.append((item, child_object))
            else:
                target_object[key] = item
    return cloned


def _valid_external_device_key(value: object) -> bool:
    return type(value) is str and bool(value) and _is_utf8_text(value)


def _window_projection(settings: v2.WindowSettings) -> WindowSettingsProjection:
    return WindowSettingsProjection(
        columns=settings["columns"],
        auto_fit=settings["auto_fit"],
        window_x=settings["window_x"],
        window_y=settings["window_y"],
        window_w=settings["window_w"],
        window_h=settings["window_h"],
    )


def _launch_projection(settings: v2.UrlLaunchSettings) -> LaunchSettingsProjection:
    return LaunchSettingsProjection(
        browser=settings["browser"],
        chrome_profile=settings["chrome_profile"],
        open_target=settings["open_target"],
    )


def _selected_window_settings(
    graph: _Graph,
    workspace_id: v2.EntityUUID,
    external_device_key: str | None,
) -> WindowSettingsProjection | None:
    selected = (
        None
        if external_device_key is None
        else graph.bindings.window_devices.get(
            (workspace_id, external_device_key),
        )
    )
    if selected is None:
        selected = graph.bindings.window_fallbacks.get(workspace_id)
    return None if selected is None else _window_projection(selected)


def _selected_launch_settings(
    graph: _Graph,
    placement_id: v2.EntityUUID,
    external_device_key: str | None,
) -> LaunchSettingsProjection | None:
    selected = (
        None
        if external_device_key is None
        else graph.bindings.launch_devices.get(
            (placement_id, external_device_key),
        )
    )
    if selected is None:
        selected = graph.bindings.launch_fallbacks.get(placement_id)
    return None if selected is None else _launch_projection(selected)


def _icon_value(icon: v2.LegacyStringIcon | None) -> str | None:
    return None if icon is None else icon["value"]


def _project_placement(
    graph: _Graph,
    placement: v2.Placement,
    external_device_key: str | None,
) -> PlacementProjection:
    resource = graph.resources[placement["resource_id"]]
    label_override = placement["label_override"]
    icon_override = placement["icon_override"]
    return PlacementProjection(
        id=placement["id"],
        resource_id=resource["id"],
        tab_id=placement["tab_id"],
        url=resource["target"]["url"],
        label=(resource["default_label"] if label_override is None else label_override),
        icon=_icon_value(
            resource["default_icon"] if icon_override is None else icon_override
        ),
        background_color=placement["background_color"],
        workflow_status=placement["workflow_status"],
        launch_settings=_selected_launch_settings(
            graph,
            placement["id"],
            external_device_key,
        ),
    )


def _project_tab(
    graph: _Graph,
    tab: v2.Tab,
    external_device_key: str | None,
) -> TabProjection:
    placements = tuple(
        _project_placement(
            graph,
            graph.placements[placement_id],
            external_device_key,
        )
        for placement_id in tab["display_order"]
    )
    display_filter = tuple(tab["display_filter"])
    displayed = tuple(
        placement
        for placement in placements
        if placement.workflow_status in display_filter
    )
    return TabProjection(
        id=tab["id"],
        workspace_id=tab["workspace_id"],
        name=tab["name"],
        visibility=tab["visibility"],
        lifecycle=tab["lifecycle"],
        view_mode=tab["view_mode"],
        display_filter=display_filter,
        placements=placements,
        displayed_placements=displayed,
    )


def project_workspace(
    document: object,
    workspace_id: str | None = None,
    *,
    external_device_key: str | None = None,
) -> WorkspaceProjectionResult:
    """Project one Workspace after validating the complete schema-v2 graph."""

    graph = _validated_graph(document)
    if graph is None:
        return RuntimeAdapterRejected("invalid_graph")
    if external_device_key is not None and not _valid_external_device_key(
        external_device_key
    ):
        return RuntimeAdapterRejected("invalid_device_key")

    selected_id: object = (
        graph.root["application"]["default_workspace_id"]
        if workspace_id is None
        else workspace_id
    )
    if type(selected_id) is not str:
        return RuntimeAdapterRejected("subject_not_found")
    workspace = graph.workspaces.get(selected_id)
    if workspace is None:
        return RuntimeAdapterRejected("subject_not_found")

    tabs = tuple(
        _project_tab(graph, graph.tabs[tab_id], external_device_key)
        for tab_id in workspace["tab_order"]
    )
    normal_tabs = tuple(
        tab for tab in tabs if tab.lifecycle == "active" and tab.visibility == "visible"
    )
    return WorkspaceProjection(
        application_title=graph.root["application"]["title"],
        id=workspace["id"],
        name=workspace["name"],
        tabs=tabs,
        normal_tabs=normal_tabs,
        window_settings=_selected_window_settings(
            graph,
            workspace["id"],
            external_device_key,
        ),
    )


def apply_metadata_refresh(
    document: object,
    placement_id: str,
    *,
    label: str | None = None,
    icon: str | None = None,
) -> MetadataRefreshResult:
    """Return a detached graph with successful refresh fields applied to a Resource."""

    graph = _validated_graph(document)
    if graph is None:
        return RuntimeAdapterRejected("invalid_graph")
    if type(placement_id) is not str or placement_id not in graph.placements:
        return RuntimeAdapterRejected("subject_not_found")
    if (label is not None and (type(label) is not str or not _is_utf8_text(label))) or (
        icon is not None and (type(icon) is not str or not _is_utf8_text(icon))
    ):
        return RuntimeAdapterRejected("refresh_failure")

    resource_id = graph.placements[placement_id]["resource_id"]
    candidate = cast(
        v2.Root,
        _clone_strict_json(cast(v2.StrictJsonValue, graph.root)),
    )
    resources = {item["id"]: item for item in candidate["resources"]}
    resource = resources[resource_id]
    if label is not None:
        resource["default_label"] = label
    if icon is not None:
        resource["default_icon"] = v2.LegacyStringIcon(
            kind="legacy_string",
            value=icon,
        )
    if not v2.validate_v2(candidate):
        return RuntimeAdapterRejected("refresh_failure")
    return candidate


__all__ = [
    "LaunchSettingsProjection",
    "MetadataRefreshResult",
    "PlacementProjection",
    "RuntimeAdapterFailureCategory",
    "RuntimeAdapterRejected",
    "TabProjection",
    "WindowSettingsProjection",
    "WorkspaceProjection",
    "WorkspaceProjectionResult",
    "apply_metadata_refresh",
    "project_workspace",
]
