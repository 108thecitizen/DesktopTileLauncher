import os
import shutil
import sys
from pathlib import Path

import webbrowser
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from tile_launcher import available_browsers


def test_available_browsers_detects_common_executables(monkeypatch):
    """Brave and Firefox should be detected when their executables are present."""

    monkeypatch.setattr(webbrowser, "_tryorder", [])

    def fake_which(cmd: str) -> str | None:  # pragma: no cover - simple
        mapping = {
            "brave": "/usr/bin/brave",
            "brave-browser": "/usr/bin/brave",
            "firefox": "/usr/bin/firefox",
        }
        return mapping.get(cmd)

    monkeypatch.setattr(shutil, "which", fake_which)

    browsers = available_browsers()
    assert "brave" in browsers  # nosec B101

    assert "firefox" in browsers  # nosec B101


def test_available_browsers_detects_safari(monkeypatch):
    """Safari should be detected on macOS when installed."""

    monkeypatch.setattr(webbrowser, "_tryorder", [])
    monkeypatch.setattr(sys, "platform", "darwin")

    def fake_exists(self: Path) -> bool:  # pragma: no cover - simple
        return str(self) == "/Applications/Safari.app/Contents/MacOS/Safari"

    monkeypatch.setattr(Path, "exists", fake_exists)

    browsers = available_browsers()
    assert "safari" in browsers  # nosec B101
