from __future__ import annotations

import json
import pytest

import tile_launcher
from tile_launcher import Tile, WindowManager


@pytest.mark.qt_no_exception_capture
def test_spawn_and_persist(tmp_path, monkeypatch, qtbot):
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(tile_launcher, "CFG_PATH", cfg_path)
    state = tile_launcher.AppState()
    state.cfg.tabs.append("Work")
    state.cfg.save()

    manager = WindowManager(qtbot._qapp, state)
    manager.open_initial_windows()
    win = next(iter(manager.windows.values()))
    qtbot.addWidget(win)
    win.tabs_widget.setCurrentIndex(state.cfg.tabs.index("Work"))
    manager.save_all_windows_state()

    manager.new_window(win.current_tab())
    assert len(manager.windows) == 2
    second = [w for w in manager.windows.values() if w is not win][0]
    qtbot.addWidget(second)
    second.close()
    qtbot.waitUntil(lambda: len(manager.windows) == 1)
    manager.save_all_windows_state()

    state2 = tile_launcher.AppState()
    manager2 = WindowManager(qtbot._qapp, state2)
    manager2.open_initial_windows()
    assert len(manager2.windows) == 1
    restored = next(iter(manager2.windows.values()))
    assert restored.current_tab() == "Work"


@pytest.mark.qt_no_exception_capture
def test_tray_new_window(monkeypatch, qtbot, tmp_path):
    monkeypatch.setattr(tile_launcher, "CFG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(
        tile_launcher.QSystemTrayIcon, "isSystemTrayAvailable", classmethod(lambda cls: True)
    )
    state = tile_launcher.AppState()
    manager = WindowManager(qtbot._qapp, state)
    manager.open_initial_windows()
    assert len(manager.windows) == 1
    assert manager._tray_new_action is not None
    manager._tray_new_action.trigger()
    assert len(manager.windows) == 2


@pytest.mark.qt_no_exception_capture
def test_lazy_refresh(monkeypatch, qtbot, tmp_path):
    monkeypatch.setattr(tile_launcher, "CFG_PATH", tmp_path / "config.json")
    state = tile_launcher.AppState()
    state.cfg.tiles = []
    state.cfg.tabs = ["Main", "Work"]
    state.cfg.save()

    manager = WindowManager(qtbot._qapp, state)
    manager.open_initial_windows()
    win_a = next(iter(manager.windows.values()))
    qtbot.addWidget(win_a)
    win_b = manager.new_window("Work")
    qtbot.addWidget(win_b)

    assert win_a.current_tab() == "Main"
    assert win_b.current_tab() == "Work"
    before_a = win_a._grids["Main"].count()
    before_b = win_b._grids["Main"].count()

    state.cfg.tiles.append(Tile(name="T", url="https://example.com", tab="Main"))
    state.cfg.save()
    state.model_changed.emit("Main", "tile_added", {})
    qtbot.wait(50)

    assert win_a._grids["Main"].count() == before_a + 1
    assert win_b._grids["Main"].count() == before_b

    win_b.tabs_widget.setCurrentIndex(0)
    qtbot.wait(50)
    assert win_b._grids["Main"].count() == before_b + 1

