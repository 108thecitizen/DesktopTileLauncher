import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication  # noqa: E402  # pragma: no cover

from tile_launcher import LauncherConfig, Main, Tile  # noqa: E402


def test_expands_to_seven_columns(tmp_path, monkeypatch):
    monkeypatch.setattr("tile_launcher.CFG_PATH", tmp_path / "config.json")
    tiles = [Tile(name=f"t{i}", url="https://example.com") for i in range(37)]
    cfg = LauncherConfig(title="title", columns=5, tiles=tiles)
    cfg.save()

    app = QApplication([])
    main = Main()
    assert main.cfg.columns == 7  # nosec B101

    app.quit()
