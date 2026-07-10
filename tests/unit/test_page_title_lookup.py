# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import http.client
import socket
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from typing import cast

import pytest

from page_title_lookup import (
    MAX_REDIRECTS,
    MAX_RESPONSE_BYTES,
    MAX_TITLE_LENGTH,
    NameOwnership,
    PageTitleResponse,
    TitleSuggestionController,
    extract_title,
    fetch_page_title,
    normalize_title,
    normalize_title_lookup_url,
)


pytestmark = pytest.mark.unit
_EXPECTED_USER_AGENT = (
    "DesktopTileLauncher (+https://github.com/108thecitizen/DesktopTileLauncher)"
)


class _Headers(dict[str, str]):
    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        super().__init__()
        for key, value in (values or {}).items():
            self[key] = value

    def __setitem__(self, key: str, value: str) -> None:
        super().__setitem__(key.lower(), value)

    def get(self, key: str, default: str | None = None) -> str | None:
        return super().get(key.lower(), default)


class _Response:
    def __init__(
        self,
        body: bytes = b"<html><head><title>Example</title></head></html>",
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        read_error: BaseException | None = None,
    ) -> None:
        self.status = status
        self.headers = _Headers({"Content-Type": "text/html; charset=utf-8"})
        for key, value in (headers or {}).items():
            self.headers[key] = value
        self._body = body
        self._cursor = 0
        self._read_error = read_error
        self.closed = False
        self.read_sizes: list[int] = []
        self.bytes_read = 0

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def getcode(self) -> int:
        return self.status

    def read(self, amt: int = -1) -> bytes:
        self.read_sizes.append(amt)
        if self._read_error is not None:
            raise self._read_error
        if amt < 0:
            chunk = self._body[self._cursor :]
        else:
            chunk = self._body[self._cursor : self._cursor + amt]
        self._cursor += len(chunk)
        self.bytes_read += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class _ClosableBody:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _NoHeadersResponse:
    def getcode(self) -> int:
        return 200

    def read(self, amt: int = -1) -> bytes:
        return b"bad"

    def close(self) -> None:
        return None


class _Opener:
    def __init__(self, actions: Sequence[object]) -> None:
        self._actions: Iterator[object] = iter(actions)
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float] = []
        self.last_action: object | None = None

    def open(
        self, request: urllib.request.Request, timeout: float
    ) -> PageTitleResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        action = next(self._actions)
        self.last_action = action
        if isinstance(action, BaseException):
            raise action
        return cast(PageTitleResponse, action)


def _html(title: str) -> bytes:
    return f"<html><head><title>{title}</title></head></html>".encode()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://example.test/path?x=1#frag", "http://example.test/path?x=1"),
        ("https://example.test/", "https://example.test/"),
        ("example.test/path?x=1#frag", "https://example.test/path?x=1"),
    ],
)
def test_http_https_and_bare_host_url_acceptance(raw: str, expected: str) -> None:
    assert normalize_title_lookup_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://example.test/",
        "mailto:user@example.test",
        "https:///missing-host",
        "https://user@example.test/",
        "https://user:pass@example.test/",
        "https://[bad/",
        "http://[]/",
        "[bad",
    ],
)
def test_url_rejection(raw: str) -> None:
    assert normalize_title_lookup_url(raw) is None


def test_fetch_silently_rejects_malformed_urls_before_opening() -> None:
    opener = _Opener([_Response(_html("Should Not Open"))])

    assert fetch_page_title("https://[bad/", opener=opener) is None
    assert opener.requests == []


def test_fetch_uses_fragment_stripped_url() -> None:
    opener = _Opener([_Response(_html("Fragment"))])

    assert fetch_page_title("example.test/path?x=1#secret", opener=opener) == "Fragment"
    assert opener.requests[0].full_url == "https://example.test/path?x=1"


def test_redirect_acceptance_and_five_redirects() -> None:
    responses = [
        _Response(status=302, headers={"Location": f"/step-{index}"})
        for index in range(MAX_REDIRECTS)
    ]
    responses.append(_Response(_html("Final")))
    opener = _Opener(responses)

    assert (
        fetch_page_title("https://example.test/start", opener=opener, timeout=1.25)
        == "Final"
    )
    assert opener.requests[-1].full_url == "https://example.test/step-4"
    assert opener.timeouts == [1.25] * (MAX_REDIRECTS + 1)
    assert all(response.closed for response in responses)


def test_redirect_limit_rejects_sixth_redirect() -> None:
    responses = [
        _Response(status=302, headers={"Location": f"/step-{index}"})
        for index in range(MAX_REDIRECTS + 1)
    ]
    opener = _Opener(responses)

    assert fetch_page_title("https://example.test/start", opener=opener) is None
    assert len(opener.requests) == MAX_REDIRECTS + 1


@pytest.mark.parametrize(
    "location",
    ["ftp://example.test/file", "https://user:pass@example.test/"],
)
def test_redirect_rejection_for_unsupported_scheme_or_credentials(
    location: str,
) -> None:
    opener = _Opener([_Response(status=302, headers={"Location": location})])

    assert fetch_page_title("https://example.test/start", opener=opener) is None
    assert isinstance(opener.last_action, _Response)
    assert opener.last_action.closed


def test_simple_title_extraction() -> None:
    assert extract_title(_html("Example Title")) == "Example Title"


def test_entity_decoding_happens_exactly_once() -> None:
    assert extract_title(_html("AT&amp;amp;T")) == "AT&amp;T"


def test_unicode_preservation_and_whitespace_normalization() -> None:
    body = "<html><title>\n Café\t東京  Test \r\n</title></html>".encode()

    assert extract_title(body) == "Café 東京 Test"


@pytest.mark.parametrize(
    "body",
    [
        b"<html><head></head></html>",
        b"<html><head><title>   </title></head></html>",
        b"<html><head><title></head></html>",
    ],
)
def test_missing_and_malformed_titles(body: bytes) -> None:
    assert extract_title(body) is None


@pytest.mark.parametrize(
    ("headers", "body", "expected"),
    [
        ({"Content-Type": "text/html; charset=utf-8"}, _html("HTML"), "HTML"),
        ({"Content-Type": "application/xhtml+xml"}, _html("XHTML"), "XHTML"),
        ({"Content-Type": ""}, _html("Missing"), "Missing"),
        (
            {"Content-Type": ""},
            b"<?xml version='1.0'?><html><head><title>XML HTML</title></head></html>",
            "XML HTML",
        ),
        ({"Content-Type": "application/json"}, b'{"title": "No"}', None),
        ({"Content-Type": ""}, b"plain text", None),
        (
            {"Content-Type": ""},
            b"<?xml version='1.0'?><rss><title>Feed</title></rss>",
            None,
        ),
        (
            {"Content-Type": ""},
            b"<?xml version='1.0'?><svg><title>Icon</title></svg>",
            None,
        ),
    ],
)
def test_content_type_handling(
    headers: Mapping[str, str], body: bytes, expected: str | None
) -> None:
    opener = _Opener([_Response(body, headers=headers)])

    assert fetch_page_title("https://example.test/", opener=opener) == expected


def test_attachment_response_is_rejected() -> None:
    opener = _Opener(
        [_Response(_html("Download"), headers={"Content-Disposition": "attachment"})]
    )

    assert fetch_page_title("https://example.test/", opener=opener) is None


def test_unsupported_content_encoding_is_rejected() -> None:
    opener = _Opener([_Response(_html("Zip"), headers={"Content-Encoding": "gzip"})])

    assert fetch_page_title("https://example.test/", opener=opener) is None


def test_declared_oversized_response_succeeds_with_title_in_bounded_prefix() -> None:
    response = _Response(
        _html("Large Declared") + (b"x" * MAX_RESPONSE_BYTES),
        headers={"Content-Length": str(MAX_RESPONSE_BYTES + 1)},
    )
    opener = _Opener([response])

    assert fetch_page_title("https://example.test/", opener=opener) == "Large Declared"
    assert 0 < response.bytes_read < len(response._body)
    assert response.closed


def test_actual_oversized_response_succeeds_with_title_in_bounded_prefix() -> None:
    response = _Response(_html("Early Title") + (b"x" * MAX_RESPONSE_BYTES))
    opener = _Opener([response])

    assert fetch_page_title("https://example.test/", opener=opener) == "Early Title"
    assert response.bytes_read < len(response._body)
    assert response.closed


def test_large_response_with_no_title_inside_cap_returns_none() -> None:
    response = _Response((b"x" * MAX_RESPONSE_BYTES) + _html("Too Late"))
    opener = _Opener([response])

    assert fetch_page_title("https://example.test/", opener=opener) is None
    assert response.bytes_read == MAX_RESPONSE_BYTES
    assert response.closed


def test_title_closing_tag_beyond_cap_returns_none() -> None:
    body = b"<html><head><title>" + (b"x" * MAX_RESPONSE_BYTES) + b"</title>"
    response = _Response(body)
    opener = _Opener([response])

    assert fetch_page_title("https://example.test/", opener=opener) is None
    assert response.bytes_read == MAX_RESPONSE_BYTES
    assert response.closed


def test_unclosed_or_chunk_truncated_title_returns_none() -> None:
    response = _Response(b"<html><head><title>Partial")
    opener = _Opener([response])

    assert fetch_page_title("https://example.test/", opener=opener) is None
    assert response.bytes_read == len(response._body)
    assert response.closed


def test_declared_non_html_content_type_rejected_without_reading() -> None:
    response = _Response(
        _html("Ignored"),
        headers={"Content-Type": "application/json"},
    )
    opener = _Opener([response])

    assert fetch_page_title("https://example.test/", opener=opener) is None
    assert response.read_sizes == []
    assert response.closed


def test_empty_and_overlong_titles_are_rejected() -> None:
    assert normalize_title("") is None
    assert normalize_title("x" * (MAX_TITLE_LENGTH + 1)) is None


def test_charset_handling_uses_valid_declared_charset() -> None:
    body = "<html><title>Caf\xe9</title></html>".encode("iso-8859-1")

    assert extract_title(body, "text/html; charset=iso-8859-1") == "Café"


def test_invalid_charset_falls_back_to_utf8_replacement() -> None:
    body = b"<html><title>\xff</title></html>"

    assert extract_title(body, "text/html; charset=not-a-codec") == "�"


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("timeout"),
        urllib.error.HTTPError("https://example.test/", 500, "error", {}, None),
        urllib.error.URLError("http error"),
        http.client.BadStatusLine("bad"),
        socket.timeout("socket timeout"),
        OSError("network"),
    ],
)
def test_network_failures_return_none(failure: BaseException) -> None:
    opener = _Opener([failure])

    assert fetch_page_title("https://example.test/", opener=opener) is None


def test_raised_http_error_closes_response_body() -> None:
    body = _ClosableBody()
    error = urllib.error.HTTPError("https://example.test/", 500, "error", {}, body)
    opener = _Opener([error])

    assert fetch_page_title("https://example.test/", opener=opener) is None
    assert body.closed


def test_http_error_status_and_malformed_response_return_none() -> None:
    assert (
        fetch_page_title(
            "https://example.test/",
            opener=_Opener([_Response(status=404)]),
        )
        is None
    )
    assert (
        fetch_page_title(
            "https://example.test/",
            opener=_Opener([_NoHeadersResponse()]),
        )
        is None
    )


def test_response_closes_on_success_and_failure() -> None:
    success = _Response(_html("Closed"))
    failure = _Response(_html("Closed"), read_error=OSError("read"))

    assert fetch_page_title("https://example.test/", opener=_Opener([success]))
    assert success.closed
    assert fetch_page_title("https://example.test/", opener=_Opener([failure])) is None
    assert failure.closed


def test_forbidden_headers_are_not_sent() -> None:
    opener = _Opener(
        [
            _Response(status=302, headers={"Location": "/next"}),
            _Response(_html("Headers")),
        ]
    )

    assert fetch_page_title("https://example.test/", opener=opener) == "Headers"
    for request in opener.requests:
        sent = {key.lower(): value for key, value in request.header_items()}
        assert sent["user-agent"] == _EXPECTED_USER_AGENT
        assert sent["accept-encoding"] == "identity"
        assert "cookie" not in sent
        assert "authorization" not in sent
        assert "proxy-authorization" not in sent
        assert "referer" not in sent
        assert "origin" not in sent


def test_state_transitions_for_untouched_auto_and_user() -> None:
    state = TitleSuggestionController(is_add_dialog=True)

    request = state.begin_lookup("example.test")
    assert request is not None
    decision = state.apply_result(request.generation, "Example", "")
    assert decision.title == "Example"
    assert state.ownership is NameOwnership.AUTO

    state.name_edited()
    assert state.ownership is NameOwnership.USER
    request = state.begin_lookup("https://example.test/other")
    assert request is None


def test_every_url_change_invalidates_earlier_generation() -> None:
    state = TitleSuggestionController(is_add_dialog=True)
    request = state.begin_lookup("example.test")
    assert request is not None

    change = state.url_changed("")

    assert change.generation != request.generation
    assert state.apply_result(request.generation, "Stale", "").title is None


def test_user_ownership_prevents_application() -> None:
    state = TitleSuggestionController(is_add_dialog=True)
    request = state.begin_lookup("example.test")
    assert request is not None

    state.name_edited()

    assert state.apply_result(request.generation, "Title", "Mine").title is None


def test_auto_replacement_after_url_change() -> None:
    state = TitleSuggestionController(is_add_dialog=True)
    first = state.begin_lookup("example.test")
    assert first is not None
    assert state.apply_result(first.generation, "First", "").title == "First"

    change = state.url_changed("First")
    assert change.clear_name
    second = state.begin_lookup("example.test/next")
    assert second is not None

    assert state.apply_result(second.generation, "Second", "").title == "Second"


def test_deactivation_prevents_late_application() -> None:
    state = TitleSuggestionController(is_add_dialog=True)
    request = state.begin_lookup("example.test")
    assert request is not None

    state.deactivate()

    assert state.apply_result(request.generation, "Late", "").title is None


def test_add_disabled_state_never_begins_lookup() -> None:
    state = TitleSuggestionController(is_add_dialog=False)

    assert state.begin_lookup("https://example.test/") is None
