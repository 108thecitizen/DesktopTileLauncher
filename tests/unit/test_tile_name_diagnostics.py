# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
import sys
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

import tile_launcher  # noqa: E402
from tile_launcher import Main, Tile  # noqa: E402


pytestmark = pytest.mark.unit

_SENSITIVE_TITLE = "Sensitive Retrieved Title"


@dataclass
class _Config:
    tabs: list[str] = field(default_factory=lambda: ["Main"])
    tiles: list[Tile] = field(default_factory=list)
    save_count: int = 0

    def save(self) -> None:
        self.save_count += 1


class _TabCombo:
    def findText(self, _text: str, _flags: object) -> int:
        return 0

    def setCurrentIndex(self, _index: int) -> None:
        return None


class _Dialog:
    tab_combo = _TabCombo()
    data: dict[str, str | None] = {
        "name": _SENSITIVE_TITLE,
        "url": "https://example.test/",
        "tab": "Main",
        "icon": None,
        "browser": None,
        "chrome_profile": None,
        "open_target": "tab",
    }

    def __init__(self, **_kwargs: object) -> None:
        return None

    def exec(self) -> object:
        return tile_launcher.QDialog.DialogCode.Accepted


class _MainHarness:
    def __init__(self) -> None:
        self.cfg = _Config()
        self.rebuilt = False
        self.selected_tab: str | None = None

    def current_tab(self) -> str:
        return "Main"

    def rebuild(self) -> None:
        self.rebuilt = True

    def _set_current_tab_by_name(self, tab: str) -> None:
        self.selected_tab = tab


def _capture_breadcrumbs(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, dict[str, object]]]:
    captured: list[tuple[str, dict[str, object]]] = []

    def fake_record(event: str, **fields: object) -> None:
        captured.append((event, fields))

    monkeypatch.setattr(tile_launcher, "record_breadcrumb", fake_record)
    return captured


def _assert_sensitive_title_absent(
    captured: list[tuple[str, dict[str, object]]],
) -> None:
    assert _SENSITIVE_TITLE not in repr(captured)


def test_open_tile_does_not_pass_tile_name_to_breadcrumbs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_breadcrumbs(monkeypatch)
    tile = Tile(name=_SENSITIVE_TITLE, url="https://example.test/")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(webbrowser, "open", lambda _url, new=0: True)

    Main.open_tile(object(), tile)

    assert captured
    launch_attempt = [fields for event, fields in captured if event == "launch_attempt"]
    assert launch_attempt
    assert "name" not in launch_attempt[0]
    _assert_sensitive_title_absent(captured)


def test_add_tile_does_not_pass_accepted_tile_name_to_breadcrumbs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _capture_breadcrumbs(monkeypatch)
    harness = _MainHarness()

    monkeypatch.setattr(tile_launcher, "TileEditorDialog", _Dialog)
    monkeypatch.setattr(tile_launcher, "available_browsers", lambda: [])
    monkeypatch.setattr(tile_launcher, "ICON_DIR", tmp_path)

    Main.add_tile(harness, default_tab="Main")

    assert harness.cfg.save_count == 1
    assert harness.rebuilt
    assert harness.selected_tab == "Main"
    assert harness.cfg.tiles[0].name == _SENSITIVE_TITLE
    tile_add = [fields for event, fields in captured if event == "tile_add"]
    assert tile_add
    assert "name" not in tile_add[0]
    _assert_sensitive_title_absent(captured)
