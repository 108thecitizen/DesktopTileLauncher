import pytest
from debug_scaffold import SENSITIVE_KEYS, sanitize_url

pytestmark = pytest.mark.unit


def test_sanitize_url_redacts_sensitive_params():
    url = "https://example.test/path?token=sample-token&code=sample-code&ok=1"
    out = sanitize_url(url)
    assert "token=REDACTED" in out
    assert "code=REDACTED" in out
    assert "ok=REDACTED" in out


def test_sensitive_keys_catalog_is_nonempty():
    assert SENSITIVE_KEYS
