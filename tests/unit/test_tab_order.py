# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from tab_order import (
    TabOrderState,
    add_tab,
    delete_tab,
    move_visible_tab,
    normalize_tab_order,
    rename_tab,
)

A_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
B_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
C_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
D_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
H_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
OBSOLETE_ID = "11111111-1111-4111-8111-111111111111"
UNKNOWN_ID = "22222222-2222-4222-8222-222222222222"


def _id_factory(values: list[str]) -> Callable[[], str]:
    identifiers = iter(values)
    return lambda: next(identifiers)


@pytest.mark.unit
def test_legacy_migration_preserves_existing_tab_order() -> None:
    state = normalize_tab_order(
        ["Main", "Work", "Personal"],
        None,
        None,
        _id_factory([A_ID, B_ID, C_ID]),
    )

    assert state.tabs == ["Main", "Work", "Personal"]
    assert state.tab_ids == {"Main": A_ID, "Work": B_ID, "Personal": C_ID}
    assert state.tab_order == [A_ID, B_ID, C_ID]


@pytest.mark.unit
def test_saved_order_uses_identifiers_and_reorders_titles() -> None:
    state = normalize_tab_order(
        ["Main", "Renamed Work"],
        {"Main": A_ID.upper(), "Renamed Work": B_ID},
        [B_ID, A_ID.upper()],
    )

    assert state.tabs == ["Renamed Work", "Main"]
    assert state.tab_ids == {"Renamed Work": B_ID, "Main": A_ID}
    assert state.tab_order == [B_ID, A_ID]


@pytest.mark.unit
def test_normalized_persistence_fields_round_trip_through_json() -> None:
    state = normalize_tab_order(
        ["Main", "Work"],
        {"Main": A_ID, "Work": B_ID},
        [B_ID, A_ID],
    )
    payload = json.loads(
        json.dumps({"tab_ids": state.tab_ids, "tab_order": state.tab_order})
    )

    reloaded = normalize_tab_order(
        state.tabs,
        payload["tab_ids"],
        payload["tab_order"],
    )

    assert reloaded == state


@pytest.mark.unit
def test_partial_and_malformed_saved_data_is_safely_normalized() -> None:
    state = normalize_tab_order(
        ["A", "B", "C", "D"],
        {
            "A": A_ID,
            "B": A_ID,
            "C": C_ID,
            "D": "not-a-uuid",
            "Obsolete": OBSOLETE_ID,
        },
        [None, C_ID, UNKNOWN_ID, A_ID, C_ID, "not-a-uuid", 42],
        _id_factory([A_ID, OBSOLETE_ID, B_ID, C_ID, D_ID]),
    )

    assert state.tabs == ["C", "A", "B", "D"]
    assert state.tab_ids == {"C": C_ID, "A": A_ID, "B": B_ID, "D": D_ID}
    assert state.tab_order == [C_ID, A_ID, B_ID, D_ID]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_tab_ids", "raw_tab_order"),
    [
        ([A_ID], [A_ID]),
        ({"A": A_ID}, "not-a-list"),
        ("not-a-mapping", {"not": "a-list"}),
    ],
)
def test_malformed_top_level_saved_data_falls_back_to_current_order(
    raw_tab_ids: object, raw_tab_order: object
) -> None:
    state = normalize_tab_order(
        ["A", "B"],
        raw_tab_ids,
        raw_tab_order,
        _id_factory([B_ID, C_ID]),
    )

    assert state.tabs == ["A", "B"]
    assert state.tab_order == list(state.tab_ids.values())


@pytest.mark.unit
def test_rename_transfers_identifier_without_moving_tab() -> None:
    state = TabOrderState(
        tabs=["B", "A"],
        tab_ids={"B": B_ID, "A": A_ID},
        tab_order=[B_ID, A_ID],
    )

    renamed = rename_tab(state, "A", "Renamed A")

    assert renamed.tabs == ["B", "Renamed A"]
    assert renamed.tab_ids == {"B": B_ID, "Renamed A": A_ID}
    assert renamed.tab_order == [B_ID, A_ID]


@pytest.mark.unit
def test_add_appends_unique_identifier_after_factory_collision() -> None:
    state = TabOrderState(["A"], {"A": A_ID}, [A_ID])

    added = add_tab(state, "B", _id_factory([A_ID, B_ID]))

    assert added.tabs == ["A", "B"]
    assert added.tab_ids == {"A": A_ID, "B": B_ID}
    assert added.tab_order == [A_ID, B_ID]


@pytest.mark.unit
def test_delete_removes_identifier_and_all_order_occurrences() -> None:
    state = TabOrderState(
        ["A", "B", "C"],
        {"A": A_ID, "B": B_ID, "C": C_ID},
        [A_ID, B_ID, B_ID, C_ID],
    )

    deleted = delete_tab(state, "B")

    assert deleted.tabs == ["A", "C"]
    assert deleted.tab_ids == {"A": A_ID, "C": C_ID}
    assert deleted.tab_order == [A_ID, C_ID]


@pytest.mark.unit
def test_move_visible_tab_to_front_across_hidden_tab() -> None:
    moved = move_visible_tab(
        [A_ID, H_ID, B_ID, C_ID],
        [H_ID],
        2,
        0,
        [C_ID, A_ID, B_ID],
    )

    assert moved == [C_ID, A_ID, H_ID, B_ID]


@pytest.mark.unit
def test_move_visible_tab_to_back_across_hidden_tab() -> None:
    moved = move_visible_tab(
        [A_ID, H_ID, B_ID, C_ID],
        [H_ID],
        0,
        2,
        [B_ID, C_ID, A_ID],
    )

    assert moved == [H_ID, B_ID, C_ID, A_ID]


@pytest.mark.unit
def test_move_visible_tab_left_keeps_hidden_tab_before_destination() -> None:
    moved = move_visible_tab(
        [A_ID, H_ID, B_ID, C_ID],
        [H_ID],
        2,
        1,
        [A_ID, C_ID, B_ID],
    )

    assert moved == [A_ID, H_ID, C_ID, B_ID]


@pytest.mark.unit
def test_move_visible_tab_right_keeps_hidden_tab_with_non_dragged_tabs() -> None:
    moved = move_visible_tab(
        [A_ID, H_ID, B_ID, C_ID],
        [H_ID],
        0,
        1,
        [B_ID, A_ID, C_ID],
    )

    assert moved == [H_ID, B_ID, A_ID, C_ID]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("full_order", "hidden_ids", "from_index", "to_index", "visible_after"),
    [
        ([A_ID, H_ID, B_ID], [H_ID], -1, 1, [B_ID, A_ID]),
        ([A_ID, H_ID, B_ID], [H_ID], 0, 2, [B_ID, A_ID]),
        ([A_ID, H_ID, B_ID], [H_ID], 0, 0, [A_ID, B_ID]),
        ([A_ID, H_ID, B_ID], [H_ID], 0, 1, [A_ID, B_ID]),
        ([A_ID, H_ID, B_ID], [H_ID], 0, 1, [B_ID, B_ID]),
        ([A_ID, H_ID, B_ID], [H_ID], 0, 1, [B_ID, None]),
        ([A_ID, H_ID, B_ID], [UNKNOWN_ID], 0, 1, [B_ID, A_ID]),
        ([A_ID, H_ID, B_ID], [H_ID, H_ID], 0, 1, [B_ID, A_ID]),
        ([A_ID, A_ID, B_ID], [], 0, 1, [B_ID, A_ID, A_ID]),
        (["not-a-uuid", B_ID], [], 0, 1, [B_ID, "not-a-uuid"]),
    ],
)
def test_invalid_or_inconsistent_move_is_safe_no_op(
    full_order: list[str],
    hidden_ids: list[object],
    from_index: int,
    to_index: int,
    visible_after: list[object],
) -> None:
    original = list(full_order)

    moved = move_visible_tab(
        full_order, hidden_ids, from_index, to_index, visible_after
    )

    assert moved == original
    assert full_order == original
