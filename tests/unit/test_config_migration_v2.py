# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import NoReturn, cast
from uuid import UUID, uuid5

import pytest

import config_migration as engine
import config_migration_v2 as migration
import config_schema as v1
import config_schema_v2 as v2
import config_serialization_v2 as serialization
import config_transform_v1_to_v2 as checked

pytestmark = pytest.mark.unit

WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
MAIN_TAB_ID = "22222222-2222-4222-8222-222222222222"
LOW_TAB_ID = "00000000-0000-4000-8000-000000000001"
HIGH_TAB_ID = "ffffffff-ffff-4fff-8fff-ffffffffffff"
RESOURCE_ID = "f6c4f407-1e69-55a9-b68d-1e509b5ed9e3"
PLACEMENT_ID = "c58596f5-047d-543a-b39c-33c2c366d364"
WORKSPACE_BINDING_ID = "3f99d908-f507-5905-9d68-e664e6a8a25e"
LAUNCH_BINDING_ID = "a8f543e0-fdb2-5033-b8ca-7982d96a5399"


def _tile(
    name: str,
    url: str,
    tab_id: str,
    *,
    icon: str | None = None,
    bg: str = "#F5F6FA",
    browser: str | None = None,
    chrome_profile: str | None = None,
    open_target: str = "tab",
) -> v1.JsonObject:
    return {
        "name": name,
        "url": url,
        "tab_id": tab_id,
        "icon": icon,
        "bg": bg,
        "browser": browser,
        "chrome_profile": chrome_profile,
        "open_target": open_target,
    }


def _one_tile_source() -> v1.JsonObject:
    return {
        "schema_version": 1,
        "application": {
            "title": "My Launcher",
            "default_workspace_id": WORKSPACE_ID,
            "extensions": {},
        },
        "workspaces": [
            {
                "id": WORKSPACE_ID,
                "name": "Default Workspace",
                "tab_order": [MAIN_TAB_ID],
                "extensions": {},
            }
        ],
        "tabs": [
            {
                "id": MAIN_TAB_ID,
                "workspace_id": WORKSPACE_ID,
                "name": "Main",
                "visibility": "visible",
                "extensions": {},
            }
        ],
        "tiles": [
            _tile(
                "ChatGPT",
                "https://chat.openai.com",
                MAIN_TAB_ID,
            )
        ],
        "columns": 5,
        "auto_fit": True,
        "window_x": None,
        "window_y": None,
        "window_w": None,
        "window_h": None,
        "extensions": {},
    }


def _two_tab_source() -> v1.JsonObject:
    return {
        "schema_version": 1,
        "application": {
            "title": "Ordering",
            "default_workspace_id": WORKSPACE_ID,
            "extensions": {},
        },
        "workspaces": [
            {
                "id": WORKSPACE_ID,
                "name": "Current Workspace Name",
                "tab_order": [HIGH_TAB_ID, LOW_TAB_ID],
                "extensions": {},
            }
        ],
        "tabs": [
            {
                "id": LOW_TAB_ID,
                "workspace_id": WORKSPACE_ID,
                "name": "Low",
                "visibility": "hidden",
                "extensions": {},
            },
            {
                "id": HIGH_TAB_ID,
                "workspace_id": WORKSPACE_ID,
                "name": "High",
                "visibility": "visible",
                "extensions": {},
            },
        ],
        "tiles": [
            _tile("low-0", "https://low.example/0", LOW_TAB_ID),
            _tile("high-0", "https://high.example/0", HIGH_TAB_ID),
            _tile("low-1", "https://low.example/1", LOW_TAB_ID),
            _tile("high-1", "https://high.example/1", HIGH_TAB_ID),
        ],
        "columns": -2,
        "auto_fit": True,
        "window_x": 0,
        "window_y": -40,
        "window_w": None,
        "window_h": 900,
        "extensions": {},
    }


def _candidate(source: v1.JsonObject) -> v2.Root:
    result = migration.migrate_v1_to_v2(source)
    assert result is not None  # nosec B101
    return result


def _canonical_bytes(document: object) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")


def _reverse_object_insertions(value: object) -> object:
    if type(value) is dict:
        return {
            key: _reverse_object_insertions(item)
            for key, item in reversed(list(cast(dict[str, object], value).items()))
        }
    if type(value) is list:
        return [_reverse_object_insertions(item) for item in cast(list[object], value)]
    return value


def _generated_ids(candidate: v2.Root) -> tuple[str, ...]:
    return tuple(
        [resource["id"] for resource in candidate["resources"]]
        + [placement["id"] for placement in candidate["placements"]]
        + [binding["id"] for binding in candidate["device_bindings"]]
    )


def _replace_workspace_id(source: v1.JsonObject, replacement: str) -> None:
    application = cast(dict[str, v1.JsonValue], source["application"])
    workspaces = cast(list[dict[str, v1.JsonValue]], source["workspaces"])
    tabs = cast(list[dict[str, v1.JsonValue]], source["tabs"])
    application["default_workspace_id"] = replacement
    workspaces[0]["id"] = replacement
    for tab in tabs:
        tab["workspace_id"] = replacement


def _test_registry(
    *,
    uuid5_factory: v1.Uuid5Factory = uuid5,
    target_accepts: bool = True,
) -> engine.ValidatedMigrationRegistry:
    def validate_source(
        document: Mapping[str, engine.JsonValue],
    ) -> engine.ValidationDecision:
        if v1.validate_v1(document):
            return engine.ValidationAccepted()
        return engine.ValidationRejected()

    def transform_source(
        document: Mapping[str, engine.JsonValue],
    ) -> engine.StepDecision:
        candidate = migration.migrate_v1_to_v2(
            document,
            uuid5_factory=uuid5_factory,
        )
        if candidate is None:
            return engine.StepRejected()
        return engine.StepApplied(cast(engine.JsonObject, candidate))

    def validate_target(
        document: Mapping[str, engine.JsonValue],
    ) -> engine.ValidationDecision:
        if target_accepts and v2.validate_v2(document):
            return engine.ValidationAccepted()
        return engine.ValidationRejected()

    result = engine.validate_registry(
        engine.RegistrySpec(
            1,
            2,
            (engine.MigrationStep(1, 2, "v1_to_v2", transform_source),),
            (
                engine.VersionValidator(1, validate_source),
                engine.VersionValidator(2, validate_target),
            ),
        )
    )
    if not isinstance(result, engine.RegistryReady):
        raise AssertionError("test registry must be valid")
    return result.registry


def _execute_test_migration(
    source: v1.JsonObject,
    *,
    uuid5_factory: v1.Uuid5Factory = uuid5,
    target_accepts: bool = True,
) -> engine.ExecutionResult:
    prepared = engine.prepare_migration(
        source,
        _test_registry(
            uuid5_factory=uuid5_factory,
            target_accepts=target_accepts,
        ),
    )
    if not isinstance(prepared, engine.PreparedMigration):
        raise AssertionError("test source must prepare a migration")
    return engine.execute_prepared_migration(prepared)


def test_adr_oracle_builds_the_exact_complete_graph_and_canonical_bytes() -> None:
    source = _one_tile_source()
    original = deepcopy(source)

    candidate = _candidate(source)

    expected: v2.Root = {
        "schema_version": 2,
        "application": {
            "title": "My Launcher",
            "default_workspace_id": WORKSPACE_ID,
            "extensions": {},
        },
        "workspaces": [
            {
                "id": WORKSPACE_ID,
                "name": "Default Workspace",
                "tab_order": [MAIN_TAB_ID],
                "extensions": {},
            }
        ],
        "tabs": [
            {
                "id": MAIN_TAB_ID,
                "workspace_id": WORKSPACE_ID,
                "name": "Main",
                "visibility": "visible",
                "lifecycle": "active",
                "view_mode": "display",
                "display_filter": ["new", "in_use"],
                "display_order": [PLACEMENT_ID],
                "kanban_order": {
                    "new": [],
                    "in_use": [PLACEMENT_ID],
                    "archived": [],
                },
                "extensions": {},
            }
        ],
        "resources": [
            {
                "id": RESOURCE_ID,
                "kind": "url",
                "target": {"url": "https://chat.openai.com"},
                "default_label": "ChatGPT",
                "default_icon": None,
                "extensions": {},
            }
        ],
        "placements": [
            {
                "id": PLACEMENT_ID,
                "resource_id": RESOURCE_ID,
                "tab_id": MAIN_TAB_ID,
                "label_override": None,
                "icon_override": None,
                "background_color": "#F5F6FA",
                "workflow_status": "in_use",
                "extensions": {},
            }
        ],
        "device_bindings": [
            {
                "id": WORKSPACE_BINDING_ID,
                "subject_kind": "workspace",
                "subject_id": WORKSPACE_ID,
                "binding_kind": "window",
                "applicability": {"kind": "portable_fallback"},
                "settings": {
                    "columns": 5,
                    "auto_fit": True,
                    "window_x": None,
                    "window_y": None,
                    "window_w": None,
                    "window_h": None,
                },
                "extensions": {},
            },
            {
                "id": LAUNCH_BINDING_ID,
                "subject_kind": "placement",
                "subject_id": PLACEMENT_ID,
                "binding_kind": "launch",
                "applicability": {"kind": "portable_fallback"},
                "settings": {
                    "browser": None,
                    "chrome_profile": None,
                    "open_target": "tab",
                },
                "extensions": {},
            },
        ],
        "extensions": {},
    }
    assert candidate == expected  # nosec B101
    assert source == original  # nosec B101
    assert v2.validate_v2(candidate)  # nosec B101

    serialized = serialization.serialize_v2(candidate)
    assert isinstance(  # nosec B101
        serialized,
        serialization.SerializedV2Document,
    )
    assert serialized.byte_count == 2499  # nosec B101
    assert hashlib.sha256(serialized.data).hexdigest() == (  # nosec B101
        "2ee1c4fb68ca530d77de90cac72ed6beff654c1f79f34550281ebf48f21dd6ff"
    )
    assert serialized.data == _canonical_bytes(candidate)  # nosec B101
    assert not serialized.data.endswith(b"\n")  # nosec B101


def test_zero_tiles_preserve_empty_tabs_and_create_only_window_binding() -> None:
    source = _two_tab_source()
    source["tiles"] = []

    candidate = _candidate(source)

    assert candidate["resources"] == []  # nosec B101
    assert candidate["placements"] == []  # nosec B101
    assert len(candidate["device_bindings"]) == 1  # nosec B101
    assert candidate["device_bindings"][0]["binding_kind"] == "window"  # nosec B101
    assert [tab["id"] for tab in candidate["tabs"]] == [  # nosec B101
        HIGH_TAB_ID,
        LOW_TAB_ID,
    ]
    for tab in candidate["tabs"]:
        assert tab["display_order"] == []  # nosec B101
        assert tab["kanban_order"] == {  # nosec B101
            "new": [],
            "in_use": [],
            "archived": [],
        }
    assert v2.validate_v2(candidate)  # nosec B101


def test_duplicate_tiles_remain_distinct_and_icon_values_remain_exact() -> None:
    source = _two_tab_source()
    duplicate = _tile(
        "same",
        "https://duplicate.example",
        HIGH_TAB_ID,
        icon="",
        bg=" ",
        browser="",
        chrome_profile=" ",
        open_target="window",
    )
    source["tiles"] = [
        deepcopy(duplicate),
        deepcopy(duplicate),
        _tile(
            "same",
            "https://duplicate.example",
            LOW_TAB_ID,
            icon=" ",
            bg=" ",
            browser="unsupported browser",
            chrome_profile=None,
        ),
        _tile("null-icon", "", LOW_TAB_ID, icon=None),
    ]

    candidate = _candidate(source)

    assert len(candidate["resources"]) == 4  # nosec B101
    assert len(candidate["placements"]) == 4  # nosec B101
    assert len(candidate["device_bindings"]) == 5  # nosec B101
    assert len({resource["id"] for resource in candidate["resources"]}) == 4  # nosec B101
    assert len({placement["id"] for placement in candidate["placements"]}) == 4  # nosec B101
    assert (
        len(  # nosec B101
            {binding["id"] for binding in candidate["device_bindings"]}
        )
        == 5
    )
    assert [resource["default_icon"] for resource in candidate["resources"]] == [  # nosec B101
        {"kind": "legacy_string", "value": ""},
        {"kind": "legacy_string", "value": ""},
        {"kind": "legacy_string", "value": " "},
        None,
    ]
    assert all(  # nosec B101
        resource["target"]["url"] == "https://duplicate.example"
        for resource in candidate["resources"][:3]
    )
    assert candidate["resources"][3]["target"]["url"] == ""  # nosec B101
    assert [placement["background_color"] for placement in candidate["placements"]] == [  # nosec B101
        " ",
        " ",
        " ",
        "#F5F6FA",
    ]
    window_settings = candidate["device_bindings"][0]["settings"]
    assert window_settings == {  # nosec B101
        "columns": -2,
        "auto_fit": True,
        "window_x": 0,
        "window_y": -40,
        "window_w": None,
        "window_h": 900,
    }
    assert (
        [  # nosec B101
            binding["settings"] for binding in candidate["device_bindings"][1:]
        ]
        == [
            {
                "browser": "",
                "chrome_profile": " ",
                "open_target": "window",
            },
            {
                "browser": "",
                "chrome_profile": " ",
                "open_target": "window",
            },
            {
                "browser": "unsupported browser",
                "chrome_profile": None,
                "open_target": "tab",
            },
            {
                "browser": None,
                "chrome_profile": None,
                "open_target": "tab",
            },
        ]
    )
    assert v2.validate_v2(candidate)  # nosec B101


def test_allocation_priority_and_candidate_emission_order_are_independent() -> None:
    source = _two_tab_source()
    calls: list[tuple[UUID, str]] = []

    def recording_uuid5(namespace: UUID, name: str) -> UUID:
        calls.append((namespace, name))
        return uuid5(namespace, name)

    candidate = migration.migrate_v1_to_v2(
        source,
        uuid5_factory=recording_uuid5,
    )

    assert candidate is not None  # nosec B101
    expected_resource_names = [
        f"dtl:migration:v1-to-v2:entity:resource:tab:{tab_id}:ordinal:{ordinal}:retry:0"
        for tab_id in (LOW_TAB_ID, HIGH_TAB_ID)
        for ordinal in range(2)
    ]
    expected_placement_names = [
        "dtl:migration:v1-to-v2:entity:placement:"
        f"tab:{tab_id}:ordinal:{ordinal}:retry:0"
        for tab_id in (LOW_TAB_ID, HIGH_TAB_ID)
        for ordinal in range(2)
    ]
    placement_ids_by_priority = [
        str(uuid5(migration.MIGRATION_NAMESPACE_V1_TO_V2, name))
        for name in expected_placement_names
    ]
    expected_binding_names = [
        "dtl:migration:v1-to-v2:entity:device-binding:"
        f"placement-launch:placement:{placement_id}:retry:0"
        for placement_id in placement_ids_by_priority
    ]
    assert [namespace for namespace, _ in calls] == [  # nosec B101
        migration.MIGRATION_NAMESPACE_V1_TO_V2
    ] * 13
    assert [name for _, name in calls] == (  # nosec B101
        expected_resource_names
        + expected_placement_names
        + [
            "dtl:migration:v1-to-v2:entity:device-binding:"
            f"workspace-window:workspace:{WORKSPACE_ID}:retry:0"
        ]
        + expected_binding_names
    )
    assert [tab["id"] for tab in candidate["tabs"]] == [  # nosec B101
        HIGH_TAB_ID,
        LOW_TAB_ID,
    ]
    assert candidate["workspaces"][0]["name"] == "Current Workspace Name"  # nosec B101
    assert [tab["visibility"] for tab in candidate["tabs"]] == [  # nosec B101
        "visible",
        "hidden",
    ]
    assert [resource["default_label"] for resource in candidate["resources"]] == [  # nosec B101
        "high-0",
        "high-1",
        "low-0",
        "low-1",
    ]
    placement_ids = [placement["id"] for placement in candidate["placements"]]
    assert candidate["tabs"][0]["display_order"] == placement_ids[:2]  # nosec B101
    assert candidate["tabs"][0]["kanban_order"]["in_use"] == placement_ids[:2]  # nosec B101
    assert candidate["tabs"][1]["display_order"] == placement_ids[2:]  # nosec B101
    assert candidate["tabs"][1]["kanban_order"]["in_use"] == placement_ids[2:]  # nosec B101
    assert (
        [  # nosec B101
            binding["subject_id"] for binding in candidate["device_bindings"][1:]
        ]
        == placement_ids
    )
    assert v2.validate_v2(candidate)  # nosec B101


def test_workspace_tab_order_changes_emission_but_not_locator_identities() -> None:
    first_source = _two_tab_source()
    second_source = deepcopy(first_source)
    second_workspace = cast(
        list[dict[str, v1.JsonValue]],
        second_source["workspaces"],
    )[0]
    second_workspace["tab_order"] = [LOW_TAB_ID, HIGH_TAB_ID]

    first = _candidate(first_source)
    second = _candidate(second_source)

    def identities_by_label(candidate: v2.Root) -> dict[str, tuple[str, str, str]]:
        placement_by_resource = {
            placement["resource_id"]: placement for placement in candidate["placements"]
        }
        launch_by_placement = {
            binding["subject_id"]: binding
            for binding in candidate["device_bindings"][1:]
        }
        return {
            resource["default_label"]: (
                resource["id"],
                placement_by_resource[resource["id"]]["id"],
                launch_by_placement[placement_by_resource[resource["id"]]["id"]]["id"],
            )
            for resource in candidate["resources"]
        }

    assert identities_by_label(first) == identities_by_label(second)  # nosec B101
    assert first["device_bindings"][0]["id"] == second["device_bindings"][0]["id"]  # nosec B101
    assert [tab["id"] for tab in first["tabs"]] == [HIGH_TAB_ID, LOW_TAB_ID]  # nosec B101
    assert [tab["id"] for tab in second["tabs"]] == [LOW_TAB_ID, HIGH_TAB_ID]  # nosec B101
    assert [resource["default_label"] for resource in first["resources"]] == [  # nosec B101
        "high-0",
        "high-1",
        "low-0",
        "low-1",
    ]
    assert [resource["default_label"] for resource in second["resources"]] == [  # nosec B101
        "low-0",
        "low-1",
        "high-0",
        "high-1",
    ]
    assert _canonical_bytes(first) != _canonical_bytes(second)  # nosec B101


def test_fixed_namespace_and_retry_zero_identity_vectors_are_exact() -> None:
    calls: list[str] = []

    def recording_uuid5(namespace: UUID, name: str) -> UUID:
        assert namespace == UUID(  # nosec B101
            "8cdeb2d4-8211-5078-9c60-90d397366383"
        )
        calls.append(name)
        return uuid5(namespace, name)

    candidate = migration.migrate_v1_to_v2(
        _one_tile_source(),
        uuid5_factory=recording_uuid5,
    )

    assert candidate is not None  # nosec B101
    assert calls == [  # nosec B101
        f"dtl:migration:v1-to-v2:entity:resource:tab:{MAIN_TAB_ID}:ordinal:0:retry:0",
        f"dtl:migration:v1-to-v2:entity:placement:tab:{MAIN_TAB_ID}:ordinal:0:retry:0",
        "dtl:migration:v1-to-v2:entity:device-binding:"
        f"workspace-window:workspace:{WORKSPACE_ID}:retry:0",
        "dtl:migration:v1-to-v2:entity:device-binding:"
        f"placement-launch:placement:{PLACEMENT_ID}:retry:0",
    ]
    assert _generated_ids(candidate) == (  # nosec B101
        RESOURCE_ID,
        PLACEMENT_ID,
        WORKSPACE_BINDING_ID,
        LAUNCH_BINDING_ID,
    )


def test_reserved_and_cross_family_collisions_use_the_exact_retry_name() -> None:
    reserved_source = _one_tile_source()
    _replace_workspace_id(reserved_source, RESOURCE_ID)
    reserved_calls: list[str] = []

    def reserved_recording_uuid5(namespace: UUID, name: str) -> UUID:
        reserved_calls.append(name)
        return uuid5(namespace, name)

    reserved_candidate = migration.migrate_v1_to_v2(
        reserved_source,
        uuid5_factory=reserved_recording_uuid5,
    )

    assert reserved_candidate is not None  # nosec B101
    assert reserved_calls[:2] == [  # nosec B101
        f"dtl:migration:v1-to-v2:entity:resource:tab:{MAIN_TAB_ID}:ordinal:0:retry:0",
        f"dtl:migration:v1-to-v2:entity:resource:tab:{MAIN_TAB_ID}:ordinal:0:retry:1",
    ]
    assert reserved_candidate["resources"][0]["id"] == (  # nosec B101
        "d9ba4755-c072-584f-b174-22c5fd018be8"
    )

    resource_retry_zero = UUID(RESOURCE_ID)
    placement_calls: list[str] = []

    def cross_family_collision(namespace: UUID, name: str) -> UUID:
        placement_calls.append(name)
        if ":entity:placement:" in name and name.endswith(":retry:0"):
            return resource_retry_zero
        return uuid5(namespace, name)

    cross_family_candidate = migration.migrate_v1_to_v2(
        _one_tile_source(),
        uuid5_factory=cross_family_collision,
    )

    assert cross_family_candidate is not None  # nosec B101
    assert any(name.endswith(":retry:1") for name in placement_calls)  # nosec B101
    expected_retry_one = uuid5(
        migration.MIGRATION_NAMESPACE_V1_TO_V2,
        f"dtl:migration:v1-to-v2:entity:placement:tab:{MAIN_TAB_ID}:ordinal:0:retry:1",
    )
    assert cross_family_candidate["placements"][0]["id"] == str(  # nosec B101
        expected_retry_one
    )
    assert (  # nosec B101
        "dtl:migration:v1-to-v2:entity:device-binding:"
        f"placement-launch:placement:{expected_retry_one}:retry:0" in placement_calls
    )


def test_both_device_binding_families_retry_against_the_global_id_set() -> None:
    calls: list[str] = []

    def collide_bindings(namespace: UUID, name: str) -> UUID:
        calls.append(name)
        if "workspace-window" in name and name.endswith(":retry:0"):
            return UUID(RESOURCE_ID)
        if "placement-launch" in name and name.endswith(":retry:0"):
            return UUID(PLACEMENT_ID)
        return uuid5(namespace, name)

    candidate = migration.migrate_v1_to_v2(
        _one_tile_source(),
        uuid5_factory=collide_bindings,
    )

    assert candidate is not None  # nosec B101
    workspace_retry_one_name = (
        "dtl:migration:v1-to-v2:entity:device-binding:"
        f"workspace-window:workspace:{WORKSPACE_ID}:retry:1"
    )
    launch_retry_one_name = (
        "dtl:migration:v1-to-v2:entity:device-binding:"
        f"placement-launch:placement:{PLACEMENT_ID}:retry:1"
    )
    assert workspace_retry_one_name in calls  # nosec B101
    assert launch_retry_one_name in calls  # nosec B101
    assert candidate["device_bindings"][0]["id"] == str(  # nosec B101
        uuid5(migration.MIGRATION_NAMESPACE_V1_TO_V2, workspace_retry_one_name)
    )
    assert candidate["device_bindings"][1]["id"] == str(  # nosec B101
        uuid5(migration.MIGRATION_NAMESPACE_V1_TO_V2, launch_retry_one_name)
    )


def test_collision_exhaustion_uses_exactly_retries_zero_through_thirty_one() -> None:
    source = _one_tile_source()
    _replace_workspace_id(source, RESOURCE_ID)
    calls: list[str] = []

    def always_reserved(_namespace: UUID, name: str) -> UUID:
        calls.append(name)
        return UUID(RESOURCE_ID)

    candidate = migration.migrate_v1_to_v2(
        source,
        uuid5_factory=always_reserved,
    )

    assert candidate is None  # nosec B101
    assert migration.V1_TO_V2_ID_ALLOCATION_ATTEMPTS == 32  # nosec B101
    assert calls == [  # nosec B101
        "dtl:migration:v1-to-v2:entity:resource:"
        f"tab:{MAIN_TAB_ID}:ordinal:0:retry:{retry}"
        for retry in range(32)
    ]
    assert not any(name.endswith(":retry:32") for name in calls)  # nosec B101


@pytest.mark.parametrize(
    "invalid_result",
    (
        UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        RESOURCE_ID.upper(),
        "not-a-uuid",
        object(),
    ),
)
def test_invalid_generated_uuid_output_fails_closed(invalid_result: object) -> None:
    def invalid_factory(_namespace: UUID, _name: str) -> object:
        return invalid_result

    result = migration.migrate_v1_to_v2(
        _one_tile_source(),
        uuid5_factory=cast(v1.Uuid5Factory, invalid_factory),
    )

    assert result is None  # nosec B101


def test_canonical_uuid5_string_factory_output_is_accepted() -> None:
    def string_factory(namespace: UUID, name: str) -> str:
        return str(uuid5(namespace, name))

    candidate = migration.migrate_v1_to_v2(
        _one_tile_source(),
        uuid5_factory=string_factory,
    )

    assert candidate is not None  # nosec B101
    assert _generated_ids(candidate) == (  # nosec B101
        RESOURCE_ID,
        PLACEMENT_ID,
        WORKSPACE_BINDING_ID,
        LAUNCH_BINDING_ID,
    )


def test_factory_exception_is_silent_private_and_returns_no_partial_graph(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "private-factory-failure-25a0af0d"

    def raising_factory(_namespace: UUID, _name: str) -> NoReturn:
        raise RuntimeError(sentinel)

    caplog.set_level(logging.DEBUG)
    capsys.readouterr()
    caplog.clear()

    result = _execute_test_migration(
        _one_tile_source(),
        uuid5_factory=raising_factory,
    )

    captured = capsys.readouterr()
    assert isinstance(result, engine.PureEngineDefect)  # nosec B101
    assert result.category is engine.PureEngineDefectCategory.CALLBACK_EXCEPTION  # nosec B101
    assert result.stage is engine.PureExecutionStage.STEP  # nosec B101
    assert captured.out == ""  # nosec B101
    assert captured.err == ""  # nosec B101
    assert sentinel not in caplog.text  # nosec B101
    assert sentinel not in repr(result)  # nosec B101


def test_identity_inputs_are_locator_only_and_never_include_source_values() -> None:
    first = _one_tile_source()
    first["extensions"] = {
        v1.LEGACY_EXTENSION_NAMESPACE: {"private-extension-a": [1, 2]}
    }
    second = deepcopy(first)
    second_tile = cast(list[dict[str, v1.JsonValue]], second["tiles"])[0]
    second_tile.update(
        {
            "name": "private-label-b",
            "url": "https://private-url-b.example/secret",
            "icon": "private-icon-b",
            "bg": "private-color-b",
            "browser": "private-browser-b",
            "chrome_profile": "private-profile-b",
            "open_target": "window",
        }
    )
    second["extensions"] = {
        v1.LEGACY_EXTENSION_NAMESPACE: {"private-extension-b": [2, 1]}
    }
    first_names: list[str] = []
    second_names: list[str] = []

    def first_factory(namespace: UUID, name: str) -> UUID:
        first_names.append(name)
        return uuid5(namespace, name)

    def second_factory(namespace: UUID, name: str) -> UUID:
        second_names.append(name)
        return uuid5(namespace, name)

    first_candidate = migration.migrate_v1_to_v2(
        first,
        uuid5_factory=first_factory,
    )
    second_candidate = migration.migrate_v1_to_v2(
        second,
        uuid5_factory=second_factory,
    )

    assert first_candidate is not None  # nosec B101
    assert second_candidate is not None  # nosec B101
    assert first_names == second_names  # nosec B101
    assert _generated_ids(first_candidate) == _generated_ids(  # nosec B101
        second_candidate
    )
    rendered_names = " ".join(first_names + second_names)
    for sentinel in (
        "private-label",
        "private-url",
        "private-icon",
        "private-color",
        "private-browser",
        "private-profile",
        "private-extension",
    ):
        assert sentinel not in rendered_names  # nosec B101


def test_logically_equivalent_sources_replay_to_identical_graph_and_bytes() -> None:
    first = _two_tab_source()
    first["extensions"] = {
        v1.LEGACY_EXTENSION_NAMESPACE: {
            "object": {"b": 2, "a": 1},
            "ordered": [2, 1],
        }
    }
    second = deepcopy(first)
    second["tiles"] = [
        cast(list[v1.JsonValue], first["tiles"])[1],
        cast(list[v1.JsonValue], first["tiles"])[0],
        cast(list[v1.JsonValue], first["tiles"])[3],
        cast(list[v1.JsonValue], first["tiles"])[2],
    ]
    second_tabs = cast(list[v1.JsonValue], second["tabs"])
    second["tabs"] = list(reversed(second_tabs))
    third = cast(
        v1.JsonObject,
        _reverse_object_insertions(deepcopy(second)),
    )

    candidates = [_candidate(source) for source in (first, second, third)]
    serialized = [serialization.serialize_v2(candidate) for candidate in candidates]

    assert candidates[0] == candidates[1] == candidates[2]  # nosec B101
    assert all(  # nosec B101
        isinstance(result, serialization.SerializedV2Document) for result in serialized
    )
    assert (
        len(  # nosec B101
            {
                cast(serialization.SerializedV2Document, result).data
                for result in serialized
            }
        )
        == 1
    )


def test_extensions_and_all_constructed_collections_are_detached() -> None:
    source = _two_tab_source()
    source["extensions"] = {
        v1.LEGACY_EXTENSION_NAMESPACE: {
            "schema_version": 99,
            "nested": {"ordered": [None, True, -1, 1.5, "秘密"]},
        }
    }
    source_before = deepcopy(source)

    candidate = _candidate(source)

    assert candidate["extensions"] == source["extensions"]  # nosec B101
    assert candidate["extensions"] is not source["extensions"]  # nosec B101
    candidate_legacy = cast(
        dict[str, object],
        candidate["extensions"][v1.LEGACY_EXTENSION_NAMESPACE],
    )
    source_legacy = cast(
        dict[str, object],
        source["extensions"][v1.LEGACY_EXTENSION_NAMESPACE],
    )
    assert candidate_legacy is not source_legacy  # nosec B101
    assert candidate_legacy["nested"] is not source_legacy["nested"]  # nosec B101

    source_workspace = cast(list[dict[str, object]], source["workspaces"])[0]
    assert (
        candidate["workspaces"][0]["tab_order"]
        is not (  # nosec B101
            source_workspace["tab_order"]
        )
    )
    for tab in candidate["tabs"]:
        assert tab["display_order"] is not tab["kanban_order"]["in_use"]  # nosec B101

    extension_objects = [
        candidate["application"]["extensions"],
        candidate["workspaces"][0]["extensions"],
        *(tab["extensions"] for tab in candidate["tabs"]),
        *(resource["extensions"] for resource in candidate["resources"]),
        *(placement["extensions"] for placement in candidate["placements"]),
        *(binding["extensions"] for binding in candidate["device_bindings"]),
    ]
    assert all(value == {} for value in extension_objects)  # nosec B101
    assert len({id(value) for value in extension_objects}) == len(  # nosec B101
        extension_objects
    )

    source_nested = cast(dict[str, list[object]], source_legacy["nested"])
    source_nested["ordered"].append("source-only")
    assert "source-only" not in repr(candidate["extensions"])  # nosec B101
    candidate_legacy["candidate-only"] = True
    assert "candidate-only" not in repr(source["extensions"])  # nosec B101
    assert source_before != source  # nosec B101
    assert v2.validate_v2(candidate)  # nosec B101


@pytest.mark.parametrize(
    "mutation",
    (
        lambda source: source.update({"schema_version": 2}),
        lambda source: source.update({"unexpected": "private"}),
        lambda source: source.update({"extensions": {"unexpected": {"private": True}}}),
    ),
)
def test_invalid_strict_v1_source_returns_no_candidate(
    mutation: Callable[[v1.JsonObject], None],
) -> None:
    source = _one_tile_source()
    mutation(source)

    result = migration.migrate_v1_to_v2(source)

    assert result is None  # nosec B101


def test_checked_transform_expected_rejection_is_silent_and_private(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "private-invalid-source-4e19e9ac"
    source = _one_tile_source()
    source["unexpected"] = sentinel
    caplog.set_level(logging.DEBUG)

    result = checked.transform_v1_to_v2(source)

    captured = capsys.readouterr()
    assert result is None  # nosec B101
    assert captured.out == ""  # nosec B101
    assert captured.err == ""  # nosec B101
    assert sentinel not in caplog.text  # nosec B101
    assert sentinel not in repr(result)  # nosec B101


def test_downstream_target_validation_failure_remains_distinct() -> None:
    result = _execute_test_migration(
        _one_tile_source(),
        target_accepts=False,
    )

    assert isinstance(result, engine.PureExecutionRejected)  # nosec B101
    assert (  # nosec B101
        result.category
        is engine.PureExecutionRejectionCategory.TARGET_VALIDATION_FAILURE
    )
    assert result.stage is engine.PureExecutionStage.TARGET_VALIDATION  # nosec B101


def test_checked_transform_returns_content_free_target_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serialization, "validate_v2", lambda _candidate: False)

    result = checked.transform_v1_to_v2(_one_tile_source())

    assert isinstance(result, serialization.V2SerializationRejected)  # nosec B101
    assert (  # nosec B101
        result.category
        is engine.PureExecutionRejectionCategory.TARGET_VALIDATION_FAILURE
    )
    assert result.stage is engine.PureExecutionStage.TARGET_VALIDATION  # nosec B101
    assert "chat.openai.com" not in repr(result)  # nosec B101


def _source_with_serialized_size(size: int) -> v1.JsonObject:
    source = _one_tile_source()
    source["tiles"] = []
    source["extensions"] = {v1.LEGACY_EXTENSION_NAMESPACE: {"padding": ""}}
    baseline = _canonical_bytes(_candidate(source))
    delta = size - len(baseline)
    if delta < 2:
        raise AssertionError("target size must leave room for UTF-8 padding")
    padding = ("é" * (delta // 2)) + ("x" if delta % 2 else "")
    source["extensions"] = {v1.LEGACY_EXTENSION_NAMESPACE: {"padding": padding}}
    candidate = _candidate(source)
    if len(_canonical_bytes(candidate)) != size:
        raise AssertionError("candidate size construction is not exact")
    return source


def test_transformed_candidate_composes_with_inclusive_four_mib_ceiling() -> None:
    exact_source = _source_with_serialized_size(serialization.MAX_V2_CANDIDATE_BYTES)
    oversize_source = _source_with_serialized_size(
        serialization.MAX_V2_CANDIDATE_BYTES + 1
    )

    exact = checked.transform_v1_to_v2(exact_source)
    oversize = checked.transform_v1_to_v2(oversize_source)

    assert type(exact) is dict  # nosec B101
    exact_serialized = serialization.serialize_v2(exact)
    assert isinstance(  # nosec B101
        exact_serialized,
        serialization.SerializedV2Document,
    )
    assert (  # nosec B101
        exact_serialized.byte_count == serialization.MAX_V2_CANDIDATE_BYTES
    )
    assert isinstance(oversize, serialization.V2SerializationRejected)  # nosec B101
    assert (  # nosec B101
        oversize.category
        is engine.PureEngineFailureCategory.CANDIDATE_SIZE_LIMIT_EXCEEDED
    )
    assert oversize.stage is engine.PureExecutionStage.SERIALIZATION  # nosec B101
    rendered = f"{oversize!r} {oversize!s}"
    assert "padding" not in rendered  # nosec B101
    assert "é" not in rendered  # nosec B101
