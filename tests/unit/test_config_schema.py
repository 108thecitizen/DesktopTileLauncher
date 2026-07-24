# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import ast
import inspect
import json
from collections.abc import Callable
from copy import deepcopy
from typing import cast
from uuid import UUID

import pytest

import config_schema as schema

pytestmark = pytest.mark.unit

WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
MAIN_ID = "22222222-2222-4222-8222-222222222222"
WORK_ID = "33333333-3333-4333-8333-333333333333"
OTHER_ID = "44444444-4444-4444-8444-444444444444"
DANGLING_ID = "55555555-5555-4555-8555-555555555555"
V5_WORKSPACE_ID = "8b66f209-e64d-50d9-b534-144cac41f7bf"
V5_TAB_ID = "97587f7c-ca79-5cb7-ad4d-c9e4cd08683d"
NONCANONICAL_IDS: tuple[object, ...] = (
    V5_TAB_ID.upper(),
    MAIN_ID.replace("-", ""),
    "not-a-uuid",
    "",
    None,
)


def _valid_v1() -> schema.JsonObject:
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
                "tab_order": [MAIN_ID, WORK_ID],
                "extensions": {},
            }
        ],
        "tabs": [
            {
                "id": MAIN_ID,
                "workspace_id": WORKSPACE_ID,
                "name": "Main",
                "visibility": "visible",
                "extensions": {},
            },
            {
                "id": WORK_ID,
                "workspace_id": WORKSPACE_ID,
                "name": "Work",
                "visibility": "hidden",
                "extensions": {},
            },
        ],
        "tiles": [
            {
                "name": "ChatGPT",
                "url": "https://chat.openai.com",
                "tab_id": MAIN_ID,
                "icon": None,
                "bg": "#F5F6FA",
                "browser": None,
                "chrome_profile": None,
                "open_target": "tab",
            }
        ],
        "columns": 5,
        "auto_fit": True,
        "window_x": None,
        "window_y": -20,
        "window_w": 0,
        "window_h": 900,
        "extensions": {},
    }


def _application(document: schema.JsonObject) -> dict[str, schema.JsonValue]:
    value = document["application"]
    assert isinstance(value, dict)  # nosec B101
    return value


def _workspace(document: schema.JsonObject) -> dict[str, schema.JsonValue]:
    value = document["workspaces"]
    assert isinstance(value, list) and value  # nosec B101
    workspace = value[0]
    assert isinstance(workspace, dict)  # nosec B101
    return workspace


def _tabs(document: schema.JsonObject) -> list[dict[str, schema.JsonValue]]:
    value = document["tabs"]
    assert isinstance(value, list)  # nosec B101
    assert all(isinstance(tab, dict) for tab in value)  # nosec B101
    return cast(list[dict[str, schema.JsonValue]], value)


def _tiles(document: schema.JsonObject) -> list[dict[str, schema.JsonValue]]:
    value = document["tiles"]
    assert isinstance(value, list)  # nosec B101
    assert all(isinstance(tile, dict) for tile in value)  # nosec B101
    return cast(list[dict[str, schema.JsonValue]], value)


def _replace_workspace_identity(
    document: schema.JsonObject,
    replacement: schema.JsonValue,
) -> None:
    workspace = _workspace(document)
    previous_id = workspace["id"]
    _application(document)["default_workspace_id"] = replacement
    workspace["id"] = replacement
    for tab in _tabs(document):
        assert tab["workspace_id"] == previous_id  # nosec B101
        tab["workspace_id"] = replacement


def _replace_tab_identity(
    document: schema.JsonObject,
    tab_index: int,
    replacement: schema.JsonValue,
) -> None:
    tab = _tabs(document)[tab_index]
    previous_id = tab["id"]
    tab["id"] = replacement

    workspace = _workspace(document)
    raw_order = workspace["tab_order"]
    assert isinstance(raw_order, list)  # nosec B101
    workspace["tab_order"] = [
        replacement if tab_id == previous_id else tab_id for tab_id in raw_order
    ]
    for tile in _tiles(document):
        if tile["tab_id"] == previous_id:
            tile["tab_id"] = replacement


def test_validate_v1_accepts_exact_shape_and_name_independence() -> None:
    document = _valid_v1()
    _application(document)["title"] = ""
    _workspace(document)["name"] = "工作区"
    _tabs(document)[0]["name"] = "主要"
    document["extensions"] = {
        schema.LEGACY_EXTENSION_NAMESPACE: {
            "arbitrary": [None, True, -1, 1.5, "秘密", {"nested": "kept"}]
        }
    }

    assert schema.validate_v1(document)  # nosec B101


def test_validate_v1_accepts_utf8_unicode_and_preserves_it_unchanged() -> None:
    emoji = cast(str, json.loads(r'"\ud83d\ude00"'))
    document = _valid_v1()
    _application(document)["title"] = f"Café 東京 {emoji}"
    _workspace(document)["name"] = "默认工作区"
    _tabs(document)[0]["name"] = "主要"
    tile = _tiles(document)[0]
    tile["name"] = "邮件"
    tile["url"] = "https://例子.test/路径"
    tile["icon"] = "图标.png"
    tile["bg"] = "蓝色"
    tile["browser"] = "浏览器"
    tile["chrome_profile"] = "个人资料"
    document["extensions"] = {
        schema.LEGACY_EXTENSION_NAMESPACE: {
            "扩展键": {"嵌套": ["秘密", emoji]},
        }
    }
    original = deepcopy(document)

    assert schema.validate_v1(document)  # nosec B101
    assert document == original  # nosec B101


@pytest.mark.parametrize(
    ("location", "field"),
    [
        ("application", "title"),
        ("workspace", "name"),
        ("tab", "name"),
        ("tile", "name"),
        ("tile", "url"),
        ("tile", "icon"),
        ("tile", "bg"),
        ("tile", "browser"),
        ("tile", "chrome_profile"),
    ],
)
@pytest.mark.parametrize("escaped_surrogate", [r'"\ud800"', r'"\udfff"'])
def test_validate_v1_rejects_lone_surrogates_in_schema_strings(
    location: str,
    field: str,
    escaped_surrogate: str,
) -> None:
    containers: dict[
        str,
        Callable[[schema.JsonObject], dict[str, schema.JsonValue]],
    ] = {
        "application": _application,
        "workspace": _workspace,
        "tab": lambda document: _tabs(document)[0],
        "tile": lambda document: _tiles(document)[0],
    }
    document = _valid_v1()
    surrogate = cast(str, json.loads(escaped_surrogate))
    containers[location](document)[field] = surrogate

    assert not schema.validate_v1(document)  # nosec B101


@pytest.mark.parametrize("escaped_surrogate", [r'"\ud800"', r'"\udfff"'])
def test_validate_v1_rejects_lone_surrogates_in_legacy_extension_tree(
    escaped_surrogate: str,
) -> None:
    surrogate = cast(str, json.loads(escaped_surrogate))
    payloads: tuple[schema.JsonObject, ...] = (
        {surrogate: "direct key"},
        {"nested": {surrogate: "nested key"}},
        {"nested": ["value", surrogate]},
    )

    for payload in payloads:
        document = _valid_v1()
        document["extensions"] = {schema.LEGACY_EXTENSION_NAMESPACE: payload}
        assert not schema.validate_v1(document)  # nosec B101


def test_validate_v1_rejects_overdeep_extension_without_raising() -> None:
    payload: schema.JsonObject = {}
    cursor = payload
    for _ in range(1_500):
        nested: schema.JsonObject = {}
        cursor["nested"] = nested
        cursor = nested
    document = _valid_v1()
    document["extensions"] = {schema.LEGACY_EXTENSION_NAMESPACE: payload}

    assert not schema.validate_v1(document)  # nosec B101


def test_validate_v1_rejects_missing_and_unknown_fields_at_every_level() -> None:
    locations = (
        (lambda doc: doc, "columns"),
        (_application, "title"),
        (_workspace, "name"),
        (lambda doc: _tabs(doc)[0], "visibility"),
        (lambda doc: _tiles(doc)[0], "chrome_profile"),
    )
    for locate, field in locations:
        missing = _valid_v1()
        del locate(missing)[field]
        assert not schema.validate_v1(missing)  # nosec B101

        unknown = _valid_v1()
        locate(unknown)["unexpected"] = "retained nowhere"
        assert not schema.validate_v1(unknown)  # nosec B101


@pytest.mark.parametrize("marker", [True, False, 0, 2, 1.0, "1", None])
def test_validate_v1_requires_integer_marker_one(marker: object) -> None:
    document = _valid_v1()
    document["schema_version"] = cast(schema.JsonValue, marker)

    assert not schema.validate_v1(document)  # nosec B101


def test_validate_v1_requires_exactly_one_resolving_workspace() -> None:
    missing = _valid_v1()
    missing["workspaces"] = []
    assert not schema.validate_v1(missing)  # nosec B101

    multiple = _valid_v1()
    workspaces = multiple["workspaces"]
    assert isinstance(workspaces, list)  # nosec B101
    workspaces.append(deepcopy(workspaces[0]))
    assert not schema.validate_v1(multiple)  # nosec B101

    unresolved_default = _valid_v1()
    _application(unresolved_default)["default_workspace_id"] = OTHER_ID
    assert not schema.validate_v1(unresolved_default)  # nosec B101

    empty_name = _valid_v1()
    _workspace(empty_name)["name"] = ""
    assert not schema.validate_v1(empty_name)  # nosec B101


@pytest.mark.parametrize("bad_id", NONCANONICAL_IDS)
def test_application_default_workspace_id_requires_canonical_text(
    bad_id: object,
) -> None:
    application = deepcopy(_application(_valid_v1()))
    application["default_workspace_id"] = cast(schema.JsonValue, bad_id)

    assert schema._valid_application(application) is None  # nosec B101


@pytest.mark.parametrize("bad_id", NONCANONICAL_IDS)
def test_workspace_id_requires_canonical_text(bad_id: object) -> None:
    workspace = deepcopy(_workspace(_valid_v1()))
    workspace["id"] = cast(schema.JsonValue, bad_id)

    assert schema._valid_workspace(workspace) is None  # nosec B101


@pytest.mark.parametrize("bad_id", NONCANONICAL_IDS)
def test_tab_id_requires_canonical_text(bad_id: object) -> None:
    tabs = deepcopy(_tabs(_valid_v1()))
    tabs[0]["id"] = cast(schema.JsonValue, bad_id)

    assert schema._valid_tabs(tabs, WORKSPACE_ID) is None  # nosec B101


@pytest.mark.parametrize("bad_id", NONCANONICAL_IDS)
def test_validate_v1_rejects_noncanonical_resolving_workspace_graph(
    bad_id: object,
) -> None:
    document = _valid_v1()
    replacement = cast(schema.JsonValue, bad_id)
    _replace_workspace_identity(document, replacement)

    assert _application(document)["default_workspace_id"] == replacement  # nosec B101
    assert _workspace(document)["id"] == replacement  # nosec B101
    assert all(  # nosec B101
        tab["workspace_id"] == replacement for tab in _tabs(document)
    )
    assert not schema.validate_v1(document)  # nosec B101


@pytest.mark.parametrize("bad_id", NONCANONICAL_IDS)
def test_validate_v1_rejects_noncanonical_tab_id_with_resolving_references(
    bad_id: object,
) -> None:
    document = _valid_v1()
    replacement = cast(schema.JsonValue, bad_id)
    _replace_tab_identity(document, 0, replacement)

    assert _workspace(document)["tab_order"] == [replacement, WORK_ID]  # nosec B101
    assert _tiles(document)[0]["tab_id"] == replacement  # nosec B101

    assert not schema.validate_v1(document)  # nosec B101


def test_validate_v1_rejects_global_identity_collision_and_invalid_ownership() -> None:
    workspace_collision = _valid_v1()
    _replace_tab_identity(workspace_collision, 0, WORKSPACE_ID)
    assert _workspace(workspace_collision)["tab_order"] == [  # nosec B101
        WORKSPACE_ID,
        WORK_ID,
    ]
    assert _tiles(workspace_collision)[0]["tab_id"] == WORKSPACE_ID  # nosec B101
    assert not schema.validate_v1(workspace_collision)  # nosec B101

    wrong_owner = _valid_v1()
    _tabs(wrong_owner)[0]["workspace_id"] = OTHER_ID
    assert not schema.validate_v1(wrong_owner)  # nosec B101


def test_validate_v1_requires_complete_unique_tab_order() -> None:
    for order in (
        [MAIN_ID],
        [MAIN_ID, MAIN_ID],
        [MAIN_ID, DANGLING_ID],
        [MAIN_ID, WORK_ID, WORK_ID],
    ):
        document = _valid_v1()
        _workspace(document)["tab_order"] = order
        assert not schema.validate_v1(document)  # nosec B101


def test_validate_v1_requires_nonempty_unique_tabs_and_one_visible() -> None:
    no_tabs = _valid_v1()
    no_tabs["tabs"] = []
    assert not schema.validate_v1(no_tabs)  # nosec B101

    duplicate_name = _valid_v1()
    _tabs(duplicate_name)[1]["name"] = "Main"
    assert not schema.validate_v1(duplicate_name)  # nosec B101

    empty_name = _valid_v1()
    _tabs(empty_name)[0]["name"] = ""
    assert not schema.validate_v1(empty_name)  # nosec B101

    all_hidden = _valid_v1()
    _tabs(all_hidden)[0]["visibility"] = "hidden"
    assert not schema.validate_v1(all_hidden)  # nosec B101

    invalid_visibility = _valid_v1()
    _tabs(invalid_visibility)[0]["visibility"] = "archived"
    assert not schema.validate_v1(invalid_visibility)  # nosec B101


def test_validate_v1_requires_exact_tile_fields_and_resolving_membership() -> None:
    legacy_membership = _valid_v1()
    tile = _tiles(legacy_membership)[0]
    del tile["tab_id"]
    tile["tab"] = "Main"
    assert not schema.validate_v1(legacy_membership)  # nosec B101

    unresolved = _valid_v1()
    _tiles(unresolved)[0]["tab_id"] = DANGLING_ID
    assert not schema.validate_v1(unresolved)  # nosec B101

    unhashable_membership = _valid_v1()
    _tiles(unhashable_membership)[0]["tab_id"] = [MAIN_ID]
    assert not schema.validate_v1(unhashable_membership)  # nosec B101

    omitted_nullable = _valid_v1()
    del _tiles(omitted_nullable)[0]["icon"]
    assert not schema.validate_v1(omitted_nullable)  # nosec B101

    wrong_nullable_type = _valid_v1()
    _tiles(wrong_nullable_type)[0]["browser"] = False
    assert not schema.validate_v1(wrong_nullable_type)  # nosec B101

    wrong_target = _valid_v1()
    _tiles(wrong_target)[0]["open_target"] = "default"
    assert not schema.validate_v1(wrong_target)  # nosec B101


def test_validate_v1_uses_current_integer_and_boolean_domains() -> None:
    for field in ("columns", "window_x", "window_y", "window_w", "window_h"):
        document = _valid_v1()
        document[field] = True
        assert not schema.validate_v1(document)  # nosec B101

    wrong_auto_fit = _valid_v1()
    wrong_auto_fit["auto_fit"] = 1
    assert not schema.validate_v1(wrong_auto_fit)  # nosec B101

    integers = _valid_v1()
    integers["columns"] = -999
    integers["window_x"] = 0
    assert schema.validate_v1(integers)  # nosec B101


def test_validate_v1_allows_only_the_fixed_finite_legacy_extension() -> None:
    wrong_namespace = _valid_v1()
    wrong_namespace["extensions"] = {"example.other": {}}
    assert not schema.validate_v1(wrong_namespace)  # nosec B101

    extra_namespace = _valid_v1()
    extra_namespace["extensions"] = {
        schema.LEGACY_EXTENSION_NAMESPACE: {},
        "example.other": {},
    }
    assert not schema.validate_v1(extra_namespace)  # nosec B101

    wrong_payload = _valid_v1()
    wrong_payload["extensions"] = {schema.LEGACY_EXTENSION_NAMESPACE: []}
    assert not schema.validate_v1(wrong_payload)  # nosec B101

    nonfinite = _valid_v1()
    nonfinite["extensions"] = {schema.LEGACY_EXTENSION_NAMESPACE: {"bad": float("nan")}}
    assert not schema.validate_v1(nonfinite)  # nosec B101

    nested_extension = _valid_v1()
    _application(nested_extension)["extensions"] = {"unexpected": True}
    assert not schema.validate_v1(nested_extension)  # nosec B101


def test_validate_v0_reuses_shallow_legacy_contract() -> None:
    accepted: schema.JsonObject = {
        "tabs": ["Main", False, None, ["filtered"]],
        "hidden_tabs": ["Main", 3],
        "tab_ids": ["tolerated malformed hint"],
        "tab_order": {"tolerated": "malformed hint"},
        "unknown": {"strict": [True, None]},
    }
    assert schema.validate_v0(accepted)  # nosec B101

    assert not schema.validate_v0({"columns": True})  # nosec B101
    assert not schema.validate_v0({"tiles": [{"name": "Missing URL"}]})  # nosec B101
    assert not schema.validate_v0({"schema_version": 1})  # nosec B101


@pytest.mark.parametrize("title", ["Custom", "", "Launcher", "起動器"])
def test_migration_keeps_application_title_independent_from_workspace(
    title: str,
) -> None:
    candidate = schema.migrate_v0_to_v1({"title": title})

    assert candidate is not None  # nosec B101
    assert _application(candidate)["title"] == title  # nosec B101
    assert _workspace(candidate)["name"] == "Default Workspace"  # nosec B101


def test_migration_materializes_all_legacy_and_tile_defaults() -> None:
    candidate = schema.migrate_v0_to_v1(
        {"tiles": [{"name": "Defaulted", "url": "example.test"}]}
    )

    assert candidate is not None  # nosec B101
    assert schema.validate_v1(candidate)  # nosec B101
    assert _application(candidate)["title"] == "Launcher"  # nosec B101
    assert candidate["columns"] == 5  # nosec B101
    assert candidate["auto_fit"] is True  # nosec B101
    assert all(  # nosec B101
        candidate[field] is None
        for field in ("window_x", "window_y", "window_w", "window_h")
    )
    tab = _tabs(candidate)[0]
    tile = _tiles(candidate)[0]
    assert tab["name"] == "Main"  # nosec B101
    assert tile == {  # nosec B101
        "name": "Defaulted",
        "url": "example.test",
        "tab_id": tab["id"],
        "icon": None,
        "bg": "#F5F6FA",
        "browser": None,
        "chrome_profile": None,
        "open_target": "tab",
    }


def test_migration_missing_tile_tab_appends_main_after_explicit_work() -> None:
    candidate = schema.migrate_v0_to_v1(
        {
            "tabs": ["Work"],
            "tiles": [{"name": "Implicit", "url": "example.test"}],
        }
    )

    assert candidate is not None  # nosec B101
    tabs = _tabs(candidate)
    assert [tab["name"] for tab in tabs] == ["Work", "Main"]  # nosec B101
    assert _workspace(candidate)["tab_order"] == [  # nosec B101
        tab["id"] for tab in tabs
    ]
    assert _tiles(candidate)[0]["tab_id"] == tabs[1]["id"]  # nosec B101
    assert _tiles(candidate)[0]["tab_id"] != tabs[0]["id"]  # nosec B101


def test_migration_preserves_tile_fields_order_membership_and_unknown_fields() -> None:
    source: schema.JsonObject = {
        "title": "Legacy",
        "columns": 8,
        "auto_fit": False,
        "window_x": -1,
        "window_y": 2,
        "window_w": 3,
        "window_h": 4,
        "tabs": ["Main", 7, "Work", "Main"],
        "hidden_tabs": ["Unknown", "Work", "Work"],
        "tiles": [
            {"name": "Implicit", "url": "first.example"},
            {
                "name": "Complete",
                "url": "second.example",
                "tab": "Work",
                "icon": "icon.png",
                "bg": "#123456",
                "browser": "chrome",
                "chrome_profile": "Profile 1",
                "open_target": "window",
            },
            {"name": "Tile only", "url": "third.example", "tab": "Personal"},
        ],
        "unrecognized": {"nested": ["preserved", 3.5]},
    }
    original = deepcopy(source)

    candidate = schema.migrate_v0_to_v1(source)

    assert candidate is not None  # nosec B101
    assert source == original  # nosec B101
    assert [tab["name"] for tab in _tabs(candidate)] == [  # nosec B101
        "Main",
        "Work",
        "Personal",
    ]
    tab_ids = {str(tab["name"]): tab["id"] for tab in _tabs(candidate)}
    tiles = _tiles(candidate)
    assert [tile["name"] for tile in tiles] == [  # nosec B101
        "Implicit",
        "Complete",
        "Tile only",
    ]
    assert [tile["tab_id"] for tile in tiles] == [  # nosec B101
        tab_ids["Main"],
        tab_ids["Work"],
        tab_ids["Personal"],
    ]
    assert tiles[1] == {  # nosec B101
        "name": "Complete",
        "url": "second.example",
        "tab_id": tab_ids["Work"],
        "icon": "icon.png",
        "bg": "#123456",
        "browser": "chrome",
        "chrome_profile": "Profile 1",
        "open_target": "window",
    }
    assert candidate["extensions"] == {  # nosec B101
        schema.LEGACY_EXTENSION_NAMESPACE: {
            "unrecognized": {"nested": ["preserved", 3.5]}
        }
    }


def test_migration_retains_canonicalized_ids_and_applies_saved_order() -> None:
    source: schema.JsonObject = {
        "tabs": ["Main", "Work", "Personal"],
        "tab_ids": {
            "Main": MAIN_ID.upper(),
            "Work": WORK_ID,
            "Personal": "malformed",
            "Removed": DANGLING_ID,
        },
        "tab_order": [
            None,
            WORK_ID,
            DANGLING_ID,
            MAIN_ID.upper(),
            WORK_ID,
            "malformed",
        ],
    }

    candidate = schema.migrate_v0_to_v1(source)

    assert candidate is not None  # nosec B101
    tabs = _tabs(candidate)
    assert [tab["name"] for tab in tabs] == ["Work", "Main", "Personal"]  # nosec B101
    assert tabs[0]["id"] == WORK_ID  # nosec B101
    assert tabs[1]["id"] == MAIN_ID  # nosec B101
    assert tabs[2]["id"] not in {MAIN_ID, WORK_ID, DANGLING_ID}  # nosec B101
    assert _workspace(candidate)["tab_order"] == [tab["id"] for tab in tabs]  # nosec B101


def test_migration_duplicate_id_is_retained_only_by_first_discovered_tab() -> None:
    candidate = schema.migrate_v0_to_v1(
        {
            "tabs": ["First", "Second"],
            "tab_ids": {"First": MAIN_ID, "Second": MAIN_ID},
            "tab_order": [MAIN_ID],
        }
    )

    assert candidate is not None  # nosec B101
    tabs = _tabs(candidate)
    assert [tab["name"] for tab in tabs] == ["First", "Second"]  # nosec B101
    assert tabs[0]["id"] == MAIN_ID  # nosec B101
    assert tabs[1]["id"] != MAIN_ID  # nosec B101


def test_malformed_identity_hints_are_tolerated_as_absent() -> None:
    malformed = schema.migrate_v0_to_v1(
        {"tabs": ["A", "B"], "tab_ids": [], "tab_order": {"bad": True}}
    )
    absent = schema.migrate_v0_to_v1({"tabs": ["A", "B"]})

    assert malformed is not None and absent is not None  # nosec B101
    assert [tab["name"] for tab in _tabs(malformed)] == ["A", "B"]  # nosec B101
    assert [tab["name"] for tab in _tabs(absent)] == ["A", "B"]  # nosec B101
    assert schema.validate_v1(malformed)  # nosec B101
    assert schema.validate_v1(absent)  # nosec B101


def test_migration_deduplicates_filters_and_appends_tile_only_tabs() -> None:
    candidate = schema.migrate_v0_to_v1(
        {
            "tabs": [False, "A", "A", None, "B"],
            "tiles": [
                {"name": "C tile", "url": "c.example", "tab": "C"},
                {"name": "A tile", "url": "a.example", "tab": "A"},
                {"name": "D tile", "url": "d.example", "tab": "D"},
            ],
        }
    )

    assert candidate is not None  # nosec B101
    assert [tab["name"] for tab in _tabs(candidate)] == ["A", "B", "C", "D"]  # nosec B101


def test_migration_large_ordered_dedup_characterization_is_stable() -> None:
    count = 512
    names = [f"Tab {index}" for index in range(count)]
    identifiers = {name: str(UUID(int=index + 1)) for index, name in enumerate(names)}
    expected_names = list(reversed(names))
    ordered_ids = [identifiers[name] for name in expected_names]
    source = cast(
        schema.JsonObject,
        {
            "tabs": names + names,
            "tab_ids": identifiers,
            "tab_order": ordered_ids + ordered_ids,
            "hidden_tabs": names + names,
        },
    )

    candidate = schema.migrate_v0_to_v1(source)

    assert candidate is not None  # nosec B101
    tabs = _tabs(candidate)
    assert [tab["name"] for tab in tabs] == expected_names  # nosec B101
    assert _workspace(candidate)["tab_order"] == ordered_ids  # nosec B101
    assert tabs[0]["visibility"] == "visible"  # nosec B101
    assert all(tab["visibility"] == "hidden" for tab in tabs[1:])  # nosec B101
    assert schema.validate_v1(candidate)  # nosec B101


def test_migration_all_hidden_makes_first_final_ordered_tab_visible() -> None:
    candidate = schema.migrate_v0_to_v1(
        {
            "tabs": ["A", "B"],
            "hidden_tabs": ["A", "B", "A", "Unknown"],
            "tab_ids": {"A": MAIN_ID, "B": WORK_ID},
            "tab_order": [WORK_ID, MAIN_ID],
        }
    )

    assert candidate is not None  # nosec B101
    tabs = _tabs(candidate)
    assert [(tab["name"], tab["visibility"]) for tab in tabs] == [  # nosec B101
        ("B", "visible"),
        ("A", "hidden"),
    ]


@pytest.mark.parametrize(
    "source",
    [
        {"tabs": [""]},
        {"tiles": [{"name": "Empty", "url": "x", "tab": ""}]},
    ],
)
def test_migration_rejects_empty_tab_names_without_repair(
    source: schema.JsonObject,
) -> None:
    original = deepcopy(source)

    assert schema.migrate_v0_to_v1(source) is None  # nosec B101
    assert source == original  # nosec B101


def test_migration_rejects_nonfinite_unknown_before_candidate_creation() -> None:
    source: schema.JsonObject = {"unknown": {"private": float("inf")}}

    assert schema.validate_v0(source)  # nosec B101
    assert schema.migrate_v0_to_v1(source) is None  # nosec B101


def test_exact_empty_v0_digest_uuidv5_vector_and_replay() -> None:
    expected_workspace = "8b66f209-e64d-50d9-b534-144cac41f7bf"
    expected_tab = "97587f7c-ca79-5cb7-ad4d-c9e4cd08683d"

    first = schema.migrate_v0_to_v1({})
    second = schema.migrate_v0_to_v1(json.loads("{}"))

    assert first == second  # nosec B101
    assert first is not None  # nosec B101
    assert _workspace(first)["id"] == expected_workspace  # nosec B101
    assert _tabs(first)[0]["id"] == expected_tab  # nosec B101
    serialized = json.dumps(
        first,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")
    assert serialized.endswith(b"}")  # nosec B101
    assert not serialized.endswith(b"\n")  # nosec B101


def test_canonical_source_changes_change_generated_identities() -> None:
    plain = schema.migrate_v0_to_v1({})
    with_unknown = schema.migrate_v0_to_v1({"unknown": "preserved"})

    assert plain is not None and with_unknown is not None  # nosec B101
    assert _workspace(plain)["id"] != _workspace(with_unknown)["id"]  # nosec B101
    assert _tabs(plain)[0]["id"] != _tabs(with_unknown)[0]["id"]  # nosec B101


def test_migration_rejects_reserved_workspace_collision_without_retry() -> None:
    calls: list[str] = []

    def collide(_namespace: UUID, name: str) -> UUID:
        calls.append(name)
        return UUID(V5_WORKSPACE_ID)

    result = schema.migrate_v0_to_v1(
        {"tab_ids": {"Removed": V5_WORKSPACE_ID}},
        uuid5_factory=collide,
    )

    assert result is None  # nosec B101
    assert len(calls) == 1  # nosec B101
    assert calls[0].endswith("/workspace/0")  # nosec B101


def test_migration_rejects_reserved_tab_collision_without_retry() -> None:
    calls: list[str] = []

    def collide_on_tab(_namespace: UUID, name: str) -> UUID:
        calls.append(name)
        return UUID(V5_WORKSPACE_ID if name.endswith("/workspace/0") else V5_TAB_ID)

    result = schema.migrate_v0_to_v1(
        {"tab_ids": {"Removed": V5_TAB_ID}},
        uuid5_factory=collide_on_tab,
    )

    assert result is None  # nosec B101
    assert len(calls) == 2  # nosec B101
    assert calls[1].endswith("/tab/0")  # nosec B101


def test_migration_rejects_collision_between_derived_ids() -> None:
    calls = 0

    def repeat(_namespace: UUID, _name: str) -> UUID:
        nonlocal calls
        calls += 1
        return UUID(V5_WORKSPACE_ID)

    result = schema.migrate_v0_to_v1({}, uuid5_factory=repeat)

    assert result is None  # nosec B101
    assert calls == 2  # nosec B101


def _allocator(values: list[str | UUID]) -> Callable[[], str | UUID]:
    remaining = iter(values)
    return lambda: next(remaining)


def test_build_native_v1_is_exact_friendly_configuration() -> None:
    document = schema.build_native_v1(_allocator([UUID(WORKSPACE_ID), UUID(MAIN_ID)]))

    assert schema.validate_v1(document)  # nosec B101
    assert _application(document) == {  # nosec B101
        "title": "My Launcher",
        "default_workspace_id": WORKSPACE_ID,
        "extensions": {},
    }
    assert _workspace(document) == {  # nosec B101
        "id": WORKSPACE_ID,
        "name": "Default Workspace",
        "tab_order": [MAIN_ID],
        "extensions": {},
    }
    assert _tabs(document) == [  # nosec B101
        {
            "id": MAIN_ID,
            "workspace_id": WORKSPACE_ID,
            "name": "Main",
            "visibility": "visible",
            "extensions": {},
        }
    ]
    assert [tile["name"] for tile in _tiles(document)] == [  # nosec B101
        "ChatGPT",
        "Gmail",
        "Notion",
    ]
    assert all(tile["tab_id"] == MAIN_ID for tile in _tiles(document))  # nosec B101
    assert document["columns"] == 5  # nosec B101
    assert document["auto_fit"] is True  # nosec B101
    assert document["extensions"] == {}  # nosec B101


def test_build_native_v1_retries_invalid_noncanonical_and_colliding_ids() -> None:
    document = schema.build_native_v1(
        _allocator(
            [
                "not-a-uuid",
                WORKSPACE_ID.upper(),
                WORKSPACE_ID,
                WORKSPACE_ID,
                UUID(MAIN_ID),
            ]
        )
    )

    assert _workspace(document)["id"] == WORKSPACE_ID  # nosec B101
    assert _tabs(document)[0]["id"] == MAIN_ID  # nosec B101


def test_build_native_v1_rejects_non_v4_and_exhausted_allocation() -> None:
    version_five = "8b66f209-e64d-50d9-b534-144cac41f7bf"

    with pytest.raises(schema.NativeV1ConstructionError) as exc_info:
        schema.build_native_v1(
            lambda: version_five,
        )

    assert str(exc_info.value) == "native_v1_identity_allocation_failed"  # nosec B101


def test_build_native_v1_sanitizes_allocator_failure() -> None:
    def fail() -> str:
        raise RuntimeError("private allocator details")

    with pytest.raises(schema.NativeV1ConstructionError) as exc_info:
        schema.build_native_v1(fail)

    assert "private" not in str(exc_info.value)  # nosec B101
    assert exc_info.value.__cause__ is None  # nosec B101


def test_pure_schema_layer_has_no_qt_or_ambient_identity_inputs() -> None:
    tree = ast.parse(inspect.getsource(schema))
    imports: set[tuple[str, str, str | None, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update((alias.name, "", alias.asname, 0) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.update(
                (node.module or "", alias.name, alias.asname, node.level)
                for alias in node.names
            )

    expected_imports: set[tuple[str, str, str | None, int]] = {
        ("__future__", "annotations", None, 0),
        ("hashlib", "", None, 0),
        ("json", "", None, 0),
        ("math", "", None, 0),
        ("collections.abc", "Callable", None, 0),
        ("collections.abc", "Mapping", None, 0),
        ("typing", "Final", None, 0),
        ("typing", "TypeAlias", None, 0),
        ("typing", "cast", None, 0),
        ("uuid", "NAMESPACE_URL", None, 0),
        ("uuid", "UUID", None, 0),
        ("uuid", "uuid5", None, 0),
        ("config_recovery", "LegacyConstructionFailure", None, 0),
        ("config_recovery", "validate_legacy_mapping", None, 0),
    }
    assert imports == expected_imports  # nosec B101

    locally_defined = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    allowed_name_calls = locally_defined | {
        "UUID",
        "all",
        "allocator",
        "any",
        "cast",
        "dict",
        "enumerate",
        "frozenset",
        "id",
        "isinstance",
        "len",
        "range",
        "set",
        "str",
        "super",
        "type",
        "uuid5_factory",
        "validate_legacy_mapping",
    }
    name_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert name_calls <= allowed_name_calls  # nosec B101

    allowed_attribute_calls = {
        "__init__",
        "add",
        "append",
        "dumps",
        "encode",
        "get",
        "hexdigest",
        "isfinite",
        "items",
        "loads",
        "lower",
        "remove",
        "sha256",
        "values",
    }
    attribute_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert attribute_calls <= allowed_attribute_calls  # nosec B101
