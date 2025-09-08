from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6.QtWidgets")

from tile_launcher import LauncherConfig


@pytest.mark.unit
def test_hidden_tabs_load_and_save(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "title": "Launcher",
                "columns": 5,
                "tiles": [{"name": "t1", "url": "http://e", "tab": "Extra"}],
                "tabs": ["Main"],
                "hidden_tabs": ["Extra", "Unknown", 123, "Main"],
            }
        )
    )
    monkeypatch.setattr("tile_launcher.CFG_PATH", cfg_path)
    cfg = LauncherConfig.load()
    assert cfg.tabs == ["Main", "Extra"]
    assert cfg.hidden_tabs == ["Extra"]
    cfg.save()
    data = json.loads(cfg_path.read_text())
    assert data["hidden_tabs"] == ["Extra"]
