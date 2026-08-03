# SPDX-License-Identifier: Apache-2.0
"""Qt-free schema-v2 state bridge for the launcher's current flat-grid UI.

The complete validated graph remains authoritative.  This module projects the
legacy-compatible part used by today's UI and synchronizes a detached flat edit
back into that graph without rebuilding or discarding unrelated v2 state.
Graphs containing semantics the flat UI cannot faithfully edit are projected
read-only.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Literal, TypeAlias, cast
from uuid import UUID, uuid4

import config_migration_v2 as migration_v2
import config_runtime_v2 as runtime
import config_schema as v1
import config_schema_v2 as v2

_ID_ALLOCATION_ATTEMPTS = 32
_DEFAULT_COLUMNS = 5
_DEFAULT_AUTO_FIT = True

FlatStateFailureCategory: TypeAlias = Literal[
    "invalid_graph",
    "unsupported_graph",
    "invalid_state",
    "identity_allocation_failure",
]


@dataclass(frozen=True, slots=True)
class FlatStateRejected:
    """A graph or projected edit failed without exposing persisted content."""

    category: FlatStateFailureCategory


@dataclass(frozen=True, slots=True)
class FlatTileState:
    """One flat-grid Tile projected from, or destined for, a v2 Placement."""

    placement_id: str | None = field(repr=False)
    resource_id: str | None = field(repr=False)
    launch_binding_id: str | None = field(repr=False)
    name: str = field(repr=False)
    url: str = field(repr=False)
    tab_id: str = field(repr=False)
    icon: str | None = field(repr=False)
    background_color: str = field(repr=False)
    browser: str | None = field(repr=False)
    chrome_profile: str | None = field(repr=False)
    open_target: v2.OpenTarget = field(repr=False)
    duplicate_source_placement_id: str | None = field(
        default=None,
        repr=False,
    )


@dataclass(frozen=True, slots=True)
class FlatTabState:
    """One active Tab exposed by the current flat-grid UI."""

    id: str = field(repr=False)
    name: str = field(repr=False)
    hidden: bool


@dataclass(frozen=True, slots=True)
class FlatWorkspaceState:
    """The default Workspace projection used by today's launcher UI."""

    title: str = field(repr=False)
    workspace_id: str = field(repr=False)
    workspace_name: str = field(repr=False)
    tabs: tuple[FlatTabState, ...] = field(repr=False)
    tab_order: tuple[str, ...] = field(repr=False)
    tiles: tuple[FlatTileState, ...] = field(repr=False)
    columns: int
    auto_fit: bool
    window_x: int | None
    window_y: int | None
    window_w: int | None
    window_h: int | None
    editable: bool


@dataclass(frozen=True, slots=True)
class FlatTileIdentity:
    """Persisted identities assigned to one projected Tile after a save."""

    placement_id: str = field(repr=False)
    resource_id: str = field(repr=False)
    launch_binding_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class FlatWorkspaceUpdate:
    """A complete validated candidate plus identities aligned to input Tiles."""

    document: v2.Root = field(repr=False)
    tile_identities: tuple[FlatTileIdentity, ...] = field(repr=False)


FlatWorkspaceProjectionResult: TypeAlias = FlatWorkspaceState | FlatStateRejected
FlatWorkspaceUpdateResult: TypeAlias = FlatWorkspaceUpdate | FlatStateRejected
_JsonContainer: TypeAlias = list[v2.StrictJsonValue] | dict[str, v2.StrictJsonValue]


class NativeV2ConstructionError(ValueError):
    """Native schema-v2 construction failed with one fixed safe category."""

    def __init__(
        self, category: Literal["identity_allocation_failure", "validation_failure"]
    ):
        self.category = category
        super().__init__(category)


def _is_utf8_text(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _canonical_uuid(value: object) -> str | None:
    if type(value) is not str:
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    canonical = str(parsed)
    return canonical if value == canonical else None


def _allocator_uuid4(value: object) -> str | None:
    if isinstance(value, UUID):
        parsed = value
    elif type(value) is str:
        canonical = _canonical_uuid(value)
        if canonical is None:
            return None
        parsed = UUID(canonical)
    else:
        return None
    return str(parsed) if parsed.version == 4 else None


def _allocate_id(id_factory: v1.Uuid4Allocator, blocked: set[str]) -> str | None:
    for _ in range(_ID_ALLOCATION_ATTEMPTS):
        try:
            candidate = _allocator_uuid4(id_factory())
        except Exception:
            return None
        if candidate is not None and candidate not in blocked:
            blocked.add(candidate)
            return candidate
    return None


def _clone_strict_json(value: v2.StrictJsonValue) -> v2.StrictJsonValue:
    """Clone an exact strict-JSON subtree without using the Python call stack."""

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


def build_native_v2(id_factory: v1.Uuid4Allocator = uuid4) -> v2.Root:
    """Build one complete native v2 document without ever persisting v1."""

    try:
        source = v1.build_native_v1(id_factory)
    except v1.NativeV1ConstructionError:
        raise NativeV2ConstructionError("identity_allocation_failure") from None
    candidate = migration_v2.migrate_v1_to_v2(source)
    if candidate is None or not v2.validate_v2(candidate):
        raise NativeV2ConstructionError("validation_failure")
    return candidate


def reserved_entity_ids(document: object) -> frozenset[str]:
    """Return all entity IDs from a valid graph, or an empty set if invalid."""

    if not v2.validate_v2(document):
        return frozenset()
    root = cast(v2.Root, document)
    return frozenset(
        item["id"]
        for definitions in (
            root["workspaces"],
            root["tabs"],
            root["resources"],
            root["placements"],
            root["device_bindings"],
        )
        for item in definitions
    )


def _portable_bindings(
    root: v2.Root,
) -> tuple[
    dict[str, v2.WorkspaceWindowBinding],
    dict[str, v2.PlacementLaunchBinding],
]:
    windows: dict[str, v2.WorkspaceWindowBinding] = {}
    launches: dict[str, v2.PlacementLaunchBinding] = {}
    for binding in root["device_bindings"]:
        if binding["applicability"]["kind"] != "portable_fallback":
            continue
        if binding["subject_kind"] == "workspace":
            windows[binding["subject_id"]] = binding
        else:
            launches[binding["subject_id"]] = binding
    return windows, launches


def _editable_flat_graph(root: v2.Root, workspace_id: str) -> bool:
    tabs = [tab for tab in root["tabs"] if tab["workspace_id"] == workspace_id]
    active_tabs = [tab for tab in tabs if tab["lifecycle"] == "active"]
    active_tab_ids = {tab["id"] for tab in active_tabs}
    active_placements = [
        placement
        for placement in root["placements"]
        if placement["tab_id"] in active_tab_ids
    ]
    resource_uses = Counter(
        placement["resource_id"] for placement in root["placements"]
    )
    windows, launches = _portable_bindings(root)
    if workspace_id not in windows:
        return False
    if any(
        tab["view_mode"] != "display" or tab["display_filter"] != ["new", "in_use"]
        for tab in active_tabs
    ):
        return False
    if any(
        placement["workflow_status"] != "in_use"
        or placement["label_override"] is not None
        or placement["icon_override"] is not None
        or resource_uses[placement["resource_id"]] != 1
        or placement["id"] not in launches
        for placement in active_placements
    ):
        return False
    return all(
        tab["kanban_order"]["new"] == []
        and tab["kanban_order"]["archived"] == []
        and set(tab["kanban_order"]["in_use"]) == set(tab["display_order"])
        for tab in active_tabs
    )


def project_flat_workspace(document: object) -> FlatWorkspaceProjectionResult:
    """Project the default Workspace and mark unsupported rich graphs read-only."""

    projection = runtime.project_workspace(document)
    if isinstance(projection, runtime.RuntimeAdapterRejected):
        return FlatStateRejected("invalid_graph")
    root = cast(v2.Root, document)
    workspace_id = projection.id
    windows, launches = _portable_bindings(root)
    active_tabs = tuple(tab for tab in projection.tabs if tab.lifecycle == "active")
    projected_tiles = tuple(
        placement for tab in active_tabs for placement in tab.displayed_placements
    )
    tiles: list[FlatTileState] = []
    for placement in projected_tiles:
        settings = placement.launch_settings
        binding = launches.get(placement.id)
        tiles.append(
            FlatTileState(
                placement_id=placement.id,
                resource_id=placement.resource_id,
                launch_binding_id=None if binding is None else binding["id"],
                name=placement.label,
                url=placement.url,
                tab_id=placement.tab_id,
                icon=placement.icon,
                background_color=placement.background_color,
                browser=None if settings is None else settings.browser,
                chrome_profile=None if settings is None else settings.chrome_profile,
                open_target="tab" if settings is None else settings.open_target,
            )
        )
    window = projection.window_settings
    return FlatWorkspaceState(
        title=projection.application_title,
        workspace_id=workspace_id,
        workspace_name=projection.name,
        tabs=tuple(
            FlatTabState(tab.id, tab.name, tab.visibility == "hidden")
            for tab in active_tabs
        ),
        tab_order=tuple(tab.id for tab in active_tabs),
        tiles=tuple(tiles),
        columns=_DEFAULT_COLUMNS if window is None else window.columns,
        auto_fit=_DEFAULT_AUTO_FIT if window is None else window.auto_fit,
        window_x=None if window is None else window.window_x,
        window_y=None if window is None else window.window_y,
        window_w=None if window is None else window.window_w,
        window_h=None if window is None else window.window_h,
        editable=_editable_flat_graph(root, workspace_id),
    )


def _valid_tab_state(state: FlatWorkspaceState, used_ids: set[str]) -> bool:
    if type(state.tabs) is not tuple or type(state.tab_order) is not tuple:
        return False
    if not state.tabs or any(not isinstance(tab, FlatTabState) for tab in state.tabs):
        return False
    for tab in state.tabs:
        if (
            _canonical_uuid(tab.id) is None
            or not _is_utf8_text(tab.name)
            or not tab.name
            or type(tab.hidden) is not bool
        ):
            return False

    tab_ids = [tab.id for tab in state.tabs]
    tab_names = [tab.name for tab in state.tabs]
    if (
        len(tab_ids) != len(set(tab_ids))
        or tuple(tab_ids) != state.tab_order
        or len(tab_names) != len(set(tab_names))
        or not any(not tab.hidden for tab in state.tabs)
    ):
        return False
    return all(tab_id in used_ids or UUID(tab_id).version == 4 for tab_id in tab_ids)


def _valid_tile_values(tile: object, tab_ids: set[str]) -> bool:
    if not isinstance(tile, FlatTileState):
        return False
    if any(
        value is not None and _canonical_uuid(value) is None
        for value in (
            tile.placement_id,
            tile.resource_id,
            tile.launch_binding_id,
            tile.duplicate_source_placement_id,
        )
    ):
        return False
    return (
        _is_utf8_text(tile.tab_id)
        and tile.tab_id in tab_ids
        and _is_utf8_text(tile.name)
        and _is_utf8_text(tile.url)
        and (tile.icon is None or _is_utf8_text(tile.icon))
        and _is_utf8_text(tile.background_color)
        and (tile.browser is None or _is_utf8_text(tile.browser))
        and (tile.chrome_profile is None or _is_utf8_text(tile.chrome_profile))
        and type(tile.open_target) is str
        and tile.open_target in ("tab", "window")
    )


def _valid_new_tile_positions(tiles: tuple[FlatTileState, ...]) -> bool:
    """Require flat Add/Import suffixes and source-adjacent Duplicates."""

    tiles_by_tab: dict[str, list[FlatTileState]] = {}
    for tile in tiles:
        tiles_by_tab.setdefault(tile.tab_id, []).append(tile)

    for ordered_tiles in tiles_by_tab.values():
        append_started = False
        previous: FlatTileState | None = None
        for tile in ordered_tiles:
            if tile.placement_id is not None:
                if append_started:
                    return False
            elif tile.duplicate_source_placement_id is None:
                append_started = True
            else:
                source_id = tile.duplicate_source_placement_id
                follows_source = previous is not None and (
                    previous.placement_id == source_id
                    or (
                        previous.placement_id is None
                        and previous.duplicate_source_placement_id == source_id
                    )
                )
                if append_started or not follows_source:
                    return False
            previous = tile
    return True


def _duplicate_values_match(tile: FlatTileState, source: FlatTileState) -> bool:
    return (
        tile.name,
        tile.url,
        tile.tab_id,
        tile.icon,
        tile.background_color,
        tile.browser,
        tile.chrome_profile,
        tile.open_target,
    ) == (
        source.name,
        source.url,
        source.tab_id,
        source.icon,
        source.background_color,
        source.browser,
        source.chrome_profile,
        source.open_target,
    )


def _full_tab_order(
    existing: list[str],
    old_active_ids: set[str],
    desired_active_ids: list[str],
) -> list[str]:
    desired_existing = {item for item in desired_active_ids if item in old_active_ids}
    desired_iter = iter(desired_active_ids)
    retained = [
        item
        for item in existing
        if item not in old_active_ids or item in desired_existing
    ]
    reordered = [
        next(desired_iter) if item in old_active_ids else item for item in retained
    ]
    reordered.extend(desired_iter)
    return reordered


def synchronize_flat_workspace(
    document: object,
    state: object,
    *,
    id_factory: v1.Uuid4Allocator = uuid4,
) -> FlatWorkspaceUpdateResult:
    """Apply one current-UI edit to a detached, complete v2 graph candidate."""

    base_state = project_flat_workspace(document)
    if isinstance(base_state, FlatStateRejected):
        return base_state
    if not base_state.editable:
        return FlatStateRejected("unsupported_graph")
    if not isinstance(state, FlatWorkspaceState):
        return FlatStateRejected("invalid_state")
    if state.workspace_id != base_state.workspace_id:
        return FlatStateRejected("invalid_state")
    cloned = runtime.clone_document(document)
    if isinstance(cloned, runtime.RuntimeAdapterRejected):
        return FlatStateRejected("invalid_graph")
    root = cloned
    used_ids = set(reserved_entity_ids(root))
    if (
        not _is_utf8_text(state.title)
        or not _is_utf8_text(state.workspace_name)
        or not state.workspace_name
        or type(state.columns) is not int
        or type(state.auto_fit) is not bool
        or any(
            value is not None and type(value) is not int
            for value in (
                state.window_x,
                state.window_y,
                state.window_w,
                state.window_h,
            )
        )
        or not _valid_tab_state(state, used_ids)
    ):
        return FlatStateRejected("invalid_state")

    workspace = next(
        item for item in root["workspaces"] if item["id"] == state.workspace_id
    )
    old_tabs = {
        tab["id"]: tab
        for tab in root["tabs"]
        if tab["workspace_id"] == state.workspace_id and tab["lifecycle"] == "active"
    }
    old_active_ids = set(old_tabs)
    desired_tabs = {tab.id: tab for tab in state.tabs}
    new_tab_ids = set(desired_tabs) - old_active_ids
    if any(tab_id in used_ids for tab_id in new_tab_ids):
        return FlatStateRejected("invalid_state")
    used_ids.update(new_tab_ids)
    if (
        type(state.tiles) is not tuple
        or not all(_valid_tile_values(tile, set(desired_tabs)) for tile in state.tiles)
        or not _valid_new_tile_positions(state.tiles)
    ):
        return FlatStateRejected("invalid_state")

    placements = {item["id"]: item for item in root["placements"]}
    resources = {item["id"]: item for item in root["resources"]}
    _, launches = _portable_bindings(root)
    source_tiles = {
        tile.placement_id: tile
        for tile in base_state.tiles
        if tile.placement_id is not None
    }
    bindings_by_placement: dict[str, list[v2.PlacementLaunchBinding]] = {}
    for binding in root["device_bindings"]:
        if binding["subject_kind"] != "placement":
            continue
        bindings_by_placement.setdefault(binding["subject_id"], []).append(binding)
    managed_placement_ids = {
        item["id"] for item in root["placements"] if item["tab_id"] in old_active_ids
    }
    seen_placements: set[str] = set()
    prepared_tiles: list[tuple[FlatTileState, FlatTileIdentity]] = []
    new_definitions: list[
        tuple[v2.Resource, v2.Placement, list[v2.PlacementLaunchBinding]]
    ] = []
    for tile in state.tiles:
        if tile.placement_id is None:
            if tile.resource_id is not None or tile.launch_binding_id is not None:
                return FlatStateRejected("invalid_state")
            duplicate_source_id = tile.duplicate_source_placement_id
            source_tile = (
                None
                if duplicate_source_id is None
                else source_tiles.get(duplicate_source_id)
            )
            if duplicate_source_id is not None and (
                _canonical_uuid(duplicate_source_id) is None
                or source_tile is None
                or not _duplicate_values_match(tile, source_tile)
            ):
                return FlatStateRejected("invalid_state")
            resource_id = _allocate_id(id_factory, used_ids)
            if resource_id is None:
                return FlatStateRejected("identity_allocation_failure")
            placement_id = _allocate_id(id_factory, used_ids)
            if placement_id is None:
                return FlatStateRejected("identity_allocation_failure")
            icon = (
                None
                if tile.icon is None
                else v2.LegacyStringIcon(kind="legacy_string", value=tile.icon)
            )
            resource = v2.Resource(
                id=resource_id,
                kind="url",
                target=v2.UrlTarget(url=tile.url),
                default_label=tile.name,
                default_icon=icon,
                extensions={},
            )
            placement = v2.Placement(
                id=placement_id,
                resource_id=resource_id,
                tab_id=tile.tab_id,
                label_override=None,
                icon_override=None,
                background_color=tile.background_color,
                workflow_status="in_use",
                extensions={},
            )
            new_bindings: list[v2.PlacementLaunchBinding] = []
            portable_binding_id: str | None = None
            if duplicate_source_id is None:
                binding_id = _allocate_id(id_factory, used_ids)
                if binding_id is None:
                    return FlatStateRejected("identity_allocation_failure")
                new_bindings.append(
                    v2.PlacementLaunchBinding(
                        id=binding_id,
                        subject_kind="placement",
                        subject_id=placement_id,
                        binding_kind="launch",
                        applicability=v2.PortableFallback(kind="portable_fallback"),
                        settings=v2.UrlLaunchSettings(
                            browser=tile.browser,
                            chrome_profile=tile.chrome_profile,
                            open_target=tile.open_target,
                        ),
                        extensions={},
                    )
                )
                portable_binding_id = binding_id
            else:
                for source_binding in bindings_by_placement.get(
                    duplicate_source_id,
                    [],
                ):
                    binding_id = _allocate_id(id_factory, used_ids)
                    if binding_id is None:
                        return FlatStateRejected("identity_allocation_failure")
                    copied_binding = cast(
                        v2.PlacementLaunchBinding,
                        _clone_strict_json(
                            cast(v2.StrictJsonValue, source_binding),
                        ),
                    )
                    copied_binding["id"] = binding_id
                    copied_binding["subject_id"] = placement_id
                    new_bindings.append(copied_binding)
                    if copied_binding["applicability"]["kind"] == "portable_fallback":
                        portable_binding_id = binding_id
                if portable_binding_id is None:
                    return FlatStateRejected("invalid_state")
            new_definitions.append((resource, placement, new_bindings))
            prepared_tiles.append(
                (
                    tile,
                    FlatTileIdentity(
                        placement_id,
                        resource_id,
                        portable_binding_id,
                    ),
                )
            )
            continue

        placement_id = tile.placement_id
        if (
            tile.duplicate_source_placement_id is not None
            or placement_id in seen_placements
            or placement_id not in managed_placement_ids
        ):
            return FlatStateRejected("invalid_state")
        seen_placements.add(placement_id)
        placement = placements[placement_id]
        existing_binding = launches.get(placement_id)
        if (
            tile.resource_id != placement["resource_id"]
            or existing_binding is None
            or tile.launch_binding_id != existing_binding["id"]
        ):
            return FlatStateRejected("invalid_state")
        prepared_tiles.append(
            (
                tile,
                FlatTileIdentity(
                    placement_id,
                    placement["resource_id"],
                    existing_binding["id"],
                ),
            )
        )

    current_tiles = {
        tile.placement_id: tile
        for tile, _identity in prepared_tiles
        if tile.placement_id is not None
    }
    for tile, _identity in prepared_tiles:
        source_id = tile.duplicate_source_placement_id
        if source_id is None:
            continue
        current_source = current_tiles.get(source_id)
        base_source = source_tiles.get(source_id)
        if (
            source_id not in seen_placements
            or current_source is None
            or base_source is None
            or not _duplicate_values_match(current_source, base_source)
        ):
            return FlatStateRejected("invalid_state")

    removed_placements = managed_placement_ids - seen_placements
    removed_tabs = old_active_ids - set(desired_tabs)
    root["placements"] = [
        item for item in root["placements"] if item["id"] not in removed_placements
    ]
    root["device_bindings"] = [
        item
        for item in root["device_bindings"]
        if not (
            item["subject_kind"] == "placement"
            and item["subject_id"] in removed_placements
        )
    ]
    root["tabs"] = [item for item in root["tabs"] if item["id"] not in removed_tabs]

    root["application"]["title"] = state.title
    workspace["name"] = state.workspace_name
    workspace["tab_order"] = _full_tab_order(
        workspace["tab_order"],
        old_active_ids,
        list(state.tab_order),
    )
    candidate_tabs = {item["id"]: item for item in root["tabs"]}
    for tab_id, desired in desired_tabs.items():
        if tab_id in old_tabs:
            tab = candidate_tabs[tab_id]
            tab["name"] = desired.name
            tab["visibility"] = "hidden" if desired.hidden else "visible"
            continue
        tab = v2.Tab(
            id=tab_id,
            workspace_id=state.workspace_id,
            name=desired.name,
            visibility="hidden" if desired.hidden else "visible",
            lifecycle="active",
            view_mode="display",
            display_filter=["new", "in_use"],
            display_order=[],
            kanban_order=v2.KanbanOrder(new=[], in_use=[], archived=[]),
            extensions={},
        )
        root["tabs"].append(tab)
        candidate_tabs[tab_id] = tab

    windows, _ = _portable_bindings(root)
    window_binding = windows[state.workspace_id]
    window_binding["settings"] = v2.WindowSettings(
        columns=state.columns,
        auto_fit=state.auto_fit,
        window_x=state.window_x,
        window_y=state.window_y,
        window_w=state.window_w,
        window_h=state.window_h,
    )

    for resource, placement, bindings in new_definitions:
        root["resources"].append(resource)
        root["placements"].append(placement)
        resources[resource["id"]] = resource
        placements[placement["id"]] = placement
        for binding in bindings:
            root["device_bindings"].append(binding)
            if binding["applicability"]["kind"] == "portable_fallback":
                launches[placement["id"]] = binding

    desired_by_tab: dict[str, list[str]] = {tab_id: [] for tab_id in desired_tabs}
    old_tab_by_placement = {
        placement_id: placements[placement_id]["tab_id"]
        for placement_id in seen_placements
    }
    identities: list[FlatTileIdentity] = []
    duplicate_sources: dict[str, str] = {}
    appended_by_tab: dict[str, list[str]] = {tab_id: [] for tab_id in desired_tabs}
    moved_by_tab: dict[str, list[str]] = {tab_id: [] for tab_id in desired_tabs}
    for tile, identity in prepared_tiles:
        placement = placements[identity.placement_id]
        resource = resources[identity.resource_id]
        binding = launches[identity.placement_id]
        old_tab_id = old_tab_by_placement.get(identity.placement_id)
        if old_tab_id is None:
            if tile.duplicate_source_placement_id is None:
                appended_by_tab[tile.tab_id].append(identity.placement_id)
            else:
                duplicate_sources[identity.placement_id] = (
                    tile.duplicate_source_placement_id
                )
        elif old_tab_id != tile.tab_id:
            moved_by_tab[tile.tab_id].append(identity.placement_id)
        placement["tab_id"] = tile.tab_id
        placement["background_color"] = tile.background_color
        resource["target"]["url"] = tile.url
        resource["default_label"] = tile.name
        resource["default_icon"] = (
            None
            if tile.icon is None
            else v2.LegacyStringIcon(kind="legacy_string", value=tile.icon)
        )
        binding["settings"] = v2.UrlLaunchSettings(
            browser=tile.browser,
            chrome_profile=tile.chrome_profile,
            open_target=tile.open_target,
        )
        desired_by_tab[tile.tab_id].append(identity.placement_id)
        identities.append(identity)

    for tab_id, tab in candidate_tabs.items():
        if tab_id not in desired_tabs:
            continue
        old_queue = [
            placement_id
            for placement_id in tab["kanban_order"]["in_use"]
            if placement_id in seen_placements
            and placements[placement_id]["tab_id"] == tab_id
        ]
        for index, placement_id in enumerate(desired_by_tab[tab_id]):
            source_id = duplicate_sources.get(placement_id)
            if source_id is None:
                continue
            insertion = old_queue.index(source_id) + 1
            prior_display_ids = set(desired_by_tab[tab_id][:index])
            while (
                insertion < len(old_queue)
                and old_queue[insertion] in prior_display_ids
                and duplicate_sources.get(old_queue[insertion]) == source_id
            ):
                insertion += 1
            old_queue.insert(insertion, placement_id)
        tab["display_order"] = desired_by_tab[tab_id]
        tab["kanban_order"]["new"] = []
        tab["kanban_order"]["in_use"] = (
            old_queue + appended_by_tab[tab_id] + moved_by_tab[tab_id]
        )
        tab["kanban_order"]["archived"] = []

    if not v2.validate_v2(root):
        return FlatStateRejected("invalid_state")
    return FlatWorkspaceUpdate(root, tuple(identities))


__all__ = [
    "FlatStateFailureCategory",
    "FlatStateRejected",
    "FlatTabState",
    "FlatTileIdentity",
    "FlatTileState",
    "FlatWorkspaceProjectionResult",
    "FlatWorkspaceState",
    "FlatWorkspaceUpdate",
    "FlatWorkspaceUpdateResult",
    "NativeV2ConstructionError",
    "build_native_v2",
    "project_flat_workspace",
    "reserved_entity_ids",
    "synchronize_flat_workspace",
]
