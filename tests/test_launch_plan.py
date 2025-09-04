from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from tile_launcher import LauncherConfig, Tile, build_launch_plan


def test_launch_plan_chrome_profile_tab():
    tile = Tile(
        name="t",
        url="http://e",
        browser="chrome",
        chrome_profile="Profile 1",
        open_target="tab",
    )
    plan = build_launch_plan(tile)
    assert plan.command is not None  # nosec B101

    assert any(arg.startswith("--profile-directory=") for arg in plan.command)  # nosec B101

    assert "--new-window" not in plan.command  # nosec B101



def test_launch_plan_chrome_profile_window():
    tile = Tile(
        name="t",
        url="http://e",
        browser="chrome",
        chrome_profile="Profile 1",
        open_target="window",
    )
    plan = build_launch_plan(tile)
    assert plan.command is not None  # nosec B101

    assert any(arg.startswith("--profile-directory=") for arg in plan.command)  # nosec B101

    assert "--new-window" in plan.command  # nosec B101



def test_launch_plan_firefox_tab():
    tile = Tile(name="t", url="http://e", browser="firefox", open_target="tab")
    plan = build_launch_plan(tile)
    assert plan.command is not None  # nosec B101

    assert plan.command[1] == "--new-tab"  # nosec B101



def test_launch_plan_firefox_window():
    tile = Tile(name="t", url="http://e", browser="firefox", open_target="window")
    plan = build_launch_plan(tile)
    assert plan.command is not None  # nosec B101

    assert plan.command[1] == "--new-window"  # nosec B101



def test_launch_plan_default_browser_tab_window():
    tile_tab = Tile(name="t", url="http://e")
    plan_tab = build_launch_plan(tile_tab)
    assert plan_tab.controller == "default"  # nosec B101

    assert plan_tab.new == 2  # nosec B101


    tile_win = Tile(name="t", url="http://e", open_target="window")
    plan_win = build_launch_plan(tile_win)
    assert plan_win.controller == "default"  # nosec B101

    assert plan_win.new == 1  # nosec B101



def test_config_migration_adds_open_target(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "title": "Launcher",
                "columns": 5,
                "tiles": [{"name": "t", "url": "http://e", "tab": "Main"}],
                "tabs": ["Main"],
            }
        )
    )
    monkeypatch.setattr("tile_launcher.CFG_PATH", cfg_path)
    cfg = LauncherConfig.load()
    assert cfg.tiles[0].open_target == "tab"  # nosec B101

    cfg.save()
    data = json.loads(cfg_path.read_text())
    assert data["tiles"][0]["open_target"] == "tab"  # nosec B101

