# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import gc
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import TracebackType

import pytest
import tile_metadata_refresh

from tile_metadata_refresh import (
    LookupStatus,
    OpaqueToken,
    OperationGuard,
    RefreshResult,
    ResolvedMetadata,
    TileSnapshot,
    create_batch_staging_directory,
    fetch_favicon,
    guess_domain,
    merge_refresh_result,
    run_metadata_refresh,
    select_all_for_active_tab,
    snapshot_matches,
    summarize_refresh_results,
)

pytestmark = pytest.mark.unit


class _FaviconResponse:
    def __init__(self, body: bytes = b"png-data") -> None:
        self.body = body
        self.closed = False

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> _FaviconResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.closed = True


class _FaviconOpener:
    def __init__(
        self,
        response: _FaviconResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or _FaviconResponse()
        self.error = error
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, *, timeout: float) -> _FaviconResponse:
        self.calls.append((url, timeout))
        if self.error is not None:
            raise self.error
        return self.response


def _snapshot(
    token: OpaqueToken | None = None,
    *,
    url: str = "https://example.test/current?token=private",
    name: str = "Old custom name",
    tab: str = "Main",
    icon: str | None = "C:/private/old-icon.png",
) -> TileSnapshot:
    return TileSnapshot(
        token=token or OpaqueToken(),
        url=url,
        name=name,
        tab=tab,
        icon=icon,
        bg="#123456",
        browser="chrome",
        chrome_profile="Profile 1",
        open_target="window",
    )


def _write_icon(url: str, *, output_directory: Path) -> Path:
    del url
    icon = output_directory / "icon.png"
    icon.write_bytes(b"icon")
    return icon


def test_sensitive_records_are_immutable_and_repr_safe(tmp_path: Path) -> None:
    snapshot = _snapshot()
    metadata = ResolvedMetadata(
        title_status=LookupStatus.SUCCESS,
        favicon_status=LookupStatus.SUCCESS,
        title="Secret fetched title",
        icon_path=tmp_path / "private-icon.png",
    )
    result = RefreshResult(
        token=snapshot.token,
        metadata=metadata,
        staging_directory=tmp_path / "secret-batch",
    )

    rendered = repr((snapshot, metadata, result))

    for secret in (
        snapshot.url,
        snapshot.name,
        snapshot.tab,
        snapshot.icon,
        "Secret fetched title",
        "private-icon.png",
        "secret-batch",
    ):
        assert secret is not None
        assert secret not in rendered
    with pytest.raises(FrozenInstanceError):
        snapshot.name = "mutated"  # type: ignore[misc]


def test_active_tab_select_all_keeps_value_equal_tiles_distinct() -> None:
    first = _snapshot(OpaqueToken(), url="https://example.test/same")
    second = _snapshot(OpaqueToken(), url="https://example.test/same")
    other = _snapshot(OpaqueToken(), tab="Other")

    selected = select_all_for_active_tab((first, second, other), "Main")

    assert len(selected) == 2
    assert selected[0] is first.token
    assert selected[1] is second.token


def test_active_tab_select_all_deduplicates_the_same_identity() -> None:
    snapshot = _snapshot()

    assert select_all_for_active_tab((snapshot, snapshot), "Main") == (snapshot.token,)


def test_operation_guard_rejects_duplicate_and_stale_tokens() -> None:
    guard = OperationGuard()
    first = OpaqueToken()
    second = OpaqueToken()
    first_reference = weakref.ref(first)

    assert guard.start(first)
    assert guard.active
    assert guard.is_current(first)
    assert not guard.start(first)
    assert not guard.start(second)
    assert not guard.finish(second)
    assert guard.finish(first)
    assert not guard.is_current(first)
    assert not guard.start(first)
    del first
    gc.collect()
    assert first_reference() is None
    assert guard.start(second)
    guard.invalidate()
    assert not guard.finish(second)
    assert not guard.active
    assert not guard.start(second)


@pytest.mark.parametrize(
    ("field_name", "replacement_value"),
    [
        pytest.param("token", OpaqueToken(), id="token"),
        pytest.param("url", "https://example.test/new", id="url"),
        pytest.param("name", "Changed name", id="name"),
        pytest.param("tab", "Other", id="tab"),
        pytest.param("icon", None, id="icon"),
        pytest.param("bg", "#654321", id="background"),
        pytest.param("browser", None, id="browser"),
        pytest.param("chrome_profile", None, id="chrome-profile"),
        pytest.param("open_target", "tab", id="open-target"),
    ],
)
def test_snapshot_matching_requires_identity_and_every_field(
    field_name: str,
    replacement_value: object,
) -> None:
    original = _snapshot()

    assert snapshot_matches(original, replace(original))
    assert not snapshot_matches(
        original,
        replace(original, **{field_name: replacement_value}),
    )


def test_favicon_request_preserves_google_domain_size_and_timeout(
    tmp_path: Path,
) -> None:
    response = _FaviconResponse()
    opener = _FaviconOpener(response)

    icon = fetch_favicon(
        "https://user@example.test/path",
        output_directory=tmp_path,
        opener=opener,
    )

    assert guess_domain("https://user@example.test:8443/path") == "example.test:8443"
    assert icon == tmp_path / "example.test_128.png"
    assert icon.read_bytes() == b"png-data"
    assert opener.calls == [
        (
            "https://www.google.com/s2/favicons?domain=example.test&sz=128",
            5.0,
        )
    ]
    assert response.closed


def test_favicon_failure_is_silent_and_does_not_create_a_file(
    tmp_path: Path,
) -> None:
    opener = _FaviconOpener(error=OSError("sensitive failure"))

    assert (
        fetch_favicon(
            "https://example.test/private",
            output_directory=tmp_path,
            opener=opener,
        )
        is None
    )
    assert tuple(tmp_path.iterdir()) == ()


def test_favicon_explicit_size_controls_request_and_filename(tmp_path: Path) -> None:
    opener = _FaviconOpener()

    icon = fetch_favicon(
        "https://example.test/path",
        output_directory=tmp_path,
        size=64,
        opener=opener,
    )

    assert icon == tmp_path / "example.test_64.png"
    assert opener.calls == [
        (
            "https://www.google.com/s2/favicons?domain=example.test&sz=64",
            5.0,
        )
    ]


@pytest.mark.parametrize(
    ("title_succeeds", "favicon_succeeds", "expected_name", "icon_changed"),
    [
        (True, True, "Fetched title", True),
        (True, False, "Fetched title", False),
        (False, True, "Old custom name", True),
        (False, False, "Old custom name", False),
    ],
)
def test_independent_results_and_exact_old_field_retention(
    tmp_path: Path,
    title_succeeds: bool,
    favicon_succeeds: bool,
    expected_name: str,
    icon_changed: bool,
) -> None:
    snapshot = _snapshot()
    seen_titles: list[str] = []
    seen_favicons: list[str] = []

    def title_provider(url: str) -> str | None:
        seen_titles.append(url)
        return "Fetched title" if title_succeeds else None

    def favicon_provider(url: str, *, output_directory: Path) -> Path | None:
        seen_favicons.append(url)
        if not favicon_succeeds:
            return None
        return _write_icon(url, output_directory=output_directory)

    results = run_metadata_refresh(
        (snapshot,),
        output_directory=tmp_path / "batch",
        title_provider=title_provider,
        favicon_provider=favicon_provider,
    )
    merged = merge_refresh_result(snapshot, results[0])

    assert seen_titles == [snapshot.url]
    assert seen_favicons == [snapshot.url]
    assert merged.name == expected_name
    assert merged.name_changed is title_succeeds
    assert merged.icon_changed is icon_changed
    if favicon_succeeds:
        assert merged.icon != snapshot.icon
    else:
        assert merged.icon == "C:/private/old-icon.png"
    assert snapshot.name == "Old custom name"
    assert snapshot.icon == "C:/private/old-icon.png"


def test_both_providers_are_attempted_when_either_raises(tmp_path: Path) -> None:
    snapshots = (
        _snapshot(url="https://example.test/title-error"),
        _snapshot(url="https://example.test/favicon-error"),
    )
    title_calls: list[str] = []
    favicon_calls: list[str] = []

    def title_provider(url: str) -> str | None:
        title_calls.append(url)
        if url.endswith("title-error"):
            raise RuntimeError("private title failure")
        return "Title"

    def favicon_provider(url: str, *, output_directory: Path) -> Path:
        favicon_calls.append(url)
        if url.endswith("favicon-error"):
            raise ValueError("private favicon failure")
        return _write_icon(url, output_directory=output_directory)

    results = run_metadata_refresh(
        snapshots,
        output_directory=tmp_path / "batch",
        title_provider=title_provider,
        favicon_provider=favicon_provider,
    )

    expected_urls = {snapshot.url for snapshot in snapshots}
    assert set(title_calls) == expected_urls
    assert set(favicon_calls) == expected_urls
    assert len(title_calls) == len(snapshots)
    assert len(favicon_calls) == len(snapshots)
    assert results[0].metadata.title_status is LookupStatus.ERROR
    assert results[0].metadata.favicon_status is LookupStatus.SUCCESS
    assert results[1].metadata.title_status is LookupStatus.SUCCESS
    assert results[1].metadata.favicon_status is LookupStatus.ERROR

    title_error_merge = merge_refresh_result(snapshots[0], results[0])
    assert title_error_merge.name == snapshots[0].name
    assert not title_error_merge.name_changed
    assert title_error_merge.icon != snapshots[0].icon
    assert title_error_merge.icon_changed

    favicon_error_merge = merge_refresh_result(snapshots[1], results[1])
    assert favicon_error_merge.name == "Title"
    assert favicon_error_merge.name_changed
    assert favicon_error_merge.icon == snapshots[1].icon
    assert not favicon_error_merge.icon_changed


def test_results_preserve_input_order_with_distinct_collision_safe_directories(
    tmp_path: Path,
) -> None:
    snapshots = tuple(_snapshot(url="https://example.test/same") for _ in range(3))
    live_directory = tmp_path / "live"
    live_directory.mkdir()
    live_icon = live_directory / "example.test_128.png"
    live_icon.write_bytes(b"live")

    def favicon_provider(url: str, *, output_directory: Path) -> Path:
        return fetch_favicon(
            url,
            output_directory=output_directory,
            opener=_FaviconOpener(),
        ) or Path("missing")

    results = run_metadata_refresh(
        snapshots,
        output_directory=tmp_path / "batch",
        title_provider=lambda _url: None,
        favicon_provider=favicon_provider,
    )

    assert [result.token for result in results] == [
        snapshot.token for snapshot in snapshots
    ]
    directories = [result.staging_directory for result in results]
    assert all(directory is not None for directory in directories)
    assert len(set(directories)) == len(snapshots)
    assert live_icon.read_bytes() == b"live"
    assert all(
        result.metadata.icon_path is not None
        and result.metadata.icon_path.parent == result.staging_directory
        for result in results
    )


def test_default_concurrency_passes_four_workers_to_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_executor = tile_metadata_refresh.ThreadPoolExecutor
    worker_counts: list[int] = []

    def executor_spy(*, max_workers: int) -> ThreadPoolExecutor:
        worker_counts.append(max_workers)
        return real_executor(max_workers=max_workers)

    monkeypatch.setattr(tile_metadata_refresh, "ThreadPoolExecutor", executor_spy)

    run_metadata_refresh(
        (_snapshot(),),
        output_directory=tmp_path / "batch",
        title_provider=lambda _url: None,
        favicon_provider=lambda _url, *, output_directory: None,
    )

    assert worker_counts == [4]


@pytest.mark.parametrize("workers", [0, 5, True, 1.5, float("nan")])
def test_worker_count_must_be_between_one_and_four(
    tmp_path: Path, workers: int
) -> None:
    with pytest.raises(ValueError, match="1 through 4"):
        run_metadata_refresh((), output_directory=tmp_path, max_workers=workers)


def test_precancelled_batch_runs_no_provider_and_creates_no_staging(
    tmp_path: Path,
) -> None:
    cancellation = threading.Event()
    cancellation.set()
    calls: list[str] = []
    batch = tmp_path / "batch"

    def title_provider(url: str) -> None:
        calls.append(url)
        return None

    results = run_metadata_refresh(
        (_snapshot(),),
        output_directory=batch,
        title_provider=title_provider,
        favicon_provider=lambda _url, *, output_directory: None,
        cancellation=cancellation,
    )

    assert calls == []
    assert not batch.exists()
    assert results[0].cancelled


def test_cancellation_between_providers_prevents_favicon_lookup(
    tmp_path: Path,
) -> None:
    cancellation = threading.Event()
    favicon_calls: list[str] = []

    def title_provider(_url: str) -> str:
        cancellation.set()
        return "Discarded title"

    def favicon_provider(url: str, *, output_directory: Path) -> None:
        del output_directory
        favicon_calls.append(url)
        return None

    result = run_metadata_refresh(
        (_snapshot(),),
        output_directory=tmp_path / "batch",
        title_provider=title_provider,
        favicon_provider=favicon_provider,
        cancellation=cancellation,
    )[0]

    assert favicon_calls == []
    assert result.cancelled
    assert result.metadata.favicon_status is LookupStatus.CANCELLED


def test_cancellation_during_staging_failure_starts_no_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancellation = threading.Event()
    calls: list[str] = []

    def fail_staging(*, prefix: str, dir: Path) -> str:
        del prefix, dir
        cancellation.set()
        raise OSError("private staging failure")

    def title_provider(url: str) -> None:
        calls.append(url)
        return None

    monkeypatch.setattr(
        "tile_metadata_refresh.tempfile.mkdtemp",
        fail_staging,
    )
    result = run_metadata_refresh(
        (_snapshot(),),
        output_directory=tmp_path / "batch",
        title_provider=title_provider,
        favicon_provider=lambda _url, *, output_directory: None,
        cancellation=cancellation,
    )[0]

    assert calls == []
    assert result.cancelled


def test_duplicate_snapshot_token_is_rejected(tmp_path: Path) -> None:
    first = _snapshot()
    duplicate = replace(first, url="https://example.test/other")

    with pytest.raises(ValueError, match="duplicate token"):
        run_metadata_refresh((first, duplicate), output_directory=tmp_path / "batch")


def test_provider_cannot_return_an_icon_outside_its_target_directory(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "live-icon.png"
    outside.write_bytes(b"live")

    result = run_metadata_refresh(
        (_snapshot(),),
        output_directory=tmp_path / "batch",
        title_provider=lambda _url: None,
        favicon_provider=lambda _url, *, output_directory: outside,
    )[0]

    assert result.metadata.favicon_status is LookupStatus.ERROR
    assert result.metadata.icon_path is None
    assert result.metadata.favicon_error_type == "UnsafeStagingPath"


def test_provider_symlink_is_rejected_before_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_path = Path("provider-returned-symlink.png")
    original_is_symlink = Path.is_symlink
    original_resolve = Path.resolve
    provider_path_resolved = False

    def is_symlink(path: Path) -> bool:
        if path == provider_path:
            return True
        return original_is_symlink(path)

    def resolve(path: Path, strict: bool = False) -> Path:
        nonlocal provider_path_resolved
        if path == provider_path:
            provider_path_resolved = True
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    monkeypatch.setattr(Path, "resolve", resolve)

    result = run_metadata_refresh(
        (_snapshot(),),
        output_directory=tmp_path / "batch",
        title_provider=lambda _url: None,
        favicon_provider=lambda _url, *, output_directory: provider_path,
    )[0]

    assert result.metadata.favicon_status is LookupStatus.ERROR
    assert result.metadata.icon_path is None
    assert result.metadata.favicon_error_type == "UnsafeStagingPath"
    assert not provider_path_resolved


def test_merge_rejects_a_result_for_another_identity(tmp_path: Path) -> None:
    snapshot = _snapshot()
    result = RefreshResult(
        token=OpaqueToken(),
        metadata=ResolvedMetadata(
            title_status=LookupStatus.SUCCESS,
            favicon_status=LookupStatus.SUCCESS,
            title="Other",
            icon_path=tmp_path / "other.png",
        ),
    )

    with pytest.raises(ValueError, match="token does not match"):
        merge_refresh_result(snapshot, result)


def test_diagnostics_contain_only_aggregate_counts_and_exception_types(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()

    def title_provider(_url: str) -> str:
        raise RuntimeError("secret title message")

    def favicon_provider(_url: str, *, output_directory: Path) -> Path:
        del output_directory
        raise ValueError("secret favicon message")

    result = run_metadata_refresh(
        (snapshot,),
        output_directory=tmp_path / "batch",
        title_provider=title_provider,
        favicon_provider=favicon_provider,
    )[0]
    diagnostics = summarize_refresh_results((result,))
    rendered = repr(diagnostics)

    assert diagnostics.title_errors == 1
    assert diagnostics.favicon_errors == 1
    assert diagnostics.exception_types == (("RuntimeError", 1), ("ValueError", 1))
    for secret in (
        snapshot.url,
        snapshot.name,
        "secret title message",
        "secret favicon message",
    ):
        assert secret not in rendered


def test_batch_staging_directories_are_unique_and_repository_scoped(
    tmp_path: Path,
) -> None:
    first = create_batch_staging_directory(tmp_path)
    second = create_batch_staging_directory(tmp_path)

    assert first != second
    assert first.parent == tmp_path
    assert second.parent == tmp_path
    assert first.is_dir()
    assert second.is_dir()
