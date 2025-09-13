import pytest

from tile_launcher import compute_grid_fit


@pytest.mark.unit
@pytest.mark.parametrize(
    "avail_w,avail_h,total_tiles,expected_cols,expected_rows,need_scroll",
    [
        (1280, 800, 20, 7, 3, False),
        (800, 600, 50, 4, 3, True),
        (352, 400, 10, 1, 2, True),
    ],
)
def test_compute_grid_fit_auto(
    avail_w: int,
    avail_h: int,
    total_tiles: int,
    expected_cols: int,
    expected_rows: int,
    need_scroll: bool,
) -> None:
    res = compute_grid_fit(
        avail_w,
        avail_h,
        150,
        140,
        12,
        32,
        32,
        0,
        0,
        16,
        total_tiles,
        None,
    )
    assert res.columns == expected_cols  # nosec B101
    assert res.rows_visible == expected_rows  # nosec B101
    assert res.need_vscroll is need_scroll  # nosec B101


@pytest.mark.unit
def test_compute_grid_fit_fixed_columns() -> None:
    res = compute_grid_fit(
        1280,
        800,
        150,
        140,
        12,
        32,
        32,
        0,
        0,
        16,
        10,
        3,
    )
    assert res.columns == 3  # nosec B101
    assert not res.need_vscroll  # nosec B101
    assert res.window_w == 3 * 150 + 2 * 12 + 32  # nosec B101
