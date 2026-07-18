# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import tempfile
import urllib.parse
import urllib.request
import weakref
from collections import Counter
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Literal, Protocol, cast

from page_title_lookup import fetch_page_title

DEFAULT_FAVICON_SIZE = 128
FAVICON_TIMEOUT_SECONDS = 5.0
MAX_REFRESH_WORKERS = 4


class OpaqueToken:
    """An identity-only token with a deliberately non-descriptive repr."""

    __slots__ = ("__weakref__",)

    def __repr__(self) -> str:
        return "<opaque-token>"


@dataclass(frozen=True, eq=False)
class TileSnapshot:
    """Immutable tile state captured before any refresh work starts."""

    token: OpaqueToken
    url: str = field(repr=False)
    name: str = field(repr=False)
    tab: str = field(default="Main", repr=False)
    icon: str | None = field(default=None, repr=False)
    bg: str = field(default="#F5F6FA", repr=False)
    browser: str | None = field(default=None, repr=False)
    chrome_profile: str | None = field(default=None, repr=False)
    open_target: Literal["tab", "window"] = field(default="tab", repr=False)


class LookupStatus(Enum):
    SUCCESS = "success"
    NO_RESULT = "no_result"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ResolvedMetadata:
    """Provider outcomes without exception messages or other diagnostic payloads."""

    title_status: LookupStatus
    favicon_status: LookupStatus
    title: str | None = field(default=None, repr=False)
    icon_path: Path | None = field(default=None, repr=False)
    title_error_type: str | None = None
    favicon_error_type: str | None = None


@dataclass(frozen=True, eq=False)
class RefreshResult:
    """Metadata result associated with one snapshot by object identity."""

    token: OpaqueToken
    metadata: ResolvedMetadata
    staging_directory: Path | None = field(default=None, repr=False)
    cancelled: bool = False


@dataclass(frozen=True)
class MergedMetadata:
    """Values to apply to a detached tile plus precise change information."""

    name: str = field(repr=False)
    icon: str | None = field(repr=False)
    name_changed: bool
    icon_changed: bool

    @property
    def changed(self) -> bool:
        return self.name_changed or self.icon_changed


@dataclass(frozen=True)
class RefreshDiagnostics:
    """Aggregate-only information safe for diagnostics and breadcrumbs."""

    tile_count: int
    title_successes: int
    favicon_successes: int
    title_no_results: int
    favicon_no_results: int
    title_errors: int
    favicon_errors: int
    cancelled_results: int
    exception_types: tuple[tuple[str, int], ...]


class CancellationFlag(Protocol):
    def is_set(self) -> bool: ...


class TitleProvider(Protocol):
    def __call__(self, url: str) -> str | None: ...


class FaviconProvider(Protocol):
    def __call__(self, url: str, *, output_directory: Path) -> Path | None: ...


class FaviconResponse(Protocol):
    def read(self) -> bytes: ...

    def __enter__(self) -> FaviconResponse: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class FaviconOpener(Protocol):
    def __call__(self, url: str, *, timeout: float) -> FaviconResponse: ...


class OperationGuard:
    """Accept one operation at a time and retire tokens by object identity."""

    def __init__(self) -> None:
        self._active: OpaqueToken | None = None
        self._retired_or_active: weakref.WeakSet[OpaqueToken] = weakref.WeakSet()

    @property
    def active(self) -> bool:
        return self._active is not None

    def start(self, token: OpaqueToken) -> bool:
        if self._active is not None or token in self._retired_or_active:
            return False
        self._active = token
        self._retired_or_active.add(token)
        return True

    def is_current(self, token: OpaqueToken) -> bool:
        return self._active is token

    def finish(self, token: OpaqueToken) -> bool:
        if self._active is not token:
            return False
        self._active = None
        return True

    def invalidate(self) -> None:
        self._active = None


def select_all_for_active_tab(
    snapshots: Iterable[TileSnapshot], active_tab: str
) -> tuple[OpaqueToken, ...]:
    """Return each distinct token on the active tab, preserving input order."""

    selected: list[OpaqueToken] = []
    seen: set[OpaqueToken] = set()
    for snapshot in snapshots:
        if snapshot.tab == active_tab and snapshot.token not in seen:
            selected.append(snapshot.token)
            seen.add(snapshot.token)
    return tuple(selected)


def snapshot_matches(expected: TileSnapshot, current: TileSnapshot) -> bool:
    """Compare captured fields only after confirming token identity."""

    return (
        expected.token is current.token
        and expected.url == current.url
        and expected.name == current.name
        and expected.tab == current.tab
        and expected.icon == current.icon
        and expected.bg == current.bg
        and expected.browser == current.browser
        and expected.chrome_profile == current.chrome_profile
        and expected.open_target == current.open_target
    )


def guess_domain(url: str) -> str:
    """Preserve the launcher's existing netloc-based favicon domain rule."""

    try:
        netloc = urllib.parse.urlparse(url).netloc
        return netloc.split("@")[-1]
    except Exception:  # nosec B110 - existing silent-failure behavior
        return ""


def fetch_favicon(
    url: str,
    *,
    output_directory: Path,
    size: int = DEFAULT_FAVICON_SIZE,
    timeout: float = FAVICON_TIMEOUT_SECONDS,
    opener: FaviconOpener | None = None,
) -> Path | None:
    """Save a Google S2 favicon into an explicitly supplied directory."""

    domain = guess_domain(url)
    if not domain:
        return None

    output_path = _contained_favicon_path(output_directory, domain, size)
    if output_path is None:
        return None

    source_url = f"https://www.google.com/s2/favicons?domain={domain}&sz={size}"
    active_opener = opener or _default_favicon_opener
    try:
        with active_opener(source_url, timeout=timeout) as response:
            with output_path.open("wb") as output_file:
                output_file.write(response.read())
        return output_path
    except Exception:  # nosec B110 - preserve optional lookup's silent failure
        return None


def create_batch_staging_directory(managed_icon_directory: Path) -> Path:
    """Create one uniquely owned refresh directory under managed icon storage."""

    return Path(tempfile.mkdtemp(prefix="refresh-", dir=managed_icon_directory))


def run_metadata_refresh(
    snapshots: Sequence[TileSnapshot],
    *,
    output_directory: Path,
    title_provider: TitleProvider = fetch_page_title,
    favicon_provider: FaviconProvider = fetch_favicon,
    cancellation: CancellationFlag | None = None,
    max_workers: int = MAX_REFRESH_WORKERS,
) -> tuple[RefreshResult, ...]:
    """Resolve metadata off-thread with bounded, per-tile concurrency."""

    _validate_max_workers(max_workers)
    items = tuple(snapshots)
    _validate_unique_tokens(items)
    if not items:
        return ()
    if _is_cancelled(cancellation):
        return tuple(_cancelled_result(snapshot) for snapshot in items)

    output_directory.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                _resolve_one,
                index,
                snapshot,
                output_directory,
                title_provider,
                favicon_provider,
                cancellation,
            )
            for index, snapshot in enumerate(items)
        ]
        return tuple(future.result() for future in futures)


def merge_refresh_result(
    snapshot: TileSnapshot, result: RefreshResult
) -> MergedMetadata:
    """Resolve independent provider failures without mutating the snapshot."""

    if snapshot.token is not result.token:
        raise ValueError("refresh result token does not match snapshot")

    metadata = result.metadata
    refreshed_name = snapshot.name
    refreshed_icon = snapshot.icon
    if not result.cancelled:
        if metadata.title_status is LookupStatus.SUCCESS and metadata.title is not None:
            refreshed_name = metadata.title
        if (
            metadata.favicon_status is LookupStatus.SUCCESS
            and metadata.icon_path is not None
        ):
            refreshed_icon = str(metadata.icon_path)

    return MergedMetadata(
        name=refreshed_name,
        icon=refreshed_icon,
        name_changed=refreshed_name != snapshot.name,
        icon_changed=refreshed_icon != snapshot.icon,
    )


def summarize_refresh_results(
    results: Iterable[RefreshResult],
) -> RefreshDiagnostics:
    """Build an aggregate summary that cannot disclose tile metadata."""

    items = tuple(results)
    error_types: Counter[str] = Counter()
    for result in items:
        metadata = result.metadata
        if metadata.title_error_type is not None:
            error_types[metadata.title_error_type] += 1
        if metadata.favicon_error_type is not None:
            error_types[metadata.favicon_error_type] += 1

    return RefreshDiagnostics(
        tile_count=len(items),
        title_successes=_count_status(items, "title_status", LookupStatus.SUCCESS),
        favicon_successes=_count_status(items, "favicon_status", LookupStatus.SUCCESS),
        title_no_results=_count_status(items, "title_status", LookupStatus.NO_RESULT),
        favicon_no_results=_count_status(
            items, "favicon_status", LookupStatus.NO_RESULT
        ),
        title_errors=_count_status(items, "title_status", LookupStatus.ERROR),
        favicon_errors=_count_status(items, "favicon_status", LookupStatus.ERROR),
        cancelled_results=sum(result.cancelled for result in items),
        exception_types=tuple(sorted(error_types.items())),
    )


def _default_favicon_opener(url: str, *, timeout: float) -> FaviconResponse:
    return cast(
        FaviconResponse,
        urllib.request.urlopen(url, timeout=timeout),  # nosec B310
    )


def _contained_favicon_path(
    output_directory: Path, domain: str, size: int
) -> Path | None:
    try:
        resolved_directory = output_directory.resolve()
        output_path = (resolved_directory / f"{domain}_{size}.png").resolve()
    except (OSError, RuntimeError):
        return None
    if output_path.parent != resolved_directory:
        return None
    return output_path


def _validate_max_workers(max_workers: int) -> None:
    if type(max_workers) is not int or not 1 <= max_workers <= MAX_REFRESH_WORKERS:
        raise ValueError("max_workers must be an integer from 1 through 4")


def _validate_unique_tokens(snapshots: Sequence[TileSnapshot]) -> None:
    seen: set[OpaqueToken] = set()
    for snapshot in snapshots:
        if snapshot.token in seen:
            raise ValueError("refresh snapshots contain a duplicate token")
        seen.add(snapshot.token)


def _resolve_one(
    index: int,
    snapshot: TileSnapshot,
    batch_directory: Path,
    title_provider: TitleProvider,
    favicon_provider: FaviconProvider,
    cancellation: CancellationFlag | None,
) -> RefreshResult:
    if _is_cancelled(cancellation):
        return _cancelled_result(snapshot)

    try:
        target_directory = Path(
            tempfile.mkdtemp(prefix=f"tile-{index:04d}-", dir=batch_directory)
        )
    except OSError as exc:
        if _is_cancelled(cancellation):
            return _cancelled_result(snapshot)
        title_status, title, title_error = _resolve_title(snapshot.url, title_provider)
        return RefreshResult(
            token=snapshot.token,
            metadata=ResolvedMetadata(
                title_status=title_status,
                favicon_status=LookupStatus.ERROR,
                title=title,
                title_error_type=title_error,
                favicon_error_type=_exception_type(exc),
            ),
            cancelled=_is_cancelled(cancellation),
        )

    if _is_cancelled(cancellation):
        return _cancelled_result(snapshot, target_directory)

    title_status, title, title_error = _resolve_title(snapshot.url, title_provider)
    if _is_cancelled(cancellation):
        return RefreshResult(
            token=snapshot.token,
            metadata=ResolvedMetadata(
                title_status=title_status,
                favicon_status=LookupStatus.CANCELLED,
                title=title,
                title_error_type=title_error,
            ),
            staging_directory=target_directory,
            cancelled=True,
        )

    favicon_status, icon_path, favicon_error = _resolve_favicon(
        snapshot.url, target_directory, favicon_provider
    )
    return RefreshResult(
        token=snapshot.token,
        metadata=ResolvedMetadata(
            title_status=title_status,
            favicon_status=favicon_status,
            title=title,
            icon_path=icon_path,
            title_error_type=title_error,
            favicon_error_type=favicon_error,
        ),
        staging_directory=target_directory,
        cancelled=_is_cancelled(cancellation),
    )


def _resolve_title(
    url: str, provider: TitleProvider
) -> tuple[LookupStatus, str | None, str | None]:
    try:
        title = provider(url)
    except Exception as exc:
        return LookupStatus.ERROR, None, _exception_type(exc)
    if title is None:
        return LookupStatus.NO_RESULT, None, None
    return LookupStatus.SUCCESS, title, None


def _resolve_favicon(
    url: str,
    target_directory: Path,
    provider: FaviconProvider,
) -> tuple[LookupStatus, Path | None, str | None]:
    try:
        icon_path = provider(url, output_directory=target_directory)
    except Exception as exc:
        return LookupStatus.ERROR, None, _exception_type(exc)
    if icon_path is None:
        return LookupStatus.NO_RESULT, None, None
    try:
        if icon_path.is_symlink():
            return LookupStatus.ERROR, None, "UnsafeStagingPath"
        resolved_icon = icon_path.resolve()
        resolved_target = target_directory.resolve()
    except (OSError, RuntimeError) as exc:
        return LookupStatus.ERROR, None, _exception_type(exc)
    if resolved_icon.parent != resolved_target or not resolved_icon.is_file():
        return LookupStatus.ERROR, None, "UnsafeStagingPath"
    return LookupStatus.SUCCESS, resolved_icon, None


def _cancelled_result(
    snapshot: TileSnapshot, staging_directory: Path | None = None
) -> RefreshResult:
    return RefreshResult(
        token=snapshot.token,
        metadata=ResolvedMetadata(
            title_status=LookupStatus.CANCELLED,
            favicon_status=LookupStatus.CANCELLED,
        ),
        staging_directory=staging_directory,
        cancelled=True,
    )


def _is_cancelled(cancellation: CancellationFlag | None) -> bool:
    return cancellation is not None and cancellation.is_set()


def _exception_type(exc: Exception) -> str:
    return type(exc).__name__ or "Exception"


def _count_status(
    results: Sequence[RefreshResult],
    attribute: Literal["title_status", "favicon_status"],
    expected: LookupStatus,
) -> int:
    return sum(getattr(result.metadata, attribute) is expected for result in results)
