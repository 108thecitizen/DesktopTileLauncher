# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import ipaddress
import re
import unicodedata
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum

MAX_IMPORT_CANDIDATES = 500
MAX_IMPORT_TEXT_BYTES = 1024 * 1024

_HTTP_SCHEMES = frozenset({"http", "https"})
_KNOWN_UNSUPPORTED_SCHEMES = frozenset({"data", "file", "ftp", "javascript", "mailto"})
_SCHEME_PREFIX = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")
_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_LEGACY_IPV4_LABEL = re.compile(r"(?:0[xX][0-9A-Fa-f]+|[0-9]+)")
_LEGACY_IPV4_TERMINAL_LABEL = re.compile(r"(?:0[xX][0-9A-Fa-f]*|[0-9]+)")
_URL_OTHER_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})


class UrlImportBatchError(str, Enum):
    TEXT_TOO_LARGE = "text_too_large"
    TOO_MANY_CANDIDATES = "too_many_candidates"


class UrlImportStatus(str, Enum):
    READY = "ready"
    INVALID = "invalid"
    DUPLICATE_IN_BATCH = "duplicate_in_batch"
    DUPLICATE_IN_TARGET_TAB = "duplicate_in_target_tab"
    DUPLICATE_ON_OTHER_TAB = "duplicate_on_other_tab"


class UrlInvalidReason(str, Enum):
    CONTROL_CHARACTER = "control_character"
    EMBEDDED_WHITESPACE = "embedded_whitespace"
    UNSUPPORTED_SCHEME = "unsupported_scheme"
    MISSING_HOST = "missing_host"
    CREDENTIALS = "credentials"
    INVALID_HOST = "invalid_host"
    INVALID_PORT = "invalid_port"
    MALFORMED_URL = "malformed_url"


@dataclass(frozen=True)
class UrlImportCandidate:
    source_line: int
    status: UrlImportStatus
    source_text: str = field(repr=False)
    normalized_url: str | None = field(default=None, repr=False)
    fallback_name: str | None = field(default=None, repr=False)
    invalid_reason: UrlInvalidReason | None = None
    duplicate_of_line: int | None = None


@dataclass(frozen=True)
class UrlImportCounts:
    total: int = 0
    ready: int = 0
    invalid: int = 0
    duplicate_in_batch: int = 0
    duplicate_in_target_tab: int = 0
    duplicate_on_other_tab: int = 0


@dataclass(frozen=True)
class UrlImportPlan:
    candidates: tuple[UrlImportCandidate, ...]
    counts: UrlImportCounts
    source_candidate_count: int
    batch_error: UrlImportBatchError | None = None

    @property
    def is_valid_batch(self) -> bool:
        return self.batch_error is None


@dataclass(frozen=True)
class _UrlIdentity:
    scheme: str
    hostname: str
    port: int | None
    path: str
    query: str
    fragment: str
    has_query_delimiter: bool
    has_fragment_delimiter: bool


@dataclass(frozen=True)
class _ValidUrl:
    normalized_url: str = field(repr=False)
    fallback_name: str = field(repr=False)
    identity: _UrlIdentity = field(repr=False)


def plan_url_import(
    text: str,
    *,
    target_tab_urls: Iterable[str] = (),
    other_tab_urls: Iterable[str] = (),
) -> UrlImportPlan:
    """Build an offline, immutable plan for one nonblank URL per input line."""
    if _utf8_size(text) > MAX_IMPORT_TEXT_BYTES:
        return _batch_failure(UrlImportBatchError.TEXT_TOO_LARGE)

    source_rows = tuple(_nonblank_source_rows(text.removeprefix("\ufeff")))
    if len(source_rows) > MAX_IMPORT_CANDIDATES:
        return _batch_failure(
            UrlImportBatchError.TOO_MANY_CANDIDATES,
            source_candidate_count=len(source_rows),
        )

    target_identities = _existing_identities(target_tab_urls)
    other_identities = _existing_identities(other_tab_urls)
    first_batch_lines: dict[_UrlIdentity, int] = {}
    candidates: list[UrlImportCandidate] = []

    for source_line, source_text in source_rows:
        analyzed = _analyze_url(source_text)
        if isinstance(analyzed, UrlInvalidReason):
            candidates.append(
                UrlImportCandidate(
                    source_line=source_line,
                    source_text=source_text,
                    status=UrlImportStatus.INVALID,
                    invalid_reason=analyzed,
                )
            )
            continue

        duplicate_of_line = first_batch_lines.get(analyzed.identity)
        if duplicate_of_line is not None:
            status = UrlImportStatus.DUPLICATE_IN_BATCH
        else:
            first_batch_lines[analyzed.identity] = source_line
            if analyzed.identity in target_identities:
                status = UrlImportStatus.DUPLICATE_IN_TARGET_TAB
            elif analyzed.identity in other_identities:
                status = UrlImportStatus.DUPLICATE_ON_OTHER_TAB
            else:
                status = UrlImportStatus.READY

        candidates.append(
            UrlImportCandidate(
                source_line=source_line,
                source_text=source_text,
                normalized_url=analyzed.normalized_url,
                fallback_name=analyzed.fallback_name,
                status=status,
                duplicate_of_line=duplicate_of_line,
            )
        )

    candidate_tuple = tuple(candidates)
    return UrlImportPlan(
        candidates=candidate_tuple,
        counts=_count_statuses(candidate_tuple),
        source_candidate_count=len(source_rows),
    )


def _batch_failure(
    error: UrlImportBatchError, source_candidate_count: int = 0
) -> UrlImportPlan:
    return UrlImportPlan(
        candidates=(),
        counts=UrlImportCounts(),
        source_candidate_count=source_candidate_count,
        batch_error=error,
    )


def _utf8_size(text: str) -> int:
    return len(text.encode("utf-8", errors="surrogatepass"))


def _nonblank_source_rows(text: str) -> Iterable[tuple[int, str]]:
    for source_line, raw_line in enumerate(text.split("\n"), start=1):
        if raw_line.endswith("\r"):
            raw_line = raw_line[:-1]
        source_text = raw_line.strip()
        if source_text:
            yield source_line, source_text


def _existing_identities(urls: Iterable[str]) -> frozenset[_UrlIdentity]:
    identities: set[_UrlIdentity] = set()
    for url in urls:
        analyzed = _analyze_url(url)
        if isinstance(analyzed, _ValidUrl):
            identities.add(analyzed.identity)
    return frozenset(identities)


def _analyze_url(raw_url: str) -> _ValidUrl | UrlInvalidReason:
    text = raw_url.strip()
    if any(unicodedata.category(char) in _URL_OTHER_CATEGORIES for char in text):
        return UrlInvalidReason.CONTROL_CHARACTER
    if any(char.isspace() for char in text):
        return UrlInvalidReason.EMBEDDED_WHITESPACE
    if "\\" in text or _has_malformed_percent_escape(text):
        return UrlInvalidReason.MALFORMED_URL

    prepared = _with_http_scheme(text)
    if isinstance(prepared, UrlInvalidReason):
        return prepared

    try:
        parsed = urllib.parse.urlsplit(prepared)
    except (UnicodeError, ValueError):
        return UrlInvalidReason.MALFORMED_URL

    if parsed.scheme.lower() not in _HTTP_SCHEMES:
        return UrlInvalidReason.UNSUPPORTED_SCHEME
    try:
        if parsed.username is not None or parsed.password is not None:
            return UrlInvalidReason.CREDENTIALS
        hostname = parsed.hostname
    except (UnicodeError, ValueError):
        return UrlInvalidReason.MALFORMED_URL
    if not hostname:
        return UrlInvalidReason.MISSING_HOST

    port = _validated_port(parsed.netloc)
    if isinstance(port, UrlInvalidReason):
        return port

    if not _is_valid_hostname(hostname, bracketed=parsed.netloc.startswith("[")):
        return UrlInvalidReason.INVALID_HOST

    scheme = parsed.scheme.lower()
    identity_port = None if port == _default_port(scheme) else port
    has_query_delimiter, has_fragment_delimiter = _delimiter_presence(prepared)
    identity = _UrlIdentity(
        scheme=scheme,
        hostname=hostname.lower(),
        port=identity_port,
        path=parsed.path or "/",
        query=parsed.query,
        fragment=parsed.fragment,
        has_query_delimiter=has_query_delimiter,
        has_fragment_delimiter=has_fragment_delimiter,
    )
    return _ValidUrl(
        normalized_url=prepared,
        fallback_name=_fallback_name(parsed.path, hostname),
        identity=identity,
    )


def _with_http_scheme(text: str) -> str | UrlInvalidReason:
    match = _SCHEME_PREFIX.match(text)
    if match is None:
        return f"https://{text}"

    original_scheme = match.group(1)
    scheme = original_scheme.lower()
    if scheme in _HTTP_SCHEMES:
        return text
    if scheme in _KNOWN_UNSUPPORTED_SCHEMES:
        return UrlInvalidReason.UNSUPPORTED_SCHEME
    remainder = text[match.end() :]
    if remainder.startswith("//"):
        return UrlInvalidReason.UNSUPPORTED_SCHEME
    if _looks_like_bare_host_port(original_scheme):
        return f"https://{text}"
    return UrlInvalidReason.UNSUPPORTED_SCHEME


def _looks_like_bare_host_port(host: str) -> bool:
    return "." in host or host.lower() == "localhost"


def _default_port(scheme: str) -> int:
    return 80 if scheme == "http" else 443


def _has_malformed_percent_escape(text: str) -> bool:
    return re.search(r"%(?![0-9A-Fa-f]{2})", text) is not None


def _validated_port(netloc: str) -> int | None | UrlInvalidReason:
    port_text: str | None = None
    if netloc.startswith("["):
        closing_bracket = netloc.find("]")
        if closing_bracket < 0:
            return UrlInvalidReason.INVALID_HOST
        suffix = netloc[closing_bracket + 1 :]
        if suffix:
            if not suffix.startswith(":") or ":" in suffix[1:]:
                return UrlInvalidReason.INVALID_PORT
            port_text = suffix[1:]
    elif ":" in netloc:
        if netloc.count(":") != 1:
            return UrlInvalidReason.INVALID_PORT
        port_text = netloc.rsplit(":", maxsplit=1)[1]

    if port_text is None:
        return None
    if (
        not port_text
        or len(port_text) > 5
        or not port_text.isascii()
        or not port_text.isdecimal()
    ):
        return UrlInvalidReason.INVALID_PORT
    port = int(port_text)
    if not 1 <= port <= 65535:
        return UrlInvalidReason.INVALID_PORT
    return port


def _is_valid_hostname(hostname: str, *, bracketed: bool) -> bool:
    if "%" in hostname:
        return False
    if bracketed:
        try:
            ipaddress.IPv6Address(hostname)
        except ValueError:
            return False
        return True

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        return True

    if ":" in hostname or hostname.isdecimal():
        return False
    if "." in hostname and hostname.replace(".", "").isdigit():
        return False
    if any(separator in hostname for separator in ("\u3002", "\uff0e", "\uff61")):
        return False

    labels = hostname[:-1].split(".") if hostname.endswith(".") else hostname.split(".")
    if not labels:
        return False
    if all(_LEGACY_IPV4_LABEL.fullmatch(label) for label in labels):
        return False
    ascii_labels: list[str] = []
    for label in labels:
        if not _is_valid_unicode_label(label):
            return False
        try:
            ascii_label = label.encode("idna").decode("ascii")
        except UnicodeError:
            return False
        if not _HOST_LABEL.fullmatch(ascii_label):
            return False
        if ascii_label.lower().startswith("xn--"):
            try:
                decoded_label = ascii_label.encode("ascii").decode("idna")
            except UnicodeError:
                return False
            if not _is_valid_unicode_label(decoded_label):
                return False
        ascii_labels.append(ascii_label)
    if all(_LEGACY_IPV4_LABEL.fullmatch(label) for label in ascii_labels):
        return False
    if _LEGACY_IPV4_TERMINAL_LABEL.fullmatch(ascii_labels[-1]):
        return False
    return len(".".join(ascii_labels)) <= 253


def _is_valid_unicode_label(label: str) -> bool:
    if not label:
        return False
    for index, char in enumerate(label):
        if char == "-":
            continue
        if char.isascii():
            if not char.isalnum():
                return False
            continue
        category = unicodedata.category(char)
        if category[0] not in {"L", "M", "N"}:
            return False
        if index == 0 and category.startswith("M"):
            return False
    return True


def _delimiter_presence(url: str) -> tuple[bool, bool]:
    before_fragment, separator, _fragment = url.partition("#")
    return "?" in before_fragment, bool(separator)


def _fallback_name(path: str, hostname: str) -> str:
    for encoded_segment in reversed(path.split("/")):
        if not encoded_segment:
            continue
        try:
            decoded_segment = urllib.parse.unquote(encoded_segment, errors="strict")
        except UnicodeDecodeError:
            continue
        if any(
            unicodedata.category(char) in _URL_OTHER_CATEGORIES
            for char in decoded_segment
        ):
            continue
        cleaned_segment = _clean_name(decoded_segment)
        if _is_meaningful_name(cleaned_segment):
            return cleaned_segment
    return hostname


def _clean_name(text: str) -> str:
    characters = (" " if char.isspace() else char for char in text)
    return " ".join("".join(characters).split())


def _is_meaningful_name(text: str) -> bool:
    if not text or text in {".", ".."}:
        return False
    return any(
        char not in {"/", "\\"} and unicodedata.category(char)[0] not in {"C", "M", "Z"}
        for char in text
    )


def _count_statuses(candidates: tuple[UrlImportCandidate, ...]) -> UrlImportCounts:
    return UrlImportCounts(
        total=len(candidates),
        ready=sum(item.status is UrlImportStatus.READY for item in candidates),
        invalid=sum(item.status is UrlImportStatus.INVALID for item in candidates),
        duplicate_in_batch=sum(
            item.status is UrlImportStatus.DUPLICATE_IN_BATCH for item in candidates
        ),
        duplicate_in_target_tab=sum(
            item.status is UrlImportStatus.DUPLICATE_IN_TARGET_TAB
            for item in candidates
        ),
        duplicate_on_other_tab=sum(
            item.status is UrlImportStatus.DUPLICATE_ON_OTHER_TAB for item in candidates
        ),
    )
