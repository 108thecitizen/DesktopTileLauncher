import json
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")

import tile_launcher


@pytest.fixture
def temp_main(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    icon_dir = tmp_path / "icons"
    icon_dir.mkdir()
    monkeypatch.setattr(tile_launcher, "CFG_PATH", cfg_path)
    monkeypatch.setattr(tile_launcher, "ICON_DIR", icon_dir)
    cfg = tile_launcher.LauncherConfig(
        title="t", columns=5, tiles=[], tabs=["Main", "Extra"]
    )
    main = tile_launcher.Main.__new__(tile_launcher.Main)
    main.cfg = cfg
    main.rebuild = lambda: None
    main.tabs_widget = SimpleNamespace(setCurrentIndex=lambda idx: None)
    return main, cfg, cfg_path


def test_add_tile_defaults_to_system_browser(temp_main, monkeypatch):
    main, cfg, cfg_path = temp_main
    monkeypatch.setattr(tile_launcher, "fetch_favicon", lambda url: None)
    main.add_tile_record("Test", "http://example.com", "Extra", None)
    assert cfg.tiles[0].browser is None
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["tiles"][0]["browser"] is None
    assert data["tiles"][0]["tab"] == "Extra"


@pytest.mark.parametrize("field", ["name", "url", "tab"])
def test_add_tile_validation(temp_main, field, monkeypatch):
    main, cfg, cfg_path = temp_main
    monkeypatch.setattr(tile_launcher, "fetch_favicon", lambda url: None)
    kwargs = {"name": "n", "url": "http://x", "tab": "Main", "browser": None}
    kwargs[field] = ""
    with pytest.raises(ValueError):
        main.add_tile_record(**kwargs)


def test_url_normalization(temp_main, monkeypatch):
    main, cfg, cfg_path = temp_main
    monkeypatch.setattr(tile_launcher, "fetch_favicon", lambda url: None)
    main.add_tile_record("Test", "example.com", "Main", None)
    assert cfg.tiles[0].url == "https://example.com"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["tiles"][0]["url"] == "https://example.com"
