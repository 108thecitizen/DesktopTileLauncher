# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

import pytest

import config_persistence
from config_persistence import atomic_write_text


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
