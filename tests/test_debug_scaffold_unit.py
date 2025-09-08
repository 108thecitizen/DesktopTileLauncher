import pytest
from debug_scaffold import sanitize_url, SENSITIVE_KEYS

pytestmark = pytest.mark.unit


def test_sanitize_url_redacts_sensitive_params():
    url = "https://example.com/path?token=abc123&code=xyz&ok=1"
    out = sanitize_url(url)
    assert "token=REDACTED" in out
    assert "code=REDACTED" in out
    assert "ok=1" in out


def test_sensitive_keys_catalog_is_nonempty():
    assert SENSITIVE_KEYS
