import os
import webbrowser

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from tile_launcher import Main, Tile  # noqa: E402


def test_open_tile_uses_specific_browser(monkeypatch):
    opened: dict[str, str] = {}

    class FakeBrowser:
        def open(self, url: str) -> None:  # pragma: no cover - trivial
            opened["browser"] = url

    def fake_get(name: str) -> FakeBrowser:
        assert name == "firefox"
        return FakeBrowser()

    monkeypatch.setattr(webbrowser, "get", fake_get)
    monkeypatch.setattr(
        webbrowser, "open", lambda url: opened.setdefault("default", url)
    )

    tile = Tile(name="t", url="http://example.com", browser="firefox")
    main = Main.__new__(Main)
    main.open_tile(tile)
    assert opened == {"browser": "http://example.com"}


def test_open_tile_uses_default_browser(monkeypatch):
    opened: dict[str, str] = {}

    def fake_open(url: str) -> None:  # pragma: no cover - trivial
        opened["default"] = url

    def fake_get(name: str):  # pragma: no cover - trivial
        raise AssertionError("get should not be called")

    monkeypatch.setattr(webbrowser, "open", fake_open)
    monkeypatch.setattr(webbrowser, "get", fake_get)

    tile = Tile(name="t", url="http://example.com")
    main = Main.__new__(Main)
    main.open_tile(tile)
    assert opened == {"default": "http://example.com"}
