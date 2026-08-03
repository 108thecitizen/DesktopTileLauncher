# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

import config_runtime_state_v2 as runtime_state
import config_schema_v2 as schema

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "schema_v2" / "representative.json"
)

WORKSPACE_ID = "11000000-0000-4000-8000-000000000001"
MAIN_TAB_ID = "22000000-0000-4000-8000-000000000001"
SECOND_WORKSPACE_ID = "11000000-0000-4000-8000-000000000002"
SECOND_TAB_ID = "22000000-0000-4000-8000-000000000002"
DEVICE_BINDING_ID = "55000000-0000-4000-8000-000000000001"
SOURCE_DEVICE_BINDING_ID = "55000000-0000-4000-8000-000000000002"
NEW_TAB_ID = "77000000-0000-4000-8000-000000000001"
ARCHIVED_DEVICE_BINDING_ID = "88000000-0000-4000-8000-000000000001"
NEW_ENTITY_IDS = (
    "66000000-0000-4000-8000-000000000001",
    "66000000-0000-4000-8000-000000000002",
    "66000000-0000-4000-8000-000000000003",
    "66000000-0000-4000-8000-000000000004",
    "66000000-0000-4000-8000-000000000005",
    "66000000-0000-4000-8000-000000000006",
    "66000000-0000-4000-8000-000000000007",
)


def _native_document() -> schema.Root:
    identifiers = iter((WORKSPACE_ID, MAIN_TAB_ID))
    return runtime_state.build_native_v2(lambda: next(identifiers))


def _representative_document() -> schema.Root:
    parsed = json.loads(
        REPRESENTATIVE_FIXTURE.read_text(encoding="utf-8"),
        object_pairs_hook=schema.reject_duplicate_json_members,
    )
    assert schema.validate_v2(parsed)  # nosec B101
    return cast(schema.Root, parsed)


def _projected(document: object) -> runtime_state.FlatWorkspaceState:
    result = runtime_state.project_flat_workspace(document)
    assert isinstance(result, runtime_state.FlatWorkspaceState)  # nosec B101
    return result


def _updated(
    result: runtime_state.FlatWorkspaceUpdateResult,
) -> runtime_state.FlatWorkspaceUpdate:
    assert isinstance(result, runtime_state.FlatWorkspaceUpdate)  # nosec B101
    return result


def _workspace(
    document: schema.Root, workspace_id: str = WORKSPACE_ID
) -> schema.Workspace:
    return next(item for item in document["workspaces"] if item["id"] == workspace_id)


def _tab(document: schema.Root, tab_id: str) -> schema.Tab:
    return next(item for item in document["tabs"] if item["id"] == tab_id)


def _portable_window_binding(
    document: schema.Root,
    workspace_id: str = WORKSPACE_ID,
) -> schema.WorkspaceWindowBinding:
    binding = next(
        item
        for item in document["device_bindings"]
        if item["subject_kind"] == "workspace"
        and item["subject_id"] == workspace_id
        and item["applicability"]["kind"] == "portable_fallback"
    )
    return cast(schema.WorkspaceWindowBinding, binding)


def _two_tab_document() -> schema.Root:
    document = _native_document()
    _workspace(document)["tab_order"].append(SECOND_TAB_ID)
    document["tabs"].append(
        schema.Tab(
            id=SECOND_TAB_ID,
            workspace_id=WORKSPACE_ID,
            name="Work",
            visibility="visible",
            lifecycle="active",
            view_mode="display",
            display_filter=["new", "in_use"],
            display_order=[],
            kanban_order=schema.KanbanOrder(new=[], in_use=[], archived=[]),
            extensions={"second-tab.example.test": {"retained": True}},
        )
    )
    assert schema.validate_v2(document)  # nosec B101
    return document


def _identity_tuple(tile: runtime_state.FlatTileState) -> tuple[str, str, str]:
    assert tile.placement_id is not None  # nosec B101
    assert tile.resource_id is not None  # nosec B101
    assert tile.launch_binding_id is not None  # nosec B101
    return tile.placement_id, tile.resource_id, tile.launch_binding_id


def test_native_v2_construction_projects_one_editable_current_workspace() -> None:
    document = _native_document()

    projection = _projected(document)

    assert schema.validate_v2(document)  # nosec B101
    assert document["schema_version"] == 2  # nosec B101
    assert projection.editable  # nosec B101
    assert projection.workspace_id == WORKSPACE_ID  # nosec B101
    assert projection.tab_order == (MAIN_TAB_ID,)  # nosec B101
    assert projection.tabs == (  # nosec B101
        runtime_state.FlatTabState(MAIN_TAB_ID, "Main", False),
    )
    assert len(projection.tiles) == 3  # nosec B101
    assert all(all(_identity_tuple(tile)) for tile in projection.tiles)  # nosec B101
    assert runtime_state.reserved_entity_ids(document) == frozenset(  # nosec B101
        item["id"]
        for collection in (
            document["workspaces"],
            document["tabs"],
            document["resources"],
            document["placements"],
            document["device_bindings"],
        )
        for item in collection
    )


def test_noop_sync_is_lossless_for_extensions_unselected_state_bindings_and_ids() -> (
    None
):
    document = _native_document()
    document["extensions"]["root.example.test"] = {"ordered": [2, 1]}
    document["application"]["extensions"]["application.example.test"] = None
    workspace = _workspace(document)
    workspace["extensions"]["workspace.example.test"] = {"kept": True}
    _tab(document, MAIN_TAB_ID)["extensions"]["tab.example.test"] = ["kept"]
    document["resources"][0]["extensions"]["resource.example.test"] = 7
    document["placements"][0]["extensions"]["placement.example.test"] = "kept"
    document["device_bindings"][0]["extensions"]["portable.example.test"] = False
    unselected_workspace = schema.Workspace(
        id=SECOND_WORKSPACE_ID,
        name="Unselected",
        tab_order=[],
        extensions={"unselected.example.test": {"private": "preserved"}},
    )
    document["workspaces"].append(unselected_workspace)
    device_binding = schema.WorkspaceWindowBinding(
        id=DEVICE_BINDING_ID,
        subject_kind="workspace",
        subject_id=WORKSPACE_ID,
        binding_kind="window",
        applicability=schema.DeviceSpecific(
            kind="device_specific",
            device_key="device-A",
        ),
        settings=schema.WindowSettings(
            columns=99,
            auto_fit=False,
            window_x=1,
            window_y=2,
            window_w=3,
            window_h=4,
        ),
        extensions={"device-binding.example.test": ["preserved"]},
    )
    document["device_bindings"].append(device_binding)
    assert schema.validate_v2(document)  # nosec B101
    original = deepcopy(document)
    reserved = runtime_state.reserved_entity_ids(document)
    projection = _projected(document)

    update = _updated(runtime_state.synchronize_flat_workspace(document, projection))

    assert update.document == original  # nosec B101
    assert document == original  # nosec B101
    assert runtime_state.reserved_entity_ids(update.document) == reserved  # nosec B101
    assert [_identity_tuple(tile) for tile in projection.tiles] == [  # nosec B101
        (
            identity.placement_id,
            identity.resource_id,
            identity.launch_binding_id,
        )
        for identity in update.tile_identities
    ]
    assert _workspace(update.document, SECOND_WORKSPACE_ID) == unselected_workspace  # nosec B101
    assert (
        next(  # nosec B101
            item
            for item in update.document["device_bindings"]
            if item["id"] == DEVICE_BINDING_ID
        )
        == device_binding
    )


def test_permitted_edit_preserves_populated_unselected_and_archived_graphs() -> None:
    document = _native_document()
    rich = _representative_document()
    secondary_workspace = deepcopy(rich["workspaces"][1])
    secondary_tab_ids = set(secondary_workspace["tab_order"])
    archived_tab = deepcopy(
        next(
            tab
            for tab in rich["tabs"]
            if tab["workspace_id"] == rich["application"]["default_workspace_id"]
            and tab["lifecycle"] == "archived"
            and tab["display_order"]
        )
    )
    archived_tab["workspace_id"] = WORKSPACE_ID
    selected_tab_ids = {*secondary_tab_ids, archived_tab["id"]}
    selected_tabs = [
        archived_tab,
        *[deepcopy(tab) for tab in rich["tabs"] if tab["id"] in secondary_tab_ids],
    ]
    selected_placements = [
        deepcopy(placement)
        for placement in rich["placements"]
        if placement["tab_id"] in selected_tab_ids
    ]
    selected_placement_ids = {placement["id"] for placement in selected_placements}
    selected_resource_ids = {
        placement["resource_id"] for placement in selected_placements
    }
    selected_resources = [
        deepcopy(resource)
        for resource in rich["resources"]
        if resource["id"] in selected_resource_ids
    ]
    selected_bindings = [
        deepcopy(binding)
        for binding in rich["device_bindings"]
        if (
            binding["subject_kind"] == "workspace"
            and binding["subject_id"] == secondary_workspace["id"]
        )
        or (
            binding["subject_kind"] == "placement"
            and binding["subject_id"] in selected_placement_ids
        )
    ]
    selected_bindings.append(
        schema.PlacementLaunchBinding(
            id=ARCHIVED_DEVICE_BINDING_ID,
            subject_kind="placement",
            subject_id=archived_tab["display_order"][0],
            binding_kind="launch",
            applicability=schema.DeviceSpecific(
                kind="device_specific",
                device_key="archived-device",
            ),
            settings=schema.UrlLaunchSettings(
                browser="Archived Browser",
                chrome_profile=None,
                open_target="window",
            ),
            extensions={"archived-binding.example.test": {"kept": True}},
        )
    )

    document["workspaces"].append(secondary_workspace)
    _workspace(document)["tab_order"].insert(0, archived_tab["id"])
    document["tabs"].extend(selected_tabs)
    document["resources"].extend(selected_resources)
    document["placements"].extend(selected_placements)
    document["device_bindings"].extend(selected_bindings)
    assert schema.validate_v2(document)  # nosec B101
    preserved_ids = {
        "workspaces": {secondary_workspace["id"]},
        "tabs": selected_tab_ids,
        "resources": selected_resource_ids,
        "placements": selected_placement_ids,
        "device_bindings": {binding["id"] for binding in selected_bindings},
    }
    preserved = {
        collection: {
            item["id"]: deepcopy(item)
            for item in document[collection]
            if item["id"] in identifiers
        }
        for collection, identifiers in preserved_ids.items()
    }
    projection = _projected(document)
    assert projection.editable  # nosec B101
    edited = replace(
        projection,
        title="Edited while rich state remains unexposed",
        tiles=(
            replace(projection.tiles[0], background_color="#abcdef"),
            *projection.tiles[1:],
        ),
    )

    update = _updated(runtime_state.synchronize_flat_workspace(document, edited))

    for collection, expected in preserved.items():
        actual = {
            item["id"]: item
            for item in update.document[collection]
            if item["id"] in expected
        }
        assert actual == expected  # nosec B101
    assert _workspace(update.document)["tab_order"][0] == archived_tab["id"]  # nosec B101
    assert schema.validate_v2(update.document)  # nosec B101


def test_sync_updates_title_window_and_tab_visibility_name_and_order() -> None:
    document = _two_tab_document()
    original = deepcopy(document)
    projection = _projected(document)
    reordered_tabs = (
        replace(projection.tabs[1], name="Projects", hidden=False),
        replace(projection.tabs[0], name="Home", hidden=True),
    )
    edited = replace(
        projection,
        title="Renamed Launcher",
        workspace_name="Renamed Workspace",
        tabs=reordered_tabs,
        tab_order=tuple(tab.id for tab in reordered_tabs),
        columns=8,
        auto_fit=False,
        window_x=-20,
        window_y=10,
        window_w=1200,
        window_h=700,
    )

    update = _updated(runtime_state.synchronize_flat_workspace(document, edited))
    candidate = update.document

    assert document == original  # nosec B101
    assert candidate["application"]["title"] == "Renamed Launcher"  # nosec B101
    assert _workspace(candidate)["name"] == "Renamed Workspace"  # nosec B101
    assert _workspace(candidate)["tab_order"] == [SECOND_TAB_ID, MAIN_TAB_ID]  # nosec B101
    assert _tab(candidate, SECOND_TAB_ID)["name"] == "Projects"  # nosec B101
    assert _tab(candidate, SECOND_TAB_ID)["visibility"] == "visible"  # nosec B101
    assert _tab(candidate, MAIN_TAB_ID)["name"] == "Home"  # nosec B101
    assert _tab(candidate, MAIN_TAB_ID)["visibility"] == "hidden"  # nosec B101
    assert _portable_window_binding(candidate)["settings"] == {  # nosec B101
        "columns": 8,
        "auto_fit": False,
        "window_x": -20,
        "window_y": 10,
        "window_w": 1200,
        "window_h": 700,
    }
    assert schema.validate_v2(candidate)  # nosec B101


def test_new_uuid4_tab_round_trips_into_the_complete_graph() -> None:
    document = _native_document()
    projection = _projected(document)
    added = runtime_state.FlatTabState(NEW_TAB_ID, "Added", False)
    edited = replace(
        projection,
        tabs=(*projection.tabs, added),
        tab_order=(*projection.tab_order, NEW_TAB_ID),
    )

    update = _updated(runtime_state.synchronize_flat_workspace(document, edited))

    assert _workspace(update.document)["tab_order"] == [  # nosec B101
        MAIN_TAB_ID,
        NEW_TAB_ID,
    ]
    assert _tab(update.document, NEW_TAB_ID) == {  # nosec B101
        "id": NEW_TAB_ID,
        "workspace_id": WORKSPACE_ID,
        "name": "Added",
        "visibility": "visible",
        "lifecycle": "active",
        "view_mode": "display",
        "display_filter": ["new", "in_use"],
        "display_order": [],
        "kanban_order": {"new": [], "in_use": [], "archived": []},
        "extensions": {},
    }
    assert schema.validate_v2(update.document)  # nosec B101


def test_tile_reorder_changes_only_display_order_and_preserves_kanban_order() -> None:
    document = _native_document()
    original = deepcopy(document)
    projection = _projected(document)
    edited = replace(projection, tiles=tuple(reversed(projection.tiles)))
    expected_display_order = [cast(str, tile.placement_id) for tile in edited.tiles]
    expected = deepcopy(document)
    _tab(expected, MAIN_TAB_ID)["display_order"] = expected_display_order
    original_kanban = deepcopy(_tab(document, MAIN_TAB_ID)["kanban_order"])

    update = _updated(runtime_state.synchronize_flat_workspace(document, edited))

    assert document == original  # nosec B101
    assert update.document == expected  # nosec B101
    assert _tab(update.document, MAIN_TAB_ID)["kanban_order"] == original_kanban  # nosec B101
    assert [  # nosec B101
        identity.placement_id for identity in update.tile_identities
    ] == expected_display_order


def test_existing_tile_edit_updates_only_owned_fields_and_keeps_identity() -> None:
    document = _native_document()
    projection = _projected(document)
    source = projection.tiles[0]
    placement_id, resource_id, portable_binding_id = _identity_tuple(source)
    placement = next(
        item for item in document["placements"] if item["id"] == placement_id
    )
    resource = next(item for item in document["resources"] if item["id"] == resource_id)
    portable_binding = next(
        item
        for item in document["device_bindings"]
        if item["id"] == portable_binding_id
    )
    placement["extensions"] = {"placement.example.test": {"kept": True}}
    resource["extensions"] = {"resource.example.test": ["kept"]}
    portable_binding["extensions"] = {"portable.example.test": "kept"}
    device_binding = schema.PlacementLaunchBinding(
        id=SOURCE_DEVICE_BINDING_ID,
        subject_kind="placement",
        subject_id=placement_id,
        binding_kind="launch",
        applicability=schema.DeviceSpecific(
            kind="device_specific",
            device_key="device-A",
        ),
        settings=schema.UrlLaunchSettings(
            browser="Device Browser",
            chrome_profile="Device Profile",
            open_target="tab",
        ),
        extensions={"device.example.test": {"kept": True}},
    )
    document["device_bindings"].append(device_binding)
    assert schema.validate_v2(document)  # nosec B101
    original = deepcopy(document)
    projection = _projected(document)
    edited_tile = replace(
        projection.tiles[0],
        name="Edited label",
        url="https://edited.example.test/path",
        icon="edited-icon",
        background_color="#010203",
        browser="Edited Browser",
        chrome_profile="Edited Profile",
        open_target="window",
    )
    edited = replace(projection, tiles=(edited_tile, *projection.tiles[1:]))
    expected = deepcopy(document)
    expected_placement = next(
        item for item in expected["placements"] if item["id"] == placement_id
    )
    expected_resource = next(
        item for item in expected["resources"] if item["id"] == resource_id
    )
    expected_binding = next(
        item
        for item in expected["device_bindings"]
        if item["id"] == portable_binding_id
    )
    expected_placement["background_color"] = "#010203"
    expected_resource["target"]["url"] = "https://edited.example.test/path"
    expected_resource["default_label"] = "Edited label"
    expected_resource["default_icon"] = schema.LegacyStringIcon(
        kind="legacy_string",
        value="edited-icon",
    )
    expected_binding["settings"] = schema.UrlLaunchSettings(
        browser="Edited Browser",
        chrome_profile="Edited Profile",
        open_target="window",
    )

    update = _updated(runtime_state.synchronize_flat_workspace(document, edited))

    assert document == original  # nosec B101
    assert update.document == expected  # nosec B101
    assert update.tile_identities[0] == runtime_state.FlatTileIdentity(  # nosec B101
        placement_id,
        resource_id,
        portable_binding_id,
    )
    assert schema.validate_v2(update.document)  # nosec B101


def test_add_and_duplicate_style_tiles_allocate_uuid4_and_join_in_use_order() -> None:
    document = _native_document()
    source_order = list(_tab(document, MAIN_TAB_ID)["display_order"])
    _tab(document, MAIN_TAB_ID)["kanban_order"]["in_use"] = [
        source_order[2],
        source_order[0],
        source_order[1],
    ]
    source_placement_id = source_order[0]
    source_portable = next(
        binding
        for binding in document["device_bindings"]
        if binding["subject_kind"] == "placement"
        and binding["subject_id"] == source_placement_id
        and binding["applicability"]["kind"] == "portable_fallback"
    )
    source_portable["extensions"] = {"portable.example.test": {"kept": True}}
    document["device_bindings"].append(
        schema.PlacementLaunchBinding(
            id=SOURCE_DEVICE_BINDING_ID,
            subject_kind="placement",
            subject_id=source_placement_id,
            binding_kind="launch",
            applicability=schema.DeviceSpecific(
                kind="device_specific",
                device_key="device-A",
            ),
            settings=schema.UrlLaunchSettings(
                browser="Device Browser",
                chrome_profile="Device Profile",
                open_target="window",
            ),
            extensions={"device.example.test": ["kept"]},
        )
    )
    assert schema.validate_v2(document)  # nosec B101
    original = deepcopy(document)
    projection = _projected(document)
    source = projection.tiles[0]
    added = runtime_state.FlatTileState(
        placement_id=None,
        resource_id=None,
        launch_binding_id=None,
        name="Added",
        url="https://added.example.test/path",
        tab_id=MAIN_TAB_ID,
        icon="added-icon",
        background_color="#123456",
        browser="Browser",
        chrome_profile="Profile",
        open_target="window",
    )
    duplicated = replace(
        source,
        placement_id=None,
        resource_id=None,
        launch_binding_id=None,
        duplicate_source_placement_id=source.placement_id,
    )
    edited = replace(
        projection,
        tiles=(projection.tiles[0], duplicated, *projection.tiles[1:], added),
    )
    identifiers = iter(NEW_ENTITY_IDS)
    old_display = list(_tab(document, MAIN_TAB_ID)["display_order"])
    old_in_use = list(_tab(document, MAIN_TAB_ID)["kanban_order"]["in_use"])

    update = _updated(
        runtime_state.synchronize_flat_workspace(
            document,
            edited,
            id_factory=lambda: next(identifiers),
        )
    )
    candidate = update.document
    duplicated_identity = update.tile_identities[1]
    added_identity = update.tile_identities[-1]
    duplicated_bindings = [
        binding
        for binding in candidate["device_bindings"]
        if binding["subject_kind"] == "placement"
        and binding["subject_id"] == duplicated_identity.placement_id
    ]

    assert document == original  # nosec B101
    assert (
        duplicated_identity.resource_id,
        duplicated_identity.placement_id,
        duplicated_identity.launch_binding_id,
        duplicated_bindings[1]["id"],
        added_identity.resource_id,
        added_identity.placement_id,
        added_identity.launch_binding_id,
    ) == NEW_ENTITY_IDS  # nosec B101
    assert all(UUID(identifier).version == 4 for identifier in NEW_ENTITY_IDS)  # nosec B101
    placements = {item["id"]: item for item in candidate["placements"]}
    resources = {item["id"]: item for item in candidate["resources"]}
    bindings = {item["id"]: item for item in candidate["device_bindings"]}
    assert placements[added_identity.placement_id]["workflow_status"] == "in_use"  # nosec B101
    assert placements[added_identity.placement_id]["tab_id"] == MAIN_TAB_ID  # nosec B101
    assert resources[added_identity.resource_id]["default_label"] == "Added"  # nosec B101
    assert bindings[added_identity.launch_binding_id]["subject_id"] == (  # nosec B101
        added_identity.placement_id
    )
    source_bindings = [
        binding
        for binding in document["device_bindings"]
        if binding["subject_kind"] == "placement"
        and binding["subject_id"] == source_placement_id
    ]
    assert len(source_bindings) == len(duplicated_bindings) == 2  # nosec B101
    for source_binding, copied_binding in zip(
        source_bindings,
        duplicated_bindings,
        strict=True,
    ):
        assert copied_binding["id"] != source_binding["id"]  # nosec B101
        assert copied_binding["subject_id"] == duplicated_identity.placement_id  # nosec B101
        assert {  # nosec B101
            key: value
            for key, value in copied_binding.items()
            if key not in {"id", "subject_id"}
        } == {
            key: value
            for key, value in source_binding.items()
            if key not in {"id", "subject_id"}
        }
    assert resources[duplicated_identity.resource_id]["default_label"] == source.name  # nosec B101
    tab = _tab(candidate, MAIN_TAB_ID)
    assert (
        tab["display_order"]
        == [  # nosec B101
            old_display[0],
            duplicated_identity.placement_id,
            *old_display[1:],
            added_identity.placement_id,
        ]
    )
    assert (
        tab["kanban_order"]
        == {  # nosec B101
            "new": [],
            "in_use": [
                old_in_use[0],
                old_in_use[1],
                duplicated_identity.placement_id,
                *old_in_use[2:],
                added_identity.placement_id,
            ],
            "archived": [],
        }
    )
    assert schema.validate_v2(candidate)  # nosec B101


def test_new_tiles_reject_noncontractual_display_positions_without_mutation() -> None:
    document = _native_document()
    original = deepcopy(document)
    projection = _projected(document)
    source = projection.tiles[0]
    added = runtime_state.FlatTileState(
        placement_id=None,
        resource_id=None,
        launch_binding_id=None,
        name="Added",
        url="https://added.example.test/path",
        tab_id=MAIN_TAB_ID,
        icon=None,
        background_color="#123456",
        browser=None,
        chrome_profile=None,
        open_target="tab",
    )
    duplicated = replace(
        source,
        placement_id=None,
        resource_id=None,
        launch_binding_id=None,
        duplicate_source_placement_id=source.placement_id,
    )
    malformed_states = (
        replace(projection, tiles=(added, *projection.tiles)),
        replace(projection, tiles=(*projection.tiles, duplicated)),
    )

    for state in malformed_states:
        result = runtime_state.synchronize_flat_workspace(document, state)

        assert result == runtime_state.FlatStateRejected("invalid_state")  # nosec B101
        assert document == original  # nosec B101


def test_unhashable_flat_state_fields_are_rejected_without_mutation() -> None:
    document = _native_document()
    original = deepcopy(document)
    projection = _projected(document)
    malformed_states = (
        replace(
            projection,
            tabs=(
                replace(projection.tabs[0], id=cast(str, [])),
                *projection.tabs[1:],
            ),
        ),
        replace(
            projection,
            tiles=(
                replace(projection.tiles[0], tab_id=cast(str, [])),
                *projection.tiles[1:],
            ),
        ),
        replace(
            projection,
            tiles=(
                replace(
                    projection.tiles[0],
                    placement_id=cast(str | None, {}),
                ),
                *projection.tiles[1:],
            ),
        ),
    )

    for state in malformed_states:
        result = runtime_state.synchronize_flat_workspace(document, state)

        assert result == runtime_state.FlatStateRejected("invalid_state")  # nosec B101
        assert document == original  # nosec B101


@pytest.mark.parametrize("invalid_open_target", ["popup", 1])
def test_invalid_open_target_is_rejected_without_mutation(
    invalid_open_target: object,
) -> None:
    document = _native_document()
    original = deepcopy(document)
    projection = _projected(document)
    malformed = replace(
        projection,
        tiles=(
            replace(
                projection.tiles[0],
                open_target=cast(schema.OpenTarget, invalid_open_target),
            ),
            *projection.tiles[1:],
        ),
    )

    result = runtime_state.synchronize_flat_workspace(document, malformed)

    assert result == runtime_state.FlatStateRejected("invalid_state")  # nosec B101
    assert document == original  # nosec B101


def test_move_remove_and_tab_delete_are_coordinated_and_keep_orphan_resource() -> None:
    document = _two_tab_document()
    projection = _projected(document)
    moved_first, removed, moved_last = projection.tiles
    removed_placement_id, removed_resource_id, removed_binding_id = _identity_tuple(
        removed
    )
    removed_resource = deepcopy(
        next(
            item for item in document["resources"] if item["id"] == removed_resource_id
        )
    )
    removed_resource["extensions"]["orphan.example.test"] = "retained"
    source_resource = next(
        item for item in document["resources"] if item["id"] == removed_resource_id
    )
    source_resource["extensions"] = deepcopy(removed_resource["extensions"])
    original = deepcopy(document)
    remaining_tab = replace(projection.tabs[1], hidden=False)
    moved_tiles = (
        replace(moved_first, tab_id=SECOND_TAB_ID),
        replace(moved_last, tab_id=SECOND_TAB_ID),
    )
    edited = replace(
        projection,
        tabs=(remaining_tab,),
        tab_order=(SECOND_TAB_ID,),
        tiles=moved_tiles,
    )

    update = _updated(runtime_state.synchronize_flat_workspace(document, edited))
    candidate = update.document

    assert document == original  # nosec B101
    assert all(item["id"] != MAIN_TAB_ID for item in candidate["tabs"])  # nosec B101
    assert _workspace(candidate)["tab_order"] == [SECOND_TAB_ID]  # nosec B101
    assert all(  # nosec B101
        item["id"] != removed_placement_id for item in candidate["placements"]
    )
    assert all(  # nosec B101
        item["id"] != removed_binding_id for item in candidate["device_bindings"]
    )
    assert (
        next(  # nosec B101
            item for item in candidate["resources"] if item["id"] == removed_resource_id
        )
        == removed_resource
    )
    retained_placement_ids = [cast(str, tile.placement_id) for tile in moved_tiles]
    for placement_id in retained_placement_ids:
        placement = next(
            item for item in candidate["placements"] if item["id"] == placement_id
        )
        assert placement["tab_id"] == SECOND_TAB_ID  # nosec B101
    remaining = _tab(candidate, SECOND_TAB_ID)
    assert remaining["display_order"] == retained_placement_ids  # nosec B101
    assert remaining["kanban_order"] == {  # nosec B101
        "new": [],
        "in_use": retained_placement_ids,
        "archived": [],
    }
    assert schema.validate_v2(candidate)  # nosec B101


@pytest.mark.parametrize(
    "mode",
    [
        "duplicate_placement",
        "stale_placement",
        "swapped_resource",
        "swapped_binding",
        "partial_new_identity",
    ],
)
def test_existing_tile_identity_mismatch_is_rejected_without_mutation(
    mode: str,
) -> None:
    document = _native_document()
    original = deepcopy(document)
    projection = _projected(document)
    tiles = list(projection.tiles)
    first, second = tiles[:2]

    if mode == "duplicate_placement":
        tiles.append(first)
    elif mode == "stale_placement":
        tiles[0] = replace(first, placement_id=NEW_TAB_ID)
    elif mode == "swapped_resource":
        tiles[0] = replace(first, resource_id=second.resource_id)
    elif mode == "swapped_binding":
        tiles[0] = replace(first, launch_binding_id=second.launch_binding_id)
    else:
        tiles[0] = replace(first, placement_id=None)

    result = runtime_state.synchronize_flat_workspace(
        document,
        replace(projection, tiles=tuple(tiles)),
    )

    assert result == runtime_state.FlatStateRejected("invalid_state")  # nosec B101
    assert document == original  # nosec B101


@pytest.mark.parametrize("mode", ["invalid", "collision"])
def test_new_tile_allocator_rejects_invalid_or_colliding_values_without_mutation(
    mode: str,
) -> None:
    document = _native_document()
    original = deepcopy(document)
    projection = _projected(document)
    new_tile = replace(
        projection.tiles[0],
        placement_id=None,
        resource_id=None,
        launch_binding_id=None,
    )
    edited = replace(projection, tiles=(*projection.tiles, new_tile))
    collision = WORKSPACE_ID
    calls = 0

    def allocate() -> str:
        nonlocal calls
        calls += 1
        return "not-a-uuid" if mode == "invalid" else collision

    result = runtime_state.synchronize_flat_workspace(
        document,
        edited,
        id_factory=allocate,
    )

    assert result == runtime_state.FlatStateRejected(  # nosec B101
        "identity_allocation_failure"
    )
    assert calls == 32  # nosec B101
    assert document == original  # nosec B101


@pytest.mark.parametrize("mode", ["invalid", "collision"])
def test_native_builder_rejects_invalid_or_colliding_allocator_values(
    mode: str,
) -> None:
    values = ["not-a-uuid"] * 32 if mode == "invalid" else [WORKSPACE_ID] * 33
    identifiers = iter(values)

    with pytest.raises(runtime_state.NativeV2ConstructionError) as exc_info:
        runtime_state.build_native_v2(lambda: next(identifiers))

    assert exc_info.value.category == "identity_allocation_failure"  # nosec B101
    assert str(exc_info.value) == "identity_allocation_failure"  # nosec B101


def test_rich_shared_override_filter_and_status_graph_is_read_only() -> None:
    document = _representative_document()
    original = deepcopy(document)

    projection = _projected(document)

    assert not projection.editable  # nosec B101
    assert [tile.name for tile in projection.tiles] == ["", "Shared"]  # nosec B101
    assert projection.tiles[0].icon == ""  # nosec B101
    assert projection.tiles[0].resource_id == projection.tiles[1].resource_id  # nosec B101
    result = runtime_state.synchronize_flat_workspace(document, projection)
    assert result == runtime_state.FlatStateRejected("unsupported_graph")  # nosec B101
    assert document == original  # nosec B101


@pytest.mark.parametrize(
    "reason",
    [
        "shared_resource",
        "label_override",
        "icon_override",
        "workflow_status",
        "kanban_view",
        "display_filter",
        "missing_portable_binding",
        "missing_portable_window_binding",
    ],
)
def test_each_unrepresentable_flat_graph_feature_is_independently_read_only(
    reason: str,
) -> None:
    document = _native_document()
    tab = _tab(document, MAIN_TAB_ID)
    placement_id = tab["display_order"][0]
    placement = next(
        item for item in document["placements"] if item["id"] == placement_id
    )

    if reason == "shared_resource":
        document["placements"][1]["resource_id"] = placement["resource_id"]
    elif reason == "label_override":
        placement["label_override"] = "Override"
    elif reason == "icon_override":
        placement["icon_override"] = schema.LegacyStringIcon(
            kind="legacy_string",
            value="override-icon",
        )
    elif reason == "workflow_status":
        placement["workflow_status"] = "new"
        tab["kanban_order"]["in_use"].remove(placement_id)
        tab["kanban_order"]["new"].append(placement_id)
    elif reason == "kanban_view":
        tab["view_mode"] = "kanban"
    elif reason == "display_filter":
        tab["display_filter"] = ["in_use"]
    elif reason == "missing_portable_binding":
        document["device_bindings"] = [
            binding
            for binding in document["device_bindings"]
            if not (
                binding["subject_kind"] == "placement"
                and binding["subject_id"] == placement_id
                and binding["applicability"]["kind"] == "portable_fallback"
            )
        ]
    else:
        document["device_bindings"] = [
            binding
            for binding in document["device_bindings"]
            if not (
                binding["subject_kind"] == "workspace"
                and binding["subject_id"] == WORKSPACE_ID
                and binding["applicability"]["kind"] == "portable_fallback"
            )
        ]

    assert schema.validate_v2(document)  # nosec B101
    original = deepcopy(document)
    projection = _projected(document)

    assert not projection.editable  # nosec B101
    assert runtime_state.synchronize_flat_workspace(document, projection) == (  # nosec B101
        runtime_state.FlatStateRejected("unsupported_graph")
    )
    assert document == original  # nosec B101
