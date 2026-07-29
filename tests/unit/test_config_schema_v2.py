# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

import config_schema_v2 as schema

pytestmark = pytest.mark.unit

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "schema_v2"

WORKSPACE_1 = "10000000-0000-4000-8000-000000000001"
WORKSPACE_2 = "10000000-0000-4000-8000-000000000002"
TAB_1 = "20000000-0000-4000-8000-000000000001"
TAB_2 = "20000000-0000-4000-8000-000000000002"
TAB_3 = "20000000-0000-4000-8000-000000000003"
TAB_4 = "20000000-0000-4000-8000-000000000004"
TAB_5 = "20000000-0000-4000-8000-000000000005"
RESOURCE_1 = "30000000-0000-4000-8000-000000000001"
PLACEMENT_1 = "40000000-0000-4000-8000-000000000001"
PLACEMENT_2 = "40000000-0000-4000-8000-000000000002"
PLACEMENT_3 = "40000000-0000-4000-8000-000000000003"
PLACEMENT_4 = "40000000-0000-4000-8000-000000000004"
PLACEMENT_5 = "40000000-0000-4000-8000-000000000005"
DANGLING_ID = "90000000-0000-4000-8000-000000000001"

_JsonPath = tuple[str | int, ...]
_JsonContainer = dict[str, object] | list[object]


def _load_fixture(name: str) -> schema.JsonObject:
    parsed = json.loads(
        (FIXTURE_ROOT / name).read_text(encoding="utf-8"),
        object_pairs_hook=schema.reject_duplicate_json_members,
    )
    if type(parsed) is not dict:
        raise AssertionError("fixture root must be an object")
    return cast(schema.JsonObject, parsed)


def _minimal() -> schema.JsonObject:
    return _load_fixture("minimal.json")


def _representative() -> schema.JsonObject:
    return _load_fixture("representative.json")


def _json_container_references(
    value: object,
    path: _JsonPath = (),
) -> dict[_JsonPath, _JsonContainer]:
    references: dict[_JsonPath, _JsonContainer] = {}
    if type(value) is dict:
        container = cast(dict[str, object], value)
        references[path] = container
        for key, child in container.items():
            references.update(_json_container_references(child, (*path, key)))
    elif type(value) is list:
        container = cast(list[object], value)
        references[path] = container
        for index, child in enumerate(container):
            references.update(_json_container_references(child, (*path, index)))
    return references


def _assert_valid_unchanged(candidate: object) -> None:
    before = deepcopy(candidate)
    containers_before = _json_container_references(candidate)
    result = schema.validate_v2(candidate)
    assert result is True  # nosec B101
    assert candidate == before  # nosec B101
    containers_after = _json_container_references(candidate)
    assert containers_after.keys() == containers_before.keys()  # nosec B101
    for path, container in containers_before.items():
        assert containers_after[path] is container  # nosec B101


def _assert_invalid_unchanged(candidate: object) -> None:
    before = deepcopy(candidate)
    containers_before = _json_container_references(candidate)
    result = schema.validate_v2(candidate)
    assert result is False  # nosec B101
    assert candidate == before  # nosec B101
    containers_after = _json_container_references(candidate)
    assert containers_after.keys() == containers_before.keys()  # nosec B101
    for path, container in containers_before.items():
        assert containers_after[path] is container  # nosec B101


def _objects(document: schema.JsonObject, field: str) -> list[dict[str, object]]:
    value = document[field]
    if type(value) is not list or not all(type(item) is dict for item in value):
        raise AssertionError(f"{field} must be an object array")
    return cast(list[dict[str, object]], value)


def _application(document: schema.JsonObject) -> dict[str, object]:
    value = document["application"]
    if type(value) is not dict:
        raise AssertionError("application must be an object")
    return cast(dict[str, object], value)


def _kanban(tab: dict[str, object]) -> dict[str, object]:
    value = tab["kanban_order"]
    if type(value) is not dict:
        raise AssertionError("kanban_order must be an object")
    return cast(dict[str, object], value)


def _container(document: schema.JsonObject, location: str) -> dict[str, object]:
    workspaces = _objects(document, "workspaces")
    tabs = _objects(document, "tabs")
    resources = _objects(document, "resources")
    placements = _objects(document, "placements")
    bindings = _objects(document, "device_bindings")
    containers: dict[str, dict[str, object]] = {
        "root": cast(dict[str, object], document),
        "application": _application(document),
        "workspace": workspaces[0],
        "tab": tabs[0],
        "kanban": _kanban(tabs[0]),
        "resource": resources[0],
        "target": cast(dict[str, object], resources[0]["target"]),
        "resource_icon": cast(dict[str, object], resources[2]["default_icon"]),
        "placement": placements[0],
        "placement_icon": cast(dict[str, object], placements[1]["icon_override"]),
        "window_binding": bindings[0],
        "portable_applicability": cast(dict[str, object], bindings[0]["applicability"]),
        "window_settings": cast(dict[str, object], bindings[0]["settings"]),
        "launch_binding": bindings[3],
        "device_applicability": cast(dict[str, object], bindings[4]["applicability"]),
        "launch_settings": cast(dict[str, object], bindings[3]["settings"]),
    }
    return containers[location]


def _semantic_orders(
    document: schema.JsonObject,
) -> tuple[
    dict[str, tuple[object, ...]],
    dict[
        str,
        tuple[
            tuple[object, ...],
            tuple[object, ...],
            tuple[object, ...],
            tuple[object, ...],
        ],
    ],
]:
    workspace_orders = {
        cast(str, workspace["id"]): tuple(cast(list[object], workspace["tab_order"]))
        for workspace in _objects(document, "workspaces")
    }
    tab_orders = {
        cast(str, tab["id"]): (
            tuple(cast(list[object], tab["display_order"])),
            tuple(cast(list[object], _kanban(tab)["new"])),
            tuple(cast(list[object], _kanban(tab)["in_use"])),
            tuple(cast(list[object], _kanban(tab)["archived"])),
        )
        for tab in _objects(document, "tabs")
    }
    return workspace_orders, tab_orders


def test_valid_fixtures_cover_complete_graph_without_mutation() -> None:
    for name in ("minimal.json", "representative.json"):
        document = _load_fixture(name)
        _assert_valid_unchanged(document)

    representative = _representative()
    workspaces = _objects(representative, "workspaces")
    tabs = _objects(representative, "tabs")
    resources = _objects(representative, "resources")
    placements = _objects(representative, "placements")
    assert workspaces[2]["tab_order"] == []  # nosec B101
    assert {(tab["visibility"], tab["lifecycle"]) for tab in tabs[:4]} == {  # nosec B101
        ("visible", "active"),
        ("visible", "archived"),
        ("hidden", "active"),
        ("hidden", "archived"),
    }
    assert resources[2]["id"] not in {  # nosec B101
        placement["resource_id"] for placement in placements
    }
    first = {key: value for key, value in resources[0].items() if key != "id"}
    second = {key: value for key, value in resources[1].items() if key != "id"}
    assert first == second  # nosec B101


def test_exported_typed_dicts_are_total_and_have_exact_keys() -> None:
    expected = {
        schema.Root: {
            "schema_version",
            "application",
            "workspaces",
            "tabs",
            "resources",
            "placements",
            "device_bindings",
            "extensions",
        },
        schema.Application: {"title", "default_workspace_id", "extensions"},
        schema.Workspace: {"id", "name", "tab_order", "extensions"},
        schema.Tab: {
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
        },
        schema.KanbanOrder: {"new", "in_use", "archived"},
        schema.UrlTarget: {"url"},
        schema.LegacyStringIcon: {"kind", "value"},
        schema.Resource: {
            "id",
            "kind",
            "target",
            "default_label",
            "default_icon",
            "extensions",
        },
        schema.Placement: {
            "id",
            "resource_id",
            "tab_id",
            "label_override",
            "icon_override",
            "background_color",
            "workflow_status",
            "extensions",
        },
        schema.PortableFallback: {"kind"},
        schema.DeviceSpecific: {"kind", "device_key"},
        schema.WindowSettings: {
            "columns",
            "auto_fit",
            "window_x",
            "window_y",
            "window_w",
            "window_h",
        },
        schema.UrlLaunchSettings: {"browser", "chrome_profile", "open_target"},
        schema.WorkspaceWindowBinding: {
            "id",
            "subject_kind",
            "subject_id",
            "binding_kind",
            "applicability",
            "settings",
            "extensions",
        },
        schema.PlacementLaunchBinding: {
            "id",
            "subject_kind",
            "subject_id",
            "binding_kind",
            "applicability",
            "settings",
            "extensions",
        },
    }

    for typed_dict, keys in expected.items():
        assert typed_dict.__required_keys__ == frozenset(keys)  # nosec B101
        assert typed_dict.__optional_keys__ == frozenset()  # nosec B101


@pytest.mark.parametrize(
    ("location", "field"),
    [
        ("root", "application"),
        ("application", "title"),
        ("workspace", "name"),
        ("tab", "lifecycle"),
        ("kanban", "new"),
        ("resource", "default_label"),
        ("target", "url"),
        ("resource_icon", "value"),
        ("placement", "background_color"),
        ("placement_icon", "value"),
        ("window_binding", "subject_kind"),
        ("portable_applicability", "kind"),
        ("window_settings", "columns"),
        ("launch_binding", "binding_kind"),
        ("device_applicability", "device_key"),
        ("launch_settings", "browser"),
    ],
)
def test_every_structural_shape_rejects_missing_and_extra_direct_keys(
    location: str,
    field: str,
) -> None:
    missing = _representative()
    del _container(missing, location)[field]
    _assert_invalid_unchanged(missing)

    extra = _representative()
    _container(extra, location)["unexpected"] = "not an extension"
    _assert_invalid_unchanged(extra)


@pytest.mark.parametrize(
    "bad_value",
    [
        b"bytes",
        ("tuple",),
        {"set"},
        float("nan"),
        float("inf"),
        float("-inf"),
        object(),
    ],
)
def test_strict_json_rejects_non_json_and_nonfinite_extension_values(
    bad_value: object,
) -> None:
    document = _minimal()
    document["extensions"] = cast(schema.Extensions, {"bad": bad_value})

    requires_focused_check = type(bad_value) is object or (
        type(bad_value) is float and bad_value != bad_value
    )
    if not requires_focused_check:
        _assert_invalid_unchanged(document)
        return

    extensions = cast(dict[str, object], document["extensions"])
    root_keys = tuple(document)
    extension_keys = tuple(extensions)
    result = schema.validate_v2(document)

    assert result is False  # nosec B101
    assert tuple(document) == root_keys  # nosec B101
    assert document["extensions"] is extensions  # nosec B101
    assert tuple(extensions) == extension_keys  # nosec B101
    assert extensions["bad"] is bad_value  # nosec B101


def test_strict_json_rejects_nonstring_keys_lone_surrogates_and_cycles() -> None:
    nonstring_key = _minimal()
    nonstring_key["extensions"] = cast(schema.Extensions, {1: "value"})
    _assert_invalid_unchanged(nonstring_key)

    for surrogate in ("\ud800", "\udfff"):
        bad_key = _minimal()
        bad_key["extensions"] = {surrogate: "value"}
        _assert_invalid_unchanged(bad_key)

        bad_value = _minimal()
        bad_value["extensions"] = {"key": surrogate}
        _assert_invalid_unchanged(bad_value)

    cyclic = _minimal()
    payload: dict[str, object] = {}
    payload["cycle"] = payload
    cyclic["extensions"] = cast(schema.Extensions, payload)
    acyclic_root = deepcopy(
        {key: value for key, value in cyclic.items() if key != "extensions"}
    )
    root_keys = tuple(cyclic)
    payload_keys = tuple(payload)
    assert cyclic["extensions"] is payload  # nosec B101
    assert payload["cycle"] is payload  # nosec B101

    result = schema.validate_v2(cyclic)

    assert result is False  # nosec B101
    assert tuple(cyclic) == root_keys  # nosec B101
    assert {  # nosec B101
        key: value for key, value in cyclic.items() if key != "extensions"
    } == acyclic_root
    assert cyclic["extensions"] is payload  # nosec B101
    assert tuple(payload) == payload_keys  # nosec B101
    assert payload["cycle"] is payload  # nosec B101


def test_extensions_are_opaque_finite_json_without_a_validation_depth_limit() -> None:
    document = _minimal()
    payload: dict[str, object] = {
        "schema_version": 999,
        "extensions": {"direct_field_name": None},
        "finite": 1.5,
    }
    cursor = payload
    for _ in range(2_000):
        nested: dict[str, object] = {}
        cursor["nested"] = nested
        cursor = nested
    document["extensions"] = cast(schema.Extensions, payload)

    root_keys = tuple(document)
    result = schema.validate_v2(document)

    assert result is True  # nosec B101
    assert tuple(document) == root_keys  # nosec B101
    assert document["extensions"] is payload  # nosec B101
    assert payload["schema_version"] == 999  # nosec B101
    assert payload["extensions"] == {  # nosec B101
        "direct_field_name": None
    }
    assert payload["finite"] == 1.5  # nosec B101
    cursor_after = payload
    for _ in range(2_000):
        nested_after = cursor_after.get("nested")
        assert type(nested_after) is dict  # nosec B101
        cursor_after = cast(dict[str, object], nested_after)
    assert cursor_after == {}  # nosec B101

    cannot_supply_field = _minimal()
    tab = _objects(cannot_supply_field, "tabs")[0]
    del tab["name"]
    cast(dict[str, object], tab["extensions"])["name"] = "Main"
    _assert_invalid_unchanged(cannot_supply_field)


def test_root_cardinality_exact_marker_and_exact_container_types() -> None:
    for marker in (True, False, 0, 1, 3, 2.0, "2", None):
        document = _minimal()
        document["schema_version"] = cast(schema.StrictJsonValue, marker)
        _assert_invalid_unchanged(document)

    for field in ("workspaces", "tabs"):
        document = _minimal()
        document[field] = []
        _assert_invalid_unchanged(document)

    for field in ("resources", "placements", "device_bindings"):
        document = _minimal()
        document[field] = []
        _assert_valid_unchanged(document)

    tuple_array = _minimal()
    tuple_array["resources"] = cast(schema.StrictJsonValue, ())
    _assert_invalid_unchanged(tuple_array)


def test_exact_string_boolean_integer_and_null_domains() -> None:
    representative = _representative()
    bindings = _objects(representative, "device_bindings")
    window = cast(dict[str, object], bindings[0]["settings"])
    window["columns"] = -(10**30)
    window["window_w"] = 0
    _assert_valid_unchanged(representative)

    for field in ("columns", "window_x", "window_y", "window_w", "window_h"):
        document = _representative()
        settings = cast(
            dict[str, object], _objects(document, "device_bindings")[0]["settings"]
        )
        settings[field] = True
        _assert_invalid_unchanged(document)

    wrong_auto_fit = _representative()
    settings = cast(
        dict[str, object],
        _objects(wrong_auto_fit, "device_bindings")[0]["settings"],
    )
    settings["auto_fit"] = 1
    _assert_invalid_unchanged(wrong_auto_fit)

    nullable_mismatches = (
        ("resource", "default_icon", False),
        ("placement", "label_override", False),
        ("placement", "icon_override", ""),
        ("launch_settings", "browser", False),
        ("launch_settings", "chrome_profile", 1),
        ("window_settings", "window_x", 1.5),
    )
    for location, field, value in nullable_mismatches:
        document = _representative()
        _container(document, location)[field] = value
        _assert_invalid_unchanged(document)


@pytest.mark.parametrize(
    "display_filter",
    [
        [],
        ["new"],
        ["in_use"],
        ["archived"],
        ["new", "in_use"],
        ["new", "archived"],
        ["in_use", "archived"],
        ["new", "in_use", "archived"],
    ],
)
def test_display_filter_accepts_exact_canonical_set_encodings(
    display_filter: list[str],
) -> None:
    document = _minimal()
    _objects(document, "tabs")[0]["display_filter"] = display_filter

    _assert_valid_unchanged(document)


@pytest.mark.parametrize(
    "display_filter",
    [
        ["new", "new"],
        ["in_use", "new"],
        ["archived", "new"],
        ["new", "archived", "in_use"],
        ["unknown"],
        [None],
    ],
)
def test_display_filter_rejects_duplicates_ordering_and_unknowns(
    display_filter: list[object],
) -> None:
    document = _minimal()
    _objects(document, "tabs")[0]["display_filter"] = display_filter

    _assert_invalid_unchanged(document)


@pytest.mark.parametrize(
    ("location", "field", "bad_value"),
    [
        ("tab", "visibility", "Visible"),
        ("tab", "lifecycle", "deleted"),
        ("tab", "view_mode", "grid"),
        ("resource", "kind", "image"),
        ("resource_icon", "kind", "path"),
        ("placement", "workflow_status", "in-use"),
        ("placement_icon", "kind", "url"),
        ("portable_applicability", "kind", "not_applicable"),
        ("launch_settings", "open_target", "default"),
    ],
)
def test_exact_literals_reject_unknown_or_wrong_case_values(
    location: str,
    field: str,
    bad_value: str,
) -> None:
    document = _representative()
    _container(document, location)[field] = bad_value

    _assert_invalid_unchanged(document)


@pytest.mark.parametrize(
    "bad_id",
    [
        "97587F7C-CA79-5CB7-AD4D-C9E4CD08683D",
        WORKSPACE_1.replace("-", ""),
        f"{{{WORKSPACE_1}}}",
        "not-a-uuid",
        "",
        None,
        1,
    ],
)
def test_every_identity_position_requires_canonical_uuid_text(
    bad_id: object,
) -> None:
    candidates: list[schema.JsonObject] = []

    definition = _representative()
    _objects(definition, "resources")[0]["id"] = bad_id
    candidates.append(definition)

    default_reference = _representative()
    _application(default_reference)["default_workspace_id"] = bad_id
    candidates.append(default_reference)

    owner_reference = _representative()
    _objects(owner_reference, "tabs")[0]["workspace_id"] = bad_id
    candidates.append(owner_reference)

    resource_reference = _representative()
    _objects(resource_reference, "placements")[0]["resource_id"] = bad_id
    candidates.append(resource_reference)

    tab_reference = _representative()
    _objects(tab_reference, "placements")[0]["tab_id"] = bad_id
    candidates.append(tab_reference)

    tab_order = _representative()
    cast(list[object], _objects(tab_order, "workspaces")[0]["tab_order"])[0] = bad_id
    candidates.append(tab_order)

    display_order = _representative()
    cast(list[object], _objects(display_order, "tabs")[0]["display_order"])[0] = bad_id
    candidates.append(display_order)

    kanban_order = _representative()
    cast(list[object], _kanban(_objects(kanban_order, "tabs")[0])["new"])[0] = bad_id
    candidates.append(kanban_order)

    binding_subject = _representative()
    _objects(binding_subject, "device_bindings")[0]["subject_id"] = bad_id
    candidates.append(binding_subject)

    for candidate in candidates:
        _assert_invalid_unchanged(candidate)


@pytest.mark.parametrize(
    "canonical_id",
    [
        "00000000-0000-0000-0000-000000000000",
        "97587f7c-ca79-5cb7-ad4d-c9e4cd08683d",
    ],
)
def test_canonical_uuid_versions_are_not_artificially_restricted(
    canonical_id: str,
) -> None:
    document = _minimal()
    workspace = _objects(document, "workspaces")[0]
    old_id = cast(str, workspace["id"])
    workspace["id"] = canonical_id
    _application(document)["default_workspace_id"] = canonical_id
    for tab in _objects(document, "tabs"):
        if tab["workspace_id"] == old_id:
            tab["workspace_id"] = canonical_id

    _assert_valid_unchanged(document)


def test_entity_ids_are_globally_unique_across_all_definition_families() -> None:
    within_family = _representative()
    resources = _objects(within_family, "resources")
    resources[1]["id"] = resources[0]["id"]
    _assert_invalid_unchanged(within_family)

    cross_family = _representative()
    _objects(cross_family, "device_bindings")[0]["id"] = PLACEMENT_1
    _assert_invalid_unchanged(cross_family)

    uuid_looking_device_key = _representative()
    applicability = cast(
        dict[str, object],
        _objects(uuid_looking_device_key, "device_bindings")[1]["applicability"],
    )
    applicability["device_key"] = WORKSPACE_1
    _assert_valid_unchanged(uuid_looking_device_key)


def test_names_use_exact_decoded_string_equality_only() -> None:
    duplicate_workspace = _representative()
    workspaces = _objects(duplicate_workspace, "workspaces")
    workspaces[1]["name"] = workspaces[0]["name"]
    _assert_invalid_unchanged(duplicate_workspace)

    case_variant_workspace = _representative()
    workspaces = _objects(case_variant_workspace, "workspaces")
    workspaces[1]["name"] = cast(str, workspaces[0]["name"]).lower()
    workspaces[2]["name"] = "   "
    _assert_valid_unchanged(case_variant_workspace)

    duplicate_tab = _representative()
    tabs = _objects(duplicate_tab, "tabs")
    tabs[1]["name"] = tabs[0]["name"]
    _assert_invalid_unchanged(duplicate_tab)

    case_variant_tab = _representative()
    tabs = _objects(case_variant_tab, "tabs")
    tabs[1]["name"] = cast(str, tabs[0]["name"]).lower()
    tabs[2]["name"] = "   "
    _assert_valid_unchanged(case_variant_tab)

    normalization_variants = _representative()
    tabs = _objects(normalization_variants, "tabs")
    tabs[0]["name"] = unicodedata.normalize("NFC", "Café")
    tabs[1]["name"] = unicodedata.normalize("NFD", "Café")
    assert tabs[0]["name"] != tabs[1]["name"]  # nosec B101
    _assert_valid_unchanged(normalization_variants)

    for location in ("workspace", "tab"):
        empty = _representative()
        _container(empty, location)["name"] = ""
        _assert_invalid_unchanged(empty)


def test_typed_references_and_complete_workspace_and_display_orders() -> None:
    wrong_default_type = _representative()
    _application(wrong_default_type)["default_workspace_id"] = RESOURCE_1
    _assert_invalid_unchanged(wrong_default_type)

    wrong_tab_owner_type = _representative()
    _objects(wrong_tab_owner_type, "tabs")[0]["workspace_id"] = RESOURCE_1
    _assert_invalid_unchanged(wrong_tab_owner_type)

    wrong_resource_type = _representative()
    _objects(wrong_resource_type, "placements")[0]["resource_id"] = TAB_1
    _assert_invalid_unchanged(wrong_resource_type)

    wrong_placement_owner_type = _representative()
    _objects(wrong_placement_owner_type, "placements")[0]["tab_id"] = RESOURCE_1
    _assert_invalid_unchanged(wrong_placement_owner_type)

    wrong_binding_subject_type = _representative()
    _objects(wrong_binding_subject_type, "device_bindings")[0]["subject_id"] = (
        PLACEMENT_1
    )
    _assert_invalid_unchanged(wrong_binding_subject_type)

    workspace_orders = (
        [TAB_1, TAB_1, "20000000-0000-4000-8000-000000000003"],
        [TAB_1],
        [TAB_1, RESOURCE_1],
        [TAB_1, DANGLING_ID],
    )
    for order in workspace_orders:
        document = _representative()
        _objects(document, "workspaces")[0]["tab_order"] = order
        _assert_invalid_unchanged(document)

    display_orders = (
        [PLACEMENT_1, PLACEMENT_1, PLACEMENT_3],
        [PLACEMENT_1],
        [PLACEMENT_1, RESOURCE_1, PLACEMENT_3],
        [PLACEMENT_1, DANGLING_ID, PLACEMENT_3],
        [PLACEMENT_1, PLACEMENT_4, PLACEMENT_3],
    )
    for order in display_orders:
        document = _representative()
        _objects(document, "tabs")[0]["display_order"] = order
        _assert_invalid_unchanged(document)


def test_valid_nonlexical_workspace_tab_order_is_not_sorted_or_replaced() -> None:
    document = _representative()
    workspace = _objects(document, "workspaces")[0]
    tab_order = cast(list[str], workspace["tab_order"])
    stored_tab_ids = [
        cast(str, tab["id"])
        for tab in _objects(document, "tabs")
        if tab["workspace_id"] == WORKSPACE_1
    ]
    expected_order = [TAB_3, TAB_1, TAB_4, TAB_2]
    tab_order[:] = expected_order

    assert sorted(tab_order) == sorted(stored_tab_ids)  # nosec B101
    assert tab_order != stored_tab_ids  # nosec B101
    assert tab_order != sorted(tab_order)  # nosec B101

    _assert_valid_unchanged(document)

    tab_order_after = _objects(document, "workspaces")[0]["tab_order"]
    assert tab_order_after is tab_order  # nosec B101
    assert tab_order_after == expected_order  # nosec B101


def test_default_workspace_requires_active_visible_tab_only_in_default() -> None:
    document = _representative()
    default_tab = _objects(document, "tabs")[0]
    default_tab["visibility"] = "hidden"

    _assert_invalid_unchanged(document)

    nondefault_empty_and_without_active_visible = _representative()
    _assert_valid_unchanged(nondefault_empty_and_without_active_visible)


@pytest.mark.parametrize(
    ("visibility", "lifecycle"),
    [
        ("hidden", "active"),
        ("visible", "archived"),
        ("hidden", "archived"),
    ],
)
def test_populated_nondefault_workspace_needs_no_active_visible_tab(
    visibility: str,
    lifecycle: str,
) -> None:
    document = _representative()
    application = _application(document)
    workspaces = _objects(document, "workspaces")
    tabs = _objects(document, "tabs")
    placements = _objects(document, "placements")
    secondary = next(
        workspace for workspace in workspaces if workspace["id"] == WORKSPACE_2
    )
    secondary_tabs = [tab for tab in tabs if tab["workspace_id"] == WORKSPACE_2]
    assert application["default_workspace_id"] == WORKSPACE_1  # nosec B101
    assert secondary["tab_order"] == [TAB_5]  # nosec B101
    assert [tab["id"] for tab in secondary_tabs] == [TAB_5]  # nosec B101
    assert any(placement["tab_id"] == TAB_5 for placement in placements)  # nosec B101
    secondary_tabs[0]["visibility"] = visibility
    secondary_tabs[0]["lifecycle"] = lifecycle
    assert not any(  # nosec B101
        tab["visibility"] == "visible" and tab["lifecycle"] == "active"
        for tab in secondary_tabs
    )
    assert any(  # nosec B101
        tab["workspace_id"] == WORKSPACE_1
        and tab["visibility"] == "visible"
        and tab["lifecycle"] == "active"
        for tab in tabs
    )

    _assert_valid_unchanged(document)


def test_empty_display_filter_is_valid_for_a_tab_that_owns_placements() -> None:
    document = _representative()
    tab = _objects(document, "tabs")[0]
    owned_ids = [
        placement["id"]
        for placement in _objects(document, "placements")
        if placement["tab_id"] == TAB_1
    ]
    assert owned_ids == [PLACEMENT_1, PLACEMENT_2, PLACEMENT_3]  # nosec B101
    tab["display_filter"] = []
    assert tab["display_order"] == [  # nosec B101
        PLACEMENT_2,
        PLACEMENT_1,
        PLACEMENT_3,
    ]

    _assert_valid_unchanged(document)


def test_equal_content_placements_keep_distinct_display_and_kanban_identities() -> None:
    document = _representative()
    tab = _objects(document, "tabs")[0]
    placements = _objects(document, "placements")
    equal_content = deepcopy(placements[0])
    equal_content["id"] = PLACEMENT_2
    placements[1] = equal_content
    kanban = _kanban(tab)
    kanban["new"] = [PLACEMENT_2, PLACEMENT_1]
    kanban["in_use"] = []

    first_without_id = {
        key: value for key, value in placements[0].items() if key != "id"
    }
    second_without_id = {
        key: value for key, value in placements[1].items() if key != "id"
    }
    assert placements[0]["id"] != placements[1]["id"]  # nosec B101
    assert first_without_id == second_without_id  # nosec B101
    display_order = cast(list[object], tab["display_order"])
    kanban_order = [
        item
        for status in ("new", "in_use", "archived")
        for item in cast(list[object], kanban[status])
    ]
    for placement_id in (PLACEMENT_1, PLACEMENT_2):
        assert display_order.count(placement_id) == 1  # nosec B101
        assert kanban_order.count(placement_id) == 1  # nosec B101

    _assert_valid_unchanged(document)


@pytest.mark.parametrize("field", ["workspaces", "tabs", "resources", "placements"])
def test_definition_array_permutations_do_not_change_semantic_orders(
    field: str,
) -> None:
    document = _representative()
    definition_fields = ("workspaces", "tabs", "resources", "placements")
    arrays_before = {name: deepcopy(document[name]) for name in definition_fields}
    semantic_orders_before = _semantic_orders(document)
    selected = _objects(document, field)
    document[field] = cast(
        list[schema.StrictJsonValue],
        list(reversed(selected)),
    )

    assert document[field] != arrays_before[field]  # nosec B101
    for other_field in definition_fields:
        if other_field != field:
            assert document[other_field] == arrays_before[other_field]  # nosec B101
    assert _semantic_orders(document) == semantic_orders_before  # nosec B101

    _assert_valid_unchanged(document)


def test_resources_may_be_orphaned_shared_and_equal_without_deduplication() -> None:
    document = _representative()
    resources = _objects(document, "resources")
    placements = _objects(document, "placements")

    assert resources[0]["id"] != resources[1]["id"]  # nosec B101
    assert [placement["resource_id"] for placement in placements].count(RESOURCE_1) == 3  # nosec B101
    _assert_valid_unchanged(document)

    opaque = _representative()
    resource = _objects(opaque, "resources")[0]
    placement = _objects(opaque, "placements")[0]
    cast(dict[str, object], resource["target"])["url"] = (
        "  https://opaque.example.test/Path  "
    )
    resource["default_label"] = ""
    placement["background_color"] = "definitely not CSS"
    _assert_valid_unchanged(opaque)


@pytest.mark.parametrize(
    ("queue", "replacement"),
    [
        ("duplicate", [PLACEMENT_1, PLACEMENT_1]),
        ("cross_queue", [PLACEMENT_1]),
        ("omitted", []),
        ("cross_tab", [PLACEMENT_4]),
        ("wrong_entity", [RESOURCE_1]),
        ("dangling", [DANGLING_ID]),
    ],
)
def test_kanban_rejects_duplicate_omitted_foreign_and_wrong_type_members(
    queue: str,
    replacement: list[str],
) -> None:
    document = _representative()
    kanban = _kanban(_objects(document, "tabs")[0])
    if queue == "cross_queue":
        kanban["in_use"] = replacement
    else:
        kanban["new"] = replacement

    _assert_invalid_unchanged(document)


def test_kanban_status_must_match_and_orders_remain_independent() -> None:
    wrong_status = _representative()
    kanban = _kanban(_objects(wrong_status, "tabs")[0])
    kanban["new"] = []
    kanban["archived"] = [PLACEMENT_1, PLACEMENT_3]
    _assert_invalid_unchanged(wrong_status)

    independent = _representative()
    tabs = _objects(independent, "tabs")
    placements = _objects(independent, "placements")
    placements[2]["workflow_status"] = "in_use"
    _kanban(tabs[0])["in_use"] = [PLACEMENT_3, PLACEMENT_2]
    _kanban(tabs[0])["archived"] = []
    assert tabs[0]["display_order"] == [  # nosec B101
        PLACEMENT_2,
        PLACEMENT_1,
        PLACEMENT_3,
    ]
    _assert_valid_unchanged(independent)

    archived_tab_new_placement = _representative()
    assert _objects(archived_tab_new_placement, "tabs")[1]["lifecycle"] == "archived"  # nosec B101
    assert (
        _objects(archived_tab_new_placement, "placements")[3][  # nosec B101
            "workflow_status"
        ]
        == "new"
    )
    _assert_valid_unchanged(archived_tab_new_placement)


def test_device_binding_variants_require_exact_pairings_and_subject_types() -> None:
    invalid_mutations = (
        ("window_binding", "subject_kind", "placement"),
        ("window_binding", "binding_kind", "launch"),
        ("launch_binding", "subject_kind", "workspace"),
        ("launch_binding", "binding_kind", "window"),
    )
    for location, field, value in invalid_mutations:
        document = _representative()
        _container(document, location)[field] = value
        _assert_invalid_unchanged(document)

    workspace_with_launch_settings = _representative()
    bindings = _objects(workspace_with_launch_settings, "device_bindings")
    bindings[0]["settings"] = deepcopy(bindings[3]["settings"])
    _assert_invalid_unchanged(workspace_with_launch_settings)

    placement_with_window_settings = _representative()
    bindings = _objects(placement_with_window_settings, "device_bindings")
    bindings[3]["settings"] = deepcopy(bindings[0]["settings"])
    _assert_invalid_unchanged(placement_with_window_settings)


def test_device_applicability_and_setting_domains_are_exact_and_platform_free() -> None:
    whitespace_key = _representative()
    _assert_valid_unchanged(whitespace_key)

    empty_key = _representative()
    applicability = cast(
        dict[str, object],
        _objects(empty_key, "device_bindings")[1]["applicability"],
    )
    applicability["device_key"] = ""
    _assert_invalid_unchanged(empty_key)

    portable_with_key = _representative()
    applicability = cast(
        dict[str, object],
        _objects(portable_with_key, "device_bindings")[0]["applicability"],
    )
    applicability["device_key"] = "device-A"
    _assert_invalid_unchanged(portable_with_key)

    unusual_launch_values = _representative()
    settings = cast(
        dict[str, object],
        _objects(unusual_launch_values, "device_bindings")[3]["settings"],
    )
    settings["browser"] = "   "
    settings["chrome_profile"] = "profile that need not exist"
    _assert_valid_unchanged(unusual_launch_values)


def test_device_binding_selector_uniqueness_ignores_id_settings_and_extensions() -> (
    None
):
    duplicate_portable = _representative()
    bindings = _objects(duplicate_portable, "device_bindings")
    copied = deepcopy(bindings[0])
    copied["id"] = "50000000-0000-4000-8000-000000000010"
    copied["settings"] = {
        "columns": 99,
        "auto_fit": False,
        "window_x": None,
        "window_y": None,
        "window_w": None,
        "window_h": None,
    }
    copied["extensions"] = {"different": True}
    bindings.append(copied)
    _assert_invalid_unchanged(duplicate_portable)

    duplicate_device = _representative()
    bindings = _objects(duplicate_device, "device_bindings")
    copied = deepcopy(bindings[1])
    copied["id"] = "50000000-0000-4000-8000-000000000011"
    cast(dict[str, object], copied["settings"])["columns"] = 123
    bindings.append(copied)
    _assert_invalid_unchanged(duplicate_device)

    case_distinct_key = _representative()
    bindings = _objects(case_distinct_key, "device_bindings")
    copied = deepcopy(bindings[1])
    copied["id"] = "50000000-0000-4000-8000-000000000012"
    cast(dict[str, object], copied["applicability"])["device_key"] = "DEVICE-A"
    bindings.append(copied)
    _assert_valid_unchanged(case_distinct_key)

    reversed_root_order = _representative()
    bindings = _objects(reversed_root_order, "device_bindings")
    reversed_root_order["device_bindings"] = cast(
        list[schema.StrictJsonValue],
        list(reversed(bindings)),
    )
    _assert_valid_unchanged(reversed_root_order)
