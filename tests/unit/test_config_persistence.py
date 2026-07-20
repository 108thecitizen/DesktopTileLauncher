# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import config_persistence
from config_persistence import atomic_write_bytes, atomic_write_text


pytestmark = pytest.mark.unit


def test_atomic_write_creates_json_that_round_trips(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    data = {"title": "Café 東京", "tiles": [{"name": "One"}]}

    atomic_write_text(config_path, json.dumps(data, ensure_ascii=False, indent=2))

    assert json.loads(config_path.read_text(encoding="utf-8")) == data
    assert set(tmp_path.iterdir()) == {config_path}


def test_atomic_write_replaces_existing_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("old configuration", encoding="utf-8")

    atomic_write_text(config_path, '{"title": "New"}')

    assert config_path.read_text(encoding="utf-8") == '{"title": "New"}'
    assert set(tmp_path.iterdir()) == {config_path}


def test_write_failure_preserves_original_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.json"
    original = b"original configuration"
    config_path.write_bytes(original)

    def fail_during_write(_stream: object, _text: str) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(config_persistence, "_write_and_sync", fail_during_write)

    with pytest.raises(OSError, match="simulated write failure"):
        atomic_write_text(config_path, "replacement configuration")

    assert config_path.read_bytes() == original
    assert set(tmp_path.iterdir()) == {config_path}


def test_replace_failure_preserves_original_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.json"
    original = b"original configuration"
    config_path.write_bytes(original)
    replacement = "replacement configuration"
    observed_temporary_paths: list[Path] = []

    def fail_replace(source: Path, destination: Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        observed_temporary_paths.append(source_path)
        assert source_path.parent == destination_path.parent == tmp_path
        assert destination_path == config_path
        assert source_path.read_text(encoding="utf-8") == replacement
        raise OSError("simulated replace failure")

    monkeypatch.setattr(config_persistence.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_text(config_path, replacement)

    assert len(observed_temporary_paths) == 1
    assert not observed_temporary_paths[0].exists()
    assert config_path.read_bytes() == original
    assert set(tmp_path.iterdir()) == {config_path}


def test_before_replace_runs_after_sync_and_immediately_before_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("original", encoding="utf-8")
    events: list[str] = []
    real_write_and_sync = config_persistence._write_and_sync
    real_replace = config_persistence.os.replace

    def tracked_write_and_sync(
        stream: config_persistence._SyncableTextStream,
        text: str,
    ) -> None:
        events.append("write_and_sync")
        real_write_and_sync(stream, text)

    def guard() -> None:
        events.append("guard")
        assert config_path.read_text(encoding="utf-8") == "original"  # nosec B101

    def tracked_replace(source: Path, destination: Path) -> None:
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(config_persistence, "_write_and_sync", tracked_write_and_sync)
    monkeypatch.setattr(config_persistence.os, "replace", tracked_replace)

    atomic_write_text(config_path, "replacement", before_replace=guard)

    assert events == ["write_and_sync", "guard", "replace"]  # nosec B101
    assert config_path.read_text(encoding="utf-8") == "replacement"  # nosec B101
    assert set(tmp_path.iterdir()) == {config_path}  # nosec B101


def test_before_replace_failure_preserves_original_and_removes_temporary_file(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    original = b"original"
    config_path.write_bytes(original)

    def reject_replace() -> None:
        raise RuntimeError("synthetic guard rejection")

    with pytest.raises(RuntimeError, match="synthetic guard rejection"):
        atomic_write_text(
            config_path,
            "replacement",
            before_replace=reject_replace,
        )

    assert config_path.read_bytes() == original  # nosec B101
    assert set(tmp_path.iterdir()) == {config_path}  # nosec B101


def test_atomic_write_bytes_preserves_exact_mixed_newline_and_unicode_bytes(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    payload = '{\n  "title": "Café 東京",\r\n  "value": 1\n}'.encode()

    atomic_write_bytes(config_path, payload)

    assert config_path.read_bytes() == payload  # nosec B101
    assert set(tmp_path.iterdir()) == {config_path}  # nosec B101


def test_atomic_byte_write_failure_preserves_original_and_cleans_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    original = b"original bytes"
    config_path.write_bytes(original)

    def fail_during_write(_stream: object, _data: bytes) -> None:
        raise OSError("synthetic binary write failure")

    monkeypatch.setattr(config_persistence, "_write_bytes_and_sync", fail_during_write)

    with pytest.raises(OSError, match="synthetic binary write failure"):
        atomic_write_bytes(config_path, b"replacement")

    assert config_path.read_bytes() == original  # nosec B101
    assert set(tmp_path.iterdir()) == {config_path}  # nosec B101


def test_atomic_byte_guard_runs_after_sync_and_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(b"original")
    events: list[str] = []
    real_write = config_persistence._write_bytes_and_sync
    real_replace = config_persistence.os.replace

    def tracked_write(
        stream: config_persistence._SyncableBinaryStream,
        data: bytes,
    ) -> None:
        events.append("write_and_sync")
        real_write(stream, data)

    def guard() -> None:
        events.append("guard")
        assert config_path.read_bytes() == b"original"  # nosec B101

    def tracked_replace(source: Path, destination: Path) -> None:
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(config_persistence, "_write_bytes_and_sync", tracked_write)
    monkeypatch.setattr(config_persistence.os, "replace", tracked_replace)

    atomic_write_bytes(config_path, b"replacement", before_replace=guard)

    assert events == ["write_and_sync", "guard", "replace"]  # nosec B101
    assert config_path.read_bytes() == b"replacement"  # nosec B101


def test_atomic_byte_guard_failure_preserves_original_and_only_removes_owned_temp(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    original = b"original bytes"
    config_path.write_bytes(original)
    unrelated_temp = tmp_path / ".config.json.pre-existing.tmp"
    unrelated_payload = b"not owned by the writer"
    unrelated_temp.write_bytes(unrelated_payload)
    owned_temp_paths: list[Path] = []

    def reject_replace() -> None:
        owned_temp_paths.extend(
            path
            for path in tmp_path.iterdir()
            if path not in {config_path, unrelated_temp}
        )
        raise RuntimeError("synthetic binary guard rejection")

    with pytest.raises(RuntimeError, match="synthetic binary guard rejection"):
        atomic_write_bytes(
            config_path,
            b"replacement bytes",
            before_replace=reject_replace,
        )

    assert len(owned_temp_paths) == 1  # nosec B101
    assert not owned_temp_paths[0].exists()  # nosec B101
    assert config_path.read_bytes() == original  # nosec B101
    assert unrelated_temp.read_bytes() == unrelated_payload  # nosec B101
    assert set(tmp_path.iterdir()) == {config_path, unrelated_temp}  # nosec B101


def test_atomic_byte_short_write_is_rejected_and_owned_temp_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    original = b"original bytes"
    config_path.write_bytes(original)
    real_named_temporary_file = config_persistence.tempfile.NamedTemporaryFile

    class ShortWriteTemporary:
        def __init__(self, stream: Any) -> None:
            self._stream = stream

        @property
        def name(self) -> str:
            return str(self._stream.name)

        def __enter__(self) -> ShortWriteTemporary:
            self._stream.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self._stream.__exit__(*args)

        def write(self, data: bytes) -> int:
            written = self._stream.write(data[:-1])
            return int(written)

        def flush(self) -> None:
            self._stream.flush()

        def fileno(self) -> int:
            return int(self._stream.fileno())

    def short_write_temporary(*args: object, **kwargs: object) -> object:
        return ShortWriteTemporary(real_named_temporary_file(*args, **kwargs))

    monkeypatch.setattr(
        config_persistence.tempfile,
        "NamedTemporaryFile",
        short_write_temporary,
    )

    with pytest.raises(OSError, match="short atomic byte write"):
        atomic_write_bytes(config_path, b"replacement bytes")

    assert config_path.read_bytes() == original  # nosec B101
    assert set(tmp_path.iterdir()) == {config_path}  # nosec B101


def test_atomic_byte_fsync_failure_preserves_original_and_removes_owned_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    original = b"original bytes"
    config_path.write_bytes(original)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(config_persistence.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="synthetic fsync failure"):
        atomic_write_bytes(config_path, b"replacement bytes")

    assert config_path.read_bytes() == original  # nosec B101
    assert set(tmp_path.iterdir()) == {config_path}  # nosec B101


def test_atomic_byte_replace_failure_preserves_original_and_removes_owned_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    original = b"original bytes"
    replacement = b"replacement bytes"
    config_path.write_bytes(original)
    observed_temporary_paths: list[Path] = []

    def fail_replace(source: Path, destination: Path) -> None:
        source_path = Path(source)
        observed_temporary_paths.append(source_path)
        assert Path(destination) == config_path  # nosec B101
        assert source_path.read_bytes() == replacement  # nosec B101
        raise OSError("synthetic byte replace failure")

    monkeypatch.setattr(config_persistence.os, "replace", fail_replace)

    with pytest.raises(OSError, match="synthetic byte replace failure"):
        atomic_write_bytes(config_path, replacement)

    assert len(observed_temporary_paths) == 1  # nosec B101
    assert not observed_temporary_paths[0].exists()  # nosec B101
    assert config_path.read_bytes() == original  # nosec B101
    assert set(tmp_path.iterdir()) == {config_path}  # nosec B101
