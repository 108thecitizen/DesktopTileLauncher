from __future__ import annotations

import pytest

from debug_scaffold import sanitize_url


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "http://user:pass@example.com/path?token=abc&ok=1",
            "http://example.com/path?token=REDACTED&ok=1",
        ),
        (
            "https://example.com/?Code=123&next=page",
            "https://example.com/?Code=REDACTED&next=page",
        ),
        (
            "https://example.com/path",
            "https://example.com/path",
        ),
    ],
)
def test_sanitize_url(raw: str, expected: str) -> None:
    assert sanitize_url(raw) == expected
