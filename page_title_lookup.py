# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import codecs
import http.client
import socket
import urllib.error
import urllib.parse
import urllib.request
import urllib.response
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import Message
from enum import Enum
from html.parser import HTMLParser
from typing import IO, Protocol, cast

MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 256 * 1024
MAX_TITLE_LENGTH = 512
TITLE_LOOKUP_TIMEOUT_SECONDS = 3.0
_READ_CHUNK_BYTES = 16 * 1024
_USER_AGENT = (
    "DesktopTileLauncher (+https://github.com/108thecitizen/DesktopTileLauncher)"
)

_SAFE_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
    "Accept-Encoding": "identity",
    "User-Agent": _USER_AGENT,
}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
_MALFORMED_URL_ERRORS = (ValueError, UnicodeError)
_NETWORK_ERRORS = (
    OSError,
    TimeoutError,
    http.client.HTTPException,
    urllib.error.URLError,
    ValueError,
    UnicodeError,
    socket.timeout,
)


class PageTitleResponse(Protocol):
    headers: Mapping[str, str]

    def getcode(self) -> int: ...

    def read(self, amt: int = -1) -> bytes: ...

    def close(self) -> None: ...


class PageTitleOpener(Protocol):
    def open(
        self, request: urllib.request.Request, timeout: float
    ) -> PageTitleResponse: ...


class NameOwnership(Enum):
    UNTOUCHED = "untouched"
    AUTO = "auto"
    USER = "user"


@dataclass(frozen=True)
class UrlChangeDecision:
    generation: int
    clear_name: bool


@dataclass(frozen=True)
class LookupRequest:
    generation: int
    url: str


@dataclass(frozen=True)
class TitleApplyDecision:
    title: str | None


@dataclass
class TitleSuggestionController:
    is_add_dialog: bool
    active: bool = True
    generation: int = 0
    ownership: NameOwnership = NameOwnership.UNTOUCHED
    automatic_suggestion: str | None = None

    def deactivate(self) -> None:
        self.active = False
        self.invalidate()

    def invalidate(self) -> int:
        self.generation += 1
        return self.generation

    def name_edited(self) -> None:
        self.ownership = NameOwnership.USER

    def url_changed(self, current_name: str) -> UrlChangeDecision:
        generation = self.invalidate()
        clear_name = False
        if self.ownership is NameOwnership.AUTO:
            if current_name == self.automatic_suggestion:
                clear_name = True
            if clear_name or not current_name:
                self.ownership = NameOwnership.UNTOUCHED
                self.automatic_suggestion = None
        return UrlChangeDecision(generation=generation, clear_name=clear_name)

    def begin_lookup(self, raw_url: str) -> LookupRequest | None:
        if (
            not self.active
            or not self.is_add_dialog
            or self.ownership is NameOwnership.USER
        ):
            return None
        normalized_url = normalize_title_lookup_url(raw_url)
        if normalized_url is None:
            return None
        generation = self.invalidate()
        return LookupRequest(generation=generation, url=normalized_url)

    def apply_result(
        self, generation: int, title: str | None, current_name: str
    ) -> TitleApplyDecision:
        if (
            not self.active
            or not self.is_add_dialog
            or generation != self.generation
            or self.ownership is NameOwnership.USER
        ):
            return TitleApplyDecision(title=None)
        if title is None:
            return TitleApplyDecision(title=None)
        normalized_title = normalize_title(title)
        if normalized_title is None:
            return TitleApplyDecision(title=None)

        may_replace = not current_name or current_name == self.automatic_suggestion
        if not may_replace:
            return TitleApplyDecision(title=None)

        self.ownership = NameOwnership.AUTO
        self.automatic_suggestion = normalized_title
        return TitleApplyDecision(title=normalized_title)


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_title = False
        self._done = False
        self._parts: list[str] = []

    @property
    def title(self) -> str | None:
        if not self._done:
            return None
        return normalize_title("".join(self._parts))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._done and tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if self._in_title and tag.lower() == "title":
            self._in_title = False
            self._done = True

    def handle_data(self, data: str) -> None:
        if self._in_title and not self._done:
            self._parts.append(data)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def http_error_301(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
    ) -> urllib.response.addinfourl:
        return self._return_redirect_response(req, fp, code, headers)

    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301

    @staticmethod
    def _return_redirect_response(
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        headers: http.client.HTTPMessage,
    ) -> urllib.response.addinfourl:
        return urllib.response.addinfourl(fp, headers, req.full_url, code)


def normalize_title_lookup_url(raw_url: str) -> str | None:
    try:
        text = (raw_url or "").strip()
        if not text:
            return None
        parsed = urllib.parse.urlparse(text)
        if not parsed.scheme:
            parsed = urllib.parse.urlparse(f"https://{text}")
        return _validated_url_from_parts(parsed)
    except _MALFORMED_URL_ERRORS:
        return None


def extract_title(html_bytes: bytes, content_type: str | None = None) -> str | None:
    charset = _declared_charset(content_type) or "utf-8"
    try:
        text = html_bytes.decode(charset, errors="replace")
    except LookupError:
        text = html_bytes.decode("utf-8", errors="replace")

    parser = _TitleParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return None
    return parser.title


def normalize_title(title: str) -> str | None:
    normalized = " ".join(title.split())
    if not normalized or len(normalized) > MAX_TITLE_LENGTH:
        return None
    return normalized


def fetch_page_title(
    url: str,
    *,
    opener: PageTitleOpener | None = None,
    timeout: float = TITLE_LOOKUP_TIMEOUT_SECONDS,
) -> str | None:
    current_url = normalize_title_lookup_url(url)
    if current_url is None:
        return None

    active_opener = opener or _build_private_opener()
    redirect_count = 0
    while True:
        try:
            request = urllib.request.Request(
                current_url, headers=_SAFE_REQUEST_HEADERS, method="GET"
            )
        except _MALFORMED_URL_ERRORS:
            return None
        try:
            response = active_opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            exc.close()
            return None
        except _NETWORK_ERRORS:
            return None

        try:
            status = _response_status(response)
            if status in _REDIRECT_STATUSES:
                if redirect_count >= MAX_REDIRECTS:
                    return None
                location = _header(response, "Location")
                next_url = _validated_redirect_url(current_url, location)
                if next_url is None:
                    return None
                redirect_count += 1
                current_url = next_url
                continue
            if status < 200 or status >= 300:
                return None
            return _extract_response_title(response)
        except _NETWORK_ERRORS:
            return None
        finally:
            response.close()


def _build_private_opener() -> PageTitleOpener:
    return cast(
        PageTitleOpener,
        urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        ),
    )


def _validated_redirect_url(base_url: str, location: str | None) -> str | None:
    if not location:
        return None
    try:
        joined = urllib.parse.urljoin(base_url, location)
    except _MALFORMED_URL_ERRORS:
        return None
    return normalize_title_lookup_url(joined)


def _validated_url_from_parts(parsed: urllib.parse.ParseResult) -> str | None:
    try:
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            return None
        hostname = parsed.hostname
        parsed.port
        if not hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        clean = parsed._replace(scheme=scheme, fragment="")
        return urllib.parse.urlunparse(clean)
    except _MALFORMED_URL_ERRORS:
        return None


def _response_status(response: PageTitleResponse) -> int:
    raw_status = getattr(response, "status", None)
    if raw_status is None:
        raw_status = getattr(response, "code", None)
    if raw_status is None:
        raw_status = response.getcode()
    return int(raw_status)


def _extract_response_title(response: PageTitleResponse) -> str | None:
    if _is_attachment(response) or _has_unsupported_encoding(response):
        return None
    raw_content_type = _header(response, "Content-Type")
    content_type = (
        raw_content_type if raw_content_type and raw_content_type.strip() else None
    )
    if content_type and not _is_html_content_type(content_type):
        return None

    body = bytearray()
    should_parse = content_type is not None
    while len(body) < MAX_RESPONSE_BYTES:
        remaining = MAX_RESPONSE_BYTES - len(body)
        chunk = response.read(min(_READ_CHUNK_BYTES, remaining))
        if not chunk:
            return None
        body.extend(chunk)
        if content_type is None and not should_parse:
            if not _looks_html_like(bytes(body)):
                return None
            should_parse = True
        if should_parse:
            title = extract_title(bytes(body), content_type)
            if title is not None:
                return title
    return None


def _may_parse_as_html(content_type: str | None, body: bytes) -> bool:
    if content_type:
        return _is_html_content_type(content_type)
    return _looks_html_like(body)


def _is_html_content_type(content_type: str) -> bool:
    message = _content_type_message(content_type)
    return message.get_content_type().lower() in _HTML_CONTENT_TYPES


def _looks_html_like(body: bytes) -> bool:
    prefix = body[:512].lstrip().lower()
    if prefix.startswith(codecs.BOM_UTF8):
        prefix = prefix[len(codecs.BOM_UTF8) :].lstrip()
    if prefix.startswith(b"<?xml"):
        declaration_end = prefix.find(b"?>")
        if declaration_end < 0:
            return False
        prefix = prefix[declaration_end + 2 :].lstrip()
    return prefix.startswith((b"<!doctype html", b"<html", b"<head", b"<title"))


def _is_attachment(response: PageTitleResponse) -> bool:
    disposition = _header(response, "Content-Disposition")
    if not disposition:
        return False
    return disposition.split(";", 1)[0].strip().lower() == "attachment"


def _has_unsupported_encoding(response: PageTitleResponse) -> bool:
    encoding = _header(response, "Content-Encoding")
    if not encoding:
        return False
    return encoding.strip().lower() != "identity"


def _declared_charset(content_type: str | None) -> str | None:
    if not content_type:
        return None
    charset = _content_type_message(content_type).get_content_charset()
    if not charset:
        return None
    try:
        codecs.lookup(charset)
    except LookupError:
        return None
    return charset


def _content_type_message(content_type: str) -> Message:
    message = Message()
    message["content-type"] = content_type
    return message


def _header(response: PageTitleResponse, name: str) -> str | None:
    headers = getattr(response, "headers", {})
    value = headers.get(name)
    if value is not None:
        return str(value)
    return headers.get(name.lower())
