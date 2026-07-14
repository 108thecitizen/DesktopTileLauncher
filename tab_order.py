# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

TabIdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class TabOrderState:
    """Normalized tab titles, stable identifiers, and canonical full order."""

    tabs: list[str]
    tab_ids: dict[str, str]
    tab_order: list[str]


def new_tab_id() -> str:
    """Return a new stable identifier suitable for persisted tab state."""

    return str(uuid4())


def _canonical_tab_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _new_unique_tab_id(blocked_ids: set[str], id_factory: TabIdFactory) -> str:
    while True:
        candidate = _canonical_tab_id(id_factory())
        if candidate is not None and candidate not in blocked_ids:
            return candidate


def _reserved_saved_ids(raw_tab_ids: object, raw_tab_order: object) -> set[str]:
    reserved: set[str] = set()
    if isinstance(raw_tab_ids, dict):
        for value in raw_tab_ids.values():
            candidate = _canonical_tab_id(value)
            if candidate is not None:
                reserved.add(candidate)
    if isinstance(raw_tab_order, list):
        for value in raw_tab_order:
            candidate = _canonical_tab_id(value)
            if candidate is not None:
                reserved.add(candidate)
    return reserved


def normalize_tab_order(
    tabs: Sequence[str],
    raw_tab_ids: object,
    raw_tab_order: object,
    id_factory: TabIdFactory = new_tab_id,
) -> TabOrderState:
    """Normalize persisted tab identity and ordering data without losing tabs.

    Existing valid identifiers are retained. Missing, malformed, or duplicate
    identifiers are replaced, and unknown order entries are discarded. Any known
    identifiers omitted from the saved order are appended in the input tab order.
    """

    clean_tabs: list[str] = []
    seen_titles: set[str] = set()
    for title in tabs:
        if isinstance(title, str) and title not in seen_titles:
            clean_tabs.append(title)
            seen_titles.add(title)

    saved_ids = raw_tab_ids if isinstance(raw_tab_ids, dict) else {}
    reserved_ids = _reserved_saved_ids(raw_tab_ids, raw_tab_order)
    used_ids: set[str] = set()
    tab_ids: dict[str, str] = {}
    for title in clean_tabs:
        candidate = _canonical_tab_id(saved_ids.get(title))
        if candidate is None or candidate in used_ids:
            candidate = _new_unique_tab_id(reserved_ids | used_ids, id_factory)
        tab_ids[title] = candidate
        used_ids.add(candidate)

    title_by_id = {tab_id: title for title, tab_id in tab_ids.items()}
    tab_order: list[str] = []
    seen_order_ids: set[str] = set()
    if isinstance(raw_tab_order, list):
        for raw_id in raw_tab_order:
            tab_id = _canonical_tab_id(raw_id)
            if (
                tab_id is not None
                and tab_id in title_by_id
                and tab_id not in seen_order_ids
            ):
                tab_order.append(tab_id)
                seen_order_ids.add(tab_id)

    for title in clean_tabs:
        tab_id = tab_ids[title]
        if tab_id not in seen_order_ids:
            tab_order.append(tab_id)
            seen_order_ids.add(tab_id)

    ordered_tabs = [title_by_id[tab_id] for tab_id in tab_order]
    ordered_tab_ids = {title: tab_ids[title] for title in ordered_tabs}
    return TabOrderState(ordered_tabs, ordered_tab_ids, tab_order)


def rename_tab(state: TabOrderState, old_title: str, new_title: str) -> TabOrderState:
    """Rename a tab without changing its stable identifier or position."""

    if old_title not in state.tab_ids or new_title in state.tab_ids:
        return state

    tabs = [new_title if title == old_title else title for title in state.tabs]
    tab_ids = {
        new_title if title == old_title else title: tab_id
        for title, tab_id in state.tab_ids.items()
    }
    return TabOrderState(tabs, tab_ids, list(state.tab_order))


def add_tab(
    state: TabOrderState,
    title: str,
    id_factory: TabIdFactory = new_tab_id,
) -> TabOrderState:
    """Append a newly identified tab to the canonical full order."""

    if title in state.tab_ids:
        return state

    used_ids = set(state.tab_ids.values()) | set(state.tab_order)
    tab_id = _new_unique_tab_id(used_ids, id_factory)
    return TabOrderState(
        [*state.tabs, title],
        {**state.tab_ids, title: tab_id},
        [*state.tab_order, tab_id],
    )


def delete_tab(state: TabOrderState, title: str) -> TabOrderState:
    """Remove a tab and every occurrence of its identifier from the order."""

    tab_id = state.tab_ids.get(title)
    if tab_id is None:
        return state

    return TabOrderState(
        [existing for existing in state.tabs if existing != title],
        {
            existing: existing_id
            for existing, existing_id in state.tab_ids.items()
            if existing != title
        },
        [existing_id for existing_id in state.tab_order if existing_id != tab_id],
    )


def _is_canonical_unique_id_sequence(values: Sequence[object]) -> bool:
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or _canonical_tab_id(value) != value:
            return False
        if value in seen:
            return False
        seen.add(value)
    return True


def move_visible_tab(
    full_order: Sequence[str],
    hidden_ids: Sequence[object],
    from_index: int,
    to_index: int,
    visible_ids_after: Sequence[object],
) -> list[str]:
    """Apply a validated visible-tab move to the canonical full order.

    ``visible_ids_after`` is the QTabBar data read after ``tabMoved`` fires. It
    must exactly match the move described by the signal. Invalid or inconsistent
    inputs return an unchanged copy of ``full_order``.
    """

    unchanged = list(full_order)
    full_values: list[object] = list(full_order)
    if not _is_canonical_unique_id_sequence(full_values):
        return unchanged
    if not _is_canonical_unique_id_sequence(hidden_ids):
        return unchanged

    full_ids = set(full_order)
    hidden = set(hidden_ids)
    if not hidden.issubset(full_ids):
        return unchanged

    visible_before = [tab_id for tab_id in full_order if tab_id not in hidden]
    if (
        type(from_index) is not int
        or type(to_index) is not int
        or from_index == to_index
        or not 0 <= from_index < len(visible_before)
        or not 0 <= to_index < len(visible_before)
    ):
        return unchanged

    if not _is_canonical_unique_id_sequence(visible_ids_after):
        return unchanged
    actual_after = list(visible_ids_after)
    expected_after = list(visible_before)
    moved_id = expected_after.pop(from_index)
    expected_after.insert(to_index, moved_id)
    if actual_after != expected_after:
        return unchanged

    remaining_order = [tab_id for tab_id in full_order if tab_id != moved_id]
    destination_id = visible_before[to_index]
    insertion_index = remaining_order.index(destination_id)
    if from_index < to_index:
        insertion_index += 1
    remaining_order.insert(insertion_index, moved_id)
    return remaining_order
