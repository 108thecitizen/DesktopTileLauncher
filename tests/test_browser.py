import os
import subprocess
import webbrowser

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from tile_launcher import Main, Tile  # noqa: E402


def test_open_tile_uses_specific_browser(monkeypatch):
    launched: dict[str, list[str]] = {}

    def fake_popen(cmd, close_fds=True):  # pragma: no cover - trivial
        launched["cmd"] = cmd

        class P:
            pass

        return P()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        webbrowser,
        "get",
        lambda name: pytest.fail("webbrowser.get should not be called"),
    )

    tile = Tile(name="t", url="http://example.com", browser="firefox")
    main = Main.__new__(Main)
    main.open_tile(tile)
    assert launched["cmd"][0] == "firefox"
    assert "--new-tab" in launched["cmd"]


def test_open_tile_uses_default_browser(monkeypatch):
    opened: dict[str, str] = {}

    def fake_open(url: str, *, new: int = 0):  # pragma: no cover - trivial
        opened["default"] = url

    monkeypatch.setattr(webbrowser, "open", fake_open)
    monkeypatch.setattr(
        webbrowser, "get", lambda name: pytest.fail("get should not be called")
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("Popen should not be called")
        ),
    )

    tile = Tile(name="t", url="http://example.com")
    main = Main.__new__(Main)
    main.open_tile(tile)
    assert opened == {"default": "http://example.com"}
