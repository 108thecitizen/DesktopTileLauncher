# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

import config_runtime_v2 as runtime
import config_schema as v1
import config_schema_v2 as v2
import config_serialization_v2 as serialization
import config_transform_v1_to_v2 as transform

pytestmark = pytest.mark.unit

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "schema_v2"

WORKSPACE_1 = "10000000-0000-4000-8000-000000000001"
WORKSPACE_2 = "10000000-0000-4000-8000-000000000002"
WORKSPACE_3 = "10000000-0000-4000-8000-000000000003"
TAB_1 = "20000000-0000-4000-8000-000000000001"
TAB_2 = "20000000-0000-4000-8000-000000000002"
TAB_3 = "20000000-0000-4000-8000-000000000003"
TAB_4 = "20000000-0000-4000-8000-000000000004"
TAB_5 = "20000000-0000-4000-8000-000000000005"
RESOURCE_1 = "30000000-0000-4000-8000-000000000001"
RESOURCE_2 = "30000000-0000-4000-8000-000000000002"
PLACEMENT_1 = "40000000-0000-4000-8000-000000000001"
PLACEMENT_2 = "40000000-0000-4000-8000-000000000002"
PLACEMENT_3 = "40000000-0000-4000-8000-000000000003"
PLACEMENT_4 = "40000000-0000-4000-8000-000000000004"
PLACEMENT_5 = "40000000-0000-4000-8000-000000000005"

V1_WORKSPACE = "11111111-1111-4111-8111-111111111111"
V1_HIGH_TAB = "ffffffff-ffff-4fff-8fff-ffffffffffff"
V1_LOW_TAB = "00000000-0000-4000-8000-000000000001"


def _representative() -> v2.Root:
    parsed = json.loads(
        (FIXTURE_ROOT / "representative.json").read_text(encoding="utf-8"),
        object_pairs_hook=v2.reject_duplicate_json_members,
    )
    assert v2.validate_v2(parsed)  # nosec B101
    return cast(v2.Root, parsed)


def _project(
    document: object,
    workspace_id: str | None = None,
    external_device_key: str | None = None,
) -> runtime.WorkspaceProjection:
    result = runtime.project_workspace(
        document,
        workspace_id,
        external_device_key=external_device_key,
    )
    assert isinstance(result, runtime.WorkspaceProjection)  # nosec B101
    return result


def _tab(
    projection: runtime.WorkspaceProjection,
    tab_id: str,
) -> runtime.TabProjection:
    matches = [tab for tab in projection.tabs if tab.id == tab_id]
    assert len(matches) == 1  # nosec B101
    return matches[0]


def _placement(
    projection: runtime.WorkspaceProjection,
    placement_id: str,
) -> runtime.PlacementProjection:
    matches = [
        placement
        for tab in projection.tabs
        for placement in tab.placements
        if placement.id == placement_id
    ]
    assert len(matches) == 1  # nosec B101
    return matches[0]


def _serialized(document: object) -> bytes:
    result = serialization.serialize_v2(document)
    assert isinstance(result, serialization.SerializedV2Document)  # nosec B101
    return result.data


def _deep_extension(depth: int, leaf: str) -> v2.Extensions:
    root: v2.Extensions = {}
    current = root
    for _ in range(depth):
        child: v2.Extensions = {}
        current["next"] = child
        current = child
    current["leaf"] = leaf
    return root


def _deep_extension_leaf(value: object, depth: int) -> object:
    current = value
    for _ in range(depth):
        assert type(current) is dict  # nosec B101
        current = cast(dict[str, object], current)["next"]
    assert type(current) is dict  # nosec B101
    return cast(dict[str, object], current)["leaf"]


def _json_container_ids(value: object) -> set[int]:
    pending = [value]
    identifiers: set[int] = set()
    while pending:
        current = pending.pop()
        if type(current) is dict:
            identifier = id(current)
            if identifier in identifiers:
                continue
            identifiers.add(identifier)
            pending.extend(cast(dict[object, object], current).values())
        elif type(current) is list:
            identifier = id(current)
            if identifier in identifiers:
                continue
            identifiers.add(identifier)
            pending.extend(cast(list[object], current))
    return identifiers


def _window_values(
    settings: runtime.WindowSettingsProjection | None,
) -> tuple[int, bool, int | None, int | None, int | None, int | None] | None:
    if settings is None:
        return None
    return (
        settings.columns,
        settings.auto_fit,
        settings.window_x,
        settings.window_y,
        settings.window_w,
        settings.window_h,
    )


def _launch_values(
    settings: runtime.LaunchSettingsProjection | None,
) -> tuple[str | None, str | None, v2.OpenTarget] | None:
    if settings is None:
        return None
    return settings.browser, settings.chrome_profile, settings.open_target


def _v1_tile(
    name: str,
    url: str,
    tab_id: str,
    *,
    icon: str | None,
    background: str,
    browser: str | None,
    chrome_profile: str | None,
    open_target: v2.OpenTarget,
) -> v1.JsonObject:
    return {
        "name": name,
        "url": url,
        "tab_id": tab_id,
        "icon": icon,
        "bg": background,
        "browser": browser,
        "chrome_profile": chrome_profile,
        "open_target": open_target,
    }


def _strict_v1_source() -> v1.JsonObject:
    return {
        "schema_version": 1,
        "application": {
            "title": "Migrated runtime",
            "default_workspace_id": V1_WORKSPACE,
            "extensions": {},
        },
        "workspaces": [
            {
                "id": V1_WORKSPACE,
                "name": "Default Workspace",
                "tab_order": [V1_HIGH_TAB, V1_LOW_TAB],
                "extensions": {},
            }
        ],
        "tabs": [
            {
                "id": V1_LOW_TAB,
                "workspace_id": V1_WORKSPACE,
                "name": "Low",
                "visibility": "hidden",
                "extensions": {},
            },
            {
                "id": V1_HIGH_TAB,
                "workspace_id": V1_WORKSPACE,
                "name": "High",
                "visibility": "visible",
                "extensions": {},
            },
        ],
        "tiles": [
            _v1_tile(
                "low-0",
                "custom+low://opaque",
                V1_LOW_TAB,
                icon="",
                background="",
                browser="",
                chrome_profile=None,
                open_target="window",
            ),
            _v1_tile(
                "high-0",
                "https://high.example.test/0",
                V1_HIGH_TAB,
                icon=None,
                background="#123456",
                browser=None,
                chrome_profile="",
                open_target="tab",
            ),
            _v1_tile(
                "low-1",
                "https://low.example.test/1",
                V1_LOW_TAB,
                icon="low-icon",
                background="not-a-color",
                browser="firefox",
                chrome_profile="Profile L",
                open_target="tab",
            ),
            _v1_tile(
                "high-1",
                "",
                V1_HIGH_TAB,
                icon="high-icon",
                background="blue-ish",
                browser="Unsupported Browser",
                chrome_profile="   ",
                open_target="window",
            ),
        ],
        "columns": -2,
        "auto_fit": True,
        "window_x": 0,
        "window_y": -40,
        "window_w": None,
        "window_h": 900,
        "extensions": {},
    }


def test_projection_uses_persisted_identity_and_semantic_orders() -> None:
    projection = _project(_representative())

    assert projection.application_title == "Café 東京"  # nosec B101
    assert projection.id == WORKSPACE_1  # nosec B101
    assert projection.name == "Primary"  # nosec B101
    assert [tab.id for tab in projection.tabs] == [  # nosec B101
        TAB_1,
        TAB_2,
        TAB_3,
        TAB_4,
    ]
    assert [tab.id for tab in projection.normal_tabs] == [TAB_1]  # nosec B101

    first = _tab(projection, TAB_1)
    assert (first.visibility, first.lifecycle, first.view_mode) == (  # nosec B101
        "visible",
        "active",
        "display",
    )
    assert first.display_filter == ("new", "in_use")  # nosec B101
    assert [placement.id for placement in first.placements] == [  # nosec B101
        PLACEMENT_2,
        PLACEMENT_1,
        PLACEMENT_3,
    ]
    assert [placement.id for placement in first.displayed_placements] == [  # nosec B101
        PLACEMENT_2,
        PLACEMENT_1,
    ]

    archived = _tab(projection, TAB_2)
    assert (archived.visibility, archived.lifecycle) == (  # nosec B101
        "visible",
        "archived",
    )
    assert [placement.id for placement in archived.placements] == [PLACEMENT_4]  # nosec B101
    assert archived.displayed_placements == ()  # nosec B101

    hidden = _tab(projection, TAB_3)
    assert (hidden.visibility, hidden.lifecycle, hidden.placements) == (  # nosec B101
        "hidden",
        "active",
        (),
    )

    secondary = _project(_representative(), WORKSPACE_2)
    assert [tab.id for tab in secondary.tabs] == [TAB_5]  # nosec B101
    assert [tab.id for tab in secondary.normal_tabs] == [TAB_5]  # nosec B101


@pytest.mark.parametrize(
    "field",
    ["workspaces", "tabs", "resources", "placements", "device_bindings"],
)
def test_root_definition_order_does_not_change_projection(field: str) -> None:
    document = _representative()
    reordered = deepcopy(document)
    values = cast(list[v2.StrictJsonValue], reordered[field])
    values.reverse()

    assert v2.validate_v2(reordered)  # nosec B101
    for device_key in (None, "device-A"):
        assert _project(  # nosec B101
            reordered,
            external_device_key=device_key,
        ) == _project(document, external_device_key=device_key)


def test_presentation_distinguishes_inheritance_from_explicit_empty_values() -> None:
    document = _representative()
    document["resources"][0]["default_icon"] = v2.LegacyStringIcon(
        kind="legacy_string",
        value="resource-icon",
    )
    assert v2.validate_v2(document)  # nosec B101

    primary = _project(document)
    inherited = _placement(primary, PLACEMENT_1)
    explicit_empty = _placement(primary, PLACEMENT_2)
    secondary = _placement(_project(document, WORKSPACE_2), PLACEMENT_5)

    assert (  # nosec B101
        inherited.label,
        inherited.icon,
        inherited.background_color,
        inherited.workflow_status,
    ) == ("Shared", "resource-icon", "", "new")
    assert (explicit_empty.label, explicit_empty.icon) == ("", "")  # nosec B101
    assert (secondary.label, secondary.icon) == (  # nosec B101
        "Secondary placement",
        "resource-icon",
    )


@pytest.mark.parametrize(
    ("workspace_id", "device_key", "expected"),
    [
        (WORKSPACE_1, "device-A", (0, False, None, None, None, None)),
        (WORKSPACE_1, None, (-1, True, -20, 0, None, 900)),
        (WORKSPACE_1, "device-a", (-1, True, -20, 0, None, 900)),
        (WORKSPACE_1, "device-A ", (-1, True, -20, 0, None, 900)),
        (WORKSPACE_2, "   ", (5, True, 1, 2, 3, 4)),
        (WORKSPACE_2, None, None),
        (WORKSPACE_2, "other-device", None),
        (WORKSPACE_3, None, None),
    ],
)
def test_window_binding_precedence_is_exact_and_case_sensitive(
    workspace_id: str,
    device_key: str | None,
    expected: tuple[int, bool, int | None, int | None, int | None, int | None] | None,
) -> None:
    projection = _project(_representative(), workspace_id, device_key)

    assert _window_values(projection.window_settings) == expected  # nosec B101


@pytest.mark.parametrize(
    ("workspace_id", "placement_id", "device_key", "expected"),
    [
        (
            WORKSPACE_1,
            PLACEMENT_1,
            "device-A",
            ("Unsupported Browser", "   ", "window"),
        ),
        (WORKSPACE_1, PLACEMENT_1, None, (None, "", "tab")),
        (WORKSPACE_1, PLACEMENT_1, "device-a", (None, "", "tab")),
        (WORKSPACE_1, PLACEMENT_2, "device-A", None),
        (WORKSPACE_2, PLACEMENT_5, "device-A", ("", None, "tab")),
        (WORKSPACE_2, PLACEMENT_5, None, None),
    ],
)
def test_launch_binding_precedence_preserves_presence_and_absence(
    workspace_id: str,
    placement_id: str,
    device_key: str | None,
    expected: tuple[str | None, str | None, v2.OpenTarget] | None,
) -> None:
    projection = _project(_representative(), workspace_id, device_key)

    assert (  # nosec B101
        _launch_values(_placement(projection, placement_id).launch_settings) == expected
    )


def test_selected_settings_replace_fallback_settings_without_field_merge() -> None:
    document = _representative()
    bindings = document["device_bindings"]
    portable = cast(v2.PlacementLaunchBinding, bindings[3])
    exact = cast(v2.PlacementLaunchBinding, bindings[4])
    portable["settings"] = v2.UrlLaunchSettings(
        browser="fallback-browser",
        chrome_profile="fallback-profile",
        open_target="tab",
    )
    exact["settings"] = v2.UrlLaunchSettings(
        browser=None,
        chrome_profile=None,
        open_target="window",
    )
    assert v2.validate_v2(document)  # nosec B101

    selected = _placement(
        _project(document, external_device_key="device-A"),
        PLACEMENT_1,
    )
    fallback = _placement(
        _project(document, external_device_key="nonmatching"),
        PLACEMENT_1,
    )

    assert _launch_values(selected.launch_settings) == (None, None, "window")  # nosec B101
    assert _launch_values(fallback.launch_settings) == (  # nosec B101
        "fallback-browser",
        "fallback-profile",
        "tab",
    )


def test_invalid_external_device_key_is_distinct_from_no_device_key() -> None:
    document = _representative()

    invalid = runtime.project_workspace(document, external_device_key="")
    without_key = runtime.project_workspace(document, external_device_key=None)

    assert isinstance(invalid, runtime.RuntimeAdapterRejected)  # nosec B101
    assert invalid.category == "invalid_device_key"  # nosec B101
    assert isinstance(without_key, runtime.WorkspaceProjection)  # nosec B101


def test_refresh_is_detached_and_changes_only_referenced_resource_defaults() -> None:
    source = _representative()
    source_bytes = _serialized(source)
    expected = deepcopy(source)
    expected_resource = next(
        resource for resource in expected["resources"] if resource["id"] == RESOURCE_1
    )
    expected_resource["default_label"] = "Refreshed default"
    expected_resource["default_icon"] = v2.LegacyStringIcon(
        kind="legacy_string",
        value="refreshed-icon",
    )

    result = runtime.apply_metadata_refresh(
        source,
        PLACEMENT_2,
        label="Refreshed default",
        icon="refreshed-icon",
    )

    assert not isinstance(result, runtime.RuntimeAdapterRejected)  # nosec B101
    refreshed = cast(v2.Root, result)
    assert refreshed == expected  # nosec B101
    assert refreshed is not source  # nosec B101
    assert refreshed["resources"] is not source["resources"]  # nosec B101
    assert refreshed["placements"] is not source["placements"]  # nosec B101
    assert _serialized(source) == source_bytes  # nosec B101
    assert _serialized(refreshed) == _serialized(expected)  # nosec B101
    assert v2.validate_v2(refreshed)  # nosec B101

    refreshed["application"]["title"] = "detached mutation"
    assert source["application"]["title"] == "Café 東京"  # nosec B101


def test_refresh_detaches_extensions_beyond_deepcopy_recursion_depth() -> None:
    depth = 1_750
    leaf = "deep-extension-leaf"
    source = _representative()
    source["extensions"]["deep.example.test"] = _deep_extension(depth, leaf)
    assert v2.validate_v2(source)  # nosec B101

    original_resource = next(
        resource for resource in source["resources"] if resource["id"] == RESOURCE_1
    )
    result = runtime.apply_metadata_refresh(
        source,
        PLACEMENT_1,
        label="Deep refresh",
        icon="deep-refresh-icon",
    )

    assert not isinstance(result, runtime.RuntimeAdapterRejected)  # nosec B101
    refreshed = cast(v2.Root, result)
    refreshed_resource = next(
        resource for resource in refreshed["resources"] if resource["id"] == RESOURCE_1
    )
    assert _json_container_ids(source).isdisjoint(  # nosec B101
        _json_container_ids(refreshed)
    )
    assert (  # nosec B101
        _deep_extension_leaf(source["extensions"]["deep.example.test"], depth) == leaf
    )
    assert (  # nosec B101
        _deep_extension_leaf(refreshed["extensions"]["deep.example.test"], depth)
        == leaf
    )
    assert original_resource["default_label"] == "Shared"  # nosec B101
    assert original_resource["default_icon"] is None  # nosec B101
    assert refreshed_resource["default_label"] == "Deep refresh"  # nosec B101
    assert refreshed_resource["default_icon"] == {  # nosec B101
        "kind": "legacy_string",
        "value": "deep-refresh-icon",
    }
    assert v2.validate_v2(refreshed)  # nosec B101


@pytest.mark.parametrize(
    ("label", "icon", "expected_label", "expected_icon"),
    [
        ("label-only", None, "label-only", "existing-icon"),
        (None, "icon-only", "Shared", "icon-only"),
        (None, None, "Shared", "existing-icon"),
        ("", "", "", ""),
    ],
)
def test_refresh_updates_label_and_icon_independently(
    label: str | None,
    icon: str | None,
    expected_label: str,
    expected_icon: str,
) -> None:
    source = _representative()
    source_resource = next(
        resource for resource in source["resources"] if resource["id"] == RESOURCE_1
    )
    source_resource["default_icon"] = v2.LegacyStringIcon(
        kind="legacy_string",
        value="existing-icon",
    )
    assert v2.validate_v2(source)  # nosec B101

    result = runtime.apply_metadata_refresh(
        source,
        PLACEMENT_1,
        label=label,
        icon=icon,
    )

    assert not isinstance(result, runtime.RuntimeAdapterRejected)  # nosec B101
    refreshed = cast(v2.Root, result)
    refreshed_resource = next(
        resource for resource in refreshed["resources"] if resource["id"] == RESOURCE_1
    )
    assert _json_container_ids(source).isdisjoint(  # nosec B101
        _json_container_ids(refreshed)
    )
    assert source_resource["default_label"] == "Shared"  # nosec B101
    assert source_resource["default_icon"] == {  # nosec B101
        "kind": "legacy_string",
        "value": "existing-icon",
    }
    assert refreshed_resource["default_label"] == expected_label  # nosec B101
    assert refreshed_resource["default_icon"] == {  # nosec B101
        "kind": "legacy_string",
        "value": expected_icon,
    }
    assert v2.validate_v2(refreshed)  # nosec B101


def test_shared_resource_refresh_keeps_placement_overrides_independent() -> None:
    result = runtime.apply_metadata_refresh(
        _representative(),
        PLACEMENT_2,
        label="Refreshed default",
        icon="refreshed-icon",
    )
    assert not isinstance(result, runtime.RuntimeAdapterRejected)  # nosec B101

    primary = _project(result)
    secondary = _project(result, WORKSPACE_2)
    inherited = _placement(primary, PLACEMENT_1)
    explicit_empty = _placement(primary, PLACEMENT_2)
    independent_label = _placement(secondary, PLACEMENT_5)

    assert (inherited.label, inherited.icon) == (  # nosec B101
        "Refreshed default",
        "refreshed-icon",
    )
    assert (explicit_empty.label, explicit_empty.icon) == ("", "")  # nosec B101
    assert (independent_label.label, independent_label.icon) == (  # nosec B101
        "Secondary placement",
        "refreshed-icon",
    )


def _duplicate_resource_id(document: v2.Root) -> None:
    document["resources"].append(deepcopy(document["resources"][0]))


def _dangling_resource_reference(document: v2.Root) -> None:
    document["placements"][-1]["resource_id"] = "90000000-0000-4000-8000-000000000001"


def _incomplete_display_order(document: v2.Root) -> None:
    document["tabs"][0]["display_order"].pop()


def _duplicate_binding_selector(document: v2.Root) -> None:
    duplicate = deepcopy(document["device_bindings"][0])
    duplicate["id"] = "50000000-0000-4000-8000-000000000099"
    document["device_bindings"].append(duplicate)


def _invalid_unrelated_orphan(document: v2.Root) -> None:
    cast(dict[str, object], document["resources"][2])["unexpected"] = "private"


@pytest.mark.parametrize(
    "mutation",
    [
        _duplicate_resource_id,
        _dangling_resource_reference,
        _incomplete_display_order,
        _duplicate_binding_selector,
        _invalid_unrelated_orphan,
    ],
)
def test_complete_invalid_graphs_fail_closed_without_partial_results(
    mutation: Callable[[v2.Root], None],
) -> None:
    document = _representative()
    mutation(document)
    assert not v2.validate_v2(document)  # nosec B101

    projected = runtime.project_workspace(document, WORKSPACE_1)
    refreshed = runtime.apply_metadata_refresh(
        document,
        PLACEMENT_1,
        label="unused",
    )

    assert isinstance(projected, runtime.RuntimeAdapterRejected)  # nosec B101
    assert projected.category == "invalid_graph"  # nosec B101
    assert isinstance(refreshed, runtime.RuntimeAdapterRejected)  # nosec B101
    assert refreshed.category == "invalid_graph"  # nosec B101


def test_subject_and_refresh_failures_use_fixed_categories() -> None:
    document = _representative()

    missing_workspace = runtime.project_workspace(
        document,
        "90000000-0000-4000-8000-000000000002",
    )
    missing_placement = runtime.apply_metadata_refresh(
        document,
        "90000000-0000-4000-8000-000000000003",
        label="unused",
    )
    invalid_refresh = runtime.apply_metadata_refresh(
        document,
        PLACEMENT_1,
        label="\ud800",
    )

    assert isinstance(missing_workspace, runtime.RuntimeAdapterRejected)  # nosec B101
    assert missing_workspace.category == "subject_not_found"  # nosec B101
    assert isinstance(missing_placement, runtime.RuntimeAdapterRejected)  # nosec B101
    assert missing_placement.category == "subject_not_found"  # nosec B101
    assert isinstance(invalid_refresh, runtime.RuntimeAdapterRejected)  # nosec B101
    assert invalid_refresh.category == "refresh_failure"  # nosec B101


def test_adapter_rejections_are_silent_and_content_free(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinels = (
        "https://private-runtime.example.test/path",
        "private-runtime-label",
        "private-runtime-icon",
        "private-device-key",
        "private-extension-payload",
    )
    document = _representative()
    document["resources"][0]["target"]["url"] = sentinels[0]
    document["resources"][0]["default_label"] = sentinels[1]
    document["resources"][0]["default_icon"] = v2.LegacyStringIcon(
        kind="legacy_string",
        value=sentinels[2],
    )
    exact = cast(v2.DeviceSpecific, document["device_bindings"][1]["applicability"])
    exact["device_key"] = sentinels[3]
    document["extensions"][sentinels[4]] = {"unexpected": True}
    cast(dict[str, object], document["resources"][2])["unexpected"] = sentinels[4]
    caplog.set_level(logging.DEBUG)
    capsys.readouterr()
    caplog.clear()

    projection = runtime.project_workspace(
        document,
        external_device_key=sentinels[3],
    )
    refresh = runtime.apply_metadata_refresh(
        document,
        PLACEMENT_1,
        label=sentinels[1],
        icon=sentinels[2],
    )

    assert isinstance(projection, runtime.RuntimeAdapterRejected)  # nosec B101
    assert isinstance(refresh, runtime.RuntimeAdapterRejected)  # nosec B101
    rendered = repr((projection, refresh))
    captured = capsys.readouterr()
    assert captured.out == ""  # nosec B101
    assert captured.err == ""  # nosec B101
    for sentinel in sentinels:
        assert sentinel not in rendered  # nosec B101
        assert sentinel not in caplog.text  # nosec B101


def test_strict_v1_transform_projects_equivalent_url_runtime_behavior() -> None:
    source = _strict_v1_source()
    transformed = transform.transform_v1_to_v2(source)
    assert not isinstance(  # nosec B101
        transformed,
        serialization.V2SerializationRejected,
    )
    assert transformed is not None  # nosec B101
    candidate = cast(v2.Root, transformed)
    projection = _project(candidate)

    assert projection.application_title == source["application"]["title"]  # nosec B101[index]
    assert projection.id == V1_WORKSPACE  # nosec B101
    assert projection.name == "Default Workspace"  # nosec B101
    assert [tab.id for tab in projection.tabs] == [V1_HIGH_TAB, V1_LOW_TAB]  # nosec B101
    assert [tab.id for tab in projection.normal_tabs] == [V1_HIGH_TAB]  # nosec B101
    assert _window_values(projection.window_settings) == (  # nosec B101
        -2,
        True,
        0,
        -40,
        None,
        900,
    )

    source_tabs = {
        tab["id"]: tab for tab in cast(list[dict[str, v1.JsonValue]], source["tabs"])
    }
    source_tiles = cast(list[dict[str, v1.JsonValue]], source["tiles"])
    for projected_tab in projection.tabs:
        source_tab = source_tabs[projected_tab.id]
        expected_tiles = [
            tile for tile in source_tiles if tile["tab_id"] == projected_tab.id
        ]
        assert projected_tab.name == source_tab["name"]  # nosec B101
        assert projected_tab.visibility == source_tab["visibility"]  # nosec B101
        assert projected_tab.lifecycle == "active"  # nosec B101
        assert projected_tab.view_mode == "display"  # nosec B101
        assert projected_tab.display_filter == ("new", "in_use")  # nosec B101
        assert projected_tab.displayed_placements == projected_tab.placements  # nosec B101
        actual_tiles = [
            (
                placement.url,
                placement.label,
                placement.icon,
                placement.background_color,
                placement.workflow_status,
                _launch_values(placement.launch_settings),
            )
            for placement in projected_tab.placements
        ]
        expected_runtime = [
            (
                tile["url"],
                tile["name"],
                tile["icon"],
                tile["bg"],
                "in_use",
                (tile["browser"], tile["chrome_profile"], tile["open_target"]),
            )
            for tile in expected_tiles
        ]
        assert actual_tiles == expected_runtime  # nosec B101


def test_transformed_v1_refresh_changes_only_legacy_name_and_icon_behavior() -> None:
    transformed = transform.transform_v1_to_v2(_strict_v1_source())
    assert transformed is not None  # nosec B101
    assert not isinstance(  # nosec B101
        transformed,
        serialization.V2SerializationRejected,
    )
    candidate = cast(v2.Root, transformed)
    before = _project(candidate)
    original = _tab(before, V1_HIGH_TAB).placements[0]

    result = runtime.apply_metadata_refresh(
        candidate,
        original.id,
        label="Refreshed migrated label",
        icon="refreshed-migrated-icon",
    )
    assert not isinstance(result, runtime.RuntimeAdapterRejected)  # nosec B101
    after = _placement(_project(result), original.id)

    assert (after.label, after.icon) == (  # nosec B101
        "Refreshed migrated label",
        "refreshed-migrated-icon",
    )
    assert (  # nosec B101
        after.id,
        after.resource_id,
        after.tab_id,
        after.url,
        after.background_color,
        after.workflow_status,
        _launch_values(after.launch_settings),
    ) == (
        original.id,
        original.resource_id,
        original.tab_id,
        original.url,
        original.background_color,
        original.workflow_status,
        _launch_values(original.launch_settings),
    )
    assert _project(candidate) == before  # nosec B101
