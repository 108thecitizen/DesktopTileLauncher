# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from debug_scaffold import (
    sanitize_diagnostic_value,
    sanitize_launch_command,
    sanitize_url,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "http://user:pass@example.test/path?token=sample-token&ok=1",
            "http://example.test/path?token=REDACTED&ok=REDACTED",
        ),
        (
            "https://example.test/?Code=sample-code&next=page",
            "https://example.test/?Code=REDACTED&next=REDACTED",
        ),
        (
            "https://user:pass@example.test/path?search=term#frag-value",
            "https://example.test/path?search=REDACTED#REDACTED",
        ),
        (
            "https://example.test/path",
            "https://example.test/path",
        ),
    ],
)
def test_sanitize_url(raw: str, expected: str) -> None:
    assert sanitize_url(raw) == expected  # nosec B101


@pytest.mark.unit
def test_sanitize_launch_command_redacts_url_argument() -> None:
    raw_url = "https://user:pass@example.test/path?state=sample-state#frag-value"
    command = ["firefox", "--new-tab", raw_url]

    sanitized = sanitize_launch_command(command)

    assert sanitized == [  # nosec B101
        "firefox",
        "--new-tab",
        "https://example.test/path?state=REDACTED#REDACTED",
    ]


@pytest.mark.unit
def test_sanitize_diagnostic_value_redacts_embedded_url() -> None:
    raw = {
        "last_launch_command": (
            "firefox --new-tab "
            "https://user:pass@example.test/path?token=sample-token#frag-value"
        )
    }

    sanitized = sanitize_diagnostic_value(raw)

    assert "sample-token" not in str(sanitized)  # nosec B101
    assert "user:pass@" not in str(sanitized)  # nosec B101
    assert "frag-value" not in str(sanitized)  # nosec B101
    assert "REDACTED" in str(sanitized)  # nosec B101
