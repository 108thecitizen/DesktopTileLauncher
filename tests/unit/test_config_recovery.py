# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import re
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import NoReturn

import pytest

import config_recovery
from config_recovery import (
    MAX_CONFIG_BYTES,
    ConfigLoaded,
    ConfigLoadFailureCategory,
    ConfigMissing,
    ConfigRecoveryRequired,
    ConfigurationLoadError,
    LegacyConstructionFailure,
    RecoveryFailed,
    RecoveryFailureCategory,
    RecoverySucceeded,
    load_config,
    preserve_and_reset,
    recovery_exit_diagnostics,
    recovery_required_diagnostics,
    recovery_result_diagnostics,
    validate_legacy_mapping,
)

pytestmark = pytest.mark.unit

_RESET_TEXT = '{"title": "Reset"}'
_CORRUPT_BYTES = b'{"private":"https://example.test/?token=sample"'


def _identity(mapping: dict[str, object]) -> dict[str, object]:
    return mapping


def _rejected(
    path: Path,
    *,
    max_bytes: int = MAX_CONFIG_BYTES,
) -> ConfigRecoveryRequired:
    result = load_config(path, _identity, max_bytes=max_bytes)
    assert isinstance(result, ConfigRecoveryRequired)  # nosec B101
    return result


def _recovery_files(config_path: Path) -> list[Path]:
    recovery_directory = config_path.parent / "recovery"
    if not recovery_directory.exists():
        return []
    return sorted(recovery_directory.glob("config-*.recovery"))


def _fail_source_close_after_real_close(
    monkeypatch: pytest.MonkeyPatch,
) -> list[int]:
    real_open_matching_source = config_recovery._open_matching_source
    real_close = config_recovery.os.close
    source_descriptors: set[int] = set()
    failed_descriptors: list[int] = []

    def track_source(
        path: Path,
        snapshot: config_recovery.SourceSnapshot,
    ) -> config_recovery._OpenedSource:
        source = real_open_matching_source(path, snapshot)
        source_descriptors.add(source.descriptor)
        return source

    def close_then_fail(descriptor: int) -> None:
        real_close(descriptor)
        if descriptor in source_descriptors:
            source_descriptors.remove(descriptor)
            failed_descriptors.append(descriptor)
            raise OSError("synthetic source close failure")

    monkeypatch.setattr(config_recovery, "_open_matching_source", track_source)
    monkeypatch.setattr(config_recovery.os, "close", close_then_fail)
    return failed_descriptors


def test_missing_configuration_returns_missing_without_mutation(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    constructor_called = False

    def constructor(_mapping: dict[str, object]) -> object:
        nonlocal constructor_called
        constructor_called = True
        return object()

    result = load_config(config_path, constructor)

    assert isinstance(result, ConfigMissing)  # nosec B101
    assert not constructor_called  # nosec B101
    assert list(tmp_path.iterdir()) == []  # nosec B101


def test_valid_unicode_configuration_loads_without_mutation(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    payload = {"title": "Café 東京", "tiles": []}
    original = json.dumps(payload, ensure_ascii=False).encode()
    config_path.write_bytes(original)
    calls: list[dict[str, object]] = []

    def constructor(mapping: dict[str, object]) -> dict[str, object]:
        calls.append(mapping)
        return mapping

    result = load_config(config_path, constructor)

    assert isinstance(result, ConfigLoaded)  # nosec B101
    assert result.value == payload  # nosec B101
    assert calls == [payload]  # nosec B101
    assert config_path.read_bytes() == original  # nosec B101
    assert set(tmp_path.iterdir()) == {config_path}  # nosec B101


def _object_payload_of_size(size: int) -> bytes:
    prefix = b'{"padding":"'
    suffix = b'"}'
    return prefix + (b"x" * (size - len(prefix) - len(suffix))) + suffix


def test_exactly_four_mib_is_admitted(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_object_payload_of_size(MAX_CONFIG_BYTES))

    result = load_config(config_path, _identity)

    assert isinstance(result, ConfigLoaded)  # nosec B101


@pytest.mark.parametrize("max_bytes", [0, -1, MAX_CONFIG_BYTES + 1])
def test_configured_limit_cannot_bypass_four_mib_ceiling(
    tmp_path: Path,
    max_bytes: int,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="max_bytes must be between"):
        load_config(config_path, _identity, max_bytes=max_bytes)


def test_four_mib_plus_one_is_rejected_before_construction(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_object_payload_of_size(MAX_CONFIG_BYTES + 1))
    constructor_called = False

    def constructor(_mapping: dict[str, object]) -> object:
        nonlocal constructor_called
        constructor_called = True
        return object()

    result = load_config(config_path, constructor)

    assert isinstance(result, ConfigRecoveryRequired)  # nosec B101
    assert result.category is ConfigLoadFailureCategory.SIZE_LIMIT_EXCEEDED  # nosec B101
    assert result.snapshot is not None  # nosec B101
    assert result.snapshot.evidence_byte_count == MAX_CONFIG_BYTES + 1  # nosec B101
    assert not constructor_called  # nosec B101


def test_loading_reads_no_more_than_configured_limit_plus_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    max_bytes = 32
    config_path.write_bytes(_object_payload_of_size(128))
    real_read = config_recovery.os.read
    requested_sizes: list[int] = []
    returned_sizes: list[int] = []

    def tracked_read(descriptor: int, size: int) -> bytes:
        requested_sizes.append(size)
        content = real_read(descriptor, size)
        returned_sizes.append(len(content))
        return content

    monkeypatch.setattr(config_recovery.os, "read", tracked_read)

    result = load_config(config_path, _identity, max_bytes=max_bytes)

    assert isinstance(result, ConfigRecoveryRequired)  # nosec B101
    assert result.category is ConfigLoadFailureCategory.SIZE_LIMIT_EXCEEDED  # nosec B101
    assert sum(returned_sizes) == max_bytes + 1  # nosec B101
    assert requested_sizes  # nosec B101
    assert max(requested_sizes) <= max_bytes + 1  # nosec B101


def test_file_read_failure_is_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    def fail_open(_path: Path) -> NoReturn:
        raise PermissionError("synthetic-private-path")

    monkeypatch.setattr(config_recovery, "_open_source", fail_open)

    result = load_config(config_path, _identity)

    assert isinstance(result, ConfigRecoveryRequired)  # nosec B101
    assert result.category is ConfigLoadFailureCategory.FILE_READ_FAILURE  # nosec B101
    assert "synthetic-private-path" not in repr(result)  # nosec B101
    assert str(config_path) not in repr(result)  # nosec B101


def test_invalid_utf8_is_classified(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(b"\xff\xfe")

    result = _rejected(config_path)

    assert result.category is ConfigLoadFailureCategory.INVALID_UTF8  # nosec B101


@pytest.mark.parametrize("payload", [b"{", b"\xef\xbb\xbf{}"])
def test_malformed_json_and_bom_are_classified(tmp_path: Path, payload: bytes) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(payload)

    result = _rejected(config_path)

    assert result.category is ConfigLoadFailureCategory.MALFORMED_JSON  # nosec B101


@pytest.mark.parametrize("payload", [b"[]", b'"text"', b"1", b"true", b"null"])
def test_every_non_object_root_is_rejected(tmp_path: Path, payload: bytes) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(payload)

    result = _rejected(config_path)

    assert result.category is ConfigLoadFailureCategory.NON_OBJECT_ROOT  # nosec B101


def test_expected_legacy_construction_failure_is_classified(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    def incompatible(_mapping: dict[str, object]) -> NoReturn:
        raise LegacyConstructionFailure

    result = load_config(config_path, incompatible)

    assert isinstance(result, ConfigRecoveryRequired)  # nosec B101
    assert (  # nosec B101
        result.category is ConfigLoadFailureCategory.LEGACY_CONSTRUCTION_FAILURE
    )


@pytest.mark.parametrize(
    ("exception_type", "message"),
    [
        (TypeError, "synthetic type defect"),
        (RuntimeError, "synthetic runtime defect"),
    ],
)
def test_unexpected_constructor_failure_remains_visible(
    tmp_path: Path,
    exception_type: type[Exception],
    message: str,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    def broken_implementation(_mapping: dict[str, object]) -> NoReturn:
        validate_legacy_mapping(_mapping)
        raise exception_type(message)

    with pytest.raises(exception_type, match=message):
        load_config(config_path, broken_implementation)


@pytest.mark.parametrize(
    "mapping",
    [
        pytest.param({"title": []}, id="title-list"),
        pytest.param({"title": None}, id="title-null"),
        pytest.param({"columns": "five"}, id="columns-string"),
        pytest.param({"columns": 5.0}, id="columns-float"),
        pytest.param({"columns": True}, id="columns-bool"),
        pytest.param({"auto_fit": 1}, id="auto-fit-non-bool"),
        pytest.param({"window_x": True}, id="window-x-bool"),
        pytest.param({"window_y": 1.5}, id="window-y-float"),
        pytest.param({"window_w": "100"}, id="window-w-string"),
        pytest.param({"window_h": []}, id="window-h-list"),
        pytest.param({"tiles": None}, id="tiles-null"),
        pytest.param({"tiles": {}}, id="tiles-object"),
        pytest.param({"tiles": ["tile"]}, id="tile-non-object"),
        pytest.param({"tiles": [{"url": "https://example.test"}]}, id="missing-name"),
        pytest.param({"tiles": [{"name": "Example"}]}, id="missing-url"),
        pytest.param(
            {
                "tiles": [
                    {
                        "name": "Example",
                        "url": "https://example.test",
                        "unknown": "value",
                    }
                ]
            },
            id="unknown-tile-field",
        ),
        pytest.param(
            {"tiles": [{"name": 1, "url": "https://example.test"}]},
            id="tile-name-type",
        ),
        pytest.param(
            {"tiles": [{"name": "Example", "url": None}]},
            id="tile-url-type",
        ),
        pytest.param(
            {
                "tiles": [
                    {
                        "name": "Example",
                        "url": "https://example.test",
                        "tab": None,
                    }
                ]
            },
            id="tile-tab-type",
        ),
        pytest.param(
            {
                "tiles": [
                    {
                        "name": "Example",
                        "url": "https://example.test",
                        "bg": 1,
                    }
                ]
            },
            id="tile-bg-type",
        ),
        pytest.param(
            {
                "tiles": [
                    {
                        "name": "Example",
                        "url": "https://example.test",
                        "icon": 1,
                    }
                ]
            },
            id="tile-icon-type",
        ),
        pytest.param(
            {
                "tiles": [
                    {
                        "name": "Example",
                        "url": "https://example.test",
                        "browser": False,
                    }
                ]
            },
            id="tile-browser-type",
        ),
        pytest.param(
            {
                "tiles": [
                    {
                        "name": "Example",
                        "url": "https://example.test",
                        "chrome_profile": [],
                    }
                ]
            },
            id="tile-chrome-profile-type",
        ),
        pytest.param(
            {
                "tiles": [
                    {
                        "name": "Example",
                        "url": "https://example.test",
                        "open_target": "popup",
                    }
                ]
            },
            id="invalid-open-target",
        ),
        pytest.param({"tabs": "Main"}, id="tabs-string"),
        pytest.param({"tabs": {}}, id="tabs-object"),
        pytest.param({"hidden_tabs": "Hidden"}, id="hidden-tabs-string"),
        pytest.param({"hidden_tabs": {}}, id="hidden-tabs-object"),
    ],
)
def test_shallow_legacy_validator_rejects_incompatible_values(
    mapping: dict[str, object],
) -> None:
    with pytest.raises(LegacyConstructionFailure):
        validate_legacy_mapping(mapping)


def test_shallow_legacy_validator_accepts_current_unicode_values() -> None:
    mapping: dict[str, object] = {
        "title": "Café 東京",
        "columns": 5,
        "auto_fit": True,
        "window_x": None,
        "window_y": -25,
        "window_w": 800,
        "window_h": 600,
        "tiles": [
            {
                "name": "Résumé",
                "url": "https://例え.test",
                "tab": "メイン",
                "icon": None,
                "bg": "#ffffff",
                "browser": "chrome",
                "chrome_profile": None,
                "open_target": "window",
            }
        ],
        "tabs": ["メイン"],
        "hidden_tabs": None,
    }
    original = deepcopy(mapping)

    validate_legacy_mapping(mapping)

    assert mapping == original  # nosec B101


def test_shallow_legacy_validator_allows_filtered_tab_entries() -> None:
    mapping: dict[str, object] = {
        "tabs": ["Main", 1, None, {"ignored": True}, []],
        "hidden_tabs": ["Hidden", False, None, ["ignored"]],
    }
    original = deepcopy(mapping)

    validate_legacy_mapping(mapping)

    assert mapping == original  # nosec B101


def test_shallow_legacy_validator_allows_unknown_top_level_fields() -> None:
    mapping: dict[str, object] = {
        "title": "Launcher",
        "tabs": None,
        "tab_ids": ["malformed-but-normalized-later"],
        "tab_order": {"malformed": True},
        "future_extension": {"retained": True},
    }
    original = deepcopy(mapping)

    validate_legacy_mapping(mapping)

    assert mapping == original  # nosec B101


def test_invalid_legacy_value_is_classified_without_mutation(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"columns":"five"}'
    config_path.write_bytes(original)
    constructor_reached = False

    def validated_constructor(mapping: dict[str, object]) -> dict[str, object]:
        nonlocal constructor_reached
        validate_legacy_mapping(mapping)
        constructor_reached = True
        return mapping

    result = load_config(config_path, validated_constructor)

    assert isinstance(result, ConfigRecoveryRequired)  # nosec B101
    assert (  # nosec B101
        result.category is ConfigLoadFailureCategory.LEGACY_CONSTRUCTION_FAILURE
    )
    assert not constructor_reached  # nosec B101
    assert config_path.read_bytes() == original  # nosec B101
    assert not (tmp_path / "recovery").exists()  # nosec B101


def test_sensitive_snapshot_and_exception_repr_are_protected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)
    result = _rejected(config_path)

    error = ConfigurationLoadError(result.category, result.snapshot)
    rendered = f"{result!r} {result.snapshot!r} {error!r} {error}"

    assert "example.test" not in rendered  # nosec B101
    assert "sample" not in rendered  # nosec B101
    assert str(config_path) not in rendered  # nosec B101
    assert "sha256" not in rendered  # nosec B101


def test_recovery_is_not_started_until_explicitly_requested(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)

    result = _rejected(config_path)

    assert result.category is ConfigLoadFailureCategory.MALFORMED_JSON  # nosec B101
    assert config_path.read_bytes() == _CORRUPT_BYTES  # nosec B101
    assert not (tmp_path / "recovery").exists()  # nosec B101


def test_preserve_and_reset_is_byte_for_byte(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    original = b"\xffprivate\x00bytes"
    config_path.write_bytes(original)
    rejected = _rejected(config_path)

    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoverySucceeded)  # nosec B101
    assert config_path.read_text(encoding="utf-8") == _RESET_TEXT  # nosec B101
    recovery_files = _recovery_files(config_path)
    assert len(recovery_files) == 1  # nosec B101
    assert recovery_files[0].read_bytes() == original  # nosec B101
    assert list((tmp_path / "recovery").glob("*.partial")) == []  # nosec B101


def test_oversized_input_is_streamed_and_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    original = b"x" * (config_recovery._IO_CHUNK_BYTES + 257)
    config_path.write_bytes(original)
    rejected = _rejected(config_path, max_bytes=32)
    assert rejected.category is ConfigLoadFailureCategory.SIZE_LIMIT_EXCEEDED  # nosec B101
    real_read = config_recovery.os.read
    requested_sizes: list[int] = []

    def tracked_read(descriptor: int, size: int) -> bytes:
        requested_sizes.append(size)
        return real_read(descriptor, size)

    monkeypatch.setattr(config_recovery.os, "read", tracked_read)

    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoverySucceeded)  # nosec B101
    assert config_recovery._IO_CHUNK_BYTES in requested_sizes  # nosec B101
    assert max(requested_sizes) <= config_recovery._IO_CHUNK_BYTES  # nosec B101
    assert _recovery_files(config_path)[0].read_bytes() == original  # nosec B101


def test_missing_snapshot_does_not_start_recovery(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)

    result = preserve_and_reset(config_path, None, _RESET_TEXT)

    assert isinstance(result, RecoveryFailed)  # nosec B101
    assert result.category is RecoveryFailureCategory.SOURCE_UNAVAILABLE  # nosec B101
    assert result.recovery_copy_count == 0  # nosec B101
    assert result.reset_count == 0  # nosec B101
    assert config_path.read_bytes() == _CORRUPT_BYTES  # nosec B101
    assert not (tmp_path / "recovery").exists()  # nosec B101


def test_source_close_failure_blocks_publish_and_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)
    rejected = _rejected(config_path)
    failed_descriptors = _fail_source_close_after_real_close(monkeypatch)

    def unexpected_call(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("source close failure must prevent publication and reset")

    monkeypatch.setattr(config_recovery, "_publish_verified_copy", unexpected_call)
    monkeypatch.setattr(config_recovery, "atomic_write_text", unexpected_call)

    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoveryFailed)  # nosec B101
    assert result.category is RecoveryFailureCategory.RECOVERY_COPY_FAILURE  # nosec B101
    assert result.recovery_copy_count == 0  # nosec B101
    assert result.reset_count == 0  # nosec B101
    assert len(failed_descriptors) == 1  # nosec B101
    assert config_path.read_bytes() == _CORRUPT_BYTES  # nosec B101
    assert _recovery_files(config_path) == []  # nosec B101
    assert list((tmp_path / "recovery").glob("*.partial")) == []  # nosec B101


def test_source_close_failure_preserves_prior_controlled_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)
    rejected = _rejected(config_path)
    failed_descriptors = _fail_source_close_after_real_close(monkeypatch)

    def fail_directory(_config_path: Path) -> NoReturn:
        raise OSError("synthetic recovery directory failure")

    monkeypatch.setattr(config_recovery, "_resolved_recovery_directory", fail_directory)

    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoveryFailed)  # nosec B101
    assert (  # nosec B101
        result.category is RecoveryFailureCategory.RECOVERY_DIRECTORY_FAILURE
    )
    assert result.recovery_copy_count == 0  # nosec B101
    assert result.reset_count == 0  # nosec B101
    assert len(failed_descriptors) == 1  # nosec B101
    assert config_path.read_bytes() == _CORRUPT_BYTES  # nosec B101
    assert not (tmp_path / "recovery").exists()  # nosec B101


def test_exclusive_final_name_collision_never_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)
    rejected = _rejected(config_path)
    recovery_directory = tmp_path / "recovery"
    recovery_directory.mkdir()
    stage_token = "a" * 32
    collision_token = "b" * 32
    final_token = "c" * 32
    sentinel = recovery_directory / f"config-{collision_token}.recovery"
    sentinel.write_bytes(b"sentinel")
    tokens = iter([stage_token, collision_token, final_token])
    monkeypatch.setattr(config_recovery, "_new_token", lambda: next(tokens))

    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoverySucceeded)  # nosec B101
    assert sentinel.read_bytes() == b"sentinel"  # nosec B101
    assert (recovery_directory / f"config-{final_token}.recovery").exists()  # nosec B101


def test_repeated_recovery_keeps_unique_verified_copies(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    originals = [b"{first", b"{second"]

    for original in originals:
        config_path.write_bytes(original)
        rejected = _rejected(config_path)
        result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)
        assert isinstance(result, RecoverySucceeded)  # nosec B101

    recovery_files = _recovery_files(config_path)
    assert len(recovery_files) == 2  # nosec B101
    assert {path.read_bytes() for path in recovery_files} == set(originals)  # nosec B101


@pytest.mark.parametrize("failure_point", ["write", "flush", "sync"])
def test_staging_failures_do_not_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)
    rejected = _rejected(config_path)

    def fail(*_args: object) -> NoReturn:
        raise OSError(f"synthetic {failure_point} failure")

    if failure_point == "write":
        monkeypatch.setattr(config_recovery, "_write_chunk", fail)
    elif failure_point == "flush":
        monkeypatch.setattr(config_recovery, "_flush_and_sync", fail)
    else:
        monkeypatch.setattr(config_recovery.os, "fsync", fail)

    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoveryFailed)  # nosec B101
    assert result.category is RecoveryFailureCategory.RECOVERY_COPY_FAILURE  # nosec B101
    assert result.recovery_copy_count == 0  # nosec B101
    assert config_path.read_bytes() == _CORRUPT_BYTES  # nosec B101
    assert _recovery_files(config_path) == []  # nosec B101


def test_failed_partial_is_never_counted_or_given_final_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)
    rejected = _rejected(config_path)

    def fail_write(*_args: object) -> NoReturn:
        raise OSError("synthetic write failure")

    monkeypatch.setattr(config_recovery, "_write_chunk", fail_write)
    monkeypatch.setattr(config_recovery, "_cleanup_staging", lambda _path: None)

    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoveryFailed)  # nosec B101
    assert result.recovery_copy_count == 0  # nosec B101
    assert _recovery_files(config_path) == []  # nosec B101
    partials = list((tmp_path / "recovery").glob("*.partial"))
    assert len(partials) == 1  # nosec B101
    assert partials[0].name.startswith(".config-")  # nosec B101


@pytest.mark.parametrize("mismatch", ["length", "digest"])
def test_verification_mismatch_prevents_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)
    rejected = _rejected(config_path)
    real_hash = config_recovery._hash_stable_path
    calls = 0

    def mismatched_hash(
        path: Path,
        *,
        allow_redirected: bool = False,
    ) -> tuple[config_recovery._FileFingerprint, int, bytes]:
        nonlocal calls
        fingerprint, count, digest = real_hash(
            path,
            allow_redirected=allow_redirected,
        )
        calls += 1
        if calls == 1:
            if mismatch == "length":
                count += 1
            else:
                digest = bytes([digest[0] ^ 1]) + digest[1:]
        return fingerprint, count, digest

    monkeypatch.setattr(config_recovery, "_hash_stable_path", mismatched_hash)

    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoveryFailed)  # nosec B101
    assert (  # nosec B101
        result.category is RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE
    )
    assert config_path.read_bytes() == _CORRUPT_BYTES  # nosec B101
    assert _recovery_files(config_path) == []  # nosec B101


def test_final_copy_access_failure_is_a_verification_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)
    rejected = _rejected(config_path)
    real_hash = config_recovery._hash_stable_path
    calls = 0

    def fail_final_copy(
        path: Path,
        *,
        allow_redirected: bool = False,
    ) -> tuple[config_recovery._FileFingerprint, int, bytes]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError("synthetic final-copy access failure")
        return real_hash(path, allow_redirected=allow_redirected)

    monkeypatch.setattr(config_recovery, "_hash_stable_path", fail_final_copy)

    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoveryFailed)  # nosec B101
    assert (  # nosec B101
        result.category is RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE
    )
    assert result.recovery_copy_count == 1  # nosec B101
    assert config_path.read_bytes() == _CORRUPT_BYTES  # nosec B101
    assert len(_recovery_files(config_path)) == 1  # nosec B101


@pytest.mark.parametrize("mismatch", ["count", "digest"])
def test_final_copy_mismatch_is_a_verification_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)
    rejected = _rejected(config_path)
    real_hash = config_recovery._hash_stable_path
    calls = 0

    def mismatch_final_copy(
        path: Path,
        *,
        allow_redirected: bool = False,
    ) -> tuple[config_recovery._FileFingerprint, int, bytes]:
        nonlocal calls
        fingerprint, count, digest = real_hash(
            path,
            allow_redirected=allow_redirected,
        )
        calls += 1
        if calls == 2:
            if mismatch == "count":
                count += 1
            else:
                digest = bytes([digest[0] ^ 1]) + digest[1:]
        return fingerprint, count, digest

    monkeypatch.setattr(config_recovery, "_hash_stable_path", mismatch_final_copy)

    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoveryFailed)  # nosec B101
    assert (  # nosec B101
        result.category is RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE
    )
    assert calls == 2  # nosec B101
    assert result.recovery_copy_count == 1  # nosec B101
    assert result.reset_count == 0  # nosec B101
    assert config_path.read_bytes() == _CORRUPT_BYTES  # nosec B101
    recovery_files = _recovery_files(config_path)
    assert len(recovery_files) == 1  # nosec B101
    assert recovery_files[0].read_bytes() == _CORRUPT_BYTES  # nosec B101


def test_source_changed_before_recovery_is_not_replaced(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)
    rejected = _rejected(config_path)
    changed = b"changed-before-recovery"
    config_path.write_bytes(changed)

    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoveryFailed)  # nosec B101
    assert result.category is RecoveryFailureCategory.SOURCE_CHANGED  # nosec B101
    assert config_path.read_bytes() == changed  # nosec B101
    assert not (tmp_path / "recovery").exists()  # nosec B101


def test_source_changed_during_copy_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)
    rejected = _rejected(config_path)
    real_write = config_recovery._write_chunk
    changed = False

    def change_during_write(
        stream: config_recovery._BinaryStream,
        chunk: bytes,
    ) -> None:
        nonlocal changed
        real_write(stream, chunk)
        if not changed:
            changed = True
            config_path.write_bytes(b"changed-during-copy")

    monkeypatch.setattr(config_recovery, "_write_chunk", change_during_write)

    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoveryFailed)  # nosec B101
    assert result.category is RecoveryFailureCategory.SOURCE_CHANGED  # nosec B101
    assert config_path.read_bytes() == b"changed-during-copy"  # nosec B101
    assert _recovery_files(config_path) == []  # nosec B101


def test_source_changed_immediately_before_replace_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)
    rejected = _rejected(config_path)
    real_atomic_write = config_recovery.atomic_write_text
    changed = b"changed-before-replace"

    def mutate_then_write(
        path: Path,
        text: str,
        *,
        before_replace: Callable[[], None] | None = None,
    ) -> None:
        path.write_bytes(changed)
        real_atomic_write(path, text, before_replace=before_replace)

    monkeypatch.setattr(config_recovery, "atomic_write_text", mutate_then_write)

    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoveryFailed)  # nosec B101
    assert result.category is RecoveryFailureCategory.SOURCE_CHANGED  # nosec B101
    assert result.recovery_copy_count == 1  # nosec B101
    assert config_path.read_bytes() == changed  # nosec B101
    assert _recovery_files(config_path)[0].read_bytes() == _CORRUPT_BYTES  # nosec B101


def test_atomic_reset_failure_retains_source_and_verified_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)
    rejected = _rejected(config_path)

    def fail_reset(*_args: object, **_kwargs: object) -> NoReturn:
        raise OSError("synthetic reset failure")

    monkeypatch.setattr(config_recovery, "atomic_write_text", fail_reset)

    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoveryFailed)  # nosec B101
    assert result.category is RecoveryFailureCategory.RESET_FAILURE  # nosec B101
    assert result.recovery_copy_count == 1  # nosec B101
    assert config_path.read_bytes() == _CORRUPT_BYTES  # nosec B101
    recovery_file = _recovery_files(config_path)[0]
    assert recovery_file.read_bytes() == _CORRUPT_BYTES  # nosec B101


def test_recovery_directory_redirection_fails_closed(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)
    rejected = _rejected(config_path)
    external = tmp_path / "external"
    external.mkdir()
    recovery_link = tmp_path / "recovery"
    try:
        recovery_link.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable in this environment")

    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoveryFailed)  # nosec B101
    assert (  # nosec B101
        result.category is RecoveryFailureCategory.RECOVERY_DIRECTORY_FAILURE
    )
    assert list(external.iterdir()) == []  # nosec B101
    assert config_path.read_bytes() == _CORRUPT_BYTES  # nosec B101


def test_final_recovery_names_are_direct_children(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_CORRUPT_BYTES)
    rejected = _rejected(config_path)

    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoverySucceeded)  # nosec B101
    recovery_file = _recovery_files(config_path)[0]
    assert recovery_file.parent == tmp_path / "recovery"  # nosec B101
    assert re.fullmatch(r"config-[0-9a-f]{32}\.recovery", recovery_file.name)  # nosec B101


def test_redirected_valid_source_still_loads_but_is_not_auto_recovered(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.json"
    target.write_text('{"title": "Valid"}', encoding="utf-8")
    config_path = tmp_path / "config.json"
    try:
        config_path.symlink_to(target)
    except OSError:
        pytest.skip("file symlinks are unavailable in this environment")

    loaded = load_config(config_path, _identity)
    assert isinstance(loaded, ConfigLoaded)  # nosec B101

    target.write_bytes(_CORRUPT_BYTES)
    rejected = _rejected(config_path)
    result = preserve_and_reset(config_path, rejected.snapshot, _RESET_TEXT)

    assert isinstance(result, RecoveryFailed)  # nosec B101
    assert result.category is RecoveryFailureCategory.SOURCE_UNAVAILABLE  # nosec B101
    assert target.read_bytes() == _CORRUPT_BYTES  # nosec B101
    assert config_path.is_symlink()  # nosec B101


def test_diagnostics_contain_only_curated_categories_and_counts() -> None:
    failed = RecoveryFailed(
        RecoveryFailureCategory.RECOVERY_COPY_FAILURE,
        recovery_copy_count=0,
    )
    required = recovery_required_diagnostics(ConfigLoadFailureCategory.MALFORMED_JSON)
    exited = recovery_exit_diagnostics(ConfigLoadFailureCategory.INVALID_UTF8)
    failed_result = recovery_result_diagnostics(failed)
    succeeded = recovery_result_diagnostics(RecoverySucceeded())
    assert required == {  # nosec B101
        "failure_category": "malformed_json",
        "failure_count": 1,
    }
    assert exited == {  # nosec B101
        "failure_category": "invalid_utf8",
        "exit_count": 1,
    }
    assert failed_result == {  # nosec B101
        "recovery_copy_count": 0,
        "reset_count": 0,
        "failure_category": "recovery_copy_failure",
    }
    assert succeeded == {  # nosec B101
        "recovery_copy_count": 1,
        "reset_count": 1,
    }
    diagnostics = [required, exited, failed_result, succeeded]
    allowed_strings = {category.value for category in ConfigLoadFailureCategory} | {
        category.value for category in RecoveryFailureCategory
    }
    allowed_keys = {
        "failure_category",
        "failure_count",
        "exit_count",
        "recovery_copy_count",
        "reset_count",
    }

    for fields in diagnostics:
        assert set(fields).issubset(allowed_keys)  # nosec B101
        for value in fields.values():
            assert isinstance(value, (str, bool, int))  # nosec B101
            if isinstance(value, str):
                assert value in allowed_strings  # nosec B101
    rendered = repr(diagnostics)
    for forbidden in ("example.test", "token", "password", "config.json", "sha256"):
        assert forbidden not in rendered  # nosec B101
