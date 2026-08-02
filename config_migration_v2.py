# SPDX-License-Identifier: Apache-2.0
"""Dormant, pure schema-version 1 to schema-version 2 transformation.

This module constructs one complete URL-only schema-v2 candidate from an
already detached strict schema-v1 logical graph.  It deliberately performs no
parsing, registration, target validation, serialization, persistence, platform
discovery, or Qt work.  Complete target validation, canonical serialization,
and the inclusive candidate-size ceiling remain the responsibility of the
existing downstream migration boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, TypeAlias, TypedDict, cast
from uuid import UUID, uuid5

import config_schema as v1
import config_schema_v2 as v2

MIGRATION_NAMESPACE_V1_TO_V2: Final = UUID("8cdeb2d4-8211-5078-9c60-90d397366383")
V1_TO_V2_ID_ALLOCATION_ATTEMPTS: Final = 32

_Locator: TypeAlias = tuple[str, int]


class _V1Application(TypedDict):
    title: str
    default_workspace_id: str
    extensions: v1.JsonObject


class _V1Workspace(TypedDict):
    id: str
    name: str
    tab_order: list[str]
    extensions: v1.JsonObject


class _V1Tab(TypedDict):
    id: str
    workspace_id: str
    name: str
    visibility: v2.Visibility
    extensions: v1.JsonObject


class _V1Tile(TypedDict):
    name: str
    url: str
    tab_id: str
    icon: str | None
    bg: str
    browser: str | None
    chrome_profile: str | None
    open_target: v2.OpenTarget


class _V1Root(TypedDict):
    schema_version: int
    application: _V1Application
    workspaces: list[_V1Workspace]
    tabs: list[_V1Tab]
    tiles: list[_V1Tile]
    columns: int
    auto_fit: bool
    window_x: int | None
    window_y: int | None
    window_w: int | None
    window_h: int | None
    extensions: v1.JsonObject


def _clone_json(value: v1.JsonValue) -> v2.StrictJsonValue:
    if type(value) is list:
        return [_clone_json(item) for item in value]
    if type(value) is dict:
        return {key: _clone_json(item) for key, item in value.items()}
    return value


def _clone_extensions(value: v1.JsonObject) -> v2.Extensions:
    return {key: _clone_json(item) for key, item in value.items()}


def _generated_uuid(
    name: str,
    uuid5_factory: v1.Uuid5Factory,
) -> str | None:
    result: object = uuid5_factory(MIGRATION_NAMESPACE_V1_TO_V2, name)
    if isinstance(result, UUID):
        parsed = result
        candidate = str(result)
    elif type(result) is str:
        candidate = result
        try:
            parsed = UUID(candidate)
        except (AttributeError, ValueError):
            return None
    else:
        return None
    if parsed.version != 5 or candidate != str(parsed):
        return None
    return candidate


def _allocate_id(
    name_prefix: str,
    used_ids: set[str],
    uuid5_factory: v1.Uuid5Factory,
) -> str | None:
    for retry in range(V1_TO_V2_ID_ALLOCATION_ATTEMPTS):
        candidate = _generated_uuid(
            f"{name_prefix}:retry:{retry}",
            uuid5_factory,
        )
        if candidate is None:
            return None
        if candidate in used_ids:
            continue
        used_ids.add(candidate)
        return candidate
    return None


def _group_tiles(source: _V1Root) -> dict[str, list[_V1Tile]]:
    grouped: dict[str, list[_V1Tile]] = {tab["id"]: [] for tab in source["tabs"]}
    for tile in source["tiles"]:
        grouped[tile["tab_id"]].append(tile)
    return grouped


def _allocation_order(grouped: Mapping[str, list[_V1Tile]]) -> list[_Locator]:
    return sorted(
        (tab_id, ordinal)
        for tab_id, tiles in grouped.items()
        for ordinal in range(len(tiles))
    )


def _emission_order(
    tab_order: list[str],
    grouped: Mapping[str, list[_V1Tile]],
) -> list[_Locator]:
    return [
        (tab_id, ordinal)
        for tab_id in tab_order
        for ordinal in range(len(grouped[tab_id]))
    ]


def _allocate_entity_ids(
    source: _V1Root,
    grouped: Mapping[str, list[_V1Tile]],
    uuid5_factory: v1.Uuid5Factory,
) -> (
    tuple[
        dict[_Locator, str],
        dict[_Locator, str],
        str,
        dict[_Locator, str],
    ]
    | None
):
    workspace = source["workspaces"][0]
    priority = _allocation_order(grouped)
    used_ids = {workspace["id"], *(tab["id"] for tab in source["tabs"])}

    resource_ids: dict[_Locator, str] = {}
    for tab_id, ordinal in priority:
        resource_id = _allocate_id(
            f"dtl:migration:v1-to-v2:entity:resource:tab:{tab_id}:ordinal:{ordinal}",
            used_ids,
            uuid5_factory,
        )
        if resource_id is None:
            return None
        resource_ids[(tab_id, ordinal)] = resource_id

    placement_ids: dict[_Locator, str] = {}
    for tab_id, ordinal in priority:
        placement_id = _allocate_id(
            f"dtl:migration:v1-to-v2:entity:placement:tab:{tab_id}:ordinal:{ordinal}",
            used_ids,
            uuid5_factory,
        )
        if placement_id is None:
            return None
        placement_ids[(tab_id, ordinal)] = placement_id

    workspace_binding_id = _allocate_id(
        "dtl:migration:v1-to-v2:entity:device-binding:"
        f"workspace-window:workspace:{workspace['id']}",
        used_ids,
        uuid5_factory,
    )
    if workspace_binding_id is None:
        return None

    launch_binding_ids: dict[_Locator, str] = {}
    for locator in priority:
        placement_id = placement_ids[locator]
        binding_id = _allocate_id(
            "dtl:migration:v1-to-v2:entity:device-binding:"
            f"placement-launch:placement:{placement_id}",
            used_ids,
            uuid5_factory,
        )
        if binding_id is None:
            return None
        launch_binding_ids[locator] = binding_id

    return (
        resource_ids,
        placement_ids,
        workspace_binding_id,
        launch_binding_ids,
    )


def _migrated_tabs(
    source: _V1Root,
    grouped: Mapping[str, list[_V1Tile]],
    placement_ids: Mapping[_Locator, str],
) -> list[v2.Tab]:
    source_tabs = {tab["id"]: tab for tab in source["tabs"]}
    tab_order = source["workspaces"][0]["tab_order"]
    migrated: list[v2.Tab] = []
    for tab_id in tab_order:
        source_tab = source_tabs[tab_id]
        display_order = [
            placement_ids[(tab_id, ordinal)] for ordinal in range(len(grouped[tab_id]))
        ]
        in_use_order = [
            placement_ids[(tab_id, ordinal)] for ordinal in range(len(grouped[tab_id]))
        ]
        migrated.append(
            v2.Tab(
                id=source_tab["id"],
                workspace_id=source_tab["workspace_id"],
                name=source_tab["name"],
                visibility=source_tab["visibility"],
                lifecycle="active",
                view_mode="display",
                display_filter=["new", "in_use"],
                display_order=display_order,
                kanban_order=v2.KanbanOrder(
                    new=[],
                    in_use=in_use_order,
                    archived=[],
                ),
                extensions={},
            )
        )
    return migrated


def _migrated_resources(
    grouped: Mapping[str, list[_V1Tile]],
    emission_order: list[_Locator],
    resource_ids: Mapping[_Locator, str],
) -> list[v2.Resource]:
    resources: list[v2.Resource] = []
    for locator in emission_order:
        tab_id, ordinal = locator
        tile = grouped[tab_id][ordinal]
        icon = tile["icon"]
        default_icon = (
            None
            if icon is None
            else v2.LegacyStringIcon(kind="legacy_string", value=icon)
        )
        resources.append(
            v2.Resource(
                id=resource_ids[locator],
                kind="url",
                target=v2.UrlTarget(url=tile["url"]),
                default_label=tile["name"],
                default_icon=default_icon,
                extensions={},
            )
        )
    return resources


def _migrated_placements(
    grouped: Mapping[str, list[_V1Tile]],
    emission_order: list[_Locator],
    resource_ids: Mapping[_Locator, str],
    placement_ids: Mapping[_Locator, str],
) -> list[v2.Placement]:
    placements: list[v2.Placement] = []
    for locator in emission_order:
        tab_id, ordinal = locator
        tile = grouped[tab_id][ordinal]
        placements.append(
            v2.Placement(
                id=placement_ids[locator],
                resource_id=resource_ids[locator],
                tab_id=tab_id,
                label_override=None,
                icon_override=None,
                background_color=tile["bg"],
                workflow_status="in_use",
                extensions={},
            )
        )
    return placements


def _migrated_bindings(
    source: _V1Root,
    grouped: Mapping[str, list[_V1Tile]],
    emission_order: list[_Locator],
    placement_ids: Mapping[_Locator, str],
    workspace_binding_id: str,
    launch_binding_ids: Mapping[_Locator, str],
) -> list[v2.DeviceBinding]:
    workspace = source["workspaces"][0]
    bindings: list[v2.DeviceBinding] = [
        v2.WorkspaceWindowBinding(
            id=workspace_binding_id,
            subject_kind="workspace",
            subject_id=workspace["id"],
            binding_kind="window",
            applicability=v2.PortableFallback(kind="portable_fallback"),
            settings=v2.WindowSettings(
                columns=source["columns"],
                auto_fit=source["auto_fit"],
                window_x=source["window_x"],
                window_y=source["window_y"],
                window_w=source["window_w"],
                window_h=source["window_h"],
            ),
            extensions={},
        )
    ]
    for locator in emission_order:
        tab_id, ordinal = locator
        tile = grouped[tab_id][ordinal]
        bindings.append(
            v2.PlacementLaunchBinding(
                id=launch_binding_ids[locator],
                subject_kind="placement",
                subject_id=placement_ids[locator],
                binding_kind="launch",
                applicability=v2.PortableFallback(kind="portable_fallback"),
                settings=v2.UrlLaunchSettings(
                    browser=tile["browser"],
                    chrome_profile=tile["chrome_profile"],
                    open_target=tile["open_target"],
                ),
                extensions={},
            )
        )
    return bindings


def migrate_v1_to_v2(
    document: Mapping[str, v1.JsonValue],
    *,
    uuid5_factory: v1.Uuid5Factory = uuid5,
) -> v2.Root | None:
    """Construct one complete deterministic schema-v2 candidate or reject the step."""

    if not v1.validate_v1(document):
        return None
    source = cast(_V1Root, document)
    grouped = _group_tiles(source)
    allocated = _allocate_entity_ids(source, grouped, uuid5_factory)
    if allocated is None:
        return None
    resource_ids, placement_ids, workspace_binding_id, launch_binding_ids = allocated

    source_workspace = source["workspaces"][0]
    emission_order = _emission_order(source_workspace["tab_order"], grouped)
    candidate = v2.Root(
        schema_version=2,
        application=v2.Application(
            title=source["application"]["title"],
            default_workspace_id=source["application"]["default_workspace_id"],
            extensions={},
        ),
        workspaces=[
            v2.Workspace(
                id=source_workspace["id"],
                name=source_workspace["name"],
                tab_order=list(source_workspace["tab_order"]),
                extensions={},
            )
        ],
        tabs=_migrated_tabs(source, grouped, placement_ids),
        resources=_migrated_resources(grouped, emission_order, resource_ids),
        placements=_migrated_placements(
            grouped,
            emission_order,
            resource_ids,
            placement_ids,
        ),
        device_bindings=_migrated_bindings(
            source,
            grouped,
            emission_order,
            placement_ids,
            workspace_binding_id,
            launch_binding_ids,
        ),
        extensions=_clone_extensions(source["extensions"]),
    )
    return candidate


__all__ = [
    "MIGRATION_NAMESPACE_V1_TO_V2",
    "V1_TO_V2_ID_ALLOCATION_ATTEMPTS",
    "migrate_v1_to_v2",
]
