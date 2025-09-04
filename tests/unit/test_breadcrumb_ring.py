from __future__ import annotations

import pytest

from debug_scaffold import BREADCRUMB_LIMIT, get_breadcrumbs, record_breadcrumb


@pytest.mark.unit
def test_breadcrumb_ring_limit() -> None:
    for i in range(BREADCRUMB_LIMIT + 10):
        record_breadcrumb("evt", idx=i)
    crumbs = get_breadcrumbs()
    assert len(crumbs) == BREADCRUMB_LIMIT
    assert crumbs[0]["idx"] == 10
    assert crumbs[-1]["idx"] == BREADCRUMB_LIMIT + 9
