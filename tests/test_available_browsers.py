import os
import webbrowser

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from tile_launcher import available_browsers  # noqa: E402


def test_checks_common_browsers(monkeypatch):
    checked: list[str] = []

    def fake_get(name: str):
        checked.append(name)
        raise webbrowser.Error

    monkeypatch.setattr(webbrowser, "get", fake_get)
    monkeypatch.setattr(webbrowser, "_tryorder", [])

    available_browsers()

    for expected in {"brave", "firefox", "safari"}:
        assert expected in checked
