from __future__ import annotations

import pytest

from debug_scaffold import sanitize_log_extra


@pytest.mark.unit
@pytest.mark.parametrize(
    "extra,expected",
    [
        (
            {"name": "tile", "message": "msg", "levelname": "INFO", "other": 1},
            {
                "tile_name": "tile",
                "event_message": "msg",
                "extra_levelname": "INFO",
                "other": 1,
            },
        ),
        ({"foo": "bar"}, {"foo": "bar"}),
    ],
)
def test_sanitize_log_extra(
    extra: dict[str, object], expected: dict[str, object]
) -> None:
    assert sanitize_log_extra(extra) == expected  # nosec B101


@pytest.mark.unit
def test_sanitize_log_extra_none() -> None:
    assert sanitize_log_extra(None) is None  # nosec B101
