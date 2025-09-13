from __future__ import annotations

import sys
import subprocess
import webbrowser

import pytest
pytestmark = pytest.mark.qt

pytest.importorskip("PySide6.QtWidgets")

from tile_launcher import Main, Tile


@pytest.mark.unit
def test_windows_default_chrome_new_window(monkeypatch) -> None:
    tile = Tile(name="t", url="http://example.com", open_target="window")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("tile_launcher.is_windows_default_browser_chrome", lambda: True)

    called: dict[str, tuple[str, str, str, None | str]] = {}

    def fake_launch(
        url: str, profile_dir: str, open_target: str, chrome_path: str | None = None
    ) -> bool:
        called["args"] = (url, profile_dir, open_target, chrome_path)
        return True

    monkeypatch.setattr("tile_launcher.launch_chrome_with_profile", fake_launch)

    def fail_popen(
        *args: object, **kwargs: object
    ) -> None:  # pragma: no cover - should not run
        raise AssertionError("Popen called")

    monkeypatch.setattr(subprocess, "Popen", fail_popen)

    def fail_open(
        *args: object, **kwargs: object
    ) -> None:  # pragma: no cover - should not run
        raise AssertionError("webbrowser.open called")

    monkeypatch.setattr(webbrowser, "open", fail_open)

    Main.open_tile(object(), tile)

    assert called["args"] == ("http://example.com", "Default", "window", None)  # nosec B101


@pytest.mark.unit
def test_explicit_chrome_cli_fallback(monkeypatch) -> None:
    tile = Tile(name="t", url="http://e", browser="chrome", open_target="window")

    monkeypatch.setattr(sys, "platform", "win32")

    launch_calls = {"count": 0}

    def fake_launch(
        url: str, profile_dir: str, open_target: str, chrome_path: str | None = None
    ) -> bool:
        launch_calls["count"] += 1
        return False

    monkeypatch.setattr("tile_launcher.launch_chrome_with_profile", fake_launch)

    popen_args: dict[str, list[str]] = {}

    def fake_popen(cmd: list[str], **kwargs: object) -> object:
        popen_args["cmd"] = cmd
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    def fail_open(
        *args: object, **kwargs: object
    ) -> None:  # pragma: no cover - should not run
        raise AssertionError("webbrowser.open called")

    monkeypatch.setattr(webbrowser, "open", fail_open)

    Main.open_tile(object(), tile)

    assert launch_calls["count"] == 1  # nosec B101
    assert "--new-window" in popen_args["cmd"]  # nosec B101


@pytest.mark.unit
def test_firefox_tab_cli(monkeypatch) -> None:
    tile = Tile(name="t", url="http://e", browser="firefox", open_target="tab")

    def fail_launch(
        *args: object, **kwargs: object
    ) -> None:  # pragma: no cover - should not run
        raise AssertionError("launch_chrome_with_profile called")

    monkeypatch.setattr("tile_launcher.launch_chrome_with_profile", fail_launch)

    popen_args: dict[str, list[str]] = {}

    def fake_popen(cmd: list[str], **kwargs: object) -> object:
        popen_args["cmd"] = cmd
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    def fail_open(
        *args: object, **kwargs: object
    ) -> None:  # pragma: no cover - should not run
        raise AssertionError("webbrowser.open called")

    monkeypatch.setattr(webbrowser, "open", fail_open)

    Main.open_tile(object(), tile)

    assert "--new-tab" in popen_args["cmd"]  # nosec B101


@pytest.mark.unit
def test_default_browser_new_flag(monkeypatch) -> None:
    tile_win = Tile(name="t1", url="http://e", open_target="window")
    tile_tab = Tile(name="t2", url="http://e")  # defaults to tab

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "tile_launcher.is_windows_default_browser_chrome", lambda: False
    )

    def fail_launch(
        *args: object, **kwargs: object
    ) -> None:  # pragma: no cover - should not run
        raise AssertionError("launch_chrome_with_profile called")

    monkeypatch.setattr("tile_launcher.launch_chrome_with_profile", fail_launch)

    def fail_popen(
        *args: object, **kwargs: object
    ) -> None:  # pragma: no cover - should not run
        raise AssertionError("Popen called")

    monkeypatch.setattr(subprocess, "Popen", fail_popen)

    calls: list[int] = []

    def fake_open(url: str, new: int = 0) -> bool:
        calls.append(new)
        return True

    monkeypatch.setattr(webbrowser, "open", fake_open)

    Main.open_tile(object(), tile_win)
    Main.open_tile(object(), tile_tab)

    assert calls == [1, 2]  # nosec B101
